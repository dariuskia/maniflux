"""The vault's content/ folder: spotting saved URLs and writing new notes.

Maniflux only creates notes. It never edits or deletes one.
"""

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from . import kinds
from .text import clean_author, clean_url, parse_time, url_key


class _TextDates(yaml.SafeLoader):
    """Reads dates as plain text, as Obsidian does with odd ones like 14:02:96."""


_TextDates.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


@dataclass
class Broken:
    path: str
    problem: str


def read_frontmatter(text: str) -> dict:
    """Frontmatter as a dict. Raises ValueError when it's missing or can't be read."""
    if not text.startswith("---"):
        raise ValueError("no frontmatter")
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|$)", text, re.S)
    if not m:
        raise ValueError("frontmatter isn't closed")
    try:
        data = yaml.load(m.group(1), Loader=_TextDates)
    except yaml.YAMLError as e:
        raise ValueError(f"frontmatter can't be read ({str(e).splitlines()[0]})") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("frontmatter isn't a list of properties")
    return data


def urls_of(fm: dict) -> list[str]:
    """URLs a note stands for. The Web Clipper sometimes keeps the URL under `source`."""
    out = []
    for key in ("url", "source"):
        v = fm.get(key)
        if isinstance(v, str) and re.match(r"^https?://", v.strip(), re.I):
            out.append(v.strip())
    return out


class VaultIndex:
    """Which URLs are already in the notes folder, kept up to date by re-reading changed files."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.lock = threading.Lock()
        self.files: dict[Path, tuple[float, list[str], str | None]] = {}  # path -> (mtime, url keys, problem)

    def refresh(self):
        with self.lock:
            seen = set()
            for p in self.folder.rglob("*.md"):
                if any(part.startswith(".") for part in p.relative_to(self.folder).parts):
                    continue
                seen.add(p)
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                cached = self.files.get(p)
                if cached and cached[0] == mtime:
                    continue
                try:
                    fm = read_frontmatter(p.read_text(encoding="utf-8"))
                    self.files[p] = (mtime, [url_key(u) for u in urls_of(fm)], None)
                except (ValueError, OSError, UnicodeDecodeError) as e:
                    self.files[p] = (mtime, [], str(e))
            for p in set(self.files) - seen:
                del self.files[p]

    def find(self, url: str) -> Path | None:
        self.refresh()
        key = url_key(url)
        with self.lock:
            for p, (_, keys, _) in self.files.items():
                if key in keys:
                    return p
        return None

    def broken(self) -> list[Broken]:
        self.refresh()
        with self.lock:
            return sorted(
                (Broken(str(p.relative_to(self.folder)), prob) for p, (_, _, prob) in self.files.items() if prob),
                key=lambda b: b.path,
            )

    def count(self) -> int:
        self.refresh()
        return len(self.files)


# ---------- writing ----------

def safe_file_name(s: str) -> str:
    """Same as safeFileName() in Link Ingest, plus no leading dots (hidden files)."""
    s = re.sub(r'[\\/:*?"<>|#^\[\]]', "", s or "")
    s = re.sub(r"\s+", " ", s).strip()[:120].strip()
    return s.lstrip(".").strip()


def split_authors(raw: str) -> list[str]:
    raw = re.sub(r"^\s*by\s+", "", clean_author(raw), flags=re.I)
    names = re.split(r"\s*(?:,|;|&|\band\b)\s*", raw)
    out, seen = [], set()
    for n in names:
        n = re.sub(r"[\[\]|#^]", "", n).strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def q(s) -> str:
    """Double-quoted YAML string (JSON strings are valid YAML)."""
    return json.dumps(str(s), ensure_ascii=False)


def published_day(entry: dict, tz) -> str:
    return parse_time(entry["published_at"]).astimezone(tz).date().isoformat() if entry.get("published_at") else ""


def note_props(entry: dict, tz) -> dict:
    """What the note says about the item, also given to the model as context."""
    feed = entry.get("feed") or {}
    return {
        "title": entry.get("title") or feed.get("title") or "Untitled",
        "url": clean_url(entry["url"]),
        "source": feed.get("title") or "",
        "author": ", ".join(split_authors(entry.get("author", ""))),
        "published": published_day(entry, tz),
    }


def render_note(entry: dict, thought: str, now: datetime, people_folder: str, tz,
                note_type: str, description: str, authors: list[str] | None = None, status: str = "") -> str:
    """authors: from the model, used when the feed doesn't name a real author."""
    props = note_props(entry, tz)
    lib_type, form = kinds.note_type(note_type)
    minutes = "" if note_type == "book" else (entry.get("reading_time") or "")
    authors = split_authors(entry.get("author", "")) or [
        a for a in (re.sub(r"[\[\]|#^]", "", str(x)).strip() for x in authors or []) if a]

    lines = [
        "---",
        f"title: {q(props['title'])}",
        f"url: {q(props['url'])}",
        f"source: {q(props['source']) if props['source'] else ''}",
        "via: reader",
        "creators:",
        *[f"  - {q(f'[[{people_folder}/{a}|{a}]]')}" for a in authors],
        f"published: {props['published']}",
        f"date-added: {now.isoformat(timespec='seconds')}",
        f"type: {lib_type}",
        f"form: {form}".rstrip(),
        f"minutes: {minutes}",
        f"description: {q(description) if description else ''}",
        "tags:",
        "  - clippings",
        f"  - {lib_type}",
        "date-archived:",
        *([f"status: {status}"] if status else []),
        "---",
        "",
        "## Notes",
        "",
        *([thought.strip()] if thought.strip() else []),
        "",
    ]
    return "\n".join(lines)


def write_note(folder: Path, entry: dict, body: str) -> Path:
    """Create the note with a free file name. Never overwrites."""
    base = safe_file_name(entry.get("title", "")) or safe_file_name(urlsplit(entry["url"]).hostname or "") or "Untitled"
    folder.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        name = f"{base}.md" if n == 1 else f"{base} ({n}).md"
        path = folder / name
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(body)
            return path
        except FileExistsError:
            n += 1
