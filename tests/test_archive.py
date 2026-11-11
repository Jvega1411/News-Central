# tests/test_archive.py
import inspect
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from newscentral import store as store_mod
from newscentral.store import Store
from tests.helpers import ApiCase, SOURCES

V1_SCHEMA = """
    CREATE TABLE items (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, title TEXT, link TEXT,
        summary TEXT, published REAL, first_seen REAL NOT NULL);
    CREATE TABLE saved (id TEXT PRIMARY KEY, source_id TEXT, source_name TEXT, region TEXT,
        country TEXT, lang TEXT, title TEXT, link TEXT, summary TEXT, published REAL,
        saved_at REAL, note TEXT DEFAULT '', digested INTEGER DEFAULT 0, country_iso TEXT DEFAULT '');
    CREATE TABLE feed_state (source_id TEXT PRIMARY KEY, feed_used TEXT, status TEXT, error TEXT,
        last_try REAL, last_ok REAL, count INTEGER DEFAULT 0, etag TEXT, lastmod TEXT);
    PRAGMA user_version = 1;
"""


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "news.db")
        self.store = None

    def tearDown(self):
        if self.store:
            self.store.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def open(self):
        self.store = Store(self.path)
        return self.store

    def make_v1(self, rows=()):
        db = sqlite3.connect(self.path)
        db.executescript(V1_SCHEMA)
        for sql, params in rows:
            db.execute(sql, params)
        db.commit()
        db.close()


class TestNoPrune(StoreCase):
    def test_nothing_deletes_items(self):
        self.assertFalse(hasattr(Store, "prune"))
        self.assertNotIn("DELETE FROM items", inspect.getsource(store_mod))

    def test_old_items_stay(self):
        s = self.open()
        s.add_items("elpais", [{"title": "Old", "link": "https://example.com/old"}], "es")
        s.db.execute("UPDATE items SET first_seen = ?, published = ?", (time.time() - 400 * 86400,) * 2)
        s.db.commit()
        rows, truncated = s.items(0)
        self.assertEqual(len(rows), 1)
        self.assertFalse(truncated)


class TestLangAndSnapshot(StoreCase):
    def test_new_items_carry_lang(self):
        s = self.open()
        s.add_items("ahram", [{"title": "T", "link": "https://example.com/a"}], "ar")
        self.assertEqual(s.db.execute("SELECT lang FROM items").fetchone()[0], "ar")

    def test_sync_sources_snapshots_and_backfills(self):
        s = self.open()
        s.add_items("elpais", [{"title": "T", "link": "https://example.com/a"}])     # lang unknown
        s.sync_sources(SOURCES, now=1000.0)
        row = dict(s.db.execute("SELECT * FROM sources WHERE id='meduza'").fetchone())
        self.assertEqual((row["name"], row["country_iso"], row["exile"], row["covers"], row["lang"]),
                         ("Meduza", "LV", 1, "RU", "ru"))
        self.assertEqual((row["first_seen"], row["last_seen"], row["active"]), (1000.0, 1000.0, 1))
        self.assertEqual(s.db.execute("SELECT lang FROM items").fetchone()[0], "es")

    def test_removed_source_keeps_its_row(self):
        s = self.open()
        s.sync_sources(SOURCES, now=1000.0)
        s.sync_sources([x for x in SOURCES if x["id"] != "bbc"], now=2000.0)
        row = dict(s.db.execute("SELECT * FROM sources WHERE id='bbc'").fetchone())
        self.assertEqual((row["active"], row["first_seen"], row["last_seen"], row["name"]),
                         (0, 1000.0, 1000.0, "BBC World"))
        row = dict(s.db.execute("SELECT * FROM sources WHERE id='elpais'").fetchone())
        self.assertEqual((row["active"], row["first_seen"], row["last_seen"]), (1, 1000.0, 2000.0))

    def test_sync_updates_changed_metadata(self):
        s = self.open()
        s.sync_sources(SOURCES, now=1000.0)
        changed = [dict(x, name="BBC News") if x["id"] == "bbc" else x for x in SOURCES]
        s.sync_sources(changed, now=2000.0)
        self.assertEqual(s.db.execute("SELECT name FROM sources WHERE id='bbc'").fetchone()[0], "BBC News")


class TestUpgradeFromV1(StoreCase):
    def test_v1_database_with_rows_upgrades(self):
        self.make_v1([("INSERT INTO items VALUES ('a1','elpais','T','https://example.com/a','S',1.0,2.0)", ())])
        s = self.open()
        self.assertEqual(s.db.execute("PRAGMA user_version").fetchone()[0], store_mod.schema_version())
        self.assertEqual(s.db.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)
        self.assertIn("lang", [r[1] for r in s.db.execute("PRAGMA table_info(items)")])

    def test_failed_migration_rolls_back(self):
        self.open().db.close()
        self.store = None

        def boom(db):
            db.execute("CREATE TABLE half_done (x)")
            raise RuntimeError("boom")

        store_mod.MIGRATIONS.append((999, boom))
        try:
            with self.assertRaises(RuntimeError):
                Store(self.path)
        finally:
            store_mod.MIGRATIONS.remove((999, boom))
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], store_mod.schema_version())
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='half_done'").fetchone())
        finally:
            db.close()

    def test_existing_text_is_renormalized(self):
        self.make_v1([
            ("INSERT INTO items VALUES ('a1','elpais',?,?,?,1.0,2.0)",
             ("Café a​b", "https://example.com/a", "growth <2% and ­jobs >3%")),
            ("INSERT INTO saved (id, title, summary, note) VALUES ('a1', ?, ?, ?)",
             ("Café", "x﻿y", "my​note")),
        ])
        s = self.open()
        title, summary = s.db.execute("SELECT title, summary FROM items").fetchone()
        self.assertEqual(title, "Café ab")
        self.assertEqual(summary, "growth <2% and jobs >3%")          # angle brackets survive
        title, summary, note = s.db.execute("SELECT title, summary, note FROM saved").fetchone()
        self.assertEqual((title, summary), ("Café", "xy"))
        self.assertEqual(note, "my​note")                        # the user's own notes are not touched
        self.assertEqual(s.db.execute("SELECT id FROM items").fetchone()[0], "a1")


class TestTruncatedFlag(ApiCase):
    def test_items_reports_truncation(self):
        for i in range(3):
            self.add("elpais", "T%d" % i, "https://example.com/%d" % i)
        _, body = self.get("/api/items?since=0")
        self.assertIs(body["truncated"], False)
        self.assertEqual(body["items"][0]["lang"], "es")
        self.app.live_row_limit = 2
        _, body = self.get("/api/items?since=0")
        self.assertIs(body["truncated"], True)
        self.assertEqual(len(body["items"]), 2)


if __name__ == "__main__":
    unittest.main()
