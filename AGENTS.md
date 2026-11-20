# Agent Instructions — `local-sales-posting-tool`

## 1. Authoritative Brain Context
Before making any changes in this repository, read the project's brain files:
- @[Steerage](~/brain/projects/local-sales-posting-tool/STEERAGE.md)
- @[Plan](~/brain/projects/local-sales-posting-tool/PLAN.md)
- @[State Log](~/brain/projects/local-sales-posting-tool/STATE_LOG.md)
- @[Global Agent Instructions](~/brain/shared/GLOBAL_AGENTS.md)

### Project Overview
- **Goal**: Local-first WSL workflow and CLI (`local-sales-posting-tool`) that detects the oldest photo in an image batch (EXIF/XMP/Takeout JSON/filename/mtime), creates a date-stamped album (`YYYY-MM-DD - <Title>` + optional Google Photos sync), generates listings combining shared policy text (e.g., cash only, no shipping, no checks, meet in Arlington) and platform-unique text for user-selected targets (**Craigslist**, **Facebook Marketplace**, **Freecycle**), and automates photo upload and form entry via Playwright persistent WSL browser sessions + Violentmonkey userscript.

## 2. Session Handoff
Always update @[State Log](~/brain/projects/local-sales-posting-tool/STATE_LOG.md) and check off completed items in @[Plan](~/brain/projects/local-sales-posting-tool/PLAN.md) before ending a session or when `/session-handoff` is invoked.
