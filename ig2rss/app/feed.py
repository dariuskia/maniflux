"""RSS 2.0 for one account. Captions are untrusted: they're escaped into HTML, and ElementTree escapes the XML."""

import base64
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime

DC = "http://purl.org/dc/elements/1.1/"
ET.register_namespace("dc", DC)

TITLE_CHARS = 90
# Characters XML 1.0 doesn't allow; one in a caption would break the whole feed
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def clean(text: str) -> str:
    return _NOT_XML.sub("", text or "")


def post_url(shortcode: str) -> str:
    return f"https://www.instagram.com/p/{shortcode}/"


def title_for(caption: str, is_video: bool, media_count: int, username: str) -> str:
    caption = clean(caption)
    first = next((line.strip() for line in caption.splitlines() if line.strip()), "")
    if first:
        return first if len(first) <= TITLE_CHARS else first[:TITLE_CHARS - 1].rstrip() + "…"
    if is_video:
        return f"Video by @{username}"
    return f"{media_count} photos by @{username}" if media_count > 1 else f"Photo by @{username}"


def content_html(caption: str, images: list[bytes], is_video: bool, media_count: int, link: str) -> str:
    caption = clean(caption)
    parts = [f'<p><img src="data:image/jpeg;base64,{base64.b64encode(jpeg).decode()}" alt=""></p>' for jpeg in images]
    for para in re.split(r"\n\s*\n", caption.strip()):
        if para.strip():
            parts.append("<p>" + html.escape(para.strip()).replace("\n", "<br>") + "</p>")
    href = html.escape(link)
    if is_video:
        parts.append(f'<p><a href="{href}">Watch the video on Instagram</a></p>')
    elif media_count > len(images):
        more = media_count - len(images)
        parts.append(f'<p><a href="{href}">{more} more {"photo" if more == 1 else "photos"} on Instagram</a></p>')
    return "\n".join(parts)


def build(username: str, full_name: str, posts, images_for) -> bytes:
    """posts: rows with shortcode, caption, published, is_video, media_count. images_for(shortcode) -> JPEGs."""
    full_name = clean(full_name)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    name = f"{full_name} (@{username})" if full_name else f"@{username}"
    ET.SubElement(channel, "title").text = f"{name} on Instagram"
    ET.SubElement(channel, "link").text = f"https://www.instagram.com/{username}/"
    ET.SubElement(channel, "description").text = f"Posts by @{username}, read by ig2rss"
    for p in posts:
        link = post_url(p["shortcode"])
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = title_for(p["caption"], bool(p["is_video"]), p["media_count"], username)
        ET.SubElement(item, "link").text = link
        ET.SubElement(item, "guid", isPermaLink="true").text = link
        published = datetime.fromtimestamp(p["published"], tz=timezone.utc)
        ET.SubElement(item, "pubDate").text = format_datetime(published, usegmt=True)
        ET.SubElement(item, f"{{{DC}}}creator").text = full_name or f"@{username}"
        ET.SubElement(item, "description").text = content_html(
            p["caption"], images_for(p["shortcode"]), bool(p["is_video"]), p["media_count"], link)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)
