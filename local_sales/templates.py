"""Multi-service listing description composition and offline launchpad generator."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .album import AlbumBundle


CANONICAL_SERVICES = ("craigslist", "facebook", "freecycle")

SERVICE_ALIASES = {
    "craigslist": "craigslist",
    "cl": "craigslist",
    "facebook": "facebook",
    "fb": "facebook",
    "facebook-marketplace": "facebook",
    "facebook_marketplace": "facebook",
    "marketplace": "facebook",
    "fb-marketplace": "facebook",
    "freecycle": "freecycle",
    "freecycle.com": "freecycle",
    "freecycle.org": "freecycle",
    "fc": "freecycle",
}

SERVICE_DISPLAY_NAMES = {
    "craigslist": "Craigslist",
    "facebook": "Facebook Marketplace",
    "freecycle": "Freecycle",
}

DEFAULT_SERVICE_URLS = {
    "craigslist": "https://post.craigslist.org",
    "facebook": "https://www.facebook.com/marketplace/create/item",
    "freecycle": "https://www.freecycle.org/town/ArlingtonVA",
}

DEFAULT_COMMON_TEXT = (
    "Cash only. No shipping available, no checks, no electronic payment apps. "
    "Meet in Arlington, VA."
)

DEFAULT_SERVICE_UNIQUE_TEXT = {
    "craigslist": (
        "Contact through Craigslist chat or Craigslist email relay only. "
        "Do not ask for personal phone numbers or off-platform texts."
    ),
    "facebook": (
        "Contact through Facebook Messenger / Marketplace chat only to coordinate "
        "an Arlington pickup time. If this listing is up, it is still available."
    ),
    "freecycle": (
        "Freecycle OFFER: Please reply via Freecycle with your proposed pickup "
        "day and time window in Arlington."
    ),
}


def normalize_service_name(name: str) -> str:
    """Normalize user-facing service names like 'freecycle.com' or 'fb'."""
    key = name.strip().lower()
    if key in SERVICE_ALIASES:
        return SERVICE_ALIASES[key]
    raise ValueError(
        f"Unsupported service '{name}'. Choose from: craigslist, facebook, freecycle (or freecycle.com)."
    )


def normalize_services(services: Sequence[str]) -> List[str]:
    """Normalize and deduplicate a list of target services while preserving order."""
    result: List[str] = []
    seen = set()
    for raw in services:
        for part in str(raw).split(","):
            cleaned = part.strip()
            if not cleaned:
                continue
            canon = normalize_service_name(cleaned)
            if canon not in seen:
                seen.add(canon)
                result.append(canon)
    if not result:
        raise ValueError("At least one target service must be selected.")
    return result


@dataclass
class ItemDetails:
    """User-supplied and enriched item metadata for generating multi-platform listings."""

    title: str
    body: str
    price: str = "0"
    condition: str = "Good"
    location: str = "Arlington, VA"
    postal_code: str = "22201"
    dimensions: Optional[str] = None
    extra_notes: Optional[str] = None
    category_hint: Optional[str] = None
    brand: Optional[str] = None
    model_number: Optional[str] = None
    manufacturer_url: Optional[str] = None
    manufacturer_description: Optional[str] = None
    condition_dropdowns: Dict[str, str] = field(default_factory=dict)
    price_suggestion: Optional[Dict[str, Any]] = None


@dataclass
class ListingConfig:
    """Common and service-unique description templates plus regional defaults."""

    common_text: str = DEFAULT_COMMON_TEXT
    service_unique_text: Dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_SERVICE_UNIQUE_TEXT)
    )
    service_urls: Dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_SERVICE_URLS)
    )
    default_location: str = "Arlington, VA"
    default_postal_code: str = "22201"
    date_format: str = "%Y-%m-%d"

    @classmethod
    def from_file(cls, path: Path) -> "ListingConfig":
        """Load configuration from a YAML or JSON file."""
        resolved = Path(path).expanduser().resolve()
        raw_text = resolved.read_text(encoding="utf-8")
        data: Dict[str, Any]
        if resolved.suffix.lower() == ".json":
            data = json.loads(raw_text)
        else:
            try:
                import yaml  # type: ignore

                loaded = yaml.safe_load(raw_text)
                data = loaded if isinstance(loaded, dict) else {}
            except ImportError as exc:
                raise RuntimeError(
                    "PyYAML is required to load YAML config files. Install PyYAML or pass a .json config."
                ) from exc

        cfg = cls()
        if isinstance(data.get("common_text"), str) and data["common_text"].strip():
            cfg.common_text = data["common_text"].strip()

        raw_unique = data.get("service_unique_text")
        if isinstance(raw_unique, dict):
            for k, v in raw_unique.items():
                if isinstance(v, str) and v.strip():
                    cfg.service_unique_text[normalize_service_name(k)] = v.strip()

        raw_urls = data.get("service_urls")
        if isinstance(raw_urls, dict):
            for k, v in raw_urls.items():
                if isinstance(v, str) and v.strip():
                    cfg.service_urls[normalize_service_name(k)] = v.strip()

        if isinstance(data.get("default_location"), str) and data["default_location"].strip():
            cfg.default_location = data["default_location"].strip()
        if isinstance(data.get("default_postal_code"), (str, int)):
            cfg.default_postal_code = str(data["default_postal_code"]).strip()
        if isinstance(data.get("date_format"), str) and data["date_format"].strip():
            cfg.date_format = data["date_format"].strip()
        return cfg


def format_price_for_service(price: str, service: str) -> str:
    """Format listing price cleanly for paid marketplaces vs. Freecycle."""
    svc = normalize_service_name(service)
    cleaned = str(price).strip()
    if svc == "freecycle":
        return "FREE"
    if not cleaned or cleaned.lower() in {"0", "0.0", "0.00", "free"}:
        return "FREE"
    if cleaned.startswith("$"):
        return cleaned
    return f"${cleaned}"


def numeric_price_value(price: str, service: str) -> str:
    """Return digits-only/decimal price string suitable for form price inputs."""
    svc = normalize_service_name(service)
    if svc == "freecycle":
        return "0"
    cleaned = str(price).strip().lstrip("$").replace(",", "")
    if not cleaned or cleaned.lower() == "free":
        return "0"
    return cleaned


def render_service_description(
    item: ItemDetails,
    service: str,
    config: ListingConfig,
    date_stamp: Optional[str] = None,
) -> str:
    """Render a service-specific description combining item body, common text, and unique text."""
    svc = normalize_service_name(service)
    formatted_price = format_price_for_service(item.price, svc)

    lines: List[str] = [
        f"{item.title.strip()} — {formatted_price}",
        "",
        item.body.strip(),
        "",
        f"Condition: {item.condition.strip()}",
        f"Pickup Location: {item.location.strip()}",
    ]
    if item.dimensions:
        lines.append(f"Dimensions / Specs: {item.dimensions.strip()}")
    if item.extra_notes:
        lines.append(f"Notes: {item.extra_notes.strip()}")

    if (
        item.brand
        or item.model_number
        or item.manufacturer_description
        or item.manufacturer_url
    ):
        lines.extend(["", "Product & Manufacturer Details:"])
        if item.brand:
            lines.append(f"Brand: {item.brand.strip()}")
        if item.model_number:
            lines.append(f"Model Number: {item.model_number.strip()}")
        if item.manufacturer_description:
            lines.append(item.manufacturer_description.strip())
        if item.manufacturer_url:
            lines.append(f"Manufacturer Product Page: {item.manufacturer_url.strip()}")

    lines.extend(
        [
            "",
            "Terms (All Listings):",
            config.common_text.strip(),
        ]
    )

    unique_block = config.service_unique_text.get(svc, "").strip()
    if unique_block:
        lines.extend(
            [
                "",
                f"{SERVICE_DISPLAY_NAMES[svc]} Instructions:",
                unique_block,
            ]
        )

    if date_stamp:
        lines.extend(
            [
                "",
                f"Photo Album Date Stamp (Oldest Photo): {date_stamp}",
            ]
        )

    return "\n".join(lines).strip() + "\n"


def _build_launchpad_html(bundle_payload: Dict[str, Any]) -> str:
    """Build a self-contained offline HTML launchpad for reviewing and copying listings."""
    album_title = html.escape(bundle_payload["album"]["album_title"])
    date_stamp = html.escape(bundle_payload["album"]["date_stamp"])
    oldest_name = html.escape(bundle_payload["album"]["oldest_photo"]["filename"])
    oldest_source = html.escape(bundle_payload["album"]["oldest_photo"]["source"])
    exif_stripped = "Yes (EXIF/XMP removed)" if bundle_payload["album"].get("exif_stripped") else "No"
    item_meta = bundle_payload.get("item", {})
    enrichment_bits = []
    if item_meta.get("brand") or item_meta.get("model_number"):
        bm = " ".join(p for p in (item_meta.get("brand"), item_meta.get("model_number")) if p)
        enrichment_bits.append(f"Product: <strong>{html.escape(bm)}</strong>")
    if item_meta.get("manufacturer_url"):
        murl = html.escape(item_meta["manufacturer_url"])
        enrichment_bits.append(f'<a href="{murl}" target="_blank" rel="noopener noreferrer">Manufacturer Page</a>')
    if item_meta.get("price_suggestion"):
        ps = item_meta["price_suggestion"]
        enrichment_bits.append(
            f"Suggested Price: <strong>${ps['recommended_price']}</strong> (range ${ps['low_price']}–${ps['high_price']})"
        )
    enrichment_line = (
        "<p>" + " &bull; ".join(enrichment_bits) + "</p>" if enrichment_bits else ""
    )

    photo_cards = []
    for photo_path_str in bundle_payload["album"]["photos"]:
        p = Path(photo_path_str)
        rel_src = html.escape(f"photos/{p.name}")
        caption = html.escape(p.name)
        photo_cards.append(
            f'<figure class="photo-card"><img src="{rel_src}" alt="{caption}"><figcaption>{caption}</figcaption></figure>'
        )

    service_cards = []
    for svc_key, listing in bundle_payload["listings"].items():
        display = html.escape(listing["display_name"])
        url = html.escape(listing["post_url"])
        title = html.escape(listing["title"])
        price = html.escape(listing["formatted_price"])
        desc = html.escape(listing["description"])
        cond_dd = html.escape(str(listing.get("condition_dropdown") or listing.get("condition") or ""))
        svc_id = html.escape(svc_key)
        service_cards.append(
            f"""
            <section class="service-card" data-service="{svc_id}">
              <div class="service-header">
                <div>
                  <h2>{display}</h2>
                  <p class="meta">Title: <strong>{title}</strong> &bull; Price: <strong>{price}</strong> &bull; Dropdown Condition: <strong>{cond_dd}</strong></p>
                </div>
                <div class="actions">
                  <button type="button" onclick="copyText('title-{svc_id}')">Copy Title</button>
                  <button type="button" class="primary" onclick="copyText('desc-{svc_id}')">Copy Description</button>
                  <a class="button-link" href="{url}" target="_blank" rel="noopener noreferrer">Open {display}</a>
                </div>
              </div>
              <input id="title-{svc_id}" type="hidden" value="{title}">
              <textarea id="desc-{svc_id}" readonly rows="12">{desc}</textarea>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{album_title} — Sales Posting Launchpad</title>
  <style>
    :root {{ color-scheme: dark light; font-family: system-ui, -apple-system, sans-serif; }}
    body {{ max-width: 1000px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
    header {{ border-bottom: 1px solid #8884; padding-bottom: 1rem; margin-bottom: 1.5rem; }}
    .photos {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 0.75rem; margin-bottom: 2rem; }}
    .photo-card {{ margin: 0; border: 1px solid #8884; border-radius: 8px; overflow: hidden; padding: 0.4rem; }}
    .photo-card img {{ width: 100%; height: 120px; object-fit: cover; border-radius: 4px; display: block; }}
    .photo-card figcaption {{ font-size: 0.75rem; word-break: break-all; margin-top: 0.3rem; }}
    .service-card {{ border: 1px solid #8884; border-radius: 10px; padding: 1rem; margin-bottom: 1.25rem; }}
    .service-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }}
    .service-header h2 {{ margin: 0 0 0.25rem 0; }}
    .meta {{ margin: 0; font-size: 0.9rem; opacity: 0.85; }}
    .actions {{ display: flex; gap: 0.5rem; flex-wrap: wrap; }}
    button, .button-link {{ padding: 0.45rem 0.8rem; border-radius: 6px; border: 1px solid #8886; cursor: pointer; text-decoration: none; font-size: 0.9rem; }}
    button.primary {{ font-weight: 600; }}
    textarea {{ width: 100%; margin-top: 0.75rem; font-family: ui-monospace, monospace; font-size: 0.88rem; padding: 0.65rem; border-radius: 6px; box-sizing: border-box; }}
  </style>
</head>
<body>
  <header>
    <h1>{album_title}</h1>
    <p>Oldest Photo Date Stamp: <strong>{date_stamp}</strong> (from <code>{oldest_name}</code> via <code>{oldest_source}</code>) &bull; Metadata Stripped: <strong>{exif_stripped}</strong></p>
    {enrichment_line}
  </header>
  <h2>Ordered Album Photos</h2>
  <div class="photos">
    {"".join(photo_cards)}
  </div>
  <h2>Generated Listings</h2>
  {"".join(service_cards)}
  <script>
    async function copyText(elementId) {{
      const el = document.getElementById(elementId);
      const val = el.value;
      await navigator.clipboard.writeText(val);
    }}
  </script>
</body>
</html>
"""


def render_all_listings(
    item: ItemDetails,
    services: Sequence[str],
    album: AlbumBundle,
    config: ListingConfig,
) -> Dict[str, Dict[str, Any]]:
    """Write per-service text listings, listing_bundle.json, and launchpad.html."""
    canonical_services = normalize_services(services)
    listings_dir = album.album_dir / "listings"
    listings_dir.mkdir(parents=True, exist_ok=True)

    listings: Dict[str, Dict[str, Any]] = {}
    for svc in canonical_services:
        description = render_service_description(
            item=item,
            service=svc,
            config=config,
            date_stamp=album.date_stamp,
        )
        text_path = listings_dir / f"{svc}.txt"
        text_path.write_text(description, encoding="utf-8")

        listings[svc] = {
            "service": svc,
            "display_name": SERVICE_DISPLAY_NAMES[svc],
            "post_url": config.service_urls.get(svc, DEFAULT_SERVICE_URLS[svc]),
            "title": item.title.strip(),
            "price": numeric_price_value(item.price, svc),
            "formatted_price": format_price_for_service(item.price, svc),
            "condition": item.condition.strip(),
            "condition_dropdown": item.condition_dropdowns.get(svc, item.condition.strip()),
            "location": item.location.strip(),
            "postal_code": item.postal_code.strip(),
            "brand": item.brand,
            "model_number": item.model_number,
            "manufacturer_url": item.manufacturer_url,
            "price_suggestion": item.price_suggestion,
            "description": description,
            "common_text": config.common_text.strip(),
            "service_unique_text": config.service_unique_text.get(svc, "").strip(),
            "text_file": str(text_path),
            "photos": [str(p) for p in album.photos],
        }

    bundle_payload: Dict[str, Any] = {
        "album": album.to_dict(),
        "item": {
            "title": item.title.strip(),
            "body": item.body.strip(),
            "price": item.price,
            "condition": item.condition.strip(),
            "condition_dropdowns": dict(item.condition_dropdowns),
            "location": item.location.strip(),
            "postal_code": item.postal_code.strip(),
            "dimensions": item.dimensions,
            "extra_notes": item.extra_notes,
            "category_hint": item.category_hint,
            "brand": item.brand,
            "model_number": item.model_number,
            "manufacturer_url": item.manufacturer_url,
            "manufacturer_description": item.manufacturer_description,
            "price_suggestion": item.price_suggestion,
        },
        "selected_services": canonical_services,
        "listings": listings,
    }

    bundle_json_path = album.album_dir / "listing_bundle.json"
    bundle_json_path.write_text(
        json.dumps(bundle_payload, indent=2) + "\n", encoding="utf-8"
    )

    launchpad_path = album.album_dir / "launchpad.html"
    launchpad_path.write_text(_build_launchpad_html(bundle_payload), encoding="utf-8")

    return {
        "bundle_json_path": str(bundle_json_path),
        "launchpad_html_path": str(launchpad_path),
        "listings": listings,
    }
