import gzip
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

from newscentral import backup, config
from newscentral.store import Store

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import restore_backup  # noqa: E402

DAY = 86400
TODAY = 1700000000 // DAY * DAY                 # 00:00 UTC of "today"
NOW = TODAY + 5 * 3600
SRC = {"id": "elpais", "name": "El País", "region": "Spain", "country_iso": "ES", "lang": "es", "kind": "news"}
TITLE = "Año nuevo — 世界"


def read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return f.read()


class BackupCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.dir, "data", "news.db")
        self.out = os.path.join(self.dir, "backup")
        self.store = Store(self.db_path)
        self.store.sync_sources([SRC])

    def tearDown(self):
        self.store.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def put(self, n, first_seen, title="Titular"):
        self.store.db.execute(
            "INSERT INTO items (id, source_id, title, link, summary, published, first_seen, lang) "
            "VALUES (?,?,?,?,?,?,?,'es')",
            ("id%d" % n, "elpais", title, "https://example.com/%d" % n, "Resumen", first_seen, first_seen))
        self.store.db.commit()

    def seed(self):
        self.put(1, TODAY - 3 * DAY + 100, TITLE)
        self.put(2, TODAY - 3 * DAY + 50)
        self.put(3, TODAY - 2 * DAY + 10)
        self.put(4, TODAY - 1 * DAY + 10)
        self.put(5, TODAY + 10)                    # today: not exported yet

    def day_files(self):
        found = []
        for root, _, names in os.walk(os.path.join(self.out, "items")):
            found += [os.path.join(root, n) for n in names]
        return sorted(found)

    def snapshot(self):
        out = {}
        for f in self.day_files():
            with open(f, "rb") as fh:
                out[f] = (os.path.getmtime(f), fh.read())
        return out

    def test_complete_days_exported_not_today(self):
        self.seed()
        summary = backup.run_backup(self.db_path, self.out, NOW)
        files = self.day_files()
        self.assertEqual(len(files), 3)
        self.assertTrue(all(f.endswith(".ndjson.gz") for f in files))
        self.assertEqual(summary["written"]["items"], 4)
        ids = [json.loads(line)["id"] for f in files for line in read_gz(f).splitlines()]
        self.assertEqual(ids, ["id2", "id1", "id3", "id4"])       # time order, id5 absent
        self.assertTrue(os.path.isfile(os.path.join(self.out, "backup.json")))

    def test_second_run_writes_nothing_and_keeps_files(self):
        self.seed()
        backup.run_backup(self.db_path, self.out, NOW)
        before = self.snapshot()
        summary = backup.run_backup(self.db_path, self.out, NOW + 60)
        self.assertEqual(summary["written"]["items"], 0)
        self.assertEqual(before, self.snapshot())

    def test_new_row_on_exported_day_does_not_change_file(self):
        self.seed()
        backup.run_backup(self.db_path, self.out, NOW)
        before = self.snapshot()
        self.put(9, TODAY - 2 * DAY + 500)
        backup.run_backup(self.db_path, self.out, NOW + 60)
        self.assertEqual(before, self.snapshot())

    def test_tomorrow_exports_today(self):
        self.seed()
        backup.run_backup(self.db_path, self.out, NOW)
        summary = backup.run_backup(self.db_path, self.out, NOW + DAY)
        self.assertEqual(summary["written"]["items"], 1)
        self.assertEqual(len(self.day_files()), 4)

    def test_state_retention_keeps_14(self):
        self.seed()
        for i in range(20):
            backup.run_backup(self.db_path, self.out, NOW + i * DAY)
        sdir = os.path.join(self.out, "state")
        names = sorted(os.listdir(sdir))
        self.assertEqual(len(names), 14)
        self.assertEqual(names[-1], "state-%s.json.gz" % backup.utc_date(NOW + 19 * DAY))
        open(os.path.join(sdir, "notes.txt"), "w").close()         # a foreign file is never deleted
        backup.run_backup(self.db_path, self.out, NOW + 20 * DAY)
        self.assertIn("notes.txt", os.listdir(sdir))

    def test_unicode_round_trip(self):
        self.seed()
        backup.run_backup(self.db_path, self.out, NOW)
        text = "".join(read_gz(f) for f in self.day_files())
        self.assertIn(TITLE, text)                                   # characters, not escapes
        self.assertEqual(json.loads(text.splitlines()[1])["title"], TITLE)

    def test_restore_reproduces_items_saved_and_search(self):
        self.seed()
        self.store.save(self.store.get_item("id1"), SRC)
        self.store.update_saved("id1", note="nota útil", digested=True)
        backup.run_backup(self.db_path, self.out, NOW)
        new_db = os.path.join(self.dir, "restored", "news.db")
        counts = restore_backup.restore(self.out, new_db)
        self.assertEqual(counts["items"], 4)
        new = Store(new_db)
        try:
            old_rows = [dict(r) for r in self.store.db.execute(
                "SELECT * FROM items WHERE first_seen < ? ORDER BY id", (TODAY,))]
            new_rows = [dict(r) for r in new.db.execute("SELECT * FROM items ORDER BY id")]
            self.assertEqual(new_rows, old_rows)
            self.assertEqual(new.saved(), self.store.saved())
            self.assertEqual(new.saved()[0]["note"], "nota útil")
            self.assertEqual(new.db.execute("PRAGMA user_version").fetchone()[0],
                             self.store.db.execute("PRAGMA user_version").fetchone()[0])
            rows, total, engine = new.search("nuevo")
            self.assertEqual(engine, "fts")
            self.assertEqual([r["id"] for r in rows], ["id1"])
        finally:
            new.db.close()

    def test_translations_are_backed_up_by_day_and_restored(self):
        self.seed()
        created = TODAY - 2 * DAY + 77
        self.store.db.execute(
            "INSERT INTO translations (item_id, field, lang, text, engine, created) VALUES (?,?,?,?,?,?)",
            ("id1", "title", "es", TITLE, "fake", created))
        self.store.db.execute(
            "INSERT INTO translations (item_id, field, lang, text, engine, created) VALUES (?,?,?,?,?,?)",
            ("id5", "title", "es", "hoy", "fake", TODAY + 5))            # today: not exported yet
        self.store.db.commit()
        summary = backup.run_backup(self.db_path, self.out, NOW)
        self.assertEqual(summary["written"]["translations"], 1)
        path = os.path.join(self.out, "translations", backup.utc_date(created)[:4],
                            backup.utc_date(created) + ".ndjson.gz")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(json.loads(read_gz(path))["text"], TITLE)
        new_db = os.path.join(self.dir, "restored", "news.db")
        counts = restore_backup.restore(self.out, new_db)
        self.assertEqual(counts["translations"], 1)
        new = Store(new_db)
        try:
            rows = [tuple(r) for r in new.db.execute(
                "SELECT item_id, field, lang, text, engine, created FROM translations")]
            self.assertEqual(rows, [("id1", "title", "es", TITLE, "fake", created)])
        finally:
            new.db.close()

    def test_restore_refuses_existing_target(self):
        self.seed()
        backup.run_backup(self.db_path, self.out, NOW)
        with self.assertRaises(SystemExit):
            restore_backup.restore(self.out, self.db_path)

    def test_missing_tables_are_skipped(self):
        path = os.path.join(self.dir, "bare.db")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE items (id TEXT, first_seen REAL)")
        db.execute("INSERT INTO items VALUES ('x', ?)", (TODAY - DAY + 1,))
        db.commit()
        db.close()
        summary = backup.run_backup(path, os.path.join(self.dir, "b2"), NOW)
        self.assertEqual(summary["written"], {"items": 1})


class TestBackupConfig(unittest.TestCase):
    def test_disabled_by_default(self):
        cfg = config.load_config(os.path.join(tempfile.gettempdir(), "nc-no-such-config.json"))
        self.assertIsNone(cfg["backup_dir"])
        self.assertIsNone(config.paths(cfg)["backup"])

    def test_resolution(self):
        cfg = dict(config.DEFAULTS, backup_dir="bk/nc")
        self.assertEqual(config.paths(cfg)["backup"], os.path.join(config.BASE_DIR, "bk", "nc"))
        absolute = os.path.abspath(os.path.join(tempfile.gettempdir(), "bk"))
        cfg = dict(config.DEFAULTS, backup_dir=absolute)
        self.assertEqual(config.paths(cfg)["backup"], absolute)

    def test_validation(self):
        config._check("backup_dir", None)
        config._check("backup_dir", "x")
        for bad in ("", 5, ["a"], True):
            with self.assertRaises(config.ConfigError):
                config._check("backup_dir", bad)

    def test_fetcher_without_backup_dir_does_nothing(self):
        from newscentral.fetcher import Fetcher
        f = Fetcher.__new__(Fetcher)
        f.backup_dir = None
        f.maybe_backup()        # must not raise


if __name__ == "__main__":
    unittest.main()
