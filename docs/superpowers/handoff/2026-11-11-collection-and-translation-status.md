# Collection foundation and translation: status / handoff

Date: 2026-11-11. Branch: `main` (everything below is merged). Supersedes the
press-geography handoff of 2026-11-09, whose work is merged and complete.

## What was built today

- **Press geography map finished**: RSF 2026 press-freedom layer, map zoom and
  pan (`static/js/mapzoom.js`), spine and country-filter fixes.
- **Spec 1, collection foundation** (`docs/superpowers/specs/2026-11-11-collection-foundation-design.md`,
  plan in three parts under `docs/superpowers/plans/2026-11-11-collection-foundation*.md`):
  `newscentral/` package, `config.json`, text normalization, language
  reference, charset-aware decoding, permanent archive with `sources` snapshot,
  `fetch_runs` and `/api/stats`, FTS5 archive search, Archive mode, language
  filter, `official` kind, log file and keep-awake, Windows install scripts,
  `tools/check_candidates.py`, feed category tags stored per item.
- **Fixes after review and audit**: daily incremental backup to a synced folder
  (`newscentral/backup.py`, `backup_dir`, `--backup-now`,
  `tools/restore_backup.py`); the live feed loads only the selected window
  (72 h maximum); Python 3.13+ strict X.509 check disabled (chain and hostname
  still verified).
- **Spec 2a, translation** (`docs/superpowers/specs/2026-11-11-translation-design.md`,
  plan in `docs/superpowers/plans/2026-11-11-translation*.md`): WAL mode,
  worker process supervised by the server (`--no-worker` to disable; exits if
  the server dies), translation queue and job, CTranslate2 engines (NLLB,
  M2M100, Opus-MT) imported lazily, translations in the reader and in search,
  `tools/get_models.py`, `tools/bench_translate.py`, `/bench/translate`.
- **Sources**: 185 to 323 active (batch 1a: 42 economics and official sources;
  batch 1b: 138 original-language outlets), 13 more added disabled (see below).
  15 countries added to `geo/countries.json`.
- Tests: 287, all passing (`py -m unittest discover -s tests`). Database at
  schema version 6.

## Decisions recorded today (also in CLAUDE.md / ROADMAP.md)

- Interim host: the Windows MSI laptop, until the Raspberry Pi.
- Reports: deterministic daily briefing in the server; debate reports by Claude
  over MCP.
- Translation: local engines only, into Spanish, titles and summaries always,
  translation shown above the original.
- Topic filter (spec 2b): closed taxonomy of BP motion areas, up to 2-3 labels
  per item, every label with method and confidence, ~300 items hand-labelled by
  the user in a labelling view to measure precision.
- `state_media` only for outlets run by a government, a state news agency or a
  ruling party; public broadcasters with statutory independence are not marked.
- Backup: incremental, compressed, to `C:\Users\sebas\OneDrive\NewsCentral-backup`
  (about 1.6 MB a day).

## Pending, in order

1. **User, at home**: `git push origin main` if the agent could not push;
   run `tools\windows\install.ps1` from an elevated PowerShell (autostart,
   AC power, `config.json` with `keep_awake`, `log_file` and `backup_dir`);
   check `data\server.log`, `powercfg /requests`, lid closed 30 min with no gap.
2. **Recheck the 13 sources disabled because the TVITEC firewall blocks them**
   (IRNA, ISNA, Yonhap, JoongAng, Toyo Keizai, RTHK x3, RIA Novosti,
   Kommersant, RBC, Detik, Antara). Their `note` says so; several feed URLs are
   unverified. Run from the home network and enable those that work. Also run
   `py server.py --check`: the 9 active sources that fail at TVITEC (TASS,
   CGTN, Global Times, Sixth Tone, Yonhap EN, Korea Herald, Tehran Times,
   ANTARA EN, VnExpress) should work at home.
3. **Install translation (needs the user's OK)**:
   `py -m pip install -r requirements-translate.txt`, then
   `py tools/get_models.py nllb-600m` (about 0.6 GB), then
   `py -m newscentral.translate.selfcheck nllb-600m`. M2M100 (about 1 GB) and
   Opus-MT (2-3 GB) only for the benchmark.
4. **Benchmark (plan Task 16)** after batch 1b has collected 3 days:
   `py tools/bench_translate.py run`, Claude scores in one session, the user
   reviews 20-30 at `/bench/translate`, choose an engine per language in
   `config.json`.
5. **Spec 2b (topics)**, then **2c (stories)**, then spec 3 (daily briefing).

## Known issues

- On the TVITEC network a firewall intercepts TLS to some outlets in Russia,
  China, South Korea, Iran, Indonesia and Vietnam (certificate for
  `*.tvitec.com`). Do not disable sources for failing there.
- `tests.test_wal.TestTwoConnections` failed once with "database is locked"
  under full-suite load; it passed in about 30 later runs.
- `/api/stats` counts the translation backlog over the whole archive under the
  store lock; it will slow down as the archive grows (cache it).
- No alert yet for sources failing for several days (ROADMAP Phase 2).
- Volume after batch 1b: about 8,000 to 10,000 items a day (first day inflated
  by backlogs). Infobae alone publishes about 360 a day, so raw counts measure
  feed volume, not importance; spec 2c must weight by outlet.

## Process notes

- Token economy: implementers on Haiku or Sonnet, the orchestrator reviews
  small diffs, one reviewer for the whole branch at the end, few browser runs
  (see the user's memory notes).
- Headless Edge driven over the DevTools protocol works on this machine for
  browser checks (`msedge.exe --headless=new --remote-debugging-port=...`).
- Earlier sessions could not push (read-only deploy key, no `gh`).
