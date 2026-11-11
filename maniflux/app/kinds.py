"""Type of an entry (video, article, podcast…), using the same URL rules as Link Ingest."""

import re
from urllib.parse import urlsplit

TYPES = ["article", "video", "podcast", "paper", "music", "thread", "book", "other"]

# The kinds above decide ranking, rules and tabs. Saved notes use the library types and an optional
# form (see library/README.md; the same mapping as Link Ingest's NOTE_TYPE).
NOTE_TYPE = {
    "article": ("writing", ""), "paper": ("research", "paper"), "thread": ("writing", "thread"),
    "video": ("video", ""), "podcast": ("podcast", "episode"), "music": ("music", ""),
    "book": ("book", ""), "other": ("project", ""),
}


def note_type(kind: str) -> tuple[str, str]:
    return NOTE_TYPE.get(kind, ("writing", ""))


def _host(url: str) -> str:
    try:
        return re.sub(r"^(www|m)\.", "", urlsplit(url).hostname or "")
    except ValueError:
        return ""


def rule_type(url: str) -> str | None:
    """Port of ruleType() in Link Ingest."""
    h = _host(url)
    try:
        path = urlsplit(url).path
    except ValueError:
        path = ""
    if re.search(r"^music\.youtube\.com$|^music\.apple\.com$|(^|\.)soundcloud\.com$|(^|\.)bandcamp\.com$", h):
        return "music"
    if h == "open.spotify.com":
        if re.match(r"^/(intl-[^/]+/)?(track|album|playlist|artist)/", path):
            return "music"
        return "podcast"
    if re.search(r"(^|\.)youtube\.com$|^youtu\.be$|(^|\.)vimeo\.com$", h):
        return "video"
    if re.search(r"(^|\.)(arxiv\.org|openreview\.net|biorxiv\.org|ssrn\.com|aclanthology\.org)$", h):
        return "paper"
    if re.search(r"^(podcasts\.apple\.com|overcast\.fm|pca\.st)$", h):
        return "podcast"
    if re.search(r"^(twitter\.com|x\.com|bsky\.app)$", h):
        return "thread"
    if re.search(r"(^|\.)substack\.com$", h):
        return "article"
    return None


def known_type(entry: dict) -> str | None:
    """The type when it's clear from the entry, else None."""
    t = rule_type(entry.get("url", ""))
    if t:
        return t
    mimes = [e.get("mime_type", "") for e in entry.get("enclosures") or []]
    if any(m.startswith("audio/") for m in mimes):
        return "podcast"
    if any(m.startswith("video/") for m in mimes):
        return "video"
    feed_url = (entry.get("feed") or {}).get("feed_url", "")
    if "youtube.com/feeds/" in feed_url:
        return "video"
    if feed_url.endswith(".substack.com/feed"):
        return "article"
    return None


def display_type(entry: dict) -> str:
    """Type for ranking, rules and the card. Unclear entries count as articles."""
    return known_type(entry) or "article"
