"""Runs Maniflux with made-up feeds and articles, for screenshots and trying it out.

    .venv/bin/python demo.py        then open http://127.0.0.1:8099

Nothing touches a real Miniflux, vault or OpenRouter: everything lives in a temporary folder.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import uvicorn

os.environ["MANIFLUX_NO_POLL"] = "1"

from app import config, main  # noqa: E402
from app.ai import Summarizer  # noqa: E402
from app.db import DB  # noqa: E402
from app.miniflux import Miniflux  # noqa: E402
from app.vault import VaultIndex  # noqa: E402

NOW = datetime.now(timezone.utc)

FEEDS = [
    (1, "City Desk", "https://citydesk.example/feed"),
    (2, "Field Notes", "https://fieldnotes.example/feed"),
    (3, "Signal & Noise", "https://signalnoise.example/feed"),
    (4, "Small Machines", "https://www.youtube.com/feeds/videos.xml?channel_id=demo"),
    (5, "The Long Run", "https://longrun.example/feed"),
    (6, "Margins", "https://margins.example/feed"),
    (7, "Neighbourhood Weekly", "https://weekly.example/feed"),
]

# feed id, title, hours old, minutes, first lines, extra
ITEMS = [
    (2, "Why every city should have a tool library", 3, 9,
     "Borrowing a drill for an afternoon beats owning one forever. A look at the libraries lending out ladders, sewing machines and pressure washers, and what it takes to run one.", {}),
    (1, "Council approves the new waterfront bike lanes", 5, 3,
     "After two years of hearings, the protected lanes along the waterfront will be built next spring, connecting the ferry terminal to downtown.", {}),
    (4, "Building a mechanical calculator from scratch", 8, 24,
     "Gears, levers and a lot of patience: a full build of a hand-cranked adding machine, from the first sketches to the last stubborn carry.", {"url": "https://www.youtube.com/watch?v=demo1"}),
    (3, "Episode 112: How recommendation systems learned to listen", 11, 58,
     "A conversation about feedback loops, what a skip really means, and why the best systems ask before they guess.", {"audio": True}),
    (5, "The quiet comeback of the paper map", 20, 14,
     "Phones know where we are. Paper maps show where we could go. Why hikers, cyclists and planners are printing again.", {}),
    (6, "Notes on reading slowly", 26, 6,
     "Some thoughts on finishing fewer things, underlining more, and letting a good essay take a whole evening.", {}),
    (7, "Saturday market moves to the old rail yard", 30, 2,
     "Starting next month the farmers' market will set up in the restored rail yard, with twice the stalls and a covered area for winter.", {}),
    (2, "What a year of composting taught me", 40, 11,
     "Three bins, one curious dog and roughly four hundred kilos of kitchen scraps later, here is what worked and what definitely did not.", {}),
    (1, "Library hours extended through the winter", 46, 2,
     "All branches will stay open until 9pm on weekdays from November, after a pilot last year doubled evening visits.", {}),
    (5, "Running the same route for a thousand days", 52, 18,
     "On repetition, seasons, and noticing the one tree that changes first every autumn.", {}),
    (4, "Restoring a 1970s synthesizer", 60, 31,
     "Cracked keys, scratchy pots and a power supply that had seen better decades: bringing an old synth back to life.", {"url": "https://www.youtube.com/watch?v=demo2"}),
    (6, "A short history of the index card", 70, 8,
     "Before databases there were drawers. How a humble card organised libraries, businesses and at least one famous novelist's entire life.", {}),
]

ARCHIVED = [
    (2, "Seed swaps and the economics of sharing", 80, 12, "Seed libraries run on trust, surplus and a little bit of chaos.", True),
    (5, "Why trail running feels easier than road running", 90, 7, "Softer ground, more variety and fewer reasons to check your watch.", False),
    (1, "New crosswalks planned near three schools", 100, 3, "The city will add raised crossings and longer walk signals this summer.", False),
]


def feed(fid):
    _, title, url = next(f for f in FEEDS if f[0] == fid)
    return {
        "id": fid, "user_id": 1, "feed_url": url, "site_url": url.rsplit("/", 1)[0], "title": title,
        "category": {"id": 1, "title": "All", "user_id": 1, "hide_globally": False},
    }


def entry(eid, fid, title, hours, minutes, text, extra=None, status="unread", archived_hours=None):
    extra = extra or {}
    stamp = (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    changed = (NOW - timedelta(hours=archived_hours)).strftime("%Y-%m-%dT%H:%M:%SZ") if archived_hours else stamp
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    body = f"<p>{text}</p>" + "".join(
        f"<p>{p}</p>" for p in [
            "This is a made-up article for the Maniflux demo. The words are here so the reading view has something to show.",
            "In a real setup, this text comes straight from Miniflux, and “Load full article” fetches the whole page when a feed only sends a summary.",
        ])
    return {
        "id": eid, "user_id": 1, "feed_id": fid, "status": status, "hash": f"h{eid}", "title": title,
        "url": extra.get("url", f"{feed(fid)['site_url']}/{slug}"), "comments_url": "", "language": "en",
        "published_at": stamp, "created_at": stamp, "changed_at": changed, "content": body,
        "author": "", "share_code": "", "starred": False, "reading_time": minutes,
        "enclosures": [{"id": eid, "url": "https://example.com/a.mp3", "mime_type": "audio/mpeg", "size": 1}]
        if extra.get("audio") else [],
        "feed": feed(fid), "tags": [],
    }


ENTRIES = {}
for i, (fid, title, hours, minutes, text, extra) in enumerate(ITEMS, start=1):
    ENTRIES[i] = entry(i, fid, title, hours, minutes, text, extra)
for i, (fid, title, hours, minutes, text, saved) in enumerate(ARCHIVED, start=100):
    ENTRIES[i] = entry(i, fid, title, hours, minutes, text, status="read", archived_hours=(i - 99) * 5)


def miniflux(request: httpx.Request) -> httpx.Response:
    path, p = request.url.path, request.url.params
    if request.method == "GET" and path == "/v1/entries":
        found = [e for e in ENTRIES.values() if e["status"] == p.get("status", e["status"])]
        q = p.get("search", "").lower()
        if q:
            found = [e for e in found if all(w in (e["title"] + e["content"]).lower() for w in q.split())]
        order = p.get("order", "published_at")
        found.sort(key=lambda e: e[order], reverse=p.get("direction", "desc") == "desc")
        offset, limit = int(p.get("offset", 0)), int(p.get("limit", 100))
        return httpx.Response(200, json={"total": len(found), "entries": found[offset:offset + limit]})
    if request.method == "PUT" and path == "/v1/entries":
        body = json.loads(request.content)
        for i in body["entry_ids"]:
            ENTRIES[i]["status"] = body["status"]
        return httpx.Response(204)
    if m := re.fullmatch(r"/v1/entries/(\d+)/fetch-content", path):
        e = ENTRIES[int(m.group(1))]
        return httpx.Response(200, json={"content": e["content"], "reading_time": e["reading_time"]})
    if m := re.fullmatch(r"/v1/entries/(\d+)", path):
        return httpx.Response(200, json=ENTRIES[int(m.group(1))])
    if path == "/v1/feeds":
        return httpx.Response(200, json=[feed(f[0]) for f in FEEDS])
    if path == "/v1/version":
        return httpx.Response(200, json={"version": "2.3.4 (demo)"})
    return httpx.Response(404, json={"error_message": "not in the demo"})


def openrouter(request: httpx.Request) -> httpx.Response:
    answer = {"type": "article", "description": "A made-up summary written by the demo instead of a model."}
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})


def setup(tmp: Path):
    config.CONTENT_DIR = tmp / "content"
    config.CONTENT_DIR.mkdir()
    db = DB(tmp / "demo.db")
    groups = {name: db.add_group(name) for name in ["Local", "Making things", "Ideas", "Listening"]}
    for fid, names in {1: ["Local"], 7: ["Local"], 2: ["Making things", "Local"], 4: ["Making things"],
                       5: ["Ideas"], 6: ["Ideas"], 3: ["Listening", "Ideas"]}.items():
        for n in names:
            db.toggle_member(fid, groups[n])
    db.set_group_weight(groups["Making things"], 1.5)
    db.set_weight("feed", 7, 0.75)
    db.add_rule("keyword", "bike lanes", "boost", None, None)
    db.add_rule("limit", "type:video", None, 3, None)
    db.add_rule("keyword", "sponsored", "hide", None, (NOW + timedelta(days=30)).date().isoformat())
    for i, (*_, saved) in enumerate(ARCHIVED, start=100):
        if saved:
            db.add_saved(i, ENTRIES[i]["url"], "demo.md")
    main.app.state.db = db
    main.app.state.vault = VaultIndex(config.CONTENT_DIR)
    main.app.state.mf = Miniflux("http://miniflux.demo/v1", "demo", transport=httpx.MockTransport(miniflux))
    main.app.state.ai = Summarizer("demo", "demo", transport=httpx.MockTransport(openrouter))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        setup(Path(tmp))
        print("Maniflux demo with made-up data: http://127.0.0.1:8099")
        uvicorn.run(main.app, host="127.0.0.1", port=8099, log_level="warning")
