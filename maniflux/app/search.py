"""Search that matches the start of words, so results show up while you're still typing.

"RIP J" finds "RIP Juan Atkins"; "rent" finds "rent" and "renters" but not "current".
Case and accents are ignored.
"""

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass, field

from .text import clean_author, parse_time, quick_text

_WORD = re.compile(r"\w+")


def fold(text: str) -> str:
    """Lowercase without accents: "José" -> "jose"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def words(text: str) -> list[str]:
    return _WORD.findall(fold(text))


def terms(query: str) -> list[str]:
    return words(query)


def matches(query_terms: list[str], text_words: set[str] | list[str]) -> bool:
    """Every search term starts some word of the text."""
    return all(any(w.startswith(t) for w in text_words) for t in query_terms)


# ---------- the archive ----------

FULL_RELOAD_SECONDS = 30 * 60
PAGE = 1000  # Miniflux returns at most this many per request in practice; we page through
TEXT_KEPT = 1500  # characters of text kept per entry, enough for the card


@dataclass
class _Indexed:
    entry: dict           # the entry, with its content cut down to plain text
    title_words: set[str]
    all_words: set[str]


@dataclass
class ArchiveIndex:
    """Archived (read) entries in memory, kept current with Miniflux's "changed since" filter."""
    entries: dict[int, _Indexed] = field(default_factory=dict)
    synced_at: float = 0.0       # Unix time of the last sync with Miniflux
    loaded_at: float = 0.0       # Unix time of the last full load
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @staticmethod
    def _index(e: dict) -> _Indexed:
        text = quick_text(e.get("content", ""))
        slim = dict(e, content=text[:TEXT_KEPT])
        feed = (e.get("feed") or {}).get("title", "")
        title_words = set(words(e.get("title", "")))
        all_words = title_words | set(words(f"{text} {feed} {clean_author(e.get('author', ''))}"))
        return _Indexed(slim, title_words, all_words)

    async def refresh(self, mf):
        async with self.lock:
            now = time.time()
            if not self.loaded_at or now - self.loaded_at > FULL_RELOAD_SECONDS:
                fresh, offset = {}, 0
                while True:
                    page = await mf.entries(status="read", order="changed_at", direction="desc",
                                            limit=PAGE, offset=offset)
                    for e in page["entries"] or []:
                        fresh[e["id"]] = self._index(e)
                    offset += PAGE
                    if offset >= page["total"]:
                        break
                self.entries, self.loaded_at = fresh, now
            else:
                # Only what changed since the last sync (a minute of overlap for clocks)
                since = int(self.synced_at) - 60
                archived = await mf.entries(status="read", changed_after=since, limit=PAGE)
                for e in archived["entries"] or []:
                    self.entries[e["id"]] = self._index(e)
                unarchived = await mf.entries(status="unread", changed_after=since, limit=PAGE)
                for e in unarchived["entries"] or []:
                    self.entries.pop(e["id"], None)
            self.synced_at = now

    def search(self, query: str) -> list[dict]:
        """Matching entries: title matches first, then the rest, each most recently archived first."""
        t = terms(query)
        hits = [i for i in self.entries.values() if matches(t, i.all_words)]
        hits.sort(key=lambda i: parse_time(i.entry["changed_at"]), reverse=True)
        hits.sort(key=lambda i: not matches(t, i.title_words))
        return [i.entry for i in hits]
