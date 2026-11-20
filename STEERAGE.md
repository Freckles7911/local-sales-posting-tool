# STEERAGE — local-sales-posting-tool

> **Authoritative Human Intent**: Agents must read this file before starting work and treat its directives, constraints, and non-goals as authoritative.

## Vision & Objective
- **Primary Goal**: Automate local-sales photo batch preparation and multi-platform posting workflows from WSL Linux:
  1. Point to a batch of local images, a directory, a Google Takeout ZIP, or an optional Google Photos Picker selection.
  2. Automatically determine the **oldest photo** in the batch (EXIF -> XMP -> Google Takeout `.json` sidecar -> filename -> filesystem `mtime`) and create a date-stamped album (`YYYY-MM-DD - <Title>` / `YYYY-MM-DD_<slug>`), with optional sync to Google Photos.
  3. Compose descriptions for user-selected targets among **Craigslist**, **Facebook Marketplace**, and **Freecycle** (`freecycle.com` / `freecycle.org`) combining **common policy text** (e.g., cash only, no shipping, no checks, meet in Arlington) and **service-unique text** (e.g., Craigslist chat/email relay only).
  4. Upload photos and populate listing forms via a WSL-isolated Playwright persistent browser profile (`~/.config/local-sales-posting-tool/browser-profile`), an offline HTML launchpad (`launchpad.html`), and a Violentmonkey userscript (`userscript/local-sales-autofill.user.js`).
- **Repository**:
  - Local Path: `/home/user/projects/local-sales-posting-tool`
  - GitHub Remote: `Freckles7911/local-sales-posting-tool` (`https://github.com/Freckles7911/local-sales-posting-tool`)
- **Target Audience / Environment**: Windows Subsystem for Linux (WSL2 / Ubuntu Linux) with Python 3.9+ (tested on Python 3.14). Strictly self-contained inside WSL without touching the Windows host installation.

## Architectural Principles & Constraints
- **Local & Offline-First Core**: Core EXIF binary parsing, oldest-photo detection, date-stamped album assembly, listing description templating, `listing_bundle.json`, and `launchpad.html` generation must require **zero mandatory third-party packages** (pure Python standard library, with optional `PyYAML` support when available).
- **WSL Isolation**: All files, browser profiles (`~/.config/local-sales-posting-tool/browser-profile`), temporary staging directories, and caches stay inside Linux/WSL (`/home/user/...` or `/tmp`), never touching `/mnt/c` or the Windows installation.
- **Modular & Reviewable Architecture**: Separate timestamp extraction (`local_sales/exif_dates.py`), album/Google Photos integration (`local_sales/album.py`), template composition (`local_sales/templates.py`), browser automation (`local_sales/posters.py`), and CLI entrypoint (`local_sales_poster.py`).
- **Safe Default Review Mode**: Browser posting defaults to `--submit-mode review` so users verify photos and text before publishing (with `--submit-mode draft` or `--submit-mode publish` available explicitly).

## Non-Goals (Out of Scope)
- Modifying, reading, or installing software on the host Windows filesystem (`/mnt/c/...`).
- Solving, bypassing, or circumventing CAPTCHAs, anti-bot challenges, or platform authentication walls.
- Heavyweight native GUI toolkits or cloud-hosted backend servers.

## Key Decisions & Trade-offs
| Date (ISO 8601) | Decision | Rationale |
| :--- | :--- | :--- |
| 2026-11-20 | Pure-Python binary JPEG/TIFF EXIF parser for oldest-photo detection | Eliminates external dependency on `Pillow` or `exiftool` so core album creation works everywhere in WSL out of the box |
| 2026-11-20 | Two-layer posting automation: `prepare` bundle/HTML launchpad + Playwright persistent WSL profile + Violentmonkey userscript | Craigslist, Facebook Marketplace, and Freecycle lack free public consumer write APIs; persistent WSL browser sessions + userscript give reliable automation without fragile unauthenticated scraping |
| 2026-11-20 | Default `--submit-mode review` before final publish click | Prevents accidental mispriced or incomplete public postings while automating 100% of photo uploads and form entry |
| 2026-11-20 | Normalize `freecycle.com` and `freecycle.org` to `freecycle` | Matches natural user terminology while targeting Freecycle's active web posting domain (`freecycle.org`) |
