import os

import instaloader
import pytest
from instaloader.exceptions import (
    AbortDownloadException,
    ConnectionException,
    LoginRequiredException,
    PrivateProfileNotFollowedException,
    ProfileNotExistsException,
    TooManyRequestsException,
)

from app import scraper as scraper_mod
from app.scraper import ScrapeError, Scraper, classify, to_post

from .conftest import FakeIGPost, FakeNode


class FakeLoader:
    def __init__(self):
        self.context = object()
        self.loaded = []
        self.fail_load = False

    def load_session_from_file(self, username, filename):
        if self.fail_load:
            raise ValueError("bad pickle")
        self.loaded.append((username, filename))

    def close(self):
        pass


class FakeProfile:
    def __init__(self, posts, reels=(), full_name="Alice A", error_after=None):
        self.full_name = full_name
        self._posts = posts
        self._reels = list(reels)
        self.error_after = error_after
        self.taken = 0

    def get_posts(self):
        for p in self._posts:
            if self.error_after is not None and self.taken >= self.error_after:
                raise TooManyRequestsException("429 at https://www.instagram.com/graphql?secret=1")
            self.taken += 1
            yield p

    def get_reels(self):
        yield from self._reels


@pytest.fixture
def loader():
    return FakeLoader()


def use_profile(monkeypatch, profile):
    monkeypatch.setattr(instaloader.Profile, "from_username", staticmethod(lambda ctx, name: profile))


def test_to_post_single_sidecar_and_video():
    p = to_post(FakeIGPost("A1", 1_700_000_000, caption=None))
    assert (p.shortcode, p.caption, p.published, p.images, p.media_count) == (
        "A1", "", 1_700_000_000, ["https://cdn.example/A1.jpg"], 1)
    side = to_post(FakeIGPost("S1", 1_700_000_000, typename="GraphSidecar",
                              nodes=[FakeNode("u1"), FakeNode("u2", is_video=True), FakeNode("u3")]))
    assert side.images == ["u1", "u2", "u3"] and side.media_count == 3 and not side.is_video
    video = to_post(FakeIGPost("V1", 1_700_000_000, typename="GraphVideo", is_video=True))
    assert video.is_video and video.images == ["https://cdn.example/V1.jpg"]


def test_fetch_reads_a_bounded_number_newest_first(monkeypatch, loader):
    # A pinned old post first, then newer ones
    posts = [FakeIGPost("OLD", 1_500_000_000)] + [FakeIGPost(f"P{i}", 1_700_000_000 + i) for i in range(20)]
    profile = FakeProfile(posts)
    use_profile(monkeypatch, profile)
    s = Scraper(loader_factory=lambda: loader)
    full_name, got = s.fetch("alice", 5)
    assert profile.taken == 5
    assert full_name == "Alice A"
    assert [p.shortcode for p in got] == ["P3", "P2", "P1", "P0", "OLD"]


def test_reels_are_read_only_when_on_and_deduplicated(monkeypatch, loader):
    posts = [FakeIGPost("P1", 1_700_000_001)]
    reels = [FakeIGPost("P1", 1_700_000_001, is_video=True), FakeIGPost("R1", 1_700_000_005, is_video=True)]
    use_profile(monkeypatch, FakeProfile(posts, reels))
    assert [p.shortcode for p in Scraper(loader_factory=lambda: loader).fetch("a", 12)[1]] == ["P1"]
    assert [p.shortcode for p in Scraper(reels=True, loader_factory=lambda: loader).fetch("a", 12)[1]] == ["R1", "P1"]


def test_failure_hides_instaloader_text(monkeypatch, loader):
    posts = [FakeIGPost(f"P{i}", 1_700_000_000 + i) for i in range(5)]
    use_profile(monkeypatch, FakeProfile(posts, error_after=2))
    with pytest.raises(ScrapeError) as info:
        Scraper(loader_factory=lambda: loader).fetch("alice", 12)
    err = info.value
    assert err.pause_all
    assert "secret" not in err.message and "instagram.com" not in err.message
    assert err.__cause__ is None and err.__suppress_context__


@pytest.mark.parametrize("exc, logged_in, pause_all", [
    (ProfileNotExistsException("x"), True, False),
    (PrivateProfileNotFollowedException("x"), True, False),
    (LoginRequiredException("x"), False, False),
    (LoginRequiredException("x"), True, True),
    (TooManyRequestsException("x"), False, True),
    (AbortDownloadException('Query to https://www.instagram.com/api?x=1 responded with "401 Unauthorized"'), True, True),
    (AbortDownloadException("Redirected to login page. You've been logged out"), True, True),
    (AbortDownloadException('Query to https://www.instagram.com/x responded with "400": {"message": "challenge_required"}'),
     True, True),
    (ConnectionException("timeout https://x/?sig=abc"), True, False),
])
def test_classify(exc, logged_in, pause_all):
    err = classify(exc, logged_in)
    assert err.pause_all is pause_all
    assert "https://" not in err.message and "sig=" not in err.message


def test_unexpected_errors_are_not_caught_here(monkeypatch, loader):
    class Weird(FakeProfile):
        def get_posts(self):
            raise KeyError("data")
            yield
    use_profile(monkeypatch, Weird([]))
    with pytest.raises(KeyError):
        Scraper(loader_factory=lambda: loader).fetch("alice", 12)


def test_session_file_missing_then_loaded_then_replaced(tmp_path, loader):
    path = tmp_path / "session"
    s = Scraper(login="me", session_file=path, loader_factory=lambda: loader)
    assert "No session file" in s.problem()
    path.write_bytes(b"x")
    assert s.problem() == "" and loader.loaded == [("me", str(path))] and s.session_loads == 1
    # No reload while the file is unchanged
    assert s.problem() == "" and len(loader.loaded) == 1
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 10))
    assert s.problem() == "" and len(loader.loaded) == 2 and s.session_loads == 2


def test_unreadable_session_file_is_reported_once(tmp_path, loader):
    path = tmp_path / "session"
    path.write_bytes(b"junk")
    loader.fail_load = True
    s = Scraper(login="me", session_file=path, loader_factory=lambda: loader)
    assert "couldn't be read" in s.problem()
    loader.fail_load = False
    # Same file: not tried again until it changes
    assert "couldn't be read" in s.problem() and loader.loaded == []


def test_no_login_means_no_session_needed(tmp_path, loader):
    s = Scraper(login="", session_file=tmp_path / "missing", loader_factory=lambda: loader)
    assert s.problem() == "" and not s.logged_in


def test_real_loader_settings():
    # Builds a real Instaloader without network access
    loader = scraper_mod.make_loader()
    try:
        assert loader.context.quiet and loader.context.sleep
        # Instaloader's own retries and waits stay on
        assert loader.context.max_connection_attempts == 3 and loader.context.fatal_status_codes == []
        assert loader.context.iphone_support is False
        assert not loader.download_pictures and not loader.save_metadata
    finally:
        loader.close()


def test_challenge_and_feedback_have_their_own_messages():
    challenge = classify(AbortDownloadException('responded with "400 Bad Request" {"message":"checkpoint_required"}'), True)
    assert challenge.message.startswith("Instagram wants to confirm it's you")
    feedback = classify(AbortDownloadException('responded with "400 Bad Request" {"message":"feedback_required"}'), True)
    assert feedback.message == "Instagram is blocking requests from this account for now"


# Reading only new posts (Instaloader's --latest-stamps rule)

from app import config  # noqa: E402
from app.scraper import new_posts  # noqa: E402

from .conftest import make_post  # noqa: E402

NEWEST = 1_700_000_000


class Counted:
    """A newest-first stream that counts how many posts were taken (each is a request when logged out)."""

    def __init__(self, dates):
        self.posts = [make_post(f"P{i}", published=d) for i, d in enumerate(dates)]
        self.taken = 0

    def __iter__(self):
        for p in self.posts:
            self.taken += 1
            yield p


def read(dates, newest=NEWEST, first_limit=6):
    stream = Counted(dates)
    got = [p.published for p in new_posts(iter(stream), newest, first_limit)]
    return got, stream.taken


OLD, NEW = NEWEST - 100, NEWEST + 100


def test_first_read_takes_a_fixed_number():
    assert read([NEW] * 20, newest=None) == ([NEW] * 6, 6)


def test_nothing_new_stops_after_the_possibly_pinned_posts():
    assert read([OLD - 9999, OLD - 999, OLD, OLD, OLD]) == ([], 4)


def test_new_posts_below_three_pinned_ones_are_read():
    got, taken = read([OLD - 9999, OLD - 999, OLD - 99, NEW + 2, NEW + 1, OLD, OLD])
    assert got == [NEW + 2, NEW + 1] and taken == 6


def test_new_posts_without_pins():
    assert read([NEW + 2, NEW + 1, OLD, OLD, OLD]) == ([NEW + 2, NEW + 1], 4)


def test_a_long_gap_reads_everything_new_up_to_the_cap():
    got, taken = read([NEW + i for i in range(40, 0, -1)])
    assert len(got) == config.MAX_POSTS_PER_CHECK and taken == config.MAX_POSTS_PER_CHECK


def test_more_than_three_pinned_posts_hide_new_ones():
    # The known limit of this rule, same as in Instaloader
    assert read([OLD, OLD, OLD, OLD, NEW]) == ([], 4)


def test_fetch_passes_newest_through(monkeypatch, loader):
    posts = [FakeIGPost("PIN", 1_500_000_000), FakeIGPost("N1", NEW), FakeIGPost("O1", OLD),
             FakeIGPost("O2", OLD - 1), FakeIGPost("O3", OLD - 2), FakeIGPost("O4", OLD - 3)]
    profile = FakeProfile(posts)
    use_profile(monkeypatch, profile)
    _, got = Scraper(loader_factory=lambda: loader).fetch("alice", 6, newest=NEWEST)
    assert [p.shortcode for p in got] == ["N1"] and profile.taken == 4
