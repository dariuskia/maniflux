"""SQLite: only what Miniflux doesn't know. Keyed by Miniflux entry and feed ids."""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .ranking import Steering

SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    entry_id INTEGER PRIMARY KEY,
    score REAL NOT NULL,
    reason TEXT NOT NULL,
    scored_at TEXT NOT NULL
);
-- Groups of feeds, kept in Maniflux (a feed can be in several); Miniflux categories aren't used
CREATE TABLE IF NOT EXISTS feed_group (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    weight REAL NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS feed_group_member (
    feed_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    PRIMARY KEY (feed_id, group_id)
);
-- Feed weights (kind 'category' is from before groups and is no longer used)
CREATE TABLE IF NOT EXISTS weights (
    kind TEXT NOT NULL CHECK (kind IN ('feed', 'category')),
    target_id INTEGER NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (kind, target_id)
);
-- kind: keyword (value = word or phrase), type (value = video, article…),
--       limit (value = 'type:video', 'feed:12' or 'group:3', amount = how many a day)
-- action: boost, lower or hide (keyword and type rules)
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('keyword', 'type', 'limit')),
    value TEXT NOT NULL,
    action TEXT,
    amount INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    ends_on TEXT,
    created_at TEXT NOT NULL
);
-- title, url and feed_id are kept so feedback still means something after
-- Miniflux deletes the entry
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    at TEXT NOT NULL,
    title TEXT,
    url TEXT,
    feed_id INTEGER
);
CREATE INDEX IF NOT EXISTS feedback_entry ON feedback (entry_id);
CREATE TABLE IF NOT EXISTS saved (
    entry_id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    path TEXT NOT NULL,
    saved_at TEXT NOT NULL
);
-- Digests: written by a model from groups of feeds, on a schedule
-- frequency: daily, weekly, biweekly, monthly; weekday 0 = Monday; at = "HH:MM" local time
CREATE TABLE IF NOT EXISTS digest (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL,
    frequency TEXT NOT NULL CHECK (frequency IN ('daily', 'weekly', 'biweekly', 'monthly')),
    weekday INTEGER NOT NULL DEFAULT 0,
    monthday INTEGER NOT NULL DEFAULT 1,
    at TEXT NOT NULL DEFAULT '08:00',
    active INTEGER NOT NULL DEFAULT 1,
    next_run_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digest_group (
    digest_id INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    PRIMARY KEY (digest_id, group_id)
);
-- status: running, done, empty (no new items), error
CREATE TABLE IF NOT EXISTS digest_run (
    id INTEGER PRIMARY KEY,
    digest_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    items TEXT,  -- JSON: number, title, link, feed and date of each item read (not the text)
    body TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS digest_run_digest ON digest_run (digest_id, id);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def x(self, sql: str, args=()) -> int:
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    # ---------- weights ----------

    def set_weight(self, kind: str, target_id: int, weight: float):
        if weight == 1:
            self.x("DELETE FROM weights WHERE kind = ? AND target_id = ?", (kind, target_id))
        else:
            self.x(
                "INSERT INTO weights (kind, target_id, weight) VALUES (?, ?, ?) "
                "ON CONFLICT (kind, target_id) DO UPDATE SET weight = excluded.weight",
                (kind, target_id, weight),
            )

    def feed_weights(self) -> dict[int, float]:
        return {r["target_id"]: r["weight"] for r in self.q("SELECT * FROM weights WHERE kind = 'feed'")}

    # ---------- groups ----------

    def groups(self) -> dict[int, dict]:
        return {r["id"]: dict(r) for r in self.q("SELECT * FROM feed_group ORDER BY name COLLATE NOCASE")}

    def memberships(self) -> dict[int, set[int]]:
        out: dict[int, set[int]] = {}
        for r in self.q("SELECT feed_id, group_id FROM feed_group_member"):
            out.setdefault(r["feed_id"], set()).add(r["group_id"])
        return out

    def add_group(self, name: str) -> int | None:
        try:
            return self.x("INSERT INTO feed_group (name) VALUES (?)", (name,))
        except sqlite3.IntegrityError:
            return None

    def rename_group(self, group_id: int, name: str) -> bool:
        try:
            self.x("UPDATE feed_group SET name = ? WHERE id = ?", (name, group_id))
            return True
        except sqlite3.IntegrityError:
            return False

    def delete_group(self, group_id: int):
        with self.lock:
            c = self.conn
            c.execute("BEGIN")
            c.execute("DELETE FROM feed_group_member WHERE group_id = ?", (group_id,))
            c.execute("DELETE FROM rules WHERE kind = 'limit' AND value = ?", (f"group:{group_id}",))
            c.execute("DELETE FROM digest_group WHERE group_id = ?", (group_id,))
            c.execute("DELETE FROM feed_group WHERE id = ?", (group_id,))
            c.execute("COMMIT")

    def set_group_weight(self, group_id: int, weight: float):
        self.x("UPDATE feed_group SET weight = ? WHERE id = ?", (weight, group_id))

    def toggle_member(self, feed_id: int, group_id: int) -> bool:
        """Puts the feed in the group or takes it out. Returns whether it's in now."""
        if self.q("SELECT 1 FROM feed_group_member WHERE feed_id = ? AND group_id = ?", (feed_id, group_id)):
            self.x("DELETE FROM feed_group_member WHERE feed_id = ? AND group_id = ?", (feed_id, group_id))
            return False
        self.x("INSERT INTO feed_group_member (feed_id, group_id) VALUES (?, ?)", (feed_id, group_id))
        return True

    def set_member(self, feed_id: int, group_id: int, on: bool) -> None:
        if on:
            self.x("INSERT OR IGNORE INTO feed_group_member (feed_id, group_id) VALUES (?, ?)", (feed_id, group_id))
        else:
            self.x("DELETE FROM feed_group_member WHERE feed_id = ? AND group_id = ?", (feed_id, group_id))

    def steering(self):
        return Steering(self.feed_weights(), self.groups(), self.memberships(), self.rules())

    # ---------- digests ----------

    def digests(self) -> list[dict]:
        rows = [dict(r) for r in self.q("SELECT * FROM digest ORDER BY name COLLATE NOCASE")]
        groups = {}
        for r in self.q("SELECT digest_id, group_id FROM digest_group"):
            groups.setdefault(r["digest_id"], set()).add(r["group_id"])
        for d in rows:
            d["group_ids"] = groups.get(d["id"], set())
        return rows

    def digest(self, digest_id: int) -> dict | None:
        return next((d for d in self.digests() if d["id"] == digest_id), None)

    def save_digest(self, digest_id: int | None, fields: dict, group_ids: set[int]) -> int:
        cols = ["name", "instructions", "frequency", "weekday", "monthday", "at", "active", "next_run_at"]
        with self.lock:
            c = self.conn
            c.execute("BEGIN")
            if digest_id is None:
                cur = c.execute(
                    f"INSERT INTO digest ({', '.join(cols)}, created_at) VALUES ({', '.join('?' * len(cols))}, ?)",
                    [fields[k] for k in cols] + [now()])
                digest_id = cur.lastrowid
            else:
                c.execute(f"UPDATE digest SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?",
                          [fields[k] for k in cols] + [digest_id])
            c.execute("DELETE FROM digest_group WHERE digest_id = ?", (digest_id,))
            c.executemany("INSERT INTO digest_group (digest_id, group_id) VALUES (?, ?)",
                          [(digest_id, g) for g in group_ids])
            c.execute("COMMIT")
        return digest_id

    def delete_digest(self, digest_id: int):
        with self.lock:
            c = self.conn
            c.execute("BEGIN")
            for t in ("digest_group", "digest_run"):
                c.execute(f"DELETE FROM {t} WHERE digest_id = ?", (digest_id,))
            c.execute("DELETE FROM digest WHERE id = ?", (digest_id,))
            c.execute("COMMIT")

    def runs(self, digest_id: int | None = None, limit: int = 20) -> list[dict]:
        if digest_id is None:
            rows = self.q("SELECT r.*, d.name FROM digest_run r JOIN digest d ON d.id = r.digest_id "
                          "ORDER BY r.id DESC LIMIT ?", (limit,))
        else:
            rows = self.q("SELECT r.*, d.name FROM digest_run r JOIN digest d ON d.id = r.digest_id "
                          "WHERE r.digest_id = ? ORDER BY r.id DESC LIMIT ?", (digest_id, limit))
        return [dict(r) for r in rows]

    def run(self, run_id: int) -> dict | None:
        rows = self.q("SELECT r.*, d.name FROM digest_run r JOIN digest d ON d.id = r.digest_id WHERE r.id = ?",
                      (run_id,))
        return dict(rows[0]) if rows else None

    # ---------- rules ----------

    def rules(self) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM rules ORDER BY id")]

    def add_rule(self, kind, value, action, amount, ends_on):
        self.x(
            "INSERT INTO rules (kind, value, action, amount, ends_on, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, value, action, amount, ends_on or None, now()),
        )

    # ---------- feedback and saved ----------

    def feedback(self, entry: dict, action: str):
        self.x(
            "INSERT INTO feedback (entry_id, action, at, title, url, feed_id) VALUES (?, ?, ?, ?, ?, ?)",
            (entry["id"], action, now(), entry.get("title"), entry.get("url"), entry.get("feed_id")),
        )

    def saved_path(self, entry_id: int) -> str | None:
        rows = self.q("SELECT path FROM saved WHERE entry_id = ?", (entry_id,))
        return rows[0]["path"] if rows else None

    def add_saved(self, entry_id: int, url: str, path: str):
        self.x(
            "INSERT OR REPLACE INTO saved (entry_id, url, path, saved_at) VALUES (?, ?, ?, ?)",
            (entry_id, url, path, now()),
        )

    # ---------- state ----------

    def get_state(self, key: str, default=None):
        rows = self.q("SELECT value FROM state WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_state(self, key: str, value):
        self.x(
            "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    # ---------- scores ----------

    def replace_scores(self, rows: list[tuple[int, float, str]]):
        """Store the latest score for each unread entry; drop scores for entries no longer unread."""
        stamp = now()
        with self.lock:
            c = self.conn
            c.execute("BEGIN")
            try:
                c.execute("CREATE TEMP TABLE IF NOT EXISTS keep (id INTEGER PRIMARY KEY)")
                c.execute("DELETE FROM keep")
                c.executemany("INSERT INTO keep (id) VALUES (?)", [(r[0],) for r in rows])
                c.execute("DELETE FROM scores WHERE entry_id NOT IN (SELECT id FROM keep)")
                c.executemany(
                    "INSERT INTO scores (entry_id, score, reason, scored_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (entry_id) DO UPDATE SET score = excluded.score, reason = excluded.reason, "
                    "scored_at = excluded.scored_at "
                    "WHERE scores.score != excluded.score OR scores.reason != excluded.reason",
                    [(eid, score, reason, stamp) for eid, score, reason in rows],
                )
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise

    def counts(self) -> dict[str, int]:
        return {
            t: self.q(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"]
            for t in ("scores", "weights", "feed_group", "rules", "feedback", "saved")
        }
