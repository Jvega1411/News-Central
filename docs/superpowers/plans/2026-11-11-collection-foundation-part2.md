# Collection Foundation Implementation Plan, part 2 of 3

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Tasks 6 to 9: permanent archive, renormalization, fetch runs and the Collection block. Read the header, ground rules, deviations and file structure in `2026-11-11-collection-foundation.md` first. Tasks 1 to 5 must be merged before these start.

---

### Task 6: Permanent archive, source snapshot and `items.lang`

**Files:**
- Modify: `newscentral/store.py`, `newscentral/fetcher.py`, `newscentral/api.py`, `newscentral/config.py`, `tests/helpers.py`
- Create: `tests/test_archive.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

`test_failed_migration_rolls_back` leaves a `Store` half-constructed with an open connection when the migration raises. To make the temporary folder removable on Windows, `Store.__init__` must close its connection before re-raising (Step 4 shows this).

- [ ] **Step 2: Run them**

Run: `py -m unittest tests.test_archive`
Expected: failures and errors (`Store` still has `prune`, `sync_sources` does not exist, `items()` returns a list).

- [ ] **Step 3: Make migrations transactional and add migration 2 in `newscentral/store.py`**

```python
def run_migrations(db, lock):
    """Apply pending migrations in order, each in its own transaction.
    Migrations must not call executescript(): it commits implicitly."""
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


@migration(2)
def _m2_sources_snapshot_and_item_lang(db):
    db.execute("""CREATE TABLE sources (
        id TEXT PRIMARY KEY, name TEXT, region TEXT, country_iso TEXT, scope TEXT,
        exile INTEGER DEFAULT 0, covers TEXT, lang TEXT, kind TEXT,
        state_media INTEGER DEFAULT 0, paywall INTEGER DEFAULT 0,
        first_seen REAL, last_seen REAL, active INTEGER DEFAULT 1)""")
    db.execute("ALTER TABLE items ADD COLUMN lang TEXT")
    db.execute("CREATE INDEX items_source_seen ON items(source_id, first_seen)")
```

- [ ] **Step 4: Change `Store`**

In `Store.__init__`, close the connection if a migration fails:

```python
        try:
            run_migrations(self.db, self.lock)
        except Exception:
            self.db.close()
            raise
```

Delete the `prune` method. Replace `add_items` and `items`, and add `sync_sources`:

```python
    def add_items(self, source_id, items, lang=None):
        now = time.time()
        rows = [(hashlib.sha1(it["link"].encode("utf-8")).hexdigest()[:16], source_id, it["title"],
                 it["link"], it.get("summary", ""), it.get("published"), now, lang) for it in items]
        with self.lock:
            before = self.db.total_changes
            self.db.executemany(
                "INSERT OR IGNORE INTO items (id, source_id, title, link, summary, published, first_seen, lang) "
                "VALUES (?,?,?,?,?,?,?,?)", rows)
            self.db.commit()
            return self.db.total_changes - before

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
```

`INSERT OR IGNORE` followed by `UPDATE` is used instead of `ON CONFLICT DO UPDATE` because upsert needs SQLite 3.24 and Python 3.8 on older systems can ship an older library.

- [ ] **Step 5: Fetcher: no prune, language on insert, snapshot on change**

In `newscentral/fetcher.py`:

- Delete the line `self.store.prune(time.time() - KEEP_HOURS * 3600)` from `cycle`.
- Change the insert to `new_total += self.store.add_items(s["id"], result["items"], s.get("lang"))`.
- Add `self._synced_mtime = None` to `__init__` before `self.reload_sources()`.
- Replace `reload_sources`:

```python
    def reload_sources(self):
        try:
            self.sources = load_sources()
            self.sources_error = ""
            mtime = self.sources_mtime()
            if mtime != self._synced_mtime:      # first load, or sources.json edited
                self.store.sync_sources(self.sources)
                self._synced_mtime = mtime
        except Exception as e:
            self.sources_error = "Could not read sources.json: %s" % e
        return self.sources
```

- [ ] **Step 6: Rename the window constant and report truncation**

`newscentral/config.py`: replace the `KEEP_HOURS` line with:

```python
LIVE_WINDOW_HOURS = 96         # how far back the live feed and the map look; the archive keeps everything
LIVE_ROW_LIMIT = 30000         # rows per /api/items response; beyond it the response says "truncated"
```

Rename every `KEEP_HOURS` in `newscentral/api.py` to `LIVE_WINDOW_HOURS`. In `App.__init__` add `self.live_row_limit = LIVE_ROW_LIMIT`. Then:

```python
def items_payload(app, since):
    """Returns (items, truncated)."""
    min_time = time.time() - LIVE_WINDOW_HOURS * 3600
    known = app.fetcher.source_map()
    rows, truncated = app.store.items(max(since, min_time - 3600), app.live_row_limit)
    out = []
    for r in rows:
        if r["source_id"] not in known:
            continue
        t = sort_time(r["published"], r["first_seen"])
        if t < min_time:
            continue
        out.append({"id": r["id"], "s": r["source_id"], "t": t, "fs": r["first_seen"],
                    "title": r["title"], "link": r["link"], "summary": r["summary"] or "",
                    "lang": r["lang"] or known[r["source_id"]].get("lang", "")})
    out.sort(key=lambda x: x["t"], reverse=True)
    return out, truncated
```

In `geo_payload`: `rows, _ = app.store.items(max(since, cutoff), app.live_row_limit)`.

In `Handler.do_GET`, the `/api/items` branch ends with:

```python
                payload = status_payload(app)
                payload["items"], payload["truncated"] = items_payload(app, since)
                self._json(payload)
```

- [ ] **Step 7: Update the test helper**

`tests/helpers.py`: `ApiCase.add` passes the language, and `setUp` snapshots the sources right after the store is created (`self.store.sync_sources(self.sources)`).

```python
    def add(self, source_id, title, link, summary="", published=None, lang=None):
        if lang is None:
            lang = self.fetcher.source_map().get(source_id, {}).get("lang")
        self.store.add_items(source_id, [{"title": title, "link": link, "summary": summary,
                                          "published": published}], lang)
```

- [ ] **Step 8: Run the tests**

Run: `py -m unittest discover -s tests`
Expected: `OK`, no failures or errors.

- [ ] **Step 9: Check on the real database**

Stop any running News Central first. Copy the live database so the check cannot damage it: `cp data/news.db data/news.db.bak-task6`. Run `py server.py --port 8799` in the background for 60 seconds, stop it, then:

```bash
py -c "import sqlite3; d=sqlite3.connect('data/news.db'); print(d.execute('pragma user_version').fetchone(), d.execute('select count(*) from sources').fetchone(), d.execute('select count(*) from items where lang is null').fetchone(), d.execute('select lang, count(*) from items group by 1').fetchall())"
```

Expected: `user_version` is 2, `sources` has about 143 rows, and the count of items with a null `lang` is 0 or only items of sources no longer in `sources.json`. Delete the `.bak-task6` copy when the numbers look right.

- [ ] **Step 10: Commit**

```bash
git add newscentral tests
git commit -m "Keep the full archive: drop pruning, snapshot sources, store language per item"
```

---

### Task 7: Renormalize stored text (migration 3)

**Files:**
- Modify: `newscentral/store.py`, `tests/test_archive.py`

- [ ] **Step 1: Write the failing test**

Add to `TestUpgradeFromV1` in `tests/test_archive.py`:

```python
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
        self.assertEqual(note, "my​note")                         # the user's own notes are not touched
        self.assertEqual(s.db.execute("SELECT id FROM items").fetchone()[0], "a1")
```

- [ ] **Step 2: Run it**

Run: `py -m unittest tests.test_archive.TestUpgradeFromV1`
Expected: FAIL on the title assertion (the stored title still has the combining accent and the zero-width space).

- [ ] **Step 3: Implement**

In `newscentral/store.py`, import `canonical` from `.text` and add:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `py -m unittest discover -s tests`
Expected: `OK`.

- [ ] **Step 5: Check on the real database**

Stop any running News Central. `cp data/news.db data/news.db.bak-task7`, start and stop the server as in Task 6, then save this as a file in the scratchpad (not in the repository) and run it with `py`:

```python
import re, sqlite3, unicodedata
db = sqlite3.connect("data/news.db")
rows = db.execute("SELECT title, summary FROM items").fetchall()
invisible = re.compile("[​‎‏­﻿⁠‪-‮]")
joined = [(t or "") + (s or "") for t, s in rows]
print(len(rows), sum(1 for x in joined if invisible.search(x)),
      sum(1 for x in joined if unicodedata.normalize("NFC", x) != x))
```

Expected: the row count is unchanged and the two problem counts are `0 0` (they were 182 and 2 on 2026-11-11). Delete the backup copy afterwards.

- [ ] **Step 6: Commit**

```bash
git add newscentral/store.py tests/test_archive.py
git commit -m "Renormalize stored titles and summaries to the text standard"
```

---

### Task 8: Fetch runs, gaps and volume

**Files:**
- Create: `newscentral/stats.py`, `tests/test_stats.py`
- Modify: `newscentral/store.py`, `newscentral/fetcher.py`, `newscentral/api.py`, `tests/test_api.py`

- [ ] **Step 1: Write the failing tests for the pure functions**

```python
# tests/test_stats.py
import unittest

from newscentral import stats

I = 900  # 15 minutes


class TestGaps(unittest.TestCase):
    def test_steady_collection_has_no_gaps(self):
        starts = [i * I for i in range(0, 11)]
        out = stats.gaps(starts, I, since=0, now=10 * I)
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["longest"], 0)
        self.assertEqual(out["uptime"], 1.0)

    def test_exactly_twice_the_interval_is_not_a_gap(self):
        out = stats.gaps([0, 2 * I], I, since=0, now=2 * I)
        self.assertEqual(out["gaps"], [])

    def test_gap_between_runs(self):
        out = stats.gaps([0, I, 5 * I, 6 * I], I, since=0, now=6 * I)
        self.assertEqual(out["gaps"], [{"from": I, "to": 5 * I, "seconds": 4 * I}])
        self.assertEqual(out["longest"], 4 * I)
        self.assertAlmostEqual(out["uptime"], 1 - (3 * I) / (6 * I))

    def test_gap_at_the_end_counts(self):
        out = stats.gaps([0, I], I, since=0, now=10 * I)
        self.assertEqual(out["gaps"], [{"from": I, "to": 10 * I, "seconds": 9 * I}])

    def test_window_starts_at_the_first_run_ever(self):
        # collection began at t=1000*I; the window must not count the time before it
        starts = [1000 * I, 1001 * I, 1002 * I]
        out = stats.gaps(starts, I, since=0, now=1002 * I)
        self.assertEqual(out["gaps"], [])
        self.assertEqual(out["uptime"], 1.0)

    def test_older_run_means_the_window_starts_at_since(self):
        # a run before the window proves collection had begun, so silence inside the window is a gap
        out = stats.gaps([0, 10 * I], I, since=5 * I, now=10 * I)
        self.assertEqual(out["gaps"], [{"from": 5 * I, "to": 10 * I, "seconds": 5 * I}])

    def test_no_runs(self):
        out = stats.gaps([], I, since=0, now=100)
        self.assertEqual(out, {"gaps": [], "longest": 0, "uptime": None})


class TestVolume(unittest.TestCase):
    def test_formats_days_as_utc_dates(self):
        rows = [(20767, "es", 120, 30000), (20767, "ar", 40, 9000), (20766, "", 3, None)]
        self.assertEqual(stats.volume(rows), [
            {"day": "2026-11-10", "lang": "es", "items": 120, "chars": 30000},
            {"day": "2026-11-10", "lang": "ar", "items": 40, "chars": 9000},
            {"day": "2026-11-09", "lang": "", "items": 3, "chars": 0},
        ])


if __name__ == "__main__":
    unittest.main()
```

Day 20767 is 2026-11-10 (verified: `(date(2026,11,10) - date(1970,1,1)).days`).

- [ ] **Step 2: Run them**

Run: `py -m unittest tests.test_stats`
Expected: `ModuleNotFoundError: No module named 'newscentral.stats'`.

- [ ] **Step 3: Implement `newscentral/stats.py`**

```python
# newscentral/stats.py
# Collection health numbers. Pure functions: no database, no clock.
from datetime import datetime, timedelta


def gaps(starts, interval, since, now):
    """starts: fetch-run start times, ascending: every run from `since`, preceded by the first
    run ever when that one is older (Store.run_starts returns exactly this).
    A gap is a stretch longer than twice the interval with no run starting.
    Uptime is the share of the window not lost to gaps; None when nothing was ever collected."""
    if not starts:
        return {"gaps": [], "longest": 0, "uptime": None}
    begin = since if starts[0] < since else starts[0]   # never count time before collection began
    points = [begin] + [t for t in starts if t > begin] + [now]
    found = []
    for a, b in zip(points, points[1:]):
        if b - a > 2 * interval:
            found.append({"from": a, "to": b, "seconds": b - a})
    span = max(now - begin, 1)
    lost = sum(g["seconds"] - interval for g in found)
    return {"gaps": found, "longest": max((g["seconds"] for g in found), default=0),
            "uptime": max(0.0, 1.0 - lost / span)}


def volume(rows):
    """rows: (days since 1970-01-01 UTC, lang, item count, character count)."""
    epoch = datetime(1970, 1, 1)
    return [{"day": (epoch + timedelta(days=int(d))).strftime("%Y-%m-%d"), "lang": lang or "",
             "items": items, "chars": chars or 0} for d, lang, items, chars in rows]
```

- [ ] **Step 4: Run them**

Run: `py -m unittest tests.test_stats`
Expected: `OK`.

- [ ] **Step 5: Storage for runs (migration 4) and the volume query**

`newscentral/store.py`:

```python
@migration(4)
def _m4_fetch_runs(db):
    db.execute("""CREATE TABLE fetch_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL NOT NULL, finished REAL,
        sources_checked INTEGER, new_items INTEGER, errors INTEGER)""")
    db.execute("CREATE INDEX fetch_runs_started ON fetch_runs(started)")
```

Methods on `Store`:

```python
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
```

- [ ] **Step 6: Record each cycle in `newscentral/fetcher.py`**

In `cycle`: take `started = time.time()` as the first line inside the `try`; initialise `errors = 0` next to `new_total = 0`; inside the loop, after `result` is known, add `if result["status"] == "error": errors += 1`; and after `self.last_cycle = time.time()` add:

```python
            self.store.add_run(started, self.last_cycle, len(todo), new_total, errors)
```

- [ ] **Step 7: The endpoint, tests first**

Add to `tests/test_api.py` (and `import time` at the top of the file):

```python
    def test_stats_empty(self):
        status, body = self.get("/api/stats")
        self.assertEqual(status, 200)
        self.assertIsNone(body["uptime_7d"])
        self.assertEqual(body["gaps"], [])
        self.assertEqual(body["volume"], [])
        self.assertEqual(body["read_langs"], ["es", "en"])

    def test_stats_reports_runs_and_volume(self):
        now = time.time()
        for k in range(8, 0, -1):
            self.store.add_run(now - k * 900, now - k * 900 + 30, 5, 10, 0)
        self.add("elpais", "Hola", "https://example.com/a", "mundo")
        self.add("ahram", "الأخبار", "https://example.com/b")
        _, body = self.get("/api/stats?days=30")
        self.assertEqual(body["uptime_7d"], 1.0)
        self.assertEqual(body["longest_gap"], 0)
        by_lang = {v["lang"]: v for v in body["volume"]}
        self.assertEqual(by_lang["es"]["items"], 1)
        self.assertEqual(by_lang["es"]["chars"], len("Hola") + len("mundo"))
        self.assertEqual(by_lang["ar"]["items"], 1)

    def test_stats_rejects_bad_days(self):
        self.assertEqual(self.get("/api/stats?days=abc")[0], 400)
        self.assertEqual(self.get("/api/stats?days=0")[0], 400)
```

Run: `py -m unittest tests.test_api`
Expected: the three new tests fail with status 404.

`newscentral/api.py` (import `gaps` and `volume` from `.stats`):

```python
def stats_payload(app, days, now=None):
    now = time.time() if now is None else now
    interval = app.config["refresh_minutes"] * 60
    wide = gaps(app.store.run_starts(now - days * 86400), interval, now - days * 86400, now)
    week = gaps(app.store.run_starts(now - 7 * 86400), interval, now - 7 * 86400, now)
    return {"uptime_7d": week["uptime"], "uptime_30d": wide["uptime"], "days": days,
            "gaps": wide["gaps"], "longest_gap": wide["longest"],
            "volume": volume(app.store.volume_rows(now - days * 86400)),
            "read_langs": app.config["read_langs"]}
```

The key `uptime_30d` holds the uptime over the requested `days`; the reader always asks for 30.

In `Handler.do_GET`, before the final `else`:

```python
            elif path == "/api/stats":
                try:
                    days = int((q.get("days") or ["30"])[0])
                except ValueError:
                    days = 0
                if not 1 <= days <= 3650:
                    return self._json({"error": "days must be between 1 and 3650"}, 400)
                self._json(stats_payload(app, days))
```

- [ ] **Step 8: Run the tests**

Run: `py -m unittest discover -s tests`
Expected: `OK`.

- [ ] **Step 9: Commit**

```bash
git add newscentral tests
git commit -m "Record fetch runs and expose uptime, gaps and volume by language"
```

---

### Task 9: Collection block in the Sources view

**Files:**
- Modify: `static/js/api.js`, `static/js/reader.js`, `static/index.html`

- [ ] **Step 1: API wrapper**

Append to `static/js/api.js`:

```javascript
export function fetchStats(days) {
  return getJSON('/api/stats?days=' + (days || 30));
}
```

- [ ] **Step 2: Load and render in `static/js/reader.js`**

Add `fetchStats` to the import from `./api.js`. Add `stats: null` to the `S` object. Add these functions above `renderSources`:

```javascript
function pct(x) { return x === null || x === undefined ? 'no data' : (Math.round(x * 1000) / 10) + '%'; }

function duration(seconds) {
  var m = Math.round(seconds / 60);
  if (m < 60) return m + ' min';
  var h = Math.floor(m / 60);
  return h < 48 ? h + ' h ' + (m % 60) + ' min' : Math.round(h / 24) + ' days';
}

function collectionHTML() {
  var st = S.stats;
  if (!st) return '';
  var read = st.read_langs || [];
  var days = {}, order = [];
  st.volume.forEach(function (v) {
    if (!days[v.day]) { days[v.day] = { read: 0, other: 0, chars: 0, langs: [] }; order.push(v.day); }
    var d = days[v.day];
    if (read.indexOf(v.lang) !== -1) {
      d.read += v.items;
    } else {
      d.other += v.items; d.chars += v.chars;
      d.langs.push((v.lang || 'unknown') + ' ' + v.items);
    }
  });
  var h = '<h2>Collection</h2><div class="toolbar">Uptime last 7 days: ' + esc(pct(st.uptime_7d)) +
    ' · last 30 days: ' + esc(pct(st.uptime_30d)) + ' · ' + st.gaps.length + ' gap' + (st.gaps.length === 1 ? '' : 's') +
    (st.longest_gap ? ' · longest ' + esc(duration(st.longest_gap)) : '') + '</div>';
  h += '<table class="volume"><thead><tr><th>Day (UTC)</th><th>Read (' + esc(read.join(', ')) +
    ')</th><th>Other languages</th><th>Characters to translate</th><th>Breakdown</th></tr></thead><tbody>';
  h += order.slice(0, 14).map(function (day) {
    var d = days[day];
    return '<tr><td>' + esc(day) + '</td><td class="count">' + d.read + '</td><td class="count">' + d.other +
      '</td><td class="count">' + d.chars.toLocaleString() + '</td><td class="detail">' + esc(d.langs.join(' · ')) + '</td></tr>';
  }).join('');
  return h + '</tbody></table><h2>Sources</h2>';
}
```

In `renderSources`, change the last line to:

```javascript
  $('#sources').innerHTML = collectionHTML() + h + '</tbody></table>';
```

In `setView`, replace the line `if (v === 'sources') renderSources();` with:

```javascript
  if (v === 'sources') {
    renderSources();
    fetchStats(30).then(function (d) { S.stats = d; if (S.view === 'sources') renderSources(); })
      .catch(function () { toast('Server not reachable'); });
  }
```

- [ ] **Step 3: Styles in `static/index.html`**

Add after the `.map-legend .pf-src` rule:

```css
#sources h2 { font-size: 17px; margin: 18px 0 6px; }
table.volume td.count { text-align: right; }
```

`td.count` already gets tabular numbers from the `.count` selector in the editorial block.

- [ ] **Step 4: Verify in a browser**

Run: `node --check static/js/reader.js`. Then search both files for `??` and `?.` (expected: none in code).

Start `py server.py --port 8799`, open `http://127.0.0.1:8799/`, click Sources. Expected: a "Collection" heading, an uptime line, a table with one row per day and non-zero numbers in the "Read" column, then the existing sources table under a "Sources" heading. The console shows no errors. Stop the server.

Note for whoever reads the numbers: the first fetch of a new source stores its whole backlog (up to 60 items) on one day, so the day a batch of sources is added overstates the steady daily volume.

- [ ] **Step 5: Commit**

```bash
git add static
git commit -m "Show collection uptime, gaps and volume by language in the Sources view"
```
