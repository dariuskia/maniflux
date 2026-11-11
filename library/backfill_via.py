# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pyyaml"]
# ///
"""Gives every note in content/ the new `via` (the organisation or outlet that put it out, linked into
orgs/) and moves organisations that were filed as people into orgs/.

    uv run library/backfill_via.py            # dry run: asks the LLM, saves the plan, prints it
    uv run library/backfill_via.py --apply    # carries out exactly the saved plan

For each note the LLM gets its title, site, creators, description and source (often the feed, which
is usually the publication), and answers with its via and which of its creators are organisations.
--apply backs up every note, then: sets via (replacing the old tool names: clip, reader, link
ingest), links organisation creators to orgs/ instead of people/, moves those organisations' notes
from people/ to orgs/ and points every link at the new place, and makes a small note in orgs/ for
each organisation that has none. Then it checks no link to a moved note is left behind.
"""

from __future__ import annotations

import json
import re
import sys
import tarfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library as L  # noqa: E402

PLAN = Path.home() / "feed-backups" / "via-backfill-plan.json"
TOOL_NAMES = {"clip", "reader", "link ingest", "web clipper", "maniflux", "linkingest"}
PLATFORMS = {"youtube", "substack", "spotify", "soundcloud", "bandcamp", "apple podcasts", "arxiv", "medium", "x",
             "twitter", "instagram", "vimeo", "mixcloud", "google", "github", "linkedin", "tiktok", "reddit"}
PROMPT = """For each saved item in a personal library, answer two things.
- "via": the organisation or outlet that put it out: the publication, the company (for its own blog), the
  newsletter, podcast, YouTube channel, radio station, record label or publishing house. Never the platform it is
  hosted on (YouTube, Substack, Spotify, SoundCloud, Bandcamp, Apple Podcasts, arXiv, Medium, X, Instagram). For an
  academic paper, the journal or conference if clear, else "". "" if unclear.
- "orgs": which of the item's listed creators are organisations rather than people (exact names from its list).
"source" is how the item reached its owner; when it's a feed or newsletter name, that is often the outlet.
Reply with JSON only: {"items": [{"i": <i>, "via": "...", "orgs": [...]}, ...]}, one per item."""


def notes() -> list[Path]:
    return sorted((L.VAULT / L.NOTES_DIR).glob("*.md"))


def ask(batch: list[dict]) -> dict[int, dict]:
    for attempt in range(3):
        try:
            r = httpx.post("https://openrouter.ai/api/v1/chat/completions",
                           headers={"Authorization": f"Bearer {L.env('OPENROUTER_API_KEY')}"},
                           json={"model": L.env("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5"),
                                 "messages": [{"role": "system", "content": PROMPT},
                                              {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
                                 "response_format": {"type": "json_object"}, "temperature": 0}, timeout=120)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            items = json.loads(text[text.index("{"): text.rindex("}") + 1])["items"]
            out = {int(x["i"]): x for x in items}
            if len(out) == len(batch):
                return out
        except Exception as e:
            print(f"  retrying a batch: {e}", file=sys.stderr)
    sys.exit("The LLM didn't answer a batch after 3 tries; nothing changed.")


def make_plan() -> list[dict]:
    items, paths = [], notes()
    for i, p in enumerate(paths):
        fm = L.frontmatter(p.read_text())
        creators = [L.link_target(c) for c in (fm.get("creators") or []) if c and c.strip()]
        items.append({"i": i, "title": fm.get("title") or p.stem, "site": urlsplit(fm.get("url") or "").hostname or "",
                      "creators": creators, "source": fm.get("source") or "", "type": fm.get("type") or "",
                      "description": (fm.get("description") or "")[:300]})
    answers: dict[int, dict] = {}
    for start in range(0, len(items), 25):
        print(f"  {start + 1}–{min(start + 25, len(items))} of {len(items)}", file=sys.stderr)
        answers.update(ask(items[start:start + 25]))
    plan = []
    for it, p in zip(items, paths):
        a = answers[it["i"]]
        via = re.sub(r"\s+", " ", str(a.get("via") or "")).strip()
        if via.lower() in PLATFORMS:
            via = ""
        orgs = [o for o in (a.get("orgs") or []) if o in it["creators"]]
        plan.append({"note": p.name, "title": it["title"], "via": via, "org_creators": orgs})
    return plan


def show(plan: list[dict]) -> None:
    vias = Counter(x["via"] for x in plan if x["via"])
    print(f"\n{sum(1 for x in plan if x['via'])} of {len(plan)} notes get a via; {len(vias)} different outlets.")
    print("Most common:", ", ".join(f"{v} ({n})" for v, n in vias.most_common(25)))
    orgs = Counter(o for x in plan for o in x["org_creators"])
    print(f"\nCreators that are organisations ({len(orgs)}), to move from people/ to orgs/:")
    print("  " + ", ".join(f"{o} ({n})" for o, n in orgs.most_common()))
    print("\nA sample:")
    for x in plan[:: max(1, len(plan) // 15)][:15]:
        print(f"  {x['title'][:60]:60} via {x['via'] or '—'}")


def all_vault_notes() -> list[Path]:
    return [p for p in L.VAULT.rglob("*.md") if ".obsidian" not in p.parts and ".trash" not in p.parts]


def apply(plan: list[dict]) -> None:
    vault = L.VAULT
    backup = Path.home() / "feed-backups" / f"vault-notes-before-via-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
    with tarfile.open(backup, "w:gz") as tar:
        for p in all_vault_notes():
            tar.add(p, arcname=str(p.relative_to(vault)))
    print(f"Backed up every note to {backup}")
    people, orgs_dir = vault / L.PEOPLE_DIR, vault / L.ORGS_DIR
    orgs_dir.mkdir(exist_ok=True)
    org_names = {o for x in plan for o in x["org_creators"]}

    # 1. Each note: via, and organisation creators linked into orgs/
    for x in plan:
        p = vault / L.NOTES_DIR / x["note"]
        text = p.read_text()
        fm = L.frontmatter(text)
        creators = [L.link_target(c) for c in (fm.get("creators") or []) if c and c.strip()]
        updates = {"via": L.org_link(x["via"]) if x["via"] else ""}
        if any(c in org_names for c in creators):
            updates["creators"] = [L.org_link(c) if c in x["org_creators"] else L.person_link(c) for c in creators]
        old_via = str(fm.get("via") or "").strip()
        new = L.set_props(text, updates, before="published")
        L.frontmatter(new)
        if new != text:
            p.write_text(new)
        if old_via and old_via.lower() not in TOOL_NAMES and not old_via.startswith("[["):
            print(f"  note: {p.name} had via {old_via!r}, now {updates['via'] or 'empty'}")

    # 2. Organisations' notes move from people/ to orgs/, and every link follows
    moved = []
    for name in sorted(org_names):
        src, dst = people / f"{name}.md", orgs_dir / f"{name}.md"
        if src.exists() and not dst.exists():
            src.rename(dst)
            moved.append(name)
    pattern = re.compile(r"\[\[" + re.escape(L.PEOPLE_DIR) + r"/(" + "|".join(re.escape(n) for n in org_names) + r")(\||\]\]|#)") if org_names else None
    changed = 0
    if pattern:
        for p in all_vault_notes():
            t = p.read_text()
            n = pattern.sub(lambda m: f"[[{L.ORGS_DIR}/{m.group(1)}{m.group(2)}", t)
            if n != t:
                p.write_text(n)
                changed += 1

    # 3. A small note for every organisation that has none
    made = 0
    for name in sorted({x["via"] for x in plan if x["via"]} | org_names):
        safe = re.sub(r'[\\/:*?"<>|#^\[\]]', "", name).strip()
        if safe and not (orgs_dir / f"{safe}.md").exists():
            (orgs_dir / f"{safe}.md").write_text(L.render({"type": "org"}, ""))
            made += 1
    left = [str(p.relative_to(vault)) for p in all_vault_notes() if pattern and pattern.search(p.read_text())]
    print(f"Set via on {sum(1 for x in plan if x['via'])} notes; moved {len(moved)} organisations from people/ to "
          f"orgs/ ({', '.join(moved) or 'none'}); updated links in {changed} notes; made {made} notes in orgs/. "
          f"Links still pointing at people/ for an organisation: {len(left)} {left[:3]}")


def main() -> None:
    if "--apply" in sys.argv:
        if not PLAN.exists():
            sys.exit("No saved plan; run the dry run first.")
        apply(json.loads(PLAN.read_text()))
        return
    plan = make_plan()
    PLAN.parent.mkdir(exist_ok=True)
    PLAN.write_text(json.dumps(plan, indent=1, ensure_ascii=False))
    show(plan)
    print(f"\nSaved to {PLAN}. Nothing changed. Run with --apply to carry out exactly this plan.")


if __name__ == "__main__":
    main()
