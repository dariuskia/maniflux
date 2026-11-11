# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pyyaml"]
# ///
"""DONE: ran once on 2026-11-10 and renamed author to creators in 473 notes. Kept as a record,
alongside its backup in ~/feed-backups; it refuses to run again.

What it did:
Renames the `author` property to `creators` in every note in content/, so books, films, music
and writing all name their makers the same way. Only the property's name changes; its links and
everything else stay as they are.

    uv run library/rename_author.py            # dry run: counts what would change
    uv run library/rename_author.py --apply    # backs up every note, renames, checks
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import library as L  # noqa: E402


def renamed(text: str) -> str | None:
    """The note with author renamed, or None if it has no author (or already has creators)."""
    fm_text, body = L.split_note(text)
    if not re.search(r"(?m)^author:", fm_text) or re.search(r"(?m)^creators:", fm_text):
        return None
    return "---\n" + re.sub(r"(?m)^author:", "creators:", fm_text, count=1) + "\n---\n" + body


def main() -> None:
    sys.exit("Already done on 2026-11-10; see the note at the top of this file.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    notes = sorted((L.VAULT / L.NOTES_DIR).glob("*.md"))
    changes = {p: n for p in notes if (n := renamed(p.read_text())) is not None}
    both = [p.name for p in notes if re.search(r"(?m)^author:", L.split_note(p.read_text())[0])
            and re.search(r"(?m)^creators:", L.split_note(p.read_text())[0])]
    print(f"{len(notes)} notes; {len(changes)} have author to rename; {len(both)} have both (left alone): {both[:5]}")
    for p, new in list(changes.items())[:2]:
        before, after = L.frontmatter(p.read_text()), L.frontmatter(new)
        print(f"  e.g. {p.name}: author {before.get('author')} → creators {after.get('creators')}")
    if not args.apply:
        print("Nothing changed. Run with --apply to rename.")
        return
    backup = Path.home() / "feed-backups" / f"vault-notes-before-creators-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
    with tarfile.open(backup, "w:gz") as tar:
        for p in notes:
            tar.add(p, arcname=str(p.relative_to(L.VAULT)))
    print(f"Backed up content/ to {backup}")
    for p, new in changes.items():
        before = L.frontmatter(p.read_text())
        after = L.frontmatter(new)
        assert after.get("creators") == before.get("author") and "author" not in after, p.name
        p.write_text(new)
    left = [p.name for p in notes if re.search(r"(?m)^author:", L.split_note(p.read_text())[0])]
    print(f"Renamed author → creators in {len(changes)} notes. Notes still with author: {len(left)} {left[:5]}")


if __name__ == "__main__":
    main()
