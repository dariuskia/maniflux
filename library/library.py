"""Notes in the vault's library (the content/ folder): books, films, music, writing, research and the
rest. Cleaning up what a clip knew with an LLM, looking up facts in open databases, and writing notes
in the library format (see README.md).

The LLM only tidies titles and names who made a work. Years, IDs and covers come from Open Library
(books) and Wikidata (films); if neither finds a confident match, those stay empty.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
import unicodedata
from pathlib import Path

import httpx

FEED_DIR = Path(__file__).resolve().parent.parent
VAULT = Path(os.environ.get("VAULT_PATH", Path.home() / "Documents" / "Obsidian Vault"))
NOTES_DIR = "content"  # the vault folder the library lives in
PEOPLE_DIR = "people"
UA = {"User-Agent": "library-notes/1.0 (https://github.com/dariuskia/maniflux; personal Obsidian vault script)"}
# Characters Obsidian doesn't allow in file names, or that break links
_BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]')


# ---------- settings ----------

def env(name: str, default: str = "") -> str:
    """A setting from the environment or the feed repo's .env. Never printed."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = FEED_DIR / ".env"
    if not env_file.exists():  # e.g. in the server's container, where settings come from the environment
        return default
    for line in env_file.read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


# ---------- reading notes ----------

def split_note(text: str) -> tuple[str, str]:
    """(frontmatter without the --- lines, body)."""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    return (m.group(1), m.group(2)) if m else ("", text)


def frontmatter(text: str) -> dict:
    """Frontmatter as strings and lists of strings, exactly as written (no date or number parsing)."""
    import yaml
    fm, _ = split_note(text)
    data = yaml.load(fm, Loader=yaml.BaseLoader) if fm.strip() else {}
    return data or {}


def link_target(link: str) -> str:
    """'[[people/Susan Sontag|Susan Sontag]]' -> 'Susan Sontag'"""
    inner = link.strip().strip("[]")
    return inner.split("|")[0].split("/")[-1].strip()


# ---------- the LLM ----------

CLEAN_PROMPT = """You tidy up a saved web clip about a book, film or other work, so it can become a clean
catalogue entry. Use only what's given plus well-known facts about the work. Reply with JSON only:
{
  "type": one of "book", "film", "series", "album", "essay", "artwork", "other". "book" is anything
          published as a book, however short, including essay collections. "essay" is only a single
          piece published online or in a magazine, not as a book.
  "title": the work's proper full title with subtitle, normal capitalisation, no site names or uploader names,
  "short_title": the title without its subtitle (the part before a colon), for a file name,
  "creators": the authors / directors / artists as full names, in the usual order,
  "doubt": "" or one short sentence if something is unclear (e.g. not really a book)
}"""


def clean(clip: dict) -> dict:
    """Ask the LLM for a tidy title, type and creators for one clip."""
    payload = {k: clip.get(k) for k in ("title", "url", "type", "published", "source")}
    payload["author"] = [link_target(a) for a in (clip.get("author") or []) if a]
    payload["description"] = (clip.get("description") or "")[:600]
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {env('OPENROUTER_API_KEY')}"},
        json={"model": env("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5"),
              "messages": [{"role": "system", "content": CLEAN_PROMPT},
                           {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
              "response_format": {"type": "json_object"}, "temperature": 0},
        timeout=60)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    out = json.loads(text[text.index("{"): text.rindex("}") + 1])
    out["creators"] = [c.strip() for c in out.get("creators") or [] if c.strip()]
    return out


# ---------- open databases ----------

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s).split(":")[0].strip()


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _surnames(names) -> set[str]:
    return {_norm(n).split()[-1] for n in names if _norm(n)}


def lookup_book(title: str, short_title: str, creators: list[str]) -> dict:
    """Open Library: the work whose title and author match. {} if none matches well."""
    params = {"title": short_title, "limit": 10,
              "fields": "key,title,author_name,first_publish_year,cover_i,id_wikidata"}
    if creators:
        params["author"] = creators[0]
    r = httpx.get("https://openlibrary.org/search.json", params=params, headers=UA, timeout=30)
    r.raise_for_status()
    best = None
    for doc in r.json().get("docs", []):
        score = max(_similar(doc.get("title", ""), short_title), _similar(doc.get("title", ""), title))
        if score >= 0.75 and (not creators or _surnames(creators) & _surnames(doc.get("author_name", []))):
            if not best or score > best[0]:
                best = (score, doc)
    if not best:
        return {}
    doc = best[1]
    out = {"openlibrary": doc["key"].rsplit("/", 1)[-1]}
    if doc.get("first_publish_year"):
        out["year"] = str(doc["first_publish_year"])
    if doc.get("id_wikidata"):
        out["wikidata"] = doc["id_wikidata"][0]
    if doc.get("cover_i"):
        out["cover"] = f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg"
    return out


def lookup_wikidata(title: str, short_title: str, creators: list[str], creator_prop: str,
                    kinds: list[str] | None = None) -> dict:
    """Wikidata: an item with this title whose creator (P57 director, P50 author, ...) is one of the
    creators, optionally of one of these kinds (instance of, or a subclass). {} if none matches."""
    if not creators:
        return {}
    ids = []
    for term in dict.fromkeys([short_title, title]):
        r = httpx.get("https://www.wikidata.org/w/api.php", headers=UA, timeout=30, params={
            "action": "wbsearchentities", "search": term, "language": "en", "type": "item",
            "limit": 20, "format": "json"})
        r.raise_for_status()
        ids += [x["id"] for x in r.json().get("search", []) if x["id"] not in ids]
    if not ids:
        return {}
    kind_filter = (f"?f wdt:P31/wdt:P279* ?kind . VALUES ?kind {{ {' '.join('wd:' + k for k in kinds)} }}"
                   if kinds else "")
    query = f"""SELECT ?f ?label ?date ?imdb ?whoLabel WHERE {{
      VALUES ?f {{ {' '.join('wd:' + i for i in ids)} }}
      {kind_filter}
      ?f wdt:{creator_prop} ?who . ?f rdfs:label ?label . FILTER(lang(?label) = "en")
      OPTIONAL {{ ?f wdt:P577 ?date }} OPTIONAL {{ ?f wdt:P345 ?imdb }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }} }}"""
    r = httpx.get("https://query.wikidata.org/sparql", params={"query": query, "format": "json"},
                  headers=UA, timeout=60)
    r.raise_for_status()
    rows = r.json()["results"]["bindings"]
    want = _surnames(creators)
    best = None
    for row in rows:
        score = max(_similar(row["label"]["value"], short_title), _similar(row["label"]["value"], title))
        if _surnames([row["whoLabel"]["value"]]) & want and score >= 0.75 and (not best or score > best[0]):
            best = (score, row["f"]["value"])
    if not best:
        return {}
    mine = [x for x in rows if x["f"]["value"] == best[1]]
    out = {"wikidata": best[1].rsplit("/", 1)[-1]}
    years = sorted(x["date"]["value"][:4] for x in mine if "date" in x)
    if years:
        out["year"] = years[0]
    imdb = next((x["imdb"]["value"] for x in mine if "imdb" in x), None)
    if imdb:
        out["imdb"] = imdb
    return out


def lookup_film(title: str, short_title: str, creators: list[str]) -> dict:
    # film, television series, short film
    return lookup_wikidata(title, short_title, creators, "P57", ["Q11424", "Q5398426", "Q24862"])


def lookup_book_all(title: str, short_title: str, creators: list[str]) -> dict:
    """Open Library for the cover and its year; Wikidata for the link between works, and the year
    when Open Library has none."""
    facts = lookup_book(title, short_title, creators)
    wd = lookup_wikidata(title, short_title, creators, "P50")
    if wd.get("wikidata"):
        facts["wikidata"] = wd["wikidata"]
    if not facts.get("year") and wd.get("year"):
        facts["year"] = wd["year"]
    return facts


def lookup_music(title: str, short_title: str, creators: list[str]) -> dict:
    """MusicBrainz: a release group (album, EP, single) with this title by one of the creators.
    The cover comes from the Cover Art Archive when it has one."""
    if not creators:
        return {}
    query = f'releasegroup:"{short_title}" AND artist:"{creators[0]}"'
    for wait in (2, 5, 10, 0):  # MusicBrainz answers 503 when asked too often; wait and ask again
        r = httpx.get("https://musicbrainz.org/ws/2/release-group/", headers=UA, timeout=30,
                      params={"query": query, "fmt": "json", "limit": 10})
        if r.status_code != 503 or not wait:
            break
        time.sleep(wait)
    r.raise_for_status()
    want = _surnames(creators) | {_norm(c) for c in creators}
    for rg in r.json().get("release-groups", []):
        artists = [a.get("name", "") for a in rg.get("artist-credit", []) if isinstance(a, dict)]
        named = {_norm(a) for a in artists} | _surnames(artists)
        if max(_similar(rg.get("title", ""), short_title), _similar(rg.get("title", ""), title)) >= 0.8 and named & want:
            out = {"musicbrainz": rg["id"]}
            if rg.get("first-release-date"):
                out["year"] = rg["first-release-date"][:4]
            kind = (rg.get("primary-type") or "").lower()
            out["form"] = {"album": "album", "ep": "ep", "single": "track"}.get(kind, "")
            art = httpx.head(f"https://coverartarchive.org/release-group/{rg['id']}/front-500",
                             headers=UA, timeout=30, follow_redirects=True)
            if art.status_code == 200:
                out["cover"] = f"https://coverartarchive.org/release-group/{rg['id']}/front-500"
            return out
    return {}


def database_cover(ids: dict) -> str:
    """A cover from the databases, for a note that already has their IDs: Open Library for books,
    the Cover Art Archive for music. Wikidata has no film posters. "" if there's none."""
    if ids.get("openlibrary"):
        r = httpx.get(f"https://openlibrary.org/works/{ids['openlibrary']}.json", headers=UA, timeout=30,
                      follow_redirects=True)
        if r.status_code == 200:
            covers = [c for c in r.json().get("covers") or [] if isinstance(c, int) and c > 0]
            if covers:
                return f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg"
    if ids.get("musicbrainz"):
        url = f"https://coverartarchive.org/release-group/{ids['musicbrainz']}/front-500"
        if httpx.head(url, headers=UA, timeout=30, follow_redirects=True).status_code == 200:
            return url
    return ""


def youtube_id(url: str) -> str:
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/)([\w-]{11})", url)
    return m.group(1) if m else ""


def page_image(url: str) -> str:
    """The page's own preview image, used as a cover when the databases have none: the poster on
    Letterboxd, the cover on Goodreads, the art on Bandcamp and Spotify. YouTube won't serve its page
    to scripts, but its thumbnails are at a fixed address for each video."""
    if vid := youtube_id(url):
        return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    try:
        r = httpx.get(url, headers={**UA, "Accept": "text/html"}, timeout=20, follow_redirects=True)
    except httpx.HTTPError:
        return ""
    # Structured data first: on Letterboxd it's the poster, where og:image is a wide still
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', r.text[:500_000], re.S):
        m = re.search(r'"image"\s*:\s*"(https?:[^"]+)"', block)
        if m:
            return m.group(1).replace("\\/", "/")
    for pattern in (r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']'):
        m = re.search(pattern, r.text[:300_000])
        if m and m.group(1).startswith("http"):
            return m.group(1).replace("&amp;", "&")
    return ""


def lookup(kind: str, title: str, short_title: str, creators: list[str]) -> dict:
    if kind == "book":
        return lookup_book_all(title, short_title, creators)
    if kind == "essay":
        return lookup_wikidata(title, short_title, creators, "P50")
    if kind in ("film", "series", "show"):
        return lookup_film(title, short_title, creators)
    if kind == "music":
        return lookup_music(title, short_title, creators)
    return {}


# ---------- writing notes ----------

ID_KEYS = ["openlibrary", "isbn", "imdb", "tmdb", "musicbrainz", "wikidata", "spotify"]
ROLE = {"book": "author", "essay": "author", "film": "director", "series": "director",
        "album": "musician", "artwork": "artist"}


def file_name(short_title: str, creators: list[str]) -> str:
    name = short_title.strip()
    if creators:
        name += f" ({creators[0]})"
    name = _BAD_NAME.sub("", name)
    return re.sub(r"\s+", " ", name).strip(" .")


def person_link(name: str) -> str:
    return f"[[{PEOPLE_DIR}/{name}|{name}]]"


def _yaml_value(v: str) -> str:
    """A scalar as Obsidian writes it: plain when safe, otherwise double-quoted."""
    if v == "":
        return ""
    if (re.fullmatch(r"[\w .,'()&!:/?=%+~@-]*", v) and v[0] not in "-!&*'?:%@"
            and ": " not in v and " #" not in v and not v.endswith(":")):
        return v
    return json.dumps(v, ensure_ascii=False)


def render(props: dict, body: str) -> str:
    """A note: frontmatter in the order given, then the body."""
    lines = ["---"]
    for k, v in props.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            lines += [f"  - {_yaml_value(x)}" for x in v]
        else:
            lines.append(f"{k}: {_yaml_value(str(v)) if v is not None else ''}".rstrip())
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def work_props(*, kind: str, title: str, creators: list[str], year: str, date_added: str,
               date_archived: str, via: str, url: str, source: str, facts: dict, tags: list[str]) -> dict:
    """Properties in the library format, in a fixed order."""
    props = {"type": kind, "title": title, "creators": [person_link(c) for c in creators], "year": year,
             "date-added": date_added, "date-archived": date_archived, "via": via, "url": url}
    if source:
        props["source"] = source
    props["cover"] = facts.get("cover", "")
    for k in ID_KEYS:
        if facts.get(k):
            props[k] = facts[k]
    props["tags"] = tags
    return props


# ---------- editing a note in place ----------

def set_props(text: str, updates: dict, remove: tuple = (), before: str = "tags",
              in_place_of: dict | None = None) -> str:
    """Changes only the named properties of a note, keeping every other line as it is. An existing
    property is replaced where it stands; one named in in_place_of ({new: old}) takes the old one's
    place; any other new one goes just before `before` (or at the end).
    Values are strings or lists of strings, written the same way as render()."""
    fm_text, body = split_note(text)
    blocks: list[list[str]] = []  # one block per property: its line plus any indented lines under it
    for line in fm_text.split("\n"):
        if blocks and (line.startswith((" ", "\t")) or not line.strip()):
            blocks[-1].append(line)
        else:
            blocks.append([line])

    def key(block):
        m = re.match(r"^([^:\s][^:]*):", block[0])
        return m.group(1) if m else None

    def lines_for(k, v):
        if isinstance(v, list):
            return [f"{k}:"] + [f"  - {_yaml_value(x)}" for x in v]
        return [f"{k}: {_yaml_value(str(v))}".rstrip()]

    done = set()
    out = []
    for b in blocks:
        k = key(b)
        swap = next((n for n, o in (in_place_of or {}).items() if o == k and n in updates and n not in done), None)
        if swap:  # e.g. creators goes where author was
            out.append(lines_for(swap, updates[swap]))
            done.add(swap)
        if k in remove:
            continue
        if k in updates:
            out.append(lines_for(k, updates[k]))
            done.add(k)
        else:
            out.append(b)
    new = [lines_for(k, v) for k, v in updates.items() if k not in done]
    at = next((i for i, b in enumerate(out) if key(b) == before), len(out))
    out[at:at] = new
    return "---\n" + "\n".join(l for b in out for l in b) + "\n---\n" + body
