import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app import digests
from app.main import run_digest

TZ = ZoneInfo("America/Los_Angeles")


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def sched(**kw):
    return {"frequency": "weekly", "weekday": 0, "monthday": 1, "at": "08:00", **kw}


def test_next_run():
    wed = at("2026-11-04T10:00")
    assert digests.next_run(sched(frequency="daily"), wed) == at("2026-11-05T08:00")
    assert digests.next_run(sched(frequency="daily", at="18:30"), wed) == at("2026-11-04T18:30")
    assert digests.next_run(sched(weekday=0), wed) == at("2026-11-09T08:00")          # next Monday
    assert digests.next_run(sched(weekday=2, at="11:00"), wed) == at("2026-11-04T11:00")  # later today
    # every other week: skip a week when the last one was recent
    assert digests.next_run(sched(frequency="biweekly"), wed, last_run=at("2026-11-02T08:00")) == at("2026-11-16T08:00")
    assert digests.next_run(sched(frequency="monthly", monthday=1), wed) == at("2026-12-01T08:00")
    assert digests.next_run(sched(frequency="monthly", monthday=1), at("2026-12-20T09:00")) == at("2027-01-01T08:00")


def test_render_links_citations_and_escapes_html():
    items = [{"n": 1, "title": "EP", "url": "https://ex.com/ep", "feed": "Label", "date": "2026-11-01"}]
    out = digests.render("## New\n- An EP <script>x</script> [1] and [7]", items)
    assert '<a class="cite" href="https://ex.com/ep"' in out and "[7]" not in out
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<h2>New</h2>" in out


def make_digest(client, groups=None, **extra):
    db = client.app.state.db
    gid = db.add_group("Music")
    db.toggle_member(1, gid)
    data = {"name": "Music this week", "instructions": "New EPs and shows in Oakland.", "frequency": "weekly",
            "weekday": "0", "at": "08:00", "active": "1", "group": str(gid), **extra}
    r = client.post("/digests", data=data, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_digest_runs_and_shows_up(client, fake, ai):
    did = make_digest(client)
    run_id = asyncio.run(run_digest(client.app, did))
    r = client.app.state.db.run(run_id)
    assert r["status"] == "done" and r["item_count"] == 3   # the three entries from feed 1, read or not
    assert "New EPs and shows in Oakland." in ai.prompts[-1]
    assert "[1]" in ai.prompts[-1] and "Fresh post" in ai.prompts[-1]

    page = client.get(f"/digests/runs/{run_id}").text
    assert "A new EP is out" in page and 'class="cite"' in page and "3 items read" in page
    feed = client.get("/digests/feed.xml")
    assert feed.headers["content-type"].startswith("application/atom+xml")
    assert f"tag:maniflux,digest-run-{run_id}" in feed.text and "Music this week" in feed.text
    assert "Music this week" in client.get("/digests").text

    # the next run only covers what's new since: nothing
    run2 = asyncio.run(run_digest(client.app, did))
    assert client.app.state.db.run(run2)["status"] == "empty"
    assert feed.text.count("<entry>") == 1


def test_long_input_is_picked_in_batches(client, fake, ai, monkeypatch):
    monkeypatch.setattr(digests, "ONE_CALL", 10)
    monkeypatch.setattr(digests, "BATCH", 10)
    did = make_digest(client)
    asyncio.run(run_digest(client.app, did))
    assert sum(p.startswith("You are helping write a digest") for p in ai.prompts) == 3
    assert "picked detail [1]" in ai.prompts[-1]


def test_failure_is_shown(client, fake, ai):
    did = make_digest(client)
    ai.fail = True
    run_id = asyncio.run(run_digest(client.app, did))
    r = client.app.state.db.run(run_id)
    assert r["status"] == "error" and "Rate limited" in r["error"]
    assert "Rate limited" in client.get(f"/digests/{did}").text


def test_form_checks_and_subscribe(client, fake):
    r = client.post("/digests", data={"name": "x", "instructions": "y", "frequency": "weekly"})
    assert "Pick at least one group" in r.text
    assert "Show digests in my list" in client.get("/digests").text
    client.post("/digests/subscribe")
    assert any(f["feed_url"].endswith("/digests/feed.xml") for f in fake.feeds)
    assert "Show digests in my list" not in client.get("/digests").text


def test_deleting_a_group_takes_it_out_of_digests(client):
    did = make_digest(client)
    gid = next(iter(client.app.state.db.groups()))
    client.post(f"/groups/{gid}/delete")
    assert client.app.state.db.digest(did)["group_ids"] == set()
