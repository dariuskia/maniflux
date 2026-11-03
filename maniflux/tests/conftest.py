"""A fake Miniflux that answers with the same JSON shapes as Miniflux 2.3.4."""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

os.environ["MANIFLUX_NO_POLL"] = "1"


def make_feed(feed_id=1, title="Example Feed", category=(1, "All"), feed_url="https://example.com/feed"):
    return {
        "id": feed_id, "user_id": 1, "feed_url": feed_url, "site_url": "https://example.com/",
        "title": title, "description": "", "disabled": False, "crawler": False,
        "category": {"id": category[0], "title": category[1], "user_id": 1, "hide_globally": False},
        "icon": {"feed_id": feed_id, "icon_id": 0, "external_icon_id": ""},
    }


def make_entry(entry_id, title="A title", url=None, feed=None, hours_old=1, content="<p>Some text.</p>",
               reading_time=5, author="Jane Doe", enclosures=None, status="unread"):
    feed = feed or make_feed()
    published = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": entry_id, "user_id": 1, "feed_id": feed["id"], "status": status, "hash": f"h{entry_id}",
        "title": title, "url": url or f"https://example.com/posts/{entry_id}", "comments_url": "",
        "language": "", "published_at": published, "created_at": published, "changed_at": published,
        "content": content, "author": author, "share_code": "", "starred": False,
        "reading_time": reading_time, "enclosures": enclosures or [], "feed": feed, "tags": [],
    }


class FakeMiniflux:
    def __init__(self, entries, feeds=None):
        self.entries = {e["id"]: e for e in entries}
        self.feeds = feeds or [make_feed()]
        self.calls = []
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.url.params, request.content))
        if self.down:
            raise httpx.ConnectError("refused")
        if request.headers.get("X-Auth-Token") != "test-key":
            return httpx.Response(401, json={"error_message": "Access Unauthorized"})
        path, m = request.url.path, request.method
        if m == "GET" and path == "/v1/entries":
            p = request.url.params
            status, search = p.get("status"), p.get("search", "").lower()
            limit, offset = int(p.get("limit", 100)), int(p.get("offset", 0))
            found = [e for e in self.entries.values() if not status or e["status"] == status]
            if search:
                found = [e for e in found if all(w in (e["title"] + " " + e["content"]).lower() for w in search.split())]
            order = p.get("order", "published_at")
            found.sort(key=lambda e: e[order], reverse=p.get("direction", "desc") == "desc")
            return httpx.Response(200, json={"total": len(found), "entries": found[offset:offset + limit]})
        if m == "PUT" and path == "/v1/entries":
            body = json.loads(request.content)
            for i in body["entry_ids"]:
                self.entries[i]["status"] = body["status"]
            return httpx.Response(204)
        if mm := re.fullmatch(r"/v1/entries/(\d+)/fetch-content", path):
            e = self.entries[int(mm.group(1))]
            e["content"] = "<p>The full article.</p>"
            return httpx.Response(200, json={"content": e["content"], "reading_time": 9})
        if mm := re.fullmatch(r"/v1/entries/(\d+)", path):
            e = self.entries.get(int(mm.group(1)))
            return httpx.Response(200, json=e) if e else httpx.Response(404, json={"error_message": "Not found"})
        if path == "/v1/feeds":
            return httpx.Response(200, json=self.feeds)
        if path == "/v1/version":
            return httpx.Response(200, json={"version": "2.3.4", "commit": "x"})
        return httpx.Response(404, json={"error_message": "no route"})


class FakeOpenRouter:
    def __init__(self):
        self.prompts = []
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        self.prompts.append(prompt)
        if self.fail:
            return httpx.Response(429, json={"error": {"message": "Rate limited"}})
        answer = {"description": "A short summary."}
        if '"type":' in prompt:
            answer["type"] = "Essay"
        return httpx.Response(200, json={"choices": [{"message": {"content": "Here: " + json.dumps(answer)}}]})


@pytest.fixture
def ai():
    return FakeOpenRouter()


@pytest.fixture
def fake():
    return FakeMiniflux([
        make_entry(1, "Fresh post", hours_old=1),
        make_entry(2, "Old post", hours_old=24 * 6),
        make_entry(3, "Housing is too expensive", hours_old=30, content="<p>About housing costs.</p>"),
    ])


@pytest.fixture
def client(fake, ai, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config, main
    from app.ai import Summarizer
    from app.db import DB
    from app.miniflux import Miniflux
    from app.vault import VaultIndex

    content = tmp_path / "content"
    content.mkdir()
    monkeypatch.setattr(config, "CONTENT_DIR", content)
    main.app.state.mf = Miniflux("http://miniflux.test/v1", "test-key", transport=httpx.MockTransport(fake.handler))
    main.app.state.db = DB(tmp_path / "test.db")
    main.app.state.vault = VaultIndex(content)
    main.app.state.ai = Summarizer("or-key", "test/model", transport=httpx.MockTransport(ai.handler))
    with TestClient(main.app) as c:
        c.content_dir = content
        yield c
    for k in ("mf", "db", "vault", "ai"):
        delattr(main.app.state, k)
