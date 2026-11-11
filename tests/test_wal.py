import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

from newscentral import store as store_mod
from newscentral.store import SchemaMismatch, Store


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "news.db")
        self.opened = []

    def tearDown(self):
        for s in self.opened:
            try:
                s.db.close()
            except Exception:
                pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def open(self, **kw):
        s = Store(self.path, **kw)
        self.opened.append(s)
        return s

    def set_version(self, version):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA user_version = %d" % version)
        db.commit()
        db.close()


class TestJournal(StoreCase):
    def test_wal_is_persistent_in_the_file(self):
        s = self.open()
        self.assertEqual(s.journal_mode, "wal")
        raw = sqlite3.connect(self.path)
        try:
            self.assertEqual(raw.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            raw.close()

    def test_busy_timeout_is_five_seconds(self):
        s = self.open()
        self.assertEqual(s.db.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        w = self.open(migrate=False)
        self.assertEqual(w.db.execute("PRAGMA busy_timeout").fetchone()[0], 5000)


class TestMigration6(StoreCase):
    def test_tables_and_columns(self):
        s = self.open()
        def cols(table):
            return [r[1] for r in s.db.execute("PRAGMA table_info(%s)" % table)]
        self.assertEqual(cols("translations"), ["item_id", "field", "lang", "text", "engine", "created"])
        self.assertEqual(cols("translation_failures"),
                         ["item_id", "field", "lang", "attempts", "last_error", "last_try"])
        self.assertEqual(cols("worker_status"),
                         ["job", "state", "last_batch", "done_last_hour", "last_error", "updated"])
        indexes = {r[0] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("items_lang_seen", indexes)
        self.assertIn("translations_created", indexes)

    def test_field_is_title_or_summary(self):
        s = self.open()
        with self.assertRaises(sqlite3.IntegrityError):
            s.db.execute("INSERT INTO translations VALUES ('x', 'body', 'es', 't', 'fake', 1)")


class TestOpenWithoutMigrating(StoreCase):
    def test_missing_file_is_not_created(self):
        with self.assertRaises(FileNotFoundError):
            Store(self.path, migrate=False)
        self.assertFalse(os.path.exists(self.path))

    def test_matching_version_opens(self):
        self.open()
        w = self.open(migrate=False)
        self.assertEqual(w.db.execute("PRAGMA user_version").fetchone()[0], store_mod.schema_version())

    def test_newer_and_older_versions_are_refused(self):
        self.open().db.close()
        for version in (store_mod.schema_version() + 1, store_mod.schema_version() - 1):
            self.set_version(version)
            with self.assertRaises(SchemaMismatch) as cm:
                Store(self.path, migrate=False)
            self.assertIn(str(version), str(cm.exception))

    def test_no_table_is_created_and_no_migration_runs(self):
        self.open().db.close()
        self.set_version(store_mod.schema_version())
        before = sqlite3.connect(self.path)
        names_before = sorted(r[0] for r in before.execute("SELECT name FROM sqlite_master"))
        before.close()
        self.open(migrate=False)
        after = sqlite3.connect(self.path)
        names_after = sorted(r[0] for r in after.execute("SELECT name FROM sqlite_master"))
        after.close()
        self.assertEqual(names_before, names_after)


class TestWorkerStatus(StoreCase):
    def test_round_trip_and_replace(self):
        s = self.open()
        s.set_worker_status("translate", "idle", None, 0, "", now=100.0)
        s.set_worker_status("translate", "running", 150.0, 12, "", now=150.0)
        s.set_worker_status("embed", "unavailable", None, 0, "x" * 900, now=160.0)
        rows = s.worker_status()
        self.assertEqual([r["job"] for r in rows], ["embed", "translate"])
        self.assertEqual(rows[1]["state"], "running")
        self.assertEqual(rows[1]["done_last_hour"], 12)
        self.assertEqual(rows[1]["last_batch"], 150.0)
        self.assertEqual(len(rows[0]["last_error"]), 500)


class TestTwoConnections(StoreCase):
    def test_writers_and_a_reader_never_see_locked(self):
        server = self.open()
        worker = self.open(migrate=False)
        errors = []
        done = threading.Event()

        def write_items():
            try:
                for n in range(150):
                    server.add_items("bbc", [{"title": "t%d" % n, "link": "https://example.com/%d" % n}], "en")
            except Exception as e:
                errors.append(e)
            finally:
                done.set()

        def write_status():
            try:
                n = 0
                while not done.is_set():
                    worker.set_worker_status("translate", "running", time.time(), n, "")
                    n += 1
            except Exception as e:
                errors.append(e)

        def read():
            reader = sqlite3.connect(self.path, timeout=5)
            try:
                while not done.is_set():
                    reader.execute("SELECT COUNT(*) FROM items").fetchone()
            except Exception as e:
                errors.append(e)
            finally:
                reader.close()

        threads = [threading.Thread(target=f) for f in (write_items, write_status, read)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(server.db.execute("SELECT COUNT(*) FROM items").fetchone()[0], 150)


if __name__ == "__main__":
    unittest.main()
