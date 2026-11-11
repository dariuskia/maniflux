# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pyyaml"]
# ///
"""DONE: ran once on 2026-11-10 and moved 60 person notes from the top of the vault into people/,
deleting the empty top-level copies of Audrey Tang and Ezra Klein. Kept as a record, alongside its
backup in ~/feed-backups; it refuses to run again.

A lesson from running it: --apply asked the LLM again instead of reusing the dry run's answers, and
the second answer also called "stefan heimersheim mars ideas" a person, so that note was moved too
(and moved straight back). A script like this should save the dry run's plan and apply exactly that,
as library/done/merge_content.py does. Afterwards "Ariel D. Procaccia" (empty, 2 links) was merged
into "Ariel Procaccia" by hand.

What it did:
Moves notes about people from the top of the vault into people/, so each person has one note
there and every link (`[[Name]]` or `[[people/Name|Name]]`) points to it.

    uv run library/people_to_folder.py            # dry run: lists what it would move, changes nothing
    uv run library/people_to_folder.py --apply    # backs up, moves, checks

The LLM decides which top-level notes are about a person, from each note's name and first lines.
A note already in people/ under the same name: if the top-level one is empty it's deleted; if both
have text, nothing happens to that pair and it's listed to merge by hand. Moved notes keep their
name and contents. Links aren't rewritten: with one note per name, Obsidian resolves [[Name]] to
people/Name.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tarfile
from datetime import datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import library as L  # noqa: E402

PEOPLE = L.VAULT / L.PEOPLE_DIR
PROMPT = """For each note in a personal Obsidian vault, decide whether the note is about one specific
real person (a note named after them, e.g. "Tyler Cowen" or "Hélène Landemore"). Notes about
organisations, ideas, projects, applications, essays, recipes, places, or anything else are not.
Reply with JSON only: {"items": [{"i": <i>, "person": true or false}, ...]}, one per note."""


def classify(items: list[dict]) -> dict[int, bool]:
    out: dict[int, bool] = {}
    for start in range(0, len(items), 40):
        batch = items[start:start + 40]
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {L.env('OPENROUTER_API_KEY')}"},
            json={"model": L.env("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5"),
                  "messages": [{"role": "system", "content": PROMPT},
                               {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
                  "response_format": {"type": "json_object"}, "temperature": 0},
            timeout=120)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        for it in json.loads(text[text.index("{"): text.rindex("}") + 1])["items"]:
            out[int(it["i"])] = bool(it["person"])
    if len(out) != len(items):
        sys.exit("The LLM didn't answer for every note; nothing changed.")
    return out


def all_notes() -> list[Path]:
    return [p for p in L.VAULT.rglob("*.md") if ".obsidian" not in p.parts and ".trash" not in p.parts]


def main() -> None:
    sys.exit("Already done on 2026-11-10; see the note at the top of this file.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    top = sorted(L.VAULT.glob("*.md"))
    items = [{"i": i, "name": p.stem, "start": L.split_note(p.read_text())[1].strip()[:200]} for i, p in enumerate(top)]
    is_person = classify(items)
    move, delete, by_hand = [], [], []
    for i, p in enumerate(top):
        if not is_person[i]:
            continue
        twin = PEOPLE / p.name
        if not twin.exists():
            move.append(p)
        elif not p.read_text().strip():
            delete.append(p)
        elif not twin.read_text().strip():
            delete.append(twin)  # the people/ copy is the empty one: replaced by the top-level note
            move.append(p)
        else:
            by_hand.append(p)
    print(f"{len(top)} top-level notes; {sum(is_person.values())} about a person.")
    print(f"\nMove into people/ ({len(move)}):\n  " + "\n  ".join(p.stem for p in move))
    print(f"\nDelete, empty duplicate of a people/ note ({len(delete)}):\n  " + "\n  ".join(str(p.relative_to(L.VAULT)) for p in delete))
    if by_hand:
        print(f"\nBoth copies have text; merge by hand ({len(by_hand)}):\n  " + "\n  ".join(p.stem for p in by_hand))
    if not args.apply:
        print("\nNothing changed. Run with --apply to do it.")
        return

    backup = Path.home() / "feed-backups" / f"vault-notes-before-people-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
    with tarfile.open(backup, "w:gz") as tar:
        for p in all_notes():
            tar.add(p, arcname=str(p.relative_to(L.VAULT)))
    print(f"\nBacked up every note to {backup}")
    for p in delete:
        p.unlink()
    for p in move:
        shutil.move(str(p), PEOPLE / p.name)
    # Check: every link to these names now has exactly one note to go to, in people/
    names = {p.stem for p in move + delete}
    stems: dict[str, list[Path]] = {}
    for p in all_notes():
        stems.setdefault(p.stem, []).append(p)
    problems = [n for n in names if [q.parent.name for q in stems.get(n, [])] != [L.PEOPLE_DIR]]
    linked = sum(1 for p in all_notes() for n in names
                 if re.search(r"\[\[(?:people/)?" + re.escape(n) + r"(\||#|\]\])", p.read_text()))
    print(f"Moved {len(move)}, deleted {len(delete)}. Each now has one note, in people/: "
          f"{'yes' if not problems else 'NO: ' + ', '.join(problems)}. Notes linking to them: {linked}.")


if __name__ == "__main__":
    main()
