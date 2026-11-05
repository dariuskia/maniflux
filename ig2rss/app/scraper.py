"""The only place that talks to Instagram, through Instaloader.

One Instaloader object is kept for the life of the process: Instaloader paces its own requests and
waits out "too many requests" answers, and that only works if it remembers earlier requests. Error messages are written here in plain words and never include Instaloader's
text, which can contain request URLs.
"""

from dataclasses import dataclass
from datetime import timezone
from itertools import islice
from pathlib import Path

import instaloader
from instaloader.exceptions import (
    AbortDownloadException,
    InstaloaderException,
    LoginException,
    LoginRequiredException,
    PrivateProfileNotFollowedException,
    ProfileNotExistsException,
    QueryReturnedBadRequestException,
    QueryReturnedForbiddenException,
    TooManyRequestsException,
)

from . import config


@dataclass
class Post:
    shortcode: str
    caption: str
    published: int  # UTC epoch seconds
    images: list[str]  # picture or video thumbnail URLs, in order
    is_video: bool = False
    media_count: int = 1


class ScrapeError(Exception):
    """A failed check. pause_all: the problem isn't this account (login, Instagram refusing us), so the
    other accounts wait for the next round too."""

    def __init__(self, message: str, pause_all: bool = False):
        super().__init__(message)
        self.message = message
        self.pause_all = pause_all


def make_loader() -> instaloader.Instaloader:
    return instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        request_timeout=60,
        # Logged in, post lists already carry the images; this stops any extra per-post requests
        iphone_support=False,
    )


def to_post(p) -> Post:
    if p.typename == "GraphSidecar":
        images = [node.display_url for node in p.get_sidecar_nodes()]
        count = len(images)
    else:
        images = [p.url]
        count = 1
    # date_utc is a naive datetime in UTC
    published = int(p.date_utc.replace(tzinfo=timezone.utc).timestamp())
    return Post(p.shortcode, p.caption or "", published, [u for u in images if u], bool(p.is_video), count)


def new_posts(posts, newest: int | None, first_limit: int):
    """The posts newer than `newest`, from a newest-first stream, read the way Instaloader's --latest-stamps reads:
    an older post among the first POSSIBLY_PINNED (maybe pinned) is skipped, and the first older one after them
    stops the reading. Without `newest`, the first `first_limit` posts."""
    if newest is None:
        yield from islice(posts, first_limit)
        return
    for number, post in enumerate(islice(posts, config.MAX_POSTS_PER_CHECK), start=1):
        if post.published > newest:
            yield post
        elif number > config.POSSIBLY_PINNED:
            return


def classify(e: BaseException, logged_in: bool) -> ScrapeError:
    """Plain words for an Instaloader error. Account problems stop only that account; the rest stop the round."""
    if isinstance(e, ProfileNotExistsException):
        return ScrapeError("Account not found")
    if isinstance(e, PrivateProfileNotFollowedException):
        return ScrapeError("Private account that your Instagram account doesn't follow")
    if isinstance(e, LoginRequiredException) and not logged_in:
        return ScrapeError("Instagram needs a login to show this account")
    if isinstance(e, (LoginRequiredException, LoginException)):
        return ScrapeError("Instagram rejected the login. Make a new session file.", pause_all=True)
    if isinstance(e, AbortDownloadException):
        # Instaloader stops when Instagram sends it to the login page, or answers with a checkpoint,
        # challenge or feedback_required. Only these words are read from its message.
        text = str(e)
        if "checkpoint_required" in text or "challenge_required" in text:
            return ScrapeError("Instagram wants to confirm it's you. Log into the account in a browser or the app, "
                               "follow its steps, then make a new session file.", pause_all=True)
        if "feedback_required" in text:
            return ScrapeError("Instagram is blocking requests from this account for now", pause_all=True)
        return ScrapeError("Instagram asked for a login (session expired, or too many requests)", pause_all=True)
    if isinstance(e, TooManyRequestsException):
        return ScrapeError("Instagram is limiting requests, even after Instaloader waited", pause_all=True)
    if isinstance(e, (QueryReturnedForbiddenException, QueryReturnedBadRequestException)):
        return ScrapeError("Instagram refused the request", pause_all=True)
    return ScrapeError(f"Couldn't read from Instagram ({type(e).__name__})")


class Scraper:
    def __init__(self, login: str = "", session_file: Path = Path("/data/session"), reels: bool = False,
                 loader_factory=make_loader):
        self.login = login
        self.session_file = Path(session_file)
        self.reels = reels
        self._loader = loader_factory()
        self._loaded_mtime: float | None = None
        self._bad_mtime: float | None = None
        # Goes up each time a (new) session file is loaded
        self.session_loads = 0

    @property
    def logged_in(self) -> bool:
        return bool(self.login)

    def problem(self) -> str:
        """Why checks can't run yet, or ''. Only looks at the local session file; (re)loads it when it changes."""
        if not self.login:
            return ""
        try:
            mtime = self.session_file.stat().st_mtime
        except OSError:
            return "No session file yet. See the README to make one."
        if mtime == self._loaded_mtime:
            return ""
        if mtime == self._bad_mtime:
            return "The session file couldn't be read. Make a new one."
        try:
            # Only ever load a session file you made yourself: it's a Python pickle
            self._loader.load_session_from_file(self.login, str(self.session_file))
        except Exception:
            self._bad_mtime = mtime
            return "The session file couldn't be read. Make a new one."
        self._loaded_mtime = mtime
        self.session_loads += 1
        return ""

    def fetch(self, username: str, limit: int, newest: int | None = None) -> tuple[str, list[Post]]:
        """(full name, newest posts first). newest: date of the newest post saved before, or None for a first read
        of `limit` posts. Logged out, Instaloader makes one request per post read, so reading stops early."""
        found: dict[str, Post] = {}
        try:
            profile = instaloader.Profile.from_username(self._loader.context, username)
            streams = [new_posts((to_post(p) for p in profile.get_posts()), newest, limit)]
            if self.reels:
                streams.append(new_posts((to_post(p) for p in profile.get_reels()), newest, config.REELS_PER_CHECK))
            for stream in streams:
                for post in stream:
                    found.setdefault(post.shortcode, post)
            full_name = profile.full_name or ""
        except (InstaloaderException, AbortDownloadException) as e:
            raise classify(e, self.logged_in) from None
        return full_name, sorted(found.values(), key=lambda p: p.published, reverse=True)

    def download(self, url: str) -> bytes:
        """An image from Instagram's image servers (not its API)."""
        resp = self._loader.context.get_raw(url)
        try:
            data = resp.raw.read(config.MAX_IMAGE_BYTES + 1)
        finally:
            resp.close()
        if len(data) > config.MAX_IMAGE_BYTES:
            raise ValueError("image too large")
        return data

    def close(self):
        self._loader.close()
