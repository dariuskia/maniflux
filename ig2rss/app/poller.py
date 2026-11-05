"""Checks one account at a time in the background. Feed requests never trigger a check."""

import io
import logging
import random
import threading
import time
from urllib.parse import urlsplit

from PIL import Image

from . import config
from .db import DB
from .scraper import Post, ScrapeError

log = logging.getLogger("ig2rss")


def shrink(data: bytes, width: int = config.IMAGE_WIDTH) -> bytes:
    """A smaller JPEG, small enough to put in the feed itself."""
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im.thumbnail((width, width * 3))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=72, optimize=True)
    return out.getvalue()


class Poller:
    """Each account is checked once every POLL_HOURS, one at a time. A failed check waits for the next round;
    Instaloader itself handles pacing and "too many requests" answers."""

    def __init__(self, db: DB, scraper, clock=time.time):
        self.db = db
        self.scraper = scraper
        self.clock = clock
        self._session_loads = scraper.session_loads
        # Set when a preview is asked for, so it doesn't wait for the current pause to end
        self.wake = threading.Event()

    def step(self) -> float:
        """Reads a waiting preview, or checks the next due account. Returns seconds to wait before the next step."""
        now = self.clock()
        self.db.remove_old_previews(int(now - config.PREVIEW_KEEP_HOURS * 3600))
        problem = self.scraper.problem()
        self.db.set_state("problem", problem)
        if problem:
            self._answer_previews(problem)
            return 60
        if self.scraper.session_loads != self._session_loads:
            # A new session file: no need to wait for the next round
            self._session_loads = self.scraper.session_loads
            self.db.set_state("paused_until", 0)
        paused_until = float(self.db.get_state("paused_until", "0") or 0)
        if paused_until > now:
            self._answer_previews(f"ig2rss is waiting until the next round: {self.db.get_state('pause_reason')}")
            return min(paused_until - now, 60)
        if preview := self.db.next_preview():
            self.check(preview["username"])
            self.db.update_account(preview["username"], preview_requested=None)
            return random.uniform(*config.ACCOUNT_GAP_SECONDS)
        account = self.db.next_due()
        if account is None:
            return 60
        due = (account["last_check"] or 0) + config.POLL_HOURS * 3600
        if due > now:
            return min(due - now, 60)
        self.check(account["username"])
        return random.uniform(*config.ACCOUNT_GAP_SECONDS)

    def _answer_previews(self, reason: str) -> None:
        """Waiting previews get the reason Instagram can't be asked now, instead of a request."""
        while preview := self.db.next_preview():
            self.db.update_account(preview["username"], preview_requested=None, last_error=f"Not read: {reason}")

    def check(self, username: str) -> None:
        now = int(self.clock())
        limit = config.POSTS_PER_CHECK if self.scraper.logged_in else config.POSTS_PER_CHECK_LOGGED_OUT
        try:
            full_name, posts = self.scraper.fetch(username, limit, self.db.newest_published(username))
        except ScrapeError as e:
            log.warning("@%s: %s", username, e.message)
            self.db.update_account(username, last_check=now, last_error=e.message)
            if e.pause_all:
                # Not this account's fault: the others wait for the next round too
                self.db.set_state("paused_until", int(now + config.POLL_HOURS * 3600))
                self.db.set_state("pause_reason", e.message)
            return
        except Exception as e:
            # Not Instaloader's own errors (those are ScrapeError), so likely a changed Instagram answer
            log.warning("@%s: unexpected %s", username, type(e).__name__)
            self.db.update_account(username, last_check=now, last_error=f"Unexpected error ({type(e).__name__})")
            return
        if not self.db.account(username):
            return  # removed on the status page during the check
        self.store(username, posts, now)
        self.db.prune(username, config.KEEP_POSTS)
        self.db.update_account(username, full_name=full_name, last_check=now, last_success=now, last_error="")
        self.db.set_state("pause_reason", "")
        log.info("@%s: %d posts read", username, len(posts))

    def store(self, username: str, posts: list[Post], now: int) -> None:
        for post in posts:
            self.db.upsert_post(username, post, now)
            have = self.db.image_positions(post.shortcode)
            # Images missing from an earlier try are tried again while the post is still near the top
            for position, url in enumerate(post.images[:config.IMAGES_PER_POST]):
                if position in have:
                    continue
                try:
                    self.db.add_image(post.shortcode, position, shrink(self.scraper.download(url)))
                except Exception as e:
                    # The server name only: image links carry access tokens
                    log.info("@%s: image %s of %s not saved (%s from %s)", username, position + 1, post.shortcode,
                             type(e).__name__, urlsplit(url).hostname)

    def run(self, stop: threading.Event) -> None:
        stop.wait(10)
        while not stop.is_set():
            try:
                wait = self.step()
            except Exception:
                log.exception("check loop error")
                wait = 300
            self.wake.wait(max(wait, 1))
            self.wake.clear()
