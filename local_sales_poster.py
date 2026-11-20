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
from local_sales.enrichment import enrich_listing_metadata
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

    enriched = enrich_listing_metadata(
        title=args.title,
        description=args.description,
        explicit_brand=args.brand,
        explicit_model=args.model_number,
        explicit_mfr_url=args.mfr_url,
        mfr_text=args.mfr_text,
        mfr_text_file=Path(args.mfr_text_file) if args.mfr_text_file else None,
        fetch_mfr_online=args.lookup_mfr,
        explicit_condition=args.condition,
        explicit_msrp=args.msrp,
    )

    album = create_dated_album(
        inputs=image_inputs,
        item_title=args.title,
        output_root=Path(args.output_dir),
        date_format=cfg.date_format,
        copy_mode=args.copy_mode,
        strip_exif=args.strip_exif,
        remove_bg=args.remove_bg,
        remove_bg_backend=args.remove_bg_backend,
        remove_bg_command=args.remove_bg_command,
    )

    raw_price = str(args.price).strip()
    effective_price = (
        str(enriched["price_suggestion"]["recommended_price"])
        if raw_price.lower() == "auto"
        else raw_price
    )

    item = ItemDetails(
        title=args.title,
        body=args.description,
        price=effective_price,
        condition=enriched["condition"]["display_label"],
        location=args.location or cfg.default_location,
        postal_code=args.postal_code or cfg.default_postal_code,
        dimensions=args.dimensions,
        extra_notes=args.notes,
        category_hint=args.category,
        brand=enriched.get("brand"),
        model_number=enriched.get("model_number"),
        manufacturer_url=enriched.get("manufacturer_url"),
        manufacturer_description=enriched.get("manufacturer_description"),
        condition_dropdowns=enriched["condition"]["dropdown_values"],
        price_suggestion=enriched["price_suggestion"],
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
        "enrichment": enriched,
        "effective_price": effective_price,
        "bundle_json_path": rendered["bundle_json_path"],
        "launchpad_html_path": rendered["launchpad_html_path"],
        "selected_services": services,
        "google_photos": google_photos_result,
    }


def _print_prepare_summary(summary: Dict[str, Any]) -> None:
    album = summary["album"]
    enrichment = summary.get("enrichment", {})
    print(f"Created date-stamped album: {album['album_title']}")
    print(f"  Directory     : {album['album_dir']}")
    print(
        f"  Oldest photo  : {album['oldest_photo']['filename']} "
        f"({album['oldest_photo']['taken_at']}, source={album['oldest_photo']['source']})"
    )
    print(
        f"  Photos staged : {len(album['photos'])} "
        f"(EXIF stripped: {'yes' if album.get('exif_stripped') else 'no'})"
    )
    if enrichment.get("brand") or enrichment.get("model_number"):
        print(
            f"  Product ID    : brand={enrichment.get('brand') or '-'}, "
            f"model={enrichment.get('model_number') or '-'}"
        )
    if enrichment.get("manufacturer_url"):
        print(f"  Mfr Link      : {enrichment['manufacturer_url']}")
    cond = enrichment.get("condition", {})
    if cond:
        print(
            f"  Condition     : {cond.get('display_label')} ({cond.get('confidence')}) "
            f"dropdowns={cond.get('dropdown_values')}"
        )
    ps = enrichment.get("price_suggestion", {})
    if ps:
        print(
            f"  Price         : ${summary.get('effective_price')} "
            f"(suggested ${ps.get('recommended_price')}, range ${ps.get('low_price')}-${ps.get('high_price')})"
        )
    print(f"  Bundle JSON   : {summary['bundle_json_path']}")
    print(f"  Launchpad HTML: {summary['launchpad_html_path']}")
    print(f"  Services      : {', '.join(summary['selected_services'])}")
    if summary.get("google_photos"):
        gp = summary["google_photos"]
        print(f"  Google Photos : {gp.get('status')} ({gp.get('album_title')})")


def _cmd_analyze(args: argparse.Namespace) -> int:
    enriched = enrich_listing_metadata(
        title=args.title,
        description=args.description,
        explicit_brand=args.brand,
        explicit_model=args.model_number,
        explicit_mfr_url=args.mfr_url,
        mfr_text=args.mfr_text,
        mfr_text_file=Path(args.mfr_text_file) if args.mfr_text_file else None,
        fetch_mfr_online=args.lookup_mfr,
        explicit_condition=args.condition,
        explicit_msrp=args.msrp,
    )
    if args.json:
        print(json.dumps(enriched, indent=2))
        return 0

    print(f"Title             : {args.title}")
    print(f"Brand             : {enriched.get('brand') or '-'}")
    print(f"Model Number      : {enriched.get('model_number') or '-'}")
    print(f"Manufacturer Link : {enriched.get('manufacturer_url') or '-'}")
    cond = enriched["condition"]
    print(
        f"Estimated Cond.   : {cond['display_label']} ({cond['canonical']}, "
        f"confidence={cond['confidence']}, cues={cond['matched_cues']})"
    )
    print(f"Platform Dropdowns: {cond['dropdown_values']}")
    ps = enriched["price_suggestion"]
    print(
        f"Suggested Price   : ${ps['recommended_price']} "
        f"(range ${ps['low_price']}-${ps['high_price']}; {ps['rationale']})"
    )
    if enriched.get("manufacturer_description"):
        print("\nExtracted Manufacturer Description:")
        print(enriched["manufacturer_description"])
    return 0


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


def _add_enrichment_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--brand", default=None, help="Explicit brand/manufacturer name.")
    parser.add_argument("--model-number", default=None, help="Explicit model number.")
    parser.add_argument(
        "--mfr-url",
        default=None,
        help="Explicit manufacturer product page URL.",
    )
    parser.add_argument(
        "--mfr-text",
        default=None,
        help="Raw manufacturer product text or HTML to extract description and MSRP from.",
    )
    parser.add_argument(
        "--mfr-text-file",
        default=None,
        help="Path to text/HTML file containing manufacturer specs or product info.",
    )
    parser.add_argument(
        "--lookup-mfr",
        action="store_true",
        help="Fetch --mfr-url online to extract product description when available.",
    )
    parser.add_argument(
        "--msrp",
        type=float,
        default=None,
        help="Optional original retail/MSRP price to anchor price suggestion.",
    )


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
        default="auto",
        help="Asking price ('auto' uses suggested price, or e.g. '45' / '$45'). Freecycle always shows FREE.",
    )
    parser.add_argument(
        "--condition",
        default="auto",
        help="Item condition ('auto' estimates from text cues, or e.g. 'Like New', 'Good', 'Fair').",
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
    _add_enrichment_arguments(parser)
    parser.add_argument(
        "--strip-exif",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Strip all EXIF/XMP/IPTC/comment metadata from staged album photos (default: enabled).",
    )
    parser.add_argument(
        "--remove-bg",
        action="store_true",
        help="Run optional background removal pipeline on staged album photos.",
    )
    parser.add_argument(
        "--remove-bg-backend",
        choices=("auto", "rembg", "pillow", "simple", "command"),
        default="auto",
        help="Background removal backend (default: auto).",
    )
    parser.add_argument(
        "--remove-bg-command",
        default=None,
        help="Custom command template when --remove-bg-backend=command, e.g. 'rembg i {input} {output}'.",
    )
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

    p_analyze = sub.add_parser(
        "analyze",
        help="Extract model number, manufacturer link/description, condition dropdowns, and suggested price.",
    )
    p_analyze.add_argument("--title", required=True, help="Item title.")
    p_analyze.add_argument("--description", "--body", dest="description", required=True, help="Item description text.")
    p_analyze.add_argument("--condition", default="auto", help="Explicit condition or 'auto'.")
    _add_enrichment_arguments(p_analyze)
    p_analyze.add_argument("--json", action="store_true", help="Output JSON.")
    p_analyze.set_defaults(func=_cmd_analyze)

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
