# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pyyaml"]
# ///
"""DONE: ran once on 2026-11-10 and removed the heading from 24 notes (writing under it kept; 4 notes
with the heading further down were left alone). Kept as a record; it refuses to run again.

What it did:
Removes the "## Notes" heading from the start of notes in content/. Anything written under it stays.
Only a heading that's the first thing after the properties is removed; one further down is left alone.

    uv run library/done/remove_notes_heading.py            # dry run
    uv run library/done/remove_notes_heading.py --apply    # backs up content/, removes, checks
"""

import re
import sys
import tarfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import library as L  # noqa: E402

sys.exit("Already done on 2026-11-10; see the note at the top of this file.")
notes = sorted((L.VAULT / L.NOTES_DIR).glob("*.md"))
changes = {}
for p in notes:
    text = p.read_text()
    m = re.match(r"^(---\n.*?\n---\n)(\s*)## Notes[ \t]*\n(.*)$", text, re.S)
    if not m:
        continue
    rest = m.group(3).lstrip("\n")
    new = m.group(1) + ("\n" + rest if rest.strip() else "")
    assert L.frontmatter(new) == L.frontmatter(text) and rest.strip() in new, p.name
    changes[p] = (new, rest.strip())
with_text = [p.stem for p, (_, r) in changes.items() if r]
print(f"{len(changes)} notes start with ## Notes; {len(with_text)} have writing under it, which stays: {with_text}")
if "--apply" not in sys.argv:
    sys.exit("Nothing changed. Run with --apply.")
backup = Path.home() / "feed-backups" / f"vault-notes-before-notes-heading-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
with tarfile.open(backup, "w:gz") as tar:
    for p in notes:
        tar.add(p, arcname=str(p.relative_to(L.VAULT)))
for p, (new, _) in changes.items():
    p.write_text(new)
left = [p.name for p in notes if re.match(r"^---\n.*?\n---\n\s*## Notes", p.read_text(), re.S)]
print(f"Backed up to {backup}; removed the heading from {len(changes)} notes. Still starting with it: {len(left)}")
