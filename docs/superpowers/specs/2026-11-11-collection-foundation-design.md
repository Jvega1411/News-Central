# Collection foundation: design

Date: 2026-11-11. Status: approved in brainstorming, pending written review.

## Context

News Central is becoming the base of a data pipeline for news digestion: a
deterministic daily briefing, weekly or fortnightly debate reports written by
Claude over an MCP connector, and later a move to a Raspberry Pi 5. The work is
split into five specs, each with its own plan:

1. **Collection foundation** (this document).
2. Enrichment: batch-job framework, headline translation with a swappable
   engine, topic tagging, story clustering.
3. Daily briefing: deterministic generation, Briefing tab, phone notification.
4. Claude connector (MCP over the HTTP API) and debate reports for matter files.
5. Migration to the Raspberry Pi 5.

Decisions already taken that constrain this spec:

- The host is the Windows MSI laptop, stationary and plugged in, until the Pi
  arrives.
- Sources grow in batches the user approves one by one, with no target number,
  and will include press in its original language (Romance languages and
  German; Arabic, Persian, Turkish; Chinese, Japanese, Korean; Russian, Hindi
  and others).
- The user reads Spanish and English. Everything else will be translated in
  spec 2. No pay-per-token service; the translation engine is chosen after
  measuring real volume.

## Audit findings this spec answers

Confirmed on 2026-11-11:

- No archive exists. `Store.prune` deletes items older than 96 h.
- Collection is not continuous. The server only fetches while its window is
  open.
- `/api/items` returns at most 30,000 rows and the reader filters client-side.
- Source metadata (name, country, language) lives only in `sources.json`.
  Removing a source orphans its archived items.
- `decode_bytes` reads the encoding from the XML declaration only, then tries
  UTF-8, cp1252 and latin-1. It ignores the HTTP `Content-Type` charset. A feed
  in windows-1251 or GBK that declares its charset only in the header would be
  stored as garbage with no error.
- In the current archive (8,583 items, four Latin-script languages) 182 items
  contain invisible characters and 2 are not in NFC.
- No tests cover the parser, the fetcher or the HTTP API.
- `server.py` is about 1,100 lines.

## Goals

- Keep every item ever fetched, with stable attribution.
- Collect continuously on the MSI and measure the gaps.
- Store text in one canonical form regardless of language.
- Search the whole archive from the reader and from the future connector.
- Keep the reader usable while sources in unread languages are added.
- Produce the volume numbers needed to choose a translation engine.

## Non-goals

Translation, topic tagging, clustering, the briefing, the MCP connector, the Pi
migration, per-source refresh intervals, full article text, and database
backups. The lack of a backup is a known risk (see Risks).

## 1. Package split

No behaviour change. `server.py` becomes a thin entry point so that
`python server.py ...` and `NewsCentral.command` keep working.

```
server.py                 entry: from newscentral.cli import main
newscentral/
  __init__.py
  config.py               defaults, config.json, flag precedence
  text.py                 text normalization, charset decoding
  http_client.py          http_get, curl_get, get_with_fallback
  feeds.py                parse_feed, discover_feeds, fetch_source
  sources.py              load_sources, validate_sources, countries, languages, press freedom
  store.py                Store, migrations
  fetcher.py              Fetcher thread
  stats.py                gap and volume computations (pure functions)
  api.py                  payload builders and the HTTP handler
  cli.py                  argument parsing, --check, --check-sources, main
```

Each module has one responsibility and depends only on modules above it in
this list. `STORE` and `FETCHER` stop being module globals read by payload
functions: the handler receives them through an application object, so the API
can be tested against a temporary database.

Tests import from `newscentral`. `CLAUDE.md` is updated: the single-file rule
and the 1,500-line threshold are replaced by the package layout.

## 2. Configuration

`config.json` at the repository root, optional and gitignored.
`config.example.json` is committed. Precedence: command-line flag, then
`config.json`, then built-in default.

| Key | Default | Meaning |
|---|---|---|
| `port` | `8787` | HTTP port |
| `listen` | `"127.0.0.1"` | Bind address; `--lan` sets `"0.0.0.0"` |
| `data_dir` | `"data"` | Folder for `news.db`, status file and log; relative paths resolve against the repository root |
| `read_langs` | `["es", "en"]` | Languages shown in the reader by default |
| `refresh_minutes` | `15` | Fetch interval |
| `keep_awake` | `false` | Ask the OS not to sleep while the server runs (Windows only) |
| `log_file` | `null` | Path for stdout and stderr when there is no console |

An unknown key or a wrong type stops startup with a message naming the key. A
missing file is not an error.

## 3. Text standard

One function, `text.normalize(value, limit=None)`, is the only path by which
feed text reaches the database. It replaces `clean_text` and applies, in order:

1. Remove `<script>` and `<style>` blocks and HTML tags; unescape HTML
   entities (twice, as today).
2. Unicode NFC. Not NFKC: compatibility folding destroys information in the
   original. A folded key for matching belongs to spec 2.
3. Remove invisible characters: BOM `U+FEFF`, soft hyphen `U+00AD`, zero-width
   space `U+200B`, word joiner `U+2060`, directional marks `U+200E` `U+200F`,
   directional embeddings, overrides and isolates `U+202A`-`U+202E` and
   `U+2066`-`U+2069`, and C0/C1 control characters. Line and paragraph
   separators `U+2028` `U+2029` become a space.
4. Keep `U+200C` (zero-width non-joiner) and `U+200D` (zero-width joiner). They
   change how words are written in Persian and Indic scripts and inside emoji
   sequences.
5. Collapse every run of Unicode whitespace to one space and trim.
6. Truncate to `limit` on a word boundary where the text has spaces; otherwise
   cut at the limit without splitting a combining sequence.

The original text is never replaced by a translation. Translations will live in
their own table in spec 2.

### Charset decoding

`text.decode_bytes(data, http_charset=None)` tries, in order: a byte-order
mark, the XML declaration, the HTTP `Content-Type` charset, UTF-8. `http_get`
and `curl_get` pass the header charset through.

The cp1252 and latin-1 fallback is kept only for sources whose `lang` is
written in Latin script. For any other source, undecodable bytes make the fetch
fail with the error `undecodable feed (no usable charset)` instead of storing
garbage. The failure shows in the Sources view like any other.

### Language per item

Each item stores `lang`, an ISO 639-1 code. It comes from the source's `lang`.
A feed-level or entry-level `xml:lang` is ignored in this spec: outlets set it
unreliably, and one source is one language in `sources.json`. An outlet that
publishes in two languages is entered as two sources.

`geo/languages.json` is the closed reference: code to `{name, script, rtl}`.
`validate_sources` rejects a `lang` that is not in it.

## 4. Archive

- `Store.prune` and its call are removed. Nothing deletes items.
- `KEEP_HOURS` is renamed `LIVE_WINDOW_HOURS` and only bounds the live feed
  (`/api/items`, `/api/geo`).
- New table `sources`: a snapshot of every source ever seen, upserted each time
  `sources.json` is loaded. Columns: `id`, `name`, `region`, `country_iso`,
  `scope`, `exile`, `covers`, `lang`, `kind`, `state_media`, `paywall`,
  `first_seen`, `last_seen`, `active`. A source removed from `sources.json`
  keeps its row with `active = 0`. Archive queries read attribution from this
  table, never from the file.
- `items` gains `lang`, nullable. The migration only adds the column. The
  backfill is part of the snapshot upsert: after `sources.json` is loaded, items
  with a null `lang` take it from their source. This runs on every load, so it
  also covers items of sources whose language is corrected later only while
  their `lang` is still null. New items are written with `lang` set.
- `/api/items` returns `"truncated": true` when the row limit cuts the result,
  and the reader shows a notice instead of silently dropping the oldest rows.

### Migrations

Numbered after the existing migration 1:

2. Create `sources`; add `items.lang`; create index `items(source_id, first_seen)`.
3. Renormalize `title` and `summary` of existing `items` and `saved` rows with
   `text.normalize`. Item ids do not change: they derive from the link.
4. Create the FTS index and its triggers, and populate it (skipped when FTS5 is
   unavailable).
5. Create `fetch_runs`.

Each migration runs inside one transaction, so a failure leaves the previous
version intact.

## 5. Archive search

- `items_fts`: an FTS5 external-content table over `items(title, summary)`,
  tokenizer `unicode61 remove_diacritics 2`, kept in sync by insert, update and
  delete triggers.
- Availability is probed once at startup. Without FTS5 the endpoint falls back
  to `LIKE` on title and summary, and reports `"engine": "like"`.
- `GET /api/search` parameters: `q`, `from`, `to` (UTC epoch seconds),
  `region`, `country` (ISO alpha-2, matched against the effective country:
  `covers` for exile outlets), `lang`, `kind`, `source`, `limit` (default 50,
  maximum 200), `offset`. At least one of `q`, `from`, `source` is required.
- Response: `{items, total, engine, limit, offset}`. Each item carries the same
  fields as `/api/items` plus `lang`, source name and country taken from the
  `sources` table. Order: newest first; with `q`, FTS rank first, then time.
- User input is passed to FTS as quoted terms joined with AND, so query syntax
  characters cannot cause an error or change the meaning of the query.
- Reader: the time selector gains "Archive". In that mode the search box
  queries the server, date-from and date-to inputs appear, and results page
  with "Load more". The region, country and kind selections apply as
  parameters. Saving an item works as in the live feed.

Known limitation: `unicode61` does not segment Chinese, Japanese or Korean, so
searching those languages matches only whole runs of characters. Cross-language
search arrives with the translated text in spec 2.

## 6. Language filter in the reader

- `/api/status` includes `read_langs`.
- The live feed hides items whose `lang` is not in `read_langs`. A checkbox
  "Show other languages (N)" reveals them; N is the hidden count under the
  current filters. The choice persists in `localStorage`.
- Titles and summaries render with `dir="auto"`, so right-to-left scripts
  display correctly. Item rows show the language code, as they do today.
- The map and spine counts are unchanged: they measure coverage, including
  languages the user cannot read yet.

## 7. Fetch runs, gaps and volume

- `fetch_runs(id, started, finished, sources_checked, new_items, errors)`, one
  row per fetch cycle.
- `stats.gaps(runs, interval, since)`: a gap is the time between the start of
  consecutive runs when it exceeds twice the configured interval. Returns the
  gap list, the longest gap and the uptime share. Pure function.
- `stats.volume(rows)`: new items per UTC day and language.
- `GET /api/stats?days=30` returns `{uptime_7d, uptime_30d, gaps, longest_gap,
  volume}`, where `volume` is a list of `{day, lang, items, chars}`. `chars` is
  the summed length of title and summary: the input size a translation engine
  would process.
- Sources view: a "Collection" block with uptime for 7 and 30 days, the number
  of gaps and the longest one, and a table of items per day by language for the
  last 14 days, separating read languages from the rest.

## 8. Windows host

- `tools/windows/install.ps1`, run by the user from an elevated PowerShell:
  - Registers a Task Scheduler task `NewsCentral` that starts
    `pythonw server.py` at logon, hidden, with the repository as working
    directory, restarting on failure (three attempts, one minute apart).
  - Writes `config.json` if it does not exist, with `keep_awake: true` and
    `log_file: "data/server.log"`.
  - Saves the current AC power settings to `data/power-backup.json`, then sets,
    for AC power only: lid close action to "do nothing", standby timeout 0,
    hibernate timeout 0. Battery settings are not touched.
  - Prints what it changed.
- `tools/windows/uninstall.ps1` removes the task and restores the saved power
  settings.
- `keep_awake`: on Windows the server calls `SetThreadExecutionState` with
  `ES_CONTINUOUS | ES_SYSTEM_REQUIRED` through `ctypes` at startup and clears
  it on exit. On other systems the option is ignored with one log line.
- `log_file`: stdout and stderr are redirected to the file, which is rotated at
  startup when it exceeds 5 MB (one previous file kept).
- The second-copy check (port busy) already exists, so a manual start while the
  task is running exits cleanly.

Nothing in this section changes behaviour on macOS or Linux.

## 9. Source batches

Data work that runs alongside the code, one batch per commit.

Process for each batch:

1. Subagents research candidates for the batch theme.
2. Every candidate feed is fetched from this machine. Sources with no working
   feed are left out, or added as `disabled` with a `note` when the outlet
   matters and may be retried later.
3. The user gets a table: outlet, country, language, kind, feed status, item
   count, `state_media`, `paywall`. Nothing is added before approval.
4. Approved sources go into `sources.json`; `--check-sources` must report 0
   problems.

Metadata stays factual: ownership type, paywall, language, country. No bias
scores.

First batches:

- **1a. Economics and EU/Spain primary sources.** Economic press, central
  banks, the European Commission, Parliament and Council, IMF, OECD, Spanish
  institutions. Mostly Spanish and English. A new `kind` value `official` is
  added for institutional publishers, so they can be filtered apart from news
  and analysis; the reader's kind selector gains "Official only".
- **1b. Press in original language.** One to three outlets per key country in
  the languages listed under Context. Depends on sections 3 and 6 being
  merged first, so the text is stored correctly and the reader stays readable.

## 10. Testing

Standard-library `unittest`, no network.

- `text`: one sample per script (Latin with combining marks, Arabic, Persian
  with ZWNJ, Devanagari with ZWJ, Chinese, Japanese, Korean, Cyrillic),
  invisible-character removal, truncation without splitting a combining
  sequence, idempotence (`normalize(normalize(x)) == normalize(x)`).
- Charset: windows-1251, GBK, Shift_JIS and windows-1256 fixtures declared only
  through the header; undecodable non-Latin input fails instead of storing
  mojibake.
- Feeds: RSS, Atom and broken-XML fixtures through `parse_feed`.
- Store and migrations: a version 1 database with rows upgrades to the latest
  version; `sources` snapshot keeps removed sources; no path deletes items.
- Search: FTS and `LIKE` engines return the same items for plain queries;
  query-syntax characters in `q` do not raise; filters and paging.
- Stats: gap detection at the boundary (exactly twice the interval is not a
  gap), uptime share, volume by day and language.
- API: the handler on an ephemeral port against a temporary database, covering
  every GET endpoint and the `X-NewsCentral` requirement on POST.
- Config: precedence, unknown key, wrong type.
- Frontend: loaded in a real browser after each UI change, with the console
  checked. Headless Edge driven over the DevTools protocol works on this
  machine.
- After the fetcher changes: `py server.py --check` on this machine.

## Risks and open points

- **Lid closed on the MSI (assumed, not verified).** Many laptops of this class
  cool through the keyboard deck. The server load is light, so it should not
  matter, but temperatures have not been measured on this model.
- **Reboots after Windows Update (to verify).** The task starts at logon, so an
  unattended reboot stops collection until the user signs in. Starting without
  a session is possible with Task Scheduler but untested here.
- **Single copy.** The whole archive is one file on one laptop. Backups are out
  of scope for this spec.
- **FTS5 on the Pi (hypothesis).** Raspberry Pi OS Python is expected to ship
  SQLite with FTS5. The `LIKE` fallback covers the case where it does not.
- **Database growth (estimate).** About 0.6 KB per item today (3.2 MB for 5,595
  items). At 10,000 items per day that is roughly 2 GB per year before the FTS
  index. Acceptable on
  the laptop and on an SSD-backed Pi; to be re-measured after the first
  batches.

## Build order

1. Package split and API test harness.
2. Config file.
3. Text standard, charset decoding, `languages.json`, `items.lang`, `sources`
   snapshot, archive without prune.
4. Fetch runs, stats endpoint, Collection block.
5. Archive search and the reader's Archive mode.
6. Language filter.
7. Windows host scripts and `keep_awake`.
8. `CLAUDE.md` and `ROADMAP.md` updates.

Source batch 1a can start after step 1. Batch 1b starts after steps 3 and 6.
