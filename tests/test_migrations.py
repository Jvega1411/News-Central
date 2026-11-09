import os, sqlite3, tempfile, unittest
import server

class TestMigrations(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "news.db")

    def test_fresh_db_reaches_latest_version(self):
        store = server.Store(self.path)
        v = store.db.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(v, server.SCHEMA_VERSION)

    def test_saved_has_country_iso_column(self):
        store = server.Store(self.path)
        cols = [r[1] for r in store.db.execute("PRAGMA table_info(saved)")]
        self.assertIn("country_iso", cols)

    def test_migration_is_idempotent(self):
        server.Store(self.path)            # first open runs migrations
        store2 = server.Store(self.path)   # second open must not fail or duplicate
        v = store2.db.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(v, server.SCHEMA_VERSION)

if __name__ == "__main__":
    unittest.main()
