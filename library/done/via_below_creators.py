# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pyyaml"]
# ///
"""DONE: ran once on 2026-11-10 and moved via below creators in 269 notes. Kept as a record; it
refuses to run again.

What it did:
Moves via to directly below creators in every note in content/ that has both. Only the order changes.

    uv run library/done/via_below_creators.py            # dry run
    uv run library/done/via_below_creators.py --apply    # backs up content/, moves, checks
"""

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
    new = L.place_after(text, "via", "creators")
    if new != text:
        assert L.frontmatter(new) == L.frontmatter(text), p.name  # same properties, only the order changes
        changes[p] = new
no_creators = sum(1 for p in notes if "via" in L.frontmatter(p.read_text()) and "creators" not in L.frontmatter(p.read_text()))
print(f"{len(changes)} notes to reorder; {no_creators} have via but no creators property (left as they are)")
if "--apply" not in sys.argv:
    sys.exit("Nothing changed. Run with --apply.")
backup = Path.home() / "feed-backups" / f"vault-notes-before-via-order-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
with tarfile.open(backup, "w:gz") as tar:
    for p in notes:
        tar.add(p, arcname=str(p.relative_to(L.VAULT)))
for p, new in changes.items():
    p.write_text(new)
left = sum(1 for p in notes if L.place_after(p.read_text(), "via", "creators") != p.read_text())
print(f"Backed up to {backup}; reordered {len(changes)} notes. Still out of order: {left}")
