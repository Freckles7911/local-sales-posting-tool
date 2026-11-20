# PLAN — local-sales-posting-tool

> **Alignment**: All tasks in this plan implement the intent defined in `STEERAGE.md`. Check off (`[x]`) tasks as they are verified and completed.

## Current Milestone: M1 — Core CLI, Album Builder, Multi-Service Posters & Publishing
- [ ] Initialize project governance files (`STEERAGE.md`, `PLAN.md`, `STATE_LOG.md`, `AGENTS.md`)
- [ ] Implement pure-Python EXIF/XMP/sidecar/filename oldest-photo detection (`local_sales/exif_dates.py`)
- [ ] Implement date-stamped album creator & optional Google Photos API client (`local_sales/album.py`)
- [ ] Implement common + service-unique description composer & offline HTML launchpad (`local_sales/templates.py`)
- [ ] Implement Playwright persistent-profile browser poster (`local_sales/posters.py`) and Violentmonkey userscript (`userscript/local-sales-autofill.user.js`)
- [ ] Implement CLI entrypoint (`local_sales_poster.py`), `config.example.yaml`, `requirements.txt`, `.gitignore`, and `README.md`
- [ ] Write and verify unit & CLI integration tests (`tests/test_local_sales.py`)
- [ ] Initialize Git on `main`, create GitHub repo `Freckles7911/local-sales-posting-tool`, push `main`, and sync `~/brain/projects/local-sales-posting-tool/`

## Upcoming Milestones

### M2 — Live Google Photos OAuth & Interactive Browser Session Onboarding
- [ ] Document and test interactive Google Photos OAuth 2.0 client credentials setup (`credentials.json`)
- [ ] Verify persistent Playwright profile login sessions for Craigslist, Facebook Marketplace, and Freecycle in WSL

## Backlog / Icebox
- [ ] Add optional interactive TUI picker for selecting subset of photos from a directory
- [ ] Add image downscaling/EXIF stripping option for privacy before marketplace upload
