import re
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from app.vault import VaultIndex, read_frontmatter, render_note, safe_file_name, split_authors, write_note

from .conftest import make_entry

TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=TZ)


def note(entry, thought="", note_type="article", description="What it covers."):
    return render_note(entry, thought, NOW, "people", TZ, note_type=note_type, description=description)


def test_note_format():
    e = make_entry(1, "Why Politics Has Become So Frenzied", url="https://www.theatlantic.com/x?utm_source=rss&id=3",
                   author="Jane Doe and John Roe", reading_time=9)
    e["feed"]["title"] = "The Atlantic"
    text = note(e, "my thought", description='Says "a lot": about politics.')
    fm = read_frontmatter(text)

    assert fm["title"] == "Why Politics Has Become So Frenzied"
    assert fm["url"] == "https://www.theatlantic.com/x?id=3"
    assert fm["source"] == "The Atlantic"
    assert "via" not in fm  # via is the outlet now, worked out by the library service
    assert fm["creators"] == ["[[people/Jane Doe|Jane Doe]]", "[[people/John Roe|John Roe]]"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", fm["published"])
    assert fm["date-added"] == "2026-10-30T12:00:00-07:00"
    assert fm["type"] == "writing" and fm["form"] is None  # an article is writing, with no form
    assert fm["minutes"] == 9
    assert fm["description"] == 'Says "a lot": about politics.'
    assert "tags" not in fm  # tags are yours; the tools don't write any
    assert "date-archived" in fm and fm["date-archived"] is None and "archive" not in fm
    # finished note: no prompts and no page text
    assert "{{" not in text and "%%" not in text
    assert text.endswith("---\n\nmy thought\n")


def test_empty_fields_and_books():
    e = make_entry(1, 'Quotes "and" colons: a [test] #1', author="", reading_time=7)
    fm = read_frontmatter(note(e, note_type="book", description=""))
    assert fm["title"] == 'Quotes "and" colons: a [test] #1'
    assert fm["creators"] is None and fm["description"] is None
    assert fm["minutes"] is None  # books don't get a length
    assert safe_file_name(e["title"]) == "Quotes and colons a test 1"
    assert note(e).endswith("---\n") and "## Notes" not in note(e)


def test_write_never_overwrites(tmp_path):
    e = make_entry(1, "Same title")
    a = write_note(tmp_path, e, "one")
    b = write_note(tmp_path, e, "two")
    assert a.name == "Same title.md" and b.name == "Same title (2).md"
    assert a.read_text() == "one"


def test_index_finds_urls_and_lists_broken(tmp_path):
    (tmp_path / "a.md").write_text('---\ntitle: A\nurl: "https://www.example.com/a/?utm_source=x"\n---\n')
    (tmp_path / "clip.md").write_text("---\nsource: https://example.com/clipped\n---\n")
    (tmp_path / "bad.md").write_text("---\ntitle: [unclosed\n---\n")
    (tmp_path / "nofm.md").write_text("just text")
    (tmp_path / "typo.md").write_text('---\ndate-added: 2025-08-04T14:02:96\nurl: "https://example.com/typo"\n---\n')
    idx = VaultIndex(tmp_path)
    assert idx.find("https://example.com/a").name == "a.md"
    assert idx.find("https://example.com/clipped#x").name == "clip.md"
    assert idx.find("https://example.com/other") is None
    assert idx.find("https://example.com/typo").name == "typo.md"
    assert {b.path for b in idx.broken()} == {"bad.md", "nofm.md"}


def test_split_authors():
    assert split_authors("By Jane Doe, John Roe & Ann Lee") == ["Jane Doe", "John Roe", "Ann Lee"]
    assert split_authors("Alexandra Andrews") == ["Alexandra Andrews"]
    assert split_authors("") == []


def test_unicode_survives_yaml():
    text = note(make_entry(1, "Ünïcode — “quotes” 'single'"), description="Café ✨")
    fm = yaml.safe_load(text.split("---\n")[1])
    assert fm["title"] == "Ünïcode — “quotes” 'single'" and fm["description"] == "Café ✨"


def test_source_text_keeps_sentences_whole():
    from app.text import full_text
    assert full_text('<p>He backed <a href="#">a petition</a> today.</p><p>Next<br>line</p>') == \
        "He backed a petition today.\nNext\nline"


def test_email_authors_are_not_names():
    from app.text import clean_author
    assert clean_author("dariuskianersi+caf_=yl6q74d5=news.example.com@gmail.com") == ""
    assert clean_author("forwarding-noreply@google.com") == ""
    assert clean_author("bounces+50396554-33ad=news.example.com@em4834.newsletter.oaktown.co") == ""
    assert clean_author("Jane Doe <jane@example.com>") == "Jane Doe"
    assert clean_author("Jane Doe") == "Jane Doe"
    e = make_entry(1, author="dariuskianersi+caf_=x=news.example.com@gmail.com")
    fm = read_frontmatter(note(e))
    assert fm["creators"] is None
    fm = read_frontmatter(render_note(e, "", NOW, "people", TZ, note_type="article", description="d",
                                      authors=["Resident Advisor", "[[x]]"]))
    assert fm["creators"] == ["[[people/Resident Advisor|Resident Advisor]]", "[[people/x|x]]"]
    # a real author from the feed wins over the model's
    fm = read_frontmatter(render_note(make_entry(2, author="Jane Doe"), "", NOW, "people", TZ,
                                      note_type="article", description="d", authors=["Someone Else"]))
    assert fm["creators"] == ["[[people/Jane Doe|Jane Doe]]"]


def test_kinds_become_library_types():
    for kind, (t, form) in {"paper": ("research", "paper"), "thread": ("writing", "thread"),
                            "podcast": ("podcast", "episode"), "other": ("project", "")}.items():
        fm = read_frontmatter(note(make_entry(1, "A title", author="Jane Doe"), note_type=kind))
        assert fm["type"] == t and (fm["form"] or "") == form and "tags" not in fm


def test_film_and_book_links():
    from app import kinds
    assert kinds.rule_type("https://letterboxd.com/film/close-up/") == "film"
    assert kinds.rule_type("https://www.imdb.com/title/tt0100234/") == "film"
    assert kinds.rule_type("https://www.goodreads.com/en/book/show/52374.X") == "book"
    assert kinds.rule_type("https://letterboxd.com/someone/") is None
    fm = read_frontmatter(note(make_entry(1, "Close-Up", url="https://letterboxd.com/film/close-up/"), note_type="film"))
    assert fm["type"] == "film" and "tags" not in fm
