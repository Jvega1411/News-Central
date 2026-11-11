# Collection Foundation Implementation Plan, part 1 of 3

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn News Central into a continuous collector with a permanent, searchable, language-safe archive, ready for the enrichment and briefing specs.

**Architecture:** `server.py` is split into a `newscentral/` package with one responsibility per module and an `App` object passed to the HTTP handler, so the API is testable against a temporary database. All feed text goes through one normalization function before it is stored. The archive is never pruned, source attribution is snapshotted into SQLite, and search runs server-side on FTS5 with a `LIKE` fallback.

**Tech Stack:** Python 3.8+ standard library only (`sqlite3`, `http.server`, `unittest`, `ctypes` on Windows), SQLite FTS5, vanilla ES modules, PowerShell 5.1 for the Windows host scripts.

**Spec:** `docs/superpowers/specs/2026-11-11-collection-foundation-design.md`

**Parts:** this file holds the shared rules and Tasks 1 to 5. Tasks 6 to 9 are in `2026-11-11-collection-foundation-part2.md`, Tasks 10 to 16 in `2026-11-11-collection-foundation-part3.md`. Tasks run in numeric order.

---

## Ground rules for every task

- Windows dev machine. Python is `py`. Run tests with `py -m unittest discover -s tests`. There is no pytest.
- Python 3.8 compatible syntax only: no `match`, no `dict | dict`, no `list[str]` at runtime, no `str.removeprefix`.
- Frontend: no `??`, no `?.`, `var` and function declarations as in the existing files, no `innerHTML` with unescaped data (use the existing `esc()`), valid `dataset` keys only.
- Never commit anything under `data/`. `config.json` is gitignored from Task 2 on.
- This machine has live internet. After fetcher changes run `py server.py --check`.
- After any frontend change load the page in a real browser and check the console. Syntax checks (`node --check`) do not catch DOM runtime errors.
- One logical change per commit, imperative subject.
- Text files use LF (`.gitattributes` enforces it).

## Deviations from the spec, decided while planning

The spec is amended in Task 16 to match these:

1. **Migration numbers follow build order.** Migrations apply in ascending order and a database never goes back, so the number must match the order they ship: 2 = sources snapshot and `items.lang`, 3 = renormalize, 4 = `fetch_runs`. The spec listed FTS as 4 and `fetch_runs` as 5.
2. **The FTS index is not a numbered migration.** It depends on the SQLite build, not on the schema version. A database created on a machine without FTS5 and later moved to one with it must still get the index. `Store.ensure_fts()` runs at every startup and creates the index when it is missing and FTS5 is available.
3. **Charset order is BOM, XML declaration, UTF-8, HTTP charset, Latin fallback.** Many servers send `charset=ISO-8859-1` by default for feeds that are really UTF-8. Latin-1 decodes any bytes without error, so trusting the header before trying UTF-8 would create mojibake. Strict UTF-8 decoding almost never succeeds by accident on text in another encoding.
4. **`sources` sits above `feeds` in the module order**, because `feeds` needs the language reference to decide whether the Latin fallback applies.
5. **`/api/search` also accepts `scope`**, so the transnational buckets filter the archive like they filter the live feed.
6. **Renormalizing stored rows skips markup stripping.** Stored text is already markup-free; running the tag stripper again would delete legitimate text such as `inflation <2% and growth >3%`. The migration applies only the Unicode steps.

## File structure

```
server.py                       entry point only
config.example.json             committed example
newscentral/
  __init__.py                   empty
  config.py                     paths, constants, config file loading
  text.py                       normalize(), canonical(), decode_bytes()
  http_client.py                http_get, curl_get, get_with_fallback, short_error
  sources.py                    sources.json, countries, languages, press freedom, validation
  feeds.py                      feed parsing, discovery, fetch_source
  store.py                      Store, migrations, FTS
  fetcher.py                    Fetcher thread, source_payload
  stats.py                      gaps(), volume() pure functions
  api.py                        App, payload builders, Handler, Server
  cli.py                        argument parsing, --check, logging, keep-awake, main
geo/languages.json              ISO 639-1 reference: name, script, rtl
tools/check_candidates.py       fetch a list of candidate sources and print a table
tools/windows/install.ps1       scheduled task + AC power settings
tools/windows/uninstall.ps1     undo both
tests/
  helpers.py                    FakeFetcher, ApiCase (server on an ephemeral port)
  fixtures/                     feed samples
  test_*.py
```

Suggested models for subagent execution: Tasks 1, 5, 14, 15, 16 are mechanical (small model). Tasks 3, 4, 6, 10, 11, 12 need care with Unicode, SQL or DOM state (mid model). Review every task before the next one starts.

---

### Task 1: Split `server.py` into the `newscentral` package

No behaviour change. Code moves verbatim except where this task says otherwise.

**Files:**
- Create: `newscentral/__init__.py`, `newscentral/config.py`, `newscentral/text.py`, `newscentral/http_client.py`, `newscentral/sources.py`, `newscentral/feeds.py`, `newscentral/store.py`, `newscentral/fetcher.py`, `newscentral/api.py`, `newscentral/cli.py`
- Create: `tests/helpers.py`, `tests/test_api.py`
- Modify: `server.py` (becomes the entry point), every file in `tests/`

- [ ] **Step 1: Record the baseline**

Run: `py -m unittest discover -s tests`
Expected: `Ran 23 tests` and `OK`.

- [ ] **Step 2: Create `newscentral/__init__.py` (empty) and `newscentral/config.py`**

```python
# newscentral/config.py
# Paths and tuning constants. No imports from the rest of the package.
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_FILE = os.path.join(BASE_DIR, "sources.json")
COUNTRIES_FILE = os.path.join(BASE_DIR, "geo", "countries.json")
PRESS_FREEDOM_FILE = os.path.join(BASE_DIR, "geo", "press-freedom-2026.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")
INDEX_FILE = os.path.join(STATIC_DIR, "index.html")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_FILE = os.path.join(DATA_DIR, "news.db")
STATUS_FILE = os.path.join(DATA_DIR, "sources_status.json")

REFRESH_SECONDS = 900          # background fetch every 15 minutes
ERROR_RETRY_SECONDS = 3600     # failing sources retried hourly, or on manual refresh
KEEP_HOURS = 96                # renamed LIVE_WINDOW_HOURS in Task 6
FETCH_TIMEOUT = 20
WORKERS = 16
MAX_ITEMS_PER_FEED = 60
MAX_BYTES = 12 * 1024 * 1024
SUMMARY_CHARS = 500
```

- [ ] **Step 3: Move the code**

Move each name from `server.py` into the module shown, unchanged. Each module imports what it needs from the standard library and from the modules above it in this table.

| Module | Names moved from `server.py` |
|---|---|
| `text.py` | `TAG_RE`, `WS_RE`, `APPEARED_RE`, `decode_bytes`, `clean_text` |
| `http_client.py` | `USER_AGENT`, `ALT_USER_AGENT`, `FEED_ACCEPT`, `BLOCK_CODES`, `CURL`, `make_ssl_context`, `SSL_CTX`, `_Redirects`, `OPENER`, `NotModified`, `http_get`, `curl_get`, `get_with_fallback`, `short_error` |
| `sources.py` | `_COUNTRIES`, `load_countries`, `_PRESS_FREEDOM`, `load_press_freedom`, `load_sources`, `SCOPE_VOCAB`, `validate_sources`, `_country_label` (rename to `country_label`) |
| `feeds.py` | `XML_ENTITIES`, `CTRL_RE`, `ISO_RE`, `LINK_TAG_RE`, `HREF_RE`, `TRACKING_PARAMS`, `repair_xml`, `parse_date`, `strip_namespaces`, `child_text`, `entry_link`, `normalize_link`, `parse_entry`, `regex_items`, `parse_feed`, `discover_feeds`, `configured_feeds`, `fetch_source` |
| `store.py` | `MIGRATIONS`, `migration`, `run_migrations`, `_m1_saved_country_iso`, `sort_time`, `Store` |
| `fetcher.py` | `source_payload`, `Fetcher` |
| `api.py` | `status_payload`, `items_payload`, `aggregate_geo`, `geo_payload`, `export_markdown`, `static_path`, `Handler`, `Server` |
| `cli.py` | `lan_ip`, `run_check`, `main` |

Rules for the move:
- `sources.py` must import the file paths with `from .config import SOURCES_FILE, COUNTRIES_FILE, PRESS_FREEDOM_FILE`, so that `sources.PRESS_FREEDOM_FILE` is a module attribute the loader reads. A test patches it.
- In `store.py`, replace the constant `SCHEMA_VERSION` with a function, so migrations added later in the file are counted:

```python
def schema_version():
    return max((v for v, _ in MIGRATIONS), default=0)
```

- `static_path` in `api.py` uses `STATIC_DIR` from config: `return os.path.join(STATIC_DIR, *parts)`.

- [ ] **Step 4: Replace the `STORE` / `FETCHER` globals with an `App` object in `api.py`**

Add at the top of `api.py`, and change the four payload builders to take `app`:

```python
class App:
    """Everything a request handler needs. One per server; tests build their own."""

    def __init__(self, store, fetcher, config=None):
        self.store = store
        self.fetcher = fetcher
        self.config = config or {}


def status_payload(app):
    f = app.fetcher
    return {"running": f.running, "updated": f.last_cycle, "sources_error": f.sources_error}


def items_payload(app, since):
    min_time = time.time() - KEEP_HOURS * 3600
    known = app.fetcher.source_map()
    out = []
    for r in app.store.items(max(since, min_time - 3600)):
        if r["source_id"] not in known:
            continue
        t = sort_time(r["published"], r["first_seen"])
        if t < min_time:
            continue
        out.append({"id": r["id"], "s": r["source_id"], "t": t, "fs": r["first_seen"],
                    "title": r["title"], "link": r["link"], "summary": r["summary"] or ""})
    out.sort(key=lambda x: x["t"], reverse=True)
    return out


def geo_payload(app, since):
    cutoff = time.time() - KEEP_HOURS * 3600 - 3600
    rows = app.store.items(max(since, cutoff))
    out = aggregate_geo(app.fetcher.sources, rows, load_countries(), since)
    out["updated"] = app.fetcher.last_cycle
    out["press_freedom"] = load_press_freedom()
    return out
```

`export_markdown(app, only_undigested)` reads `app.store.saved()`; the rest of its body is unchanged.

In `Handler.do_GET` and `Handler.do_POST`, add `app = self.server.app` as the first line of the `try` block and replace every `STORE` with `app.store`, every `FETCHER` with `app.fetcher`, and every payload call with the `app` form, for example `self._json(geo_payload(app, since))`. `source_payload(FETCHER.sources, STORE)` becomes `source_payload(app.fetcher.sources, app.store)`.

- [ ] **Step 5: Wire `cli.main` and reduce `server.py`**

In `cli.main`, replace the `global STORE, FETCHER` block with:

```python
    store = Store(DB_FILE)
    fetcher = Fetcher(store)
    url = "http://127.0.0.1:%d" % args.port
    try:
        httpd = Server(("0.0.0.0" if args.lan else "127.0.0.1", args.port), Handler)
    except OSError:
        print("Port %d is busy; News Central is probably already running at %s" % (args.port, url))
        if args.open:
            webbrowser.open(url)
        return 1
    httpd.app = App(store, fetcher)
    fetcher.start()
```

The rest of `main` is unchanged. `server.py` becomes exactly:

```python
#!/usr/bin/env python3
# News Central: local world-news reader. Entry point; the code lives in newscentral/.
# Standard library only, Python 3.8+.
import sys

from newscentral.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Point the existing tests at the package**

Replace `import server` and every `server.X` reference using this map:

| Test file | New import | Replacements |
|---|---|---|
| `test_migrations.py` | `from newscentral import store` | `server.Store` to `store.Store`; `server.SCHEMA_VERSION` to `store.schema_version()` |
| `test_geo.py` | `from newscentral import api` | `server.aggregate_geo` to `api.aggregate_geo` |
| `test_countries.py` | `from newscentral import sources` | `server.load_countries` to `sources.load_countries` |
| `test_press_freedom.py` | `from newscentral import sources` | every `server.` to `sources.` |
| `test_validate_sources.py` | `from newscentral import sources` | every `server.` to `sources.` |
| `test_static_path.py` | `from newscentral import api, config` | `server.static_path` to `api.static_path`; `server.BASE_DIR` to `config.BASE_DIR` |

- [ ] **Step 7: Run the suite**

Run: `py -m unittest discover -s tests`
Expected: `Ran 23 tests`, `OK`.

- [ ] **Step 8: Add the API test harness**

```python
# tests/helpers.py
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from newscentral.api import App, Handler, Server
from newscentral.store import Store

SOURCES = [
    {"id": "elpais", "name": "El País", "region": "Spain", "country_iso": "ES", "lang": "es", "kind": "news"},
    {"id": "bbc", "name": "BBC World", "region": "Global", "country_iso": "GB", "lang": "en", "kind": "news"},
    {"id": "ahram", "name": "Al-Ahram", "region": "Middle East & N. Africa", "country_iso": "EG",
     "lang": "ar", "kind": "news", "state_media": True},
    {"id": "meduza", "name": "Meduza", "region": "Eurasia", "country_iso": "LV", "exile": True,
     "covers": "RU", "lang": "ru", "kind": "news"},
    {"id": "ecb", "name": "ECB", "region": "Europe & EU", "country_iso": None,
     "scope": "Intergovernmental", "lang": "en", "kind": "official"},
]


class FakeFetcher:
    """Stands in for the fetch thread: same attributes the API reads, no network."""

    def __init__(self, sources):
        self.sources = sources
        self.running = False
        self.last_cycle = 0.0
        self.sources_error = ""
        self.refreshed = 0

    def source_map(self):
        return {s["id"]: s for s in self.sources}

    def refresh_now(self):
        self.refreshed += 1
        return True


class ApiCase(unittest.TestCase):
    """Starts the real handler on an ephemeral port against a temporary database."""

    sources = SOURCES
    config = None

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.dir, "news.db"))
        self.fetcher = FakeFetcher(list(self.sources))
        self.app = App(self.store, self.fetcher, self.config)
        self.httpd = Server(("127.0.0.1", 0), Handler)
        self.httpd.app = self.app
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.store.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _open(self, req):
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def get(self, path):
        status, ctype, body = self._open(urllib.request.Request(self.base + path))
        if "json" in ctype:
            return status, json.loads(body.decode("utf-8"))
        return status, body.decode("utf-8")

    def post(self, path, body=None, header=True):
        headers = {"Content-Type": "application/json"}
        if header:
            headers["X-NewsCentral"] = "1"
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode("utf-8"),
                                     headers=headers, method="POST")
        status, ctype, raw = self._open(req)
        return status, json.loads(raw.decode("utf-8"))

    def add(self, source_id, title, link, summary="", published=None):
        self.store.add_items(source_id, [{"title": title, "link": link, "summary": summary,
                                          "published": published}])
```

```python
# tests/test_api.py
import unittest

from tests.helpers import ApiCase


class TestApi(ApiCase):
    def test_status(self):
        status, body = self.get("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["running"], False)

    def test_items_returns_stored_item(self):
        self.add("elpais", "Titular", "https://example.com/a", "Resumen")
        status, body = self.get("/api/items?since=0")
        self.assertEqual(status, 200)
        self.assertEqual([i["title"] for i in body["items"]], ["Titular"])
        self.assertEqual(body["items"][0]["s"], "elpais")

    def test_items_skips_unknown_source(self):
        self.add("gone", "Old outlet", "https://example.com/b")
        _, body = self.get("/api/items?since=0")
        self.assertEqual(body["items"], [])

    def test_sources_lists_every_source(self):
        _, body = self.get("/api/sources")
        self.assertEqual({s["id"] for s in body["sources"]}, {s["id"] for s in self.sources})

    def test_geo_counts_exile_under_covers(self):
        self.add("meduza", "Новости", "https://example.com/c")
        _, body = self.get("/api/geo?since=0")
        self.assertEqual(body["countries"]["RU"]["items"], 1)
        self.assertNotIn("LV", body["countries"])
        self.assertIn("press_freedom", body)

    def test_post_requires_header(self):
        status, _ = self.post("/api/refresh", header=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.fetcher.refreshed, 0)

    def test_refresh(self):
        status, body = self.post("/api/refresh")
        self.assertEqual((status, body), (200, {"started": True}))

    def test_save_update_export_unsave(self):
        self.add("elpais", "Titular", "https://example.com/a", "Resumen")
        _, body = self.get("/api/items?since=0")
        item_id = body["items"][0]["id"]
        self.assertEqual(self.post("/api/save", {"id": item_id})[0], 200)
        self.assertEqual(self.post("/api/saved/update", {"id": item_id, "note": "para la moción"})[0], 200)
        _, saved = self.get("/api/saved")
        self.assertEqual(saved["saved"][0]["note"], "para la moción")
        self.assertEqual(saved["saved"][0]["country_iso"], "ES")
        status, md = self.get("/api/export.md")
        self.assertEqual(status, 200)
        self.assertIn("## Titular", md)
        self.assertIn("para la moción", md)
        self.post("/api/unsave", {"id": item_id})
        self.assertEqual(self.get("/api/saved")[1]["saved"], [])

    def test_save_unknown_item_is_404(self):
        self.assertEqual(self.post("/api/save", {"id": "nope"})[0], 404)

    def test_unknown_path_is_404(self):
        self.assertEqual(self.get("/api/nope")[0], 404)

    def test_static_js_and_traversal(self):
        self.assertEqual(self.get("/js/main.js")[0], 200)
        self.assertEqual(self.get("/js/..%5C..%5Cserver.js")[0], 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 9: Run everything and smoke-test the real server**

Run: `py -m unittest discover -s tests`
Expected: `OK`, no failures or errors.

Run: `py server.py --check-sources`
Expected: `0 problem(s)`.

Run: `py server.py --port 8799` in the background, wait 20 seconds, then `curl -s http://127.0.0.1:8799/api/status`. Expected: JSON with `"running"`. Open `http://127.0.0.1:8799/` in a browser: the feed lists items and the console has no errors. Stop the server.

- [ ] **Step 10: Commit**

```bash
git add server.py newscentral tests
git commit -m "Split server.py into the newscentral package and add an API test harness"
```

---

### Task 2: Config file

**Files:**
- Modify: `newscentral/config.py`, `newscentral/cli.py`, `newscentral/fetcher.py`, `newscentral/api.py`, `.gitignore`
- Create: `config.example.json`, `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
import json
import os
import shutil
import tempfile
import unittest

from newscentral import config


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "config.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, obj, bom=False):
        with open(self.path, "w", encoding="utf-8-sig" if bom else "utf-8") as f:
            json.dump(obj, f)

    def test_missing_file_gives_defaults(self):
        cfg = config.load_config(self.path)
        self.assertEqual(cfg["port"], 8787)
        self.assertEqual(cfg["listen"], "127.0.0.1")
        self.assertEqual(cfg["read_langs"], ["es", "en"])
        self.assertEqual(cfg["refresh_minutes"], 15)
        self.assertIs(cfg["keep_awake"], False)
        self.assertIsNone(cfg["log_file"])

    def test_file_overrides_defaults(self):
        self.write({"port": 9000, "read_langs": ["es"]})
        cfg = config.load_config(self.path)
        self.assertEqual(cfg["port"], 9000)
        self.assertEqual(cfg["read_langs"], ["es"])
        self.assertEqual(cfg["refresh_minutes"], 15)

    def test_overrides_beat_file_and_none_is_ignored(self):
        self.write({"port": 9000})
        cfg = config.load_config(self.path, {"port": 9100, "listen": None})
        self.assertEqual(cfg["port"], 9100)
        self.assertEqual(cfg["listen"], "127.0.0.1")

    def test_file_with_bom_is_read(self):
        self.write({"port": 9000}, bom=True)
        self.assertEqual(config.load_config(self.path)["port"], 9000)

    def test_unknown_key_names_the_key(self):
        self.write({"prot": 9000})
        with self.assertRaises(config.ConfigError) as cm:
            config.load_config(self.path)
        self.assertIn("prot", str(cm.exception))

    def test_wrong_type_names_the_key(self):
        for bad in ({"port": "9000"}, {"port": True}, {"read_langs": "es"}, {"read_langs": [1]},
                    {"keep_awake": "yes"}, {"refresh_minutes": 0}):
            self.write(bad)
            with self.assertRaises(config.ConfigError) as cm:
                config.load_config(self.path)
            self.assertIn(list(bad)[0], str(cm.exception))

    def test_invalid_json_is_a_config_error(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertRaises(config.ConfigError):
            config.load_config(self.path)

    def test_paths_resolve_relative_to_repo(self):
        p = config.paths({"data_dir": "data", "log_file": "data/server.log"})
        self.assertEqual(p["db"], os.path.join(config.BASE_DIR, "data", "news.db"))
        self.assertEqual(p["status"], os.path.join(config.BASE_DIR, "data", "sources_status.json"))
        self.assertEqual(p["log"], os.path.join(config.BASE_DIR, "data", "server.log"))

    def test_absolute_data_dir_is_kept(self):
        p = config.paths({"data_dir": self.dir, "log_file": None})
        self.assertEqual(p["db"], os.path.join(self.dir, "news.db"))
        self.assertIsNone(p["log"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them**

Run: `py -m unittest tests.test_config`
Expected: errors with `module 'newscentral.config' has no attribute 'load_config'`.

- [ ] **Step 3: Implement**

Append to `newscentral/config.py` (add `import json` at the top; remove the `DATA_DIR`, `DB_FILE` and `STATUS_FILE` constants, which `paths()` replaces):

```python
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULTS = {
    "port": 8787,
    "listen": "127.0.0.1",
    "data_dir": "data",
    "read_langs": ["es", "en"],
    "refresh_minutes": 15,
    "keep_awake": False,
    "log_file": None,
}


class ConfigError(Exception):
    pass


def _check(key, value):
    def is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    if key == "port":
        ok = is_int(value) and 1 <= value <= 65535
    elif key == "refresh_minutes":
        ok = is_int(value) and value >= 1
    elif key in ("listen", "data_dir"):
        ok = isinstance(value, str) and bool(value)
    elif key == "read_langs":
        ok = isinstance(value, list) and bool(value) and all(isinstance(x, str) for x in value)
    elif key == "keep_awake":
        ok = isinstance(value, bool)
    elif key == "log_file":
        ok = value is None or (isinstance(value, str) and bool(value))
    else:
        raise ConfigError("config: unknown key %r" % key)
    if not ok:
        raise ConfigError("config: invalid value for %r: %r" % (key, value))


def load_config(path=CONFIG_FILE, overrides=None):
    """Defaults, then the config file, then overrides (command-line flags). None overrides are ignored."""
    cfg = dict(DEFAULTS)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8-sig") as f:   # utf-8-sig: PowerShell writes a BOM
                data = json.load(f)
        except ValueError as e:
            raise ConfigError("config: %s is not valid JSON: %s" % (path, e))
        if not isinstance(data, dict):
            raise ConfigError("config: %s must contain a JSON object" % path)
        for key, value in data.items():
            _check(key, value)
            cfg[key] = value
    for key, value in (overrides or {}).items():
        if value is not None:
            _check(key, value)
            cfg[key] = value
    return cfg


def paths(cfg):
    def resolve(p):
        return p if os.path.isabs(p) else os.path.join(BASE_DIR, *p.replace("\\", "/").split("/"))

    data_dir = resolve(cfg["data_dir"])
    return {
        "data": data_dir,
        "db": os.path.join(data_dir, "news.db"),
        "status": os.path.join(data_dir, "sources_status.json"),
        "log": resolve(cfg["log_file"]) if cfg.get("log_file") else None,
    }
```

- [ ] **Step 4: Run the config tests**

Run: `py -m unittest tests.test_config`
Expected: `OK`, no failures or errors.

- [ ] **Step 5: Use the config in the fetcher and the CLI**

`newscentral/fetcher.py`: the constructor takes the interval and the status path instead of reading constants.

```python
class Fetcher(threading.Thread):
    def __init__(self, store, refresh_seconds=REFRESH_SECONDS, status_file=None):
        super().__init__(daemon=True)
        self.store = store
        self.refresh_seconds = refresh_seconds
        self.status_file = status_file
        self.wake = threading.Event()
        self.force_next = True   # first cycle after start retries everything, failing sources included
        self.running = False
        self.last_cycle = 0.0
        self.sources = []
        self.sources_error = ""
        self.reload_sources()
```

In `Fetcher.run` replace `REFRESH_SECONDS` with `self.refresh_seconds`. `write_status` becomes:

```python
    def write_status(self):
        if not self.status_file:
            return
        try:
            payload = {"updated": self.last_cycle, "sources": source_payload(self.sources, self.store)}
            tmp = self.status_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.status_file)
        except Exception:
            pass
```

`newscentral/cli.py`: flags default to `None` so the file can supply them.

```python
def main():
    ap = argparse.ArgumentParser(description="News Central: local world-news reader")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--lan", action="store_true", help="also serve to other devices on this Wi-Fi")
    ap.add_argument("--config", default=CONFIG_FILE, help="path to config.json")
    ap.add_argument("--open", action="store_true", help="open the reader in the browser after starting")
    ap.add_argument("--check", action="store_true", help="test every source once, print a report and exit")
    ap.add_argument("--check-sources", action="store_true", help="validate source data model and exit")
    args = ap.parse_args()
    if args.check:
        return run_check()
    if args.check_sources:
        errs = validate_sources(load_sources(), load_countries())
        for e in errs:
            print("FAIL", e)
        print("%d problem(s)" % len(errs))
        return 1 if errs else 0

    try:
        cfg = load_config(args.config, {"port": args.port, "listen": "0.0.0.0" if args.lan else None})
    except ConfigError as e:
        print(e)
        return 2
    p = paths(cfg)
    lan = cfg["listen"] != "127.0.0.1"
    store = Store(p["db"])
    fetcher = Fetcher(store, cfg["refresh_minutes"] * 60, p["status"])
    url = "http://127.0.0.1:%d" % cfg["port"]
    try:
        httpd = Server((cfg["listen"], cfg["port"]), Handler)
    except OSError:
        print("Port %d is busy; News Central is probably already running at %s" % (cfg["port"], url))
        if args.open:
            webbrowser.open(url)
        return 1
    httpd.app = App(store, fetcher, cfg)
    fetcher.start()
    print("News Central is running at", url)
    if lan:
        ip = lan_ip()
        if ip:
            print("From your phone on the same Wi-Fi: http://%s:%d" % (ip, cfg["port"]))
    print("Keep this window open while you read. Close it (or press Ctrl+C) to stop.", flush=True)
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
```

`newscentral/api.py`: `App.__init__` falls back to the defaults, and `status_payload` exposes the read languages.

```python
        self.config = dict(DEFAULTS, **(config or {}))
```

```python
def status_payload(app):
    f = app.fetcher
    return {"running": f.running, "updated": f.last_cycle, "sources_error": f.sources_error,
            "read_langs": app.config["read_langs"]}
```

Add to `tests/test_api.py`:

```python
    def test_status_reports_read_langs(self):
        self.assertEqual(self.get("/api/status")[1]["read_langs"], ["es", "en"])
```

- [ ] **Step 6: Example file and gitignore**

`config.example.json`:

```json
{
  "port": 8787,
  "listen": "127.0.0.1",
  "data_dir": "data",
  "read_langs": ["es", "en"],
  "refresh_minutes": 15,
  "keep_awake": false,
  "log_file": null
}
```

Append to `.gitignore`:

```
# Per-machine configuration
config.json
```

- [ ] **Step 7: Verify**

Run: `py -m unittest discover -s tests`
Expected: `OK`, no failures or errors.

Run: `py server.py --port 8799` in the background, then `curl -s http://127.0.0.1:8799/api/status`.
Expected: JSON containing `"read_langs": ["es", "en"]`. Stop the server.

- [ ] **Step 8: Commit**

```bash
git add newscentral tests config.example.json .gitignore
git commit -m "Add optional config.json with flag precedence"
```

---

### Task 3: Text normalization

**Files:**
- Modify: `newscentral/text.py`, `newscentral/feeds.py`
- Create: `tests/test_text.py`

- [ ] **Step 1: Write the failing tests**

All non-ASCII samples are written as escapes so the test file cannot be corrupted by an editor.

```python
# tests/test_text.py
import unicodedata
import unittest

from newscentral import text

ZWNJ, ZWJ = "‌", "‍"
PERSIAN = "می" + ZWNJ + "خواهم"      # "mi-khaham" with ZWNJ
DEVANAGARI = "क्" + ZWJ + "ष"                           # conjunct forced with ZWJ
ARABIC = "الأخبار"
CHINESE = "中国经济新闻"
JAPANESE = "日本のニュース"
KOREAN = "한국 뉴스"
CYRILLIC = "Новости"
SAMPLES = [PERSIAN, DEVANAGARI, ARABIC, CHINESE, JAPANESE, KOREAN, CYRILLIC, "Café del día"]


class TestNormalize(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(text.normalize(None), "")
        self.assertEqual(text.normalize(""), "")

    def test_strips_markup_and_entities(self):
        self.assertEqual(text.normalize("<p>Hello &amp; <b>world</b></p>"), "Hello & world")
        self.assertEqual(text.normalize("a<script>x()</script>b"), "a b")

    def test_nfc(self):
        self.assertEqual(text.normalize("Café"), "Café")
        decomposed_korean = unicodedata.normalize("NFD", KOREAN)
        self.assertEqual(text.normalize(decomposed_korean), KOREAN)

    def test_removes_invisible_characters(self):
        self.assertEqual(text.normalize("﻿a​b­c⁠d"), "abcd")
        self.assertEqual(text.normalize("‮abc‬ ⁦def⁩‎‏"), "abc def")
        self.assertEqual(text.normalize("a\x00b\x1fc\x7fd"), "abcd")

    def test_keeps_joiners(self):
        self.assertEqual(text.normalize(PERSIAN), PERSIAN)
        self.assertEqual(text.normalize(DEVANAGARI), DEVANAGARI)

    def test_whitespace(self):
        self.assertEqual(text.normalize("  a b c d\t\n e　f "), "a b c d e f")

    def test_scripts_survive_unchanged(self):
        for s in SAMPLES:
            self.assertEqual(text.normalize(s), s)

    def test_truncates_on_word_boundary_within_limit(self):
        out = text.normalize("one two three four five six seven", 20)
        self.assertLessEqual(len(out), 20)
        self.assertTrue(out.endswith("…"))
        self.assertEqual(out, "one two three four…")

    def test_truncates_text_without_spaces(self):
        out = text.normalize(CHINESE * 10, 10)
        self.assertEqual(len(out), 10)
        self.assertTrue(out.endswith("…"))

    def test_truncation_does_not_split_a_combining_sequence(self):
        syllable = "कि"            # ka + vowel sign i (a combining mark)
        out = text.normalize(syllable * 10, 6)
        body = out[:-1]
        self.assertTrue(out.endswith("…"))
        self.assertEqual(body, syllable * (len(body) // 2))

    def test_idempotent(self):
        for s in SAMPLES + ["one two three four five six seven", syllable_text()]:
            for limit in (None, 6, 20):
                once = text.normalize(s, limit)
                self.assertEqual(text.normalize(once, limit), once)

    def test_canonical_leaves_angle_brackets_alone(self):
        s = "inflation <2% and growth >3%"
        self.assertEqual(text.canonical(s), s)


def syllable_text():
    return "कि" * 10


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them**

Run: `py -m unittest tests.test_text`
Expected: errors with `module 'newscentral.text' has no attribute 'normalize'`.

- [ ] **Step 3: Implement**

Replace `clean_text` in `newscentral/text.py` with the following. Keep `decode_bytes` as it is for now (Task 4 rewrites it).

```python
import html
import re
import unicodedata

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
APPEARED_RE = re.compile(r"\s*The post .{1,300}? appeared first on .{1,200}?\.?\s*$", re.S)
SCRIPT_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
# C0 and C1 controls except tab, LF, VT, FF, CR and NEL, which count as whitespace.
CONTROL_RE = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x84\x86-\x9f]")
JOINERS = "‌‍"   # zero-width non-joiner and joiner: meaningful in Persian and Indic scripts
_REMOVE = dict.fromkeys(map(ord, "﻿­​⁠‎‏"
                                 "‪‫‬‭‮⁦⁧⁨⁩"))
_REMOVE[0x2028] = " "
_REMOVE[0x2029] = " "


def strip_markup(value):
    s = SCRIPT_RE.sub(" ", value)
    s = TAG_RE.sub(" ", s)
    s = html.unescape(html.unescape(s))
    s = TAG_RE.sub(" ", s)
    return APPEARED_RE.sub("", s)


def _truncate(s, limit):
    """Cut to at most `limit` characters including the ellipsis."""
    cut = s[:limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut.rsplit(" ", 1)[0]
    else:
        i = len(cut)   # s[i] is the first character left out
        while i > 0 and (unicodedata.category(s[i])[0] == "M" or s[i] in JOINERS or s[i - 1] in JOINERS):
            i -= 1
        cut = s[:i]
    return cut.rstrip(" ,;:.") + "…"


def canonical(value, limit=None):
    """The Unicode half of normalize(): NFC, no invisible characters, single spaces. Safe to re-apply."""
    if not value:
        return ""
    s = unicodedata.normalize("NFC", value)
    s = s.translate(_REMOVE)
    s = CONTROL_RE.sub("", s)
    s = WS_RE.sub(" ", s).strip()
    if limit and len(s) > limit:
        s = _truncate(s, limit)
    return s


def normalize(value, limit=None):
    """The only path by which feed text reaches the database."""
    if not value:
        return ""
    return canonical(strip_markup(value), limit)
```

In `newscentral/feeds.py` replace the import of `clean_text` with `normalize` and the four calls `clean_text(...)` in `parse_entry` and `regex_items` with `normalize(...)`. The arguments do not change.

- [ ] **Step 4: Run the tests**

Run: `py -m unittest discover -s tests`
Expected: `OK`, no failures or errors.

- [ ] **Step 5: Commit**

```bash
git add newscentral/text.py newscentral/feeds.py tests/test_text.py
git commit -m "Normalize all feed text to one canonical Unicode form"
```

---

### Task 4: Language reference, `official` kind and source validation

**Files:**
- Create: `geo/languages.json`
- Modify: `newscentral/sources.py`, `newscentral/config.py`, `newscentral/cli.py`, `tests/test_validate_sources.py`
- Create: `tests/test_languages.py`

- [ ] **Step 1: Create `geo/languages.json`**

`script` is an ISO 15924 code. `rtl` marks right-to-left scripts.

```json
{
  "am": {"name": "Amharic", "script": "Ethi", "rtl": false},
  "ar": {"name": "Arabic", "script": "Arab", "rtl": true},
  "bg": {"name": "Bulgarian", "script": "Cyrl", "rtl": false},
  "bn": {"name": "Bengali", "script": "Beng", "rtl": false},
  "ca": {"name": "Catalan", "script": "Latn", "rtl": false},
  "cs": {"name": "Czech", "script": "Latn", "rtl": false},
  "da": {"name": "Danish", "script": "Latn", "rtl": false},
  "de": {"name": "German", "script": "Latn", "rtl": false},
  "el": {"name": "Greek", "script": "Grek", "rtl": false},
  "en": {"name": "English", "script": "Latn", "rtl": false},
  "es": {"name": "Spanish", "script": "Latn", "rtl": false},
  "eu": {"name": "Basque", "script": "Latn", "rtl": false},
  "fa": {"name": "Persian", "script": "Arab", "rtl": true},
  "fi": {"name": "Finnish", "script": "Latn", "rtl": false},
  "fr": {"name": "French", "script": "Latn", "rtl": false},
  "gl": {"name": "Galician", "script": "Latn", "rtl": false},
  "ha": {"name": "Hausa", "script": "Latn", "rtl": false},
  "he": {"name": "Hebrew", "script": "Hebr", "rtl": true},
  "hi": {"name": "Hindi", "script": "Deva", "rtl": false},
  "hu": {"name": "Hungarian", "script": "Latn", "rtl": false},
  "id": {"name": "Indonesian", "script": "Latn", "rtl": false},
  "it": {"name": "Italian", "script": "Latn", "rtl": false},
  "ja": {"name": "Japanese", "script": "Jpan", "rtl": false},
  "ko": {"name": "Korean", "script": "Kore", "rtl": false},
  "ms": {"name": "Malay", "script": "Latn", "rtl": false},
  "nl": {"name": "Dutch", "script": "Latn", "rtl": false},
  "no": {"name": "Norwegian", "script": "Latn", "rtl": false},
  "pl": {"name": "Polish", "script": "Latn", "rtl": false},
  "pt": {"name": "Portuguese", "script": "Latn", "rtl": false},
  "ro": {"name": "Romanian", "script": "Latn", "rtl": false},
  "ru": {"name": "Russian", "script": "Cyrl", "rtl": false},
  "sr": {"name": "Serbian", "script": "Cyrl", "rtl": false},
  "sv": {"name": "Swedish", "script": "Latn", "rtl": false},
  "sw": {"name": "Swahili", "script": "Latn", "rtl": false},
  "ta": {"name": "Tamil", "script": "Taml", "rtl": false},
  "th": {"name": "Thai", "script": "Thai", "rtl": false},
  "tr": {"name": "Turkish", "script": "Latn", "rtl": false},
  "uk": {"name": "Ukrainian", "script": "Cyrl", "rtl": false},
  "ur": {"name": "Urdu", "script": "Arab", "rtl": true},
  "vi": {"name": "Vietnamese", "script": "Latn", "rtl": false},
  "zh": {"name": "Chinese", "script": "Hani", "rtl": false}
}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_languages.py
import unittest

from newscentral import sources


class TestLanguages(unittest.TestCase):
    def test_reference_shape(self):
        langs = sources.load_languages()
        self.assertIn("es", langs)
        for code, meta in langs.items():
            self.assertEqual(code, code.lower())
            self.assertEqual(len(code), 2)
            self.assertEqual(set(meta), {"name", "script", "rtl"})
            self.assertEqual(len(meta["script"]), 4)
            self.assertIsInstance(meta["rtl"], bool)

    def test_rtl_languages(self):
        langs = sources.load_languages()
        self.assertEqual(sorted(c for c, m in langs.items() if m["rtl"]), ["ar", "fa", "he", "ur"])

    def test_latin_script(self):
        self.assertTrue(sources.latin_script("es"))
        self.assertTrue(sources.latin_script("vi"))
        self.assertFalse(sources.latin_script("ru"))
        self.assertFalse(sources.latin_script("zh"))
        self.assertTrue(sources.latin_script(None))      # unknown: keep the old lenient behaviour
        self.assertTrue(sources.latin_script("xx"))

    def test_every_real_source_validates(self):
        errs = sources.validate_sources(sources.load_sources(), sources.load_countries(),
                                        sources.load_languages())
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
```

Add to the test class in `tests/test_validate_sources.py` (it already imports `sources`):

```python
    def test_lang_must_be_in_reference(self):
        langs = {"es": {"name": "Spanish", "script": "Latn", "rtl": False}}
        countries = {"ES": {"name": "Spain", "region": "Spain"}}
        ok = {"id": "a", "country_iso": "ES", "lang": "es"}
        self.assertEqual(sources.validate_sources([ok], countries, langs), [])
        errs = sources.validate_sources([dict(ok, lang="xx")], countries, langs)
        self.assertTrue(any("lang" in e for e in errs))
        errs = sources.validate_sources([{"id": "b", "country_iso": "ES"}], countries, langs)
        self.assertTrue(any("lang" in e for e in errs))

    def test_kind_vocabulary(self):
        langs = {"es": {"name": "Spanish", "script": "Latn", "rtl": False}}
        countries = {"ES": {"name": "Spain", "region": "Spain"}}
        base = {"id": "a", "country_iso": "ES", "lang": "es"}
        for kind in ("news", "analysis", "official"):
            self.assertEqual(sources.validate_sources([dict(base, kind=kind)], countries, langs), [])
        self.assertEqual(sources.validate_sources([base], countries, langs), [])   # kind is optional
        errs = sources.validate_sources([dict(base, kind="blog")], countries, langs)
        self.assertTrue(any("kind" in e for e in errs))
```

- [ ] **Step 3: Run them**

Run: `py -m unittest tests.test_languages tests.test_validate_sources`
Expected: errors with `has no attribute 'load_languages'`.

- [ ] **Step 4: Implement**

`newscentral/config.py`: add `LANGUAGES_FILE = os.path.join(BASE_DIR, "geo", "languages.json")`.

`newscentral/sources.py`: import `LANGUAGES_FILE`, then add:

```python
_LANGUAGES = None

def load_languages():
    global _LANGUAGES
    if _LANGUAGES is None:
        with open(LANGUAGES_FILE, encoding="utf-8") as f:
            _LANGUAGES = json.load(f)
    return _LANGUAGES


def latin_script(lang):
    """True when the language is written in Latin script, or is unknown."""
    meta = load_languages().get(lang or "")
    return meta is None or meta["script"] == "Latn"


KIND_VOCAB = ("news", "analysis", "official")
```

Change the signature to `def validate_sources(sources, countries, languages=None):` and add, inside the loop before `return errors`:

```python
        if languages is not None:
            lang = s.get("lang")
            if lang not in languages:
                errors.append("%s: lang %r not in language reference" % (sid, lang))
        kind = s.get("kind")
        if kind is not None and kind not in KIND_VOCAB:
            errors.append("%s: kind %r not in %r" % (sid, kind, KIND_VOCAB))
```

`newscentral/cli.py`: the `--check-sources` branch passes the reference:

```python
        errs = validate_sources(load_sources(), load_countries(), load_languages())
```

- [ ] **Step 5: Run the tests**

Run: `py -m unittest discover -s tests`
Expected: `OK`, no failures or errors.

Run: `py server.py --check-sources`
Expected: `0 problem(s)`.

- [ ] **Step 6: Commit**

```bash
git add geo/languages.json newscentral tests
git commit -m "Add language reference and validate source lang and kind"
```

---

### Task 5: Charset-aware decoding

**Files:**
- Modify: `newscentral/text.py`, `newscentral/http_client.py`, `newscentral/feeds.py`
- Create: `tests/test_charset.py`, `tests/test_feeds.py`, `tests/fixtures/rss.xml`, `tests/fixtures/atom.xml`, `tests/fixtures/broken.xml`

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/rss.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Fixture</title>
<item><title>First &amp; foremost</title><link>https://example.com/one?utm_source=rss&amp;id=1</link>
<description>&lt;p&gt;Summary &lt;b&gt;one&lt;/b&gt;&lt;/p&gt;</description>
<pubDate>Tue, 10 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>Second</title><link>https://example.com/two</link></item>
<item><title>No link</title></item>
<item><title>Bad scheme</title><link>javascript:alert(1)</link></item>
</channel></rss>
```

`tests/fixtures/atom.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Fixture</title>
<entry><title>Atom entry</title><link rel="alternate" href="/path/a"/>
<summary>Atom summary</summary><updated>2026-11-10T09:30:00+01:00</updated></entry>
</feed>
```

`tests/fixtures/broken.xml` (unclosed tag and a stray ampersand, so only the regex fallback can read it):

```xml
<rss><channel>
<item><title>Broken & still readable</title><link>https://example.com/broken</link><description>Text</item>
</channel></rss>
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_charset.py
import unittest

from newscentral import text
from newscentral.feeds import parse_feed

RUSSIAN = "Новости дня"
CHINESE = "中国经济新闻"
JAPANESE = "日本のニュース"
ARABIC = "الأخبار"


def feed(title, encoding, declare):
    head = '<?xml version="1.0" encoding="%s"?>' % encoding if declare else ""
    xml = "%s<rss><channel><item><title>%s</title><link>https://example.com/x</link></item></channel></rss>" % (
        head, title)
    return xml.encode(encoding)


class TestCharsetFromHeader(unittest.TestCase):
    def test_parses_content_type(self):
        f = text.charset_from_content_type
        self.assertEqual(f("text/xml; charset=windows-1251"), "windows-1251")
        self.assertEqual(f('application/rss+xml; charset="GBK"'), "GBK")
        self.assertIsNone(f("text/xml"))
        self.assertIsNone(f(None))


class TestDecodeBytes(unittest.TestCase):
    def test_utf8_wins_over_a_wrong_latin1_header(self):
        data = "España".encode("utf-8")
        self.assertEqual(text.decode_bytes(data, "ISO-8859-1"), "España")

    def test_header_charset_used_when_not_utf8(self):
        self.assertEqual(text.decode_bytes(RUSSIAN.encode("windows-1251"), "windows-1251"), RUSSIAN)

    def test_bom(self):
        self.assertEqual(text.decode_bytes(RUSSIAN.encode("utf-16")), RUSSIAN)
        self.assertEqual(text.decode_bytes(b"\xef\xbb\xbfabc"), "abc")

    def test_latin_fallback_only_when_allowed(self):
        data = "España".encode("cp1252")
        self.assertEqual(text.decode_bytes(data), "España")
        with self.assertRaises(text.UndecodableFeed):
            text.decode_bytes(RUSSIAN.encode("windows-1251"), latin_fallback=False)

    def test_unknown_declared_charset_is_skipped(self):
        self.assertEqual(text.decode_bytes(b"abc", "no-such-charset"), "abc")


class TestParseFeedCharsets(unittest.TestCase):
    def check(self, title, encoding, declare):
        items = parse_feed(feed(title, encoding, declare), "https://example.com/",
                           http_charset=None if declare else encoding, latin_fallback=False)
        self.assertEqual([i["title"] for i in items], [title])

    def test_declared_in_xml(self):
        self.check(RUSSIAN, "windows-1251", True)
        self.check(CHINESE, "gbk", True)
        self.check(JAPANESE, "shift_jis", True)
        self.check(ARABIC, "windows-1256", True)

    def test_declared_only_in_http_header(self):
        self.check(RUSSIAN, "windows-1251", False)
        self.check(CHINESE, "gbk", False)
        self.check(JAPANESE, "shift_jis", False)
        self.check(ARABIC, "windows-1256", False)

    def test_non_latin_without_charset_fails_instead_of_storing_garbage(self):
        with self.assertRaises(text.UndecodableFeed):
            parse_feed(feed(RUSSIAN, "windows-1251", False), "https://example.com/",
                       http_charset=None, latin_fallback=False)

    def test_latin_source_keeps_the_lenient_fallback(self):
        items = parse_feed(feed("España hoy", "cp1252", False), "https://example.com/")
        self.assertEqual(items[0]["title"], "España hoy")


if __name__ == "__main__":
    unittest.main()
```

```python
# tests/test_feeds.py
import os
import unittest

from newscentral.feeds import parse_feed

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


class TestParseFeed(unittest.TestCase):
    def test_rss(self):
        items = parse_feed(load("rss.xml"), "https://example.com/feed")
        self.assertEqual([i["title"] for i in items], ["First & foremost", "Second"])
        self.assertEqual(items[0]["link"], "https://example.com/one?id=1")   # tracking parameter removed
        self.assertEqual(items[0]["summary"], "Summary one")
        self.assertEqual(items[0]["published"], 1794297600.0)                # 2026-11-10 08:00 UTC
        self.assertIsNone(items[1]["published"])

    def test_atom_resolves_relative_links_and_offsets(self):
        items = parse_feed(load("atom.xml"), "https://example.com/feed")
        self.assertEqual(items[0]["title"], "Atom entry")
        self.assertEqual(items[0]["link"], "https://example.com/path/a")
        self.assertEqual(items[0]["published"], 1794299400.0)                # 09:30 +01:00 = 08:30 UTC

    def test_broken_xml_falls_back_to_regex(self):
        items = parse_feed(load("broken.xml"), "https://example.com/feed")
        self.assertEqual(items[0]["title"], "Broken & still readable")
        self.assertEqual(items[0]["link"], "https://example.com/broken")

    def test_html_page_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            parse_feed(b"<!DOCTYPE html><html><body>Checking your browser</body></html>", "https://example.com/")
        self.assertIn("web page", str(cm.exception))

    def test_garbage_is_unreadable(self):
        with self.assertRaises(ValueError):
            parse_feed(b"not a feed at all", "https://example.com/")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run them**

Run: `py -m unittest tests.test_charset tests.test_feeds`
Expected: `test_feeds` passes (it covers existing behaviour); `test_charset` errors with `has no attribute 'charset_from_content_type'`. If a `test_feeds` assertion on an epoch value fails, compute the expected value with `py -c "from datetime import datetime, timezone; print(datetime(2026,11,10,8,0,tzinfo=timezone.utc).timestamp())"` and correct the test constant, not the parser.

- [ ] **Step 4: Implement decoding in `newscentral/text.py`**

Replace `decode_bytes` with:

```python
import codecs

XML_ENCODING_RE = re.compile(rb'encoding=["\']([A-Za-z0-9_.\-]+)["\']')
CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9_.\-]+)", re.I)
LATIN_FALLBACKS = ("cp1252", "latin-1")
_BOMS = ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
         (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))


class UndecodableFeed(ValueError):
    pass


def charset_from_content_type(value):
    m = CHARSET_RE.search(value or "")
    return m.group(1) if m else None


def decode_bytes(data, http_charset=None, latin_fallback=True):
    """Order: byte-order mark, XML declaration, UTF-8, HTTP charset, then (Latin-script sources only)
    cp1252 and latin-1. UTF-8 goes before the HTTP charset because servers often announce
    ISO-8859-1 for feeds that are really UTF-8, and latin-1 decodes anything without error."""
    candidates = []
    for bom, enc in _BOMS:
        if data.startswith(bom):
            candidates.append(enc)
            break
    m = XML_ENCODING_RE.search(data[:300])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    if http_charset:
        candidates.append(http_charset)
    if latin_fallback:
        candidates.extend(LATIN_FALLBACKS)
    for enc in candidates:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    raise UndecodableFeed("undecodable feed (no usable charset)")
```

- [ ] **Step 5: Pass the charset through `newscentral/http_client.py`**

`http_get` returns a fifth value. Inside the `with` block add `charset = charset_from_content_type(resp.headers.get("Content-Type"))` and end the function with:

```python
    return data, final_url, new_etag, new_lastmod, charset
```

`curl_get` asks curl for the content type:

```python
def curl_get(url, ua, accept=None):
    """Second opinion for sites that refuse Python's HTTP client."""
    if not CURL:
        raise RuntimeError("curl not available")
    cmd = [CURL, "-sS", "-L", "--compressed", "-m", str(FETCH_TIMEOUT), "-A", ua,
           "-H", "Accept: " + (accept or FEED_ACCEPT), "-H", "Accept-Language: en, es;q=0.9",
           "-w", "\n%{http_code}\t%{content_type}\t%{url_effective}", url]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=FETCH_TIMEOUT + 5)
    if proc.returncode != 0:
        raise RuntimeError("curl: " + proc.stderr.decode("utf-8", "ignore").strip()[:100])
    body, _, tail = proc.stdout.rpartition(b"\n")
    parts = tail.decode("utf-8", "ignore").split("\t", 2)
    code = parts[0]
    ctype = parts[1] if len(parts) > 1 else ""
    final_url = parts[2] if len(parts) > 2 else ""
    if code != "200":
        raise urllib.error.HTTPError(url, int(code or 0), "curl", None, None)
    return body, final_url or url, None, None, charset_from_content_type(ctype)
```

`get_with_fallback` hands the charset to `check` and returns it:

```python
def get_with_fallback(url, accept=None, etag=None, lastmod=None, check=None):
    """GET url; if the site blocks us (403 etc. or a bot-check page), retry with other clients.
    check(data, final_url, charset) parses the body and raises ValueError("...web page...") on a bot page.
    Returns (data or check result, final_url, etag, lastmod, charset)."""
    def finish(result):
        data, final_url, et, lm, charset = result
        return (check(data, final_url, charset) if check else data), final_url, et, lm, charset

    try:
        return finish(http_get(url, etag, lastmod, accept))
    except NotModified:
        raise
    except urllib.error.HTTPError as e:
        if e.code not in BLOCK_CODES:
            raise
        first = e
    except UndecodableFeed:
        raise
    except ValueError as e:
        if "web page" not in str(e):
            raise
        first = e
    attempts = (lambda: http_get(url, accept=accept, ua=ALT_USER_AGENT),
                lambda: curl_get(url, USER_AGENT, accept),
                lambda: curl_get(url, ALT_USER_AGENT, accept))
    for attempt in attempts:
        try:
            return finish(attempt())
        except Exception:
            continue
    raise first
```

In `short_error`, add as the first lines so the message reads cleanly in the Sources view:

```python
    if isinstance(exc, UndecodableFeed):
        return str(exc)
```

Import `charset_from_content_type` and `UndecodableFeed` from `.text`.

- [ ] **Step 6: Use it in `newscentral/feeds.py`**

```python
def repair_xml(data, http_charset=None, latin_fallback=True):
    text = decode_bytes(data, http_charset, latin_fallback).lstrip("﻿ \t\r\n")
```

(The rest of `repair_xml` is unchanged.)

```python
def parse_feed(data, base, http_charset=None, latin_fallback=True):
    head = data[:1024].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        raise ValueError("got a web page, not a feed")
    root = None
    for attempt in (lambda: data, lambda: repair_xml(data, http_charset, latin_fallback)):
        try:
            root = ET.fromstring(attempt())
            break
        except ET.ParseError:
            continue
    if root is None:
        items = regex_items(decode_bytes(data, http_charset, latin_fallback), base)
        if not items:
            raise ValueError("unreadable feed")
        return items
    strip_namespaces(root)
    out = []
    for el in list(root.iter("item")) + list(root.iter("entry")):
        item = parse_entry(el, base)
        if item:
            out.append(item)
    return out
```

`discover_feeds` unpacks five values and decodes with the header charset:

```python
    data, final_url, _, _, charset = get_with_fallback(site, accept="text/html,application/xhtml+xml,*/*;q=0.8")
    text = decode_bytes(data[:800000], charset)
```

In `fetch_source`, build the check once at the top and use it in both calls, unpacking five values:

```python
    latin = latin_script(src.get("lang"))

    def check(data, base, charset):
        return parse_feed(data, base, charset, latin)
```

```python
            items, _, etag, lastmod, _ = get_with_fallback(
                url, etag=state.get("etag") if same else None,
                lastmod=state.get("lastmod") if same else None, check=check)
```

```python
                    items, _, etag, lastmod, _ = get_with_fallback(url, check=check)
```

Import `latin_script` from `.sources`.

- [ ] **Step 7: Run the tests, then the live check**

Run: `py -m unittest discover -s tests`
Expected: `OK`, no failures or errors.

Run: `py server.py --check`
Expected: the last line reports about the same numbers as before this task (on 2026-11-11: 120 ok, 12 found by auto-discovery, 11 failing). A source that newly fails with `undecodable feed` is a real finding: report it, do not hide it.

- [ ] **Step 8: Commit**

```bash
git add newscentral tests
git commit -m "Decode feeds with BOM, XML, UTF-8 and HTTP charset; refuse undecodable non-Latin feeds"
```

---

Continue with Task 6 in `2026-11-11-collection-foundation-part2.md`.
