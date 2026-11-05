import os
from pathlib import Path
from zoneinfo import ZoneInfo

# Your own Instagram username, if you made a session file (see README). Empty = not logged in.
LOGIN = os.environ.get("IG2RSS_LOGIN", "").strip().lstrip("@").lower()
SESSION_FILE = Path(os.environ.get("SESSION_FILE", "/data/session"))
DB_PATH = Path(os.environ.get("DB_PATH", "/data/ig2rss.db"))
# Hours between checks of each account (at least 1)
POLL_HOURS = max(1.0, float(os.environ.get("IG2RSS_POLL_HOURS") or 6))
# Also read the Reels tab. Reels shared to the profile grid already come through posts; this adds one request per reel.
REELS = os.environ.get("IG2RSS_REELS", "0").strip().lower() in ("1", "true", "yes")
TZ = ZoneInfo(os.environ.get("TZ", "America/Los_Angeles"))
# Where Miniflux (in Docker) and a browser on this Mac reach ig2rss; shown on the status page
INTERNAL_URL = "http://ig2rss:8091"
LOCAL_URL = "http://127.0.0.1:8091"

# Posts read on an account's first check (or preview). Logged out, Instagram needs one request per post, so fewer.
POSTS_PER_CHECK = 12
POSTS_PER_CHECK_LOGGED_OUT = 6
# Reels read on the first check when REELS is on (one request each)
REELS_PER_CHECK = 3
# Later checks read until the first post older than the newest one saved, as Instaloader's --latest-stamps does:
# the first POSSIBLY_PINNED posts can be pinned, so an older post among them doesn't stop the reading.
POSSIBLY_PINNED = 3
# The most posts one check reads (after a long gap, such as the Mac asleep)
MAX_POSTS_PER_CHECK = 30
# Pause between two accounts, in seconds (random in this range). Instaloader adds its own waits when needed.
ACCOUNT_GAP_SECONDS = (30, 90)
# Images kept per post, shrunk to this width, stored in the database and put in the feed
IMAGES_PER_POST = 3
IMAGE_WIDTH = 480
MAX_IMAGE_BYTES = 15_000_000
# Previews you don't follow are deleted after this; previewing again within PREVIEW_REUSE_HOURS shows the saved one
PREVIEW_KEEP_HOURS = 24
PREVIEW_REUSE_HOURS = 1
# Posts in each feed, and posts kept per account
FEED_ITEMS = 30
KEEP_POSTS = 100
