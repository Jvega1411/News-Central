# News Central

Single-user, local world-news reader built for preparing CMUDE 2027 (Spanish-language British Parliamentary debate worlds, Barcelona). It pulls RSS/Atom feeds from ~140 outlets across 12 regions, serves a reader in the browser, and lets the user save items with notes and export them as Markdown to digest later with Claude. The user's main knowledge gap is economics, politics and EU/Spain context, with global scope (Asia, Africa, Latin America, culture and ideas).

The plan lives in `ROADMAP.md`. Work phase by phase.

## Run

```
python server.py --open      # Windows: py server.py --open   ·   macOS: ./NewsCentral.command
python server.py --check     # fetch every enabled source once, print OK / FOUND / FAIL, exit
python server.py --lan       # also listen on the local network (phone on the same Wi-Fi)
python server.py --config other.json   # use another config file (default: config.json, optional)
py tools/check_candidates.py file.json # fetch candidate sources and print a review table
```

Reader at http://127.0.0.1:8787. Runtime data goes to `data/` (SQLite `news.db` with archive, saved items and notes; `sources_status.json` written after every fetch cycle). `data/` is gitignored and must never be committed: the repository is public.

Windows host: `tools/windows/install.ps1` (elevated) registers autostart at logon and sets AC power so the lid can stay closed; `uninstall.ps1` undoes both. `config.json` is per-machine and gitignored; `config.example.json` documents the keys.

## Code structure and guidelines

- `server.py` is only the entry point. The code lives in the `newscentral/` package, one responsibility per module, each depending only on the ones before it: `config`, `text`, `http_client`, `sources`, `feeds`, `store`, `fetcher`, `stats`, `api`, `cli`. Core stays Python 3.8+ standard library; a new dependency needs a reason, must be optional, and must ship ARM64 Linux wheels.
- All feed text enters the database through `text.normalize()` (markup stripped, Unicode NFC, invisible characters removed, joiners kept). The original text is never replaced by a translation.
- The frontend is vanilla ES modules under `static/js/` loaded from `static/index.html` via `<script type="module">`. No build step, no bundler, no dependency. Current modules: `state.js` (shared selection state + a tiny pub/sub: `state`, `setState`, `onState`), `api.js` (HTTP API wrappers), `reader.js` (feed, saved, sources views), `spine.js` (Region/Country navigation), `map.js` (choropleth), `mapzoom.js` (map zoom and pan), `main.js` (entry). Each module has one responsibility.
- Do not use `??` or `?.` in frontend code (older Safari support).
- Database schema changes go through the numbered migrations in `newscentral/store.py` (`@migration(n)` + `run_migrations`, tracked by `PRAGMA user_version`).
- Tests are stdlib `unittest` under `tests/`, run with `py -m unittest discover -s tests` (Windows) / `python -m unittest ...`. There is no pytest.

## Architecture

- `server.py` plus the `newscentral/` package: everything server-side, Python standard library only.
  - Fetch loop thread: every 15 min (failing sources hourly, forced on manual refresh or when `sources.json` changes on disk). 16 workers. ETag / Last-Modified.
  - Per source: last working feed URL, then configured candidates (`feed` may be a string or a list), then auto-discovery from `site` (`<link rel="alternate">` plus common paths).
  - Blocked sites (401/403/406/429/503 or an HTML bot page): retry with an alternate user agent, then `curl` with both user agents.
  - Parsing: ElementTree with namespace stripping, repair pass (HTML entities, control chars, stray `&`), regex fallback for broken XML. Only http(s) links are kept; tracking params stripped; item id = sha1 of the normalised link.
  - Storage (`Store`): tables `items` (with `lang` and `feed_tags`), `saved`, `feed_state`, `sources` (snapshot of every source ever seen), `fetch_runs`. Nothing is pruned: the archive is permanent. The live feed and the map look back 96 h. Each item stores the `<category>` / `dc:subject` tags its feed provides, raw and normalized, as a JSON list in `items.feed_tags` (migration 5). Nothing interprets them yet; they are input for the topic classifier in spec 2.
  - Search: `items_fts` (FTS5) is created at startup when the SQLite build has FTS5; otherwise search uses `LIKE`. Never `VACUUM` the database without rebuilding the index.
  - Decoding: byte-order mark, XML declaration, UTF-8, HTTP charset, and cp1252/latin-1 only for Latin-script languages. An undecodable non-Latin feed fails instead of being stored.
  - HTTP API: `GET /api/items?since=` (returns `truncated` when the row limit cuts the result), `/api/sources`, `/api/saved`, `/api/status` (returns `read_langs`), `/api/search`, `/api/stats`, `/api/export.md[?only=undigested][&download=1]`; `POST /api/refresh`, `/api/save`, `/api/unsave`, `/api/saved/update`. POSTs require the header `X-NewsCentral: 1`.
- `sources.json`: one object per line, grouped by region. Fields: `id`, `name`, `region`, `country_iso` (ISO alpha-2, or `null` plus `scope` from the closed vocabulary `Global` / `Intergovernmental` / `Transnational-regional`), `lang` (must exist in `geo/languages.json`; one source is one language), `kind` (`news`, `analysis` or `official`), `feed` (string or list), `site`, optional `exile` + `covers` (exile outlets: factual host in `country_iso`, country reported on in `covers`), `paywall`, `state_media`, `disabled` + `note`. `py server.py --check-sources` validates the model.
- `static/index.html` plus vanilla ES modules in `static/js/` (`state`, `api`, `reader`, `spine`, `map`, `mapzoom`, `main`): no build step, still no `??` or `?.` for older Safari. Region chips, time window, news/analysis, sports & celebrity filter (URL-section based), state-controlled toggle, search, saved view with notes and "Copy for Claude", sources health table.
- `NewsCentral.command`: macOS double-click launcher.

## Hard constraints

- Core stays standard-library Python 3.8+. A new dependency needs a reason, must be optional, and must ship ARM64 Linux wheels.
- Must run unchanged on macOS Intel (current host), Windows (dev laptop) and Raspberry Pi OS 64-bit (future host). Per-machine differences go through flags or a config file, never code edits.
- One SQLite file is the whole state. Schema changes go through numbered migrations, each in one transaction. The FTS index is derived data and is managed by `Store.ensure_fts()`, not by a migration.
- The Claude connector (MCP) talks to the HTTP API, never to `news.db` directly, so it keeps working after the move to the Pi.
- Heavy processing (embeddings, clustering) runs as batch jobs that can run on any machine and store results in the database.
- Times are stored as UTC epoch seconds and converted only in the UI.
- Never invent outlet bias scores. Outlet metadata stays factual: ownership type, paywall, language, country.

## Visual rules

- The map panel uses a light "clear" surface. Tokens: ocean/background `#eaf0f6`, no-data land `#d9dee4`, white country borders, sequential count ramp in blues `#cfe1f2 -> #93c0e0 -> #5699cd -> #2b6cb0 -> #13375e`, active-country highlight dark blue `#0a3d91`.
- The rest of the app keeps the existing light/dark auto theme. Serif display (Georgia stack) for headings and the brand; system sans for article lists and controls; numbers are tabular (`font-variant-numeric: tabular-nums`).
- Color always encodes meaning (counts, press-freedom, freshness), never decoration. One accent per surface, used sparingly. No gradients, glassmorphism, emoji in the chrome, or decorative blur.
- The map is an Equal Earth projection SVG at `static/world.svg` (country `<path id>` = ISO alpha-2), generated from public-domain Natural Earth data by `tools/gen_world_svg.py`. The map keeps hairline borders at every zoom (`vector-effect: non-scaling-stroke`).

## Testing

- Cloud sandboxes cannot reach news sites. Test parsing, API and UI there with local fixture feeds (serve XML files with `python -m http.server`) and a headless browser.
- Live source health only on a real machine: `python server.py --check`, or the Sources view.
- The HTTP API is tested with `tests/helpers.ApiCase`, which runs the real handler on an ephemeral port against a temporary database.
- After changing the UI, load it in a browser and check it; after changing the fetcher, run `--check`.

## Working with the user

- He writes in Spanish or English; answer in the language of his message.
- Critical, rational analysis. In technical analysis separate confirmed, assumed and hypothetical, and mark hypotheses as pending before building on them. If something is a real weak point, say so plainly.
- When something is ambiguous, ask the smallest question that resolves it. Silence is not approval.
- Do not create files or documentation nobody asked for (no extra READMEs, guides or reports). Superpowers spec and plan files are accepted only for roadmap phases, under `docs/superpowers/`.
- Superpowers: use brainstorming, writing-plans and verification for roadmap phases. For small fixes skip the ceremony (no TDD loop or worktrees unless asked).
- feature-dev: only for the large phases (Claude connector, embeddings), because each run launches many subagents.
- In chat messages: no em dashes, and no closing offers or option menus. End when the content ends.
- Commits: one logical change each, imperative subject. Pushing to `main` is fine after the user confirms.
