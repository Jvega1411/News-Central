# Translation (spec 2a): design

Date: 2026-11-11. Status: approved in brainstorming, pending written review.

## Context

Spec 1 (collection foundation, merged) made the archive permanent, stores the
language of every item, and hides languages the user does not read. Batch 1b
adds press in its original language (French, Portuguese, Italian, German,
Catalan, Arabic, Persian, Turkish, Chinese, Japanese, Korean, Russian,
Ukrainian, Hindi, Urdu, Bengali, Indonesian). The user reads Spanish and
English.

Spec 2 (enrichment) is split into three specs, built in this order:

1. **2a. Translation** (this document): a batch-job framework and headline
   translation for reading.
2. **2b. Topics**: multilingual embeddings, the topic classifier over the BP
   motion areas, and the labelling view (decisions already recorded in
   `ROADMAP.md`, "Topic filter").
3. **2c. Stories**: clustering the same story across outlets and languages,
   coverage by region and blind spots.

Multilingual embedding models place a headline and its translation close to
each other, so topic classification and clustering (2b, 2c) work on the
original text and do not depend on translation. Translation exists for the
user to read.

Decisions taken in brainstorming:

- Target language: Spanish.
- Titles and summaries are always translated, in batch.
- Display: the translation on top, the original smaller underneath, and a
  "translated from <lang>" mark.
- No pay-per-token service. Engines run locally.
- The engine is chosen by a benchmark on real items. Claude scores the
  translations in one Claude Code session on the user's subscription; the user
  reviews 20 to 30 of the scores to check the judgement.
- Jobs run in a separate worker process that the server starts and supervises
  (option A).

## Goals

- Every item in a language outside `read_langs` gets a Spanish title and
  summary without user action.
- The reader, the archive search and the saved view show the translation with
  the original.
- Translation never blocks collection or the reader, and a crash in the model
  never stops the server.
- The engine can be replaced, and the archive retranslated, without schema
  changes.
- The framework runs the jobs of 2b and 2c later.

## Non-goals

Topic classification, embeddings, clustering, the daily briefing,
translating full article text, translating English items, and translating
into more than one target language. Choosing the engine is part of this spec;
building a translation model is not.

## 1. Worker process and job framework

- `newscentral/worker.py`, started with `python -m newscentral.worker`. It
  runs a loop: pick the next job with pending work, run one batch, write the
  results, repeat; sleep 30 seconds when no job has work.
- A job is a class with `name`, `available()` (dependencies and model present),
  `pending(store, limit)` (the next batch of work) and `run(batch)` (returns
  results). The first job is `translate`. 2b and 2c add their own jobs.
- Supervision: `cli.main` starts the worker as a child process with
  `subprocess.Popen([sys.executable, "-m", "newscentral.worker", "--config", <path>])`
  after the server has bound its port. A supervisor thread restarts it after a
  crash, with a delay that doubles from 10 seconds to a maximum of 10 minutes,
  and resets after the worker has run for 10 minutes. On server shutdown the
  worker is terminated. `--no-worker` disables it; `config.json` key
  `worker` (default `true`) does the same.
- Status: the worker writes a heartbeat row (`worker_status`: job, state,
  last batch time, items done in the last hour, last error) every batch.
  `/api/status` and the Sources view show it.
- Resource limits: batch size and the number of CPU threads the engine may use
  come from config (`translate_batch`, default 16; `worker_threads`, default
  half the CPU cores, minimum 1), so the reader and the fetcher stay
  responsive.

### SQLite with two processes

- The database switches to WAL mode (`PRAGMA journal_mode=WAL`, persistent in
  the file) so readers never block the writer and the worker can write while
  the server reads. Both processes set `PRAGMA busy_timeout=5000`.
- Migrations run only in the server process. The worker opens the database
  after the server has started it and refuses to run if `user_version` is not
  the version it expects.
- The worker holds no long transaction: it reads a batch, translates outside
  any transaction, then writes the results in one short transaction.
- WAL creates `news.db-wal` and `news.db-shm` next to the database. They live
  in `data/`, which is gitignored. Copying the database for a backup must copy
  all three files, or use SQLite's backup API; `CLAUDE.md` gets that note.

## 2. Engines

- `newscentral/translate/` package: `base.py` (the interface), one module per
  engine, `registry.py` (engine names to classes).
- Interface: `Engine.translate(texts, src, tgt) -> list of str`, plus `name`,
  `version` and `supports(src, tgt)`. Engines receive normalized text and
  return normalized text (`text.normalize`).
- Candidates for the benchmark:
  - **NLLB-200 distilled 600M** on CTranslate2 (int8). One model for every
    language. Licence CC-BY-NC 4.0: acceptable for this personal,
    non-commercial use; weights are downloaded, never committed.
  - **M2M100 418M** on CTranslate2 (int8). MIT licence.
  - **Opus-MT** (Marian) on CTranslate2: a direct model per pair into Spanish
    where one exists, otherwise source to English then English to Spanish.
    CC-BY 4.0.
- Dependencies: CTranslate2 and sentencepiece, optional, listed in
  `requirements-translate.txt`. Binary wheels exist for Python 3.14 on Windows
  (checked 2026-11-11). ARM64 Linux wheels for the Pi are assumed and must be
  verified before the move. Without the dependencies the job reports
  "unavailable" and nothing else changes.
- Models: `tools/get_models.py <engine>` downloads and converts a model into
  `data/models/<engine>/`. Models never enter the repository.
- Config: `translate_engine` maps source languages to engines, for example
  `{"default": "nllb-600m", "zh": "opus-mt"}`. The benchmark decides the
  values.

## 3. Data

Migration 6:

```
translations(item_id, field, lang, text, engine, created,
             PRIMARY KEY (item_id, field, lang))
translation_failures(item_id, field, lang, attempts, last_error, last_try,
                     PRIMARY KEY (item_id, field, lang))
worker_status(job PRIMARY KEY, state, last_batch, done_last_hour, last_error, updated)
```

- `field` is `title` or `summary`; `lang` is the target language (`es`).
- The original in `items` is never changed.
- Retranslating: deleting the rows of one engine puts those items back in the
  queue. `tools/retranslate.py --engine <name> [--lang <src>]` does that.
- Search: a second FTS5 table `translations_fts` over `translations.text`,
  managed by `Store.ensure_fts()` like `items_fts`. `/api/search` matches
  either the original or the translation; results are not duplicated.

## 4. Queue and priority

- Pending work: items whose `lang` is not in `read_langs` and that lack a
  translation of a field for the target language, excluding items with three
  or more failures for that field.
- Order: all pending titles, newest first; then summaries, newest first. When
  the machine cannot keep up, the backlog grows in summaries, never in titles.
- An item that fails is recorded in `translation_failures`; after three
  failures it is skipped until `tools/retranslate.py` clears it.
- Volume and backlog: the Collection block in the Sources view gains a line
  with pending titles, pending summaries and translations done in the last
  24 hours.

## 5. Benchmark

- `tools/bench_translate.py` samples about 200 archived items: up to 15 per
  source language present in the archive, newest first, titles and summaries.
- It runs every installed engine on the sample, records the seconds per item
  and per field on the current machine, and writes
  `data/bench/translate-<date>.json` with the originals, every translation and
  the timings.
- Judging: in one Claude Code session, Claude reads that file and scores each
  translation for adequacy (meaning preserved) and fluency (natural Spanish)
  from 1 to 5, writing `data/bench/translate-<date>-scores.json`. The user
  reviews 20 to 30 scored items in a plain page served at
  `/bench/translate`, which shows original, translations and scores side by
  side, and marks the scores they disagree with.
- The decision is one engine per source language, picked by mean adequacy
  first and speed second, and written into `config.json`.
- The benchmark runs after batch 1b has collected for at least three days, on
  this machine, and again on the Pi before the move.

## 6. Reader

- `/api/items`, `/api/search` and `/api/saved` add `tr: {title, summary,
  engine}` to items that have a translation.
- Feed, archive and saved views: when `tr` exists, the Spanish title is the
  main title, the original title follows in a smaller grey line with
  `dir="auto"` and `lang=<code>`, and the language tag reads "traducido de
  <code>". The summary shows the translation; the original summary opens on
  click.
- The language filter from spec 1 changes meaning: hidden by default are
  items in unread languages that have no translation yet. Translated items
  appear in the feed.
- Client-side search in the live feed matches the original and the
  translation.
- The Markdown export for Claude includes both the original and the
  translation, marked.

## 7. Error handling

- Engine missing or model not downloaded: the job is unavailable; the worker
  reports it in `worker_status`; the reader shows originals.
- Worker crash: the supervisor restarts it with backoff; the last error is in
  `worker_status` and in the log.
- Database locked beyond the busy timeout: the batch is retried on the next
  loop; no partial writes, because results are written in one transaction.
- Output that is empty, or longer than five times the input: counted as a
  failure for that item.
- Version mismatch between worker and database: the worker exits with a clear
  message and the supervisor stops restarting it.

## 8. Testing

Standard-library `unittest`; no model in the tests.

- A fake engine (reverses words, or tags them) drives the job framework, the
  queue order, the failure counting, the API fields and the reader.
- Queue: titles before summaries; newest first; read languages excluded;
  three failures stop retries.
- Worker: the loop exits cleanly on a signal; refuses a newer or older
  `user_version`; writes results in one transaction.
- Supervision: a worker that exits at once is restarted with growing delays;
  `--no-worker` starts none.
- WAL: two connections, one writing in a loop and one reading, never raise
  "database is locked" within the busy timeout.
- Search: a Spanish query finds an item whose original is Arabic through its
  translation, once.
- Frontend: loaded in headless Edge after each UI change, console checked.
- Engines: exercised only by the benchmark tool, on this machine.

## Risks and open points

- **The Pi keeping up with titles and summaries (hypothesis).** Unmeasured.
  The benchmark measures seconds per item on the Pi. If it cannot keep up, the
  backlog grows in summaries; the fallback is to translate summaries on demand.
- **ARM64 wheels for CTranslate2 (assumed).** Verify on the Pi before the move.
- **Volume after batch 1b (estimate).** Perhaps 3,000 to 5,000 translated
  items per day. Measured by the Collection block before the benchmark.
- **Quality in distant pairs (unknown).** Chinese, Japanese and Korean into
  Spanish may be weak in every candidate; the per-language engine choice and
  pivoting through English exist for that.
- **NLLB licence.** Non-commercial. Fine for this use; would block any
  commercial use of the project.
- **Claude as judge.** Its scores can share the biases of a strong model; the
  user's review of a sample is the check.

## Build order

1. WAL mode, busy timeout, migration 6, `worker_status`.
2. Job framework and worker loop with a fake engine.
3. Supervision from `cli.main`, `--no-worker`, status in the Sources view.
4. Translation queue, failure handling, `translations_fts`.
5. API fields and the reader display.
6. Engines, `requirements-translate.txt`, `tools/get_models.py`.
7. Benchmark tool and the `/bench/translate` review page.
8. Run the benchmark (after three days of batch 1b), judge, choose, configure.
9. `CLAUDE.md` and `ROADMAP.md` updates.
