# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pyyaml"]
# ///
"""DONE: ran once on 2026-11-10 and removed tags from all 498 notes. Kept as a record; it refuses to
run again.

What it did:
Removes the `tags` property from every note in content/ (the tools' "clippings" and type tags, and the
few added by hand), so tags start empty and are only ever your own.

    uv run library/done/remove_tags.py            # dry run
    uv run library/done/remove_tags.py --apply    # backs up content/, removes, checks
"""

import sys
import tarfile
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import library as L  # noqa: E402

sys.exit("Already done on 2026-11-10; see the note at the top of this file.")
notes = sorted((L.VAULT / L.NOTES_DIR).glob("*.md"))
changes, seen = {}, Counter()
for p in notes:
    text = p.read_text()
    fm = L.frontmatter(text)
    if "tags" not in fm:
        continue
    tags = fm["tags"] if isinstance(fm["tags"], list) else [fm["tags"]]
    seen.update(t for t in tags if t)
    new = L.set_props(text, {}, remove=("tags",))
    after = L.frontmatter(new)
    assert "tags" not in after and {k: v for k, v in fm.items() if k != "tags"} == after, p.name
    changes[p] = new
print(f"{len(changes)} notes have tags; removing: " + ", ".join(f"{t} ({n})" for t, n in seen.most_common()))
if "--apply" not in sys.argv:
    sys.exit("Nothing changed. Run with --apply.")
backup = Path.home() / "feed-backups" / f"vault-notes-before-tags-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
with tarfile.open(backup, "w:gz") as tar:
    for p in notes:
        tar.add(p, arcname=str(p.relative_to(L.VAULT)))
for p, new in changes.items():
    p.write_text(new)
left = [p.name for p in notes if "tags" in L.frontmatter(p.read_text())]
print(f"Backed up to {backup}; removed tags from {len(changes)} notes. Notes still with tags: {len(left)}")
