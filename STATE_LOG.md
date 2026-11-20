# STATE LOG — local-sales-posting-tool

> **Session Continuity**: Agents must read this log before starting work and append/prepend an ISO 8601 timestamped entry before ending any session or when `/session-handoff` is invoked.

---

## 2026-11-20T11:28:34-05:00 — Milestone M1 Completed & GitHub Repository Published
- **Completed**:
  - Built core Python package (`local_sales/exif_dates.py`, `local_sales/album.py`, `local_sales/templates.py`, `local_sales/posters.py`) and executable CLI (`local_sales_poster.py`) strictly inside WSL (`/home/user/projects/local-sales-posting-tool`).
  - Implemented pure-Python binary JPEG/TIFF EXIF, XMP, Google Takeout `.json` sidecar, filename, and `mtime` cascade to detect the oldest photo in an image batch and stamp albums with `YYYY-MM-DD - <Title>`.
  - Implemented common + service-unique description rendering across user-selected subsets of `craigslist`, `facebook`, and `freecycle` (`freecycle.com` / `freecycle.org`), generating `listings/<service>.txt`, `listing_bundle.json`, and offline `launchpad.html`.
  - Implemented Playwright persistent-profile WSL browser poster (`~/.config/local-sales-posting-tool/browser-profile`), Google Photos Picker/Library API helper (`GooglePhotosClient`), and Violentmonkey userscript (`userscript/local-sales-autofill.user.js`).
  - Verified 4/4 unit and CLI integration tests passing (`python3 -m unittest discover -s tests -v`).
  - Initialized Git on `main` (`eac9b0e`), created public GitHub repository `https://github.com/Freckles7911/local-sales-posting-tool` (`git@github.com:Freckles7911/local-sales-posting-tool.git`), and pushed `main` to `origin`.
- **In Progress / Current State**:
  - Milestone M1 complete and verified. Ready for Milestone M2 (optional Playwright Chromium install, live Google Photos OAuth token setup, and first-time interactive login in the WSL browser profile).
- **Next Steps**:
  - Optionally install `playwright` + Chromium inside WSL (`pip install -r requirements.txt && playwright install chromium`) for automated browser form filling and photo uploads.
  - Copy `config.example.yaml` to `config.yaml` to customize Arlington pickup details, common terms, or platform-unique text.
- **Blockers / Decisions Needed**:
  - None.

---

## 2026-11-20T11:25:00-05:00 — Initial Setup
- **Completed**:
  - Created initial `STEERAGE.md`, `PLAN.md`, `STATE_LOG.md`, and `AGENTS.md` in `/home/user/projects/local-sales-posting-tool/`.
- **In Progress / Current State**:
  - Executing Milestone M1 implementation.
- **Next Steps**:
  - Implement `local_sales/` package modules (`exif_dates.py`, `album.py`, `templates.py`, `posters.py`), CLI entrypoint, userscript, tests, and GitHub publishing.
- **Blockers / Decisions Needed**:
  - None.
