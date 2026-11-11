import os
from pathlib import Path
from zoneinfo import ZoneInfo

MINIFLUX_URL = os.environ.get("MINIFLUX_URL", "http://miniflux:8080/v1").rstrip("/")
MINIFLUX_API_KEY = os.environ.get("MINIFLUX_API_KEY", "").strip()
CONTENT_DIR = Path(os.environ.get("CONTENT_DIR", "/vault/content"))
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
# Model for each saved note's type and description
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5")
# Model that writes digests (picking out relevant parts of long batches uses OPENROUTER_MODEL)
DIGEST_MODEL = os.environ.get("DIGEST_MODEL", "anthropic/claude-sonnet-5.5")
# Where Maniflux is opened in a browser, for links in the digests feed
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://127.0.0.1:8090").rstrip("/")
# Where Miniflux (in Docker) fetches the digests feed from
DIGEST_FEED_URL = os.environ.get("DIGEST_FEED_URL", "http://maniflux:8090/digests/feed.xml")
# The library service (library/ in the feed repo) that turns a saved link into a finished note
LIBRARY_URL = os.environ.get("LIBRARY_URL", "http://library:8095").rstrip("/")
LIBRARY_TOKEN = os.environ.get("LIBRARY_TOKEN", "").strip()
DB_PATH = Path(os.environ.get("DB_PATH", "/data/maniflux.db"))
PEOPLE_FOLDER = os.environ.get("PEOPLE_FOLDER", "people").strip("/")
TZ = ZoneInfo(os.environ.get("TZ", "America/Los_Angeles"))

# How many unread entries to load for the list
LIST_LIMIT = 500
# Entries with fewer words than this are saved with their own text as the description (no summary, no page fetch)
SHORT_TEXT_WORDS = 150
# Minutes between background score refreshes
POLL_MINUTES = 5
