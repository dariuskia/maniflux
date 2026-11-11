# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pyyaml"]
# ///
"""DONE: ran once on 2026-11-10 and turned the 15 book and film clips in content/ into library notes in a works/ folder. The folder was renamed back to content/ the same day.
Kept as a record, alongside its backup and plan in ~/feed-backups. It refuses to run again,
because the vault no longer looks the way it expects.

What it did:
Moves book and film clips from the vault's content/ folder into works/, in the works format.

    uv run works/migrate_content.py            # dry run: works out the plan and prints it, changes nothing
    uv run works/migrate_content.py --apply    # carries out the saved plan

The dry run asks the LLM to tidy each clip and looks it up in Open Library / Wikidata, then saves the
plan to ~/feed-backups/works-migration-plan.json. --apply uses that saved plan, so it does exactly
what the dry run showed. Before changing anything it backs up every note in the vault to
~/feed-backups. It then writes the works/ notes, points every link at the new notes (keeping their
display text), deletes the old clips, creates missing creator notes in people/, and checks that no
link to an old clip is left.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import library as works  # noqa: E402

TYPES = {"book", "movie", "film"}
PLAN = Path.home() / "feed-backups" / "works-migration-plan.json"


def candidates() -> list[Path]:
    out = []
    for p in sorted((works.VAULT / "content").glob("*.md")):
        if works.frontmatter(p.read_text()).get("type", "").strip() in TYPES:
            out.append(p)
    return out


def plan_one(path: Path) -> dict:
    fm = works.frontmatter(path.read_text())
    tidy = works.clean(fm)
    kind = {"movie": "film"}.get(tidy.get("type"), tidy.get("type")) or "other"
    # The authors saved on the clip are kept; the LLM only names them when the clip has none
    creators = [works.link_target(a) for a in fm.get("author") or [] if a.strip()] or tidy["creators"]
    facts = {}
    try:
        facts = works.lookup(kind, tidy["title"], tidy["short_title"], creators)
    except Exception as e:  # a lookup failing leaves the facts empty; the plan says so
        facts = {"_error": str(e)[:200]}
    year = facts.get("year") or (fm.get("published", "") if re.fullmatch(r"\d{4}", fm.get("published", "")) else "")
    archived = fm.get("archive", "").strip().lower() == "true"
    date_added = fm.get("date-added", "")
    extra_tags = [t for t in fm.get("tags") or [] if t and t not in ("clippings", "book", "movie", "film", kind)]
    props = works.work_props(
        kind=kind, title=tidy["title"], creators=creators, year=year, date_added=date_added,
        date_archived=date_added[:10] if archived else "", via=fm.get("via") or "clip",
        url=fm.get("url", ""), source=fm.get("source", ""), facts=facts, tags=[kind] + extra_tags)
    return {"old": path.stem, "new": works.file_name(tidy["short_title"], creators), "props": props,
            "doubt": tidy.get("doubt", ""), "lookup": "found" if facts and "_error" not in facts else
            (facts.get("_error") and "error: " + facts["_error"]) or "no confident match",
            "creators": creators, "kind": kind}


def make_plan() -> list[dict]:
    plan = []
    for p in candidates():
        print(f"  {p.stem[:70]}…", file=sys.stderr)
        plan.append(plan_one(p))
        time.sleep(0.5)  # be gentle with the open databases
    names = [x["new"] for x in plan]
    for x in plan:
        if names.count(x["new"]) > 1 or (works.VAULT / "works" / f"{x['new']}.md").exists():
            x["new"] += f" {x['props']['year']}".rstrip()
    return plan


def show(plan: list[dict]) -> None:
    for x in plan:
        p = x["props"]
        ids = ", ".join(f"{k} {p[k]}" for k in works.ID_KEYS if p.get(k)) or "none"
        print(f"\n{x['old']}\n  → works/{x['new']}.md")
        print(f"    {p['type']} · {p['title']} · {', '.join(x['creators'])} · {p['year'] or 'no year'}")
        print(f"    archived: {p['date-archived'] or 'no'} · lookup: {x['lookup']} · ids: {ids}"
              f"{' · cover' if p.get('cover') else ''}")
        if x["doubt"]:
            print(f"    doubt: {x['doubt']}")


def link_pattern(old: str) -> re.Pattern:
    """[[old]], [[content/old]], [[old.md]], [[old#heading]], [[old|text]], and the same as embeds."""
    return re.compile(r"(!?)\[\[(?:content/)?" + re.escape(old) + r"(?:\.md)?(#[^\]|]*)?(\|[^\]]*)?\]\]")


def all_notes() -> list[Path]:
    return [p for p in works.VAULT.rglob("*.md") if ".obsidian" not in p.parts and ".trash" not in p.parts]


def apply(plan: list[dict]) -> None:
    vault = works.VAULT
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path.home() / "feed-backups" / f"vault-notes-before-works-{stamp}.tar.gz"
    with tarfile.open(backup, "w:gz") as tar:
        for p in all_notes():
            tar.add(p, arcname=str(p.relative_to(vault)))
    print(f"Backed up every note to {backup}")

    (vault / "works").mkdir(exist_ok=True)
    for x in plan:
        src, dst = vault / "content" / f"{x['old']}.md", vault / "works" / f"{x['new']}.md"
        if not src.exists():
            sys.exit(f"{src} is gone; run the dry run again.")
        if dst.exists():
            sys.exit(f"{dst} already exists; not overwriting.")
        _, body = works.split_note(src.read_text())
        body = body if "## Notes" in body else body.rstrip("\n") + ("\n\n" if body.strip() else "\n") + "## Notes\n"
        dst.write_text(works.render(x["props"], body))

    changed = 0
    patterns = [(link_pattern(x["old"]), x["new"]) for x in plan]
    for p in all_notes():
        if p.parent.name == "content" and p.stem in {x["old"] for x in plan}:
            continue
        text = p.read_text()
        new = text
        for pat, name in patterns:
            new = pat.sub(lambda m, n=name: f"{m.group(1)}[[{n}{m.group(2) or ''}{m.group(3) or ''}]]", new)
        if new != text:
            p.write_text(new)
            changed += 1
    print(f"Pointed links at the new notes in {changed} notes")

    for x in plan:
        (vault / "content" / f"{x['old']}.md").unlink()

    existing = {p.stem for p in all_notes()}
    made = []
    for x in plan:
        for name in x["creators"]:
            if name not in existing:
                role = works.ROLE.get(x["kind"])
                props = {"type": "person", "roles": [role] if role else []}
                (vault / works.PEOPLE_DIR / f"{name}.md").write_text(works.render(props, ""))
                existing.add(name)
                made.append(name)
    print(f"Made {len(made)} creator notes in people/: {', '.join(made) or 'none'}")

    left = [(p.relative_to(vault), x["old"]) for p in all_notes() for x in plan
            if link_pattern(x["old"]).search(p.read_text())]
    missing = [x["new"] for x in plan if not (vault / "works" / f"{x['new']}.md").exists()]
    if left or missing:
        print("PROBLEM:", left, missing)
        sys.exit(1)
    print(f"Checked: {len(plan)} notes in works/, and no links to the old clips are left.")


def main() -> None:
    sys.exit("Already done on 2026-11-10; see the note at the top of this file.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="carry out the saved plan")
    args = ap.parse_args()
    if args.apply:
        if not PLAN.exists():
            sys.exit("No saved plan; run without --apply first.")
        plan = json.loads(PLAN.read_text())
        show(plan)
        print()
        apply(plan)
    else:
        print("Working out the plan (LLM + Open Library / Wikidata)…", file=sys.stderr)
        plan = make_plan()
        PLAN.parent.mkdir(exist_ok=True)
        PLAN.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
        show(plan)
        print(f"\nSaved to {PLAN}. Nothing changed. Run with --apply to carry it out.")


if __name__ == "__main__":
    main()
