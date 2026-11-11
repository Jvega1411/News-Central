# Press geography map — implementation status / handoff

Date: 2026-11-09. Branch: `feature/press-geography-map` (16 commits ahead of `main`).

This is a handoff for the next agent instance. It summarizes the plan, what was
built, where it stopped, and the context needed to continue from a common point.

## Source documents

- Spec: `docs/superpowers/specs/2026-11-09-press-geography-map-design.md`
- Plan (13 tasks, TDD): `docs/superpowers/plans/2026-11-09-press-geography-map.md`

Read both before continuing. This feature is Phase 1 "Map of where the press is
from" + "Press-freedom layer" from `ROADMAP.md`.

## How to run / test

```
py server.py --open                 # reader + map at http://127.0.0.1:8787
py server.py --check-sources        # validate the source data model, exit 0 if clean
py -m unittest discover -s tests     # 17 tests, all passing
```

Note: this dev machine HAS live internet (news feeds reachable). The CLAUDE.md
"cloud sandboxes cannot reach news sites" caveat does not apply here; use live
fetches and real browser checks on this machine.

If `py server.py` says the port is busy, a previous instance is still running;
kill the process listening on 8787 and restart (otherwise it serves stale code,
because routes live in the running process even though static files are read per
request).

## Task status

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Schema migration mechanism (`@migration`, `PRAGMA user_version`) | DONE | 1f63ecd, 4399447 |
| 2 | ISO country reference `geo/countries.json` + `load_countries()` | DONE | 025eed1 |
| 3 | `validate_sources()` + `--check-sources` | DONE | 2737930 |
| 4 | Migrate `sources.json` to ISO model | DONE | 285cbaf |
| 5 | `/api/geo` endpoint + ISO fields in `source_payload` | DONE | ef20598, 8ec66c9, c3caf01 |
| 6 | Split frontend into ES modules | DONE | 12e1c69 |
| 7 | Region/Country spine sidebar | DONE | e121f11, 5644b1b, 7155238 |
| 8 | World SVG asset (Equal Earth, Natural Earth) | DONE | ad07536 |
| 9 | Choropleth map (hover, click-filter, legend, tray) | DONE | 96f6099 |
| 10 | Editorial CSS (serif headings, tabular numbers) | DONE | fa208e9 |
| 13 | Update CLAUDE.md | DONE | aa203c6 |
| 11 | RSF 2026 press-freedom data | **NOT STARTED** (gated) | — |
| 12 | Press-freedom map layer toggle | **NOT STARTED** (depends on 11, 9) | — |

## Post-plan UI changes (user-requested, after the plan's 13 tasks)

- **Map recolored to a light "clear" surface** (commit `ac3ef32`). The original design
  was a dark panel with an amber heat ramp; the user asked for a light background
  with blues. Current map tokens (also in CLAUDE.md "Visual rules"): ocean/panel
  `#eaf0f6`, no-data land `#d9dee4`, white country borders, count ramp in blues
  `#cfe1f2 -> #93c0e0 -> #5699cd -> #2b6cb0 -> #13375e`, active-country highlight
  dark blue `#0a3d91`. If you touch map colors, keep this scheme, not the old dark/amber.
  (Note: `map.js` line-2 comment still says "Dark Equal Earth" — stale, harmless.)
- **Map is now a dedicated "Map" tab, not the default view** (commit `eac2908`).
  Default view is the text Feed (spine on the left, no map), which is how the user
  prefers to read. The view switcher is Feed / Map / Saved / Sources. The Map tab
  shows the map on top + the feed below + the spine, with click-to-filter (the
  pre-change behavior). Mechanism: `reader.js` uses `feedVisible = (view==='feed'
  || view==='map')` so the feed renders in both; `map.js` has a `_ready` flag and
  its `onState` subscriber shows `#map` only when `state.view === 'map'` (hidden
  otherwise). Default `state.view` is `'feed'`, so the map starts hidden.

Each done task passed a two-stage subagent review (spec compliance + code
quality) where applicable. Backend is covered by `unittest`.

## What remains (next agent, do these)

1. **Task 11 — RSF press-freedom data.** Source the RSF World Press Freedom
   Index 2026 (confirm it is published; if only 2025 exists, use 2025 and name
   the file accordingly). Store `geo/press-freedom-2026.json` as ISO alpha-2 ->
   numeric score (RSF scale 0-100, higher = freer). Verify reuse terms allow a
   public repo (store only the numeric facts with attribution, not RSF prose).
   Add `load_press_freedom()` (mirror `load_countries()`) and include it in
   `geo_payload`.
2. **Task 12 — press-freedom layer.** Add a toggle in the map that recolors by
   press-freedom using a distinct factual ramp, attributed to RSF. See the plan
   for the suggested `paintPressFreedom` approach. `map.js` already stores the
   geo payload; add `geo.press_freedom` consumption.
3. **Browser verification done for the main UI.** The user has visually confirmed
   in a browser: the ES-module refactor, the spine, the choropleth map (after the
   light/blue recolor), and the Map tab. The press-freedom layer (11-12) is the
   only UI piece not yet built/verified. Still re-check in a browser after any
   further frontend change (reminder: `node --check` does not catch DOM runtime
   errors).
4. **Fix a stale doc line:** `CLAUDE.md` Architecture still lists `sources.json`
   field `country`. It is now `country_iso` (ISO alpha-2) or `country_iso: null`
   + `scope`, plus `exile`/`covers`. Update that bullet.
5. Optional UX nits from review: spine regions default collapsed (consider
   auto-expanding the selected region); country/scope selection is not persisted
   to localStorage (region is). Neither is a bug.
6. When 11-12 land and the browser checks pass, use
   `superpowers:finishing-a-development-branch` to open a PR / merge to `main`.

## Key decisions and context

- **Data model.** Each source has `country_iso` (ISO alpha-2 uppercase) OR
  `country_iso: null` + `scope` from a CLOSED vocabulary
  (`Global`, `Intergovernmental`, `Transnational-regional`). No "miscellaneous"
  bucket by design; adding a scope value requires justification. Exile outlets
  (Iran International -> host GB covers IR; Meduza -> LV covers RU; The Moscow
  Times -> NL covers RU) store the factual host in `country_iso` plus
  `exile: true` and `covers` (the country they report on). Map and spine group
  exile outlets under `covers`; the factual host shows in the source detail.
  `validate_sources` enforces all of this; `py server.py --check-sources` must
  stay at `0 problem(s)`.
- **`/api/geo` shape:** `{countries: {ISO: {items, sources, name, region}},
  buckets: {scope: {items, sources}}, updated}`. Computed by the pure function
  `aggregate_geo(sources, items, countries, since)` (unit-tested). Exile items
  count under `covers`. The map and spine both consume this; keep the shape
  stable.
- **Frontend state.** `static/js/state.js` is a tiny pub/sub (`state`,
  `setState`, `onState`). The spine and the map both call `setState({region,
  country, scope})` on click; `reader.js` filters the feed from state; `reader`
  and `map` subscribe via `onState` to re-render / re-highlight. Subscribers
  that only read state and toggle classes must NEVER call `setState` (loop
  guard). `reader.js` uses an `_ignoreState` flag to avoid its own setState
  bouncing back.
- **The map SVG.** `static/world.svg` is an Equal Earth projection generated by
  `tools/gen_world_svg.py` from public-domain Natural Earth 110m data. Country
  `<path id>` = ISO alpha-2 (uppercase). To regenerate: download
  `ne_110m_admin_0_countries.geojson` from the natural-earth-vector repo and run
  `py tools/gen_world_svg.py <geojson> static/world.svg`. The generator uses
  `ISO_A2_EH` (falls back to `ISO_A2`) so France/Norway are coded correctly.
- **Known map limitation:** the 110m admin-0 map has no separate polygon for
  Hong Kong (it is inside China) or other non-sovereign entities. HK sources
  therefore do not light a country on the map, though the spine still lists HK.
  Taiwan IS present. This is acceptable for v1; a higher-res asset could fix it.
- **Frontend testing lesson:** `node --check` validates ES-module SYNTAX but
  does NOT catch DOM runtime errors. A real bug shipped past review this way
  (`element.dataset['data-spine-region']` is illegal; must be `dataset.spineRegion`
  or `setAttribute`). ALWAYS have a human load the page and watch the console
  after frontend changes.

## Process / environment notes

- Execution was subagent-driven (superpowers:subagent-driven-development):
  Haiku for mechanical tasks, Sonnet for JS/integration, two-stage review per
  task. This matches the user's preference to plan with the strong model and
  implement with cheaper models.
- Git author identity was set repo-locally to the user's name and email (there was no global identity). Verify the name
  is correct.
- **Push is blocked from the agent environment:** the only SSH key available is
  a read-only deploy key, and `gh` is not installed. The branch must be pushed
  with the user's own credentials:
  `git push -u origin feature/press-geography-map`.
