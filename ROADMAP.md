# Roadmap

Goal: the best news tool for this specific use (global coverage for CMUDE prep, study and digestion with Claude), not the best general reader. Every phase must keep the code portable to the Raspberry Pi 5 (see constraints in `CLAUDE.md`).

## Phase 1: Foundations

- [ ] OPML export of the source list (portability insurance)
- [ ] Config file: port, listen address, data folder
- [ ] Numbered schema migrations
- [ ] Keep the full archive (drop the 96 h prune for items) and add full-text search over history (SQLite FTS5; check it exists in each machine's Python)
- [ ] Map of where the press is from: choropleth of item counts by source country, click to filter. Needs ISO country codes in `sources.json` and rules for "Global", "UN", "Pan-African", exile outlets
- [ ] Press-freedom layer on the map (RSF World Press Freedom Index 2026)
- [ ] Topics without new downloads: section names in links plus multilingual keyword rules, mapped to BP motion areas (economy, governance, international relations and security, law and rights, environment and energy, technology, health, education, religion and culture, gender and society, migration, media)
- [ ] Refresh interval per source (wires 5 min, think tanks 6 h)

## Phase 2: Study loop

- [ ] Tags on saved items by motion area and matter-file section; Markdown export per tag into the debate corpus
- [ ] Claude connector (MCP server) over the HTTP API: search archive, saved items and notes
- [ ] Fetch-gap counter per source (shows what is lost while the host sleeps; trigger for Phase 4)
- [ ] Google News `site:` search feeds as fallback for blocked sources (undocumented format; test on a real machine)
- [ ] Map of where stories happen: gazetteer of country names, demonyms and capitals (en/es/pt/fr); watch false positives (Chad, Jordan, Georgia)
- [ ] Keyword alerts to the phone (for example ntfy; verify terms first)
- [ ] Flag sources failing for several days

## Phase 3: Intelligence

- [ ] Benchmark a small multilingual embedding model on the Mac and on the Pi before choosing one
- [ ] Topic classifier from embeddings
- [ ] Cross-language story clustering: coverage by region and regional blind spots (hypothesis to test on real data: short multilingual headlines cluster well enough)
- [ ] Full article text for saved items only (paywalls will limit it)

## Phase 4: Move to the Raspberry Pi 5

Start when any trigger fires: fetch gaps from the Mac sleeping, the Mac short on resources, need for access away from home, batch jobs slowing the Mac.

- [ ] 64-bit Raspberry Pi OS, wired network, SSD instead of SD card
- [ ] Run as a service that starts on boot and restarts on failure
- [ ] Move `data/news.db` (single copy), keep a backup on the Mac
- [ ] Tailscale on Pi, Mac and phone; HTTPS through Tailscale's proxy, app still bound to localhost (verify setup)
- [ ] Point the Claude connector at the Pi
- [ ] Run `--check` on the Pi (Linux clients may be blocked differently from macOS)

## Known issues

- 21 sources disabled: 13 block automated readers even through curl (Elcano, ECFR, Ahram, News24, Americas Quarterly, Brookings, Indian Express, ...), 8 without a working public feed (Público, CIDOB, Nueva Sociedad, El Faro, Kyodo, ...). Each has a `note` in `sources.json`.
- Sixth Tone: 308 redirect fix is in `server.py`; not yet confirmed on a real machine.
- Animal Político: the site returned server errors (504) on both checks.
- Sports & celebrity filter hid 4.7% of items in the first real snapshot (219 of 4,673). Outlets that don't put the section in the URL slip through, so the true share is unknown.
- Windows: second-copy detection relies on `allow_reuse_address = False` there; not yet tested on Windows.
