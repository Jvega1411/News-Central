import os, shutil, tempfile, unittest
from newscentral import store

class TestMigrations(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "news.db")
        self.stores = []

    def tearDown(self):
        for s in self.stores:
            try:
                s.db.close()
            except Exception:
                pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def _store(self):
        s = store.Store(self.path)
        self.stores.append(s)
        return s

    def test_fresh_db_reaches_latest_version(self):
        st = self._store()
        v = st.db.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(v, store.schema_version())

    def test_saved_has_country_iso_column(self):
        st = self._store()
        cols = [r[1] for r in st.db.execute("PRAGMA table_info(saved)")]
        self.assertIn("country_iso", cols)

    def test_migration_is_idempotent(self):
        self._store()            # first open runs migrations
        store2 = self._store()   # second open must not fail or duplicate
        v = store2.db.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(v, store.schema_version())

if __name__ == "__main__":
    unittest.main()
