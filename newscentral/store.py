import hashlib
import os
import sqlite3
import threading
import time

from .sources import country_label


def sort_time(published, first_seen):
    # Trust the feed's date unless it is in the future (bad time zones).
    if published and published <= first_seen + 3600:
        return published
    return first_seen


# ---------------------------------------------------------------- migrations

MIGRATIONS = []  # list of (version, fn(db)); applied in ascending order


def migration(version):
    def register(fn):
        MIGRATIONS.append((version, fn))
        return fn
    return register


def run_migrations(db, lock):
    with lock:
        current = db.execute("PRAGMA user_version").fetchone()[0]
        for version, fn in sorted(MIGRATIONS, key=lambda t: t[0]):
            if version > current:
                fn(db)
                db.execute("PRAGMA user_version = %d" % version)  # int only, never user input
                db.commit()


@migration(1)
def _m1_saved_country_iso(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(saved)")]
    if "country_iso" not in cols:
        db.execute("ALTER TABLE saved ADD COLUMN country_iso TEXT DEFAULT ''")


def schema_version():
    return max((v for v, _ in MIGRATIONS), default=0)


# ---------------------------------------------------------------- storage

class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, title TEXT, link TEXT,
                    summary TEXT, published REAL, first_seen REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS items_first_seen ON items(first_seen);
                CREATE TABLE IF NOT EXISTS saved (
                    id TEXT PRIMARY KEY, source_id TEXT, source_name TEXT, region TEXT,
                    country TEXT, lang TEXT, title TEXT, link TEXT, summary TEXT,
                    published REAL, saved_at REAL, note TEXT DEFAULT '', digested INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS feed_state (
                    source_id TEXT PRIMARY KEY, feed_used TEXT, status TEXT, error TEXT,
                    last_try REAL, last_ok REAL, count INTEGER DEFAULT 0, etag TEXT, lastmod TEXT);
            """)
            self.db.commit()
        run_migrations(self.db, self.lock)

    def add_items(self, source_id, items):
        now = time.time()
        rows = [(hashlib.sha1(it["link"].encode("utf-8")).hexdigest()[:16], source_id, it["title"],
                 it["link"], it.get("summary", ""), it.get("published"), now) for it in items]
        with self.lock:
            before = self.db.total_changes
            self.db.executemany("INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?)", rows)
            self.db.commit()
            return self.db.total_changes - before

    def set_state(self, source_id, result):
        now = time.time()
        with self.lock:
            prev = self.db.execute("SELECT * FROM feed_state WHERE source_id=?", (source_id,)).fetchone()
            ok = result["status"] in ("ok", "discovered")
            last_ok = now if ok else (prev["last_ok"] if prev else None)
            if result.get("not_modified"):
                count = prev["count"] if prev else 0
            else:
                count = len(result["items"])
            feed_used = result.get("feed") or (prev["feed_used"] if prev else None)
            self.db.execute("INSERT OR REPLACE INTO feed_state VALUES (?,?,?,?,?,?,?,?,?)",
                            (source_id, feed_used, result["status"], result.get("error", ""), now,
                             last_ok, count, result.get("etag"), result.get("lastmod")))
            self.db.commit()

    def states(self):
        with self.lock:
            return {r["source_id"]: dict(r) for r in self.db.execute("SELECT * FROM feed_state")}

    def items(self, min_first_seen):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM items WHERE first_seen >= ? ORDER BY first_seen DESC LIMIT 30000",
                (min_first_seen,))]

    def prune(self, cutoff):
        with self.lock:
            self.db.execute("DELETE FROM items WHERE first_seen < ? AND COALESCE(published, first_seen) < ?",
                            (cutoff, cutoff))
            self.db.commit()

    def get_item(self, item_id):
        with self.lock:
            r = self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            return dict(r) if r else None

    def save(self, item, src):
        with self.lock:
            self.db.execute(
                "INSERT OR IGNORE INTO saved (id, source_id, source_name, region, country, lang, title, "
                "link, summary, published, saved_at, country_iso) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["id"], item["source_id"], src.get("name", item["source_id"]), src.get("region", ""),
                 country_label(src), src.get("lang", ""), item["title"], item["link"],
                 item["summary"], sort_time(item["published"], item["first_seen"]), time.time(),
                 src.get("country_iso") or ""))
            self.db.commit()

    def unsave(self, item_id):
        with self.lock:
            self.db.execute("DELETE FROM saved WHERE id=?", (item_id,))
            self.db.commit()

    def update_saved(self, item_id, note=None, digested=None):
        with self.lock:
            if note is not None:
                self.db.execute("UPDATE saved SET note=? WHERE id=?", (str(note)[:5000], item_id))
            if digested is not None:
                self.db.execute("UPDATE saved SET digested=? WHERE id=?", (1 if digested else 0, item_id))
            self.db.commit()

    def saved(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM saved ORDER BY saved_at DESC")]
