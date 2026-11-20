# local-sales-posting-tool

A Linux / WSL2-native CLI and browser automation workflow for batching item photos, creating an **oldest-photo date-stamped album** (locally + optional Google Photos sync), composing **shared + platform-unique descriptions**, and uploading/posting to user-selected combinations of **Craigslist**, **Facebook Marketplace**, and **Freecycle** (`freecycle.com` / `freecycle.org`).

> **WSL Isolation:** Everything runs strictly inside Linux/WSL (`~/.config/local-sales-posting-tool/` and your project directory). It never touches your Windows host installation.

## Features

- **Oldest-Photo Date-Stamped Albums (`local_sales/exif_dates.py`, `local_sales/album.py`)**:
  - Accepts individual image files, folders, or Google Takeout `.zip` archives.
  - Extracts capture timestamps using a pure-Python zero-dependency cascade:
    1. Binary JPEG/TIFF EXIF (`DateTimeOriginal` `0x9003`, `DateTimeDigitized` `0x9004`, `DateTime` `0x0132`)
    2. Embedded XMP creation tags
    3. Google Takeout `.json` sidecars (`photoTakenTime.timestamp`)
    4. Smartphone/camera filenames (`PXL_YYYYMMDD_HHMMSS`, `IMG_YYYYMMDD_HHMMSS`, `YYYY-MM-DD`)
    5. Filesystem `mtime` fallback
  - Finds the **oldest photo** in the batch and stamps the album title (`YYYY-MM-DD - <Item Title>`) and folder (`YYYY-MM-DD_<slug>`), copying the photos in chronological order.
  - **Privacy EXIF/Metadata Stripping (`local_sales/image_pipeline.py`)**: After extracting the oldest photo date, strips all EXIF, XMP, IPTC, ICC, comment, and PNG text/EXIF chunks from every staged album image by default (`--strip-exif` / `--no-strip-exif`).
  - **Optional Background Removal (`--remove-bg`)**: Supports `rembg`, `Pillow`, a pure-Python PNG corner-flood keyer (`simple`), or a custom WSL command (`--remove-bg-backend command --remove-bg-command ...`).
  - Optional `GooglePhotosClient` supports Google Photos Picker API selection and creating app-owned date-stamped albums via `albums.create` + `mediaItems:batchCreate`.
- **Product Enrichment, Condition Estimation & Price Suggestion (`local_sales/enrichment.py`)**:
  - Automatically detects **brand & model numbers** (e.g. `Sony WH-1000XM5`), generates direct **manufacturer product page links**, and extracts **manufacturer descriptions, spec bullets, and MSRP** from pasted text, local HTML/text files (`--mfr-text` / `--mfr-text-file`), or optional online fetch (`--lookup-mfr`).
  - Estimates **item condition** (`New`, `Like New`, `Good`, `Fair`, `For Parts`) from description cues and maps each service's exact **condition dropdown** (`--condition auto`).
  - Suggests a local resale **price & range** (`--price auto`) anchored on MSRP or category baselines + condition.
- **Shared + Service-Unique Description Composition (`local_sales/templates.py`)**:
  - Choose any subset of `craigslist`, `facebook`, and `freecycle` (`freecycle.com`).
  - **Common Text** appears in *every* generated description (e.g. `Cash only. No shipping available, no checks, no electronic payment apps. Meet in Arlington, VA.`).
  - **Service-Unique Text** appears *only* on that platform (e.g. Craigslist chat/email relay instructions only on Craigslist).
  - Generates per-service `.txt` files, `listing_bundle.json`, and an offline `launchpad.html` with photo thumbnails, 1-click Copy Title / Copy Description buttons, and direct links.
- **Photo Upload & Posting Automation (`local_sales/posters.py` + Userscript)**:
  - **Playwright Persistent Profile (`post` / `run`)**: Launches Chromium with a persistent WSL profile (`~/.config/local-sales-posting-tool/browser-profile`), opens your selected target tabs, fills title/price/location/description, selects the mapped **condition dropdown** when present, uploads all EXIF-stripped album photos via `<input type="file">`, and pauses in `--submit-mode review` so you can verify before publishing.
  - **Violentmonkey Userscript ([userscript/local-sales-autofill.user.js](userscript/local-sales-autofill.user.js))**: Adds a floating autofill widget on Craigslist, Facebook Marketplace, and Freecycle posting pages that reads `listing_bundle.json` and fills forms + condition dropdowns.

## Quick Start (WSL)

The core commands (`inspect-dates`, `analyze`, and `prepare`) require **zero external dependencies** on Python 3.9+.

### 1. Inspect Photo Dates or Analyze Listing Metadata

```bash
./local_sales_poster.py inspect-dates ./my-item-photos/
./local_sales_poster.py analyze \
  --title "Sony WH-1000XM5 Headphones" \
  --description "Barely used, like new in case. Originally $399." \
  --mfr-text "Industry-leading noise cancellation with 30-hour battery life."
```

### 2. Create Date-Stamped EXIF-Stripped Album & Multi-Service Descriptions

```bash
./local_sales_poster.py prepare ./my-item-photos/ \
  --title "Walnut Bookshelf" \
  --price auto \
  --condition auto \
  --description "Solid walnut 4-shelf bookcase, clean and sturdy." \
  --targets craigslist facebook freecycle.com \
  --config config.example.yaml
```

This creates `./albums/<OLDEST-DATE>_walnut-bookshelf/` containing:
- `photos/` — ordered photos (`01_...`, `02_...`)
- `album_manifest.json` — oldest-photo provenance & metadata
- `listings/craigslist.txt`, `listings/facebook.txt`, `listings/freecycle.txt`
- `listing_bundle.json`
- `launchpad.html`

### 3. Enable Optional Playwright Browser Upload Automation

```bash
pip install -r requirements.txt
playwright install chromium
```

Then run `post` on a prepared bundle (or `run` to prepare + post together):

```bash
./local_sales_poster.py post ./albums/2026-03-14_walnut-bookshelf/ --submit-mode review
```

Log in once inside the WSL Chromium window; your session persists in `~/.config/local-sales-posting-tool/browser-profile` across future listings.

### 4. Google Photos Sync (Optional)

Preview the Google Photos album creation payload:

```bash
./local_sales_poster.py prepare ./my-item-photos/ \
  --title "Walnut Bookshelf" \
  --description "Solid walnut bookcase." \
  --google-photos-dry-run
```

To upload to Google Photos directly, export an OAuth 2.0 Bearer token with `https://www.googleapis.com/auth/photoslibrary.appendonly` into `GOOGLE_PHOTOS_ACCESS_TOKEN` and pass `--sync-google-photos`.

## Running Tests

```bash
python3 -m unittest discover -s tests -v
```
