"""Scores without an LLM: freshness × feed and group weights, adjusted by rules."""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from . import kinds
from . import search as searching
from .text import clean_author, excerpt, parse_time, quick_text

HALF_LIFE_DAYS = 3
BOOST = 2.0
LOWER = 0.5


# Tabs on the list: which item types each one shows
SHOW = {
    "read": {"article", "paper", "thread", "book", "other"},
    "watch": {"video"},
    "listen": {"podcast", "music"},
}


@dataclass
class Steering:
    """Everything that steers the ranking, from Maniflux's database."""
    feed_weights: dict[int, float] = field(default_factory=dict)
    groups: dict[int, dict] = field(default_factory=dict)        # id -> {name, weight}
    memberships: dict[int, set[int]] = field(default_factory=dict)  # feed id -> group ids
    rules: list[dict] = field(default_factory=list)


@dataclass
class Item:
    entry: dict
    type: str
    minutes: int
    text: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    hidden: str | None = None
    group_ids: set[int] = field(default_factory=set)

    @property
    def id(self) -> int:
        return self.entry["id"]

    @property
    def excerpt(self) -> str:
        return excerpt(self.text)

    @property
    def reason(self) -> str:
        return " · ".join(self.reasons)


def rule_is_live(rule: dict, today: date) -> bool:
    if not rule["active"]:
        return False
    return not rule["ends_on"] or today.isoformat() <= rule["ends_on"]


def _age(published: datetime, now: datetime) -> tuple[float, str]:
    days = max(0.0, (now - published).total_seconds() / 86400)
    if days < 1:
        hours = int(days * 24)
        label = "just now" if hours == 0 else f"{hours}h old"
    else:
        label = f"{int(days)} day{'s' if int(days) != 1 else ''} old"
    return days, label


def _fmt(w: float) -> str:
    return f"×{w:g}"


def _plural(t: str) -> str:
    return t if t.endswith("s") else t + "s"


def _keyword_re(word: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(word.strip()) + r"(?!\w)", re.I)


def rank(entries: list[dict], steer: Steering, now: datetime) -> list[Item]:
    """All entries as Items, best first. Items with .hidden set are left out of the list."""
    today = now.date()
    live = [r for r in steer.rules if rule_is_live(r, today)]
    keyword_rules = [(r, _keyword_re(r["value"])) for r in live if r["kind"] == "keyword" and r["value"].strip()]
    type_rules = [r for r in live if r["kind"] == "type"]
    limit_rules = [r for r in live if r["kind"] == "limit" and (r["amount"] or 0) > 0]

    items = []
    for e in entries:
        feed_id = e.get("feed_id") or (e.get("feed") or {}).get("id")
        it = Item(entry=e, type=kinds.display_type(e), minutes=e.get("reading_time") or 0,
                  text=quick_text(e.get("content", "")),
                  group_ids={g for g in steer.memberships.get(feed_id, set()) if g in steer.groups})
        days, age_label = _age(parse_time(e["published_at"]), now)
        score = 100 * 0.5 ** (days / HALF_LIFE_DAYS)
        reasons = [age_label]

        fw = steer.feed_weights.get(feed_id, 1.0)
        # In several groups: the highest group weight counts, so an extra group never pushes a feed down
        gw, top_group = 1.0, None
        if it.group_ids:
            top_group = max(it.group_ids, key=lambda g: steer.groups[g]["weight"])
            gw = steer.groups[top_group]["weight"]
        if fw == 0:
            it.hidden = "feed turned off"
        elif gw == 0:
            it.hidden = "group turned off"
        if fw != 1:
            reasons.append(f"feed {_fmt(fw)}")
        if gw != 1:
            reasons.append(f"{steer.groups[top_group]['name']} {_fmt(gw)}")
        score *= fw * gw

        haystack = f"{e.get('title', '')} {it.text}"
        for r, pattern in keyword_rules:
            if pattern.search(haystack):
                score, label = _apply(r, score, f"“{r['value'].strip()}”")
                if label == "hide":
                    it.hidden = it.hidden or f"hidden by rule “{r['value'].strip()}”"
                else:
                    reasons.append(label)
        for r in type_rules:
            if r["value"] == it.type:
                score, label = _apply(r, score, _plural(it.type))
                if label == "hide":
                    it.hidden = it.hidden or f"{_plural(it.type)} hidden by rule"
                else:
                    reasons.append(label)

        it.score, it.reasons = score, reasons
        items.append(it)

    items.sort(key=lambda i: i.score, reverse=True)

    # At most N a day for a type, feed or group, by publish day; the best-scored ones stay
    for r in limit_rules:
        kind, _, target = r["value"].partition(":")
        seen = defaultdict(int)
        for it in items:
            if it.hidden:
                continue
            if kind == "type":
                match = it.type == target
            elif kind == "group":
                match = target.isdigit() and int(target) in it.group_ids
            else:
                match = str(it.entry.get("feed_id")) == target
            if not match:
                continue
            day = parse_time(it.entry["published_at"]).astimezone(now.tzinfo).date()
            seen[day] += 1
            if seen[day] > r["amount"]:
                it.hidden = f"over the limit of {r['amount']} a day"

    return items


def _apply(rule: dict, score: float, what: str) -> tuple[float, str]:
    if rule["action"] == "hide":
        return score, "hide"
    if rule["action"] == "lower":
        return score * LOWER, f"{what} lowered"
    return score * BOOST, f"{what} boosted"


def search(items: list[Item], q: str) -> list[Item]:
    """Items where every search word starts a word of the title, text, feed or author."""
    t = searching.terms(q)
    if not t:
        return items

    def text_words(it: Item) -> set[str]:
        e = it.entry
        return set(searching.words(f"{e.get('title', '')} {it.text} {(e.get('feed') or {}).get('title', '')} "
                                   f"{clean_author(e.get('author', ''))}"))

    return [it for it in items if searching.matches(t, text_words(it))]


def shown_by(items: list[Item], show: str, group: int | None) -> list[Item]:
    """Items for a tab (all, read, watch, listen) and, optionally, one group."""
    out = items
    if show in SHOW:
        out = [i for i in out if i.type in SHOW[show]]
    if group is not None:
        out = [i for i in out if group in i.group_ids]
    return out


def budget_set(items: list[Item], minutes: int) -> tuple[list[Item], int]:
    """Best items that fit the time, in rank order. Returns the set and how many had no known length."""
    chosen, total, unknown = [], 0, 0
    for it in items:
        if not it.minutes:
            unknown += 1
            continue
        if total + it.minutes <= minutes:
            chosen.append(it)
            total += it.minutes
    return chosen, unknown
