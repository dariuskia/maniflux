"""The library service: every saved link becomes a finished note, in the background.

- POST /save takes a link straight from Link Ingest or Maniflux and answers at once: "exists" (with
  the note) when that link is already saved, else "queued". The worker then writes the finished
  note into content/ with status: ready, and Process New Clips on the Mac links it in the journal.
- Notes that arrive unfinished (the Web Clipper, or Link Ingest when the server was out of reach)
  say status: pending. The service sees them as they sync in, waits until they've been still for a
  minute, then fills them in the same way and sets status: ready. A duplicate becomes
  status: duplicate, and Process New Clips merges it into the original.
- A save that turns out to be already in the library doesn't make a second note: the existing note
  gets relink: <date>, and Process New Clips links it in the journal again for that day.
- Every 30 minutes it also runs the older sweep (enrich.py): covers and IDs for notes that lack
  them, and duplicate flags.

The server never renames or deletes a note that anything links to; only Obsidian does (through
Process New Clips). The one exception is safe: a pending note saved offline, named after the site,
gets its real title, since nothing links to a note until it's ready.
Run by Docker on the personal server; reachable only over Tailscale, with a token.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

import enrich
import fill
import library as L

FOLDER = (L.VAULT / L.NOTES_DIR).resolve()  # resolved, so it matches the paths the watcher reports
TOKEN = os.environ.get("LIBRARY_TOKEN", "")
SETTLE_SECONDS = float(os.environ.get("LIBRARY_SETTLE_SECONDS", "60"))
SWEEP_MINUTES = float(os.environ.get("LIBRARY_SWEEP_MINUTES", "30"))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
queue: asyncio.Queue = asyncio.Queue()
queued: set[str] = set()  # url keys and paths waiting, so the same thing isn't queued twice
changed: dict[Path, float] = {}  # pending notes seen changing, and when


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


class Save(BaseModel):
    url: str
    source: str = ""
    via: str = "link ingest"
    date_added: str = ""
    thought: str = ""  # Maniflux: what you wrote when saving
    # What the device or Maniflux already knows (all optional)
    title: str = ""
    creators: list[str] = []
    published: str = ""
    minutes: int | None = None
    description: str = ""
    text: str = ""
    channel: str = ""  # YouTube, read on the device
    category: str = ""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def relink(path: Path, day: str) -> None:
    """Asks Process New Clips to link an existing note in the journal again, under this day."""
    text = path.read_text()
    path.write_text(L.set_props(text, {"relink": day}))


# ---------- the work ----------

def save_direct(job: dict) -> None:
    index = fill.Index(FOLDER)
    day = (job.get("date_added") or now_iso())[:10]
    if (existing := index.by_url(job["url"])):
        relink(existing, day)
        log(f"already saved: {existing.name} (linked again for {day})")
        return
    note = fill.work_out(job)
    dup, why = index.duplicate(note)
    if dup:
        relink(dup, day)
        log(f"already saved as {dup.name} ({why}); linked again for {day}")
        return
    props = fill.props_for(note, source=job.get("source", ""), via=job.get("via", "link ingest"),
                           date_added=job.get("date_added") or now_iso())
    body = "\n## Notes\n\n" + (job["thought"].strip() + "\n" if job.get("thought", "").strip() else "")
    path = fill.free_name(FOLDER, note["title"])
    with open(path, "x", encoding="utf-8") as f:  # never overwrites
        f.write(L.render(props, body))
    log(f"saved {path.name}: {note['type']}{'/' + note['form'] if note.get('form') else ''}"
        f"{', cover' if note.get('cover') else ''}")


def finish_pending(path: Path) -> None:
    if not path.exists():
        return
    before = path.read_text()
    fm = L.frontmatter(before)
    if fm.get("status") != "pending" or not fm.get("url"):
        return
    index = fill.Index(FOLDER)  # pending notes aren't in it, so this one doesn't match itself
    if (dup := index.by_url(fm["url"])):
        why = "the same link"
    else:
        job = {"url": fm["url"], "title": fm.get("title", ""), "minutes": int(fm["minutes"]) if
               str(fm.get("minutes", "")).isdigit() else None, "published": fm.get("published", ""),
               "description": fm.get("description", ""), "channel": fm.get("yt-channel", ""),
               "category": fm.get("yt-category", "")}
        note = fill.work_out(job)
        dup, why = index.duplicate(note)
    if dup:
        new = L.set_props(before, {"status": "duplicate", "duplicate-of": f"[[{dup.stem}]]"})
        log(f"{path.name} is already saved as {dup.name} ({why}); Process New Clips will merge it")
    else:
        props = fill.props_for(note, source=fm.get("source", ""), via=fm.get("via") or "clip",
                               date_added=fm.get("date-added") or now_iso())
        props["date-archived"] = fm.get("date-archived", "")  # never undo an archive
        new = L.set_props(before, props, remove=("author", "yt-channel", "yt-category"),
                          in_place_of={"creators": "author"})
        log(f"filled in {path.name}: {note['type']}{'/' + note['form'] if note.get('form') else ''}")
    L.frontmatter(new)  # must still parse
    if not path.exists() or path.read_text() != before:  # changed while we worked on it: next time
        return
    # A pending note saved offline is named after the site (the page couldn't be read then). Nothing
    # links to a pending note yet, so it can take its real title; other names are left alone.
    if not dup and path.stem in (fill.host(fm["url"]), "Untitled"):
        target = fill.free_name(FOLDER, note["title"])
        with open(target, "x", encoding="utf-8") as f:
            f.write(new)
        path.unlink()
        log(f"named {path.name} → {target.name}")
    else:
        path.write_text(new)


async def worker() -> None:
    while True:
        kind, item = await queue.get()
        try:
            if kind == "save":
                await asyncio.to_thread(save_direct, item)
            elif kind == "pending":
                await asyncio.to_thread(finish_pending, item)
            elif kind == "sweep":
                await asyncio.to_thread(enrich.run, True)
        except Exception as e:
            log(f"failed ({kind}): {item if kind != 'save' else item.get('url')}: {str(e)[:200]}")
            if kind == "save":  # nothing was written: leave a pending note so it's tried again
                try:
                    await asyncio.to_thread(write_pending, item)
                except Exception as e2:
                    log(f"couldn't even leave a pending note: {e2}")
        finally:
            queued.discard(fill.url_key(item["url"]) if kind == "save" else str(item))
            queue.task_done()


def write_pending(job: dict) -> None:
    """A bare note the service will finish later (after a failed try)."""
    props = {"title": job.get("title") or fill.host(job["url"]), "url": fill.clean_url(job["url"]),
             "source": job.get("source", ""), "via": job.get("via", "link ingest"),
             "date-added": job.get("date_added") or now_iso(), "status": "pending"}
    if job.get("channel"):
        props["yt-channel"] = job["channel"]
    if job.get("category"):
        props["yt-category"] = job["category"]
    body = "\n## Notes\n\n" + (job["thought"].strip() + "\n" if job.get("thought", "").strip() else "")
    path = fill.free_name(FOLDER, props["title"])
    with open(path, "x", encoding="utf-8") as f:
        f.write(L.render(props, body))


def put(kind: str, item) -> None:
    key = fill.url_key(item["url"]) if kind == "save" else str(item)
    if key in queued:
        return
    queued.add(key)
    queue.put_nowait((kind, item))


# ---------- noticing pending notes ----------

def pending_notes() -> list[Path]:
    out = []
    for p in FOLDER.glob("*.md"):
        p = p.resolve()
        try:
            with open(p, encoding="utf-8") as f:
                head = f.read(4000)
        except OSError:
            continue
        # status: pending, also written with quotes ("status": "pending"), as the Web Clipper does
        if re.search(r'(?m)^["\']?status["\']?\s*:\s*["\']?pending["\']?\s*$', head):
            out.append(p)
    return out


async def watch() -> None:
    """Notes changing in content/ (synced in from the phone or Mac). A pending note is queued once
    it has been still for SETTLE_SECONDS."""
    from watchfiles import awatch
    async for changes in awatch(FOLDER, recursive=False, step=1000):
        for _, p in changes:
            p = Path(p).resolve()
            if p.suffix == ".md":
                changed[p] = time.time()


async def settle() -> None:
    for p in pending_notes():  # on start: anything already waiting
        changed.setdefault(p, p.stat().st_mtime)
    while True:
        await asyncio.sleep(10)
        for p, t in list(changed.items()):
            if time.time() - t < SETTLE_SECONDS:
                continue
            del changed[p]
            if p in pending_notes():
                put("pending", p)


async def sweep() -> None:
    while True:
        put("sweep", "sweep")
        await asyncio.sleep(SWEEP_MINUTES * 60)


@app.on_event("startup")
async def start() -> None:
    FOLDER.mkdir(exist_ok=True)
    for task in (worker(), watch(), settle(), sweep()):
        asyncio.create_task(task)
    log(f"library service: {FOLDER}, sweep every {SWEEP_MINUTES:g} min")


# ---------- the endpoint ----------

@app.post("/save")
async def save(body: Save, authorization: str = Header("")) -> dict:
    if not TOKEN or authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "wrong or missing token")
    url = fill.clean_url(body.url)
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "that isn't a link")
    existing = await asyncio.to_thread(lambda: fill.Index(FOLDER).by_url(url))
    if existing:
        return {"status": "exists", "note": existing.stem}
    job = body.model_dump()
    job["url"] = url
    job["date_added"] = body.date_added or now_iso()
    put("save", job)
    return {"status": "queued"}


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "waiting": queue.qsize()}
