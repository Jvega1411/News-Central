# Translation (spec 2a) Implementation Plan, part 2 of 3

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Tasks 5 to 10: worker status in the Sources view, the translation queue, the translate job, search over translations, the API fields and the reader display. Read the header, ground rules, deviations and file structure in `2026-11-11-translation.md` first. Tasks 1 to 4 must be merged before these start.

---

### Task 5: Worker status in `/api/status` and the Sources view

**Files:**
- Modify: `newscentral/api.py`, `static/js/reader.js`
- Create: `tests/test_worker_status_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_worker_status_api.py
import unittest

from tests.helpers import ApiCase


class TestWorkerStatusApi(ApiCase):
    def test_status_lists_the_worker_jobs(self):
        self.store.set_worker_status("translate", "idle", None, 0, "", now=100.0)
        status, body = self.get("/api/status")
        self.assertEqual(status, 200)
        self.assertIs(body["worker_enabled"], True)
        self.assertEqual([(w["job"], w["state"]) for w in body["worker"]], [("translate", "idle")])

    def test_sources_and_items_carry_the_same_status(self):
        self.assertEqual(self.get("/api/sources")[1]["worker"], [])
        self.assertEqual(self.get("/api/items?since=0")[1]["worker"], [])


class TestWorkerDisabled(ApiCase):
    config = {"worker": False}

    def test_disabled_worker_is_reported(self):
        self.assertIs(self.get("/api/status")[1]["worker_enabled"], False)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_worker_status_api -v`
Expected: `KeyError: 'worker_enabled'` and `KeyError: 'worker'`.

- [ ] **Step 3: Add the status to the payload**

In `newscentral/api.py`, replace `status_payload` with:

```python
def status_payload(app):
    f = app.fetcher
    return {"running": f.running, "updated": f.last_cycle, "sources_error": f.sources_error,
            "read_langs": app.config["read_langs"], "worker_enabled": bool(app.config["worker"]),
            "worker": app.store.worker_status()}
```

- [ ] **Step 4: Show it in the Sources view**

In `static/js/reader.js`, in the `S` object, replace

```javascript
  readLangs: ['es', 'en'], showLangs: pref.get('showLangs', false), truncated: false,
```

with:

```javascript
  readLangs: ['es', 'en'], showLangs: pref.get('showLangs', false), truncated: false,
  worker: [], workerEnabled: true,
```

In `loadSources`, after the line `if (d.read_langs && d.read_langs.length) S.readLangs = d.read_langs;` add:

```javascript
  S.worker = d.worker || [];
  S.workerEnabled = d.worker_enabled !== false;
```

Add these functions directly above `function collectionHTML() {`:

```javascript
function agoText(ts) { var a = ago(ts); return a === 'now' ? 'just now' : a + ' ago'; }

// Worker states map to the existing status colours: working (ok), not working (pending), broken (error).
var WORKER_CLASS = { running: 'st-ok', idle: 'st-ok', unavailable: 'st-pending', stopped: 'st-pending', error: 'st-error' };

function workerHTML() {
  if (!S.workerEnabled) {
    return '<div class="toolbar">Background worker: off (--no-worker or "worker": false in config.json)</div>';
  }
  if (!S.worker.length) return '<div class="toolbar">Background worker: no report yet</div>';
  return S.worker.map(function (w) {
    var parts = ['Worker job ' + esc(w.job) + ': <span class="' + (WORKER_CLASS[w.state] || 'st-pending') + '">' +
                 esc(w.state) + '</span>'];
    if (w.last_batch) parts.push('last batch ' + agoText(w.last_batch));
    parts.push((w.done_last_hour || 0).toLocaleString() + ' done in the last hour');
    if (w.updated) parts.push('reported ' + agoText(w.updated));
    var h = '<div class="toolbar">' + parts.join(' · ') + '</div>';
    if (w.last_error) h += '<div class="toolbar detail">' + esc(w.last_error) + '</div>';
    return h;
  }).join('');
}
```

In `collectionHTML`, replace

```javascript
    (st.longest_gap ? ' · longest ' + esc(duration(st.longest_gap)) : '') + '</div>';
```

with:

```javascript
    (st.longest_gap ? ' · longest ' + esc(duration(st.longest_gap)) : '') + '</div>';
  h += workerHTML();
```

- [ ] **Step 5: Run the tests**

Run: `py -m unittest tests.test_worker_status_api -v`, then `py -m unittest discover -s tests`.
Expected: all pass.

- [ ] **Step 6: Verify in a browser**

Run `node --check static/js/reader.js`; search `static/js/reader.js` for `??` and `?.` (expected: none in code).

Start `py server.py --port 8799`, open `http://127.0.0.1:8799/` with the console open, click Sources. Expected: under the Collection uptime line, `Background worker: no report yet` (no job exists before Task 7). The console shows no errors. Stop the server.

Start `py server.py --port 8799 --no-worker`, open Sources. Expected: `Background worker: off (--no-worker or "worker": false in config.json)`. No console errors. Stop the server.

- [ ] **Step 7: Commit**

```bash
git add newscentral/api.py static/js/reader.js tests/test_worker_status_api.py
git commit -m "Report the worker status in /api/status and the Sources view" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Engine interface, fake engine and the translation queue

**Files:**
- Create: `newscentral/translate/__init__.py` (empty), `newscentral/translate/base.py`, `newscentral/translate/fake.py`, `newscentral/translate/registry.py`, `tests/test_translate_queue.py`
- Modify: `newscentral/store.py`, `tests/helpers.py`

- [ ] **Step 1: Add the shared test fixture**

Append to `tests/helpers.py`:

```python
class TranslationCase(unittest.TestCase):
    """A temporary database with items in Arabic, Russian, Spanish and English, at known times.
    self.ids maps a short key to the item id."""

    READ = ["es", "en"]

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "news.db"))
        self.store.sync_sources(SOURCES)
        self.ids = {}
        self.add_at("old_ar", "ahram", "\u0627\u0644\u0623\u062e\u0628\u0627\u0631", "\u0645\u0644\u062e\u0635", 1000)
        self.add_at("new_ru", "meduza", "\u041d\u043e\u0432\u043e\u0441\u0442\u0438", "\u0422\u0435\u043a\u0441\u0442", 2000)
        self.add_at("es", "elpais", "Titular", "Resumen", 3000)
        self.add_at("en", "bbc", "Headline", "Summary", 4000)
        self.add_at("ar_no_summary", "ahram", "\u062e\u0628\u0631", "", 500)

    def tearDown(self):
        self.store.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def add_at(self, key, source, title, summary, first_seen):
        link = "https://example.com/" + key
        lang = {s["id"]: s["lang"] for s in SOURCES}[source]
        self.store.add_items(source, [{"title": title, "link": link, "summary": summary}], lang)
        item_id = self.store.db.execute("SELECT id FROM items WHERE link = ?", (link,)).fetchone()[0]
        self.store.db.execute("UPDATE items SET first_seen = ? WHERE id = ?", (first_seen, item_id))
        self.store.db.commit()
        self.ids[key] = item_id
        return item_id

    def queue(self, limit=50):
        return [(u["item_id"], u["field"]) for u in self.store.pending_translations(self.READ, "es", limit)]

    def key(self, name, field):
        return (self.ids[name], field)


def tr_ok(item_id, field, text="texto", engine="fake"):
    return {"item_id": item_id, "field": field, "lang": "es", "text": text, "engine": engine, "error": ""}


def tr_failed(item_id, field, error="boom"):
    return {"item_id": item_id, "field": field, "lang": "es", "text": "", "engine": "fake", "error": error}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_translate_queue.py
import sqlite3
import unittest

from newscentral.translate import registry
from newscentral.translate.fake import FakeEngine
from tests.helpers import TranslationCase, tr_failed, tr_ok


class TestQueue(TranslationCase):
    def test_titles_first_then_summaries_newest_first(self):
        self.assertEqual(self.queue(), [
            self.key("new_ru", "title"), self.key("old_ar", "title"), self.key("ar_no_summary", "title"),
            self.key("new_ru", "summary"), self.key("old_ar", "summary")])

    def test_unit_fields(self):
        unit = self.store.pending_translations(self.READ, "es", 1)[0]
        self.assertEqual(unit, {"item_id": self.ids["new_ru"], "field": "title", "lang": "ru",
                                "text": "\u041d\u043e\u0432\u043e\u0441\u0442\u0438", "first_seen": 2000})

    def test_limit_is_filled_with_titles_first(self):
        self.assertEqual(self.queue(2), [self.key("new_ru", "title"), self.key("old_ar", "title")])
        self.assertEqual(self.queue(4)[3], self.key("new_ru", "summary"))

    def test_read_languages_and_unknown_languages_are_excluded(self):
        self.store.db.execute("UPDATE items SET lang = NULL WHERE id = ?", (self.ids["new_ru"],))
        self.store.db.commit()
        ids = {item_id for item_id, _ in self.queue()}
        self.assertEqual(ids, {self.ids["old_ar"], self.ids["ar_no_summary"]})

    def test_translated_fields_leave_the_queue(self):
        self.assertEqual(self.store.save_translations([tr_ok(self.ids["new_ru"], "title")]), 1)
        self.assertNotIn(self.key("new_ru", "title"), self.queue())
        self.assertIn(self.key("new_ru", "summary"), self.queue())

    def test_three_failures_stop_the_retries(self):
        for _ in range(2):
            self.store.save_translations([tr_failed(self.ids["new_ru"], "title")])
        self.assertIn(self.key("new_ru", "title"), self.queue())
        self.store.save_translations([tr_failed(self.ids["new_ru"], "title", "last one")])
        self.assertNotIn(self.key("new_ru", "title"), self.queue())
        row = self.store.db.execute("SELECT attempts, last_error FROM translation_failures").fetchone()
        self.assertEqual((row[0], row[1]), (3, "last one"))

    def test_success_clears_the_failure_count(self):
        self.store.save_translations([tr_failed(self.ids["new_ru"], "title")])
        self.store.save_translations([tr_ok(self.ids["new_ru"], "title")])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM translation_failures").fetchone()[0], 0)

    def test_a_new_translation_replaces_the_old_one(self):
        self.store.save_translations([tr_ok(self.ids["new_ru"], "title", "uno")])
        self.store.save_translations([tr_ok(self.ids["new_ru"], "title", "dos", "other")])
        rows = self.store.db.execute("SELECT text, engine FROM translations").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("dos", "other")])

    def test_a_batch_is_written_in_one_transaction(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_translations([tr_ok(self.ids["new_ru"], "title"), tr_ok(self.ids["old_ar"], "body")])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM translations").fetchone()[0], 0)
        self.assertIn(self.key("new_ru", "title"), self.queue())

    def test_save_counts_successes_only(self):
        n = self.store.save_translations([tr_ok(self.ids["new_ru"], "title"),
                                          tr_failed(self.ids["old_ar"], "title")])
        self.assertEqual(n, 1)

    def test_counts(self):
        self.assertEqual(self.store.translation_counts(self.READ, "es", now=5000),
                         {"pending_titles": 3, "pending_summaries": 2, "done_24h": 0, "failed": 0})
        self.store.save_translations([tr_ok(self.ids["new_ru"], "title")], now=4000)
        for _ in range(3):
            self.store.save_translations([tr_failed(self.ids["old_ar"], "summary")])
        self.assertEqual(self.store.translation_counts(self.READ, "es", now=5000),
                         {"pending_titles": 2, "pending_summaries": 1, "done_24h": 1, "failed": 1})
        self.assertEqual(self.store.translation_counts(self.READ, "es", now=4000 + 86401)["done_24h"], 0)

    def test_unknown_field_is_refused(self):
        with self.assertRaises(ValueError):
            self.store._pending_sql("body", self.READ, "es", 3)


class TestRegistryAndFakeEngine(unittest.TestCase):
    def test_fake_is_always_available(self):
        self.assertEqual(registry.check("fake", "no-such-dir"), "")
        engine = registry.create("fake", "no-such-dir", threads=2)
        self.assertEqual((engine.name, engine.version, engine.threads), ("fake", "1", 2))
        self.assertTrue(engine.supports("ar", "es"))
        self.assertFalse(engine.supports("es", "es"))

    def test_unknown_engine(self):
        reason = registry.check("nope", "models")
        self.assertIn("unknown engine 'nope'", reason)
        self.assertIn("fake", reason)

    def test_fake_translation_and_markers(self):
        engine = FakeEngine("unused")
        self.assertEqual(engine.translate(["hola mundo", "EMPTY x"], "ar", "es"), ["[ar>es] hola mundo", ""])
        self.assertEqual(engine.translate(["LONG"], "ar", "es"), [" ".join(["LONG"] * 6)])
        with self.assertRaises(RuntimeError):
            engine.translate(["a", "CRASH"], "ar", "es")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `py -m unittest tests.test_translate_queue -v`
Expected: `ModuleNotFoundError: No module named 'newscentral.translate'`.

- [ ] **Step 4: The engine interface**

Create the empty file `newscentral/translate/__init__.py`, then:

```python
# newscentral/translate/base.py
# The interface every translation engine implements. Engines receive canonical text (from items)
# and return plain text; the job canonicalizes and checks the output. Heavy dependencies are
# imported inside the engine, never at module level, so this package imports without them.
import importlib.util
import json
import os

SPECIAL_TOKENS = ("</s>", "<s>", "<pad>")


class Engine:
    name = ""
    requires = ()          # importable module names the engine needs

    def __init__(self, model_dir, threads=1):
        self.model_dir = model_dir
        self.threads = max(1, int(threads))
        self.version = read_version(model_dir)

    @classmethod
    def missing_modules(cls):
        return [m for m in cls.requires if importlib.util.find_spec(m) is None]

    @classmethod
    def model_ready(cls, model_dir):
        return os.path.isfile(os.path.join(model_dir, "model.bin"))

    @classmethod
    def check(cls, model_dir):
        """Empty string when the engine can run, otherwise the reason it cannot."""
        missing = cls.missing_modules()
        if missing:
            return ("missing Python packages: %s (py -m pip install -r requirements-translate.txt)"
                    % ", ".join(missing))
        if not cls.model_ready(model_dir):
            return "model not downloaded (py tools/get_models.py %s)" % cls.name
        return ""

    def supports(self, src, tgt):
        raise NotImplementedError

    def translate(self, texts, src, tgt):
        """One output string per input string, in order."""
        raise NotImplementedError


def read_version(model_dir):
    """The model revision recorded by tools/get_models.py in <model_dir>/source.json."""
    try:
        with open(os.path.join(model_dir, "source.json"), encoding="utf-8") as f:
            meta = json.load(f)
        return str(meta.get("revision") or "unknown")[:12]
    except (OSError, ValueError, AttributeError):
        return "unknown"


def strip_special(tokens):
    return [t for t in tokens if t not in SPECIAL_TOKENS]
```

```python
# newscentral/translate/fake.py
# A deterministic engine for tests, development and browser checks. Never used for real reading.
# Markers in the input simulate engine faults: EMPTY -> empty output, LONG -> six times the input,
# CRASH -> the whole call raises.
from .base import Engine


class FakeEngine(Engine):
    name = "fake"

    def __init__(self, model_dir, threads=1):
        super().__init__(model_dir, threads)
        self.version = "1"

    @classmethod
    def model_ready(cls, model_dir):
        return True

    def supports(self, src, tgt):
        return bool(src) and src != tgt

    def translate(self, texts, src, tgt):
        out = []
        for t in texts:
            if "CRASH" in t:
                raise RuntimeError("fake engine crash")
            if "EMPTY" in t:
                out.append("")
            elif "LONG" in t:
                out.append(" ".join([t] * 6))
            else:
                out.append("[%s>%s] %s" % (src, tgt, t))
        return out
```

```python
# newscentral/translate/registry.py
# Engine names (as written in config.json "translate_engine" and in translations.engine) to classes.
# Each engine keeps its model under <data_dir>/models/<name>/.
import os

from .fake import FakeEngine

ENGINES = {
    "fake": FakeEngine,
}


def check(name, models_dir):
    cls = ENGINES.get(name)
    if cls is None:
        return "unknown engine %r (known: %s)" % (name, ", ".join(sorted(ENGINES)))
    return cls.check(os.path.join(models_dir, name))


def create(name, models_dir, threads=1):
    return ENGINES[name](os.path.join(models_dir, name), threads)


def installed(models_dir, include_fake=False):
    """Names of the engines that can run on this machine now."""
    return [n for n in sorted(ENGINES) if (include_fake or n != "fake") and not check(n, models_dir)]
```

- [ ] **Step 5: The queue in the store**

In `newscentral/store.py`, change the import block at the top to add the config constant:

```python
from .config import TARGET_LANG
from .sources import country_label
from .text import canonical
```

Above `class Store:` add:

```python
TRANSLATED_FIELDS = ("title", "summary")
MAX_ATTEMPTS = 3      # failures after which an item's field is skipped until tools/retranslate.py clears it
```

Append to `class Store`:

```python
    def _pending_sql(self, field, skip_langs, target, max_attempts):
        """FROM/WHERE for items whose `field` still needs a translation into `target`."""
        if field not in TRANSLATED_FIELDS:
            raise ValueError("field must be one of %r" % (TRANSLATED_FIELDS,))
        skip = list(skip_langs) + [target]
        sql = ("FROM items i WHERE i.lang IS NOT NULL AND i.lang NOT IN (%s) AND COALESCE(i.%s, '') != '' "
               "AND NOT EXISTS (SELECT 1 FROM translations t WHERE t.item_id = i.id AND t.field = ? "
               "AND t.lang = ?) "
               "AND NOT EXISTS (SELECT 1 FROM translation_failures f WHERE f.item_id = i.id AND f.field = ? "
               "AND f.lang = ? AND f.attempts >= ?)" % (",".join("?" * len(skip)), field))
        return sql, skip + [field, target, field, target, max_attempts]

    def pending_translations(self, skip_langs, target, limit, max_attempts=MAX_ATTEMPTS):
        """Every pending title, newest first, then every pending summary, newest first, so a
        backlog grows in summaries, never in titles."""
        out = []
        with self.lock:
            for field in TRANSLATED_FIELDS:
                if len(out) >= limit:
                    break
                sql, params = self._pending_sql(field, skip_langs, target, max_attempts)
                rows = self.db.execute(
                    "SELECT i.id, i.lang, i.%s AS text, i.first_seen " % field + sql +
                    " ORDER BY i.first_seen DESC LIMIT ?", params + [limit - len(out)]).fetchall()
                out.extend({"item_id": r["id"], "field": field, "lang": r["lang"], "text": r["text"],
                            "first_seen": r["first_seen"]} for r in rows)
        return out

    def save_translations(self, results, now=None):
        """Write one batch in one transaction. A result with text and no error replaces any earlier
        translation of that field and clears its failures; any other result adds one attempt.
        Returns the number of successes."""
        now = time.time() if now is None else now
        done = 0
        with self.lock:
            self.db.execute("BEGIN")
            try:
                for r in results:
                    key = (r["item_id"], r["field"], r["lang"])
                    if r.get("error") or not r.get("text"):
                        error = str(r.get("error") or "empty output")[:500]
                        cur = self.db.execute(
                            "UPDATE translation_failures SET attempts = attempts + 1, last_error = ?, last_try = ? "
                            "WHERE item_id = ? AND field = ? AND lang = ?", (error, now) + key)
                        if cur.rowcount == 0:
                            self.db.execute(
                                "INSERT INTO translation_failures (item_id, field, lang, attempts, last_error, "
                                "last_try) VALUES (?,?,?,1,?,?)", key + (error, now))
                    else:
                        # DELETE then INSERT, not INSERT OR REPLACE: REPLACE does not fire the delete
                        # trigger that keeps translations_fts in step (Task 8).
                        self.db.execute("DELETE FROM translations WHERE item_id = ? AND field = ? AND lang = ?", key)
                        self.db.execute("INSERT INTO translations (item_id, field, lang, text, engine, created) "
                                        "VALUES (?,?,?,?,?,?)", key + (r["text"], r["engine"], now))
                        self.db.execute("DELETE FROM translation_failures WHERE item_id = ? AND field = ? "
                                        "AND lang = ?", key)
                        done += 1
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return done

    def translation_counts(self, skip_langs, target, now=None, max_attempts=MAX_ATTEMPTS):
        now = time.time() if now is None else now
        out = {}
        with self.lock:
            for field, name in (("title", "pending_titles"), ("summary", "pending_summaries")):
                sql, params = self._pending_sql(field, skip_langs, target, max_attempts)
                out[name] = self.db.execute("SELECT COUNT(*) " + sql, params).fetchone()[0]
            out["done_24h"] = self.db.execute(
                "SELECT COUNT(*) FROM translations WHERE lang = ? AND created >= ?",
                (target, now - 86400)).fetchone()[0]
            out["failed"] = self.db.execute(
                "SELECT COUNT(*) FROM translation_failures WHERE lang = ? AND attempts >= ?",
                (target, max_attempts)).fetchone()[0]
        return out
```

`TARGET_LANG` is imported here for Task 8; `config.py` imports nothing from the package, so there is no cycle.

- [ ] **Step 6: Run the tests**

Run: `py -m unittest tests.test_translate_queue -v`, then `py -m unittest discover -s tests`.
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add newscentral/translate/__init__.py newscentral/translate/base.py newscentral/translate/fake.py newscentral/translate/registry.py newscentral/store.py tests/helpers.py tests/test_translate_queue.py
git commit -m "Add the engine interface, a fake engine and the translation queue" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The translate job, failure rules and the backlog line

**Files:**
- Create: `newscentral/translate/job.py`, `tests/test_translate_job.py`
- Modify: `newscentral/worker.py`, `newscentral/api.py`, `static/js/reader.js`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_translate_job.py
import unittest
from unittest import mock

from newscentral import worker as worker_mod
from newscentral.config import DEFAULTS
from newscentral.translate import registry
from newscentral.translate.fake import FakeEngine
from newscentral.translate.job import TranslateJob, check_output
from newscentral.worker import Worker
from tests.helpers import ApiCase, TranslationCase


def cfg(**kw):
    c = dict(DEFAULTS, translate_engine={"default": "fake"}, translate_batch=10, worker_threads=1)
    c.update(kw)
    return c


class TestCheckOutput(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(check_output("hola", "  buenos   dias "), ("buenos dias", ""))
        self.assertEqual(check_output("hola", " "), ("", "empty output"))
        self.assertEqual(check_output("ab", "x" * 10), ("x" * 10, ""))
        text, error = check_output("ab", "x" * 11)
        self.assertEqual(text, "")
        self.assertIn("five times", error)


class TestTranslateJob(TranslationCase):
    def job(self, **kw):
        return TranslateJob(cfg(**kw), self.dir)

    def test_available_with_the_fake_engine(self):
        job = self.job()
        self.assertTrue(job.available())
        self.assertEqual(job.reason, "")

    def test_unavailable_engine_gives_a_reason(self):
        job = self.job(translate_engine={"default": "fake", "zh": "nope"})
        self.assertFalse(job.available())
        self.assertIn("nope: unknown engine", job.reason)

    def test_engine_per_language(self):
        job = self.job(translate_engine={"default": "fake", "zh": "nllb-600m"})
        self.assertEqual((job.engine_name("zh"), job.engine_name("ru")), ("nllb-600m", "fake"))

    def test_one_batch_translates_titles_then_summaries(self):
        job = self.job()
        batch = job.pending(self.store, job.batch_size)
        results = job.run(batch)
        self.assertEqual(job.save(self.store, batch, results), 5)
        rows = {(r[0], r[1]): (r[2], r[3]) for r in self.store.db.execute(
            "SELECT item_id, field, text, engine FROM translations")}
        self.assertEqual(rows[(self.ids["new_ru"], "title")],
                         ("[ru>es] \u041d\u043e\u0432\u043e\u0441\u0442\u0438", "fake"))
        self.assertEqual(self.queue(), [])

    def test_empty_and_long_outputs_are_failures(self):
        self.add_at("bad1", "meduza", "EMPTY news", "", 5000)
        self.add_at("bad2", "meduza", "LONG news", "", 5001)
        job = self.job()
        batch = job.pending(self.store, 2)
        self.assertEqual([u["item_id"] for u in batch], [self.ids["bad2"], self.ids["bad1"]])
        job.save(self.store, batch, job.run(batch))
        errors = dict(self.store.db.execute("SELECT item_id, last_error FROM translation_failures").fetchall())
        self.assertEqual(errors[self.ids["bad1"]], "empty output")
        self.assertIn("five times", errors[self.ids["bad2"]])

    def test_an_engine_crash_fails_only_its_group(self):
        self.add_at("crash", "meduza", "CRASH", "", 5000)
        job = self.job()
        batch = job.pending(self.store, 10)
        results = {(r["item_id"], r["field"]): r for r in job.run(batch)}
        self.assertEqual(results[(self.ids["crash"], "title")]["error"], "RuntimeError: fake engine crash")
        self.assertEqual(results[(self.ids["new_ru"], "title")]["error"], "RuntimeError: fake engine crash")
        self.assertEqual(results[(self.ids["old_ar"], "title")]["error"], "")      # other language, other group

    def test_unsupported_language_is_a_failure(self):
        job = self.job()
        batch = job.pending(self.store, 1)
        with mock.patch.object(FakeEngine, "supports", return_value=False):
            results = job.run(batch)
        self.assertIn("does not translate ru into es", results[0]["error"])

    def test_engine_load_errors_raise_instead_of_counting_failures(self):
        job = self.job()
        batch = job.pending(self.store, 2)
        with mock.patch.object(registry, "create", side_effect=OSError("model.bin is corrupt")):
            with self.assertRaises(OSError):
                job.run(batch)

    def test_worker_drains_the_queue(self):
        job = self.job(translate_batch=2)
        w = Worker(self.store, [job], idle_seconds=0.01)
        while w.step():
            pass
        self.assertEqual(self.queue(), [])
        st = self.store.worker_status()[0]
        self.assertEqual((st["job"], st["state"], st["done_last_hour"]), ("translate", "idle", 5))

    def test_build_jobs(self):
        jobs = worker_mod.build_jobs(cfg(), {"models": self.dir})
        self.assertEqual([j.name for j in jobs], ["translate"])


class TestBacklogInStats(ApiCase):
    def test_stats_carry_translation_counts(self):
        self.add("ahram", "\u062e\u0628\u0631", "https://example.com/ar", "\u0645\u0644\u062e\u0635")
        _, body = self.get("/api/stats?days=30")
        self.assertEqual(body["translation"],
                         {"pending_titles": 1, "pending_summaries": 1, "done_24h": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()
```

In `TestBacklogInStats`, `meduza` (ru) has no items, so only the Arabic item is pending.

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_translate_job -v`
Expected: `ModuleNotFoundError: No module named 'newscentral.translate.job'`.

- [ ] **Step 3: The job**

```python
# newscentral/translate/job.py
# The "translate" batch job: titles and summaries of items in languages outside read_langs,
# into TARGET_LANG, with the engine chosen per source language in config "translate_engine".
from ..config import TARGET_LANG, worker_threads
from ..jobs import Job
from ..store import MAX_ATTEMPTS
from ..text import canonical
from . import registry

MAX_GROWTH = 5     # output longer than five times the input counts as a failure


def check_output(source_text, output):
    """Returns (text, error). Engine output is plain text: canonical(), not normalize(), so a
    legitimate '<' in a headline survives."""
    text = canonical(output or "")
    if not text:
        return "", "empty output"
    if len(text) > MAX_GROWTH * max(len(source_text or ""), 1):
        return "", "output more than five times longer than the input"
    return text, ""


class TranslateJob(Job):
    name = "translate"

    def __init__(self, cfg, models_dir):
        super().__init__()
        self.read_langs = list(cfg["read_langs"])
        self.mapping = dict(cfg["translate_engine"])
        self.batch_size = cfg["translate_batch"]
        self.threads = worker_threads(cfg)
        self.models_dir = models_dir
        self.target = TARGET_LANG
        self.engines = {}

    def engine_name(self, src):
        return self.mapping.get(src) or self.mapping["default"]

    def available(self):
        problems = []
        for name in sorted(set(self.mapping.values())):
            reason = registry.check(name, self.models_dir)
            if reason:
                problems.append("%s: %s" % (name, reason))
        self.reason = "; ".join(problems)
        return not problems

    def engine(self, name):
        # Loading a model can fail (corrupt file, out of memory). That raises out of run(), so the
        # worker reports the job as failing instead of charging a failure to every item.
        if name not in self.engines:
            self.engines[name] = registry.create(name, self.models_dir, self.threads)
        return self.engines[name]

    def pending(self, store, limit):
        return store.pending_translations(self.read_langs, self.target, limit, MAX_ATTEMPTS)

    def result(self, unit, engine, text, error):
        return {"item_id": unit["item_id"], "field": unit["field"], "lang": self.target,
                "text": text, "engine": engine, "error": error}

    def run(self, batch):
        groups = {}
        for unit in batch:
            groups.setdefault((unit["lang"], unit["field"]), []).append(unit)
        results = []
        for (src, field) in sorted(groups):
            units = groups[(src, field)]
            name = self.engine_name(src)
            engine = self.engine(name)
            try:
                if not engine.supports(src, self.target):
                    raise ValueError("%s does not translate %s into %s" % (name, src, self.target))
                outputs = engine.translate([u["text"] for u in units], src, self.target)
                if len(outputs) != len(units):
                    raise ValueError("%s returned %d texts for %d inputs" % (name, len(outputs), len(units)))
            except Exception as e:
                error = ("%s: %s" % (type(e).__name__, e))[:300]
                results.extend(self.result(u, name, "", error) for u in units)
                continue
            for unit, output in zip(units, outputs):
                text, error = check_output(unit["text"], output)
                results.append(self.result(unit, name, text, error))
        return results

    def save(self, store, batch, results):
        return store.save_translations(results)
```

- [ ] **Step 4: Register the job in the worker**

In `newscentral/worker.py`, replace `build_jobs` with:

```python
def build_jobs(cfg, p):
    """The jobs this worker runs, in priority order."""
    from .translate.job import TranslateJob
    return [TranslateJob(cfg, p["models"])]
```

- [ ] **Step 5: Translation counts in `/api/stats`**

In `newscentral/api.py`, change the config import to:

```python
from .config import DEFAULTS, INDEX_FILE, LIVE_ROW_LIMIT, LIVE_WINDOW_HOURS, STATIC_DIR, TARGET_LANG
```

In `stats_payload`, replace the `return` statement with:

```python
    return {"uptime_7d": week["uptime"], "uptime_30d": wide["uptime"], "days": days,
            "gaps": wide["gaps"], "longest_gap": wide["longest"],
            "volume": volume(app.store.volume_rows(now - days * 86400)),
            "read_langs": app.config["read_langs"],
            "translation": app.store.translation_counts(app.config["read_langs"], TARGET_LANG, now)}
```

- [ ] **Step 6: The backlog line in the Collection block**

In `static/js/reader.js`, add above `function collectionHTML() {`:

```javascript
function translationHTML(tr) {
  if (!tr) return '';
  return '<div class="toolbar">Translation into Spanish: ' + tr.pending_titles.toLocaleString() + ' titles and ' +
    tr.pending_summaries.toLocaleString() + ' summaries waiting · ' + tr.done_24h.toLocaleString() +
    ' done in the last 24 h' + (tr.failed ? ' · ' + tr.failed.toLocaleString() + ' given up after 3 failures' : '') +
    '</div>';
}
```

In `collectionHTML`, replace the line `  h += workerHTML();` (from Task 5) with:

```javascript
  h += workerHTML() + translationHTML(st.translation);
```

- [ ] **Step 7: Run the tests**

Run: `py -m unittest tests.test_translate_job -v`, then `py -m unittest discover -s tests`.
Expected: all pass.

- [ ] **Step 8: End-to-end check with the fake engine and a scratch data folder**

This check never touches `data/`. In Git Bash:

```bash
mkdir -p "$TMP/nc-check"
printf '{"data_dir": "%s", "port": 8799, "translate_engine": {"default": "fake"}}\n' "$(cygpath -m "$TMP/nc-check")" > "$TMP/nc-check/config.json"
py server.py --config "$TMP/nc-check/config.json"
```

Run the last command in the background. Wait about two minutes (first fetch, then one worker loop). Then `curl -s http://127.0.0.1:8799/api/status` shows a `worker` entry with `"job": "translate"` and `"state": "running"` or `"idle"`, and `curl -s "http://127.0.0.1:8799/api/stats?days=1"` shows `translation.done_24h` greater than zero (the sources in French, Portuguese and the other languages of batch 1b are translated by the fake engine).

Open `http://127.0.0.1:8799/` with the console open, click Sources. Expected: a line `Worker job translate: idle` (green) or `running`, `N done in the last hour`, and the line `Translation into Spanish: ... titles and ... summaries waiting · ... done in the last 24 h`. No console errors.

Kill the worker process to test supervision: `powershell -Command "Get-CimInstance Win32_Process -Filter \"Name like 'py%'\" | Where-Object { $_.CommandLine -like '*newscentral.worker*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"`. Expected in the server output: `supervisor: worker exited with code <n>; restarting in 10 s` (a forced kill on Windows gives a code such as `1` or `4294967295`), and 10 seconds later a new worker process exists. Stop the server; no worker process remains. Delete `$TMP/nc-check`.

- [ ] **Step 9: Commit**

```bash
git add newscentral/translate/job.py newscentral/worker.py newscentral/api.py static/js/reader.js tests/test_translate_job.py
git commit -m "Add the translate job with failure rules and show the translation backlog" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Search over translations and `tools/retranslate.py`

**Files:**
- Modify: `newscentral/store.py`, `tests/test_search.py`, `tests/helpers.py`
- Create: `tools/retranslate.py`, `tests/test_retranslate.py`

- [ ] **Step 1: Write the failing search tests**

In `tests/test_search.py`, add these methods to `class SearchTests` (after `test_item_fields_and_removed_source`):

```python
    def translate_ahram(self):
        item_id = self.get("/api/search?source=ahram")[1]["items"][0]["id"]
        self.store.save_translations([
            {"item_id": item_id, "field": "title", "lang": "es",
             "text": "Noticias econ\u00f3micas de Egipto", "engine": "fake", "error": ""},
            {"item_id": item_id, "field": "summary", "lang": "es",
             "text": "Egipto publica sus cifras", "engine": "fake", "error": ""}])
        return item_id

    def test_spanish_query_finds_the_arabic_original_once(self):
        self.translate_ahram()
        _, body = self.get("/api/search?q=egipto")
        self.assertEqual([i["title"] for i in body["items"]], [ARABIC])
        self.assertEqual(body["total"], 1)

    def test_translation_matches_combine_with_filters(self):
        self.translate_ahram()
        self.assertEqual(self.titles("q=egipto&lang=ar"), [ARABIC])
        self.assertEqual(self.titles("q=egipto&source=bbc"), [])
        self.assertEqual(self.titles("q=rates"), ["Bank of England holds rates", "ECB rates decision",
                                                  "Rates at a closed outlet"])

    def test_deleted_translation_is_no_longer_found(self):
        self.translate_ahram()
        self.store.clear_translations(engine="fake")
        self.assertEqual(self.titles("q=egipto"), [])
```

Add to `class TestSearchFts`:

```python
    def test_translation_diacritics_are_folded(self):
        self.translate_ahram()
        self.assertEqual(self.titles("q=economicas"), [ARABIC])

    def test_translation_index_is_rebuilt_when_missing(self):
        path = os.path.join(self.dir, "news.db")
        self.translate_ahram()
        for name in ("translations_fts_ai", "translations_fts_ad", "translations_fts_au"):
            self.store.db.execute("DROP TRIGGER %s" % name)
        self.store.db.execute("DROP TABLE translations_fts")
        self.store.db.commit()
        self.store.db.close()
        self.store = Store(path)
        self.app.store = self.store
        self.assertEqual(self.titles("q=egipto"), [ARABIC])
```

- [ ] **Step 2: Write the failing retranslate tests**

Append to `tests/helpers.py`:

```python
def load_tool(name):
    """Import tools/<name>.py as a module (tools/ is not a package)."""
    import importlib.util
    from newscentral.config import BASE_DIR
    path = os.path.join(BASE_DIR, "tools", name + ".py")
    spec = importlib.util.spec_from_file_location("tool_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

```python
# tests/test_retranslate.py
import contextlib
import io
import json
import os
import unittest

from tests.helpers import TranslationCase, load_tool, tr_failed, tr_ok


class TestClearTranslations(TranslationCase):
    def setUp(self):
        super().setUp()
        self.store.save_translations([
            tr_ok(self.ids["new_ru"], "title", engine="nllb-600m"),
            tr_ok(self.ids["old_ar"], "title", engine="nllb-600m"),
            tr_ok(self.ids["old_ar"], "summary", engine="opus-mt")])
        for _ in range(3):
            self.store.save_translations([tr_failed(self.ids["new_ru"], "summary"),
                                          tr_failed(self.ids["ar_no_summary"], "title")])

    def count(self, table):
        return self.store.db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]

    def test_one_engine_goes_back_to_the_queue(self):
        self.assertEqual(self.store.clear_translations("nllb-600m"), (2, 2))
        self.assertEqual(self.count("translations"), 1)
        self.assertIn(self.key("new_ru", "title"), self.queue())
        self.assertIn(self.key("ar_no_summary", "title"), self.queue())     # failures cleared too

    def test_limited_to_one_source_language(self):
        self.assertEqual(self.store.clear_translations("nllb-600m", "ar"), (1, 1))
        self.assertNotIn(self.key("new_ru", "title"), self.queue())
        self.assertNotIn(self.key("new_ru", "summary"), self.queue())

    def test_failures_only(self):
        self.assertEqual(self.store.clear_translations(None), (0, 2))
        self.assertEqual(self.count("translations"), 3)


class TestRetranslateTool(TranslationCase):
    def setUp(self):
        super().setUp()
        self.store.save_translations([tr_ok(self.ids["new_ru"], "title", engine="nllb-600m")])
        self.config = os.path.join(self.dir, "config.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"data_dir": self.dir}, f)
        self.tool = load_tool("retranslate")

    def run_tool(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.tool.main(["--config", self.config] + list(args))
        return code, out.getvalue()

    def test_engine(self):
        code, out = self.run_tool("--engine", "nllb-600m")
        self.assertEqual(code, 0)
        self.assertIn("1 translation(s) deleted", out)
        self.assertIn(self.key("new_ru", "title"), self.queue())

    def test_engine_or_failures_is_required(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.tool.main(["--config", self.config])

    def test_missing_database(self):
        os.remove(os.path.join(self.dir, "config.json"))
        other = os.path.join(self.dir, "other")
        os.makedirs(other)
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({"data_dir": other}, f)
        code, out = self.run_tool("--failures")
        self.assertEqual(code, 1)
        self.assertIn("does not exist yet", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `py -m unittest tests.test_search tests.test_retranslate -v`
Expected: the new search tests fail (`AssertionError: Lists differ: [] != [...]`, since search ignores translations; `AttributeError: 'Store' object has no attribute 'clear_translations'`), and the retranslate tests fail with `FileNotFoundError` for `tools/retranslate.py`.

- [ ] **Step 4: The translations index**

In `newscentral/store.py`, after the `FTS_TRIGGERS` dictionary add:

```python
TR_FTS_TABLE = ("CREATE VIRTUAL TABLE translations_fts USING fts5(text, content='translations', "
                "content_rowid='rowid', tokenize='%s')")
TR_FTS_TRIGGERS = {
    "translations_fts_ai": "CREATE TRIGGER translations_fts_ai AFTER INSERT ON translations BEGIN "
                           "INSERT INTO translations_fts(rowid, text) VALUES (new.rowid, new.text); END",
    "translations_fts_ad": "CREATE TRIGGER translations_fts_ad AFTER DELETE ON translations BEGIN "
                           "INSERT INTO translations_fts(translations_fts, rowid, text) "
                           "VALUES ('delete', old.rowid, old.text); END",
    "translations_fts_au": "CREATE TRIGGER translations_fts_au AFTER UPDATE OF text ON translations BEGIN "
                           "INSERT INTO translations_fts(translations_fts, rowid, text) "
                           "VALUES ('delete', old.rowid, old.text); "
                           "INSERT INTO translations_fts(rowid, text) VALUES (new.rowid, new.text); END",
}
# (index name, CREATE statement with a %s for the tokenizer, triggers)
FTS_INDEXES = (("items_fts", FTS_TABLE, FTS_TRIGGERS), ("translations_fts", TR_FTS_TABLE, TR_FTS_TRIGGERS))
```

Replace the whole `ensure_fts` method with:

```python
    def ensure_fts(self, wanted=True):
        """The search indexes are derived data that depend on the SQLite build, not on the schema
        version, so they are not numbered migrations. Runs at every startup of the server.
        The indexes use the implicit rowids of `items` and `translations`: never VACUUM the
        database without calling this afterwards with the triggers dropped, because VACUUM may
        renumber rowids."""
        with self.lock:
            names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master")}
            if not (wanted and fts5_available(self.db)):
                # Triggers that write to a module this build lacks would break every insert.
                for _, _, triggers in FTS_INDEXES:
                    for name in triggers:
                        self.db.execute("DROP TRIGGER IF EXISTS %s" % name)
                self.db.commit()
                self.fts = False
                return False
            self.db.execute("BEGIN")
            try:
                for index, create, triggers in FTS_INDEXES:
                    rebuild = False
                    if index not in names:
                        try:
                            self.db.execute(create % "unicode61 remove_diacritics 2")
                        except sqlite3.OperationalError:      # SQLite older than 3.27
                            self.db.execute(create % "unicode61")
                        rebuild = True
                    for name, sql in triggers.items():
                        if name not in names:
                            self.db.execute(sql)
                            rebuild = True
                    if rebuild:
                        self.db.execute("INSERT INTO %s(%s) VALUES ('rebuild')" % (index, index))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            self.fts = True
            return True
```

In `_check_version` (Task 1), replace the last line with:

```python
        self.fts = bool(fts) and "items_fts" in names and "translations_fts" in names
```

- [ ] **Step 5: Search the translations too**

Replace the whole `search` method with:

```python
    def search(self, q="", since=None, until=None, region=None, country=None, scope=None, lang=None,
               kind=None, source=None, limit=50, offset=0, tr_lang=TARGET_LANG):
        """Whole-archive search over the original text and its translations into tr_lang.
        Returns (rows, total, engine). Dates filter on first_seen. An item matched through both
        its original and its translation appears once, ranked by its best score. With FTS, every
        term must be in the original (title and summary together) or in one translated field."""
        terms = search_terms(q)
        cte, cte_params = "", []
        joins = "FROM items i LEFT JOIN sources s ON s.id = i.source_id"
        where, params = [], []
        order = "i.first_seen DESC"
        if terms and self.fts:
            match = " ".join('"%s"' % t for t in terms)
            cte = ("WITH hits(rid, score) AS ("
                   "SELECT rowid, bm25(items_fts) FROM items_fts WHERE items_fts MATCH ? "
                   "UNION ALL "
                   "SELECT i2.rowid, bm25(translations_fts) FROM translations_fts "
                   "JOIN translations t ON t.rowid = translations_fts.rowid "
                   "JOIN items i2 ON i2.id = t.item_id "
                   "WHERE translations_fts MATCH ? AND t.lang = ?), "
                   "best AS (SELECT rid, MIN(score) AS score FROM hits GROUP BY rid) ")
            cte_params = [match, match, tr_lang]
            joins += " JOIN best ON best.rid = i.rowid"
            order = "best.score, i.first_seen DESC"
        elif terms:
            for t in terms:
                like = "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                where.append("(i.title LIKE ? ESCAPE '\\' OR i.summary LIKE ? ESCAPE '\\' OR i.id IN "
                             "(SELECT item_id FROM translations WHERE lang = ? AND text LIKE ? ESCAPE '\\'))")
                params.extend([like, like, tr_lang, like])
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
            total = self.db.execute(cte + "SELECT COUNT(*) " + joins + sql_where,
                                    cte_params + params).fetchone()[0]
            rows = [dict(r) for r in self.db.execute(
                cte + "SELECT i.id, i.source_id, i.title, i.link, i.summary, i.published, i.first_seen, i.lang, "
                "s.name AS source_name, s.region, s.country_iso, s.scope, s.exile, s.covers, s.kind "
                + joins + sql_where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                cte_params + params + [limit, offset])]
        return rows, total, ("fts" if self.fts else "like")
```

- [ ] **Step 6: Clearing translations**

Append to `class Store`:

```python
    def clear_translations(self, engine=None, src_lang=None, target=TARGET_LANG):
        """Put items back in the translation queue. With an engine, delete that engine's
        translations; always clear the failure counts. src_lang limits both to items whose original
        is in that language. Returns (translations deleted, failure records cleared)."""
        only = ""
        extra = []
        if src_lang:
            only = " AND item_id IN (SELECT id FROM items WHERE lang = ?)"
            extra = [src_lang]
        with self.lock:
            self.db.execute("BEGIN")
            try:
                deleted = 0
                if engine:
                    deleted = self.db.execute("DELETE FROM translations WHERE lang = ? AND engine = ?" + only,
                                              [target, engine] + extra).rowcount
                cleared = self.db.execute("DELETE FROM translation_failures WHERE lang = ?" + only,
                                          [target] + extra).rowcount
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return deleted, cleared
```

- [ ] **Step 7: The tool**

```python
#!/usr/bin/env python3
# Put translated items back in the translation queue. Safe while the server runs (WAL): the
# worker picks them up on its next loop. Run it after changing "translate_engine" in config.json.
# Usage: py tools/retranslate.py --engine nllb-600m [--lang zh]   delete that engine's translations
#        py tools/retranslate.py --failures [--lang zh]           only clear failure counts
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newscentral.config import CONFIG_FILE, ConfigError, TARGET_LANG, load_config, paths   # noqa: E402
from newscentral.store import SchemaMismatch, Store                                        # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Put translations back in the queue")
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--engine", help="delete the translations made by this engine (and clear failures)")
    what.add_argument("--failures", action="store_true", help="only clear the failure counts")
    ap.add_argument("--lang", help="only items whose original language is this code")
    ap.add_argument("--config", default=CONFIG_FILE, help="path to config.json")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(e)
        return 2
    try:
        store = Store(paths(cfg)["db"], migrate=False)
    except (SchemaMismatch, FileNotFoundError) as e:
        print(e)
        return 1
    try:
        deleted, cleared = store.clear_translations(args.engine, args.lang, TARGET_LANG)
    finally:
        store.db.close()
    print("%d translation(s) deleted, %d failure record(s) cleared" % (deleted, cleared))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Run the tests**

Run: `py -m unittest tests.test_search tests.test_retranslate -v`, then `py -m unittest discover -s tests`.
Expected: all pass, `TestSearchLike` included (the `LIKE` path also searches translations).

- [ ] **Step 9: Commit**

```bash
git add newscentral/store.py tools/retranslate.py tests/test_search.py tests/test_retranslate.py tests/helpers.py
git commit -m "Search translations in the archive and add tools/retranslate.py" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Translation fields in the API and the Markdown export

**Files:**
- Modify: `newscentral/store.py`, `newscentral/api.py`
- Create: `tests/test_translation_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_translation_api.py
import time
import unittest

from tests.helpers import ApiCase

AR_TITLE = "\u0627\u0631\u062a\u0641\u0639 \u0645\u0639\u062f\u0644 \u0627\u0644\u062a\u0636\u062e\u0645"
AR_SUMMARY = "\u0627\u0644\u0623\u062e\u0628\u0627\u0631 \u0627\u0644\u0627\u0642\u062a\u0635\u0627\u062f\u064a\u0629"
ES_TITLE = "Sube la inflaci\u00f3n"
ES_SUMMARY = "Noticias econ\u00f3micas"


class TestTranslationApi(ApiCase):
    def setUp(self):
        super().setUp()
        self.add("ahram", AR_TITLE, "https://example.com/ar", AR_SUMMARY)
        self.add("elpais", "Titular", "https://example.com/es", "Resumen")
        self.ar = self.item_id("https://example.com/ar")
        self.es = self.item_id("https://example.com/es")

    def item_id(self, link):
        return self.store.db.execute("SELECT id FROM items WHERE link = ?", (link,)).fetchone()[0]

    def translate(self, title=True, summary=True, now=None):
        rows = []
        if title:
            rows.append({"item_id": self.ar, "field": "title", "lang": "es", "text": ES_TITLE,
                         "engine": "fake", "error": ""})
        if summary:
            rows.append({"item_id": self.ar, "field": "summary", "lang": "es", "text": ES_SUMMARY,
                         "engine": "fake", "error": ""})
        self.store.save_translations(rows, now)

    def items(self, query="since=0"):
        return self.get("/api/items?" + query)[1]

    def test_items_carry_tr_only_when_translated(self):
        body = self.items()
        self.assertEqual(body["tr_seen"], 0)
        self.assertFalse(any("tr" in i for i in body["items"]))
        self.translate()
        by_id = {i["id"]: i for i in self.items()["items"]}
        self.assertEqual(by_id[self.ar]["tr"], {"title": ES_TITLE, "summary": ES_SUMMARY, "engine": "fake"})
        self.assertNotIn("tr", by_id[self.es])
        self.assertEqual(by_id[self.ar]["title"], AR_TITLE)          # the original is never replaced

    def test_summary_only_translation(self):
        self.translate(title=False)
        item = [i for i in self.items()["items"] if i["id"] == self.ar][0]
        self.assertEqual(item["tr"], {"title": "", "summary": ES_SUMMARY, "engine": "fake"})

    def test_translations_that_arrive_later_reach_the_feed(self):
        first = self.items()
        self.assertNotIn("translated", first)
        self.translate(now=time.time())
        later = self.items("since=%f&tr_since=1" % (time.time() + 60))
        self.assertEqual(later["items"], [])
        self.assertEqual(later["translated"][self.ar]["title"], ES_TITLE)
        self.assertGreater(later["tr_seen"], 1)
        again = self.items("since=%f&tr_since=%f" % (time.time() + 60, later["tr_seen"]))
        self.assertEqual(again["translated"], {})

    def test_old_items_are_not_sent_as_translated(self):
        self.store.db.execute("UPDATE items SET first_seen = 1000 WHERE id = ?", (self.ar,))
        self.store.db.commit()
        self.translate(now=time.time())
        self.assertEqual(self.items("since=0&tr_since=1")["translated"], {})

    def test_search_carries_tr(self):
        self.translate()
        _, body = self.get("/api/search?source=ahram")
        self.assertEqual(body["items"][0]["tr"]["title"], ES_TITLE)

    def test_saved_and_export_carry_both(self):
        self.translate()
        self.post("/api/save", {"id": self.ar})
        self.post("/api/save", {"id": self.es})
        saved = {x["id"]: x for x in self.get("/api/saved")[1]["saved"]}
        self.assertEqual(saved[self.ar]["tr"]["summary"], ES_SUMMARY)
        self.assertNotIn("tr", saved[self.es])
        _, md = self.get("/api/export.md")
        self.assertIn("## " + ES_TITLE, md)
        self.assertIn("- Original title (ar): " + AR_TITLE, md)
        self.assertIn("- Summary (machine translation from ar, fake): " + ES_SUMMARY, md)
        self.assertIn("- Original summary (ar): " + AR_SUMMARY, md)
        self.assertIn("## Titular", md)
        self.assertIn("- Summary: Resumen", md)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `py -m unittest tests.test_translation_api -v`
Expected: `KeyError: 'tr_seen'` and `KeyError: 'tr'` failures.

- [ ] **Step 3: Store lookups**

Append to `class Store` in `newscentral/store.py`:

```python
    def translations_for(self, ids, lang=TARGET_LANG):
        """{item_id: {"title", "summary", "engine"}} for the given ids that have any translation.
        A missing field is "". The engine is the title's, or the summary's when only that exists."""
        out = {}
        ids = list(ids)
        with self.lock:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                rows = self.db.execute(
                    "SELECT item_id, field, text, engine FROM translations WHERE lang = ? AND item_id IN (%s)"
                    % ",".join("?" * len(chunk)), [lang] + chunk).fetchall()
                for r in rows:
                    tr = out.setdefault(r["item_id"], {"title": "", "summary": "", "engine": ""})
                    tr[r["field"]] = r["text"]
                    if r["field"] == "title" or not tr["engine"]:
                        tr["engine"] = r["engine"]
        return out

    def max_translation_created(self, lang=TARGET_LANG):
        with self.lock:
            value = self.db.execute("SELECT MAX(created) FROM translations WHERE lang = ?", (lang,)).fetchone()[0]
        return value or 0

    def translated_since(self, after, lang, min_first_seen):
        """Translations created after `after` for items first seen from min_first_seen on."""
        with self.lock:
            ids = [r[0] for r in self.db.execute(
                "SELECT DISTINCT t.item_id FROM translations t JOIN items i ON i.id = t.item_id "
                "WHERE t.lang = ? AND t.created > ? AND i.first_seen >= ?", (lang, after, min_first_seen))]
        return self.translations_for(ids, lang)     # outside the lock: it takes the lock itself
```

- [ ] **Step 4: API**

In `newscentral/api.py`, add above `def status_payload(app):`:

```python
def attach_translations(app, items, key="id"):
    """Add tr: {title, summary, engine} to the items that have a translation. Returns items."""
    trs = app.store.translations_for([it[key] for it in items], TARGET_LANG)
    for it in items:
        tr = trs.get(it[key])
        if tr:
            it["tr"] = tr
    return items
```

In `items_payload`, replace `    out.sort(key=lambda x: x["t"], reverse=True)` with:

```python
    out.sort(key=lambda x: x["t"], reverse=True)
    attach_translations(app, out)
```

In `search_payload`, replace the `return` line with:

```python
    attach_translations(app, items)
    return {"items": items, "total": total, "engine": engine, "limit": limit, "offset": offset}
```

Replace `export_markdown` with:

```python
def export_markdown(app, only_undigested):
    rows = attach_translations(app, app.store.saved())
    if only_undigested:
        rows = [r for r in rows if not r["digested"]]
    lines = ["# Saved news for digestion", "",
             "Exported %s \u00b7 %d items" % (time.strftime("%Y-%m-%d %H:%M"), len(rows)), ""]
    for r in rows:
        tr = r.get("tr") or {}
        lang = r["lang"] or "?"
        lines.append("## " + (tr.get("title") or r["title"] or ""))
        if tr.get("title"):
            lines.append("- Original title (%s): %s" % (lang, r["title"] or ""))
            lines.append("- Title: machine translation from %s, %s" % (lang, tr.get("engine") or "?"))
        where = ", ".join(x for x in (r["country"], r["lang"]) if x)
        lines.append("- Source: %s%s \u00b7 %s" % (r["source_name"], " (%s)" % where if where else "", r["region"]))
        if r["published"]:
            lines.append("- Published: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(r["published"])))
        lines.append("- Link: " + (r["link"] or ""))
        if tr.get("summary"):
            lines.append("- Summary (machine translation from %s, %s): %s" % (lang, tr.get("engine") or "?",
                                                                            tr["summary"]))
            if r["summary"]:
                lines.append("- Original summary (%s): %s" % (lang, r["summary"]))
        elif r["summary"]:
            lines.append("- Summary: " + r["summary"])
        if r["note"]:
            lines.append("- My note: " + " ".join(r["note"].split()))
        lines.append("")
    return "\n".join(lines)
```

In `Handler.do_GET`, replace the `/api/items` branch with:

```python
            elif path == "/api/items":
                try:
                    since = float((q.get("since") or ["0"])[0] or 0)
                except ValueError:
                    since = 0.0
                try:
                    tr_since = float((q.get("tr_since") or ["0"])[0] or 0)
                except ValueError:
                    tr_since = 0.0
                payload = status_payload(app)
                # Read the newest translation time before the items, so nothing written in between
                # is missed by the next poll.
                payload["tr_seen"] = app.store.max_translation_created(TARGET_LANG)
                payload["items"], payload["truncated"] = items_payload(app, since)
                if tr_since > 0:
                    payload["translated"] = app.store.translated_since(
                        tr_since, TARGET_LANG, time.time() - LIVE_WINDOW_HOURS * 3600 - 3600)
                self._json(payload)
```

Replace the `/api/saved` branch with:

```python
            elif path == "/api/saved":
                self._json({"saved": attach_translations(app, app.store.saved())})
```

- [ ] **Step 5: Run the tests**

Run: `py -m unittest tests.test_translation_api tests.test_api -v`, then `py -m unittest discover -s tests`.
Expected: all pass (`test_save_update_export_unsave` in `test_api.py` still finds `## Titular`).

- [ ] **Step 6: Commit**

```bash
git add newscentral/store.py newscentral/api.py tests/test_translation_api.py
git commit -m "Return translations with items, search results and saved items, and export both" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Translated display in the reader

**Files:**
- Modify: `static/js/api.js`, `static/js/reader.js`, `static/index.html`

- [ ] **Step 1: API wrapper**

In `static/js/api.js`, replace `fetchItems` with:

```javascript
export function fetchItems(since, trSince) {
  return getJSON('/api/items?since=' + since + (trSince ? '&tr_since=' + trSince : ''));
}
```

- [ ] **Step 2: State and loading in `static/js/reader.js`**

In the `S` object, replace `  worker: [], workerEnabled: true,` (Task 5) with:

```javascript
  worker: [], workerEnabled: true, trSeen: 0,
```

Replace the whole `loadItems` function with:

```javascript
// Text the live-feed search matches: original, translation, outlet and country.
function itemText(it, src) {
  var tr = it.tr || {};
  return norm(it.title + ' ' + (it.summary || '') + ' ' + (tr.title || '') + ' ' + (tr.summary || '') + ' ' +
              (src ? src.name + ' ' + (src.country || '') : ''));
}

async function loadItems() {
  var d = await fetchItems(S.maxSeen, S.trSeen);
  var added = 0;
  for (var i = 0; i < d.items.length; i++) {
    var it = d.items[i];
    if (S.items.has(it.id)) continue;
    it._sport = isSportOrCeleb(it.link);
    it._text = itemText(it, S.sources[it.s]);
    S.items.set(it.id, it);
    if (it.fs > S.maxSeen) S.maxSeen = it.fs;
    added++;
  }
  // Translations finished after their item reached the page.
  var translated = d.translated || {};
  for (var tid in translated) {
    if (!Object.prototype.hasOwnProperty.call(translated, tid)) continue;
    var known = S.items.get(tid);
    if (!known) continue;
    known.tr = translated[tid];
    known._text = itemText(known, S.sources[known.s]);
    added++;
  }
  if (d.tr_seen) S.trSeen = d.tr_seen;
  S.running = d.running; S.updated = d.updated;
  S.truncated = !!d.truncated;
  var minT = Date.now() / 1000 - 96 * 3600;
  for (var entry of S.items) {
    var id = entry[0], item = entry[1];
    if (item.t < minT && item.fs < minT) S.items.delete(id);
  }
  return added;
}
```

- [ ] **Step 3: The language filter keeps translated items**

In `compute`, replace

```javascript
    if (!S.showLangs && S.readLangs.indexOf(it.lang || src.lang) === -1) {
```

with:

```javascript
    // Hidden: unread languages with no translated title yet.
    if (!S.showLangs && S.readLangs.indexOf(it.lang || src.lang) === -1 && !(it.tr && it.tr.title)) {
```

- [ ] **Step 4: Rendering helpers**

Add above `function itemHTML(it, now, src) {`:

```javascript
// Title block: the Spanish title as the link, the original underneath in a smaller grey line.
function titleHTML(it) {
  var href = safeHref(it.link);
  if (it.tr && it.tr.title) {
    return '<a class="title" lang="es" href="' + href + '" target="_blank" rel="noopener">' + esc(it.tr.title) + '</a>' +
      '<div class="orig" dir="auto" lang="' + esc(it.lang || '') + '">' + esc(it.title) + '</div>';
  }
  return '<a class="title" dir="auto" href="' + href + '" target="_blank" rel="noopener">' + esc(it.title) + '</a>';
}

// Summary block: the translation, with the original summary behind a click.
function summaryHTML(it, open) {
  var cls = 'sum' + (open ? ' open' : '');
  if (it.tr && it.tr.summary) {
    return '<div class="' + cls + '" lang="es">' + esc(it.tr.summary) + '</div>' +
      (it.summary ? '<details class="orig-sum"><summary>Resumen original</summary><div dir="auto" lang="' +
                    esc(it.lang || '') + '">' + esc(it.summary) + '</div></details>' : '');
  }
  return it.summary ? '<div class="' + cls + '" dir="auto">' + esc(it.summary) + '</div>' : '';
}

function langTag(it, lang) {
  if (it.tr && (it.tr.title || it.tr.summary)) {
    return '<span class="tag" title="Machine translation (' + esc(it.tr.engine || '') + ')">traducido de ' +
      esc(lang) + '</span>';
  }
  return lang && lang !== 'en' ? '<span class="tag">' + esc(lang) + '</span>' : '';
}
```

- [ ] **Step 5: Use them in the feed and archive items**

In `itemHTML`, replace

```javascript
  var tags = [];
  if (src.lang && src.lang !== 'en') tags.push('<span class="tag">' + esc(src.lang) + '</span>');
```

with:

```javascript
  var tags = [];
  var lang = it.lang || src.lang;
  var tagLang = langTag(it, lang);
  if (tagLang) tags.push(tagLang);
```

Replace

```javascript
  if (src.lang && src.lang !== 'en' && src.lang !== 'es') {
```

with:

```javascript
  if (lang && lang !== 'en' && lang !== 'es' && !(it.tr && it.tr.title)) {
```

Replace

```javascript
    '<a class="title" dir="auto" href="' + safeHref(it.link) + '" target="_blank" rel="noopener">' + esc(it.title) + '</a>' +
    (it.summary ? '<div class="sum" dir="auto">' + esc(it.summary) + '</div>' : '') + '</div>' +
```

with:

```javascript
    titleHTML(it) + summaryHTML(it, false) + '</div>' +
```

- [ ] **Step 6: Use them in the saved view**

In `renderSaved`, replace

```javascript
      esc(x.region || '') + '</span><span>' + (x.published ? esc(new Date(x.published * 1000).toLocaleString()) : '') + '</span></div>' +
      '<a class="title" dir="auto" href="' + safeHref(x.link) + '" target="_blank" rel="noopener">' + esc(x.title) + '</a>' +
      (x.summary ? '<div class="sum open" dir="auto">' + esc(x.summary) + '</div>' : '') +
```

with:

```javascript
      esc(x.region || '') + '</span><span>' + (x.published ? esc(new Date(x.published * 1000).toLocaleString()) : '') + '</span>' +
      (x.tr ? langTag(x, x.lang) : '') + '</div>' +
      titleHTML(x) + summaryHTML(x, true) +
```

Saved rows carry `lang` (the outlet's language at save time), `title`, `summary`, `link` and `tr`, which is all `titleHTML` and `summaryHTML` read.

- [ ] **Step 7: Styles**

In `static/index.html`, after the rule `.sum.open { -webkit-line-clamp: unset; display: block; }` add:

```css
.orig { color: var(--muted); font-size: 13px; margin: 0 0 2px; }
.orig-sum { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.orig-sum summary { cursor: pointer; }
.orig-sum div { font-size: 13px; margin-top: 2px; }
```

- [ ] **Step 8: Verify in a browser**

Run `node --check static/js/reader.js` and `node --check static/js/api.js`; search both for `??` and `?.` (expected: none in code).

Create the scratch setup from Task 7, Step 8 (fake engine, `$TMP/nc-check`, port 8799), start the server in the background and wait about two minutes for the first fetch and a few worker batches. Open `http://127.0.0.1:8799/` with the console open. Check, in this order:

1. With "Show other languages" off, items from French or Portuguese outlets appear, with the title `[fr>es] ...` as the link, the original title in a smaller grey line under it, and a tag reading `traducido de fr`. Inspecting that grey line shows `dir="auto"` and `lang="fr"`.
2. Such an item has no "translate" (Google) link; an untranslated item in a language other than es or en, if any is visible with "Show other languages" on, still has it.
3. A translated summary shows `[fr>es] ...`; clicking "Resumen original" opens the original summary; clicking the summary text still expands it.
4. Typing a word that appears only in the original French title into the search box finds the item; typing `fr>es` also finds it (matches the translation).
5. Leave the page open for two poll cycles (two minutes): items translated after the page loaded update their counts in the region chips without a reload, and the console stays clean.
6. Switch the time window to "Archive (search)" and search for a word of a translated title: the result shows the same translated layout.
7. Save a translated item with the star, open Saved: it shows the Spanish title, the grey original, the tag and the open translated summary. "Copy for Claude" copies Markdown with `- Original title (fr): ...` and `- Summary (machine translation from fr, fake): ...`.
8. Sources view: the worker line and the translation line from Tasks 5 and 7 are still there.

Stop the server and delete `$TMP/nc-check`.

- [ ] **Step 9: Commit**

```bash
git add static/js/api.js static/js/reader.js static/index.html
git commit -m "Show translated titles and summaries with the original in the reader" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

Continue with Task 11 in `2026-11-11-translation-part3.md`.
