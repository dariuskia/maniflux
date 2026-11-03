import html
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_DROP = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_BLOCKS = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "tr", "figure",
           "figcaption", "section", "article", "header", "footer", "ul", "ol", "table", "hr"]


def quick_text(content: str) -> str:
    """Fast plain text for cards and keyword rules."""
    return _SPACE.sub(" ", html.unescape(_TAGS.sub(" ", _DROP.sub(" ", content or "")))).strip()


def full_text(content: str) -> str:
    """Plain text with paragraph breaks, for the note's ai-source block."""
    soup = BeautifulSoup(content or "", "html.parser")
    for el in soup(["script", "style", "noscript", "iframe", "svg", "form"]):
        el.decompose()
    # Line breaks only between blocks, so links and emphasis stay inside their sentence
    for el in soup.find_all(_BLOCKS):
        el.insert_after("\n")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    lines = (_SPACE.sub(" ", s).strip() for s in soup.get_text().split("\n"))
    return "\n".join(line for line in lines if line)


def excerpt(text: str, n: int = 220) -> str:
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# Same tracking parameters Link Ingest strips
_TRACKING = {"si", "fbclid", "gclid", "ref_src"}


def clean_url(url: str) -> str:
    """Drop tracking parameters, as Link Ingest's cleanUrl does."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k not in _TRACKING]
    return urlunsplit((p.scheme, p.netloc, p.path or "/", urlencode(q), p.fragment))


def url_key(url: str) -> str:
    """Looser form of a URL for spotting duplicates: no tracking, fragment, www. or trailing slash."""
    try:
        p = urlsplit(clean_url(url))
    except ValueError:
        return url.strip().lower()
    host = p.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = p.path.rstrip("/") or ""
    return f"{host}{path}" + (f"?{p.query}" if p.query else "")
