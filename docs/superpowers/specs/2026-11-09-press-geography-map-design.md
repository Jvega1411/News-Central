# Press geography map — design

Date: 2026-11-09
Roadmap: Phase 1, item "Map of where the press is from" + "Press-freedom layer".
Status: approved design, pending implementation plan.

## Goal

Give News Central a geographic spine: browse and filter the archive by the
country and region a source is *from*, with an interactive world map as the main
view. This is the core navigation surface, not a decoration. It also forces the
source metadata into a clean, factual, ISO-based model that later phases
(clustering, tagging, the connector) can rely on.

Scope note: this spec covers the geography feature only. Other Phase 1 items
(OPML export, config file, full archive + FTS, topic rules, per-source refresh
interval) are separate sub-projects with their own specs. The numbered-migration
mechanism is introduced here because this feature needs a schema change and the
rest of Phase 1 depends on having it.

## Non-goals (YAGNI)

- Map zoom / pan.
- Clustering or exploding tiny countries. The spine list reaches micro-states.
- Animated transitions beyond hover feedback.
- "Where stories happen" (gazetteer of place names in article text). That is a
  separate Phase 2 item; this feature is strictly "where the *source* is from".
- Any bias or quality score for outlets. Metadata stays factual.

## Interface

Two coordinated surfaces plus the existing reader:

- **Spine (sidebar):** collapsible `Region -> Country -> source` tree with counts
  at each level. Click any level to filter the feed. A separate "Transnational"
  group at the bottom holds the non-country buckets.
- **Map (main viewer):** dark choropleth of the world. Hover for detail, click a
  country to filter the feed and expand that country in the spine. A small tray
  beside the map holds the non-country buckets as chips. A layer toggle switches
  the coloring between item counts and press-freedom.
- **Reader:** the existing article list, filtered by the current selection. Keeps
  its current ~980px column; the map/spine view is wider.

The map and spine always agree because they consume the same aggregated payload.

## Data model

### `sources.json` per-source fields

Replace the current free-text `country` with:

- `country_iso` — ISO-3166-1 alpha-2, uppercase (`ES`, `FR`, `GB`). The factual
  home / registration country of the outlet. `null` when the outlet genuinely
  has no single home country.
- `scope` — present only when `country_iso` is `null`. Closed vocabulary, exactly
  one of:
  - `Global` — genuinely global remit, no home country (Project Syndicate, The
    Conversation Global).
  - `Intergovernmental` — intergovernmental bodies only (UN News).
  - `Transnational-regional` — covers a bloc, not a country (Pan-African,
    pan-Arab outlets).
  Adding a new `scope` value requires a written justification in the PR. There is
  no "miscellaneous" / "other" value, by design.
- `exile` — `true` for outlets that operate from outside the country they cover
  (Iran International, Meduza). Absent/false otherwise.
- `covers` — ISO alpha-2 of the country an exile outlet reports on. Present only
  when `exile` is true. Example: Meduza `country_iso: "LV"`, `covers: "RU"`,
  `exile: true`.
- `region` — unchanged. The existing editorial region grouping used by the spine
  and the current region chips.

Factual rule (from CLAUDE.md): `country_iso` is where the outlet actually is.
Exile outlets keep their factual host in `country_iso`; the map/spine group them
under `covers` for usefulness, and the source-detail row shows the real host.

### Country reference

A single in-repo reference file (committed, not runtime data), e.g.
`geo/countries.json`, maps `ISO alpha-2 -> { name, region }`. This is the one
source of truth for display names and region assignment, so the spine, the map
tray, and any future feature read the same mapping. The `region` here must match
the region vocabulary already used in `sources.json`.

### Migration of existing data

The 164 existing sources carry free-text `country` values that mix real
countries, sub-regions (`Balkans`, `Caucasus & Central Asia`), special buckets
(`Global`, `UN`, `Pan-African`) and modifiers (`Iran (exile)`). A one-time
cleanup pass rewrites every source to the new fields using the country reference
as input. This is mechanical and suitable for a low-capability model, but every
`scope` assignment and every `exile`/`covers` pair must be checked by hand
against the closed vocabulary. The pass must end with: zero free-text `country`
values remain, every source has either a valid `country_iso` or a valid `scope`,
and every `exile` source has a `covers`.

## Backend

### New endpoint

`GET /api/geo?since=<epoch>` returns the aggregation the map and spine both use:

- per `country_iso`: item count in the window, source count;
- per `scope` bucket: item count, source count;
- exile outlets counted under their `covers` country, not their host.

`since` reuses the existing time-window semantics. No auth header (it is a GET,
like the other read endpoints). The payload is computed by joining `items` to
each source's `country_iso` (or `covers` for exile) via the in-memory source map
the `Fetcher` already maintains; it does not require storing country on each
item row.

### Schema / migrations

This feature introduces the **numbered schema migration** mechanism (also needed
by the rest of Phase 1). Design:

- A `schema_version` row (or a dedicated one-row table) in SQLite.
- An ordered list of migration functions in `server.py`, each `(n, fn)`, applied
  in order on startup when `schema_version < n`, inside a transaction, bumping
  the version on success.
- Introduced as its own clearly-marked section in `server.py`.

Whether this feature itself needs a new column is an implementation detail: the
`/api/geo` aggregation can be done from the source map without persisting country
on items. The migration mechanism is introduced regardless, as the vehicle for
this and later schema changes. If the aggregation turns out to need an index or a
denormalized column for performance at archive scale, that goes through a
migration.

## Frontend structure

Move the inline `<script>` in `static/index.html` to local ES modules loaded via
`<script type="module">`. No build step, no bundler, no dependency — the server
keeps serving static files, works offline, copies to the Pi unchanged.

Proposed modules (final boundaries decided in the plan):

- `static/js/api.js` — fetch wrappers for the HTTP API (`/api/items`, `/api/geo`,
  `/api/sources`, POSTs with the `X-NewsCentral` header).
- `static/js/map.js` — the SVG choropleth: load the map asset, color by payload,
  hover/click, layer toggle, non-country tray.
- `static/js/spine.js` — the `Region -> Country -> source` tree.
- `static/js/reader.js` — the existing article list / saved / sources views.
- A small entry module wiring shared state (current selection, time window)
  between map, spine and reader.

`server.py` stays a single file for now; split into a package only if it crosses
~1,500 lines.

### Map rendering

- Inline SVG choropleth. Each country is a `<path>` whose `id` is its ISO alpha-2
  code. JS sets `fill` from the `/api/geo` payload.
- Source SVG: a CC0 / public-domain world map, Natural Earth or Robinson
  projection (better than the flat equirectangular). **Must be license-vetted
  before committing** — the repo is public. Stored under `static/`.
- No Leaflet, no D3, no tiles, no external requests.
- Countries with no data use the no-data land color. Hover shows a tooltip;
  click sets the global selection. Non-country buckets are chips in the tray, not
  painted on the map.

## Aesthetic rules

Codified so they are rules, not a vibe. To be added to CLAUDE.md.

### The map surface (dark control-room)

Always dark, even when the app is in light mode — an intentional dark data-viz
panel. Tokens:

- Panel background `#0b0e12`, no-data land `#10151b`, hairline borders `#222a33`.
- Sequential count heat ramp (few -> many):
  `#2a2410`, `#6b5312`, `#b98a16`, `#f0b429`, `#ffd968`.
- Active / selected country highlight: cyan `#35d0d6` (stroke, subtle).
- Press-freedom layer: a separate factual ramp sourced from the RSF index (see
  open questions). Attributed to RSF on the layer. Not reused for counts.

### The app (editorial restraint)

- Keeps the existing light/dark auto theme and existing palette for the reader
  chrome.
- Serif display (web-safe Georgia stack, no web-font download) for page/section
  headings and the map title. System sans for article lists and controls, for
  density and legibility. Do not set article titles in serif.
- Numbers are always tabular / monospace (`font-variant-numeric: tabular-nums`),
  as the current CSS already does for counts and timestamps.

### Low-AI-look rules (apply everywhere)

- Color always encodes meaning (counts, press-freedom, freshness). Never
  decoration.
- One accent per surface, used sparingly.
- No generic purple/indigo gradients, no glassmorphism, no decorative blur blobs,
  no emoji in the chrome.

## CLAUDE.md changes (part of implementation)

1. New **"Code structure and guidelines"** preface: file boundaries (single-file
   `server.py` for now, ES-module frontend, no build step), the no-dependency
   rule restated for the frontend, naming and single-responsibility expectations,
   and the numbered-migration requirement.
2. Update the UI constraint from "the whole UI in one file" to "vanilla ES
   modules, no build step".
3. Add the aesthetic rules above.

## Testing

Per CLAUDE.md, cloud sandboxes cannot reach news sites, so:

- `/api/geo` aggregation: unit-test against a local fixture of items + a small
  fixture `sources.json` covering a real country, an exile outlet, and each
  `scope` bucket. Assert exile is counted under `covers`, buckets are separate
  from countries, and the window filter works.
- Migration mechanism: test that a fresh DB and an existing DB both land on the
  latest `schema_version`, idempotently.
- Map / spine: load `index.html` in a browser against fixture data; verify
  hover, click-to-filter, the layer toggle, and that map and spine counts match.
- Data model: a lint/check script that fails if any source has a free-text
  `country`, a missing `country_iso`-or-`scope`, an unknown `scope` value, or an
  `exile` source without `covers`.
- Live source health is unchanged and only run on a real machine (`--check`).

## Open questions / pending verification

- **World SVG asset:** confirmed-needed, source and license not yet chosen. Must
  be CC0/PD, ISO-coded, license-clean for a public repo. Blocks the map visuals
  but not the data model or endpoint.
- **RSF 2026 index:** assume it is published (RSF usually releases in May; today
  is 2026-11). Need to confirm availability and reuse terms, and decide storage
  (a small attributed JSON keyed by ISO under `geo/` or `static/`). The
  press-freedom layer is gated on this; the count map is not.
- **Country reference region vocabulary** must exactly match the regions already
  in `sources.json`; reconcile any mismatch during the cleanup pass.

## Build order (for the plan)

1. Migration mechanism in `server.py` (unblocks schema work).
2. Country reference + `sources.json` cleanup to the new fields + the lint check.
3. `/api/geo` endpoint with unit tests on fixtures.
4. Frontend ES-module split (no behavior change) as a separate, reviewable step.
5. Spine tree against `/api/geo`.
6. SVG choropleth (after the asset is vetted), hover/click wiring, non-country
   tray.
7. Press-freedom layer (after RSF data is sourced).
8. CLAUDE.md updates (structure preface, UI constraint, aesthetic rules).
