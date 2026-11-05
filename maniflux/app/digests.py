"""Digests: a model reads what a few groups of feeds published and writes what the instructions ask for."""

import html
import json
import re
from datetime import datetime, time, timedelta, timezone
from xml.sax.saxutils import escape as xml_escape

import markdown

from .text import full_text, parse_time

FREQUENCIES = {"daily": "Every day", "weekly": "Every week", "biweekly": "Every two weeks", "monthly": "Every month"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# How far back the first digest looks
FIRST_LOOKBACK = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 31}

ITEM_TEXT = 4000         # characters of each item given to the model
ONE_CALL = 120_000       # above this much item text, relevant parts are picked out in batches first
BATCH = 60_000


# ---------- schedule ----------

def next_run(d: dict, after: datetime, last_run: datetime | None = None) -> datetime:
    """The next scheduled time after `after` (both in the local time zone)."""
    hh, mm = (int(x) for x in d["at"].split(":"))
    at = time(hh, mm)
    tz = after.tzinfo
    if d["frequency"] == "daily":
        candidate = datetime.combine(after.date(), at, tz)
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate
    if d["frequency"] in ("weekly", "biweekly"):
        day = after.date() + timedelta(days=(d["weekday"] - after.weekday()) % 7)
        candidate = datetime.combine(day, at, tz)
        if candidate <= after:
            candidate += timedelta(days=7)
        # Every other week: skip a week if the last digest was less than ten days before
        if d["frequency"] == "biweekly" and last_run and candidate - last_run < timedelta(days=10):
            candidate += timedelta(days=7)
        return candidate
    # monthly, on day 1–28 so every month has it
    day = min(max(d["monthday"], 1), 28)
    candidate = datetime.combine(after.date().replace(day=day), at, tz)
    if candidate <= after:
        year, month = (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
        candidate = datetime.combine(after.date().replace(year=year, month=month, day=day), at, tz)
    return candidate


def describe_schedule(d: dict) -> str:
    when = {
        "daily": "Every day",
        "weekly": f"Every {WEEKDAYS[d['weekday']]}",
        "biweekly": f"Every other {WEEKDAYS[d['weekday']]}",
        "monthly": f"Monthly on the {d['monthday']}{_suffix(d['monthday'])}",
    }[d["frequency"]]
    return f"{when} at {d['at']}"


def _suffix(n: int) -> str:
    return "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# ---------- items ----------

def to_items(entries: list[dict]) -> list[dict]:
    """Entries as numbered items for the model, oldest first."""
    entries = sorted(entries, key=lambda e: e["published_at"])
    items = []
    for n, e in enumerate(entries, start=1):
        items.append({
            "n": n,
            "id": e["id"],
            "title": e.get("title") or "Untitled",
            "url": e.get("url", ""),
            "feed": (e.get("feed") or {}).get("title", ""),
            "date": parse_time(e["published_at"]).date().isoformat(),
            "text": full_text(e.get("content", ""))[:ITEM_TEXT],
        })
    return items


def _item_block(it: dict) -> str:
    return f"[{it['n']}] {it['feed']} · {it['date']} · {it['title']}\n{it['text']}"


def _batches(items: list[dict]) -> list[list[dict]]:
    batches, current, size = [], [], 0
    for it in items:
        block = len(_item_block(it))
        if current and size + block > BATCH:
            batches.append(current)
            current, size = [], 0
        current.append(it)
        size += block
    if current:
        batches.append(current)
    return batches


# ---------- prompts ----------

def pick_prompt(instructions: str, batch: list[dict]) -> str:
    return "\n".join([
        "You are helping write a digest for one reader. Their instructions:",
        instructions.strip(),
        "",
        "From the numbered items below, write down every detail relevant to those instructions as short bullet points.",
        "End each bullet with the number of the item it comes from in brackets, e.g. [12].",
        "Keep names, titles, dates, venues and prices exactly as written. Don't add anything that isn't in the items.",
        "If nothing is relevant, reply with only: NONE",
        "",
        "Items:",
        "",
        "\n\n".join(_item_block(it) for it in batch),
    ])


def write_prompt(d: dict, start: datetime, end: datetime, material: str, from_notes: bool) -> str:
    what = "notes taken from the reader's feeds" if from_notes else "items from the reader's feeds"
    return "\n".join([
        f"You write a digest for one reader, covering {start:%B %-d} to {end:%B %-d, %Y}.",
        "Their instructions:",
        d["instructions"].strip(),
        "",
        f"Below are numbered {what}. Using only them, write the digest in Markdown:",
        "- Short sections with ## headings that fit the instructions, then bullet points. Plain, concise language.",
        "- End every point with the item numbers it comes from in brackets, e.g. [3] or [3][7].",
        "- Leave out anything the instructions don't ask for.",
        "- Don't invent names, dates, places or details that aren't in the material.",
        "- If nothing qualifies, say so in one sentence.",
        "Start directly with the content: no title, greeting or closing.",
        "",
        "Material:",
        "",
        material,
    ])


async def write(ai, model: str, pick_model: str, d: dict, items: list[dict],
                start: datetime, end: datetime) -> str:
    """The digest in Markdown, with [n] citations."""
    blocks = "\n\n".join(_item_block(it) for it in items)
    if len(blocks) <= ONE_CALL:
        return await ai.chat(write_prompt(d, start, end, blocks, from_notes=False), model=model,
                             max_tokens=4000, timeout=240)
    # Too much for one request: pick out what's relevant batch by batch, then write from those notes
    notes = []
    for batch in _batches(items):
        picked = await ai.chat(pick_prompt(d["instructions"], batch), model=pick_model, max_tokens=3000,
                               timeout=180)
        if picked.strip().upper() != "NONE":
            notes.append(picked.strip())
    if not notes:
        return "Nothing in these feeds matched the instructions this time."
    return await ai.chat(write_prompt(d, start, end, "\n".join(notes), from_notes=True), model=model,
                         max_tokens=4000, timeout=240)


# ---------- output ----------

_CITE = re.compile(r"\[(\d+)\]")


def render(body: str, items: list[dict]) -> str:
    """Markdown to HTML, with [n] turned into links to the items."""
    by_n = {it["n"]: it for it in items}
    # The model's text is shown as text, never as HTML
    safe = html.escape(body, quote=False)
    out = markdown.markdown(safe, extensions=["sane_lists"])

    def link(m):
        it = by_n.get(int(m.group(1)))
        if not it:
            return ""
        title = html.escape(f"{it['feed']}: {it['title']}")
        return f'<a class="cite" href="{html.escape(it["url"])}" title="{title}">[{it["n"]}]</a>'

    return _CITE.sub(link, out)


def sources_html(items: list[dict]) -> str:
    rows = "".join(
        f'<li>[{it["n"]}] <a href="{html.escape(it["url"])}">{html.escape(it["title"])}</a> '
        f'<span class="muted">{html.escape(it["feed"])} · {it["date"]}</span></li>'
        for it in items)
    return f'<details class="sources"><summary>{len(items)} items read</summary><ol>{rows}</ol></details>'


def atom(runs: list[dict], base_url: str) -> str:
    """The digests as an Atom feed, so Miniflux can show them in the list."""
    updated = runs[0]["finished_at"] if runs else datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries = []
    for r in runs:
        url = f"{base_url}/digests/runs/{r['id']}"
        items = json.loads(r.get("items") or "[]")
        content = render(r["body"] or "", items)
        entries.append(f"""  <entry>
    <id>tag:maniflux,digest-run-{r['id']}</id>
    <title>{xml_escape(run_title(r))}</title>
    <link href="{xml_escape(url)}"/>
    <updated>{r['finished_at']}</updated>
    <published>{r['finished_at']}</published>
    <author><name>Maniflux</name></author>
    <content type="html">{xml_escape(content)}</content>
  </entry>""")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>tag:maniflux,digests</id>
  <title>Maniflux digests</title>
  <link href="{xml_escape(base_url)}/digests"/>
  <updated>{updated}</updated>
{chr(10).join(entries)}
</feed>
"""


def run_title(r: dict) -> str:
    start, end = parse_time(r["period_start"]), parse_time(r["period_end"])
    span = f"{start:%b %-d}–{end:%b %-d}" if start.date() != end.date() else f"{end:%b %-d}"
    return f"{r['name']}: {span}"

