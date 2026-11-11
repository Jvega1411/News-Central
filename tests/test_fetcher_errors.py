import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from newscentral import fetcher as fetcher_mod
from newscentral.store import Store

SOURCES = [{"id": "a", "name": "A", "region": "Europe", "lang": "en", "feed": "http://x/a"},
           {"id": "b", "name": "B", "region": "Europe", "lang": "en", "feed": "http://x/b"}]


def fake_fetch(source, state):
    return {"feed": source["feed"], "status": "ok", "error": "",
            "items": [{"title": "t " + source["id"], "link": "http://x/" + source["id"]}]}


class FlakyStore(Store):
    """add_items fails once, like a locked database."""
    failed = False

    def add_items(self, *args, **kwargs):
        if not FlakyStore.failed:
            FlakyStore.failed = True
            raise sqlite3.OperationalError("database is locked")
        return super().add_items(*args, **kwargs)


class TestCycleSurvivesStoreError(unittest.TestCase):
    def test_cycle_records_run_after_write_error(self):
        d = tempfile.mkdtemp()
        store = None
        try:
            FlakyStore.failed = False
            store = FlakyStore(os.path.join(d, "news.db"))
            with mock.patch.object(fetcher_mod, "load_sources", lambda: list(SOURCES)), \
                    mock.patch.object(fetcher_mod, "fetch_source", fake_fetch), \
                    mock.patch.object(fetcher_mod.Fetcher, "maybe_backup", lambda self: None):
                f = fetcher_mod.Fetcher(store, status_file=os.path.join(d, "status.json"))
                f.cycle(force=True)
            self.assertTrue(os.path.exists(os.path.join(d, "status.json")))
            self.assertEqual(len(store.run_starts(0)), 1)
            self.assertEqual(len(store.states()), 1)   # the failing source is skipped, the other is stored
        finally:
            if store:
                store.db.close()
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
