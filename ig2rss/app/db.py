"""SQLite: cached posts and their shrunk images, plus when each account was last checked."""

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    username TEXT NOT NULL,
    shortcode TEXT NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    published INTEGER NOT NULL,
    is_video INTEGER NOT NULL DEFAULT 0,
    media_count INTEGER NOT NULL DEFAULT 1,
    first_seen INTEGER NOT NULL,
    PRIMARY KEY (username, shortcode)
);
-- JPEGs already shrunk for the feed; shared when a post shows up on two accounts
CREATE TABLE IF NOT EXISTS images (
    shortcode TEXT NOT NULL,
    position INTEGER NOT NULL,
    jpeg BLOB NOT NULL,
    PRIMARY KEY (shortcode, position)
);
CREATE TABLE IF NOT EXISTS accounts (
    username TEXT PRIMARY KEY,
    full_name TEXT NOT NULL DEFAULT '',
    last_check INTEGER,
    last_success INTEGER,
    last_error TEXT NOT NULL DEFAULT '',
    -- 0 for a preview you haven't followed (no feed, not checked on the schedule)
    following INTEGER NOT NULL DEFAULT 1,
    -- set while a preview waits to be read
    preview_requested INTEGER
);
-- problem (why checks can't run), paused_until and pause_reason (why this round stopped)
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            # Databases from before previews
            have = {r["name"] for r in self.conn.execute("PRAGMA table_info(accounts)")}
            if "following" not in have:
                self.conn.execute("ALTER TABLE accounts ADD COLUMN following INTEGER NOT NULL DEFAULT 1")
            if "preview_requested" not in have:
                self.conn.execute("ALTER TABLE accounts ADD COLUMN preview_requested INTEGER")

    def _all(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def _one(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def _run(self, sql, args=()):
        with self.lock:
            self.conn.execute(sql, args)

    # State

    def get_state(self, key: str, default: str = "") -> str:
        row = self._one("SELECT value FROM state WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_state(self, key: str, value) -> None:
        self._run("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (key, str(value)))

    # Accounts (the list managed on the status page) and previews

    def add_account(self, username: str) -> bool:
        """Follows an account (a new one, or one you previewed). False if already followed."""
        with self.lock:
            row = self.conn.execute("SELECT following FROM accounts WHERE username = ?", (username,)).fetchone()
            if row and row["following"]:
                return False
            if row:
                self.conn.execute("UPDATE accounts SET following = 1 WHERE username = ?", (username,))
            else:
                self.conn.execute("INSERT INTO accounts (username) VALUES (?)", (username,))
            return True

    def request_preview(self, username: str, now: int) -> None:
        with self.lock:
            self.conn.execute("INSERT OR IGNORE INTO accounts (username, following) VALUES (?, 0)", (username,))
            self.conn.execute("UPDATE accounts SET preview_requested = ? WHERE username = ?", (now, username))

    def next_preview(self):
        return self._one("""SELECT * FROM accounts WHERE preview_requested IS NOT NULL
                            ORDER BY preview_requested LIMIT 1""")

    def remove_account(self, username: str) -> None:
        """Forgets the account (or preview) and its saved posts."""
        with self.lock:
            self._delete(["username = ?"], (username,))

    def remove_old_previews(self, before: int) -> None:
        """Previews you didn't follow, read before this time."""
        with self.lock:
            self._delete(["following = 0", "preview_requested IS NULL", "COALESCE(last_check, 0) < ?"], (before,))

    def _delete(self, where: list[str], args) -> None:
        cond = " AND ".join(where)
        self.conn.execute(f"DELETE FROM posts WHERE username IN (SELECT username FROM accounts WHERE {cond})", args)
        self.conn.execute(f"DELETE FROM accounts WHERE {cond}", args)
        self.conn.execute("DELETE FROM images WHERE shortcode NOT IN (SELECT shortcode FROM posts)")

    def account(self, username: str):
        return self._one("SELECT * FROM accounts WHERE username = ?", (username,))

    def accounts(self, following: bool = True) -> list[sqlite3.Row]:
        """Followed accounts (or previews), with how many posts are cached."""
        return self._all("""SELECT a.*, (SELECT COUNT(*) FROM posts p WHERE p.username = a.username) AS post_count
                            FROM accounts a WHERE following = ? ORDER BY username""", (int(following),))

    def next_due(self):
        """The followed account checked longest ago (never checked first), or None."""
        return self._one("""SELECT * FROM accounts WHERE following = 1
                            ORDER BY last_check IS NOT NULL, last_check, username LIMIT 1""")

    def update_account(self, username: str, **fields) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._run(f"UPDATE accounts SET {sets} WHERE username = ?", (*fields.values(), username))

    # Posts

    def upsert_post(self, username: str, post, now: int) -> None:
        """Adds a post, or updates its caption (captions can be edited)."""
        self._run(
            """INSERT INTO posts (username, shortcode, caption, published, is_video, media_count, first_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(username, shortcode) DO UPDATE SET caption = excluded.caption,
                   is_video = excluded.is_video, media_count = excluded.media_count""",
            (username, post.shortcode, post.caption, post.published, int(post.is_video), post.media_count, now))

    def newest_published(self, username: str) -> int | None:
        """Date of the newest saved post, or None if none are saved."""
        return self._one("SELECT MAX(published) AS newest FROM posts WHERE username = ?", (username,))["newest"]

    def posts(self, username: str, limit: int) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM posts WHERE username = ? ORDER BY published DESC, shortcode LIMIT ?",
                         (username, limit))

    def image_positions(self, shortcode: str) -> set[int]:
        return {r["position"] for r in self._all("SELECT position FROM images WHERE shortcode = ?", (shortcode,))}

    def add_image(self, shortcode: str, position: int, jpeg: bytes) -> None:
        self._run("INSERT OR REPLACE INTO images (shortcode, position, jpeg) VALUES (?, ?, ?)",
                  (shortcode, position, jpeg))

    def images(self, shortcode: str) -> list[bytes]:
        return [r["jpeg"] for r in self._all("SELECT jpeg FROM images WHERE shortcode = ? ORDER BY position",
                                             (shortcode,))]

    def prune(self, username: str, keep: int) -> None:
        """Drops all but the newest posts of an account, and images no post uses."""
        with self.lock:
            self.conn.execute(
                """DELETE FROM posts WHERE username = ? AND shortcode NOT IN (
                       SELECT shortcode FROM posts WHERE username = ? ORDER BY published DESC LIMIT ?)""",
                (username, username, keep))
            self.conn.execute("DELETE FROM images WHERE shortcode NOT IN (SELECT shortcode FROM posts)")
