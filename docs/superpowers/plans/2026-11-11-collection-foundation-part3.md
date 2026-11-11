# Collection Foundation Implementation Plan, part 3 of 3

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Tasks 10 to 16: archive search, the reader's Archive mode, language filter, Windows host, source batches and documentation. Read the header, ground rules, deviations and file structure in `2026-11-11-collection-foundation.md` first. Tasks 1 to 9 must be merged before these start.

---

### Task 10: Archive search (FTS5 with a `LIKE` fallback)

**Files:**
- Modify: `newscentral/store.py`, `newscentral/api.py`, `tests/helpers.py`
- Create: `tests/test_search.py`

- [ ] **Step 1: Let tests choose the engine**

In `tests/helpers.py`, give `ApiCase` a class attribute `fts = True` and create the store with it: `self.store = Store(os.path.join(self.dir, "news.db"), fts=self.fts)`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_search.py
import os
import sqlite3
import unittest

from newscentral.store import Store, fts5_available
from tests.helpers import ApiCase

RUSSIAN = "Новости экономики"
ARABIC = "الأخبار الاقتصادية"


def has_fts5():
    db = sqlite3.connect(":memory:")
    try:
        return fts5_available(db)
    finally:
        db.close()


class SearchTests:
    """Shared by both engines. Queries use whole words so FTS and LIKE agree."""

    engine = None

    def setUp(self):
        super().setUp()
        self.add("elpais", "Banco de España sube tipos", "https://example.com/1", "La inflación repunta")
        self.add("bbc", "Bank of England holds rates", "https://example.com/2", "Inflation eases")
        self.add("ahram", ARABIC, "https://example.com/3")
        self.add("meduza", RUSSIAN, "https://example.com/4")
        self.add("ecb", "ECB rates decision", "https://example.com/5", "Monetary policy statement")
        self.add("gone", "Rates at a closed outlet", "https://example.com/6", lang="en")

    def titles(self, query):
        status, body = self.get("/api/search?" + query)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["engine"], self.engine)
        return sorted(i["title"] for i in body["items"])

    def test_single_term(self):
        self.assertEqual(self.titles("q=rates"), ["Bank of England holds rates", "ECB rates decision",
                                                  "Rates at a closed outlet"])

    def test_terms_are_anded_and_case_insensitive(self):
        self.assertEqual(self.titles("q=RATES+england"), ["Bank of England holds rates"])

    def test_matches_summary(self):
        self.assertEqual(self.titles("q=monetary"), ["ECB rates decision"])

    def test_non_latin_text(self):
        self.assertEqual(self.titles("q=" + "%D0%9D%D0%BE%D0%B2%D0%BE%D1%81%D1%82%D0%B8"), [RUSSIAN])

    def test_query_syntax_characters_are_harmless(self):
        for q in ('%22rates', 'rates)', 'rates+OR', 'NEAR(rates', 'rates*', '-rates', 'title:rates', 'rates+%25_'):
            status, body = self.get("/api/search?q=" + q)
            self.assertEqual(status, 200, (q, body))

    def test_filters(self):
        self.assertEqual(self.titles("q=rates&source=bbc"), ["Bank of England holds rates"])
        self.assertEqual(self.titles("q=rates&kind=official"), ["ECB rates decision"])
        self.assertEqual(self.titles("q=rates&scope=Intergovernmental"), ["ECB rates decision"])
        self.assertEqual(self.titles("q=rates&region=Global"), ["Bank of England holds rates"])
        self.assertEqual(self.titles("source=ahram&lang=ar"), [ARABIC])
        self.assertEqual(self.titles("source=ahram&lang=en"), [])

    def test_exile_outlets_match_the_country_they_cover(self):
        self.assertEqual(self.titles("from=0&country=RU"), [RUSSIAN])
        self.assertEqual(self.titles("from=0&country=LV"), [])

    def test_date_range(self):
        self.store.db.execute("UPDATE items SET first_seen = 1000 WHERE source_id = 'bbc'")
        self.store.db.execute("UPDATE items SET first_seen = 2000 WHERE source_id = 'ecb'")
        self.store.db.commit()
        self.assertEqual(self.titles("from=500&to=1500"), ["Bank of England holds rates"])
        self.assertEqual(self.titles("from=500&to=2500"), ["Bank of England holds rates", "ECB rates decision"])
        self.assertEqual(self.titles("q=rates&from=1500&to=2500"), ["ECB rates decision"])

    def test_paging_and_total(self):
        _, body = self.get("/api/search?from=0&limit=2&offset=0")
        self.assertEqual((len(body["items"]), body["total"], body["limit"], body["offset"]), (2, 6, 2, 0))
        _, more = self.get("/api/search?from=0&limit=2&offset=4")
        self.assertEqual(len(more["items"]), 2)
        self.assertFalse({i["id"] for i in body["items"]} & {i["id"] for i in more["items"]})

    def test_limit_is_clamped(self):
        self.assertEqual(self.get("/api/search?from=0&limit=999")[1]["limit"], 200)
        self.assertEqual(self.get("/api/search?from=0&limit=0")[1]["limit"], 1)

    def test_requires_a_criterion(self):
        self.assertEqual(self.get("/api/search")[0], 400)
        self.assertEqual(self.get("/api/search?region=Spain")[0], 400)
        self.assertEqual(self.get("/api/search?q=%22%29")[0], 400)      # no searchable term
        self.assertEqual(self.get("/api/search?q=%25_")[0], 400)
        self.assertEqual(self.get("/api/search?from=abc")[0], 400)

    def test_item_fields_and_removed_source(self):
        _, body = self.get("/api/search?q=inflaci%C3%B3n")
        item = body["items"][0]
        self.assertEqual((item["s"], item["source_name"], item["lang"], item["country_iso"], item["kind"]),
                         ("elpais", "El País", "es", "ES", "news"))
        self.assertIn("t", item)
        _, body = self.get("/api/search?source=gone")
        self.assertEqual((body["items"][0]["source_name"], body["items"][0]["kind"]), ("gone", "news"))


@unittest.skipUnless(has_fts5(), "this SQLite build has no FTS5")
class TestSearchFts(SearchTests, ApiCase):
    engine = "fts"

    def test_diacritics_are_folded(self):
        self.assertEqual(self.titles("q=espana"), ["Banco de España sube tipos"])
        self.assertEqual(self.titles("q=inflacion"), ["Banco de España sube tipos"])

    def test_index_is_rebuilt_when_triggers_were_missing(self):
        path = os.path.join(self.dir, "news.db")
        self.store.db.execute("DROP TRIGGER items_fts_ai")
        self.store.db.commit()
        self.add("bbc", "Untracked zebra story", "https://example.com/7")
        self.assertEqual(self.titles("q=zebra"), [])
        self.store.db.close()
        self.store = Store(path)
        self.app.store = self.store
        self.assertEqual(self.titles("q=zebra"), ["Untracked zebra story"])


class TestSearchLike(SearchTests, ApiCase):
    engine = "like"
    fts = False

    def test_no_fts_triggers_are_left_behind(self):
        names = [r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]
        self.assertEqual(names, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run them**

Run: `py -m unittest tests.test_search`
Expected: `ImportError: cannot import name 'fts5_available'`.

- [ ] **Step 4: The index in `newscentral/store.py`**

Add `import re` if it is not imported. Module level:

```python
FTS_TABLE = ("CREATE VIRTUAL TABLE items_fts USING fts5(title, summary, content='items', "
             "content_rowid='rowid', tokenize='%s')")
FTS_TRIGGERS = {
    "items_fts_ai": "CREATE TRIGGER items_fts_ai AFTER INSERT ON items BEGIN "
                    "INSERT INTO items_fts(rowid, title, summary) VALUES (new.rowid, new.title, new.summary); END",
    "items_fts_ad": "CREATE TRIGGER items_fts_ad AFTER DELETE ON items BEGIN "
                    "INSERT INTO items_fts(items_fts, rowid, title, summary) "
                    "VALUES ('delete', old.rowid, old.title, old.summary); END",
    "items_fts_au": "CREATE TRIGGER items_fts_au AFTER UPDATE OF title, summary ON items BEGIN "
                    "INSERT INTO items_fts(items_fts, rowid, title, summary) "
                    "VALUES ('delete', old.rowid, old.title, old.summary); "
                    "INSERT INTO items_fts(rowid, title, summary) VALUES (new.rowid, new.title, new.summary); END",
}


def fts5_available(db):
    try:
        db.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(x)")
        db.execute("DROP TABLE temp.fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def search_terms(q):
    """Words of a user query: runs of letters and digits in any script. Everything else, the
    underscore included, is dropped, so FTS query syntax (quotes, parentheses, operators, column
    filters) can never reach the engine and no term is empty after tokenizing."""
    return re.findall(r"[^\W_]+", q or "", re.UNICODE)
```

`Store.__init__` takes `fts=True`, stores `self.fts = False`, and calls `self.ensure_fts(fts)` after the migrations:

```python
    def ensure_fts(self, wanted=True):
        """The search index is derived data that depends on the SQLite build, not on the schema
        version, so it is not a numbered migration. Runs at every startup.
        The index uses the implicit rowid of `items`: never VACUUM the database without calling
        this afterwards with the triggers dropped, because VACUUM may renumber rowids."""
        with self.lock:
            names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master")}
            if not (wanted and fts5_available(self.db)):
                # Triggers that write to a module this build lacks would break every insert.
                for name in FTS_TRIGGERS:
                    self.db.execute("DROP TRIGGER IF EXISTS %s" % name)
                self.db.commit()
                self.fts = False
                return False
            rebuild = False
            self.db.execute("BEGIN")
            try:
                if "items_fts" not in names:
                    try:
                        self.db.execute(FTS_TABLE % "unicode61 remove_diacritics 2")
                    except sqlite3.OperationalError:      # SQLite older than 3.27
                        self.db.execute(FTS_TABLE % "unicode61")
                    rebuild = True
                for name, sql in FTS_TRIGGERS.items():
                    if name not in names:
                        self.db.execute(sql)
                        rebuild = True
                if rebuild:
                    self.db.execute("INSERT INTO items_fts(items_fts) VALUES ('rebuild')")
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            self.fts = True
            return True
```

- [ ] **Step 5: The query in `newscentral/store.py`**

```python
    def search(self, q="", since=None, until=None, region=None, country=None, scope=None, lang=None,
               kind=None, source=None, limit=50, offset=0):
        """Whole-archive search. Returns (rows, total, engine). Dates filter on first_seen."""
        terms = search_terms(q)
        joins = "FROM items i LEFT JOIN sources s ON s.id = i.source_id"
        where, params = [], []
        order = "i.first_seen DESC"
        if terms and self.fts:
            joins += " JOIN items_fts ON items_fts.rowid = i.rowid"
            where.append("items_fts MATCH ?")
            params.append(" ".join('"%s"' % t for t in terms))
            order = "bm25(items_fts), i.first_seen DESC"
        elif terms:
            for t in terms:
                like = "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                where.append("(i.title LIKE ? ESCAPE '\\' OR i.summary LIKE ? ESCAPE '\\')")
                params.extend([like, like])
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
            total = self.db.execute("SELECT COUNT(*) " + joins + sql_where, params).fetchone()[0]
            rows = [dict(r) for r in self.db.execute(
                "SELECT i.id, i.source_id, i.title, i.link, i.summary, i.published, i.first_seen, i.lang, "
                "s.name AS source_name, s.region, s.country_iso, s.scope, s.exile, s.covers, s.kind "
                + joins + sql_where + " ORDER BY " + order + " LIMIT ? OFFSET ?", params + [limit, offset])]
        return rows, total, ("fts" if self.fts else "like")
```

Every value reaches SQL as a bound parameter; the only text concatenated into the statement is the fixed clauses above.

- [ ] **Step 6: The endpoint in `newscentral/api.py`**

```python
class BadRequest(Exception):
    pass


def search_payload(app, q):
    def one(name):
        return (q.get(name) or [""])[0].strip()

    def number(name, cast):
        value = one(name)
        if not value:
            return None
        try:
            return cast(value)
        except ValueError:
            raise BadRequest("%s must be a number" % name)

    text, source = one("q"), one("source")
    since, until = number("from", float), number("to", float)
    if not search_terms(text) and since is None and not source:
        raise BadRequest("give at least one of q, from or source")
    limit = number("limit", int)
    limit = 50 if limit is None else min(max(limit, 1), 200)
    offset = max(number("offset", int) or 0, 0)
    rows, total, engine = app.store.search(
        text, since, until, one("region"), one("country").upper(), one("scope"), one("lang").lower(),
        one("kind"), source, limit, offset)
    items = [{"id": r["id"], "s": r["source_id"], "t": sort_time(r["published"], r["first_seen"]),
              "fs": r["first_seen"], "title": r["title"], "link": r["link"], "summary": r["summary"] or "",
              "lang": r["lang"] or "", "source_name": r["source_name"] or r["source_id"],
              "region": r["region"] or "", "country_iso": r["country_iso"], "scope": r["scope"],
              "exile": bool(r["exile"]), "covers": r["covers"], "kind": r["kind"] or "news"} for r in rows]
    return {"items": items, "total": total, "engine": engine, "limit": limit, "offset": offset}
```

Import `search_terms` from `.store`. In `Handler.do_GET`, before the final `else`:

```python
            elif path == "/api/search":
                self._json(search_payload(app, q))
```

and add an `except` clause to the `try` in `do_GET`, above `except Exception`:

```python
        except BadRequest as e:
            self._json({"error": str(e)}, 400)
```

- [ ] **Step 7: Run the tests**

Run: `py -m unittest discover -s tests`
Expected: `OK`. On this machine both engines run (SQLite 3.50 has FTS5).

- [ ] **Step 8: Check on the real database**

Stop any running News Central, start `py server.py --port 8799`, wait 30 seconds, then:

```bash
curl -s "http://127.0.0.1:8799/api/search?q=inflacion&limit=3"
curl -s "http://127.0.0.1:8799/api/search?from=0&country=GB&limit=1"
```

Expected: the first prints `"engine": "fts"`, a `total` above 0 and Spanish titles containing "inflación"; the second prints a `total` above 0 with items from United Kingdom outlets. Stop the server.

- [ ] **Step 9: Commit**

```bash
git add newscentral tests
git commit -m "Add whole-archive search endpoint on FTS5 with a LIKE fallback"
```

---

### Task 11: Archive mode in the reader

**Files:**
- Modify: `static/js/api.js`, `static/js/reader.js`, `static/index.html`

- [ ] **Step 1: API wrapper**

Append to `static/js/api.js`:

```javascript
export function fetchSearch(params) {
  var parts = [];
  for (var k in params) {
    if (!Object.prototype.hasOwnProperty.call(params, k)) continue;
    if (params[k] === '' || params[k] === null || params[k] === undefined) continue;
    parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
  }
  return getJSON('/api/search?' + parts.join('&'));
}
```

- [ ] **Step 2: Markup in `static/index.html`**

In `<select id="win">` add a fifth option, and add two date inputs right after the select:

```html
          <option value="archive">Archive (search)</option>
        </select>
        <input class="btn" id="arch-from" type="date" title="From this day" aria-label="From date" hidden>
        <input class="btn" id="arch-to" type="date" title="Up to this day" aria-label="To date" hidden>
```

- [ ] **Step 3: State and helpers in `static/js/reader.js`**

Add `fetchSearch` to the import from `./api.js`. Add `var ARCHIVE_PAGE = 50;` under `PAGE`. Add to the `S` object:

```javascript
  archFrom: '', archTo: '',
  archive: { key: null, items: [], total: 0, loading: false, error: '' },
```

Before using `S.win` anywhere else, check that no other module does arithmetic on the shared `win`: search `static/js/` for `.win`. `spine.js` and `map.js` must not multiply it. If one does, guard it with `typeof st.win === 'number'`.

Add these functions above `renderFeed`:

```javascript
/* ------------------------------------------------------------ archive mode */

function dayStart(v) { return v ? new Date(v + 'T00:00:00').getTime() / 1000 : ''; }

function archiveParams() {
  var p = { q: S.q.trim(), from: dayStart(S.archFrom), to: S.archTo ? dayStart(S.archTo) + 86400 : '' };
  if (S.src) p.source = S.src;
  if (S.country) p.country = S.country;
  else if (S.scope) p.scope = S.scope;
  else if (S.region !== 'All') p.region = S.region;
  if (S.kind !== 'all') p.kind = S.kind;
  return p;
}

// The server needs a text query, a start date or an outlet. Without one there is nothing to ask.
function archiveHasCriteria(p) { return !!(p.q || p.from !== '' || p.source); }

function archiveSource(it) {
  var live = S.sources[it.s];
  if (live) return live;
  // An outlet that is no longer in sources.json: describe it from the archived row.
  return { name: it.source_name, country: it.country_iso || it.scope || '', lang: it.lang, kind: it.kind,
           state_media: false, paywall: false };
}

function loadArchive(more) {
  var A = S.archive, p = archiveParams(), key = JSON.stringify(p);
  if (!more) { A.key = key; A.items = []; A.total = 0; A.error = ''; }
  if (!archiveHasCriteria(p)) { A.loading = false; return; }
  p.limit = ARCHIVE_PAGE;
  p.offset = A.items.length;
  A.loading = true;
  fetchSearch(p).then(function (d) {
    if (A.key !== key) return;                 // the filters changed while this request was out
    A.items = A.items.concat(d.items);
    A.total = d.total;
    A.loading = false;
    renderArchive();
  }).catch(function () {
    if (A.key !== key) return;
    A.loading = false;
    A.error = 'Search failed. Is the News Central server running?';
    renderArchive();
  });
}

function renderArchive() {
  var A = S.archive, p = archiveParams();
  // loadArchive sets A.key synchronously, so a second render with the same filters does not search again.
  if (A.key !== JSON.stringify(p)) loadArchive(false);

  var chips = '';
  if (S.src) {
    var only = S.sources[S.src] ? S.sources[S.src].name : S.src;
    chips += '<button class="chip on" data-clearsrc="1">Only ' + esc(only) + ' ✕</button>';
  }
  chips += ['All'].concat(regionsPresent()).map(function (g) {
    return '<button class="chip' + (S.region === g ? ' on' : '') + '" data-region="' + esc(g) + '">' + esc(g) + '</button>';
  }).join('');
  $('#regions').innerHTML = chips;
  $('#banner').style.display = 'none';
  document.title = 'News Central';

  var el = $('#feed');
  if (!archiveHasCriteria(p)) {
    el.innerHTML = '<div class="empty">Type a search, pick a start date or click an outlet name to search the whole archive.</div>';
    return;
  }
  if (A.error) { el.innerHTML = '<div class="empty">' + esc(A.error) + '</div>'; return; }
  if (!A.items.length) {
    el.innerHTML = '<div class="empty">' + (A.loading ? 'Searching the archive…' : 'Nothing in the archive matches.') + '</div>';
    return;
  }
  var now = Date.now() / 1000;
  var h = '<div class="toolbar">' + A.total.toLocaleString() + ' result' + (A.total === 1 ? '' : 's') + ' in the archive</div>';
  h += A.items.map(function (it) { return itemHTML(it, now, archiveSource(it)); }).join('');
  if (A.items.length < A.total) {
    h += '<button class="btn more" id="more">' + (A.loading ? 'Loading…' : 'Show more (' + (A.total - A.items.length) + ' left)') + '</button>';
  }
  el.innerHTML = h;
}

function syncArchiveInputs() {
  var on = S.win === 'archive';
  $('#arch-from').hidden = !on;
  $('#arch-to').hidden = !on;
  $('#q').placeholder = on ? 'Search the whole archive  ( / )' : 'Search titles, summaries, outlets  ( / )';
}
```

- [ ] **Step 4: Wire it into the existing functions**

`itemHTML` takes the source as an optional third argument. Change its first two lines to:

```javascript
function itemHTML(it, now, src) {
  src = src || S.sources[it.s];
```

`renderFeed` hands over to the archive view. Add as its first lines:

```javascript
  if (S.win === 'archive') {
    // The poller calls renderFeed(false) when new live items arrive; the archive list does not depend on them.
    if (listToo !== false) renderArchive();
    return;
  }
```

In the `change` listener replace the `win` branch:

```javascript
    if (t.id === 'win') {
      S.win = t.value === 'archive' ? 'archive' : +t.value;
      setState({ win: S.win }); pref.set('win', S.win); S.shown = PAGE;
      syncArchiveInputs(); renderFeed();
    }
    else if (t.id === 'arch-from') { S.archFrom = t.value; renderFeed(); }
    else if (t.id === 'arch-to') { S.archTo = t.value; renderFeed(); }
```

(The following branch keeps its `else if`.)

In the `click` listener, change the `#more` line to:

```javascript
    if (t.closest('#more')) {
      if (S.win === 'archive') { loadArchive(true); renderArchive(); return; }
      S.shown += PAGE; renderFeed(); return;
    }
```

In the `input` listener, use a longer debounce in archive mode so each keystroke does not hit the server. Replace `}, 150);` on the `q` branch with:

```javascript
      }, S.win === 'archive' ? 400 : 150);
```

In `initReader`, after `$('#win').value = String(S.win);` add `syncArchiveInputs();`.

- [ ] **Step 5: Verify in a browser**

Run `node --check static/js/reader.js` and search `static/js/` for `??` and `?.` (expected: none in code).

Start `py server.py --port 8799` and open the page. Check each of these, with the console open:

1. Select "Archive (search)". Two date inputs appear and the feed shows the "Type a search…" hint. No request to `/api/search` is made (Network tab).
2. Type `inflacion`. After a short pause the list shows results with a "N results in the archive" line. Accented matches appear.
3. Click "Show more". More rows append and the count left goes down. No row appears twice.
4. Click a region chip, then a country in the spine. The results narrow each time.
5. Click an outlet name. The "Only X ✕" chip appears and the results are only that outlet. Clear it.
6. Set a start date with the search box empty. Results for that range appear.
7. Star an item, open Saved: it is there. Remove it.
8. Switch back to "Last 24 h". The date inputs disappear and the live feed returns with region counts on the chips.
9. Reload with "Archive" selected: the mode is restored and the page does not error.

No console errors at any step. Stop the server.

- [ ] **Step 6: Commit**

```bash
git add static
git commit -m "Add Archive mode to the reader: server-side search with date range and paging"
```

---

### Task 12: Language filter, text direction, `official` kind and truncation notice

**Files:**
- Modify: `static/js/reader.js`, `static/index.html`

- [ ] **Step 1: Markup in `static/index.html`**

Replace the `kind` select and add the checkbox after "Hide state-controlled":

```html
        <select class="btn" id="kind">
          <option value="all">All kinds</option>
          <option value="news">News only</option>
          <option value="analysis">Analysis only</option>
          <option value="official">Official only</option>
        </select>
        <label class="btn"><input type="checkbox" id="hidesports"> Hide sports &amp; celebrity</label>
        <label class="btn"><input type="checkbox" id="hidestate"> Hide state-controlled</label>
        <label class="btn" title="Languages outside read_langs in config.json"><input type="checkbox" id="showlangs"> Show other languages <span id="showlangs-n"></span></label>
```

- [ ] **Step 2: State in `static/js/reader.js`**

Add to the `S` object:

```javascript
  readLangs: ['es', 'en'], showLangs: pref.get('showLangs', false), truncated: false,
```

In `loadSources`, after the line that sets `S.running`:

```javascript
  if (d.read_langs && d.read_langs.length) S.readLangs = d.read_langs;
```

In `loadItems`, after `S.running = d.running; S.updated = d.updated;`:

```javascript
  S.truncated = !!d.truncated;
```

- [ ] **Step 3: Filter in `compute`**

Declare `hidden = 0` next to `pending = 0`. Insert right after the line that computes `inRegion`, before the `if (it.fs > S.cutoff)` line:

```javascript
    // Languages the user does not read stay out of the feed (and out of its counts) unless asked for.
    if (!S.showLangs && S.readLangs.indexOf(src.lang) === -1) {
      if (inRegion && it.fs <= S.cutoff) hidden++;
      continue;
    }
```

Return it: `return { shown: shown, counts: counts, total: total, pending: pending, hidden: hidden };`

In `renderFeed`, right after `var r = compute();`:

```javascript
  $('#showlangs-n').textContent = r.hidden ? '(' + r.hidden + ')' : '';
```

In `renderArchive`, next to the line that hides the banner, clear it: `$('#showlangs-n').textContent = '';`. The archive is not filtered by language: a search there is explicit.

In the `change` listener add a branch:

```javascript
    else if (t.id === 'showlangs') { S.showLangs = t.checked; pref.set('showLangs', S.showLangs); S.shown = PAGE; renderFeed(); }
```

In `initReader` add `$('#showlangs').checked = S.showLangs;`.

- [ ] **Step 4: Direction, kind tag and truncation notice**

In `itemHTML`, add after the `analysis` tag line:

```javascript
  if (src.kind === 'official') tags.push('<span class="tag an">official</span>');
```

and add `dir="auto"` to the title link and the summary, so right-to-left scripts align and punctuate correctly:

```javascript
    '<a class="title" dir="auto" href="' + safeHref(it.link) + '" target="_blank" rel="noopener">' + esc(it.title) + '</a>' +
    (it.summary ? '<div class="sum" dir="auto">' + esc(it.summary) + '</div>' : '') + '</div>' +
```

In `renderSaved`, make the same two additions: `<a class="title" dir="auto" ...>` and `<div class="sum open" dir="auto">`.

In `renderStatus`, before the line that writes `txt` to `#status`:

```javascript
  if (S.truncated) txt += ' · too many items in the window, showing the newest';
```

- [ ] **Step 5: Verify in a browser**

Run `node --check static/js/reader.js` and search for `??` and `?.` (expected: none in code).

Start `py server.py --port 8799` and open the page with the console open:

1. With the box unchecked, "Show other languages" shows a number in parentheses (French and Portuguese outlets exist today) and no item in the feed carries an `fr` or `pt` tag.
2. Check the box: the number disappears, `fr` and `pt` items appear, and the "All" chip count rises by the number that was shown.
3. Reload: the box keeps its state.
4. The kind selector lists "Official only"; selecting it shows an empty feed until batch 1a adds official sources. Set it back to "All kinds".
5. Check right-to-left rendering without waiting for Arabic sources. In the console run:
   `var a = document.querySelector('#feed a.title'); a.textContent = 'الأخبار اليوم.'; getComputedStyle(a).direction`
   Expected: `"rtl"`, and the text is right-aligned with the full stop on the left.
6. Map and spine counts are the same with the box checked or unchecked.

Stop the server.

- [ ] **Step 6: Commit**

```bash
git add static
git commit -m "Hide unread languages by default, set text direction, add official kind filter"
```

---

### Task 13: Log file and keep-awake

**Files:**
- Modify: `newscentral/cli.py`, `newscentral/fetcher.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import os
import shutil
import sys
import tempfile
import unittest

from newscentral import cli


class TestSetupLog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "logs", "server.log")
        self.saved = (sys.stdout, sys.stderr)

    def tearDown(self):
        sys.stdout, sys.stderr = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_redirects_both_streams_and_creates_the_folder(self):
        stream = cli.setup_log(self.path)
        try:
            print("to stdout")
            print("to stderr", file=sys.stderr)
        finally:
            sys.stdout, sys.stderr = self.saved
            stream.close()
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "to stdout\nto stderr\n")

    def test_rotates_a_large_file(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("x" * 200)
        stream = cli.setup_log(self.path, max_bytes=100)
        try:
            print("fresh")
        finally:
            sys.stdout, sys.stderr = self.saved
            stream.close()
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "fresh\n")
        with open(self.path + ".1", encoding="utf-8") as f:
            self.assertEqual(len(f.read()), 200)

    def test_small_file_is_appended_to(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("old\n")
        stream = cli.setup_log(self.path)
        try:
            print("new")
        finally:
            sys.stdout, sys.stderr = self.saved
            stream.close()
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "old\nnew\n")


class TestKeepAwake(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_windows_accepts_and_clears_the_request(self):
        self.assertTrue(cli.keep_awake(True))
        self.assertTrue(cli.keep_awake(False))

    @unittest.skipIf(os.name == "nt", "non-Windows only")
    def test_other_systems_ignore_it(self):
        self.assertFalse(cli.keep_awake(True))
        self.assertFalse(cli.keep_awake(False))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them**

Run: `py -m unittest tests.test_cli`
Expected: errors with `module 'newscentral.cli' has no attribute 'setup_log'`.

- [ ] **Step 3: Implement in `newscentral/cli.py`**

```python
def setup_log(path, max_bytes=5 * 1024 * 1024):
    """Send stdout and stderr to a file. Needed under pythonw, which has no console.
    A file over max_bytes is moved to <path>.1 first (one previous file is kept)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
    except OSError:
        pass
    stream = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    return stream


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake(on):
    """Ask Windows not to sleep on idle while this thread is alive (on=True), or withdraw the
    request (on=False). Call it from the thread that runs the server. It does not override the
    lid-close action: tools/windows/install.ps1 sets that. Returns True when Windows accepted."""
    if os.name != "nt":
        if on:
            print("keep_awake is only supported on Windows; ignored", flush=True)
        return False
    import ctypes
    fn = ctypes.windll.kernel32.SetThreadExecutionState
    fn.argtypes = [ctypes.c_uint]
    fn.restype = ctypes.c_uint
    return bool(fn(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)))
```

In `main`, right after `p = paths(cfg)`:

```python
    if p["log"]:
        setup_log(p["log"])
```

and replace the final `try` block:

```python
    awake = cfg["keep_awake"] and keep_awake(True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if awake:
            keep_awake(False)
    return 0
```

In `newscentral/fetcher.py`, give the cycle line a date so the log file is readable across days: change `time.strftime("%H:%M:%S")` to `time.strftime("%Y-%m-%d %H:%M:%S")`.

- [ ] **Step 4: Run the tests**

Run: `py -m unittest discover -s tests`
Expected: `OK`.

- [ ] **Step 5: Check the real thing on Windows**

Write a temporary `config.json` (it is gitignored; delete it after the check if you did not have one):

```json
{ "keep_awake": true, "log_file": "data/server.log" }
```

Start `py server.py --port 8799`, wait 60 seconds, then in an elevated PowerShell run `powercfg /requests`.
Expected: under `SYSTEM:` a line naming `python.exe` (or `py.exe`). `data/server.log` contains the "News Central is running" line and a dated "checked N sources" line. Stop the server and run `powercfg /requests` again: the python line is gone.

- [ ] **Step 6: Commit**

```bash
git add newscentral tests
git commit -m "Add log file redirection and a Windows keep-awake request"
```

---

### Task 14: Windows host scripts

These scripts change system settings. The implementer writes and parse-checks them but does **not** run them. The user runs them.

**Files:**
- Create: `tools/windows/install.ps1`, `tools/windows/uninstall.ps1`

Keep both files pure ASCII: Windows PowerShell 5.1 misreads non-ASCII characters in a script without a byte-order mark.

- [ ] **Step 1: Create `tools/windows/install.ps1`**

```powershell
#Requires -RunAsAdministrator
# News Central: start at logon and keep this laptop collecting on AC power with the lid closed.
# Run from an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File tools\windows\install.ps1
# Undo with tools\windows\uninstall.ps1.

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dataDir = Join-Path $repo 'data'
$taskName = 'NewsCentral'
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

$launcher = Get-Command pyw.exe -ErrorAction SilentlyContinue
if (-not $launcher) { $launcher = Get-Command pythonw.exe -ErrorAction SilentlyContinue }
if (-not $launcher) { throw 'Neither pyw.exe nor pythonw.exe is on PATH. Install Python from python.org first.' }

New-Item -ItemType Directory -Force $dataDir | Out-Null

# 1. config.json: only created when missing, never edited.
$configPath = Join-Path $repo 'config.json'
if (Test-Path $configPath) {
    Write-Host 'config.json already exists and was left unchanged.'
    Write-Host '  Make sure it has  "keep_awake": true  and  "log_file": "data/server.log".'
} else {
    $json = "{`n  `"keep_awake`": true,`n  `"log_file`": `"data/server.log`"`n}`n"
    [System.IO.File]::WriteAllText($configPath, $json, $utf8NoBom)
    Write-Host 'Created config.json (keep_awake on, log in data\server.log).'
}

# 2. Power, AC only. Battery settings are not touched.
$powerSettings = @(
    @{ name = 'lid';       sub = 'SUB_BUTTONS'; id = 'LIDACTION' },
    @{ name = 'standby';   sub = 'SUB_SLEEP';   id = 'STANDBYIDLE' },
    @{ name = 'hibernate'; sub = 'SUB_SLEEP';   id = 'HIBERNATEIDLE' }
)

function Get-AcIndex($sub, $id) {
    # powercfg prints localized labels, so read the values by position: the last two
    # 0x numbers of the output are the current AC and DC indexes, in that order.
    $out = (powercfg /query SCHEME_CURRENT $sub $id) -join "`n"
    $found = [regex]::Matches($out, '0x[0-9a-fA-F]{8}')
    if ($found.Count -lt 2) { throw "Could not read power setting $sub $id" }
    return [Convert]::ToInt64($found[$found.Count - 2].Value, 16)
}

$backupPath = Join-Path $dataDir 'power-backup.json'
if (-not (Test-Path $backupPath)) {
    $backup = @{}
    foreach ($s in $powerSettings) { $backup[$s.name] = Get-AcIndex $s.sub $s.id }
    [System.IO.File]::WriteAllText($backupPath, ($backup | ConvertTo-Json), $utf8NoBom)
    Write-Host "Saved the previous AC power settings to $backupPath"
}
foreach ($s in $powerSettings) {
    powercfg /setacvalueindex SCHEME_CURRENT $s.sub $s.id 0
    if ($LASTEXITCODE -ne 0) { throw "powercfg could not set $($s.id)" }
}
powercfg /setactive SCHEME_CURRENT
Write-Host 'On AC power: closing the lid does nothing, no sleep, no hibernate.'

# 3. Scheduled task: start hidden at logon, restart on failure.
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $launcher.Source -Argument 'server.py' -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "Registered and started the scheduled task '$taskName' for $user."
Write-Host 'Reader: http://127.0.0.1:8787   Log: data\server.log'
```

- [ ] **Step 2: Create `tools/windows/uninstall.ps1`**

```powershell
#Requires -RunAsAdministrator
# News Central: remove the scheduled task and restore the AC power settings saved by install.ps1.
#   powershell -ExecutionPolicy Bypass -File tools\windows\uninstall.ps1

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$taskName = 'NewsCentral'

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed the scheduled task '$taskName'."
} else {
    Write-Host "No scheduled task '$taskName' found."
}

$backupPath = Join-Path (Join-Path $repo 'data') 'power-backup.json'
if (Test-Path $backupPath) {
    $backup = Get-Content -Raw $backupPath | ConvertFrom-Json
    powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION $backup.lid
    powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE $backup.standby
    powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE $backup.hibernate
    powercfg /setactive SCHEME_CURRENT
    Remove-Item $backupPath
    Write-Host 'Restored the previous AC power settings.'
} else {
    Write-Host 'No saved power settings found; power settings were left as they are.'
}
Write-Host 'config.json and data\ were left in place. A server that is still running keeps running until it is closed.'
```

- [ ] **Step 3: Parse-check both scripts without running them**

Run with the PowerShell tool:

```powershell
foreach ($f in 'tools\windows\install.ps1', 'tools\windows\uninstall.ps1') { $e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path $f), [ref]$null, [ref]$e); "$f : $($e.Count) parse errors" }
```

Expected: `0 parse errors` for both.

Confirm both files are ASCII: `py -c "import sys; [open(f,'rb').read().decode('ascii') for f in sys.argv[1:]]; print('ascii ok')" tools/windows/install.ps1 tools/windows/uninstall.ps1`
Expected: `ascii ok`.

Read-only check that the positional parsing in `Get-AcIndex` holds on this machine (Spanish Windows prints localized labels):

```powershell
$out = (powercfg /query SCHEME_CURRENT SUB_BUTTONS LIDACTION) -join "`n"; [regex]::Matches($out, '0x[0-9a-fA-F]{8}') | ForEach-Object Value
```

Expected: at least two values, the last two being small numbers such as `0x00000001`. If the output has fewer than two matches, stop and report: the script needs another way to read the setting on this machine.

- [ ] **Step 4: Commit**

```bash
git add tools/windows
git commit -m "Add Windows install and uninstall scripts for autostart and AC power settings"
```

- [ ] **Step 5: Hand over to the user (not a subagent step)**

The user runs `install.ps1` from an elevated PowerShell and confirms:

1. `http://127.0.0.1:8787` answers a minute later, with no console window open.
2. `data\server.log` grows by one "checked N sources" line every 15 minutes.
3. With the charger connected, closing the lid for 30 minutes leaves no gap: Sources view, Collection block.
4. After signing out and in again, the server is back without any manual start.
5. With the lid closed for an hour, the laptop is not hot to the touch (the spec lists this as unverified).

---

### Task 15: Candidate checker and source batches

**Files:**
- Create: `tools/check_candidates.py`
- Modify (per batch, after the user approves): `sources.json`, `ROADMAP.md`

- [ ] **Step 1: Create `tools/check_candidates.py`**

```python
#!/usr/bin/env python3
# Fetch candidate sources once and print a review table. Writes nothing.
# Usage: py tools/check_candidates.py candidates.json
# candidates.json is a JSON list of source objects in the sources.json format.
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newscentral.config import SOURCES_FILE                                          # noqa: E402
from newscentral.feeds import fetch_source                                           # noqa: E402
from newscentral.sources import load_countries, load_languages, validate_sources     # noqa: E402


def existing_ids():
    with open(SOURCES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("sources", []) if isinstance(data, dict) else data
    return {str(s.get("id")) for s in raw}          # disabled sources count too


def main(path):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with open(path, encoding="utf-8") as f:
        candidates = json.load(f)
    problems = validate_sources(candidates, load_countries(), load_languages())
    taken, seen = existing_ids(), set()
    for s in candidates:
        if s.get("id") in taken:
            problems.append("%s: id already exists in sources.json" % s.get("id"))
        if s.get("id") in seen:
            problems.append("%s: id repeated in the candidate file" % s.get("id"))
        seen.add(s.get("id"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda s: fetch_source(s, None), candidates))
    print("%-6s %-24s %-4s %-18s %-9s %5s  %s" % ("STATE", "ID", "LANG", "PLACE", "KIND", "ITEMS", "SAMPLE / ERROR"))
    ok = 0
    for s, r in zip(candidates, results):
        place = s.get("country_iso") or s.get("scope") or "?"
        if s.get("exile"):
            place += ">" + str(s.get("covers"))
        flags = ("S" if s.get("state_media") else "") + ("P" if s.get("paywall") else "")
        if r["status"] == "error":
            state, detail = "FAIL", r["error"]
        else:
            ok += 1
            state = "OK" if r["status"] == "ok" else "FOUND"
            detail = r["items"][0]["title"][:70]
            if r["status"] == "discovered":
                detail += "  [feed: %s]" % r["feed"]
        print("%-6s %-24s %-4s %-18s %-9s %5d  %s" % (
            state, str(s.get("id"))[:24], s.get("lang", "?"), (place + (" " + flags if flags else ""))[:18],
            s.get("kind") or "news", len(r["items"]), detail))
    print("\n%d of %d working" % (ok, len(candidates)))
    for p in problems:
        print("PROBLEM", p)
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__ or "usage: check_candidates.py candidates.json")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
```

The sample title is printed so a reviewer can see at a glance whether a non-Latin feed decoded into real text or into garbage.

- [ ] **Step 2: Try it**

Write `one.json` in the scratchpad (not in the repository):

```json
[{"id": "candidate-test", "name": "ECB press", "region": "Europe & EU", "country_iso": null,
  "scope": "Intergovernmental", "lang": "en", "kind": "official",
  "feed": "https://www.ecb.europa.eu/rss/press.html", "site": "https://www.ecb.europa.eu"}]
```

Run: `py tools/check_candidates.py <scratchpad>/one.json`
Expected: one line starting `OK` or `FOUND` with an English headline, then `1 of 1 working` and no `PROBLEM` line. If the line says `FAIL`, the tool still works; the feed URL is just a guess to be corrected during the real batch.

- [ ] **Step 3: Commit the tool**

```bash
git add tools/check_candidates.py
git commit -m "Add a checker that fetches candidate sources and prints a review table"
```

- [ ] **Step 4: Batch procedure (run once per batch by the orchestrator)**

This is the same for every batch. It is data work: no tests are written, and nothing enters `sources.json` before the user approves.

1. **Research.** Dispatch one subagent per sub-theme with web access. The brief must say: find outlets for the theme; for each, give the official site, a feed URL found on the site itself (not guessed), the country where the outlet is based, the language of that feed, whether it is state-owned or state-controlled (a fact about ownership, with the source of the claim), and whether it has a paywall. No opinions about bias or quality. Output: a JSON list in the `sources.json` format with ids in lowercase-hyphen form.
2. **Assemble** the lists into one candidate file in the scratchpad. Set `region` from the existing vocabulary, `country_iso` or `scope`, `lang` from `geo/languages.json`, and `kind`. If a language is missing from `geo/languages.json`, add it there in the same commit as the batch.
3. **Check.** `py tools/check_candidates.py <file>`. Fix feed URLs that fail when the site shows a better one. Read the SAMPLE column for every non-Latin source: garbage there means a charset problem to report, not a source to add.
4. **Present** the table to the user: outlet, country, language, kind, feed state, items, state-owned, paywall, and the outlets that failed with the reason. Wait for approval. The user may drop or add outlets.
5. **Add** the approved sources to `sources.json`, one object per line, inside their region group. Outlets that matter but have no working feed go in with `"disabled": true` and a `note` saying why.
6. **Validate.** `py server.py --check-sources` must print `0 problem(s)`, and `py -m unittest discover -s tests` must pass.
7. **Commit** the batch on its own: `git commit -m "Add sources: <batch name> (<n> outlets)"`.
8. **Record** in `ROADMAP.md` under Known issues any outlet that was wanted and could not be added, with the reason.

- [ ] **Step 5: Batch 1a, economics and EU/Spain primary sources**

May start as soon as Task 4 is merged (it needs the `official` kind in the validator). Sub-themes for the research subagents:

- Economic press in Spanish and English (Spain, Latin America, international).
- EU institutions: Commission, Parliament, Council, ECB, Eurostat, Court of Justice. `kind: official`, `country_iso: null`, `scope: "Intergovernmental"`, `region: "Europe & EU"`.
- Spanish institutions: Banco de España, INE, La Moncloa, Congreso, AIReF, CNMC. `kind: official`, `country_iso: "ES"`, `region: "Spain"`.
- International economic bodies: IMF, OECD, World Bank, BIS, WTO. `kind: official`, `scope: "Intergovernmental"`, `region: "Global"`.

- [ ] **Step 6: Batch 1b, press in original language**

Starts only after Tasks 5, 6 and 12 are merged, so text is decoded and stored correctly and the reader hides what the user cannot read. One to three outlets per key country. Sub-themes:

- French, Portuguese, Italian, German, Catalan.
- Arabic, Persian, Turkish.
- Chinese, Japanese, Korean.
- Russian, Ukrainian, Hindi, Urdu, Bengali, Indonesian.

After this batch has collected for seven days, read the Collection block: the "Characters to translate" column is the input for choosing the translation engine in spec 2.

---

### Task 16: Documentation and spec alignment

**Files:**
- Modify: `CLAUDE.md`, `ROADMAP.md`, `docs/superpowers/specs/2026-11-11-collection-foundation-design.md`

- [ ] **Step 1: `CLAUDE.md`**

Make these edits; keep everything else.

- **Run** section: add two lines to the code block and one sentence after it.

```
python server.py --config other.json   # use another config file (default: config.json, optional)
py tools/check_candidates.py file.json # fetch candidate sources and print a review table
```

"Windows host: `tools/windows/install.ps1` (elevated) registers autostart at logon and sets AC power so the lid can stay closed; `uninstall.ps1` undoes both. `config.json` is per-machine and gitignored; `config.example.json` documents the keys."

- **Code structure and guidelines**, first bullet, replace with:

"`server.py` is only the entry point. The code lives in the `newscentral/` package, one responsibility per module, each depending only on the ones before it: `config`, `text`, `http_client`, `sources`, `feeds`, `store`, `fetcher`, `stats`, `api`, `cli`. Core stays Python 3.8+ standard library; a new dependency needs a reason, must be optional, and must ship ARM64 Linux wheels."

- Add a bullet to the same section:

"All feed text enters the database through `text.normalize()` (markup stripped, Unicode NFC, invisible characters removed, joiners kept). The original text is never replaced by a translation."

- **Architecture**: replace the `server.py` bullet and its sub-bullets with the package description. Keep the facts that still hold (15-minute loop, 16 workers, ETag, fallbacks, discovery, parsing, item id) and change these:
  - Storage: tables `items` (with `lang`), `saved`, `feed_state`, `sources` (snapshot of every source ever seen), `fetch_runs`. Nothing is pruned: the archive is permanent. The live feed and the map look back 96 h.
  - Search: `items_fts` (FTS5) is created at startup when the SQLite build has FTS5; otherwise search uses `LIKE`. Never `VACUUM` the database without rebuilding the index.
  - Decoding: byte-order mark, XML declaration, UTF-8, HTTP charset, and cp1252/latin-1 only for Latin-script languages. An undecodable non-Latin feed fails instead of being stored.
  - HTTP API: add `GET /api/search`, `GET /api/stats`; `/api/items` returns `truncated`; `/api/status` returns `read_langs`.
- **`sources.json` bullet**: `kind` is `news`, `analysis` or `official`; `lang` must exist in `geo/languages.json`; one source is one language.
- **Hard constraints**: replace "Schema changes go through numbered migrations (to be introduced in Phase 1)" with "Schema changes go through numbered migrations, each in one transaction. The FTS index is derived data and is managed by `Store.ensure_fts()`, not by a migration."
- **Testing**: add "The HTTP API is tested with `tests/helpers.ApiCase`, which runs the real handler on an ephemeral port against a temporary database."

- [ ] **Step 2: `ROADMAP.md`**

In Phase 1 mark as done: "Config file: port, listen address, data folder" and "Keep the full archive (drop the 96 h prune for items) and add full-text search over history". In Phase 2 mark as done: "Fetch-gap counter per source" and reword it to "Fetch-gap counter (per collection run, shown in the Sources view)". In Phase 4 add under the heading: "Interim host: the Windows MSI laptop, via `tools/windows/install.ps1`."

Under Known issues add:

- "Chinese, Japanese and Korean text is not segmented by the search index; searching those languages matches whole runs only until translated text exists."
- "No database backup yet: the archive is one file on one laptop."
- "Lid-closed temperature on the MSI and collection across unattended Windows Update reboots are unverified."

- [ ] **Step 3: Align the spec with what was built**

In `docs/superpowers/specs/2026-11-11-collection-foundation-design.md`:

- Section 1: move `sources.py` above `feeds.py` in the layout.
- Section 3, Charset decoding: the order is byte-order mark, XML declaration, UTF-8, HTTP charset; add the reason (servers that announce ISO-8859-1 for UTF-8 feeds).
- Section 4, Migrations: 2 = `sources` and `items.lang`, 3 = renormalize (Unicode steps only, markup is not stripped again), 4 = `fetch_runs`. Replace the FTS entry with: "The FTS index is created at startup by `Store.ensure_fts()` when FTS5 is available, because it depends on the SQLite build and not on the schema version."
- Section 5: add `scope` to the parameter list.
- Change the status line to "Status: implemented" with the date.

- [ ] **Step 4: Full verification before merging**

Run each and read the output:

```bash
py -m unittest discover -s tests
py server.py --check-sources
py server.py --check
```

Expected: `OK`; `0 problem(s)`; the check summary close to the previous run, with every new failure explained.

Search the frontend for forbidden operators: no `??` or `?.` in `static/js/`.

Confirm nothing private is staged: `git status --short` shows no `data/` path and no `config.json`.

Start the server on the default port, load the reader, and walk through Feed, Map (both layers), Saved, Sources (Collection block) and Archive mode once more with the console open.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md ROADMAP.md docs/superpowers/specs/2026-11-11-collection-foundation-design.md
git commit -m "Document the package layout, archive, search and Windows host"
```

Then use superpowers:finishing-a-development-branch to merge `feature/collection-foundation` into `main`.

---

## Self-review against the spec

| Spec section | Tasks |
|---|---|
| 1. Package split | 1 |
| 2. Configuration | 2 |
| 3. Text standard, charset, language per item | 3, 4, 5, 6 |
| 4. Archive, `sources` snapshot, migrations, truncated flag | 6, 7 |
| 5. Archive search | 10, 11 |
| 6. Language filter | 12 |
| 7. Fetch runs, gaps and volume | 8, 9 |
| 8. Windows host | 13, 14 |
| 9. Source batches, `official` kind | 4, 12, 15 |
| 10. Testing | every task; API harness in 1 |
| Build order | Task order follows it; batch gates are stated in Task 15 |

Names used across tasks: `App(store, fetcher, config)`, `status_payload(app)`, `items_payload(app, since)` returning `(items, truncated)` from Task 6, `Store.items(min_first_seen, limit)` returning `(rows, truncated)`, `Store.add_items(source_id, items, lang=None)`, `Store.sync_sources(sources, now=None)`, `Store.ensure_fts(wanted=True)`, `Store.search(...)` returning `(rows, total, engine)`, `search_terms(q)`, `text.normalize(value, limit)`, `text.canonical(value, limit)`, `text.decode_bytes(data, http_charset, latin_fallback)`, `sources.latin_script(lang)`, `stats.gaps(starts, interval, since, now)`, `stats.volume(rows)`, `cli.setup_log(path, max_bytes)`, `cli.keep_awake(on)`.
