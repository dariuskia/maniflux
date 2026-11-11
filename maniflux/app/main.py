import asyncio
import hashlib
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from fastapi import BackgroundTasks, FastAPI, Form, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, digests, kinds
from .ai import AIError, Summarizer
from .library import Library, LibraryError
from .db import DB
from .miniflux import Miniflux, MinifluxError
from .ranking import SHOW, Item, budget_set, rank, rule_is_live, search, shown_by
from .search import ArchiveIndex
from .text import clean_author, full_text, parse_time
from .vault import VaultIndex, published_day, split_authors

log = logging.getLogger("maniflux")
HERE = Path(__file__).parent
BUDGETS = (10, 20, 45)


def now() -> datetime:
    return datetime.now(config.TZ)


async def poll_forever(app: FastAPI):
    """Every few minutes: score the unread entries and drop scores for ones no longer unread."""
    while True:
        try:
            await refresh_scores(app)
        except MinifluxError as e:
            log.warning("score refresh skipped: %s", e)
        except Exception:
            log.exception("score refresh failed")
        await asyncio.sleep(config.POLL_MINUTES * 60)


async def refresh_scores(app: FastAPI):
    db: DB = app.state.db
    entries = await app.state.mf.unread(config.LIST_LIMIT)
    items = rank(entries, db.steering(), now())
    rows = [(i.id, round(i.score, 3), f"hidden: {i.hidden}" if i.hidden else i.reason) for i in items]
    await run_in_threadpool(db.replace_scores, rows)
    if entries:
        top = max(e["id"] for e in entries)
        if top > int(db.get_state("max_entry_id", 0)):
            db.set_state("max_entry_id", top)


async def rescore(app: FastAPI):
    """Refresh stored scores after a weight or rule change; the list itself always ranks live."""
    try:
        await refresh_scores(app)
    except MinifluxError as e:
        log.warning("score refresh skipped: %s", e)


# ---------- digests: running and scheduling ----------

async def run_digest(app: FastAPI, digest_id: int) -> int | None:
    """Write one digest from what its groups' feeds published since the last one. Returns the run's id."""
    db: DB = app.state.db
    running: set = app.state.digests_running
    d = db.digest(digest_id)
    if d is None or digest_id in running:
        return None
    running.add(digest_id)
    end = datetime.now(timezone.utc)
    last = next((r for r in db.runs(digest_id, limit=50) if r["status"] in ("done", "empty")), None)
    start = parse_time(last["period_end"]) if last else end - timedelta(days=digests.FIRST_LOOKBACK[d["frequency"]])
    stamp = lambda t: t.isoformat(timespec="seconds")
    run_id = db.x("INSERT INTO digest_run (digest_id, status, period_start, period_end, started_at) "
                  "VALUES (?, 'running', ?, ?, ?)", (digest_id, stamp(start), stamp(end), stamp(end)))
    try:
        steer = db.steering()
        feed_ids = sorted(f for f, groups in steer.memberships.items() if groups & d["group_ids"])
        entries = []
        for f in feed_ids:
            got = await app.state.mf.feed_entries(f, published_after=int(start.timestamp()), limit=500)
            entries += [e for e in got if start < parse_time(e["published_at"]) <= end]
        items = digests.to_items(entries)
        listing = json.dumps([{k: it[k] for k in ("n", "id", "title", "url", "feed", "date")} for it in items])
        if not items:
            db.x("UPDATE digest_run SET status = 'empty', items = ?, finished_at = ? WHERE id = ?",
                 (listing, stamp(datetime.now(timezone.utc)), run_id))
            return run_id
        body = await digests.write(app.state.ai, config.DIGEST_MODEL, config.OPENROUTER_MODEL, d, items,
                                   start.astimezone(config.TZ), end.astimezone(config.TZ))
        db.x("UPDATE digest_run SET status = 'done', item_count = ?, items = ?, body = ?, finished_at = ? WHERE id = ?",
             (len(items), listing, body.strip(), stamp(datetime.now(timezone.utc)), run_id))
        await _refresh_digest_feed(app)
    except (MinifluxError, AIError) as e:
        db.x("UPDATE digest_run SET status = 'error', error = ?, finished_at = ? WHERE id = ?",
             (str(e), stamp(datetime.now(timezone.utc)), run_id))
    except Exception as e:
        log.exception("digest %s failed", digest_id)
        db.x("UPDATE digest_run SET status = 'error', error = ?, finished_at = ? WHERE id = ?",
             (f"Something went wrong ({type(e).__name__})", stamp(datetime.now(timezone.utc)), run_id))
    finally:
        running.discard(digest_id)
    return run_id


async def _digest_feed_id(app: FastAPI) -> int | None:
    feeds = await app.state.mf.feeds()
    return next((f["id"] for f in feeds if f["feed_url"] == config.DIGEST_FEED_URL), None)


async def _refresh_digest_feed(app: FastAPI):
    """Ask Miniflux to fetch the digests feed now, so a new digest shows up in the list straight away."""
    try:
        feed_id = await _digest_feed_id(app)
        if feed_id:
            await app.state.mf.refresh_feed(feed_id)
    except MinifluxError as e:
        log.info("couldn't refresh the digests feed: %s", e)


def _last_finished(db: DB, digest_id: int) -> datetime | None:
    last = next((r for r in db.runs(digest_id, limit=50) if r["status"] in ("done", "empty")), None)
    return parse_time(last["finished_at"]).astimezone(config.TZ) if last else None


async def digests_forever(app: FastAPI):
    """Every minute: run any digest that's due."""
    while True:
        try:
            for d in app.state.db.digests():
                if not d["active"] or not d["next_run_at"]:
                    continue
                local_now = now()
                if parse_time(d["next_run_at"]) <= local_now:
                    nxt = digests.next_run(d, local_now, _last_finished(app.state.db, d["id"]))
                    app.state.db.x("UPDATE digest SET next_run_at = ? WHERE id = ?",
                                   (nxt.isoformat(timespec="seconds"), d["id"]))
                    await run_digest(app, d["id"])
        except Exception:
            log.exception("digest schedule check failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "mf"):
        app.state.mf = Miniflux(config.MINIFLUX_URL, config.MINIFLUX_API_KEY)
    if not hasattr(app.state, "db"):
        app.state.db = DB(config.DB_PATH)
    if not hasattr(app.state, "vault"):
        app.state.vault = VaultIndex(config.CONTENT_DIR)
    if not hasattr(app.state, "ai"):
        app.state.ai = Summarizer(config.OPENROUTER_API_KEY, config.OPENROUTER_MODEL)
    if not hasattr(app.state, "library"):
        app.state.library = Library(config.LIBRARY_URL, config.LIBRARY_TOKEN)
    app.state.save_lock = asyncio.Lock()
    app.state.archive_index = ArchiveIndex()
    app.state.digests_running = set()
    tasks = []
    if os.environ.get("MANIFLUX_NO_POLL") != "1":
        tasks = [asyncio.create_task(poll_forever(app)), asyncio.create_task(digests_forever(app))]
    yield
    for t in tasks:
        t.cancel()
    await app.state.mf.close()
    await app.state.ai.close()
    await app.state.library.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def _static_version() -> str:
    """Changes whenever a static file changes, so browsers never run an old copy."""
    h = hashlib.sha1()
    for p in sorted((HERE / "static").iterdir()):
        h.update(p.read_bytes())
    return h.hexdigest()[:10]


templates.env.globals["v"] = _static_version()
templates.env.filters["author"] = clean_author


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx)


# ---------- list ----------

TABS = [("all", "All"), ("read", "Read"), ("watch", "Watch"), ("listen", "Listen")]


@app.get("/", response_class=HTMLResponse)
async def list_view(request: Request, budget: int | None = None, show: str = "all", group: int | None = None,
                    q: str = "", msg: str | None = None):
    db: DB = request.app.state.db
    steer = db.steering()
    show = show if show in SHOW else "all"
    group = group if group in steer.groups else None
    q = " ".join(q.split())[:200]
    view = {"show": show, "group": group, "budget": budget, "q": q}
    ctx = dict(budgets=BUDGETS, tabs=TABS, groups=steer.groups, view=view, msg=msg)
    try:
        entries = await request.app.state.mf.unread(config.LIST_LIMIT)
    except MinifluxError as e:
        return page(request, "list.html", error=str(e), items=[], **ctx)
    items = rank(entries, steer, now())
    visible = [i for i in items if not i.hidden]
    in_group = shown_by(search(visible, q), "all", group)
    counts = {key: len(shown_by(in_group, key, None)) for key, _ in TABS}
    shown = shown_by(in_group, show, None)
    budget_info = None
    if budget:
        shown, unknown = budget_set(shown, budget)
        budget_info = {"total": sum(i.minutes for i in shown), "unknown": unknown}
    return page(request, "list.html", items=shown, hidden=len(items) - len(visible), counts=counts,
                budget_info=budget_info, error=None, **ctx)


def card(request: Request, item: Item, error: str | None = None) -> HTMLResponse:
    return page(request, "_card.html", item=item, error=error)


def done_row(request: Request, entry: dict, text: str, undo: str | None = None, error: bool = False) -> HTMLResponse:
    return page(request, "_done.html", entry=entry, text=text, undo=undo, error=error)


async def _entry(request: Request, entry_id: int) -> dict:
    return await request.app.state.mf.entry(entry_id)


def _back(msg: str, back: str = "/") -> RedirectResponse:
    """Back to the list view the item was opened from (its search, tab, group and budget), with a message."""
    parts = urlsplit(back)
    if parts.scheme or parts.netloc or parts.path not in ("/", "/archive"):
        parts = urlsplit("/")  # only ever back to the list or the archive on this site
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "msg"] + [("msg", msg)]
    return RedirectResponse(f"{parts.path}?{urlencode(query)}", status_code=303)


@app.post("/e/{entry_id}/read")
async def mark_read(request: Request, entry_id: int, back: str = Form("/")):
    db: DB = request.app.state.db
    try:
        entry = await _entry(request, entry_id)
        await request.app.state.mf.set_status([entry_id], "read")
    except MinifluxError as e:
        if not is_htmx(request):
            return _back(str(e), back)
        return done_row(request, {"id": entry_id, "title": ""}, str(e), error=True)
    db.feedback(entry, "read")
    if not is_htmx(request):
        return _back(f"Archived: {entry['title']}", back)
    return done_row(request, entry, "Archived", undo="read")


@app.post("/e/{entry_id}/unread")
async def keep_unread(request: Request, entry_id: int, back: str = Form("/")):
    """From the reading view: opening archived it; put it back in the list."""
    try:
        entry = await _entry(request, entry_id)
        await request.app.state.mf.set_status([entry_id], "unread")
    except MinifluxError as e:
        if is_htmx(request):
            return done_row(request, {"id": entry_id, "title": ""}, str(e), error=True)
        return _back(str(e), back)
    if is_htmx(request):
        return done_row(request, entry, "Unarchived, back in the list")
    return _back(f"Unarchived: {entry['title']}", back)


@app.post("/e/{entry_id}/undo")
async def undo(request: Request, entry_id: int):
    db: DB = request.app.state.db
    try:
        await request.app.state.mf.set_status([entry_id], "unread")
        entry = await _entry(request, entry_id)
    except MinifluxError as e:
        return done_row(request, {"id": entry_id, "title": ""}, str(e), error=True)
    # Undone, so it shouldn't count as feedback
    db.x("DELETE FROM feedback WHERE id = (SELECT MAX(id) FROM feedback WHERE entry_id = ? AND action = 'read')",
         (entry_id,))
    item = rank([entry], db.steering(), now())[0]
    return card(request, item)


@app.post("/e/{entry_id}/save")
async def save(request: Request, entry_id: int, thought: str = Form(""), back: str = Form("/")):
    db: DB = request.app.state.db
    vault: VaultIndex = request.app.state.vault
    try:
        entry = await _entry(request, entry_id)
    except MinifluxError as e:
        return _save_reply(request, {"id": entry_id, "title": ""}, str(e), error=True, back=back)

    async with request.app.state.save_lock:
        existing = await run_in_threadpool(vault.find, entry["url"])
        if existing:
            # Saved again: the library service links the existing note in today's journal again
            try:
                await request.app.state.library.save({"url": entry["url"], "relink": True,
                                                      "date_added": now().isoformat(timespec="seconds")})
                text = f"Already in Obsidian: {existing.name} (linked in today's journal again)"
            except LibraryError:
                text = f"Already in Obsidian: {existing.name}"
        else:
            # The library service works out the note (type, description, cover...) in the background,
            # so saving doesn't wait for it. If it can't be reached, nothing is saved and the card stays.
            text_body = full_text(entry.get("content", ""))
            job = {"url": entry["url"], "source": (entry.get("feed") or {}).get("title") or "",
                   "thought": thought, "title": entry.get("title") or "",
                   "creators": split_authors(entry.get("author", "")),
                   "published": published_day(entry, config.TZ), "minutes": entry.get("reading_time") or None,
                   "text": text_body[:20000], "date_added": now().isoformat(timespec="seconds"),
                   "relink": True}  # if the library already has it under another link spelling, link that again
            try:
                answer = await request.app.state.library.save(job)
            except LibraryError as e:
                msg = f"Not saved: {e}. Try again in a moment."
                if not is_htmx(request):
                    return _back(msg, back)
                return card(request, rank([entry], db.steering(), now())[0], error=msg)
            text = (f"Already in Obsidian: {answer.get('note')}.md" if answer.get("status") == "exists"
                    else "Saved to Obsidian")
            path = ""
            db.add_saved(entry_id, entry["url"], path)
            db.feedback(entry, "save")

        try:
            await request.app.state.mf.set_status([entry_id], "read")
        except MinifluxError as e:
            return _save_reply(request, entry, f"{text}, but couldn't archive it: {e}", error=True, back=back)
    return _save_reply(request, entry, text, back=back)


def _save_reply(request: Request, entry: dict, text: str, error: bool = False, back: str = "/"):
    if not is_htmx(request):
        return _back(text, back)
    return done_row(request, entry, text, error=error)


# ---------- archive ----------

ARCHIVE_PAGE = 50


def _ago(stamp: str) -> str:
    seconds = (now() - parse_time(stamp)).total_seconds()
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    days = int(seconds // 86400)
    return "yesterday" if days == 1 else f"{days} days ago"


@app.get("/archive", response_class=HTMLResponse)
async def archive_view(request: Request, q: str = "", page_no: int = Query(0, alias="page"), msg: str | None = None):
    db: DB = request.app.state.db
    q = " ".join(q.split())[:200]
    page_no = max(0, page_no)
    view = {"q": q, "page": page_no}
    try:
        if q:
            # Searched in Maniflux, so partial words match while typing (Miniflux only matches whole words)
            index: ArchiveIndex = request.app.state.archive_index
            await index.refresh(request.app.state.mf)
            found = index.search(q)
            total = len(found)
            entries = found[page_no * ARCHIVE_PAGE:(page_no + 1) * ARCHIVE_PAGE]
        else:
            entries, total = await request.app.state.mf.archived(ARCHIVE_PAGE, page_no * ARCHIVE_PAGE)
    except MinifluxError as e:
        return page(request, "archive.html", error=str(e), items=[], view=view, msg=msg)
    steer = db.steering()
    saved = {r["entry_id"] for r in db.q("SELECT entry_id FROM saved")}
    items = []
    for e in entries:
        it = rank([e], steer, now())[0]
        items.append({"item": it, "archived": _ago(e["changed_at"]), "saved": e["id"] in saved})
    return page(request, "archive.html", items=items, total=total, view=view, msg=msg, error=None,
                more=(page_no + 1) * ARCHIVE_PAGE < total)


# ---------- reading view ----------

@app.get("/e/{entry_id}", response_class=HTMLResponse)
async def read_view(request: Request, entry_id: int):
    db: DB = request.app.state.db
    try:
        entry = await _entry(request, entry_id)
    except MinifluxError as e:
        return page(request, "entry.html", error=str(e), entry=None)
    db.feedback(entry, "open")
    item = rank([entry], db.steering(), now())[0]
    # Opening an item archives it (marks it read in Miniflux); "Unarchive" puts it back
    problem, archived_now = None, False
    if entry["status"] == "unread":
        try:
            await request.app.state.mf.set_status([entry_id], "read")
            archived_now = True
        except MinifluxError as e:
            problem = f"Couldn't archive it: {e}"
    return page(request, "entry.html", entry=entry, item=item, error=None, problem=problem,
                archived_now=archived_now)


@app.post("/e/{entry_id}/full", response_class=HTMLResponse)
async def load_full_article(request: Request, entry_id: int):
    try:
        data = await request.app.state.mf.fetch_content(entry_id)
    except MinifluxError as e:
        return HTMLResponse(f'<p class="error">Couldn\'t load the full article. {escape(str(e))}</p>')
    return HTMLResponse(data.get("content") or '<p class="muted">The page had no article text.</p>')


@app.get("/e/{entry_id}/original")
async def original(request: Request, entry_id: int):
    try:
        entry = await _entry(request, entry_id)
    except MinifluxError as e:
        return _back(str(e))
    request.app.state.db.feedback(entry, "original")
    # Opening the original counts as reading it too
    if entry["status"] == "unread":
        try:
            await request.app.state.mf.set_status([entry_id], "read")
        except MinifluxError as e:
            log.warning("couldn't mark %s read: %s", entry_id, e)
    return RedirectResponse(entry["url"], status_code=302)


# ---------- digests: pages ----------

def _digest_view(d: dict, groups: dict) -> dict:
    return dict(d, schedule=digests.describe_schedule(d),
                group_names=", ".join(sorted(groups[g]["name"] for g in d["group_ids"] if g in groups)),
                next_at=parse_time(d["next_run_at"]).astimezone(config.TZ) if d["next_run_at"] and d["active"] else None)


@app.get("/digests", response_class=HTMLResponse)
async def digests_view(request: Request, msg: str | None = None):
    db: DB = request.app.state.db
    try:
        subscribed = await _digest_feed_id(request.app) is not None
    except MinifluxError:
        subscribed = None
    latest = {}
    for r in db.runs(limit=200):
        latest.setdefault(r["digest_id"], r)
    groups = db.groups()
    return page(request, "digests.html", digests=[_digest_view(d, groups) for d in db.digests()], latest=latest,
                subscribed=subscribed, msg=msg, run_title=digests.run_title)


async def _form_page(request: Request, d: dict, error: str | None = None):
    db: DB = request.app.state.db
    names = await _feed_names(request)
    group_feeds = {}
    for feed_id, group_ids in db.memberships().items():
        for g in group_ids:
            if feed_id in names:
                group_feeds.setdefault(g, []).append(names[feed_id])
    return page(request, "digest_form.html", d=d, groups=db.groups(), group_feeds=group_feeds, error=error,
                frequencies=digests.FREQUENCIES, weekdays=digests.WEEKDAYS,
                runs=db.runs(d["id"]) if d.get("id") else [], run_title=digests.run_title,
                running=d.get("id") in request.app.state.digests_running)


async def _feed_names(request: Request) -> dict[int, str]:
    try:
        return {f["id"]: f["title"] for f in await request.app.state.mf.feeds()}
    except MinifluxError:
        return {}


@app.get("/digests/new", response_class=HTMLResponse)
async def new_digest(request: Request):
    return await _form_page(request, {"id": None, "name": "", "instructions": "", "frequency": "weekly", "weekday": 0,
                                "monthday": 1, "at": "08:00", "active": 1, "group_ids": set()})


@app.get("/digests/{digest_id:int}", response_class=HTMLResponse)
async def edit_digest(request: Request, digest_id: int):
    d = request.app.state.db.digest(digest_id)
    if d is None:
        return RedirectResponse("/digests", status_code=303)
    return await _form_page(request, d)


@app.post("/digests")
@app.post("/digests/{digest_id:int}/edit")
async def save_digest(request: Request, digest_id: int | None = None):
    db: DB = request.app.state.db
    form = await request.form()
    groups = db.groups()
    d = {
        "id": digest_id,
        "name": " ".join(str(form.get("name", "")).split())[:80],
        "instructions": str(form.get("instructions", "")).strip()[:4000],
        "frequency": form.get("frequency") if form.get("frequency") in digests.FREQUENCIES else "weekly",
        "weekday": min(6, max(0, int(form.get("weekday") or 0))),
        "monthday": min(28, max(1, int(form.get("monthday") or 1))),
        "at": form.get("at") if re.fullmatch(r"\d{2}:\d{2}", str(form.get("at", ""))) else "08:00",
        "active": 1 if form.get("active") else 0,
        "group_ids": {int(g) for g in form.getlist("group") if str(g).isdigit() and int(g) in groups},
    }
    problem = (not d["name"] and "Give the digest a name.") or (not d["instructions"] and "Say what the digest "
               "should include.") or (not d["group_ids"] and "Pick at least one group of feeds.")
    if problem:
        return await _form_page(request, d, error=problem)
    last = _last_finished(db, digest_id) if digest_id else None
    d["next_run_at"] = digests.next_run(d, now(), last).isoformat(timespec="seconds") if d["active"] else None
    digest_id = db.save_digest(digest_id, d, d["group_ids"])
    return RedirectResponse(f"/digests/{digest_id}", status_code=303)


@app.post("/digests/{digest_id:int}/run")
async def run_now(request: Request, digest_id: int):
    if request.app.state.db.digest(digest_id) is not None:
        asyncio.create_task(run_digest(request.app, digest_id))
        await asyncio.sleep(0.2)  # so the page shows it running
    return RedirectResponse(f"/digests/{digest_id}", status_code=303)


@app.post("/digests/{digest_id:int}/delete")
async def delete_digest(request: Request, digest_id: int):
    request.app.state.db.delete_digest(digest_id)
    return RedirectResponse("/digests", status_code=303)


@app.get("/digests/runs/{run_id:int}", response_class=HTMLResponse)
async def digest_run_view(request: Request, run_id: int):
    r = request.app.state.db.run(run_id)
    if r is None:
        return RedirectResponse("/digests", status_code=303)
    items = json.loads(r["items"] or "[]")
    return page(request, "digest_run.html", r=r, title=digests.run_title(r),
                body=digests.render(r["body"] or "", items) if r["body"] else "",
                sources=digests.sources_html(items) if items else "")


@app.get("/digests/feed.xml")
async def digest_feed(request: Request):
    runs = [r for r in request.app.state.db.runs(limit=30) if r["status"] == "done"]
    return Response(digests.atom(runs, config.PUBLIC_URL), media_type="application/atom+xml")


@app.post("/digests/subscribe")
async def subscribe_digests(request: Request):
    """Subscribe Miniflux to the digests feed, so digests show up in the list."""
    mf = request.app.state.mf
    try:
        if await _digest_feed_id(request.app) is None:
            categories = await mf.categories()
            await mf.subscribe(config.DIGEST_FEED_URL, min(c["id"] for c in categories))
        msg = "Digests will now show up in your list."
    except MinifluxError as e:
        msg = f"Couldn't add the digests feed to Miniflux: {e}"
    return RedirectResponse(f"/digests?msg={quote(msg)}", status_code=303)


# ---------- feeds and groups ----------

@app.get("/weights")
async def old_weights():
    return RedirectResponse("/feeds", status_code=301)


def _site_link(feed: dict) -> str:
    """Where a feed's name links on the Feeds page: its website. Instagram feeds link to ig2rss's page of
    the posts it has saved. Newsletter feeds from Kill the
    Newsletter have no website (their site is the feed file itself), so they get no link. Maniflux's
    own digest feed links within Maniflux, wherever it runs now."""
    site = feed.get("site_url") or ""
    # ig2rss: its own page of the posts it has saved, rather than the Instagram profile
    if m := re.match(r"(https?://[^/]+)/feeds/([a-z0-9._]+)\.xml$", feed.get("feed_url") or ""):
        if "instagram.com" in site:
            return f"{m.group(1)}/preview/{m.group(2)}"
    if not site or site == feed.get("feed_url") or site.endswith(".xml"):
        return ""
    if site.startswith(("http://127.0.0.1:8090", "http://localhost:8090")):
        return site.split(":8090", 1)[1] or "/"
    return site


async def _feed_rows(request: Request) -> tuple[list[dict], dict]:
    """Every Miniflux feed with its weight and groups, plus the groups. Raises MinifluxError."""
    steer = request.app.state.db.steering()
    feeds = await request.app.state.mf.feeds()
    rows = [{"id": f["id"], "title": f["title"], "site_url": _site_link(f), "weight": steer.feed_weights.get(f["id"], 1.0),
             "groups": steer.memberships.get(f["id"], set()) & set(steer.groups)} for f in feeds]
    for r in rows:  # the feed's groups by name, in the order the page lists groups
        r["group_names"] = [g["name"] for gid, g in steer.groups.items() if gid in r["groups"]]
    return rows, steer.groups


def _group_lists(rows: list[dict], gid: int) -> tuple[list[dict], list[dict]]:
    """A group's feeds, and the feeds that could be added to it, each by name."""
    by_name = sorted(rows, key=lambda r: r["title"].lower())
    return [r for r in by_name if gid in r["groups"]], [r for r in by_name if gid not in r["groups"]]


@app.get("/feeds", response_class=HTMLResponse)
async def feeds_view(request: Request, msg: str | None = None):
    try:
        rows, groups = await _feed_rows(request)
    except MinifluxError as e:
        groups = request.app.state.db.groups()
        return page(request, "feeds.html", error=str(e), feeds=[], groups=groups, lists={}, msg=msg)
    # Feeds not in any group first, so new ones get sorted
    rows.sort(key=lambda r: (bool(r["groups"]), r["title"].lower()))
    lists = {g: _group_lists(rows, g) for g in groups}
    return page(request, "feeds.html", feeds=rows, groups=groups, lists=lists, error=None, msg=msg)


async def _membership_changed(request: Request, feed_id: int, group_id: int, edited_from: str) -> HTMLResponse:
    """After a feed joins or leaves a group: the part of the page that was edited, plus updates to the
    other places on the page that show the same thing (the group's list and count, the feed's chip)."""
    rows, groups = await _feed_rows(request)
    members, others = _group_lists(rows, group_id)
    on = any(r["id"] == feed_id for r in members)
    names = next((r["group_names"] for r in rows if r["id"] == feed_id), [])
    return page(request, "_membership.html", g=groups[group_id], members=members, others=others,
                feed_id=feed_id, on=on, names=names, edited_from=edited_from)


def _weight_label(weight: float) -> HTMLResponse:
    return HTMLResponse("off" if weight == 0 else f"×{weight:g}")


@app.post("/feeds/{feed_id}/weight", response_class=HTMLResponse)
async def set_feed_weight(request: Request, tasks: BackgroundTasks, feed_id: int, weight: float = Form(...)):
    weight = max(0.0, min(3.0, weight))
    request.app.state.db.set_weight("feed", feed_id, weight)
    tasks.add_task(rescore, request.app)
    return _weight_label(weight)


@app.post("/feeds/{feed_id}/groups/{group_id}", response_class=HTMLResponse)
async def toggle_feed_group(request: Request, tasks: BackgroundTasks, feed_id: int, group_id: int):
    """A chip under a feed: puts the feed in the group or takes it out."""
    db: DB = request.app.state.db
    if group_id not in db.groups():
        return Response(status_code=404)
    db.toggle_member(feed_id, group_id)
    tasks.add_task(rescore, request.app)
    return await _membership_changed(request, feed_id, group_id, "chip")


@app.post("/groups/{group_id}/feeds", response_class=HTMLResponse)
async def add_to_group(request: Request, tasks: BackgroundTasks, group_id: int, feed_id: int = Form(...)):
    """'Add a feed…' in a group's list."""
    db: DB = request.app.state.db
    if group_id not in db.groups():
        return Response(status_code=404)
    db.set_member(feed_id, group_id, True)
    tasks.add_task(rescore, request.app)
    return await _membership_changed(request, feed_id, group_id, "list")


@app.post("/groups/{group_id}/feeds/{feed_id}/remove", response_class=HTMLResponse)
async def remove_from_group(request: Request, tasks: BackgroundTasks, group_id: int, feed_id: int):
    """The × next to a feed in a group's list."""
    db: DB = request.app.state.db
    if group_id not in db.groups():
        return Response(status_code=404)
    db.set_member(feed_id, group_id, False)
    tasks.add_task(rescore, request.app)
    return await _membership_changed(request, feed_id, group_id, "list")


@app.post("/groups")
async def add_group(request: Request, name: str = Form("")):
    name = " ".join(name.split())[:60]
    if name and request.app.state.db.add_group(name) is None:
        return RedirectResponse(f"/feeds?msg={quote(f'There is already a group called {name}')}", status_code=303)
    return RedirectResponse("/feeds", status_code=303)


@app.post("/groups/{group_id}/rename")
async def rename_group(request: Request, group_id: int, name: str = Form("")):
    name = " ".join(name.split())[:60]
    if name and not request.app.state.db.rename_group(group_id, name):
        return RedirectResponse(f"/feeds?msg={quote(f'There is already a group called {name}')}", status_code=303)
    return RedirectResponse("/feeds", status_code=303)


@app.post("/groups/{group_id}/weight", response_class=HTMLResponse)
async def set_group_weight(request: Request, tasks: BackgroundTasks, group_id: int, weight: float = Form(...)):
    weight = max(0.0, min(3.0, weight))
    request.app.state.db.set_group_weight(group_id, weight)
    tasks.add_task(rescore, request.app)
    return _weight_label(weight)


@app.post("/groups/{group_id}/delete")
async def delete_group(request: Request, tasks: BackgroundTasks, group_id: int):
    request.app.state.db.delete_group(group_id)
    tasks.add_task(rescore, request.app)
    return RedirectResponse("/feeds", status_code=303)


# ---------- rules ----------

@app.get("/rules", response_class=HTMLResponse)
async def rules_view(request: Request):
    db: DB = request.app.state.db
    try:
        feeds = await request.app.state.mf.feeds()
        feed_names = {str(f["id"]): f["title"] for f in feeds}
    except MinifluxError:
        feeds, feed_names = [], {}
    groups = db.groups()
    group_names = {str(g): v["name"] for g, v in groups.items()}
    today = now().date()
    rules = [dict(r, live=rule_is_live(r, today)) for r in db.rules()]
    for r in rules:
        r["text"] = describe_rule(r, feed_names, group_names)
    return page(request, "rules.html", rules=rules, types=kinds.TYPES, groups=groups,
                feeds=sorted(feeds, key=lambda f: f["title"].lower()))


def describe_rule(r: dict, feed_names: dict, group_names: dict) -> str:
    if r["kind"] == "limit":
        kind, _, target = r["value"].partition(":")
        if kind == "type":
            name = f"{target} items"
        elif kind == "group":
            name = f"items in {group_names.get(target, 'a deleted group')}"
        else:
            name = f"items from {feed_names.get(target, 'feed ' + target)}"
        return f"At most {r['amount']} {name} a day"
    what = f"Items with “{r['value']}”" if r["kind"] == "keyword" else f"{r['value'].capitalize()} items"
    return f"{what}: {r['action']}"


@app.post("/rules")
async def add_rule(request: Request, tasks: BackgroundTasks, kind: str = Form(...), value: str = Form(""), action: str = Form("boost"),
                   amount: int | None = Form(None), ends_on: str = Form("")):
    value = value.strip()
    ok = (
        (kind == "keyword" and value and action in ("boost", "lower", "hide"))
        or (kind == "type" and value in kinds.TYPES and action in ("boost", "lower", "hide"))
        or (kind == "limit" and value.partition(":")[0] in ("type", "feed", "group") and (amount or 0) > 0)
    )
    if ok:
        request.app.state.db.add_rule(kind, value, None if kind == "limit" else action,
                                      amount if kind == "limit" else None, ends_on)
        tasks.add_task(rescore, request.app)
    return RedirectResponse("/rules", status_code=303)


@app.post("/rules/{rule_id}/toggle")
async def toggle_rule(request: Request, tasks: BackgroundTasks, rule_id: int):
    request.app.state.db.x("UPDATE rules SET active = 1 - active WHERE id = ?", (rule_id,))
    tasks.add_task(rescore, request.app)
    return RedirectResponse("/rules", status_code=303)


@app.post("/rules/{rule_id}/delete")
async def delete_rule(request: Request, tasks: BackgroundTasks, rule_id: int):
    request.app.state.db.x("DELETE FROM rules WHERE id = ?", (rule_id,))
    tasks.add_task(rescore, request.app)
    return RedirectResponse("/rules", status_code=303)


# ---------- settings ----------

@app.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request):
    db: DB = request.app.state.db
    vault: VaultIndex = request.app.state.vault
    try:
        version = await request.app.state.mf.version()
        mf_error = None
    except MinifluxError as e:
        version, mf_error = None, str(e)
    folder_ok = config.CONTENT_DIR.is_dir()
    notes = await run_in_threadpool(vault.count) if folder_ok else 0
    broken = await run_in_threadpool(vault.broken) if folder_ok else []
    return page(request, "settings.html", version=version, mf_error=mf_error, mf_url=config.MINIFLUX_URL,
                folder=config.CONTENT_DIR, folder_ok=folder_ok, notes=notes, broken=broken,
                counts=db.counts(), last_id=db.get_state("max_entry_id", "none yet"))
