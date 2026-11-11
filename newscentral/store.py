import hashlib
import json
import os
import re
import sqlite3
import threading
import time

from .sources import country_label
from .text import canonical


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
                db.execute("BEGIN")
                try:
                    fn(db)
                    db.execute("PRAGMA user_version = %d" % version)  # int only, never user input
                    db.commit()
                except Exception:
                    db.rollback()
                    raise


@migration(1)
def _m1_saved_country_iso(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(saved)")]
    if "country_iso" not in cols:
        db.execute("ALTER TABLE saved ADD COLUMN country_iso TEXT DEFAULT ''")


@migration(2)
def _m2_sources_snapshot_and_item_lang(db):
    db.execute("""CREATE TABLE sources (
        id TEXT PRIMARY KEY, name TEXT, region TEXT, country_iso TEXT, scope TEXT,
        exile INTEGER DEFAULT 0, covers TEXT, lang TEXT, kind TEXT,
        state_media INTEGER DEFAULT 0, paywall INTEGER DEFAULT 0,
        first_seen REAL, last_seen REAL, active INTEGER DEFAULT 1)""")
    db.execute("ALTER TABLE items ADD COLUMN lang TEXT")
    db.execute("CREATE INDEX items_source_seen ON items(source_id, first_seen)")


@migration(3)
def _m3_renormalize_text(db):
    """Bring text stored before the text standard to canonical form. Markup was already stripped
    when the rows were written, so only the Unicode steps run; ids derive from links and do not change."""
    for table in ("items", "saved"):
        rows = db.execute("SELECT id, title, summary FROM %s" % table).fetchall()
        for row in rows:
            title, summary = canonical(row[1]), canonical(row[2])
            if title != (row[1] or "") or summary != (row[2] or ""):
                db.execute("UPDATE %s SET title = ?, summary = ? WHERE id = ?" % table, (title, summary, row[0]))


@migration(4)
def _m4_fetch_runs(db):
    db.execute("""CREATE TABLE fetch_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL NOT NULL, finished REAL,
        sources_checked INTEGER, new_items INTEGER, errors INTEGER)""")
    db.execute("CREATE INDEX fetch_runs_started ON fetch_runs(started)")


@migration(5)
def _m5_item_feed_tags(db):
    db.execute("ALTER TABLE items ADD COLUMN feed_tags TEXT")


def schema_version():
    return max((v for v, _ in MIGRATIONS), default=0)


# ---------------------------------------------------------------- full-text search

FTS_TABLE = ("CREATE VIRTUAL TABLE items_fts USING fts5(title, summary, content='items', "
             "content_rowid='rowid', tokenize='%s')")
FTS_TRIGGERS = {
    "items_fts_ai": "CREATE TRIGGER items_fts_ai AFTER INSERT ON items BEGIN "
                    "INSERT INTO items_fts(rowid, title, summary) VALUES (new.rowid, new.title, new.summary); END",
    "items_fts_ad": "CREATE TRIGGER items_fts_ad AFTER DELETE ON items BEGIN "
                    "INSERT INTO items_fts(items_fts, rowid, title, summary) "
                    "VALUES ('delete', old.rowid, old.title, old.summary); END",
    "items_fts_au": "CREATE TRIGGER items_fts_au AFTER UPDATE OF title, summary ON items BEGIN "
                    "INSERT INTO items_fts(items_fts, rowid, title, summary) "
                    "VALUES ('delete', old.rowid, old.title, old.summary); "
                    "INSERT INTO items_fts(rowid, title, summary) VALUES (new.rowid, new.title, new.summary); END",
}


def fts5_available(db):
    try:
        db.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(x)")
        db.execute("DROP TABLE temp.fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def search_terms(q):
    """Words of a user query: runs of letters and digits in any script. Everything else, the
    underscore included, is dropped, so FTS query syntax (quotes, parentheses, operators, column
    filters) can never reach the engine and no term is empty after tokenizing."""
    return re.findall(r"[^\W_]+", q or "", re.UNICODE)


# ---------------------------------------------------------------- storage

class Store:
    def __init__(self, path, fts=True):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = threading.Lock()
        self.fts = False
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
        try:
            run_migrations(self.db, self.lock)
            self.ensure_fts(fts)
        except Exception:
            self.db.close()
            raise

    def ensure_fts(self, wanted=True):
        """The search index is derived data that depends on the SQLite build, not on the schema
        version, so it is not a numbered migration. Runs at every startup.
        The index uses the implicit rowid of `items`: never VACUUM the database without calling
        this afterwards with the triggers dropped, because VACUUM may renumber rowids."""
        with self.lock:
            names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master")}
            if not (wanted and fts5_available(self.db)):
                # Triggers that write to a module this build lacks would break every insert.
                for name in FTS_TRIGGERS:
                    self.db.execute("DROP TRIGGER IF EXISTS %s" % name)
                self.db.commit()
                self.fts = False
                return False
            rebuild = False
            self.db.execute("BEGIN")
            try:
                if "items_fts" not in names:
                    try:
                        self.db.execute(FTS_TABLE % "unicode61 remove_diacritics 2")
                    except sqlite3.OperationalError:      # SQLite older than 3.27
                        self.db.execute(FTS_TABLE % "unicode61")
                    rebuild = True
                for name, sql in FTS_TRIGGERS.items():
                    if name not in names:
                        self.db.execute(sql)
                        rebuild = True
                if rebuild:
                    self.db.execute("INSERT INTO items_fts(items_fts) VALUES ('rebuild')")
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            self.fts = True
            return True

    def add_items(self, source_id, items, lang=None):
        now = time.time()
        rows = [(hashlib.sha1(it["link"].encode("utf-8")).hexdigest()[:16], source_id, it["title"],
                 it["link"], it.get("summary", ""), it.get("published"), now, lang,
                 json.dumps(it["tags"], ensure_ascii=False) if it.get("tags") else None) for it in items]
        with self.lock:
            # rowcount counts only rows inserted into items; total_changes would also count
            # the rows the FTS triggers write into the index.
            cur = self.db.executemany(
                "INSERT OR IGNORE INTO items (id, source_id, title, link, summary, published, first_seen, lang, feed_tags) "
                "VALUES (?,?,?,?,?,?,?,?,?)", rows)
            added = max(cur.rowcount, 0)
            self.db.commit()
            return added

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

    def items(self, min_first_seen, limit=30000):
        """Newest first. Returns (rows, truncated)."""
        with self.lock:
            rows = [dict(r) for r in self.db.execute(
                "SELECT * FROM items WHERE first_seen >= ? ORDER BY first_seen DESC LIMIT ?",
                (min_first_seen, limit + 1))]
        return rows[:limit], len(rows) > limit

    def sync_sources(self, sources, now=None):
        """Snapshot source metadata so archived items keep their attribution after a source
        leaves sources.json, and give a language to items stored before the column existed."""
        now = time.time() if now is None else now
        with self.lock:
            self.db.execute("UPDATE sources SET active = 0")
            for s in sources:
                self.db.execute("INSERT OR IGNORE INTO sources (id, first_seen) VALUES (?, ?)", (s["id"], now))
                self.db.execute(
                    "UPDATE sources SET name=?, region=?, country_iso=?, scope=?, exile=?, covers=?, lang=?, "
                    "kind=?, state_media=?, paywall=?, last_seen=?, active=1 WHERE id=?",
                    (s.get("name", s["id"]), s.get("region", "Other"), s.get("country_iso"), s.get("scope"),
                     1 if s.get("exile") else 0, s.get("covers"), s.get("lang"), s.get("kind") or "news",
                     1 if s.get("state_media") else 0, 1 if s.get("paywall") else 0, now, s["id"]))
            self.db.execute("UPDATE items SET lang = (SELECT lang FROM sources WHERE sources.id = items.source_id) "
                            "WHERE lang IS NULL")
            self.db.commit()

    def add_run(self, started, finished, sources_checked, new_items, errors):
        with self.lock:
            self.db.execute("INSERT INTO fetch_runs (started, finished, sources_checked, new_items, errors) "
                            "VALUES (?,?,?,?,?)", (started, finished, sources_checked, new_items, errors))
            self.db.commit()

    def run_starts(self, since):
        """Start times from `since`, ascending, preceded by the first run ever if it is older."""
        with self.lock:
            first = self.db.execute("SELECT MIN(started) FROM fetch_runs").fetchone()[0]
            rows = [r[0] for r in self.db.execute(
                "SELECT started FROM fetch_runs WHERE started >= ? ORDER BY started", (since,))]
        if first is not None and first < since:
            rows.insert(0, first)
        return rows

    def volume_rows(self, since):
        with self.lock:
            return [tuple(r) for r in self.db.execute(
                "SELECT CAST(first_seen / 86400 AS INTEGER) AS day, COALESCE(lang, '') AS lang, COUNT(*), "
                "SUM(LENGTH(title) + LENGTH(COALESCE(summary, ''))) FROM items WHERE first_seen >= ? "
                "GROUP BY day, lang ORDER BY day DESC, COUNT(*) DESC", (since,))]

    def search(self, q="", since=None, until=None, region=None, country=None, scope=None, lang=None,
               kind=None, source=None, limit=50, offset=0):
        """Whole-archive search. Returns (rows, total, engine). Dates filter on first_seen."""
        terms = search_terms(q)
        joins = "FROM items i LEFT JOIN sources s ON s.id = i.source_id"
        where, params = [], []
        order = "i.first_seen DESC"
        if terms and self.fts:
            joins += " JOIN items_fts ON items_fts.rowid = i.rowid"
            where.append("items_fts MATCH ?")
            params.append(" ".join('"%s"' % t for t in terms))
            order = "bm25(items_fts), i.first_seen DESC"
        elif terms:
            for t in terms:
                like = "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                where.append("(i.title LIKE ? ESCAPE '\\' OR i.summary LIKE ? ESCAPE '\\')")
                params.extend([like, like])
        filters = (
            ("i.first_seen >= ?", since),
            ("i.first_seen < ?", until),
            ("s.region = ?", region),
            ("(CASE WHEN s.exile = 1 AND s.covers IS NOT NULL THEN s.covers ELSE s.country_iso END) = ?", country),
            ("s.scope = ?", scope),
            ("i.lang = ?", lang),
            ("COALESCE(s.kind, 'news') = ?", kind),
            ("i.source_id = ?", source),
        )
        for clause, value in filters:
            if value is not None and value != "":
                where.append(clause)
                params.append(value)
        sql_where = (" WHERE " + " AND ".join(where)) if where else ""
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) " + joins + sql_where, params).fetchone()[0]
            rows = [dict(r) for r in self.db.execute(
                "SELECT i.id, i.source_id, i.title, i.link, i.summary, i.published, i.first_seen, i.lang, "
                "s.name AS source_name, s.region, s.country_iso, s.scope, s.exile, s.covers, s.kind "
                + joins + sql_where + " ORDER BY " + order + " LIMIT ? OFFSET ?", params + [limit, offset])]
        return rows, total, ("fts" if self.fts else "like")

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
