from datetime import datetime, timedelta, timezone

from app import kinds
from app.ranking import Steering, budget_set, rank, shown_by

from .conftest import make_entry, make_feed

NOW = datetime.now(timezone.utc)


def rule(kind, value, action=None, amount=None, active=1, ends_on=None):
    return {"id": 1, "kind": kind, "value": value, "action": action, "amount": amount,
            "active": active, "ends_on": ends_on}


def test_newer_first():
    items = rank([make_entry(1, hours_old=100), make_entry(2, hours_old=1)], Steering(), NOW)
    assert [i.id for i in items] == [2, 1]


def test_feed_weight_reorders_and_zero_hides():
    a, b = make_feed(1, "A"), make_feed(2, "B")
    entries = [make_entry(1, feed=a, hours_old=1), make_entry(2, feed=b, hours_old=30)]
    assert rank(entries, Steering(feed_weights={2: 3.0}), NOW)[0].id == 2
    hidden = {i.id: i.hidden for i in rank(entries, Steering(feed_weights={1: 0.0}), NOW)}
    assert hidden[1] and not hidden[2]


def test_group_weights_take_the_highest():
    groups = {1: {"id": 1, "name": "News", "weight": 0.5}, 2: {"id": 2, "name": "Local", "weight": 2.0},
              3: {"id": 3, "name": "Off", "weight": 0.0}}
    a, b = make_feed(1, "A"), make_feed(2, "B")
    entries = [make_entry(1, feed=a, hours_old=1), make_entry(2, feed=b, hours_old=30)]
    steer = Steering(groups=groups, memberships={2: {1, 2}})
    items = rank(entries, steer, NOW)
    assert items[0].id == 2 and "Local ×2" in items[0].reason and items[0].group_ids == {1, 2}
    # turned off only when every group it's in is off
    assert rank(entries, Steering(groups=groups, memberships={1: {3}}), NOW)[1].hidden == "group turned off"
    assert not any(i.hidden for i in rank(entries, Steering(groups=groups, memberships={1: {3, 1}}), NOW))


def test_tabs_and_group_filter():
    entries = [make_entry(1), make_entry(2, url="https://youtu.be/x"),
               make_entry(3, enclosures=[{"mime_type": "audio/mpeg"}], feed=make_feed(2, "Pod"))]
    items = rank(entries, Steering(groups={5: {"id": 5, "name": "G", "weight": 1}}, memberships={2: {5}}), NOW)
    assert [i.id for i in shown_by(items, "watch", None)] == [2]
    assert [i.id for i in shown_by(items, "listen", None)] == [3]
    assert [i.id for i in shown_by(items, "read", None)] == [1]
    assert [i.id for i in shown_by(items, "all", 5)] == [3]


def test_keyword_rules():
    entries = [make_entry(1, "Cats", hours_old=1), make_entry(2, "Housing news", hours_old=40)]
    assert rank(entries, Steering(rules=[rule("keyword", "housing", "boost")]), NOW)[0].id == 2
    hidden = {i.id: i.hidden for i in rank(entries, Steering(rules=[rule("keyword", "HOUSING", "hide")]), NOW)}
    assert hidden[2] and not hidden[1]
    # whole words only
    assert not rank([make_entry(3, "Warehousing")], Steering(rules=[rule("keyword", "housing", "hide")]), NOW)[0].hidden


def test_expired_and_paused_rules_stop_applying():
    yesterday = (NOW - timedelta(days=1)).date().isoformat()
    e = [make_entry(1, "Housing")]
    assert not rank(e, Steering(rules=[rule("keyword", "housing", "hide", ends_on=yesterday)]), NOW)[0].hidden
    assert not rank(e, Steering(rules=[rule("keyword", "housing", "hide", active=0)]), NOW)[0].hidden
    today = NOW.date().isoformat()
    assert rank(e, Steering(rules=[rule("keyword", "housing", "hide", ends_on=today)]), NOW)[0].hidden


def test_type_rule_and_limit():
    vids = [make_entry(i, url=f"https://www.youtube.com/watch?v={i}", hours_old=i) for i in range(1, 6)]
    items = rank(vids, Steering(rules=[rule("type", "video", "lower")]), NOW)
    assert "videos lowered" in items[0].reason
    # all on one day, so the daily limit applies to all five whatever time the test runs
    noon = NOW.replace(hour=12, minute=0, second=0, microsecond=0)
    for v in vids:
        v["published_at"] = (noon - timedelta(hours=v["id"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = rank(vids, Steering(rules=[rule("limit", "type:video", amount=2)]), noon)
    assert sum(not i.hidden for i in items) == 2
    assert [i.id for i in items if not i.hidden] == [1, 2]


def test_known_type():
    assert kinds.known_type(make_entry(1, url="https://youtu.be/abc")) == "video"
    assert kinds.known_type(make_entry(1, url="https://arxiv.org/abs/1")) == "paper"
    assert kinds.known_type(make_entry(1, enclosures=[{"mime_type": "audio/mpeg"}])) == "podcast"
    # images attached to an article say nothing about the type
    assert kinds.known_type(make_entry(1, enclosures=[{"mime_type": "image/*"}])) is None
    assert kinds.known_type(make_entry(1, feed=make_feed(feed_url="https://x.substack.com/feed"))) == "article"
    # the category name no longer matters
    assert kinds.known_type(make_entry(1, feed=make_feed(category=(2, "Substack")))) is None
    assert kinds.known_type(make_entry(1, url="https://www.nytimes.com/x")) is None


def test_budget():
    entries = [make_entry(i, reading_time=m, hours_old=i) for i, m in [(1, 8), (2, 15), (3, 0), (4, 2)]]
    chosen, unknown = budget_set(rank(entries, Steering(), NOW), 10)
    assert [i.id for i in chosen] == [1, 4]
    assert sum(i.minutes for i in chosen) <= 10
    assert unknown == 1


def test_search_matches_word_starts():
    from app import search as s
    words = set(s.words("RIP Juan Atkins — José's techno, current affairs"))
    assert s.matches(s.terms("RIP J"), words)
    assert s.matches(s.terms("atk"), words)
    assert s.matches(s.terms("jose"), words)
    assert s.matches(s.terms("JUAN rip"), words)
    assert not s.matches(s.terms("rent"), words)   # not inside "current"
    assert not s.matches(s.terms("RIP X"), words)
