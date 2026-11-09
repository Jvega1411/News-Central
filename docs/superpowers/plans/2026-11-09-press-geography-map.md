# Press Geography Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dark choropleth world map plus a Region -> Country -> source spine that browse and filter the archive by the country a source is from, backed by a clean ISO-based source data model and a numbered schema-migration mechanism.

**Architecture:** `server.py` stays a single file; it gains a migration runner, an ISO country reference loader, a pure geo-aggregation function, and a `/api/geo` read endpoint. The frontend inline `<script>` is split into local ES modules (no build step). The map is an inline SVG whose country `<path>` ids are ISO alpha-2 codes, colored by JavaScript from the `/api/geo` payload.

**Tech Stack:** Python 3.8+ standard library only (`http.server`, `sqlite3`, `json`, `unittest`). Vanilla ES modules, no bundler, no dependency. SVG for the map.

**Design source:** `docs/superpowers/specs/2026-11-09-press-geography-map-design.md`. Read it before starting.

**Testing convention:** stdlib `unittest`. Tests live in `tests/`. Run a single test with `py -m unittest tests.test_name -v` (Windows) or `python -m unittest ...`. There is no pytest; do not add one.

---

## File structure

**Created:**
- `geo/countries.json` — ISO alpha-2 -> `{name, region}` reference (committed, not runtime data).
- `geo/press-freedom-2026.json` — ISO alpha-2 -> RSF 2026 score (committed). Gated on Task 11.
- `static/world.svg` — CC0/PD world map, country `<path>` ids = ISO alpha-2. Gated on Task 8.
- `static/js/api.js` — HTTP API fetch wrappers.
- `static/js/state.js` — shared selection + time-window state, tiny pub/sub.
- `static/js/map.js` — SVG choropleth: color, hover, click, legend, layer toggle, non-country tray.
- `static/js/spine.js` — Region -> Country -> source tree.
- `static/js/reader.js` — existing article list / saved / sources views (moved out of index.html).
- `static/js/main.js` — entry module, wires the others.
- `tests/__init__.py`, and one test module per backend task below.

**Modified:**
- `server.py` — migration runner, `load_countries`, `aggregate_geo`, `/api/geo`, `source_payload` fields, `saved.country_iso` migration, `validate_sources` + `--check-sources`.
- `sources.json` — every source rewritten to `country_iso` / `scope` / `exile` / `covers`.
- `static/index.html` — inline script replaced by `<script type="module" src="js/main.js">`; new map/spine DOM; CSS tokens.
- `CLAUDE.md` — code-structure preface, UI constraint, aesthetic rules.

**Build order dependency:** Tasks 1-7 and 13 have no external gate and come first. Task 8 (SVG asset) gates Task 9. Task 11 (RSF data) gates Task 12. Tasks 1 and 3 touch disjoint files and may run in parallel; everything frontend chains through `index.html`.

---

## Task 1: Schema migration mechanism

**Files:**
- Modify: `server.py` (the `Store` class area, ~504-524)
- Test: `tests/test_migrations.py`

Uses SQLite's `PRAGMA user_version`. Baseline schema (the current `CREATE IF NOT EXISTS` block) is version 0. Each migration is a function registered with a version number; on startup, every migration whose version exceeds the DB's `user_version` runs in order, then the version is bumped.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrations.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m unittest tests.test_migrations -v`
Expected: FAIL (`AttributeError: module 'server' has no attribute 'SCHEMA_VERSION'`).

- [ ] **Step 3: Add the migration runner and one migration**

In `server.py`, just above `class Store`, add:

```python
# ---------------------------------------------------------------- migrations

MIGRATIONS = []  # list of (version, fn(db)); applied in ascending order


def migration(version):
    def register(fn):
        MIGRATIONS.append((version, fn))
        return fn
    return register


SCHEMA_VERSION = max((v for v, _ in MIGRATIONS), default=0)


def run_migrations(db, lock):
    with lock:
        current = db.execute("PRAGMA user_version").fetchone()[0]
        for version, fn in sorted(MIGRATIONS):
            if version > current:
                fn(db)
                db.execute("PRAGMA user_version = %d" % version)  # int only, not user input
                db.commit()


@migration(1)
def _m1_saved_country_iso(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(saved)")]
    if "country_iso" not in cols:
        db.execute("ALTER TABLE saved ADD COLUMN country_iso TEXT DEFAULT ''")
```

Then set `SCHEMA_VERSION` after the migrations are defined. Because `SCHEMA_VERSION` is computed at import time, move its assignment to the end of the migrations block (after `_m1...`):

```python
SCHEMA_VERSION = max((v for v, _ in MIGRATIONS), default=0)
```

(Delete the earlier placeholder assignment so there is exactly one, after the last migration.)

- [ ] **Step 4: Call the runner from `Store.__init__`**

In `Store.__init__`, after the existing `self.db.commit()` that creates the baseline tables, add:

```python
        run_migrations(self.db, self.lock)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m unittest tests.test_migrations -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_migrations.py tests/__init__.py
git commit -m "Add numbered schema-migration mechanism"
```

(Create an empty `tests/__init__.py` if it does not exist.)

---

## Task 2: ISO country reference

**Files:**
- Create: `geo/countries.json`
- Modify: `server.py` (add `load_countries`, call at startup)
- Test: `tests/test_countries.py`

`geo/countries.json` maps ISO alpha-2 -> `{name, region}`. `region` values MUST exactly match the region vocabulary in `sources.json` (confirm the exact strings with: `py -c "import json;print(sorted({s['region'] for s in json.load(open('sources.json',encoding='utf-8'))['sources']}))"`). Include every country that appears in `sources.json`; more is fine.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_countries.py
import unittest, server

class TestCountries(unittest.TestCase):
    def test_loads_and_has_spain(self):
        c = server.load_countries()
        self.assertIn("ES", c)
        self.assertEqual(c["ES"]["name"], "Spain")
        self.assertIn("region", c["ES"])

    def test_keys_are_upper_alpha2(self):
        c = server.load_countries()
        for k in c:
            self.assertEqual(k, k.upper())
            self.assertEqual(len(k), 2)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m unittest tests.test_countries -v`
Expected: FAIL (no `load_countries`, no file).

- [ ] **Step 3: Create `geo/countries.json`**

Seed with at least every country used in `sources.json`. Example (extend to full coverage):

```json
{
  "ES": {"name": "Spain", "region": "Spain"},
  "FR": {"name": "France", "region": "Europe & EU"},
  "DE": {"name": "Germany", "region": "Europe & EU"},
  "GB": {"name": "United Kingdom", "region": "Europe & EU"},
  "US": {"name": "United States", "region": "North America"},
  "QA": {"name": "Qatar", "region": "Middle East & N. Africa"},
  "LV": {"name": "Latvia", "region": "Europe & EU"},
  "IR": {"name": "Iran", "region": "Middle East & N. Africa"},
  "RU": {"name": "Russia", "region": "Eurasia"}
}
```

- [ ] **Step 4: Add the loader to `server.py`**

Near `SOURCES_FILE` constants add `COUNTRIES_FILE = os.path.join(BASE_DIR, "geo", "countries.json")`. Then:

```python
_COUNTRIES = None

def load_countries():
    global _COUNTRIES
    if _COUNTRIES is None:
        with open(COUNTRIES_FILE, encoding="utf-8") as f:
            _COUNTRIES = json.load(f)
    return _COUNTRIES
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m unittest tests.test_countries -v`
Expected: PASS. If `test_loads_and_has_spain` fails on region, align the region string with `sources.json`.

- [ ] **Step 6: Commit**

```bash
git add geo/countries.json server.py tests/test_countries.py
git commit -m "Add ISO country reference and loader"
```

---

## Task 3: Source data-model validator + CLI check

**Files:**
- Modify: `server.py` (add `validate_sources`, `--check-sources`)
- Test: `tests/test_validate_sources.py`

Pure function that returns a list of human-readable errors. Enforces the closed vocabulary.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validate_sources.py
import unittest, server

COUNTRIES = {"ES": {"name": "Spain", "region": "Spain"},
             "LV": {"name": "Latvia", "region": "Europe & EU"},
             "RU": {"name": "Russia", "region": "Eurasia"}}

class TestValidate(unittest.TestCase):
    def test_clean_source_passes(self):
        src = [{"id": "a", "country_iso": "ES", "region": "Spain"}]
        self.assertEqual(server.validate_sources(src, COUNTRIES), [])

    def test_free_text_country_rejected(self):
        src = [{"id": "a", "country": "Spain", "region": "Spain"}]
        errs = server.validate_sources(src, COUNTRIES)
        self.assertTrue(any("country" in e for e in errs))

    def test_unknown_iso_rejected(self):
        src = [{"id": "a", "country_iso": "ZZ", "region": "x"}]
        self.assertTrue(server.validate_sources(src, COUNTRIES))

    def test_scope_vocab_enforced(self):
        src = [{"id": "a", "country_iso": None, "scope": "Misc", "region": "x"}]
        self.assertTrue(server.validate_sources(src, COUNTRIES))

    def test_valid_scope_passes(self):
        src = [{"id": "a", "country_iso": None, "scope": "Global", "region": "Global"}]
        self.assertEqual(server.validate_sources(src, COUNTRIES), [])

    def test_exile_requires_covers(self):
        src = [{"id": "a", "country_iso": "LV", "exile": True, "region": "Eurasia"}]
        self.assertTrue(server.validate_sources(src, COUNTRIES))

    def test_exile_with_valid_covers_passes(self):
        src = [{"id": "a", "country_iso": "LV", "exile": True, "covers": "RU", "region": "Eurasia"}]
        self.assertEqual(server.validate_sources(src, COUNTRIES), [])

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m unittest tests.test_validate_sources -v`
Expected: FAIL (no `validate_sources`).

- [ ] **Step 3: Implement `validate_sources`**

```python
SCOPE_VOCAB = ("Global", "Intergovernmental", "Transnational-regional")


def validate_sources(sources, countries):
    errors = []
    for s in sources:
        sid = s.get("id", "?")
        if "country" in s:
            errors.append("%s: legacy free-text 'country' field present" % sid)
        iso = s.get("country_iso")
        scope = s.get("scope")
        if iso:
            if iso not in countries:
                errors.append("%s: country_iso %r not in country reference" % (sid, iso))
        elif scope:
            if scope not in SCOPE_VOCAB:
                errors.append("%s: scope %r not in %r" % (sid, scope, SCOPE_VOCAB))
        else:
            errors.append("%s: needs either country_iso or scope" % sid)
        if s.get("exile"):
            cov = s.get("covers")
            if not cov:
                errors.append("%s: exile source missing 'covers'" % sid)
            elif cov not in countries:
                errors.append("%s: covers %r not in country reference" % (sid, cov))
    return errors
```

- [ ] **Step 4: Wire the `--check-sources` flag**

In `main()` argument handling, add a branch so `py server.py --check-sources` loads sources + countries, prints each error, and exits non-zero if any:

```python
    if "--check-sources" in sys.argv:
        errs = validate_sources(load_sources(), load_countries())
        for e in errs:
            print("FAIL", e)
        print("%d problem(s)" % len(errs))
        sys.exit(1 if errs else 0)
```

(Place it next to the existing `--check` handling.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m unittest tests.test_validate_sources -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_validate_sources.py
git commit -m "Add source data-model validator and --check-sources"
```

---

## Task 4: Migrate `sources.json` to the new model

**Files:**
- Modify: `sources.json` (all sources)

Mechanical rewrite guided by Task 3's validator. For each source: replace `country` with `country_iso` (uppercase ISO alpha-2) when it is a real country; otherwise set `country_iso: null` and a `scope` from the closed vocabulary; add `exile: true` + `covers` for the exile outlets.

Mapping rules for the current free-text values:
- Real countries -> their ISO: `Spain`->`ES`, `France`->`FR`, `Germany`->`DE`, `UK`->`GB`, `US`->`US`, `Qatar`->`QA`, `China`->`CN`, `Japan`->`JP`, `India`->`IN`, etc.
- `Global` -> `country_iso: null, scope: "Global"`.
- `UN` -> `country_iso: null, scope: "Intergovernmental"`.
- `Pan-African` -> `country_iso: null, scope: "Transnational-regional"`.
- Sub-region strings (`Balkans`, `Caucasus & Central Asia`, `Europe`, `Asia-Pacific`, `Israel/Palestine`) -> decide per source: if it is really a single-country outlet, use that country's ISO; if it genuinely covers a bloc, use `scope: "Transnational-regional"`. Do not invent a country.
- `Iran (exile)` -> `country_iso` = factual host (research each; Iran International is `GB`), `exile: true`, `covers: "IR"`.
- `Russia (exile)` (Meduza etc.) -> `country_iso: "LV"` (verify per outlet), `exile: true`, `covers: "RU"`.

- [ ] **Step 1: Rewrite every source**

Edit `sources.json` so no `country` key remains and each source has `country_iso` (or `scope`). Keep `region`, `lang`, `kind`, etc. unchanged.

- [ ] **Step 2: Extend `geo/countries.json`**

Add any ISO codes used in Step 1 that are missing from the reference, each with `name` and a `region` that matches the source's region.

- [ ] **Step 3: Run the validator**

Run: `py server.py --check-sources`
Expected: `0 problem(s)` and exit 0. Fix every reported line until clean.

- [ ] **Step 4: Commit**

```bash
git add sources.json geo/countries.json
git commit -m "Migrate sources.json to ISO country_iso/scope/exile model"
```

---

## Task 5: `/api/geo` aggregation endpoint

**Files:**
- Modify: `server.py` (add `aggregate_geo`, route `/api/geo`, update `source_payload` + `save`)
- Test: `tests/test_geo.py`

Pure `aggregate_geo` so it is testable without a running server. Exile items count under `covers`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_geo.py
import time, unittest, server

COUNTRIES = {"ES": {"name": "Spain", "region": "Spain"},
             "LV": {"name": "Latvia", "region": "Europe & EU"},
             "RU": {"name": "Russia", "region": "Eurasia"}}

def item(sid, age_s=10):
    now = time.time()
    return {"source_id": sid, "published": now - age_s, "first_seen": now - age_s}

class TestGeo(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {"id": "elpais", "country_iso": "ES", "region": "Spain"},
            {"id": "meduza", "country_iso": "LV", "exile": True, "covers": "RU", "region": "Eurasia"},
            {"id": "ps", "country_iso": None, "scope": "Global", "region": "Global"},
        ]

    def test_counts_country(self):
        out = server.aggregate_geo(self.sources, [item("elpais"), item("elpais")], COUNTRIES, 0)
        self.assertEqual(out["countries"]["ES"]["items"], 2)
        self.assertEqual(out["countries"]["ES"]["sources"], 1)
        self.assertEqual(out["countries"]["ES"]["name"], "Spain")

    def test_exile_counts_under_covers(self):
        out = server.aggregate_geo(self.sources, [item("meduza")], COUNTRIES, 0)
        self.assertEqual(out["countries"]["RU"]["items"], 1)
        self.assertNotIn("LV", out["countries"])

    def test_scope_bucket(self):
        out = server.aggregate_geo(self.sources, [item("ps")], COUNTRIES, 0)
        self.assertEqual(out["buckets"]["Global"]["items"], 1)

    def test_since_filters_old_items(self):
        out = server.aggregate_geo(self.sources, [item("elpais", age_s=10)], COUNTRIES, time.time() - 1)
        self.assertNotIn("ES", out["countries"])

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m unittest tests.test_geo -v`
Expected: FAIL (no `aggregate_geo`).

- [ ] **Step 3: Implement `aggregate_geo`**

```python
def aggregate_geo(sources, items, countries, since):
    min_time = time.time() - KEEP_HOURS * 3600
    floor = max(since, min_time)
    by_id = {s["id"]: s for s in sources}
    countries_out, buckets_out = {}, {}
    seen_c, seen_b = {}, {}  # iso/scope -> set of source ids with >=1 counted item

    def bump(table, seen, key, sid, meta=None):
        if key not in table:
            table[key] = {"items": 0, "sources": 0}
            if meta:
                table[key].update(meta)
            seen[key] = set()
        table[key]["items"] += 1
        if sid not in seen[key]:
            seen[key].add(sid)
            table[key]["sources"] += 1

    for r in items:
        s = by_id.get(r["source_id"])
        if not s:
            continue
        t = sort_time(r.get("published"), r["first_seen"])
        if t < floor:
            continue
        iso = s.get("covers") if s.get("exile") else s.get("country_iso")
        if iso:
            meta = countries.get(iso, {})
            bump(countries_out, seen_c, iso, s["id"],
                 {"name": meta.get("name", iso), "region": meta.get("region", "")})
        elif s.get("scope"):
            bump(buckets_out, seen_b, s["scope"], s["id"])
    return {"countries": countries_out, "buckets": buckets_out}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -m unittest tests.test_geo -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Add the route and a payload helper**

Add near `items_payload`:

```python
def geo_payload(since):
    cutoff = time.time() - KEEP_HOURS * 3600 - 3600
    rows = STORE.items(max(since, cutoff))
    out = aggregate_geo(FETCHER.sources, rows, load_countries(), since)
    out["updated"] = FETCHER.last_cycle
    return out
```

In `Handler.do_GET`, add a branch alongside `/api/items`:

```python
            elif path == "/api/geo":
                try:
                    since = float((q.get("since") or ["0"])[0] or 0)
                except ValueError:
                    since = 0.0
                self._json(geo_payload(since))
```

- [ ] **Step 6: Update `source_payload` to expose the new fields**

In `source_payload`, replace the `"country": s.get("country", "")` line with:

```python
            "country_iso": s.get("country_iso"), "scope": s.get("scope"),
            "exile": bool(s.get("exile")), "covers": s.get("covers"),
```

Keep the rest of the dict unchanged (region, lang, kind, etc.).

- [ ] **Step 7: Keep `saved` writing ISO**

In `Store.save`, add `country_iso` to the insert so saved items retain it. Change the insert to include the column and value `src.get("country_iso") or ""`. (The column exists from Migration 1.) Leave the legacy `country` column write as-is for now; it is harmless and the export still reads it.

- [ ] **Step 8: Manual endpoint smoke test**

Run: `py server.py` then in another shell `curl "http://127.0.0.1:8787/api/geo"`.
Expected: JSON with `countries`, `buckets`, `updated`. (Counts may be low until a fetch cycle runs.)

- [ ] **Step 9: Commit**

```bash
git add server.py tests/test_geo.py
git commit -m "Add /api/geo aggregation endpoint and expose ISO fields"
```

---

## Task 6: Split the frontend into ES modules (no behavior change)

**Files:**
- Modify: `static/index.html`
- Create: `static/js/api.js`, `static/js/state.js`, `static/js/reader.js`, `static/js/main.js`

Pure refactor. Move the existing inline `<script>` out of `index.html` into modules by responsibility, preserving behavior exactly. No new features in this task.

- [ ] **Step 1: Create `static/js/state.js`**

A tiny shared store for cross-module state (current region/country selection, time window, search). Minimal pub/sub:

```javascript
// static/js/state.js
const listeners = new Set();
export const state = { region: null, country: null, window: 24, search: "", view: "news" };
export function setState(patch) {
  Object.assign(state, patch);
  listeners.forEach(fn => fn(state));
}
export function onState(fn) { listeners.add(fn); return () => listeners.delete(fn); }
```

- [ ] **Step 2: Create `static/js/api.js`**

Move every `fetch(...)` call from the inline script here, as named async functions. Include the POST header the backend requires:

```javascript
// static/js/api.js
const POST_HEADERS = { "Content-Type": "application/json", "X-NewsCentral": "1" };
async function get(path) { const r = await fetch(path); return r.json(); }
async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: POST_HEADERS, body: JSON.stringify(body || {}) });
  return r.json();
}
export const api = {
  items: (since) => get("/api/items?since=" + (since || 0)),
  geo: (since) => get("/api/geo?since=" + (since || 0)),
  sources: () => get("/api/sources"),
  saved: () => get("/api/saved"),
  refresh: () => post("/api/refresh"),
  save: (id) => post("/api/save", { id }),
  unsave: (id) => post("/api/unsave", { id }),
  updateSaved: (id, patch) => post("/api/saved/update", Object.assign({ id }, patch)),
};
```

- [ ] **Step 3: Create `static/js/reader.js`**

Move the existing rendering logic (item list, saved view, sources table, filters, search, star/save, "Copy for Claude") into this module. Export an `initReader(root)` (or similar) that wires the existing DOM. Preserve all current behavior and markup. Import `api` from `./api.js` and `state`/`setState` from `./state.js` where the code currently reads globals.

- [ ] **Step 4: Create `static/js/main.js`**

```javascript
// static/js/main.js
import { initReader } from "./reader.js";
initReader(document);
// map.js and spine.js are added in later tasks:
// import { initMap } from "./map.js"; import { initSpine } from "./spine.js";
```

- [ ] **Step 5: Replace the inline script in `index.html`**

Delete the inline `<script>...</script>` block and replace with:

```html
<script type="module" src="js/main.js"></script>
```

Confirm `server.py` serves `static/js/*.js`. The current `do_GET` only serves `/` and `/api/*`. Add static-file serving for `/js/...` and later `/world.svg`:

```python
            elif path.startswith("/js/") and path.endswith(".js"):
                self._serve_static(path, "text/javascript; charset=utf-8")
```

and a helper on `Handler`:

```python
    def _serve_static(self, path, ctype):
        safe = os.path.normpath(path.lstrip("/")).replace("\\", "/")
        if safe.startswith("..") or ":" in safe:
            return self._json({"error": "not found"}, 404)
        full = os.path.join(BASE_DIR, "static", *safe.split("/"))
        if not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)
```

(Check how `index.html` is served today; reuse `INDEX_FILE`/`BASE_DIR` conventions. The existing `static/` layout means `/js/api.js` -> `static/js/api.js`.)

- [ ] **Step 6: Verify behavior is unchanged**

Run: `py server.py --open`. In the browser confirm: items load, region chips filter, time window works, search works, saving/unsaving works, saved view and "Copy for Claude" work, sources table renders. Check the devtools console for zero errors (module load failures show here).

- [ ] **Step 7: Commit**

```bash
git add static/index.html static/js/ server.py
git commit -m "Split frontend into ES modules (no behavior change)"
```

---

## Task 7: Region -> Country -> source spine

**Files:**
- Create: `static/js/spine.js`
- Modify: `static/index.html` (sidebar container), `static/js/main.js`

Collapsible tree built from `/api/geo` (region + country counts) and `/api/sources` (the source list per country). Clicking a level sets selection via `state.js`, which `reader.js` already observes to filter.

- [ ] **Step 1: Add the sidebar container to `index.html`**

Add an `<aside id="spine" class="spine"></aside>` in the layout (grid: spine left, main right on wide screens; stacked on narrow). Keep the existing reader markup.

- [ ] **Step 2: Implement `spine.js`**

```javascript
// static/js/spine.js
import { api } from "./api.js";
import { setState } from "./state.js";

export async function initSpine(el) {
  const [geo, src] = await Promise.all([api.geo(0), api.sources()]);
  const byCountry = {}; // iso -> [source names]
  (src.sources || []).forEach(s => {
    const iso = s.exile ? s.covers : s.country_iso;
    if (iso) { (byCountry[iso] = byCountry[iso] || []).push(s); }
  });
  const regions = {}; // region -> {items, countries: {iso: data}}
  Object.entries(geo.countries).forEach(([iso, d]) => {
    const r = d.region || "Other";
    const bucket = regions[r] || (regions[r] = { items: 0, countries: {} });
    bucket.items += d.items; bucket.countries[iso] = d;
  });
  el.innerHTML = "";
  Object.entries(regions).sort((a, b) => b[1].items - a[1].items).forEach(([region, rd]) => {
    const rEl = node("div", "spine-region");
    rEl.appendChild(row(region, rd.items, () => setState({ region, country: null })));
    Object.entries(rd.countries).sort((a, b) => b[1].items - a[1].items).forEach(([iso, cd]) => {
      const cEl = row(cd.name, cd.items, () => setState({ region, country: iso }), "spine-country");
      rEl.appendChild(cEl);
    });
    el.appendChild(rEl);
  });
  // Transnational buckets
  if (Object.keys(geo.buckets).length) {
    const tEl = node("div", "spine-region");
    tEl.appendChild(row("Transnational", 0, () => setState({ region: null, country: null })));
    Object.entries(geo.buckets).forEach(([name, bd]) =>
      tEl.appendChild(row(name, bd.items, () => setState({ scope: name, country: null }), "spine-country")));
    el.appendChild(tEl);
  }
}
function node(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
function row(label, n, onClick, cls) {
  const e = node("button", "spine-row " + (cls || ""));
  e.innerHTML = '<span class="l"></span><span class="n"></span>';
  e.querySelector(".l").textContent = label;
  e.querySelector(".n").textContent = n;
  e.addEventListener("click", onClick);
  return e;
}
```

- [ ] **Step 3: Wire into `main.js`**

```javascript
import { initSpine } from "./spine.js";
initSpine(document.getElementById("spine"));
```

Ensure `reader.js` filters its item list when `state.country` / `state.region` / `state.scope` change (subscribe via `onState`). Filtering is by the item's source country, which `reader.js` can resolve from the `/api/sources` payload it already loads.

- [ ] **Step 4: Verify**

Run: `py server.py --open`. Confirm: spine lists regions with counts, expands to countries, clicking a country filters the feed, clicking a region broadens it, Transnational group appears. Counts in the spine equal the `/api/geo` payload (`curl http://127.0.0.1:8787/api/geo`).

- [ ] **Step 5: Commit**

```bash
git add static/js/spine.js static/js/main.js static/index.html
git commit -m "Add Region/Country/source spine driven by /api/geo"
```

---

## Task 8: Obtain and vet the world SVG asset (gating)

**Files:**
- Create: `static/world.svg`
- Modify: `server.py` (serve `/world.svg`)

Acceptance criteria (all required before committing):
- License is CC0 or public domain, usable in a public repo. Record the source URL and license in the commit message.
- Each country is a `<path>` (or `<g>`) whose `id` is its ISO alpha-2 code (lowercase or uppercase; normalize in JS with `.toUpperCase()`). If the asset uses ISO alpha-3 or numeric ids, convert ids to alpha-2 as part of this task.
- Robinson or Natural Earth projection preferred over flat equirectangular.
- File is reasonably small (ideally < 400 KB); simplified geometry is fine.

- [ ] **Step 1: Acquire and normalize**

Obtain a conforming SVG (e.g. a CC0 world map), ensure path ids are ISO alpha-2. Save as `static/world.svg`.

- [ ] **Step 2: Serve it**

In `do_GET` add:

```python
            elif path == "/world.svg":
                self._serve_static("/world.svg", "image/svg+xml")
```

- [ ] **Step 3: Verify ids**

Run: `py -c "import re;print(sorted(set(re.findall(r'id=\"([A-Za-z]{2})\"', open('static/world.svg',encoding='utf-8').read())))[:20])"`
Expected: a list of two-letter codes including `ES`, `FR`, etc.

- [ ] **Step 4: Commit**

```bash
git add static/world.svg server.py
git commit -m "Add CC0 world SVG asset (source: <url>, license: <license>)"
```

---

## Task 9: SVG choropleth map (dark control-room)

**Files:**
- Create: `static/js/map.js`
- Modify: `static/index.html` (map container + CSS tokens), `static/js/main.js`

Loads `world.svg` inline, colors paths from `/api/geo`, hover tooltip, click-to-filter, count legend, non-country tray. Dark panel always, per the aesthetic rules.

- [ ] **Step 1: Add the map container and CSS tokens to `index.html`**

Add `<section id="map" class="mapwrap"></section>` as the main viewer. Add CSS:

```css
.mapwrap { background:#0b0e12; border-radius:14px; padding:12px; color:#e9edf2; }
.mapwrap svg { width:100%; height:auto; display:block; }
.mapwrap path { fill:#10151b; stroke:#222a33; stroke-width:.4; transition:fill .15s; }
.mapwrap path.hit { cursor:pointer; }
.mapwrap path.active { stroke:#35d0d6; stroke-width:1.2; }
.map-legend { display:flex; align-items:center; gap:6px; margin-top:8px; font:12px ui-monospace,Menlo,Consolas,monospace; color:#8a93a0; }
.map-legend i { width:18px; height:11px; display:inline-block; border-radius:2px; }
.map-tip { position:fixed; pointer-events:none; background:#12161c; color:#e9edf2; border:1px solid #222a33; border-radius:6px; padding:5px 8px; font-size:12px; z-index:20; }
.map-tray { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
.map-tray .chip { background:#12161c; border:1px solid #222a33; color:#e9edf2; }
```

- [ ] **Step 2: Implement `map.js`**

```javascript
// static/js/map.js
import { api } from "./api.js";
import { setState } from "./state.js";

const RAMP = ["#2a2410", "#6b5312", "#b98a16", "#f0b429", "#ffd968"];
const NODATA = "#10151b";

function color(count, max) {
  if (!count) return NODATA;
  const t = Math.log1p(count) / Math.log1p(max || 1);
  return RAMP[Math.min(RAMP.length - 1, Math.floor(t * RAMP.length))];
}

export async function initMap(el) {
  const svgText = await fetch("/world.svg").then(r => r.text());
  el.innerHTML = svgText + '<div class="map-legend"></div><div class="map-tray"></div>';
  const svg = el.querySelector("svg");
  const geo = await api.geo(0);
  paint(svg, el, geo);
  hover(svg, el, geo);
}

function paint(svg, el, geo) {
  const counts = geo.countries;
  const max = Math.max(1, ...Object.values(counts).map(c => c.items));
  svg.querySelectorAll("path[id]").forEach(p => {
    const iso = p.id.toUpperCase();
    const d = counts[iso];
    p.style.fill = color(d ? d.items : 0, max);
    if (d) {
      p.classList.add("hit");
      p.addEventListener("click", () => {
        svg.querySelectorAll("path.active").forEach(x => x.classList.remove("active"));
        p.classList.add("active");
        setState({ region: d.region, country: iso });
      });
    }
  });
  const legend = el.querySelector(".map-legend");
  legend.innerHTML = "fewer " + RAMP.map(c => '<i style="background:' + c + '"></i>').join("") + " more";
  const tray = el.querySelector(".map-tray");
  tray.innerHTML = Object.entries(geo.buckets)
    .map(([name, b]) => '<button class="chip" data-scope="' + name + '">' + name + ' <span>' + b.items + '</span></button>').join("");
  tray.querySelectorAll("[data-scope]").forEach(btn =>
    btn.addEventListener("click", () => setState({ scope: btn.dataset.scope, country: null })));
}

function hover(svg, el, geo) {
  let tip;
  svg.querySelectorAll("path[id]").forEach(p => {
    const d = geo.countries[p.id.toUpperCase()];
    p.addEventListener("mousemove", (e) => {
      if (!tip) { tip = document.createElement("div"); tip.className = "map-tip"; document.body.appendChild(tip); }
      const name = d ? d.name : p.id.toUpperCase();
      tip.textContent = d ? (name + " — " + d.items + " items, " + d.sources + " sources") : (name + " — no sources");
      tip.style.left = (e.clientX + 12) + "px"; tip.style.top = (e.clientY + 12) + "px";
    });
    p.addEventListener("mouseleave", () => { if (tip) { tip.remove(); tip = null; } });
  });
}
```

- [ ] **Step 3: Wire into `main.js`**

```javascript
import { initMap } from "./map.js";
initMap(document.getElementById("map"));
```

Serve `world.svg` fetch works because of Task 8's route.

- [ ] **Step 4: Verify**

Run: `py server.py --open`. Confirm: map renders dark, countries with items are shaded along the heat ramp, hover shows a tooltip with counts, clicking a shaded country filters the feed and highlights it cyan, the legend shows, and the Transnational tray chips appear and filter.

- [ ] **Step 5: Commit**

```bash
git add static/js/map.js static/js/main.js static/index.html
git commit -m "Add dark SVG choropleth map with hover, click-filter and tray"
```

---

## Task 10: Editorial aesthetic rules in CSS

**Files:**
- Modify: `static/index.html` (CSS)

Apply the "editorial restraint" rules app-wide (serif display headings, mono tabular numbers already present) without touching the always-dark map panel.

- [ ] **Step 1: Add the heading rules**

```css
h1, h2, .brand, .spine-region > .spine-row .l {
  font-family: Georgia, "Times New Roman", serif;
  letter-spacing: .1px;
}
.spine-row .n, .map-legend, .time, .chip .n { font-variant-numeric: tabular-nums; }
```

Do not set `.title` (article titles) in serif; keep them sans for density.

- [ ] **Step 2: Verify**

Run: `py server.py --open`. Confirm section headings and the brand read as serif, article list stays sans, numbers are tabular, and nothing else changed. Check both light and dark OS themes.

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "Apply editorial serif/tabular aesthetic rules"
```

---

## Task 11: Obtain RSF 2026 press-freedom data (gating)

**Files:**
- Create: `geo/press-freedom-2026.json`

Acceptance criteria:
- Values come from the RSF World Press Freedom Index 2026 (confirm it is published; if only 2025 exists, use 2025 and name the file accordingly).
- Format: ISO alpha-2 -> numeric score (0-100, higher = more free, matching RSF's own scale). Record the source URL in the commit.
- Confirm reuse terms allow inclusion in a public repo; if not, store only the numeric scores (facts) with attribution, not RSF's prose.

- [ ] **Step 1: Create the file**

```json
{ "ES": 78.2, "FR": 78.6, "RU": 29.9 }
```

(Fill with the real published values for every country in the reference.)

- [ ] **Step 2: Serve/load**

Add `PRESS_FREEDOM_FILE = os.path.join(BASE_DIR, "geo", "press-freedom-2026.json")` and a cached loader `load_press_freedom()` mirroring `load_countries()`. Extend `geo_payload` to include `"press_freedom": load_press_freedom()`.

- [ ] **Step 3: Verify**

Run: `curl http://127.0.0.1:8787/api/geo` and confirm a `press_freedom` map is present.

- [ ] **Step 4: Commit**

```bash
git add geo/press-freedom-2026.json server.py
git commit -m "Add RSF 2026 press-freedom data (source: <url>)"
```

---

## Task 12: Press-freedom map layer toggle

**Files:**
- Modify: `static/js/map.js`, `static/index.html` (toggle control)

A toggle recolors the map by press-freedom instead of counts, using a distinct factual ramp, attributed to RSF.

- [ ] **Step 1: Add a toggle control**

In the map container header add `<button id="pf-toggle" class="btn">Press freedom</button>` and a small attribution line `Source: RSF 2026` shown when active.

- [ ] **Step 2: Add a press-freedom paint mode in `map.js`**

```javascript
const PF_RAMP = ["#c0392b", "#d98f2b", "#e9d16b", "#6fae7a", "#2f8f5b"]; // low -> high freedom
function pfColor(score) {
  if (score == null) return NODATA;
  const t = Math.max(0, Math.min(1, score / 100));
  return PF_RAMP[Math.min(PF_RAMP.length - 1, Math.floor(t * PF_RAMP.length))];
}
export function paintPressFreedom(svg, geo) {
  const pf = geo.press_freedom || {};
  svg.querySelectorAll("path[id]").forEach(p => { p.style.fill = pfColor(pf[p.id.toUpperCase()]); });
}
```

Wire `#pf-toggle` to switch between `paint(...)` (counts) and `paintPressFreedom(svg, geo)`, swapping the legend to the RSF ramp and showing the attribution. Keep the geo payload in scope (store it on `initMap`).

- [ ] **Step 3: Verify**

Run: `py server.py --open`. Toggle on: map recolors by press freedom, legend and RSF attribution show. Toggle off: returns to counts. Hover still shows item counts.

- [ ] **Step 4: Commit**

```bash
git add static/js/map.js static/index.html
git commit -m "Add press-freedom map layer toggle (RSF 2026)"
```

---

## Task 13: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a "Code structure and guidelines" preface**

Near the top (after the intro), add a section stating: `server.py` is a single standard-library file (split into a package only past ~1,500 lines); the frontend is vanilla ES modules under `static/js/` with no build step and no dependency; each module has one responsibility; schema changes go through the numbered migrations in `server.py`; new dependencies need a reason and must be optional with ARM64 Linux wheels (restate).

- [ ] **Step 2: Update the UI constraint**

Change the `static/index.html` architecture bullet from "the whole UI in one file" to: UI is `index.html` plus vanilla ES modules in `static/js/` (`api`, `state`, `map`, `spine`, `reader`, `main`), no build step, no `??`/`?.`.

- [ ] **Step 3: Add the aesthetic rules**

Add a "Visual rules" subsection with: the map panel is always dark (tokens: bg `#0b0e12`, no-data `#10151b`, borders `#222a33`, heat ramp `#2a2410..#ffd968`, active cyan `#35d0d6`); editorial serif display headings, sans article lists, tabular numbers; color always encodes meaning; one accent per surface; no gradients/glass/emoji/blur in the chrome.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "Document code structure, ES-module UI constraint and visual rules"
```

---

## Self-review notes

- **Spec coverage:** data model (Tasks 2-4), `/api/geo` (Task 5), migrations (Task 1), ES-module split (Task 6), spine (Task 7), map + hover/click/tray (Tasks 8-9), aesthetic rules (Tasks 9-10, 13), press-freedom layer (Tasks 11-12), CLAUDE.md changes (Task 13), testing via `unittest` + browser verification throughout. All spec sections map to a task.
- **Gating:** Tasks 8 and 11 carry external unknowns (SVG asset, RSF data) and are isolated so the rest ships without them. The count map (Task 9) depends only on Task 8, not on RSF.
- **Type consistency:** `aggregate_geo` returns `{countries, buckets}` (Task 5), consumed unchanged by `map.js` and `spine.js` (Tasks 7, 9); `geo_payload` adds `updated` and later `press_freedom`. `country_iso`/`scope`/`exile`/`covers` names are identical across `validate_sources`, `source_payload`, `aggregate_geo`, and the JS.
```
