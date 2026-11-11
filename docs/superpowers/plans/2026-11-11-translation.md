# Translation (spec 2a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every item in a language the user does not read gets a Spanish title and summary from a local engine, in a supervised background worker, and the reader, the archive search, the saved view and the Markdown export show the translation with the original.

**Architecture:** The SQLite file switches to WAL so two processes can share it. A worker process (`python -m newscentral.worker`) runs batch jobs; the server starts it after binding its port and restarts it with backoff when it crashes. The first job, `translate`, reads a queue computed from `items` (titles first, newest first), calls an engine behind a small interface, and writes results and failures in one short transaction. Translations live in their own table (migration 6) with a second FTS5 index managed by `Store.ensure_fts()`. Engines (NLLB-200, M2M100, Opus-MT) wrap CTranslate2 and sentencepiece, imported lazily, so the package works without them. A benchmark tool and a review page choose the engine per source language.

**Tech Stack:** Python 3.8+ standard library for the core (`sqlite3`, `subprocess`, `threading`, `signal`, `urllib`, `unittest`), SQLite WAL and FTS5, optional CTranslate2 and sentencepiece (Python 3.10+ for their current wheels), vanilla ES modules.

**Spec:** `docs/superpowers/specs/2026-11-11-translation-design.md`

**Parts:** this file holds the shared rules and Tasks 1 to 4. Tasks 5 to 10 are in `2026-11-11-translation-part2.md`, Tasks 11 to 16 in `2026-11-11-translation-part3.md`. Tasks run in numeric order.

---

## Ground rules for every task

- Windows dev machine. Python is `py`. Run tests with `py -m unittest discover -s tests`. There is no pytest. Before Task 1, `main` (with the TLS-verification fix, `tests/test_ssl.py` and the extra countries and outlets) is merged into `feature/translation`; record the test count after that merge as the baseline (it was `Ran 140 tests`, `OK (skipped=1)` before it). Do not edit `make_ssl_context()`; Task 11 only reuses `SSL_CTX`.
- Python 3.8 compatible syntax only: no `match`, no `dict | dict`, no `list[str]` at runtime, no `str.removeprefix`, no walrus in new code. The optional engine dependencies need Python 3.10+, but every module must still import on 3.8 without them.
- Frontend: no `??`, no `?.`, `var` and function declarations as in the existing files, no `innerHTML` with unescaped data (use `esc()`), valid `dataset` keys only.
- Test sources are ASCII: any non-ASCII text in a test is written as `\uXXXX` escapes.
- Never commit anything under `data/`, including `data/models/` and `data/bench/`, and never commit `config.json`. Stage files by name; never `git add -A`.
- The worker and the server are two processes on one SQLite file. The worker never runs migrations, never creates tables, holds no transaction while an engine runs, and writes each batch in one short transaction.
- Real engines and model downloads are never used in unit tests. Tests use the `fake` engine, fake jobs and local HTTP servers on `127.0.0.1`.
- This machine has live internet. After fetcher changes run `py server.py --check`.
- After any frontend change load the page in a real browser (the orchestrator uses headless Edge) and check the console. `node --check` does not catch DOM runtime errors.
- One logical change per commit, imperative subject, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Text files use LF (`.gitattributes` enforces it).

## Deviations from the spec, decided while planning

Task 15 amends the spec to match these:

1. **Translated text goes through `text.canonical`, not `text.normalize`.** Engine output is plain text, not markup. `normalize` runs the tag stripper, which would delete legitimate text such as `inflación <2%`; the same reasoning made migration 3 use only the Unicode steps. The input is already normalized, since it comes from `items`.
2. **`/api/items` gains `tr_since`, `tr_seen` and `translated`.** The reader asks for new items by `first_seen` only (`fetchItems(S.maxSeen)`), so a translation finished after an item reached the page would never arrive. The response carries `tr_seen` (newest translation time) and, when the client sends `tr_since`, a map of translations created since then for items in the live window.
3. **The job interface has a fourth method, `save(store, batch, results)`, and `available()` sets `job.reason`.** The spec lists `name`, `available()`, `pending()` and `run()`; the worker also needs each job to write its own tables in one transaction and to say why it is unavailable.
4. **Search through translations matches when every term is in the same translated field.** Each translation (title or summary) is its own row in `translations`, so a two-word query matches a translation only if both words are in the translated title or both in the translated summary. Matching on the original text is unchanged. Ranking merges both indexes by the best `bm25` score; an item matched both ways appears once.
5. **Opus-MT routes depend on which converted models exist.** Checked on the Hugging Face hub on 2026-11-11: ready CTranslate2 conversions of direct models into Spanish exist only for fr, de, ca and ar (`gaudi/opus-mt-<x>-es-ctranslate2`). Helsinki-NLP publishes direct models for it, tr, ja, ko, ru, uk and id but nobody publishes their conversions, and there is no direct model at all for pt, fa, zh, hi, ur and bn. By default `tools/get_models.py opus-mt` downloads converted models only and pivots through English for everything except fr, de, ca and ar; `--convert` converts the seven unconverted direct models locally (needs `transformers` and `torch`). pt pivots through `opus-mt-ROMANCE-en` and fa through `opus-mt-mul-en` because `opus-mt-pt-en` and `opus-mt-fa-en` do not exist. The `gaudi/opus-mt-en-es-ctranslate2` repository has no `model.bin`, so English to Spanish comes from `michaelfeil/ct2fast-opus-mt-en-es`.
6. **M2M100 is downloaded as float16 and run as int8.** The only well-known conversion, `michaelfeil/ct2fast-m2m100_418M`, is float16. CTranslate2 quantizes to int8 when the model is loaded with `compute_type="int8"`, so the engine runs int8 as the spec says.
7. **Wheels.** PyPI lists `cp314` `win_amd64` and `manylinux_2_28_aarch64` wheels for `ctranslate2` 4.9.0 and `sentencepiece` 0.2.3, so ARM64 wheels are published, not just assumed. They still have to be installed and run on the Pi. Both packages require Python 3.10 or newer (`sentencepiece` 0.2.3: `>=3.10`; `ctranslate2` 4.9.0: `>=3.9`), so translation needs Python 3.10+ while the core keeps 3.8.
8. **Additions the spec implies:** an index `items(lang, first_seen)` in migration 6 for the queue; a `fake` engine in the registry for development and browser checks; exit code 3 from the worker means "schema mismatch, do not restart"; the status payload says whether the worker is enabled (`worker_enabled`); the Collection line also counts items given up after three failures.
9. **Build order steps 8 and 9 swap.** The benchmark (step 8) waits for three days of batch 1b, so the documentation (step 9) is Task 15 and the benchmark hand-over is the last task, Task 16.

## File structure

```
newscentral/
  store.py                      + WAL, busy timeout, migrate=False opening, migration 6, worker_status,
                                  translation queue, translations_fts, search over translations
  config.py                     + worker, translate_batch, worker_threads, translate_engine, TARGET_LANG,
                                  worker_threads(), paths models/bench
  jobs.py                       Job base class (new)
  worker.py                     Worker loop, signal handling, entry point (new)
  supervisor.py                 Supervisor thread, next_delay(), EXIT_SCHEMA (new)
  cli.py                        + --no-worker, start_worker(), supervisor shutdown
  api.py                        + worker status, translation counts, tr fields, tr_since, export,
                                  bench review API, /bench/translate
  translate/
    __init__.py                 empty (new)
    base.py                     Engine interface, read_version(), strip_special() (new)
    fake.py                     FakeEngine for tests and development (new)
    registry.py                 engine names to classes, check(), create(), installed() (new)
    job.py                      TranslateJob, check_output() (new)
    ct2.py                      lazy CTranslate2 / sentencepiece loaders (new)
    nllb.py                     NLLB-200 distilled 600M engine (new)
    m2m100.py                   M2M100 418M engine (new)
    opus_mt.py                  Opus-MT engine with routes and pivots (new)
    selfcheck.py                translate three fixed sentences with one engine (new)
    download.py                 model catalogue, hub download, converter path (new)
    bench.py                    benchmark sampling, run, summary, review files (new)
tools/
  get_models.py                 CLI over translate/download.py (new)
  retranslate.py                delete one engine's translations, clear failures (new)
  bench_translate.py            CLI over translate/bench.py (new)
static/
  index.html                    + styles for original title, original summary, worker line
  bench.html                    benchmark review page (new)
  js/api.js                     + fetchItems(since, trSince)
  js/reader.js                  + translated display, filter, search text, worker and translation lines
  js/bench.js                   review page logic (new)
requirements-translate.txt      optional engine dependencies (new)
config.example.json             + new keys
tests/
  test_wal.py                   WAL, busy timeout, two connections, migrate=False, migration 6 (new)
  test_worker.py                worker loop with fake jobs, signals, schema refusal (new)
  test_supervisor.py            restarts, backoff, give-up, --no-worker (new)
  test_worker_status_api.py     worker status in /api/status (new)
  test_translate_queue.py       queue order, failures, transactions, counts (new)
  test_translate_job.py         TranslateJob with the fake engine, end to end through the worker (new)
  test_retranslate.py           clear_translations and the tool (new)
  test_translation_api.py       tr fields, tr_since, saved, export (new)
  test_engines.py               engine modules without their dependencies (new)
  test_get_models.py            download against a local fake hub, error messages, Opus-MT plan (new)
  test_bench.py                 sampling, run with the fake engine, summary (new)
  test_bench_api.py             review API and page (new)
  test_config.py, test_search.py   extended
```

Suggested models for subagent execution: Tasks 2, 5, 15 are mechanical (small model). Tasks 1, 3, 4, 6, 7, 8, 9, 10 need care with SQL, processes or DOM state (mid model). Tasks 11 and 12 involve the hub and real engines (mid model, with the downloads and self-checks run by hand), 13 and 14 the benchmark. Review every task before the next one starts.

---

### Task 1: WAL mode, busy timeout, migration 6 and `worker_status`

**Files:**
- Modify: `newscentral/store.py`
- Create: `tests/test_wal.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_wal.py
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
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_wal -v`
Expected: `ImportError: cannot import name 'SchemaMismatch' from 'newscentral.store'`.

- [ ] **Step 3: Add migration 6**

In `newscentral/store.py`, after `_m5_item_feed_tags` and before `def schema_version():`, insert:

```python
@migration(6)
def _m6_translations(db):
    db.execute("""CREATE TABLE translations (
        item_id TEXT NOT NULL, field TEXT NOT NULL CHECK (field IN ('title', 'summary')),
        lang TEXT NOT NULL, text TEXT NOT NULL, engine TEXT NOT NULL, created REAL NOT NULL,
        PRIMARY KEY (item_id, field, lang))""")
    db.execute("CREATE INDEX translations_created ON translations(created)")
    db.execute("CREATE INDEX translations_engine ON translations(engine)")
    db.execute("""CREATE TABLE translation_failures (
        item_id TEXT NOT NULL, field TEXT NOT NULL, lang TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, last_try REAL,
        PRIMARY KEY (item_id, field, lang))""")
    db.execute("""CREATE TABLE worker_status (
        job TEXT PRIMARY KEY, state TEXT, last_batch REAL, done_last_hour INTEGER DEFAULT 0,
        last_error TEXT, updated REAL)""")
    # The translation queue scans items of the languages the user does not read, newest first.
    db.execute("CREATE INDEX items_lang_seen ON items(lang, first_seen)")
```

- [ ] **Step 4: WAL, busy timeout and opening without migrating**

In `newscentral/store.py`, above `class Store:` add:

```python
BUSY_TIMEOUT_MS = 5000


class SchemaMismatch(Exception):
    """The database was migrated by a different version of the code than the one opening it."""
```

Replace the start of `Store.__init__`, from `def __init__(self, path, fts=True):` down to and including the line `self.db.row_factory = sqlite3.Row`, with:

```python
    def __init__(self, path, fts=True, migrate=True):
        """migrate=False opens a database that the server owns (the worker and the tools do this):
        no table is created, no migration runs, and the schema version must match this code."""
        if not migrate and not os.path.isfile(path):
            raise FileNotFoundError("database %s does not exist yet; start the server first" % path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = threading.Lock()
        self.fts = False
        self.journal_mode = None
        self.db = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000.0, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout = %d" % BUSY_TIMEOUT_MS)
        if not migrate:
            try:
                self._check_version(fts)
            except Exception:
                self.db.close()
                raise
            return
        # WAL lets the worker process write while the server reads. The mode is stored in the file.
        self.journal_mode = self.db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
```

The rest of `__init__` (the `executescript` block, `run_migrations`, `ensure_fts`) stays as it is. Add this method right after `__init__`:

```python
    def _check_version(self, fts):
        found = self.db.execute("PRAGMA user_version").fetchone()[0]
        expected = schema_version()
        if found != expected:
            raise SchemaMismatch("database schema version is %d but this code expects %d; restart the "
                                 "server so that the server and the worker run the same code" % (found, expected))
        names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master")}
        self.fts = bool(fts) and "items_fts" in names
```

- [ ] **Step 5: Worker status methods**

Append to `class Store` (after `saved`):

```python
    def set_worker_status(self, job, state, last_batch=None, done_last_hour=0, last_error="", now=None):
        now = time.time() if now is None else now
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO worker_status (job, state, last_batch, done_last_hour, last_error, updated) "
                "VALUES (?,?,?,?,?,?)", (job, state, last_batch, int(done_last_hour or 0),
                                         str(last_error or "")[:500], now))
            self.db.commit()

    def worker_status(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM worker_status ORDER BY job")]
```

- [ ] **Step 6: Run the tests**

Run: `py -m unittest tests.test_wal tests.test_migrations tests.test_search -v`
Expected: all pass. Then `py -m unittest discover -s tests`: `OK`, with the baseline skips only.

- [ ] **Step 7: Commit**

```bash
git add newscentral/store.py tests/test_wal.py
git commit -m "Switch the database to WAL and add migration 6 for translations and worker status" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Worker and translation settings in the config

**Files:**
- Modify: `newscentral/config.py`, `config.example.json`, `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add these methods to `class TestConfig` in `tests/test_config.py`, before `if __name__`:

```python
    def test_worker_defaults(self):
        cfg = config.load_config(self.path)
        self.assertIs(cfg["worker"], True)
        self.assertEqual(cfg["translate_batch"], 16)
        self.assertIsNone(cfg["worker_threads"])
        self.assertEqual(cfg["translate_engine"], {"default": "nllb-600m"})
        self.assertEqual(config.TARGET_LANG, "es")

    def test_worker_values_from_file(self):
        self.write({"worker": False, "translate_batch": 8, "worker_threads": 2,
                    "translate_engine": {"default": "opus-mt", "zh": "nllb-600m"}})
        cfg = config.load_config(self.path)
        self.assertIs(cfg["worker"], False)
        self.assertEqual(cfg["translate_batch"], 8)
        self.assertEqual(config.worker_threads(cfg), 2)
        self.assertEqual(cfg["translate_engine"]["zh"], "nllb-600m")

    def test_worker_override_from_flag(self):
        cfg = config.load_config(self.path, {"worker": False})
        self.assertIs(cfg["worker"], False)

    def test_bad_worker_values_name_the_key(self):
        for bad in ({"worker": "no"}, {"translate_batch": 0}, {"translate_batch": 1000},
                    {"worker_threads": 0}, {"worker_threads": "2"}, {"translate_engine": "nllb-600m"},
                    {"translate_engine": {"zh": "opus-mt"}}, {"translate_engine": {"default": ""}},
                    {"translate_engine": {"default": "nllb-600m", "zh": 3}}):
            self.write(bad)
            with self.assertRaises(config.ConfigError) as cm:
                config.load_config(self.path)
            self.assertIn(list(bad)[0], str(cm.exception))

    def test_worker_threads_default_is_half_the_cores(self):
        cores = os.cpu_count() or 2
        self.assertEqual(config.worker_threads({"worker_threads": None}), max(1, cores // 2))

    def test_paths_for_models_and_bench(self):
        p = config.paths({"data_dir": self.dir, "log_file": None})
        self.assertEqual(p["models"], os.path.join(self.dir, "models"))
        self.assertEqual(p["bench"], os.path.join(self.dir, "bench"))
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_config -v`
Expected: `test_worker_defaults` fails with `KeyError: 'worker'`, `test_worker_values_from_file` and `test_worker_override_from_flag` fail with `ConfigError: config: unknown key 'worker'`, `test_worker_threads_default_is_half_the_cores` fails with `AttributeError: module 'newscentral.config' has no attribute 'worker_threads'`, and `test_paths_for_models_and_bench` fails with `KeyError: 'models'`. `test_bad_worker_values_name_the_key` already passes, because "unknown key" messages also name the key; it guards the validation added below.

- [ ] **Step 3: Implement**

In `newscentral/config.py`, after `SUMMARY_CHARS = 500` add:

```python
TARGET_LANG = "es"             # the one language translations are written in
```

Replace `DEFAULTS` with:

```python
DEFAULTS = {
    "port": 8787,
    "listen": "127.0.0.1",
    "data_dir": "data",
    "read_langs": ["es", "en"],
    "refresh_minutes": 15,
    "keep_awake": False,
    "log_file": None,
    "worker": True,
    "translate_batch": 16,
    "worker_threads": None,
    "translate_engine": {"default": "nllb-600m"},
}
```

In `_check`, replace the branch

```python
    elif key == "keep_awake":
        ok = isinstance(value, bool)
```

with:

```python
    elif key in ("keep_awake", "worker"):
        ok = isinstance(value, bool)
    elif key == "translate_batch":
        ok = is_int(value) and 1 <= value <= 256
    elif key == "worker_threads":
        ok = value is None or (is_int(value) and value >= 1)
    elif key == "translate_engine":
        ok = (isinstance(value, dict) and isinstance(value.get("default"), str)
              and all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in value.items()))
```

After `load_config`, add:

```python
def worker_threads(cfg):
    """CPU threads an engine may use. Default: half the cores, at least one, so the reader and
    the fetcher stay responsive."""
    return cfg.get("worker_threads") or max(1, (os.cpu_count() or 2) // 2)
```

In `paths`, add two entries to the returned dict after `"status"`:

```python
        "models": os.path.join(data_dir, "models"),
        "bench": os.path.join(data_dir, "bench"),
```

- [ ] **Step 4: Update the example config**

Replace `config.example.json` with:

```json
{
  "port": 8787,
  "listen": "127.0.0.1",
  "data_dir": "data",
  "read_langs": ["es", "en"],
  "refresh_minutes": 15,
  "keep_awake": false,
  "log_file": null,
  "worker": true,
  "translate_batch": 16,
  "worker_threads": null,
  "translate_engine": {"default": "nllb-600m"}
}
```

- [ ] **Step 5: Run the tests**

Run: `py -m unittest tests.test_config -v`
Expected: all pass. Then `py -m unittest discover -s tests`: `OK`, with the baseline skips only.

- [ ] **Step 6: Commit**

```bash
git add newscentral/config.py config.example.json tests/test_config.py
git commit -m "Add worker and translation settings to the config" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Job framework and worker loop

**Files:**
- Create: `newscentral/jobs.py`, `newscentral/worker.py`, `newscentral/supervisor.py` (only the exit-code constant in this task), `tests/test_worker.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_worker.py
import json
import os
import shutil
import signal
import sqlite3
import tempfile
import threading
import time
import unittest

from newscentral import worker as worker_mod
from newscentral.jobs import Job
from newscentral.store import Store, schema_version
from newscentral.supervisor import EXIT_SCHEMA
from newscentral.worker import Worker, install_signal_handlers


class ListJob(Job):
    """Hands out the numbers in `work` in batches and records what it saved."""

    name = "count"
    batch_size = 2

    def __init__(self, work, fail_run=False, ready=True):
        super().__init__()
        self.work = list(work)
        self.fail_run = fail_run
        self.ready = ready
        self.saved = []

    def available(self):
        self.reason = "" if self.ready else "model not downloaded"
        return self.ready

    def pending(self, store, limit):
        return self.work[:limit]

    def run(self, batch):
        if self.fail_run:
            raise RuntimeError("engine exploded")
        return [n * 10 for n in batch]

    def save(self, store, batch, results):
        self.saved.extend(results)
        self.work = self.work[len(batch):]
        return len(results)


class WorkerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "news.db")
        Store(self.path).db.close()                 # the "server" creates and migrates the file
        self.store = Store(self.path, migrate=False)
        self.clock = [1000.0]

    def tearDown(self):
        self.store.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def worker(self, jobs):
        return Worker(self.store, jobs, idle_seconds=0.01, clock=lambda: self.clock[0])

    def status(self):
        return {r["job"]: r for r in self.store.worker_status()}


class TestStep(WorkerCase):
    def test_runs_one_batch_per_step_and_reports(self):
        job = ListJob([1, 2, 3])
        w = self.worker([job])
        self.assertTrue(w.step())
        self.assertEqual(job.saved, [10, 20])
        self.assertEqual(self.status()["count"]["state"], "running")
        self.assertEqual(self.status()["count"]["done_last_hour"], 2)
        self.assertTrue(w.step())
        self.assertFalse(w.step())
        self.assertEqual(job.saved, [10, 20, 30])
        st = self.status()["count"]
        self.assertEqual((st["state"], st["done_last_hour"], st["last_batch"]), ("idle", 3, 1000.0))

    def test_done_last_hour_forgets_old_batches(self):
        job = ListJob([1, 2, 3, 4])
        w = self.worker([job])
        w.step()
        self.clock[0] += 3601
        w.step()
        self.assertEqual(self.status()["count"]["done_last_hour"], 2)

    def test_unavailable_job_is_reported_and_skipped(self):
        job = ListJob([1], ready=False)
        w = self.worker([job])
        self.assertFalse(w.step())
        st = self.status()["count"]
        self.assertEqual((st["state"], st["last_error"]), ("unavailable", "model not downloaded"))
        self.assertEqual(job.saved, [])

    def test_failing_run_is_reported_and_the_next_job_runs(self):
        bad = ListJob([1], fail_run=True)
        bad.name = "bad"
        good = ListJob([5])
        w = self.worker([bad, good])
        self.assertTrue(w.step())
        self.assertEqual(good.saved, [50])
        self.assertEqual(self.status()["bad"]["state"], "error")
        self.assertIn("engine exploded", self.status()["bad"]["last_error"])

    def test_locked_database_during_save_is_retried_later(self):
        job = ListJob([1, 2])
        def locked(store, batch, results):
            raise sqlite3.OperationalError("database is locked")
        job.save = locked
        w = self.worker([job])
        self.assertFalse(w.step())
        self.assertEqual(self.status()["count"]["state"], "error")
        self.assertEqual(job.work, [1, 2])          # nothing consumed: the batch comes back next loop


class TestLoop(WorkerCase):
    def test_stop_ends_the_loop_while_idle(self):
        w = self.worker([ListJob([])])
        t = threading.Thread(target=w.run_forever)
        t.start()
        time.sleep(0.1)
        w.stop()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertEqual(self.status()["count"]["state"], "stopped")

    def test_a_signal_stops_the_loop(self):
        w = self.worker([ListJob([])])
        previous = install_signal_handlers(w)
        try:
            signal.raise_signal(signal.SIGTERM)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        self.assertTrue(w.stopping.is_set())


class TestMain(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.config = os.path.join(self.dir, "config.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"data_dir": self.dir}, f)
        self.db = os.path.join(self.dir, "news.db")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_refuses_a_database_from_another_version(self):
        Store(self.db).db.close()
        raw = sqlite3.connect(self.db)
        raw.execute("PRAGMA user_version = %d" % (schema_version() + 1))
        raw.commit()
        raw.close()
        self.assertEqual(worker_mod.main(["--config", self.config, "--once"]), EXIT_SCHEMA)

    def test_missing_database_is_an_ordinary_error(self):
        self.assertEqual(worker_mod.main(["--config", self.config, "--once"]), 1)
        self.assertFalse(os.path.exists(self.db))

    def test_one_step_on_a_good_database(self):
        Store(self.db).db.close()
        self.assertEqual(worker_mod.main(["--config", self.config, "--once"]), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_worker -v`
Expected: `ModuleNotFoundError: No module named 'newscentral.worker'`.

- [ ] **Step 3: The job base class**

```python
# newscentral/jobs.py
# Batch jobs run by the worker process (worker.py). One subclass per job: translate now,
# topics and stories later. A job never holds a transaction while run() works.


class Job:
    name = ""
    batch_size = 16

    def __init__(self):
        self.reason = ""          # why available() said no, shown in the Sources view

    def available(self):
        """Dependencies and models present. Set self.reason when returning False."""
        return True

    def pending(self, store, limit):
        """The next batch of work, at most `limit` units. Empty when there is nothing to do."""
        raise NotImplementedError

    def run(self, batch):
        """Do the work outside any transaction. Returns one result per unit."""
        raise NotImplementedError

    def save(self, store, batch, results):
        """Write the results in one transaction. Returns how many units are done."""
        raise NotImplementedError
```

- [ ] **Step 4: The exit code shared by worker and supervisor**

```python
# newscentral/supervisor.py
# Starts the worker process and restarts it after a crash. Completed in Task 4.

EXIT_SCHEMA = 3    # worker exit code: database and code disagree on the schema; do not restart
```

- [ ] **Step 5: The worker**

```python
# newscentral/worker.py
# Batch-job worker. Runs as its own process: python -m newscentral.worker --config <path>.
# The server starts and supervises it (supervisor.py). Migrations never run here: the database
# must already be at the schema version of this code.
import argparse
import collections
import signal
import sqlite3
import sys
import threading
import time

from .config import CONFIG_FILE, ConfigError, load_config, paths
from .store import SchemaMismatch, Store
from .supervisor import EXIT_SCHEMA

IDLE_SECONDS = 30


def log(*parts):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "worker:", *parts, flush=True)


class Worker:
    def __init__(self, store, jobs, idle_seconds=IDLE_SECONDS, clock=time.time):
        self.store = store
        self.jobs = list(jobs)
        self.idle_seconds = idle_seconds
        self.clock = clock
        self.stopping = threading.Event()
        self.done = {job.name: collections.deque() for job in self.jobs}   # (time, units) per batch
        self.last_batch = {job.name: None for job in self.jobs}

    def stop(self):
        self.stopping.set()

    def done_last_hour(self, name, now):
        q = self.done[name]
        while q and q[0][0] < now - 3600:
            q.popleft()
        return sum(n for _, n in q)

    def status(self, job, state, error=""):
        now = self.clock()
        try:
            self.store.set_worker_status(job.name, state, self.last_batch[job.name],
                                         self.done_last_hour(job.name, now), error, now)
        except sqlite3.Error as e:
            log("could not write status:", e)

    def step(self):
        """Run one batch of the first job that has work. Returns True when a batch was saved."""
        for job in self.jobs:
            if self.stopping.is_set():
                return False
            if not job.available():
                self.status(job, "unavailable", job.reason)
                continue
            try:
                batch = job.pending(self.store, job.batch_size)
            except sqlite3.Error as e:
                self.status(job, "error", "reading the queue: %s" % e)
                continue
            if not batch:
                self.status(job, "idle")
                continue
            try:
                results = job.run(batch)
            except Exception as e:
                log(job.name, "batch failed:", e)
                self.status(job, "error", "%s: %s" % (type(e).__name__, e))
                continue
            try:
                done = job.save(self.store, batch, results)
            except sqlite3.Error as e:
                # Locked beyond the busy timeout, or any other write error: the transaction rolled
                # back, nothing was written, and the same batch comes back on the next loop.
                self.status(job, "error", "saving: %s" % e)
                continue
            now = self.clock()
            self.last_batch[job.name] = now
            self.done[job.name].append((now, done))
            self.status(job, "running")
            return True
        return False

    def run_forever(self):
        log("started; jobs:", ", ".join(j.name for j in self.jobs) or "none")
        while not self.stopping.is_set():
            try:
                ran = self.step()
            except Exception as e:
                log("loop error:", e)
                ran = False
            if not ran:
                self.stopping.wait(self.idle_seconds)
        for job in self.jobs:
            self.status(job, "stopped")
        log("stopped")


def install_signal_handlers(worker):
    """Stop cleanly on SIGTERM, SIGINT and (Windows) SIGBREAK. Returns the previous handlers."""
    def handler(signum, frame):
        worker.stop()

    previous = {}
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            previous[sig] = signal.signal(sig, handler)
    return previous


def build_jobs(cfg, p):
    """The jobs this worker runs, in priority order. Task 7 adds translation."""
    return []


def main(argv=None):
    ap = argparse.ArgumentParser(description="News Central batch-job worker")
    ap.add_argument("--config", default=CONFIG_FILE, help="path to config.json")
    ap.add_argument("--once", action="store_true", help="run one loop step and exit")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log(e)
        return 2
    p = paths(cfg)
    try:
        store = Store(p["db"], migrate=False)
    except SchemaMismatch as e:
        log(e)
        return EXIT_SCHEMA
    except FileNotFoundError as e:
        log(e)
        return 1
    worker = Worker(store, build_jobs(cfg, p))
    previous = install_signal_handlers(worker)
    try:
        if args.once:
            worker.step()
        else:
            worker.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for sig, handler in previous.items():      # matters when main() runs inside the test process
            signal.signal(sig, handler)
        store.db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests**

Run: `py -m unittest tests.test_worker -v`
Expected: all pass. Then `py -m unittest discover -s tests`: `OK`, with the baseline skips only.

Run by hand: `py -m newscentral.worker --once; echo $?` in Git Bash (uses the real `config.json` and `data/news.db`). If the server has not run since Task 1, the database is still at version 5: expected output is `worker: database schema version is 5 but this code expects 6; ...` and exit code `3`. Start `py server.py --port 8799`, wait until it prints that it is running, stop it, and run the command again. Expected: no output and exit code `0` (with no jobs yet, `--once` does nothing).

- [ ] **Step 7: Commit**

```bash
git add newscentral/jobs.py newscentral/worker.py newscentral/supervisor.py tests/test_worker.py
git commit -m "Add the batch-job framework and the worker loop" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Supervise the worker from the server, `--no-worker`

**Files:**
- Modify: `newscentral/supervisor.py`, `newscentral/cli.py`
- Create: `tests/test_supervisor.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_supervisor.py
import sys
import time
import unittest

from newscentral import cli, config, supervisor
from newscentral.supervisor import Supervisor, next_delay


def quiet(*parts):
    pass


def wait_for(cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class TestNextDelay(unittest.TestCase):
    def test_doubles_from_the_minimum_up_to_the_maximum(self):
        self.assertEqual(next_delay(0, 1, 10, 600, 600), 10)
        self.assertEqual(next_delay(10, 1, 10, 600, 600), 20)
        self.assertEqual(next_delay(320, 1, 10, 600, 600), 600)
        self.assertEqual(next_delay(600, 1, 10, 600, 600), 600)

    def test_resets_after_a_long_run(self):
        self.assertEqual(next_delay(320, 600, 10, 600, 600), 10)
        self.assertEqual(next_delay(320, 599, 10, 600, 600), 600)


class TestSupervisor(unittest.TestCase):
    def make(self, code):
        return Supervisor([sys.executable, "-c", code], min_delay=0.05, max_delay=0.2,
                          reset_after=60, log=quiet)

    def test_a_worker_that_exits_at_once_is_restarted_with_growing_delays(self):
        sup = self.make("import sys; sys.exit(1)")
        sup.start()
        try:
            self.assertTrue(wait_for(lambda: len(sup.delays) >= 4))
        finally:
            sup.stop()
        sup.join(5)
        self.assertEqual(sup.delays[:4], [0.05, 0.1, 0.2, 0.2])
        self.assertGreaterEqual(sup.starts, 4)
        self.assertFalse(sup.is_alive())

    def test_schema_mismatch_stops_the_restarts(self):
        sup = self.make("import sys; sys.exit(%d)" % supervisor.EXIT_SCHEMA)
        sup.start()
        sup.join(15)
        self.assertFalse(sup.is_alive())
        self.assertTrue(sup.gave_up)
        self.assertEqual(sup.starts, 1)

    def test_stop_terminates_a_running_worker(self):
        sup = self.make("import time; time.sleep(60)")
        sup.start()
        self.assertTrue(wait_for(lambda: sup.proc is not None))
        sup.stop()
        self.assertIsNotNone(sup.proc.poll())
        sup.join(5)
        self.assertFalse(sup.is_alive())
        self.assertEqual(sup.starts, 1)


class TestStartWorker(unittest.TestCase):
    def test_no_worker_starts_none(self):
        self.assertIsNone(cli.start_worker({"worker": False}, "config.json", start=False))

    def test_command_and_working_directory(self):
        sup = cli.start_worker({"worker": True}, "C:/x/config.json", start=False)
        self.assertEqual(sup.cmd, [sys.executable, "-m", "newscentral.worker", "--config", "C:/x/config.json"])
        self.assertEqual(sup.cwd, config.BASE_DIR)
        self.assertFalse(sup.is_alive())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_supervisor -v`
Expected: `ImportError: cannot import name 'Supervisor' from 'newscentral.supervisor'`.

- [ ] **Step 3: The supervisor**

Replace `newscentral/supervisor.py` with:

```python
# newscentral/supervisor.py
# Starts the worker process and restarts it after a crash, with a delay that doubles from
# min_delay to max_delay and goes back to min_delay after the worker has run for reset_after.
import subprocess
import threading
import time

EXIT_SCHEMA = 3    # worker exit code: database and code disagree on the schema; do not restart


def next_delay(previous, ran_for, min_delay, max_delay, reset_after):
    if previous <= 0 or ran_for >= reset_after:
        return min_delay
    return min(previous * 2, max_delay)


def _log(*parts):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "supervisor:", *parts, flush=True)


class Supervisor(threading.Thread):
    def __init__(self, cmd, cwd=None, stdout=None, min_delay=10, max_delay=600, reset_after=600, log=_log):
        super().__init__(daemon=True)
        self.cmd = list(cmd)
        self.cwd = cwd
        self.stdout = stdout
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.reset_after = reset_after
        self.log = log
        self.proc = None
        self.starts = 0
        self.delays = []
        self.gave_up = False
        self._stopping = threading.Event()
        self._lock = threading.Lock()

    def run(self):
        delay = 0
        while not self._stopping.is_set():
            started = time.monotonic()
            try:
                with self._lock:
                    if self._stopping.is_set():
                        break
                    self.proc = subprocess.Popen(
                        self.cmd, cwd=self.cwd, stdout=self.stdout,
                        stderr=subprocess.STDOUT if self.stdout is not None else None)
                    self.starts += 1
                code = self.proc.wait()
            except OSError as e:
                self.log("could not start the worker:", e)
                code = None
            if self._stopping.is_set():
                break
            if code == EXIT_SCHEMA:
                self.gave_up = True
                self.log("worker refused the database (schema version); not restarting it")
                break
            delay = next_delay(delay, time.monotonic() - started, self.min_delay, self.max_delay,
                               self.reset_after)
            self.delays.append(delay)
            self.log("worker exited with code %s; restarting in %s s" % (code, delay))
            self._stopping.wait(delay)

    def stop(self, timeout=5):
        """Terminate the worker and stop restarting it. Results are written in single
        transactions, so killing the worker mid-batch loses at most that batch."""
        self._stopping.set()
        with self._lock:
            proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
```

- [ ] **Step 4: Wire it into `cli.main`**

In `newscentral/cli.py`, change the config import line to:

```python
from .config import BASE_DIR, CONFIG_FILE, ConfigError, WORKERS, load_config, paths
```

and add below the other package imports:

```python
from .supervisor import Supervisor
```

Add this function above `def main():`:

```python
def start_worker(cfg, config_path, stdout=None, start=True):
    """Start the batch-job worker as a child process under a supervisor, or return None when
    the worker is disabled (--no-worker or "worker": false). cwd=BASE_DIR so `-m newscentral.worker`
    resolves wherever the server was started from."""
    if not cfg["worker"]:
        return None
    sup = Supervisor([sys.executable, "-m", "newscentral.worker", "--config", config_path],
                     cwd=BASE_DIR, stdout=stdout)
    if start:
        sup.start()
    return sup
```

In `main`, after the `--check-sources` argument add:

```python
    ap.add_argument("--no-worker", action="store_true", help="do not start the batch-job worker")
```

Replace the `load_config` call with:

```python
        cfg = load_config(args.config, {"port": args.port, "listen": "0.0.0.0" if args.lan else None,
                                        "worker": False if args.no_worker else None})
```

Replace

```python
    httpd.app = App(store, fetcher, cfg)
    fetcher.start()
```

with:

```python
    httpd.app = App(store, fetcher, cfg)
    fetcher.start()
    # The worker opens the database only after this process has migrated it (Store above).
    # Under pythonw the log file is the only usable output, so the child writes there too.
    worker = start_worker(cfg, os.path.abspath(args.config), sys.stdout if p["log"] else None)
```

Replace the `finally:` block at the end of `main` with:

```python
    finally:
        if worker:
            worker.stop()
        if awake:
            keep_awake(False)
```

- [ ] **Step 5: Run the tests**

Run: `py -m unittest tests.test_supervisor -v`
Expected: all pass. Then `py -m unittest discover -s tests`: `OK`, with the baseline skips only.

- [ ] **Step 6: Check the real server by hand**

Run: `py server.py --port 8799` in the background, wait 10 seconds, then list processes: `powershell -Command "Get-CimInstance Win32_Process -Filter \"Name like 'py%'\" | Select-Object ProcessId, CommandLine"`. Expected: one process with `newscentral.worker --config ...config.json` in its command line. Stop the server with Ctrl+C (or stop the background task) and list again: the worker process is gone.

Run: `py server.py --port 8799 --no-worker` in the background and list processes. Expected: no `newscentral.worker` process. Stop it.

- [ ] **Step 7: Commit**

```bash
git add newscentral/supervisor.py newscentral/cli.py tests/test_supervisor.py
git commit -m "Start and supervise the worker from the server, with --no-worker" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

Continue with Task 5 in `2026-11-11-translation-part2.md`.
