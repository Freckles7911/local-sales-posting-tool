#!/usr/bin/env python3
"""CLI entrypoint for local-sales-posting-tool.

Automates oldest-photo date-stamped album creation, common + service-unique
description generation (Craigslist, Facebook Marketplace, Freecycle), and
WSL-native photo upload/form automation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence

from local_sales.album import (
    GooglePhotosClient,
    collect_images,
    create_dated_album,
)
from local_sales.exif_dates import find_oldest_photo
from local_sales.posters import (
    DEFAULT_BROWSER_PROFILE_DIR,
    PlaywrightPoster,
    VALID_SUBMIT_MODES,
    load_listing_bundle,
)
from local_sales.templates import (
    ItemDetails,
    ListingConfig,
    normalize_services,
    render_all_listings,
)


def _merge_image_inputs(args: argparse.Namespace) -> List[Path]:
    raw_inputs: List[str] = []
    if getattr(args, "positional_images", None):
        raw_inputs.extend(args.positional_images)
    if getattr(args, "images", None):
        raw_inputs.extend(args.images)
    if not raw_inputs:
        raise ValueError(
            "Provide at least one image path, directory, or Takeout .zip file."
        )
    return [Path(p) for p in raw_inputs]


def _load_config(args: argparse.Namespace) -> ListingConfig:
    if getattr(args, "config", None):
        cfg = ListingConfig.from_file(Path(args.config))
    else:
        cfg = ListingConfig()

    if getattr(args, "common_text", None):
        cfg.common_text = args.common_text.strip()
    if getattr(args, "craigslist_text", None):
        cfg.service_unique_text["craigslist"] = args.craigslist_text.strip()
    if getattr(args, "facebook_text", None):
        cfg.service_unique_text["facebook"] = args.facebook_text.strip()
    if getattr(args, "freecycle_text", None):
        cfg.service_unique_text["freecycle"] = args.freecycle_text.strip()
    return cfg


def _cmd_inspect_dates(args: argparse.Namespace) -> int:
    images = collect_images(_merge_image_inputs(args))
    oldest, sorted_metas = find_oldest_photo(images)

    if args.json:
        payload = {
            "oldest_photo": oldest.to_dict(),
            "date_stamp": oldest.taken_at.strftime("%Y-%m-%d"),
            "photos": [m.to_dict() for m in sorted_metas],
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(
        f"Oldest photo: {oldest.path.name} -> {oldest.taken_at.isoformat(timespec='seconds')} "
        f"(source: {oldest.source})"
    )
    print(f"Album date stamp: {oldest.taken_at.strftime('%Y-%m-%d')}\n")
    print("Chronological photo order:")
    for idx, meta in enumerate(sorted_metas, start=1):
        marker = " [OLDEST]" if meta.path == oldest.path else ""
        print(
            f"  {idx:02d}. {meta.taken_at.isoformat(timespec='seconds')} "
            f"[{meta.source:12s}] {meta.path}{marker}"
        )
    return 0


def _run_prepare(args: argparse.Namespace) -> Dict[str, Any]:
    cfg = _load_config(args)
    services = normalize_services(args.targets)
    image_inputs = _merge_image_inputs(args)

    album = create_dated_album(
        inputs=image_inputs,
        item_title=args.title,
        output_root=Path(args.output_dir),
        date_format=cfg.date_format,
        copy_mode=args.copy_mode,
    )

    item = ItemDetails(
        title=args.title,
        body=args.description,
        price=args.price,
        condition=args.condition,
        location=args.location or cfg.default_location,
        postal_code=args.postal_code or cfg.default_postal_code,
        dimensions=args.dimensions,
        extra_notes=args.notes,
        category_hint=args.category,
    )

    rendered = render_all_listings(
        item=item,
        services=services,
        album=album,
        config=cfg,
    )

    google_photos_result: Optional[Dict[str, Any]] = None
    if args.sync_google_photos or args.google_photos_dry_run:
        gp_client = GooglePhotosClient(
            access_token=args.google_photos_token,
            token_file=(
                Path(args.google_photos_token_file)
                if args.google_photos_token_file
                else None
            ),
        )
        google_photos_result = gp_client.create_dated_google_album(
            album_bundle=album,
            dry_run=args.google_photos_dry_run,
        )

    return {
        "album": album.to_dict(),
        "bundle_json_path": rendered["bundle_json_path"],
        "launchpad_html_path": rendered["launchpad_html_path"],
        "selected_services": services,
        "google_photos": google_photos_result,
    }


def _print_prepare_summary(summary: Dict[str, Any]) -> None:
    album = summary["album"]
    print(f"Created date-stamped album: {album['album_title']}")
    print(f"  Directory     : {album['album_dir']}")
    print(
        f"  Oldest photo  : {album['oldest_photo']['filename']} "
        f"({album['oldest_photo']['taken_at']}, source={album['oldest_photo']['source']})"
    )
    print(f"  Photos staged : {len(album['photos'])}")
    print(f"  Bundle JSON   : {summary['bundle_json_path']}")
    print(f"  Launchpad HTML: {summary['launchpad_html_path']}")
    print(f"  Services      : {', '.join(summary['selected_services'])}")
    if summary.get("google_photos"):
        gp = summary["google_photos"]
        print(f"  Google Photos : {gp.get('status')} ({gp.get('album_title')})")


def _cmd_prepare(args: argparse.Namespace) -> int:
    summary = _run_prepare(args)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_prepare_summary(summary)
    return 0


def _cmd_post(args: argparse.Namespace) -> int:
    bundle = load_listing_bundle(Path(args.bundle))
    poster = PlaywrightPoster(
        profile_dir=Path(args.profile_dir),
        headless=args.headless,
        submit_mode=args.submit_mode,
    )
    targets = normalize_services(args.targets) if args.targets else None
    result = poster.post_bundle(
        bundle=bundle,
        services=targets,
        pause_for_review=not args.no_pause,
    )
    print(json.dumps(result, indent=2))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    summary = _run_prepare(args)
    _print_prepare_summary(summary)
    bundle = load_listing_bundle(Path(summary["bundle_json_path"]))
    poster = PlaywrightPoster(
        profile_dir=Path(args.profile_dir),
        headless=args.headless,
        submit_mode=args.submit_mode,
    )
    result = poster.post_bundle(
        bundle=bundle,
        services=summary["selected_services"],
        pause_for_review=not args.no_pause,
    )
    print(json.dumps(result, indent=2))
    return 0


def _add_prepare_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "positional_images",
        nargs="*",
        help="Image file(s), directory, or Google Takeout .zip archive(s).",
    )
    parser.add_argument(
        "--images",
        nargs="+",
        help="Explicit image file(s), directory, or .zip archive(s).",
    )
    parser.add_argument("--title", required=True, help="Listing & album title.")
    parser.add_argument(
        "--description",
        "--body",
        dest="description",
        required=True,
        help="Main item description body.",
    )
    parser.add_argument(
        "--price",
        default="0",
        help="Asking price for paid platforms (e.g. '45' or '$45'). Freecycle always shows FREE.",
    )
    parser.add_argument(
        "--condition",
        default="Good",
        help="Item condition (default: Good).",
    )
    parser.add_argument(
        "--location",
        default=None,
        help="Pickup neighborhood/city (default from config or 'Arlington, VA').",
    )
    parser.add_argument(
        "--postal-code",
        default=None,
        help="ZIP/postal code (default from config or '22201').",
    )
    parser.add_argument("--dimensions", default=None, help="Optional dimensions/specs.")
    parser.add_argument("--notes", default=None, help="Optional extra item notes.")
    parser.add_argument("--category", default=None, help="Optional category hint.")
    parser.add_argument(
        "--targets",
        nargs="+",
        default=["craigslist", "facebook", "freecycle"],
        help="Target services: craigslist, facebook, freecycle / freecycle.com.",
    )
    parser.add_argument("--config", default=None, help="Path to YAML or JSON config file.")
    parser.add_argument("--common-text", default=None, help="Override shared policy text.")
    parser.add_argument(
        "--craigslist-text",
        default=None,
        help="Override Craigslist-only instructions.",
    )
    parser.add_argument(
        "--facebook-text",
        default=None,
        help="Override Facebook Marketplace-only instructions.",
    )
    parser.add_argument(
        "--freecycle-text",
        default=None,
        help="Override Freecycle-only instructions.",
    )
    parser.add_argument(
        "--output-dir",
        default="./albums",
        help="Output parent directory for date-stamped albums (default: ./albums).",
    )
    parser.add_argument(
        "--copy-mode",
        choices=("copy", "hardlink", "symlink"),
        default="copy",
        help="How photos are placed into the date-stamped album folder (default: copy).",
    )
    parser.add_argument(
        "--sync-google-photos",
        action="store_true",
        help="Create a date-stamped album in Google Photos and upload batch photos.",
    )
    parser.add_argument(
        "--google-photos-dry-run",
        action="store_true",
        help="Preview Google Photos album creation API payloads without uploading.",
    )
    parser.add_argument(
        "--google-photos-token",
        default=None,
        help="OAuth 2.0 Bearer access token for Google Photos API.",
    )
    parser.add_argument(
        "--google-photos-token-file",
        default=None,
        help="Path to JSON file containing a Google Photos OAuth token.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON summary.",
    )


def _add_post_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile-dir",
        default=str(DEFAULT_BROWSER_PROFILE_DIR),
        help="WSL Playwright persistent profile directory.",
    )
    parser.add_argument(
        "--submit-mode",
        choices=VALID_SUBMIT_MODES,
        default="review",
        help="review (pause before publish), draft, or publish (default: review).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium headless (not recommended for interactive logins).",
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="Do not wait for Enter before closing the browser window.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local_sales_poster",
        description=(
            "Create oldest-photo date-stamped albums and post items to "
            "Craigslist, Facebook Marketplace, and Freecycle from WSL."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser(
        "inspect-dates",
        help="Inspect capture dates of a photo batch and identify the oldest photo.",
    )
    p_inspect.add_argument(
        "positional_images",
        nargs="*",
        help="Image file(s), directory, or .zip archive(s).",
    )
    p_inspect.add_argument("--images", nargs="+", help="Explicit image path(s).")
    p_inspect.add_argument("--json", action="store_true", help="Output JSON.")
    p_inspect.set_defaults(func=_cmd_inspect_dates)

    p_prepare = sub.add_parser(
        "prepare",
        help="Create a date-stamped album and generate per-service listings + launchpad.",
    )
    _add_prepare_arguments(p_prepare)
    p_prepare.set_defaults(func=_cmd_prepare)

    p_post = sub.add_parser(
        "post",
        help="Open Playwright browser tabs, fill forms, and upload photos from a bundle.",
    )
    p_post.add_argument(
        "bundle",
        help="Path to listing_bundle.json or date-stamped album directory.",
    )
    p_post.add_argument(
        "--targets",
        nargs="+",
        default=None,
        help="Optional subset of services to post from the bundle.",
    )
    _add_post_arguments(p_post)
    p_post.set_defaults(func=_cmd_post)

    p_run = sub.add_parser(
        "run",
        help="Prepare date-stamped album + descriptions, then launch Playwright posting.",
    )
    _add_prepare_arguments(p_run)
    _add_post_arguments(p_run)
    p_run.set_defaults(func=_cmd_run)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
