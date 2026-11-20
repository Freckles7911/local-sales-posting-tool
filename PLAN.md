# PLAN — local-sales-posting-tool

> **Alignment**: All tasks in this plan implement the intent defined in `STEERAGE.md`. Check off (`[x]`) tasks as they are verified and completed.

## Milestone: M1 — Core CLI, Album Builder, Multi-Service Posters & Publishing
- [x] Initialize project governance files (`STEERAGE.md`, `PLAN.md`, `STATE_LOG.md`, `AGENTS.md`)
- [x] Implement pure-Python EXIF/XMP/sidecar/filename oldest-photo detection (`local_sales/exif_dates.py`)
- [x] Implement date-stamped album creator & optional Google Photos API client (`local_sales/album.py`)
- [x] Implement common + service-unique description composer & offline HTML launchpad (`local_sales/templates.py`)
- [x] Implement Playwright persistent-profile browser poster (`local_sales/posters.py`) and Violentmonkey userscript (`userscript/local-sales-autofill.user.js`)
- [x] Implement CLI entrypoint (`local_sales_poster.py`), `config.example.yaml`, `requirements.txt`, `.gitignore`, and `README.md`
- [x] Write and verify unit & CLI integration tests (`tests/test_local_sales.py`)
- [x] Initialize Git on `main`, create GitHub repo `Freckles7911/local-sales-posting-tool`, push `main`, and sync `~/brain/projects/local-sales-posting-tool/`

## Milestone: M2 — EXIF Stripping, Optional Background Removal, Manufacturer Enrichment, Condition & Price Suggestion
- [x] Implement pure-Python lossless JPEG/PNG metadata stripping & optional background removal pipeline (`local_sales/image_pipeline.py`)
- [x] Implement brand/model extraction, manufacturer product links & text/HTML description extraction, condition estimation + dropdown mapping, and price suggestion (`local_sales/enrichment.py`)
- [x] Integrate EXIF stripping & optional background removal into `create_dated_album` (`local_sales/album.py`) after reading oldest-photo timestamps
- [x] Integrate enrichment, condition dropdown auto-selection, and price suggestions into `templates.py`, `posters.py`, `userscript/local-sales-autofill.user.js`, and `local_sales_poster.py` (`analyze` + `prepare`/`run` flags)
- [x] Expand unit and CLI test suite (`tests/test_local_sales.py` — 8/8 passing) and push updates to GitHub & `~/brain`

## Upcoming Milestones

### M3 — Live Google Photos OAuth & Interactive Browser Session Onboarding
- [ ] Document and test interactive Google Photos OAuth 2.0 client credentials setup (`credentials.json`)
- [ ] Verify persistent Playwright profile login sessions for Craigslist, Facebook Marketplace, and Freecycle in WSL

## Backlog / Icebox
- [ ] Add optional interactive TUI picker for selecting subset of photos from a directory
- [ ] Add optional image downscaling option before marketplace upload
