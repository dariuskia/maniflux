"""ig2rss: one RSS feed per Instagram account, served from the local cache. Accounts are managed on the status page."""

import hashlib
import html
import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import config, feed
from .db import DB
from .poller import Poller
from .scraper import Scraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ig2rss")

_USERNAME = re.compile(r"^[a-z0-9._]{1,30}$")
_PROFILE_LINK = re.compile(r"^(?:https?://)?(?:www\.)?instagram\.com/([^/?#\s]+)", re.IGNORECASE)


def username_from(text: str) -> str | None:
    """A username from what was typed: 'name', '@name' or a profile link."""
    text = text.strip()
    link = _PROFILE_LINK.match(text)
    name = (link.group(1) if link else text).lstrip("@").lower()
    return name if _USERNAME.match(name) else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = DB(config.DB_PATH)
    stop = threading.Event()
    scraper = app.state.poller = None
    if os.environ.get("IG2RSS_NO_POLL") != "1":
        scraper = Scraper(config.LOGIN, config.SESSION_FILE, config.REELS)
        app.state.poller = Poller(app.state.db, scraper)
        threading.Thread(target=app.state.poller.run, args=(stop,), daemon=True, name="poller").start()
    yield
    stop.set()
    if app.state.poller:
        app.state.poller.wake.set()
    if scraper:
        scraper.close()


app = FastAPI(title="ig2rss", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/feeds/{username}.xml")
def account_feed(request: Request, username: str):
    db: DB = request.app.state.db
    account = db.account(username.lower())
    if not account or not account["following"]:
        raise HTTPException(404, "Not one of the accounts on the ig2rss page")
    username = account["username"]
    body = feed.build(username, account["full_name"], db.posts(username, config.FEED_ITEMS), db.images)
    etag = '"' + hashlib.sha256(body).hexdigest()[:20] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(body, media_type="application/rss+xml; charset=utf-8", headers={"ETag": etag})


def _from_this_page(request: Request) -> bool:
    """Stops other websites open in the browser from posting to these forms."""
    origin = request.headers.get("origin")
    return not origin or urlsplit(origin).netloc == request.headers.get("host")


def _back(msg: str) -> RedirectResponse:
    return RedirectResponse(f"/?msg={quote(msg)}", status_code=303)


@app.post("/accounts")
def add_account(request: Request, username: str = Form("")):
    if not _from_this_page(request):
        raise HTTPException(403)
    name = username_from(username)
    if not name:
        return _back(f"“{username.strip()[:60]}” isn't an Instagram username")
    if not request.app.state.db.add_account(name):
        return _back(f"@{name} is already here")
    log.info("Added @%s", name)
    return _back(f"Added @{name}. It's read within a few minutes; then add {config.INTERNAL_URL}/feeds/{name}.xml "
                 "in Miniflux.")


@app.post("/accounts/{username}/remove")
def remove_account(request: Request, username: str):
    if not _from_this_page(request):
        raise HTTPException(403)
    db: DB = request.app.state.db
    name = username.lower()
    account = db.account(name)
    db.remove_account(name)
    if account and not account["following"]:
        return _back(f"Discarded the preview of @{name}.")
    log.info("Removed @%s", name)
    return _back(f"Removed @{name}. Unsubscribe from its feed in Miniflux too.")


@app.post("/previews")
def preview(request: Request, username: str = Form("")):
    """Reads an account's latest posts so you can decide whether to follow it. The checker does the reading,
    so it's paced like every other request to Instagram."""
    if not _from_this_page(request):
        raise HTTPException(403)
    name = username_from(username)
    if not name:
        return _back(f"“{username.strip()[:60]}” isn't an Instagram username")
    db: DB = request.app.state.db
    account = db.account(name)
    now = int(time.time())
    fresh = (account and account["last_check"] and not account["last_error"]
             and now - account["last_check"] < config.PREVIEW_REUSE_HOURS * 3600)
    if not account or not (account["following"] or account["preview_requested"] or fresh):
        db.request_preview(name, now)
        if request.app.state.poller:
            request.app.state.poller.wake.set()
    return RedirectResponse(f"/preview/{quote(name)}", status_code=303)


@app.post("/accounts/{username}/follow")
def follow(request: Request, username: str):
    if not _from_this_page(request):
        raise HTTPException(403)
    name = username.lower()
    if not request.app.state.db.account(name):
        return _back(f"The preview of @{name} is gone. Preview it again, or add it.")
    request.app.state.db.add_account(name)
    log.info("Following @%s", name)
    return _back(f"Following @{name}. Add {config.INTERNAL_URL}/feeds/{name}.xml in Miniflux.")


@app.get("/preview/{username}", response_class=HTMLResponse)
def preview_page(request: Request, username: str):
    db: DB = request.app.state.db
    e = html.escape
    account = db.account(username.lower())
    if not account:
        return _back(f"No preview of @{username.lower()[:30]}")
    u = e(account["username"])
    name = f"{e(account['full_name'])} (@{u})" if account["full_name"] else f"@{u}"
    if account["preview_requested"]:
        return HTMLResponse(_page(f"Reading @{u}", f"""<p><a href="/">← ig2rss</a></p>
<h1>Reading @{u}…</h1>
<p class="muted">Asking Instagram for the latest posts. This page updates by itself, usually within a minute.</p>""",
                                  head='<meta http-equiv="refresh" content="3">'))
    if account["following"]:
        actions = (f"<p>You follow this account. Its feed: <code>{config.INTERNAL_URL}/feeds/{u}.xml</code></p>")
    else:
        actions = (f"<form method='post' action='/accounts/{u}/follow' class='inline'><button>Follow</button></form> "
                   f"<form method='post' action='/accounts/{u}/remove' class='inline'><button>Discard</button></form>")
    error = ""
    if account["last_error"]:
        error = (f"<p class='note'>Couldn't read @{u}: {e(account['last_error'])}</p>"
                 f"<form method='post' action='/previews'><input type='hidden' name='username' value='{u}'>"
                 f"<button>Try again</button></form>")
    items = []
    for p in db.posts(account["username"], config.FEED_ITEMS):
        link = feed.post_url(p["shortcode"])
        when = datetime.fromtimestamp(p["published"], tz=config.TZ).strftime("%b %-d, %Y")
        items.append(f"<article><p class='muted'>{when} · <a href='{e(link)}'>on Instagram</a></p>"
                     + feed.content_html(p["caption"], db.images(p["shortcode"]), bool(p["is_video"]),
                                         p["media_count"], link) + "</article>")
    posts = "".join(items) or ("" if error else "<p class='muted'>No posts.</p>")
    read = f"Read {_when(account['last_check'])}." if account["last_check"] else ""
    return _page(f"@{u}", f"""<p><a href="/">← ig2rss</a></p>
<h1>{name}</h1>
<p class="muted">Latest posts. {read}</p>
{error}
{actions}
{posts}
{actions if items else ""}""")


def _when(ts) -> str:
    if not ts:
        return "never"
    return datetime.fromtimestamp(int(ts), tz=config.TZ).strftime("%b %-d, %H:%M")


@app.get("/", response_class=HTMLResponse)
def status(request: Request, msg: str = ""):
    db: DB = request.app.state.db
    e = html.escape
    now = time.time()
    notes = []
    if problem := db.get_state("problem"):
        notes.append(f"Not checking: {e(problem)}")
    paused_until = float(db.get_state("paused_until", "0") or 0)
    if paused_until > now:
        notes.append(f"Waiting until {_when(paused_until)}: {e(db.get_state('pause_reason'))}")
    login = f"logged in as @{e(config.LOGIN)}" if config.LOGIN else "not logged in"
    rows = []
    for a in db.accounts():
        u = e(a["username"])
        rows.append(
            f"<tr><td><a href='/preview/{u}'>@{u}</a></td><td>{a['post_count']}</td>"
            f"<td>{_when(a['last_success'])}</td>"
            f"<td>{_when(a['last_check'] + config.POLL_HOURS * 3600) if a['last_check'] else 'soon'}</td>"
            f"<td>{e(a['last_error'])}</td>"
            f"<td><a href='/feeds/{u}.xml'>feed</a><br><code>{config.INTERNAL_URL}/feeds/{u}.xml</code></td>"
            f"<td><form method='post' action='/accounts/{u}/remove' "
            f"onsubmit=\"return confirm('Stop following @{u}? Its saved posts are deleted too.')\">"
            f"<button>Remove</button></form></td></tr>")
    previews = [f"<a href='/preview/{e(a['username'])}'>@{e(a['username'])}</a>" for a in db.accounts(following=False)]
    previews_html = (f"<p class='muted'>Previewed, not followed: {', '.join(previews)}</p>" if previews else "")
    table = ("<table><tr><th>Account</th><th>Posts</th><th>Last read</th><th>Next check</th><th>Problem</th>"
             "<th>Feed (link, and the address to add in Miniflux)</th><th></th></tr>" + "".join(rows) + "</table>"
             if rows else "<p>No accounts yet. Add one above.</p>")
    msg_html = f"<p class='msg'>{e(msg)}</p>" if msg else ""
    notes_html = "".join(f"<p class='note'>{n}</p>" for n in notes)
    return _page("ig2rss", f"""<h1>ig2rss</h1>
<p class="muted">Instagram, {login}. Each account is checked about every {config.POLL_HOURS:g} hours.</p>
{msg_html}
{notes_html}
<form method="post" action="/accounts">
<input name="username" placeholder="username, @name or profile link" required autocapitalize="none" autocorrect="off">
<button formaction="/previews">Preview</button> <button>Add</button>
</form>
<p class="muted">Preview reads the latest posts from Instagram now, so you can decide before adding.</p>
{previews_html}
{table}""")


def _page(title: str, body: str, head: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>{head}
<style>
body {{ font: 15px/1.4 system-ui, sans-serif; margin: 1rem; max-width: 64rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: .4rem; border-bottom: 1px solid #ddd; vertical-align: top; }}
code {{ font-size: 12px; word-break: break-all; }}
input, button {{ font: inherit; padding: .3rem .5rem; }}
input {{ width: min(22rem, 60%); }}
form.inline {{ display: inline; }}
article {{ border-top: 1px solid #ddd; padding: .5rem 0; max-width: 34rem; }}
article img {{ max-width: 100%; }}
.msg {{ background: #e7f1ff; padding: .5rem; }}
.note {{ background: #fff3cd; padding: .5rem; }}
.muted {{ color: #666; }}
</style></head><body>
{body}
</body></html>"""
