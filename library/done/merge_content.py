# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pyyaml"]
# ///
"""DONE: ran once on 2026-11-10 and retyped all 492 notes, replaced archive with date-archived, and moved content/ into works/. The folder was renamed back to content/ the same day.
Kept as a record, alongside its backup and plan in ~/feed-backups. It refuses to run again,
because the vault no longer looks the way it expects.

What it did:
Moves every clip from content/ into works/, with the higher-level types, and replaces `archive`
with `date-archived`.

    uv run works/merge_content.py            # dry run: classifies every note, saves and prints the plan
    uv run works/merge_content.py --apply    # carries out the saved plan (quit Obsidian on the Mac first)

Types: writing, research, book, podcast, video, film, show, music, art, project; `form` is optional
detail within a type (essay, news, paper, album, ...). The LLM picks both from each clip's title,
site, description and old type. --apply then, for each note, changes only these lines of the
frontmatter: type (plus a form line), the old type's tag, and archive → date-archived (the
date-added date when it was archived; the file's creation date if it has no date-added). File names
don't change. It moves the notes and content/Attachments into works/, updates links written with a
content/ path, backs up every note first, and checks the result.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tarfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import library as works  # noqa: E402

PLAN = Path.home() / "feed-backups" / "works-merge-plan.json"
TYPES = ["writing", "research", "book", "podcast", "video", "film", "show", "music", "art", "project"]
FORMS = {
    "writing": ["essay", "news", "review", "interview", "newsletter", "thread", "manifesto", "guide"],
    "research": ["paper", "report", "syllabus"],
    "music": ["album", "ep", "track", "mix", "radio show", "performance"],
    "film": ["documentary", "short"],
    "podcast": ["episode"],
    "video": ["talk", "interview", "video essay"],
}
# What each old type normally becomes; anything else is shown in the dry run for a look
EXPECTED = {"article": "writing", "paper": "research", "thread": "writing", "podcast": "podcast",
            "video": "video", "book": "book", "movie": "film", "film": "film", "music": "music",
            "essay": "writing", "other": "project"}

PROMPT = f"""You sort saved items in a personal library into types. For each item, pick:
- "type": one of {", ".join(TYPES)}.
  writing = articles, essays, blog posts, news, reviews, newsletters, social posts and threads.
  research = academic papers, preprints, reports, working papers, syllabi.
  book = anything published as a book. podcast = podcast episodes or series.
  video = talks, interviews, lectures, video essays, other online videos.
  film = feature films, documentaries, short films, skate/surf videos. show = TV or web series.
  music = albums, tracks, mixes, radio shows, live performances, music videos.
  art = artworks, exhibitions. project = websites, tools, datasets, directories, organisations,
  initiatives, open calls / RFPs (not a piece of writing about them).
  Writing about music, film or art is still writing.
- "form": optional detail, one of: {json.dumps(FORMS)}, or "" when none fits or it's unclear.
Reply with JSON only: {{"items": [{{"i": <the item's i>, "type": ..., "form": ...}}, ...]}}, one per item."""


def describe(i: int, path: Path) -> dict:
    fm = works.frontmatter(path.read_text())
    url = fm.get("url", "")
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    return {"i": i, "title": fm.get("title") or path.stem, "site": host, "old_type": fm.get("type", ""),
            "description": (fm.get("description") or "")[:300]}


def classify(batch: list[dict]) -> dict[int, dict]:
    for attempt in range(3):
        try:
            r = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {works.env('OPENROUTER_API_KEY')}"},
                json={"model": works.env("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5"),
                      "messages": [{"role": "system", "content": PROMPT},
                                   {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
                      "response_format": {"type": "json_object"}, "temperature": 0},
                timeout=120)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            items = json.loads(text[text.index("{"): text.rindex("}") + 1])["items"]
            out = {}
            for it in items:
                t = it.get("type")
                f = (it.get("form") or "").strip().lower()
                if t in TYPES:
                    out[int(it["i"])] = {"type": t, "form": f if f in FORMS.get(t, []) else ""}
            if len(out) == len(batch):
                return out
        except Exception as e:  # retried below
            print(f"  retrying a batch: {e}", file=sys.stderr)
        time.sleep(3)
    sys.exit("The LLM didn't classify a batch after 3 tries; nothing changed.")


def notes_to_merge() -> list[Path]:
    return sorted((works.VAULT / "content").glob("*.md")) + sorted((works.VAULT / "works").glob("*.md"))


def make_plan() -> list[dict]:
    paths = notes_to_merge()
    items = [describe(i, p) for i, p in enumerate(paths)]
    result: dict[int, dict] = {}
    for start in range(0, len(items), 20):
        print(f"  classifying {start + 1}–{min(start + 20, len(items))} of {len(items)}", file=sys.stderr)
        result.update(classify(items[start:start + 20]))
    plan = []
    for it, p in zip(items, paths):
        c = result[it["i"]]
        plan.append({"path": str(p.relative_to(works.VAULT)), "title": it["title"], "site": it["site"],
                     "old_type": it["old_type"], "type": c["type"], "form": c["form"]})
    return plan


def show(plan: list[dict]) -> None:
    print("\nNew types:")
    for t, n in Counter(x["type"] for x in plan).most_common():
        forms = Counter(x["form"] for x in plan if x["type"] == t and x["form"])
        print(f"  {t:9} {n:4}   " + ", ".join(f"{f} {k}" for f, k in forms.most_common()))
    odd = [x for x in plan if EXPECTED.get(x["old_type"].strip('"')) != x["type"]]
    print(f"\nNot the usual change for their old type ({len(odd)}):")
    for x in sorted(odd, key=lambda x: (x["type"], x["title"].lower())):
        form = f"/{x['form']}" if x["form"] else ""
        print(f"  {x['old_type'] or '(none)':8} → {x['type']}{form:13}  {x['title'][:70]}  <{x['site']}>")


# Corrections to the LLM's choices, after reading the dry run
OVERRIDES = {
    "works/The Commanding Heights (Will Manidis).md": ("writing", "essay"),
    "content/Why governments are addicted to debt.md": ("video", "video essay"),
    "content/Call Super & Parris - No Tags x The Listening Room.md": ("music", "mix"),
    "content/Mei Semones plays her fav songs at my house!!.md": ("music", "performance"),
}


def with_overrides(plan: list[dict]) -> list[dict]:
    for x in plan:
        if x["path"] in OVERRIDES:
            t, f = OVERRIDES[x["path"]]
            print(f"  correcting {x['title'][:60]}: {x['type']}/{x['form'] or '-'} → {t}/{f}")
            x["type"], x["form"] = t, f
    return plan


# ---------- applying ----------

def date_archived(path: Path, fm: dict) -> str:
    if fm.get("date-archived"):
        return fm["date-archived"]
    if fm.get("archive", "").strip().strip('"').lower() != "true":
        return ""
    if fm.get("date-added"):
        return fm["date-added"][:10]
    return datetime.fromtimestamp(path.stat().st_birthtime).strftime("%Y-%m-%d")


def rewrite(text: str, path: Path, new_type: str, form: str) -> str:
    """Changes only the type, form, the old type's tag, and archive → date-archived."""
    fm_text, body = works.split_note(text)
    fm = works.frontmatter(text)
    old_type = fm.get("type", "").strip().strip('"')
    archived = date_archived(path, fm)
    lines = fm_text.split("\n")
    out, in_tags, saw_type = [], False, False
    for line in lines:
        key = re.match(r"^([A-Za-z-]+):", line)
        if key:
            in_tags = key.group(1) == "tags"
        if key and key.group(1) == "type":
            out += [f"type: {new_type}", f"form: {form}".rstrip()]
            saw_type = True
            continue
        if key and key.group(1) in ("form", "archive", "date-archived"):
            continue
        if in_tags and old_type and re.fullmatch(rf'\s+- "?{re.escape(old_type)}"?', line):
            line = re.sub(re.escape(old_type), new_type, line)
        out.append(line)
    if not saw_type:
        out += [f"type: {new_type}", f"form: {form}".rstrip()]
    # date-archived goes where archive was for clips (the end), right after date-added for works
    if "date-added" in fm and path.parent.name == "works":
        i = next(i for i, l in enumerate(out) if l.startswith("date-added:"))
        out.insert(i + 1, f"date-archived: {archived}")
    else:
        out.append(f"date-archived: {archived}")
    return "---\n" + "\n".join(out) + "\n---\n" + body


def all_notes() -> list[Path]:
    return [p for p in works.VAULT.rglob("*.md") if ".obsidian" not in p.parts and ".trash" not in p.parts]


def apply(plan: list[dict]) -> None:
    vault = works.VAULT
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path.home() / "feed-backups" / f"vault-notes-before-merge-{stamp}.tar.gz"
    with tarfile.open(backup, "w:gz") as tar:
        for p in all_notes():
            tar.add(p, arcname=str(p.relative_to(vault)))
    print(f"Backed up every note to {backup}")

    dest = vault / "works"
    for x in plan:
        src = vault / x["path"]
        if not src.exists():
            sys.exit(f"{src} is gone; run the dry run again.")
        if src.parent.name == "content" and (dest / src.name).exists():
            sys.exit(f"works/{src.name} already exists; not overwriting.")
    for x in plan:
        src = vault / x["path"]
        new = rewrite(src.read_text(), src, x["type"], x["form"])
        works.frontmatter(new)  # must still parse
        target = dest / src.name
        target.write_text(new)
        if src != target:
            src.unlink()
    print(f"Updated and moved {len(plan)} notes into works/")

    attach = vault / "content" / "Attachments"
    if attach.exists():
        (dest / "Attachments").mkdir(exist_ok=True)
        for f in attach.iterdir():
            if (dest / "Attachments" / f.name).exists():
                sys.exit(f"works/Attachments/{f.name} already exists")
            shutil.move(str(f), dest / "Attachments" / f.name)
        attach.rmdir()
        print("Moved content/Attachments into works/Attachments")

    changed = 0
    for p in all_notes():
        text = p.read_text()
        new = re.sub(r"(\[\[|\]\(|!\[\[)content/", r"\1works/", text)
        if new != text:
            p.write_text(new)
            changed += 1
    print(f"Updated content/ links in {changed} notes")

    left = [p.name for p in (vault / "content").iterdir() if not p.name.startswith(".")]
    empty_dirs = [p for p in (vault / "content").iterdir() if p.is_dir() and not any(p.iterdir())]
    for d in empty_dirs:
        d.rmdir()
    left = [p.name for p in (vault / "content").iterdir() if not p.name.startswith(".")]
    if not left:
        for hidden in (vault / "content").iterdir():  # .DS_Store and the like
            hidden.unlink()
        (vault / "content").rmdir()
        print("Removed the empty content/ folder")
    else:
        print(f"Left in content/: {left}")

    problems = []
    for x in plan:
        p = dest / Path(x["path"]).name
        fm = works.frontmatter(p.read_text())
        if fm.get("type") != x["type"] or "archive" in fm or "date-archived" not in fm:
            problems.append(p.name)
    stray = [str(p.relative_to(vault)) for p in all_notes() if re.search(r"(\[\[|\]\()content/", p.read_text())]
    if problems or stray:
        print("PROBLEM:", problems[:10], stray[:10])
        sys.exit(1)
    archived = sum(1 for x in plan if works.frontmatter((dest / Path(x["path"]).name).read_text()).get("date-archived"))
    print(f"Checked: {len(plan)} notes in works/ with the new types, {archived} archived, no archive "
          "properties or content/ links left.")


def main() -> None:
    sys.exit("Already done on 2026-11-10; see the note at the top of this file.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.apply:
        plan = with_overrides(json.loads(PLAN.read_text()))
        apply(plan)
    else:
        plan = make_plan()
        PLAN.parent.mkdir(exist_ok=True)
        PLAN.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
        show(plan)
        print(f"\nSaved to {PLAN}. Nothing changed. Run with --apply to carry it out.")


if __name__ == "__main__":
    main()
