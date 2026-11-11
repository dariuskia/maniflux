# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pyyaml"]
# ///
"""Fills in new books, films, shows and music in the library: a tidy title, `creators` (instead of
`author`), year, cover and database IDs. Notes come from the Web Clipper, Link Ingest and Maniflux,
which only know what the page said.

    uv run library/enrich.py              # dry run: shows what it would fill in, changes nothing
    uv run library/enrich.py --apply      # fills them in
    uv run library/enrich.py --watch      # fills them in now and every few minutes (the server runs this)

A note is picked up when its type is book, film, show or music and it has no database ID or no
cover. First the databases are asked for its facts (if it has no ID yet), then for a cover (by its
IDs), and only then does the cover come from the note's link (the page's own image, or a YouTube
thumbnail). Each step is tried once per note (remembered by its link in a small state file), so a
note the databases don't know isn't asked about again. Only the note's own properties change: never its file name, body,
or anything else in the vault. Facts (year, IDs) only come from the databases; the LLM only tidies
the title and names the creators when the note has none. The cover comes from the databases, or
else from the page's own preview image.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library as L  # noqa: E402

TYPES = {"book", "film", "show", "music"}
IDS = ["openlibrary", "isbn", "imdb", "tmdb", "musicbrainz", "wikidata", "spotify"]
STATE = Path(os.environ.get("LIBRARY_STATE", Path.home() / ".cache" / "library-enrich.json"))
EVERY_MINUTES = float(os.environ.get("LIBRARY_EVERY_MINUTES", "5"))


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE)


def tried(state: dict, key: str) -> dict:
    """Which steps were already tried for a note. (Early state files kept one time per note, for the
    lookup only.)"""
    v = state.get(key, {})
    return {"lookup": v} if isinstance(v, str) else v


def candidates(state: dict) -> list[Path]:
    out = []
    for p in sorted((L.VAULT / L.NOTES_DIR).glob("*.md")):
        try:
            fm = L.frontmatter(p.read_text())
        except Exception:  # a note being written right now, or broken frontmatter: skip it this round
            continue
        if fm.get("type", "").strip() not in TYPES:
            continue
        done = tried(state, fm.get("url") or p.name)
        if (not any(fm.get(k) for k in IDS) and "lookup" not in done) or (not fm.get("cover") and "cover" not in done):
            out.append(p)
    return out


def plan(path: Path, done: dict) -> tuple[dict, list[str], list[str]]:
    """The property changes for one note, what was found (for the log), and which steps ran."""
    fm = L.frontmatter(path.read_text())
    kind = fm["type"].strip()
    authors = [L.link_target(a) for a in (fm.get("creators") or fm.get("author") or []) if a and a.strip()]
    updates: dict = {}
    found: list[str] = []
    steps: list[str] = []
    ids = {k: fm[k] for k in IDS if fm.get(k)}

    # 1. Facts from the databases, if the note has no ID yet
    if not ids and "lookup" not in done:
        tidy = L.clean(fm)
        creators = authors or tidy["creators"]
        # A failed lookup (a database down or busy) raises, so the note is tried again next round
        facts = L.lookup(kind, tidy["title"], tidy["short_title"], creators)
        steps.append("lookup")
        found.append("found" if facts else "no confident match")
        if facts:
            # The LLM's tidy title and creators are only used once a database confirms the match
            if tidy["title"] != fm.get("title"):
                updates["title"] = tidy["title"]
            authors = creators
            for k in ("year", "form"):
                if facts.get(k) and not fm.get(k):
                    updates[k] = facts[k]
            for k in IDS:
                if facts.get(k):
                    updates[k] = ids[k] = facts[k]
            if facts.get("cover") and not fm.get("cover"):
                updates["cover"] = facts["cover"]
    if authors and not fm.get("creators"):
        updates["creators"] = [L.person_link(c) for c in authors]  # author becomes creators

    # 2. A cover: the databases first (by the note's IDs), then the note's link
    if not fm.get("cover") and "cover" not in updates and "cover" not in done:
        cover = L.database_cover(ids)
        source = "database"
        if not cover and fm.get("url"):
            cover, source = L.page_image(fm["url"]), "link"
        steps.append("cover")
        if cover:
            updates["cover"] = cover
            found.append(f"cover from the {source}")
        else:
            found.append("no cover")
    elif "cover" in updates:
        found.append("cover from the database")

    found += [f"{k} {updates[k]}" for k in ("year", "form", *IDS) if k in updates]
    return updates, found, steps


def run(apply: bool) -> int:
    state = load_state()
    todo = candidates(state)
    for path in todo:
        key = L.frontmatter(path.read_text()).get("url") or path.name
        try:
            updates, found, steps = plan(path, tried(state, key))
        except Exception as e:  # e.g. the LLM or a database is unreachable: try again next round
            log(f"skipped for now: {path.name}: {str(e)[:120]}")
            continue
        log(f"{'filled in' if apply else 'would fill in'} {path.name}: {', '.join(found) or 'nothing to add'}"
            + (f" · title → {updates['title']}" if "title" in updates else ""))
        if apply:
            current = path.read_text()
            if updates:
                new = L.set_props(current, updates, remove=("author",), in_place_of={"creators": "author"})
                L.frontmatter(new)  # must still parse
                if path.read_text() != current:  # changed while we were looking it up: next round
                    continue
                path.write_text(new)
            done = tried(state, key)
            now = datetime.now().isoformat(timespec="seconds")
            state[key] = {**done, **{s: now for s in steps}}
            save_state(state)
        time.sleep(1)  # MusicBrainz asks for at most one request a second
    return len(todo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--watch", action="store_true")
    args = ap.parse_args()
    if args.watch:
        log(f"watching {L.VAULT / L.NOTES_DIR} every {EVERY_MINUTES:g} minutes")
        while True:
            try:
                run(apply=True)
            except Exception as e:  # keep watching
                log(f"round failed: {e}")
            time.sleep(EVERY_MINUTES * 60)
    n = run(apply=args.apply)
    if not n:
        log("nothing to fill in")


if __name__ == "__main__":
    main()
