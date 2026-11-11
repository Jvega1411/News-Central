# News Central

Single-user, local world-news reader built for preparing CMUDE 2027 (Spanish-language British Parliamentary debate worlds, Barcelona). It pulls RSS/Atom feeds from ~140 outlets across 12 regions, serves a reader in the browser, and lets the user save items with notes and export them as Markdown to digest later with Claude. The user's main knowledge gap is economics, politics and EU/Spain context, with global scope (Asia, Africa, Latin America, culture and ideas).

The plan lives in `ROADMAP.md`. Work phase by phase.

## Run

```
python server.py --open      # Windows: py server.py --open   ·   macOS: ./NewsCentral.command
python server.py --check     # fetch every enabled source once, print OK / FOUND / FAIL, exit
python server.py --lan       # also listen on the local network (phone on the same Wi-Fi)
```

Reader at http://127.0.0.1:8787. Runtime data goes to `data/` (SQLite `news.db` with archive, saved items and notes; `sources_status.json` written after every fetch cycle). `data/` is gitignored and must never be committed: the repository is public.

## Code structure and guidelines

- `server.py` is a single standard-library Python file. Split it into a package only if it grows past ~1,500 lines. Core stays Python 3.8+ standard library; a new dependency needs a reason, must be optional, and must ship ARM64 Linux wheels.
- The frontend is vanilla ES modules under `static/js/` loaded from `static/index.html` via `<script type="module">`. No build step, no bundler, no dependency. Current modules: `state.js` (shared selection state + a tiny pub/sub: `state`, `setState`, `onState`), `api.js` (HTTP API wrappers), `reader.js` (feed, saved, sources views), `spine.js` (Region/Country navigation), `map.js` (choropleth), `main.js` (entry). Each module has one responsibility.
- Do not use `??` or `?.` in frontend code (older Safari support).
- Database schema changes go through the numbered migrations in `server.py` (`@migration(n)` + `run_migrations`, tracked by `PRAGMA user_version`).
- Tests are stdlib `unittest` under `tests/`, run with `py -m unittest discover -s tests` (Windows) / `python -m unittest ...`. There is no pytest.

## Architecture

- `server.py`: everything server-side, Python standard library only.
  - Fetch loop thread: every 15 min (failing sources hourly, forced on manual refresh or when `sources.json` changes on disk). 16 workers. ETag / Last-Modified.
  - Per source: last working feed URL, then configured candidates (`feed` may be a string or a list), then auto-discovery from `site` (`<link rel="alternate">` plus common paths).
  - Blocked sites (401/403/406/429/503 or an HTML bot page): retry with an alternate user agent, then `curl` with both user agents.
  - Parsing: ElementTree with namespace stripping, repair pass (HTML entities, control chars, stray `&`), regex fallback for broken XML. Only http(s) links are kept; tracking params stripped; item id = sha1 of the normalised link.
  - Storage (`Store`): tables `items`, `saved`, `feed_state`. Items are pruned after 96 h (saved items are kept).
  - HTTP API: `GET /api/items?since=`, `/api/sources`, `/api/saved`, `/api/status`, `/api/export.md[?only=undigested][&download=1]`; `POST /api/refresh`, `/api/save`, `/api/unsave`, `/api/saved/update`. POSTs require the header `X-NewsCentral: 1`.
- `sources.json`: one object per line, grouped by region. Fields: `id`, `name`, `region`, `country_iso` (ISO alpha-2, or `null` plus `scope` from the closed vocabulary `Global` / `Intergovernmental` / `Transnational-regional`), `lang`, `kind` (`news` or `analysis`), `feed` (string or list), `site`, optional `exile` + `covers` (exile outlets: factual host in `country_iso`, country reported on in `covers`), `paywall`, `state_media`, `disabled` + `note`. `py server.py --check-sources` validates the model.
- `static/index.html` plus vanilla ES modules in `static/js/` (`state`, `api`, `reader`, `spine`, `map`, `main`): no build step, still no `??` or `?.` for older Safari. Region chips, time window, news/analysis, sports & celebrity filter (URL-section based), state-controlled toggle, search, saved view with notes and "Copy for Claude", sources health table.
- `NewsCentral.command`: macOS double-click launcher.

## Hard constraints

- Core stays standard-library Python 3.8+. A new dependency needs a reason, must be optional, and must ship ARM64 Linux wheels.
- Must run unchanged on macOS Intel (current host), Windows (dev laptop) and Raspberry Pi OS 64-bit (future host). Per-machine differences go through flags or a config file, never code edits.
- One SQLite file is the whole state. Schema changes go through numbered migrations (to be introduced in Phase 1).
- The Claude connector (MCP) talks to the HTTP API, never to `news.db` directly, so it keeps working after the move to the Pi.
- Heavy processing (embeddings, clustering) runs as batch jobs that can run on any machine and store results in the database.
- Times are stored as UTC epoch seconds and converted only in the UI.
- Never invent outlet bias scores. Outlet metadata stays factual: ownership type, paywall, language, country.

## Visual rules

- The map panel uses a light "clear" surface. Tokens: ocean/background `#eaf0f6`, no-data land `#d9dee4`, white country borders, sequential count ramp in blues `#cfe1f2 -> #93c0e0 -> #5699cd -> #2b6cb0 -> #13375e`, active-country highlight dark blue `#0a3d91`.
- The rest of the app keeps the existing light/dark auto theme. Serif display (Georgia stack) for headings and the brand; system sans for article lists and controls; numbers are tabular (`font-variant-numeric: tabular-nums`).
- Color always encodes meaning (counts, press-freedom, freshness), never decoration. One accent per surface, used sparingly. No gradients, glassmorphism, emoji in the chrome, or decorative blur.
- The map is an Equal Earth projection SVG at `static/world.svg` (country `<path id>` = ISO alpha-2), generated from public-domain Natural Earth data by `tools/gen_world_svg.py`.

## Testing

- Cloud sandboxes cannot reach news sites. Test parsing, API and UI there with local fixture feeds (serve XML files with `python -m http.server`) and a headless browser.
- Live source health only on a real machine: `python server.py --check`, or the Sources view.
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
