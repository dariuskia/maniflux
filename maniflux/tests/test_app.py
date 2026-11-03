HX = {"HX-Request": "true"}


def test_list_shows_unread_ranked(client):
    r = client.get("/")
    assert r.status_code == 200
    text = r.text
    assert text.index("Fresh post") < text.index("Housing is too expensive") < text.index("Old post")


def test_miniflux_down_shows_message(client, fake):
    fake.down = True
    r = client.get("/")
    assert r.status_code == 200
    assert "Miniflux isn't answering" in r.text


def test_read_marks_read_and_records_feedback_then_undo(client, fake):
    r = client.post("/e/1/read", headers=HX)
    assert "Archived" in r.text and "Undo" in r.text
    assert fake.entries[1]["status"] == "read"
    db = client.app.state.db
    assert [row["action"] for row in db.q("SELECT action FROM feedback")] == ["read"]
    assert db.q("SELECT title FROM feedback")[0]["title"] == "Fresh post"

    r = client.post("/e/1/undo", headers=HX)
    assert 'class="card"' in r.text
    assert fake.entries[1]["status"] == "unread"
    assert db.q("SELECT COUNT(*) AS n FROM feedback")[0]["n"] == 0


def test_no_hide_any_more(client):
    assert client.post("/e/1/hide").status_code in (404, 405)
    assert "Hide" not in client.get("/").text


def test_opening_marks_read_and_keep_unread_undoes_it(client, fake):
    r = client.get("/e/1")
    assert "Fresh post" in r.text and "Unarchive" in r.text
    assert fake.entries[1]["status"] == "read"
    r = client.post("/e/1/unread", data={"back": "/?q=fresh"}, follow_redirects=False)
    assert r.headers["location"].startswith("/?q=fresh&msg=Unarchived")
    assert fake.entries[1]["status"] == "unread"


def test_opening_the_original_marks_read(client, fake):
    client.get("/e/2/original", follow_redirects=False)
    assert fake.entries[2]["status"] == "read"


def test_save_fills_type_and_description(client, fake, ai):
    from app.vault import read_frontmatter
    # a short summary-only entry with an unclear type
    fake.entries[1]["url"] = "https://www.nytimes.com/2026/10/30/x.html"
    client.post("/e/1/save", headers=HX)
    note = next(client.content_dir.glob("*.md"))
    fm = read_frontmatter(note.read_text())
    assert fm["description"] == "A short summary."
    assert fm["type"] == "article" and fm["tags"] == ["clippings", "article"]  # "Essay" counts as article
    assert fm["minutes"] == 9  # from the full article
    assert "%%" not in note.read_text() and "{{" not in note.read_text()
    # the full article was fetched first and given to the model
    assert any(c[1] == "/v1/entries/1/fetch-content" for c in fake.calls)
    assert "The full article." in ai.prompts[0] and "title: Fresh post" in ai.prompts[0]


def test_known_type_isnt_asked_and_video_isnt_fetched(client, fake, ai):
    from app.vault import read_frontmatter
    fake.entries[1]["url"] = "https://www.youtube.com/watch?v=abc"
    client.post("/e/1/save", headers=HX)
    fm = read_frontmatter(next(client.content_dir.glob("*.md")).read_text())
    assert fm["type"] == "video"
    assert '"type":' not in ai.prompts[0]
    assert not any(c[1].endswith("/fetch-content") for c in fake.calls)


def test_summary_failure_saves_nothing_and_keeps_card(client, fake, ai):
    ai.fail = True
    r = client.post("/e/1/save", headers=HX)
    assert 'class="card"' in r.text and "Not saved" in r.text and "Rate limited" in r.text
    assert not list(client.content_dir.glob("*.md"))
    assert fake.entries[1]["status"] == "unread"
    ai.fail = False
    r = client.post("/e/1/save", headers=HX)
    assert "Saved to Obsidian" in r.text


def test_save_writes_once_and_marks_read(client, fake):
    r = client.post("/e/3/save", data={"thought": "worth a look"}, headers=HX)
    assert "Saved to Obsidian" in r.text
    notes = list(client.content_dir.glob("*.md"))
    assert [n.name for n in notes] == ["Housing is too expensive.md"]
    assert "worth a look" in notes[0].read_text()
    assert fake.entries[3]["status"] == "read"

    fake.entries[3]["status"] = "unread"
    r = client.post("/e/3/save", headers=HX)
    assert "Already in Obsidian" in r.text
    assert len(list(client.content_dir.glob("*.md"))) == 1
    assert fake.entries[3]["status"] == "read"


def test_save_skips_url_clipped_elsewhere(client, fake):
    (client.content_dir / "clipped.md").write_text(f'---\nurl: "{fake.entries[1]["url"]}"\n---\n')
    r = client.post("/e/1/save", headers=HX)
    assert "Already in Obsidian: clipped.md" in r.text
    assert len(list(client.content_dir.glob("*.md"))) == 1


def test_save_without_htmx_redirects(client):
    r = client.post("/e/1/save", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/?msg=Saved")


def test_reading_view_and_full_text(client, fake):
    r = client.get("/e/1")
    assert "Fresh post" in r.text and "Some text." in r.text
    r = client.post("/e/1/full", headers=HX)
    assert "The full article." in r.text
    assert any(c[1] == "/v1/entries/1/fetch-content" and c[2].get("update_content") == "true" for c in fake.calls)


def test_original_redirects(client, fake):
    r = client.get("/e/1/original", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == fake.entries[1]["url"]


def test_weights_and_rules_change_the_list(client):
    client.post("/feeds/1/weight", data={"weight": "0"})
    assert "Nothing here" in client.get("/").text
    client.post("/feeds/1/weight", data={"weight": "1"})

    client.post("/rules", data={"kind": "keyword", "value": "housing", "action": "hide"})
    text = client.get("/").text
    assert "Housing is too expensive" not in text and "1 hidden" in text
    assert "Items with “housing”: hide" in client.get("/rules").text


def test_groups(client, fake):
    from .conftest import make_entry, make_feed
    news = make_feed(2, "News Feed")
    fake.entries[4] = make_entry(4, "A news item", feed=news)
    fake.feeds.append(news)
    db = client.app.state.db

    client.post("/groups", data={"name": "  News  "})
    r = client.post("/groups", data={"name": "news"}, follow_redirects=False)
    assert "already" in r.headers["location"]
    gid = next(iter(db.groups()))
    assert db.groups()[gid]["name"] == "News"

    r = client.post(f"/feeds/2/groups/{gid}")
    assert 'class="chip on"' in r.text
    page = client.get("/feeds").text
    assert "Not in a group" in page and page.index("Example Feed") < page.index("News Feed")

    # filter the list by group
    text = client.get(f"/?group={gid}").text
    assert "A news item" in text and "Fresh post" not in text

    # weight and limit
    client.post(f"/groups/{gid}/weight", data={"weight": "0"})
    assert "A news item" not in client.get("/").text
    client.post(f"/groups/{gid}/weight", data={"weight": "1"})
    client.post("/rules", data={"kind": "limit", "value": f"group:{gid}", "amount": "1"})
    assert "At most 1 items in News a day" in client.get("/rules").text

    client.post(f"/groups/{gid}/rename", data={"name": "Daily news"})
    assert db.groups()[gid]["name"] == "Daily news"
    client.post(f"/groups/{gid}/delete")
    assert db.groups() == {} and db.memberships() == {} and db.rules() == []


def test_tabs(client, fake):
    from .conftest import make_entry
    fake.entries[5] = make_entry(5, "A video", url="https://www.youtube.com/watch?v=1")
    text = client.get("/?show=watch").text
    assert "A video" in text and "Fresh post" not in text
    assert 'data-key="3"' in text
    assert "Fresh post" in client.get("/?show=read").text


def test_budget(client):
    text = client.get("/?budget=10").text
    assert "min" in text and 'class="chip on">10 min' in text


def test_settings(client):
    (client.content_dir / "bad.md").write_text("---\nx: [\n---\n")
    text = client.get("/settings").text
    assert "Miniflux 2.3.4" in text and "bad.md" in text


def test_score_refresh(client, fake):
    import asyncio

    from app.main import refresh_scores

    asyncio.run(refresh_scores(client.app))
    db = client.app.state.db
    assert db.q("SELECT COUNT(*) AS n FROM scores")[0]["n"] == 3
    fake.entries[1]["status"] = "read"
    asyncio.run(refresh_scores(client.app))
    assert db.q("SELECT COUNT(*) AS n FROM scores")[0]["n"] == 2
    assert db.get_state("max_entry_id") == "3"


def test_weight_change_rescores_stored_scores(client):
    db = client.app.state.db
    client.post("/feeds/1/weight", data={"weight": "2"})
    assert db.q("SELECT COUNT(*) AS n FROM scores")[0]["n"] == 3
    assert all("feed ×2" in r["reason"] for r in db.q("SELECT reason FROM scores"))
    client.post("/rules", data={"kind": "keyword", "value": "housing", "action": "hide"})
    assert db.q("SELECT reason FROM scores WHERE entry_id = 3")[0]["reason"].startswith("hidden")


def test_search(client, fake):
    text = client.get("/?q=housing").text
    assert "Housing is too expensive" in text and "Fresh post" not in text
    # every word has to match; feed names count too
    assert "Fresh post" in client.get("/?q=example+fresh").text
    assert "Nothing unread matches “fresh nonsense”" in client.get("/?q=fresh+nonsense").text
    # tab links keep the search
    assert 'href="/?q=housing&amp;show=read"' in client.get("/?q=housing").text


def test_actions_from_reading_view_return_to_the_same_list(client):
    r = client.post("/e/2/read", data={"back": "/?q=old&show=read&msg=stale"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?q=old&show=read&msg=Archived")
    # never anywhere but the list
    for bad in ("https://evil.example/", "//evil.example/", "/settings"):
        r = client.post("/e/1/read", data={"back": bad}, follow_redirects=False)
        assert r.headers["location"].startswith("/?msg=")
        client.post("/e/1/undo", headers=HX)


def test_archive_page(client, fake):
    client.post("/e/1/read", headers=HX)
    client.post("/e/3/save", headers=HX)
    fake.entries[1]["changed_at"] = "2030-01-01T00:00:00Z"  # archived most recently
    text = client.get("/archive").text
    assert "2 archived items" in text
    assert text.index("Fresh post") < text.index("Housing is too expensive")
    # the saved one is marked and has no Save button
    saved_card = text[text.index('id="e-3"'):]
    assert 'class="saved">saved' in saved_card.split("</article>")[0]
    assert 'data-key="s"' not in saved_card.split("</article>")[0]
    # search goes to Miniflux
    text = client.get("/archive?q=housing").text
    assert "Housing is too expensive" in text and "Fresh post" not in text
    assert any(c[2].get("search") == "housing" and c[2].get("status") == "read" for c in fake.calls)

    r = client.post("/e/1/unread", headers=HX)
    assert "Unarchived, back in the list" in r.text and fake.entries[1]["status"] == "unread"


def test_reading_view_actions_can_return_to_the_archive(client):
    r = client.post("/e/2/unread", data={"back": "/archive?q=old"}, follow_redirects=False)
    assert r.headers["location"].startswith("/archive?q=old&msg=Unarchived")
