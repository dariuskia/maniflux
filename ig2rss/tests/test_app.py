import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.db import DB

from .conftest import make_post


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ig2rss.db")

    # No page or feed request may reach Instagram
    def no_scraping(*a, **k):
        raise AssertionError("tried to scrape")
    monkeypatch.setattr(main, "Scraper", no_scraping)
    with TestClient(main.app) as c:
        c.app.state.db.add_account("alice")
        c.app.state.db.add_account("bob")
        yield c


def test_username_from_what_was_typed():
    assert main.username_from(" Martha_Radio ") == "martha_radio"
    assert main.username_from("@some.venue") == "some.venue"
    assert main.username_from("https://www.instagram.com/label_name/?hl=en") == "label_name"
    assert main.username_from("instagram.com/another_one") == "another_one"
    assert main.username_from("bad-name!") is None and main.username_from("") is None


def test_add_and_remove_accounts_on_the_page(client):
    r = client.post("/accounts", data={"username": "https://www.instagram.com/Carol/"})
    assert r.status_code == 200 and "Added @carol" in r.text and "http://ig2rss:8091/feeds/carol.xml" in r.text
    assert client.get("/feeds/carol.xml").status_code == 200
    assert "already here" in client.post("/accounts", data={"username": "@carol"}).text
    assert "isn&#x27;t an Instagram username" in client.post("/accounts", data={"username": "<b>no</b>"}).text
    db = client.app.state.db
    db.upsert_post("carol", make_post("C1"), 1)
    r = client.post("/accounts/carol/remove")
    assert "Removed @carol" in r.text and "@carol</a>" not in r.text
    assert client.get("/feeds/carol.xml").status_code == 404
    assert db.posts("carol", 5) == []


def test_other_websites_cant_change_accounts(client):
    evil = {"Origin": "https://evil.example"}
    assert client.post("/accounts", data={"username": "mallory"}, headers=evil).status_code == 403
    assert client.post("/accounts/alice/remove", headers=evil).status_code == 403
    assert client.app.state.db.account("alice") and not client.app.state.db.account("mallory")
    same = {"Origin": "http://testserver"}
    assert client.post("/accounts", data={"username": "carol"}, headers=same).status_code == 200


def test_unknown_account_is_404(client):
    assert client.get("/feeds/mallory.xml").status_code == 404


def test_feed_before_first_check_is_empty_but_valid(client):
    r = client.get("/feeds/alice.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/rss+xml")
    assert b"<channel>" in r.content and b"<item>" not in r.content


def test_feed_serves_cache_and_supports_etag(client):
    db: DB = client.app.state.db
    db.upsert_post("alice", make_post("A1", caption="Gig <b>tonight</b>"), 1)
    db.update_account("alice", full_name="Alice")
    r = client.get("/feeds/ALICE.xml")
    assert b"https://www.instagram.com/p/A1/" in r.content and b"&lt;b&gt;" in r.content
    again = client.get("/feeds/alice.xml", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304


def test_status_page_shows_errors_escaped_and_feed_addresses(client):
    db: DB = client.app.state.db
    db.update_account("alice", last_error="<b>Account not found</b>")
    db.set_state("paused_until", 9_999_999_999)
    db.set_state("pause_reason", "Instagram is limiting requests (429)")
    r = client.get("/")
    assert r.status_code == 200
    assert "&lt;b&gt;Account not found" in r.text
    assert "http://ig2rss:8091/feeds/alice.xml" in r.text
    assert "Waiting until" in r.text and "limiting requests" in r.text
    assert str(config.SESSION_FILE) not in r.text


def test_no_api_docs(client):
    assert client.get("/docs").status_code == 404


# Previews

def finish_preview(db, name, posts, error=""):
    """What the checker does after reading a preview."""
    import time
    for post in posts:
        db.upsert_post(name, post, 1)
    db.update_account(name, preview_requested=None, last_check=int(time.time()), last_error=error)


def test_preview_then_follow(client):
    db = client.app.state.db
    r = client.post("/previews", data={"username": "@NewBand"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/preview/newband"
    page = client.get("/preview/newband").text
    assert "Reading @newband" in page and 'http-equiv="refresh"' in page
    assert client.get("/feeds/newband.xml").status_code == 404

    finish_preview(db, "newband", [make_post("N1", caption="Show <b>Friday</b>")])
    page = client.get("/preview/newband").text
    assert "Show &lt;b&gt;Friday&lt;/b&gt;" in page and "data:image/jpeg" not in page  # no images in this test
    assert "Follow</button>" in page and "Discard</button>" in page
    assert "@newband</a>" in client.get("/").text  # in "Previewed, not followed"
    assert client.get("/feeds/newband.xml").status_code == 404

    r = client.post("/accounts/newband/follow")
    assert "Following @newband" in r.text and "http://ig2rss:8091/feeds/newband.xml" in r.text
    assert client.get("/feeds/newband.xml").status_code == 200
    assert "You follow this account" in client.get("/preview/newband").text


def test_preview_is_reused_for_an_hour_and_not_asked_for_followed_accounts(client):
    db = client.app.state.db
    client.post("/previews", data={"username": "newband"})
    finish_preview(db, "newband", [make_post("N1")])
    client.post("/previews", data={"username": "newband"})
    assert db.account("newband")["preview_requested"] is None
    client.post("/previews", data={"username": "alice"})
    assert db.account("alice")["preview_requested"] is None and db.account("alice")["following"] == 1


def test_failed_preview_can_be_tried_again(client):
    db = client.app.state.db
    client.post("/previews", data={"username": "nobody"})
    finish_preview(db, "nobody", [], error="Account not found")
    page = client.get("/preview/nobody").text
    assert "Couldn't read @nobody: Account not found" in page and "Try again" in page
    client.post("/previews", data={"username": "nobody"})
    assert db.account("nobody")["preview_requested"] is not None


def test_discard_deletes_the_preview(client):
    db = client.app.state.db
    client.post("/previews", data={"username": "newband"})
    finish_preview(db, "newband", [make_post("N1")])
    r = client.post("/accounts/newband/remove")
    assert "Discarded the preview of @newband" in r.text
    assert db.account("newband") is None and db.posts("newband", 5) == []
    assert "No preview of @newband" in client.get("/preview/newband").text


def test_other_websites_cant_preview_or_follow(client):
    evil = {"Origin": "https://evil.example"}
    assert client.post("/previews", data={"username": "x"}, headers=evil).status_code == 403
    assert client.post("/accounts/alice/follow", headers=evil).status_code == 403


def test_databases_from_before_previews_are_updated(tmp_path):
    import sqlite3
    from app.db import DB
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("""CREATE TABLE accounts (username TEXT PRIMARY KEY, full_name TEXT NOT NULL DEFAULT '',
                   last_check INTEGER, last_success INTEGER, last_error TEXT NOT NULL DEFAULT '')""")
    old.execute("INSERT INTO accounts (username, last_check) VALUES ('martha_radio', 100)")
    old.commit()
    old.close()
    db = DB(path)
    a = db.account("martha_radio")
    assert a["following"] == 1 and a["preview_requested"] is None and a["last_check"] == 100
