"""Fakes for Instaloader and Instagram. Nothing here talks to Instagram."""

import io
import os
from datetime import datetime, timezone

import pytest
from PIL import Image

os.environ["IG2RSS_NO_POLL"] = "1"

from app.db import DB  # noqa: E402
from app.scraper import Post  # noqa: E402


def jpeg(width=1080, height=1350, color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG")
    return out.getvalue()


class FakeScraper:
    """Stands in for app.scraper.Scraper."""

    def __init__(self, results=None, logged_in=True, problem=""):
        self.results = results or {}  # username -> (full_name, posts) or a ScrapeError/Exception to raise
        self.logged_in = logged_in
        self._problem = problem
        self.session_loads = 0
        self.fetches = []
        self.newest = []  # the newest date passed with each fetch
        self.downloads = []
        self.broken_urls = set()

    def problem(self):
        return self._problem

    def fetch(self, username, limit, newest=None):
        self.fetches.append((username, limit))
        self.newest.append(newest)
        r = self.results.get(username, ("", []))
        if isinstance(r, Exception):
            raise r
        return r

    def download(self, url):
        self.downloads.append(url)
        if url in self.broken_urls:
            raise ConnectionError("nope")
        return jpeg()


def make_post(code="ABC123", caption="Hello", published=1_760_000_000, images=None, is_video=False, media_count=None):
    images = images if images is not None else [f"https://cdn.example/{code}/0.jpg"]
    return Post(code, caption, published, images, is_video, media_count or max(1, len(images)))


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "test.db")


# Instaloader stand-ins, with the attributes app.scraper reads

class FakeNode:
    def __init__(self, display_url, is_video=False):
        self.display_url = display_url
        self.is_video = is_video
        self.video_url = None


class FakeIGPost:
    def __init__(self, shortcode, ts, caption="cap", typename="GraphImage", nodes=None, is_video=False):
        self.shortcode = shortcode
        self.caption = caption
        self.typename = typename
        self.is_video = is_video
        self.url = f"https://cdn.example/{shortcode}.jpg"
        # Instaloader gives a naive datetime in UTC
        self.date_utc = datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
        self._nodes = nodes or []

    def get_sidecar_nodes(self):
        return iter(self._nodes)
