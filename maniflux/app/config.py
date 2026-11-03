import os
from pathlib import Path
from zoneinfo import ZoneInfo

MINIFLUX_URL = os.environ.get("MINIFLUX_URL", "http://miniflux:8080/v1").rstrip("/")
MINIFLUX_API_KEY = os.environ.get("MINIFLUX_API_KEY", "").strip()
CONTENT_DIR = Path(os.environ.get("CONTENT_DIR", "/vault/content"))
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
# Model for each saved note's type and description
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-haiku-5.5")
DB_PATH = Path(os.environ.get("DB_PATH", "/data/maniflux.db"))
PEOPLE_FOLDER = os.environ.get("PEOPLE_FOLDER", "people").strip("/")
TZ = ZoneInfo(os.environ.get("TZ", "America/Los_Angeles"))

# How many unread entries to load for the list
LIST_LIMIT = 500
# Entries with fewer words than this are treated as summaries; saving fetches the full article
SHORT_TEXT_WORDS = 150
# Minutes between background score refreshes
POLL_MINUTES = 5
