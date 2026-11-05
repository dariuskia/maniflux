import xml.etree.ElementTree as ET

from app import feed

from .conftest import jpeg


def row(code="A1", caption="Hello", published=1_760_000_000, is_video=0, media_count=1):
    return {"shortcode": code, "caption": caption, "published": published, "is_video": is_video,
            "media_count": media_count}


def parse(body):
    return ET.fromstring(body)


def test_items_have_stable_links_dates_and_author():
    body = feed.build("alice", "Alice A", [row()], lambda code: [])
    item = parse(body).find("channel/item")
    assert item.findtext("link") == "https://www.instagram.com/p/A1/"
    assert item.findtext("guid") == "https://www.instagram.com/p/A1/"
    assert item.find("guid").get("isPermaLink") == "true"
    assert item.findtext("pubDate") == "Thu, 09 Oct 2025 08:53:20 GMT"
    assert item.findtext("{http://purl.org/dc/elements/1.1/}creator") == "Alice A"
    assert parse(body).findtext("channel/title") == "Alice A (@alice) on Instagram"


def test_caption_is_escaped():
    caption = 'Show tonight <script>alert(1)</script> & "more"\n\nDoors at 8'
    body = feed.build("alice", "", [row(caption=caption)], lambda code: [])
    item = parse(body).find("channel/item")
    desc = item.findtext("description")
    assert "<script>" not in desc and "&lt;script&gt;" in desc
    assert "<p>Doors at 8</p>" in desc
    assert item.findtext("title") == 'Show tonight <script>alert(1)</script> & "more"'  # plain text in XML
    assert b"<script>" not in body


def test_control_characters_dont_break_the_xml():
    body = feed.build("alice", "Al\x07ice", [row(caption="bad\x00 \x1b chars￾")], lambda code: [])
    item = parse(body).find("channel/item")
    assert item.findtext("title") == "bad  chars"


def test_images_are_embedded_and_extra_photos_linked():
    img = jpeg(10, 10)
    body = feed.build("alice", "", [row(media_count=5)], lambda code: [img, img])
    desc = parse(body).findtext("channel/item/description")
    assert desc.count('<img src="data:image/jpeg;base64,') == 2
    assert "3 more photos on Instagram" in desc


def test_titles_without_captions_and_long_captions():
    assert feed.title_for("", True, 1, "bob") == "Video by @bob"
    assert feed.title_for("  \n", False, 4, "bob") == "4 photos by @bob"
    assert feed.title_for("", False, 1, "bob") == "Photo by @bob"
    long = feed.title_for("x" * 200 + "\nsecond line", False, 1, "bob")
    assert len(long) == feed.TITLE_CHARS and long.endswith("…")


def test_video_links_to_instagram():
    desc = feed.content_html("", [], True, 1, "https://www.instagram.com/p/V/")
    assert "Watch the video on Instagram" in desc


def test_empty_feed_is_valid():
    channel = parse(feed.build("alice", "", [], lambda code: [])).find("channel")
    assert channel.findtext("title") == "@alice on Instagram" and channel.find("item") is None
