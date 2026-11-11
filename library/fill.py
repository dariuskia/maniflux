"""Working out a saved link's note: reading the page, asking the LLM what it is, looking up facts and a
cover, and spotting duplicates. Used by the library service (service.py) for every save, whether it
comes straight from Link Ingest or Maniflux, or as a pending note from the Web Clipper.

The rules and the prompt are the ones Link Ingest used, moved here so they live in one place.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

import library as L

BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                         "Version/17.0 Safari/605.1.15", "Accept-Language": "en-US,en;q=0.9"}
TEXT_LIMIT = 20000
WORDS_PER_MINUTE = 238

# How a link is read ("kinds"), then the library type and form its note gets (see README.md)
KINDS = ["video", "article", "paper", "podcast", "music", "thread", "book", "film", "other"]
NOTE_TYPE = {
    "article": ("writing", ""), "paper": ("research", "paper"), "thread": ("writing", "thread"),
    "video": ("video", ""), "podcast": ("podcast", "episode"), "music": ("music", ""), "book": ("book", ""),
    "film": ("film", ""), "other": ("project", ""),
}
FORMS = {
    "writing": ["essay", "news", "review", "interview", "newsletter", "thread", "guide"],
    "research": ["paper", "report"],
    "music": ["album", "ep", "track", "mix", "radio show", "performance"],
    "video": ["interview", "talk", "video essay"],
    "podcast": ["episode"],
    "film": ["documentary", "short"],
}
# Where things are hosted, which is never their "via" (the outlet that put them out)
PLATFORMS = {"youtube", "substack", "spotify", "soundcloud", "bandcamp", "apple podcasts", "arxiv", "medium", "x",
             "twitter", "instagram", "vimeo", "mixcloud", "google", "github", "linkedin", "tiktok", "reddit"}
# Old values of via (which tool saved a note), from before via meant the outlet
TOOL_NAMES = {"clip", "reader", "link ingest", "web clipper", "maniflux", "linkingest"}

# Types whose facts come from the databases, and types that only get a cover
LOOKED_UP = {"book", "film", "show", "music"}
COVERED = LOOKED_UP | {"video", "podcast"}


# ---------- links ----------

def clean_url(raw: str) -> str:
    """The link without tracking bits (utm_*, si, fbclid, ...)."""
    m = re.search(r"https?://\S+", raw or "")
    if not m:
        return (raw or "").strip()
    u = urlsplit(m.group(0))
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k not in ("si", "fbclid", "gclid", "ref_src")]
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))


# Parts of an address that don't change which page it is: a start time, where the link was shared from, tracking
_NOT_THE_PAGE = {"t", "start", "feature", "ref", "ref_src", "source", "si", "igshid", "fbclid", "gclid",
                 "mc_cid", "mc_eid", "share", "context", "pp"}


def url_key(url: str) -> str:
    """Two links to the same page give the same key: no scheme, www, trailing slash, tracking or share
    bits. Any YouTube link becomes its video, and Spotify links drop their country (/intl-de/)."""
    from library import youtube_id
    if vid := youtube_id(url):
        return f"youtube:{vid}"
    u = urlsplit(clean_url(url))
    host_ = re.sub(r"^(www|m)\.", "", (u.hostname or "").lower())
    path = u.path.rstrip("/") or "/"
    if host_ == "open.spotify.com":
        path = re.sub(r"^/intl-[^/]+", "", path)
    query = "&".join(sorted(f"{k}={v}" for k, v in parse_qsl(u.query)
                            if k.lower() not in _NOT_THE_PAGE and not k.lower().startswith("utm_")))
    return f"{host_}{path}" + (f"?{query}" if query else "")


def host(url: str) -> str:
    try:
        return re.sub(r"^(www|m)\.", "", urlsplit(url).hostname or "")
    except ValueError:
        return ""


def rule_kind(url: str) -> str | None:
    """The kind when the link makes it clear (the same rules Link Ingest and Maniflux use)."""
    h = host(url)
    path = urlsplit(url).path
    if re.search(r"^music\.youtube\.com$|^music\.apple\.com$|(^|\.)soundcloud\.com$|(^|\.)bandcamp\.com$", h):
        return "music"
    if re.search(r"^(nts\.live|mixcloud\.com)$", h):
        return "music"
    if h == "open.spotify.com":
        return "music" if re.match(r"^/(intl-[^/]+/)?(track|album|playlist|artist)/", path) else "podcast"
    if re.search(r"(^|\.)youtube\.com$|^youtu\.be$|(^|\.)vimeo\.com$", h):
        return "video"
    if (h == "letterboxd.com" and path.startswith("/film/")) or (h.endswith("imdb.com") and path.startswith("/title/")):
        return "film"
    if (h == "goodreads.com" and re.match(r"^/(en/)?book/show/", path)) or \
            (h == "openlibrary.org" and re.match(r"^/(works|books)/", path)):
        return "book"
    if re.search(r"(^|\.)(arxiv\.org|openreview\.net|biorxiv\.org|ssrn\.com|aclanthology\.org)$", h):
        return "paper"
    if re.search(r"^(podcasts\.apple\.com|overcast\.fm|pca\.st)$", h):
        return "podcast"
    if re.search(r"^(twitter\.com|x\.com|bsky\.app)$", h):
        return "thread"
    if re.search(r"(^|\.)substack\.com$", h):
        return "article"
    return None


# ---------- reading the page ----------

def gather(url: str) -> dict:
    """What the page says about itself: title, site, description, authors, published date and text."""
    from bs4 import BeautifulSoup
    meta = {"title": "", "page_title": "", "site": host(url), "description": "", "published": "", "authors": [],
            "text": "", "words": 0, "generator": ""}
    try:
        r = httpx.get(url, headers=BROWSER, timeout=30, follow_redirects=True)
    except httpx.HTTPError:
        return meta
    if r.status_code >= 400:
        return meta
    if "pdf" in r.headers.get("content-type", ""):
        meta["title"] = urlsplit(url).path.rsplit("/", 1)[-1]
        return meta
    doc = BeautifulSoup(r.text[:3_000_000], "html.parser")

    def m(**attrs):
        tag = doc.find("meta", attrs=attrs)
        return (tag.get("content") or "").strip() if tag else ""

    ld_authors, ld_published = [], ""
    for s in doc.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except (ValueError, TypeError):
            continue
        for item in (data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []):
            if not isinstance(item, dict):
                continue
            a = item.get("author")
            for one in (a if isinstance(a, list) else [a] if a else []):
                name = one.get("name") if isinstance(one, dict) else one
                if isinstance(name, str) and name.strip():
                    ld_authors.append(name.strip())
            ld_published = ld_published or item.get("datePublished") or ""

    meta_authors = [a for a in (m(name="author"), m(property="article:author"), m(name="citation_author"))
                    if a and not a.startswith("http")]
    meta.update({
        "title": m(property="og:title") or m(name="twitter:title") or m(name="citation_title")
        or (doc.title.string.strip() if doc.title and doc.title.string else ""),
        "site": m(property="og:site_name") or host(url),
        "description": m(property="og:description") or m(name="description"),
        "published": m(property="article:published_time") or m(name="citation_publication_date")
        or m(name="date") or ld_published,
        "authors": list(dict.fromkeys(meta_authors + ld_authors)),
        "generator": m(name="generator"),
        # The page's own title, as the browser tab shows it: the note's title
        # (only the <title> in the page's head: icons in the page can have their own, like Goodreads' stars)
        "page_title": re.sub(r"\s+", " ", doc.head.title.get_text()).strip() if doc.head and doc.head.title else "",
    })
    for t in doc(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe"]):
        t.decompose()
    # One <article> is the piece itself; several mean a list (an author page, a front page): read it all
    articles = doc.find_all("article")
    root = (articles[0] if len(articles) == 1 else None) or doc.find("main") or doc.body
    text = re.sub(r"\s+", " ", root.get_text(" ") if root else "").strip()
    meta["words"] = len(text.split()) if text else 0
    meta["text"] = text[:TEXT_LIMIT]
    return meta


# ---------- the LLM ----------

def ask_model(url: str, meta: dict, forced: str | None) -> dict:
    """ask_model_once, tried a second time if the model's answer can't be read."""
    try:
        return ask_model_once(url, meta, forced)
    except ValueError:
        return ask_model_once(url, meta, forced)


def ask_model_once(url: str, meta: dict, forced: str | None) -> dict:
    """The model's view of what the link is: kind, authors, is_music, form, description, published,
    minutes. (Not the title: that's the page's own.)"""
    prompt = "\n".join([
        "You are filing a link in a personal reading log. Using the page data below, reply with only a JSON object with these keys:",
        '- "title": "" unless the metadata title below is empty; only then, a short title for it',
        f'- "type": one of {", ".join(KINDS)} (essays and blog posts count as article)'
        + (f" (it is a {forced})" if forced else ""),
        '- "authors": array of the people who made it or are its subject. For music: the artists or DJs performing, '
        "not the channel, station or label that posted it. For an interview: the person interviewed, then the "
        "interviewer if named. People only; [] if no person is named",
        '- "organisations": array of the organisations that made it, only if no person is named (a report credited '
        'to a company, an article by several organisations with no author); otherwise []',
        '- "via": the organisation or outlet that put it out: the publication, the company (for its own blog), the '
        "newsletter, podcast, YouTube channel, radio station, record label or publishing house. Never the platform it "
        "is hosted on (YouTube, Substack, Spotify, SoundCloud, Bandcamp, Apple Podcasts, arXiv, Medium, X, Instagram). "
        '"" if unclear',
        '- "is_music": true if this is music to listen to: a DJ set, mix, radio show, live performance, session, music '
        "video, album or track. A video where the artist plays or performs music counts, even with talking in between. "
        "false if it is mainly about something (an interview without performance, a talk, review or documentary), "
        "even when the subject is music",
        f'- "form": one of {json.dumps(FORMS)} for its kind, or "" if none fits',
        '- "description": a 2-3 sentence plain summary of what it says or covers',
        '- "published": publication date as YYYY-MM-DD, or "" if unknown',
        '- "minutes": your estimate of how many minutes it takes to read, watch or listen to the full piece (a number; '
        "for a paper, the full paper, not just the abstract; for music, the track or album)",
        "",
        f"URL: {url}",
        f"Site: {meta.get('site', '')}",
        f"Metadata title: {meta.get('title', '')}",
        f"Metadata authors: {', '.join(meta.get('authors') or [])}",
        *([f"Channel that posted it: {meta['channel']}"] if meta.get("channel") else []),
        *([f"Publication named when it was saved: {meta['via_hint']}"] if meta.get("via_hint") else []),
        *([f"Category chosen by the uploader: {meta['category']}"] if meta.get("category") else []),
        f"Metadata published: {meta.get('published', '')}",
        f"Metadata description: {meta.get('description', '')}",
        "",
        "Page text:",
        (meta.get("text") or "(none available)")[:TEXT_LIMIT],
    ])
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {L.env('OPENROUTER_API_KEY')}", "X-Title": "Library"},
        json={"model": L.env("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5"), "max_tokens": 800,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=90)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content)
    m = re.search(r"\{[\s\S]*\}", content or "")
    if not m:
        raise ValueError("the model didn't return JSON")
    return json.loads(m.group(0))


def to_date(s) -> str:
    """YYYY-MM-DD from an ISO date or time, or from an email-style date ("Tue, 4 Nov 2026 ..."); "" if unclear."""
    s = str(s or "").strip()
    if m := re.match(r"(\d{4}-\d{2}-\d{2})", s):
        return m.group(1)
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        return ""


def clean_names(names) -> list[str]:
    names = [names] if isinstance(names, str) else names or []
    return list(dict.fromkeys(re.sub(r"[\[\]|#^]", "", str(n)).strip() for n in names if str(n).strip()))[:10]


# ---------- working out a note ----------

def work_out(job: dict) -> dict:
    """Everything a finished note says, from what the save knew. job: url, and optionally title,
    creators, published, minutes, text, description (from Maniflux), or the YouTube details Link
    Ingest read on the device (category, channel, description, minutes, published, title)."""
    url = clean_url(job["url"])
    forced = rule_kind(url)
    meta = {"title": job.get("title") or "", "site": host(url), "description": job.get("description") or "",
            "published": job.get("published") or "", "authors": clean_names(job.get("creators")),
            "text": job.get("text") or "", "words": len((job.get("text") or "").split()),
            "channel": job.get("channel") or "", "category": job.get("category") or "",
            "via_hint": job.get("via_hint") or ""}
    if not meta["text"] and not L.youtube_id(url):
        # Maniflux sends the article's text; otherwise read the page (YouTube won't show it to the server)
        page = gather(url)
        meta.update({k: v for k, v in page.items() if v and not meta.get(k)})
    if not forced and re.search(r"substack", meta.get("generator", ""), re.I):
        forced = "article"
    ai = ask_model(url, meta, forced)

    kind = forced or (lambda t: t if t in KINDS else "other")(
        {"essay": "article", "blog-post": "article"}.get(str(ai.get("type", "")).lower(), str(ai.get("type", "")).lower()))
    if kind == "video" and ai.get("is_music") is True:
        kind = "music"
    lib_type, rule_form = NOTE_TYPE[kind]
    ai_form = str(ai.get("form") or "").lower().strip()
    form = ai_form if ai_form in FORMS.get(lib_type, []) else rule_form

    # Creators are people whenever anyone is named; organisations only when no person is
    people = clean_names(ai.get("authors")) or meta["authors"]
    orgs = [] if people else clean_names(ai.get("organisations"))
    creators = people or orgs
    via = re.sub(r"\s+", " ", str(ai.get("via") or "")).strip()
    if via.lower() in PLATFORMS:
        via = ""
    # The title the save brought (Maniflux's item, YouTube's, the Web Clipper's), else the page's own as
    # the browser tab shows it, else its preview title, else the address. A site-name placeholder from
    # an offline save doesn't count.
    given = (job.get("title") or "").strip()
    if given in (host(url), "Untitled"):
        given = ""
    # (the model's title only when the page couldn't be read and gave none)
    title = re.sub(r"\s+", " ", given or meta.get("page_title") or meta["title"] or str(ai.get("title") or "")
                   or url).strip()
    title = re.sub("[\u200b-\u200f\u202a-\u202e\ufeff]", "", title)  # invisible marks (Letterboxd starts with one)
    minutes = job.get("minutes") or None
    if not minutes and lib_type == "writing" and meta["words"] > 150:
        minutes = max(1, round(meta["words"] / WORDS_PER_MINUTE))
    if not minutes and str(ai.get("minutes") or "").replace(".", "").isdigit() and float(ai["minutes"]) > 0:
        minutes = round(float(ai["minutes"]))
    if lib_type == "book":
        minutes = None
    out = {"url": url, "title": title, "creators": creators, "org_creators": orgs, "via": via,
           "type": lib_type, "form": form,
           "description": re.sub(r"\s+", " ", str(ai.get("description") or meta["description"] or "")).strip(),
           "published": to_date(ai.get("published")) or to_date(meta["published"]), "minutes": minutes or ""}

    # Facts from the databases, for books, films, shows and music
    if lib_type in LOOKED_UP and creators:
        tidy = L.clean({"title": title, "url": url, "type": lib_type, "author": creators,
                        "description": out["description"]})
        facts = L.lookup(lib_type, tidy["title"], tidy["short_title"], creators)
        if facts and not set(facts) <= {"cover"}:
            out["title"] = tidy["title"]  # confirmed by a database
            for k in ("year", "cover", *L_IDS):
                if facts.get(k):
                    out[k] = facts[k]
            if facts.get("form") and not out["form"]:
                out["form"] = facts["form"]
    # A cover: the databases first, then the link
    if lib_type in COVERED and not out.get("cover"):
        out["cover"] = L.database_cover({k: out[k] for k in L_IDS if out.get(k)}) or L.page_image(url)
    return out


L_IDS = ["openlibrary", "isbn", "imdb", "tmdb", "musicbrainz", "wikidata", "spotify"]


# ---------- the existing notes, for duplicates ----------

def _title_key(title: str) -> str:
    t = L._norm(str(title or "")).split(" - ")[0]
    return re.sub(r"\s+", " ", t).strip()


class Index:
    """Links, IDs and titles of the notes in content/, to spot a save that's already there."""

    def __init__(self, folder: Path):
        self.urls: dict[str, Path] = {}
        self.ids: dict[tuple[str, str], Path] = {}
        self.titles: dict[str, list[tuple[Path, set[str]]]] = {}
        for p in sorted(folder.glob("*.md")):
            try:
                self.add(p, L.frontmatter(p.read_text()))
            except Exception:
                continue

    def add(self, path: Path, fm: dict) -> None:
        if fm.get("status") in ("pending", "duplicate"):
            return
        if fm.get("url"):
            self.urls.setdefault(url_key(fm["url"]), path)
        for k in L_IDS:
            if fm.get(k):
                self.ids.setdefault((k, str(fm[k]).strip()), path)
        key = _title_key(fm.get("title") or path.stem)
        names = fm.get("creators") or fm.get("author") or []
        surnames = L._surnames([L.link_target(n) for n in (names if isinstance(names, list) else [names]) if n])
        if len(key) >= 8:
            self.titles.setdefault(key, []).append((path, surnames))

    def by_url(self, url: str) -> Path | None:
        p = self.urls.get(url_key(url))
        return p if p and p.exists() else None

    def duplicate(self, note: dict) -> tuple[Path | None, str]:
        """An existing note that's the same work: the same link, the same database ID, or the same title
        and creator. Returns (note, why)."""
        if (p := self.by_url(note["url"])):
            return p, "the same link"
        for k in L_IDS:
            if note.get(k) and (p := self.ids.get((k, str(note[k])))) and p.exists():
                return p, f"the same {k}"
        surnames = L._surnames(note.get("creators") or [])
        for p, theirs in self.titles.get(_title_key(note.get("title", "")), []):
            if p.exists() and surnames and surnames & theirs:
                return p, "the same title and creator"
        return None, ""


# ---------- writing ----------

def creator_links(note: dict) -> list[str]:
    orgs = set(note.get("org_creators") or [])
    return [L.org_link(c) if c in orgs else L.person_link(c) for c in note["creators"]]


def props_for(note: dict, *, source: str, date_added: str) -> dict:
    """The finished note's properties, in the usual order. source: how it reached you. via: the
    organisation or outlet that put it out."""
    props = {"title": note["title"], "url": note["url"], "source": source,
             "creators": creator_links(note), "via": L.org_link(note["via"]) if note.get("via") else "",
             "published": note.get("published", ""),
             "date-added": date_added, "type": note["type"], "form": note.get("form", ""),
             "minutes": str(note.get("minutes") or ""), "description": note.get("description", "")}
    for k in ("year", "cover", *L_IDS):
        if note.get(k):
            props[k] = note[k]
    props["tags"] = ["clippings", note["type"]]
    props["date-archived"] = ""
    props["status"] = "ready"
    return props


def free_name(folder: Path, title: str) -> Path:
    base = re.sub(r'[\\/:*?"<>|#^\[\]]', "", title).strip(" .")
    base = re.sub(r"\s+", " ", base)[:120] or "Untitled"
    for n in range(1, 1000):
        p = folder / (f"{base}.md" if n == 1 else f"{base} ({n}).md")
        if not p.exists():
            return p
    raise OSError("no free file name")
