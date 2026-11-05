from PIL import Image
import io

from app import config
from app.poller import Poller, shrink
from app.scraper import ScrapeError

from .conftest import FakeScraper, jpeg, make_post


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def poller(db, scraper, accounts=("alice", "bob"), clock=None):
    for name in accounts:
        db.add_account(name)
    return Poller(db, scraper, clock=clock or Clock())


def test_shrink_makes_a_small_jpeg():
    out = shrink(jpeg(2000, 2500))
    with Image.open(io.BytesIO(out)) as im:
        assert im.format == "JPEG" and im.width == config.IMAGE_WIDTH


def test_checks_accounts_in_turn_and_stores_posts_and_images(db):
    posts = [make_post("A1", images=["u0", "u1", "u2", "u3", "u4"]), make_post("A2", published=1_760_000_100)]
    s = FakeScraper({"alice": ("Alice", posts)})
    clock = Clock()
    p = poller(db, s, clock=clock)
    wait = p.step()
    assert s.fetches == [("alice", config.POSTS_PER_CHECK)]
    lo, hi = config.ACCOUNT_GAP_SECONDS
    assert lo <= wait <= hi
    assert [r["shortcode"] for r in db.posts("alice", 10)] == ["A2", "A1"]
    # At most IMAGES_PER_POST images per post
    assert len(db.images("A1")) == config.IMAGES_PER_POST and s.downloads[:3] == ["u0", "u1", "u2"]
    a = db.account("alice")
    assert a["full_name"] == "Alice" and a["last_success"] == a["last_check"] == int(clock.t)
    p.step()
    assert s.fetches[-1][0] == "bob"
    # Both checked: nothing until POLL_HOURS later
    s.fetches.clear()
    assert p.step() > 0 and s.fetches == []
    clock.t += config.POLL_HOURS * 3600 + 1
    p.step()
    assert s.fetches == [("alice", config.POSTS_PER_CHECK)]


def test_logged_out_reads_fewer_posts(db):
    s = FakeScraper(logged_in=False)
    poller(db, s).step()
    assert s.fetches == [("alice", config.POSTS_PER_CHECK_LOGGED_OUT)]


def test_images_are_not_downloaded_twice_and_missing_ones_are_retried(db):
    post = make_post("A1", images=["good", "bad"])
    s = FakeScraper({"alice": ("", [post])})
    s.broken_urls = {"bad"}
    p = poller(db, s, accounts=["alice"])
    p.check("alice")
    assert len(db.images("A1")) == 1 and db.account("alice")["last_error"] == ""
    s.downloads.clear()
    s.broken_urls = set()
    p.check("alice")
    assert s.downloads == ["bad"] and len(db.images("A1")) == 2


def test_caption_edits_are_picked_up(db):
    s = FakeScraper({"alice": ("", [make_post("A1", caption="old")])})
    p = poller(db, s, accounts=["alice"])
    p.check("alice")
    s.results["alice"] = ("", [make_post("A1", caption="new")])
    p.check("alice")
    assert db.posts("alice", 5)[0]["caption"] == "new"


def test_account_failure_waits_for_next_round_and_others_go_on(db):
    clock = Clock()
    s = FakeScraper({"alice": ScrapeError("Account not found")})
    p = poller(db, s, clock=clock)
    p.step()
    a = db.account("alice")
    assert a["last_error"] == "Account not found" and a["last_check"] == int(clock.t)
    p.step()
    assert s.fetches[-1][0] == "bob"
    # The error clears on the next good check
    clock.t += config.POLL_HOURS * 3600 + 1
    s.results["alice"] = ("", [])
    p.step()
    assert db.account("alice")["last_error"] == ""


def test_login_or_refusal_stops_the_round(db):
    clock = Clock()
    s = FakeScraper({"alice": ScrapeError("Instagram rejected the login. Make a new session file.", pause_all=True)})
    p = poller(db, s, clock=clock)
    p.step()
    assert float(db.get_state("paused_until")) == int(clock.t + config.POLL_HOURS * 3600)
    assert db.get_state("pause_reason").startswith("Instagram rejected")
    s.fetches.clear()
    assert p.step() > 0 and s.fetches == []
    # Next round: bob (never checked) goes first, then alice
    clock.t += config.POLL_HOURS * 3600 + 1
    s.results["alice"] = ("", [])
    p.step()
    p.step()
    assert [f[0] for f in s.fetches] == ["bob", "alice"]
    assert db.get_state("pause_reason") == ""


def test_new_session_file_ends_the_wait(db):
    clock = Clock()
    s = FakeScraper({"alice": ScrapeError("Instagram rejected the login", pause_all=True)})
    p = poller(db, s, clock=clock)
    p.step()
    s.session_loads += 1
    s.fetches.clear()
    p.step()
    # alice was just checked, so bob is next
    assert s.fetches == [("bob", config.POSTS_PER_CHECK)]


def test_setup_problem_stops_checks(db):
    s = FakeScraper(problem="No session file yet. See the README to make one.")
    p = poller(db, s)
    assert p.step() == 60 and s.fetches == []
    assert db.get_state("problem").startswith("No session file")
    s._problem = ""
    p.step()
    assert db.get_state("problem") == "" and s.fetches


def test_unexpected_error_is_recorded_without_its_text(db):
    s = FakeScraper({"alice": KeyError("https://x/?token=secret")})
    p = poller(db, s)
    p.step()
    assert db.account("alice")["last_error"] == "Unexpected error (KeyError)"


def test_prune_keeps_newest_posts_and_their_images(db, monkeypatch):
    monkeypatch.setattr(config, "KEEP_POSTS", 2)
    posts = [make_post(f"P{i}", published=1_700_000_000 + i) for i in range(4)]
    s = FakeScraper({"alice": ("", posts)})
    poller(db, s, accounts=["alice"]).check("alice")
    assert [r["shortcode"] for r in db.posts("alice", 10)] == ["P3", "P2"]
    assert db.images("P0") == [] and db.images("P3")


def test_added_accounts_are_checked_first_and_removed_ones_stop(db):
    s = FakeScraper({"alice": ("", [make_post("A1")])})
    p = poller(db, s, accounts=["alice"])
    p.step()
    db.add_account("carol")
    p.step()
    assert [f[0] for f in s.fetches] == ["alice", "carol"]
    db.remove_account("alice")
    db.remove_account("carol")
    assert db.posts("alice", 5) == [] and db.images("A1") == []
    s.fetches.clear()
    assert p.step() == 60 and s.fetches == []


def test_account_removed_during_a_check_is_not_stored(db):
    class Removing(FakeScraper):
        def fetch(self, username, limit, newest=None):
            db.remove_account(username)
            return "", [make_post("A1")]
    p = poller(db, Removing(), accounts=["alice"])
    p.check("alice")
    assert db.account("alice") is None and db.posts("alice", 5) == []


# Previews

def test_preview_goes_before_due_accounts_and_isnt_followed(db):
    s = FakeScraper({"newband": ("New Band", [make_post("N1")])})
    clock = Clock()
    p = poller(db, s, accounts=["alice"], clock=clock)
    db.request_preview("newband", int(clock.t))
    p.step()
    assert s.fetches == [("newband", config.POSTS_PER_CHECK)]
    a = db.account("newband")
    assert a["preview_requested"] is None and a["following"] == 0 and a["full_name"] == "New Band"
    assert [r["shortcode"] for r in db.posts("newband", 5)] == ["N1"] and db.images("N1")
    p.step()
    assert s.fetches[-1][0] == "alice"
    # Not checked on the schedule
    s.fetches.clear()
    clock.t += config.POLL_HOURS * 3600 + 1
    p.step()
    assert s.fetches == [("alice", config.POSTS_PER_CHECK)]


def test_following_after_a_preview_needs_no_new_check(db):
    s = FakeScraper()
    clock = Clock()
    p = poller(db, s, accounts=[], clock=clock)
    db.request_preview("newband", int(clock.t))
    p.step()
    db.add_account("newband")
    s.fetches.clear()
    clock.t += 3600
    p.step()
    assert s.fetches == []
    clock.t += config.POLL_HOURS * 3600
    p.step()
    assert s.fetches == [("newband", config.POSTS_PER_CHECK)]


def test_preview_while_waiting_is_answered_without_asking_instagram(db):
    clock = Clock()
    s = FakeScraper()
    p = poller(db, s, accounts=[], clock=clock)
    db.set_state("paused_until", int(clock.t + 3600))
    db.set_state("pause_reason", "Instagram rejected the login")
    db.request_preview("newband", int(clock.t))
    p.step()
    a = db.account("newband")
    assert s.fetches == [] and a["preview_requested"] is None
    assert a["last_error"] == "Not read: ig2rss is waiting until the next round: Instagram rejected the login"


def test_preview_without_a_session_file_is_answered_without_asking_instagram(db):
    s = FakeScraper(problem="No session file yet. See the README to make one.")
    p = poller(db, s, accounts=[])
    db.request_preview("newband", 1)
    p.step()
    assert s.fetches == [] and db.account("newband")["last_error"].startswith("Not read: No session file")


def test_failed_preview_keeps_the_error(db):
    s = FakeScraper({"nobody": ScrapeError("Account not found")})
    p = poller(db, s, accounts=[])
    db.request_preview("nobody", 1)
    p.step()
    assert db.account("nobody")["last_error"] == "Account not found"
    assert db.get_state("paused_until", "0") == "0"


def test_old_previews_are_deleted_but_followed_accounts_stay(db):
    clock = Clock()
    s = FakeScraper({"newband": ("", [make_post("N1")]), "alice": ("", [make_post("A1")])})
    p = poller(db, s, accounts=["alice"], clock=clock)
    db.request_preview("newband", int(clock.t))
    p.step()
    p.step()
    clock.t += config.PREVIEW_KEEP_HOURS * 3600 + 1
    p.step()
    assert db.account("newband") is None and db.images("N1") == []
    assert db.account("alice") and db.images("A1")


def test_checks_pass_the_newest_saved_date(db):
    s = FakeScraper({"alice": ("", [make_post("A1", published=100), make_post("A2", published=200)])})
    clock = Clock()
    p = poller(db, s, accounts=["alice"], clock=clock)
    p.step()
    clock.t += config.POLL_HOURS * 3600 + 1
    p.step()
    assert s.newest == [None, 200]
