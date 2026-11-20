"""Listing enrichment: model numbers, manufacturer links/descriptions, condition estimation, and price suggestions."""

from __future__ import annotations

from dataclasses import dataclass, field
import html as html_lib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
import urllib.parse
import urllib.request


KNOWN_BRANDS: Dict[str, str] = {
    "sony": "Sony",
    "lg": "LG",
    "samsung": "Samsung",
    "apple": "Apple",
    "dell": "Dell",
    "hp": "HP",
    "lenovo": "Lenovo",
    "asus": "Asus",
    "acer": "Acer",
    "netgear": "Netgear",
    "tp-link": "TP-Link",
    "garmin": "Garmin",
    "bose": "Bose",
    "jbl": "JBL",
    "yamaha": "Yamaha",
    "canon": "Canon",
    "nikon": "Nikon",
    "dewalt": "DeWalt",
    "milwaukee": "Milwaukee",
    "makita": "Makita",
    "bosch": "Bosch",
    "ryobi": "Ryobi",
    "kitchenaid": "KitchenAid",
    "cuisinart": "Cuisinart",
    "breville": "Breville",
    "zojirushi": "Zojirushi",
    "dyson": "Dyson",
    "instant pot": "Instant Pot",
    "ikea": "IKEA",
    "herman miller": "Herman Miller",
    "steelcase": "Steelcase",
    "trek": "Trek",
    "specialized": "Specialized",
    "anker": "Anker",
    "logitech": "Logitech",
    "nintendo": "Nintendo",
    "microsoft": "Microsoft",
    "google": "Google",
    "panasonic": "Panasonic",
    "philips": "Philips",
    "sonos": "Sonos",
    "sennheiser": "Sennheiser",
    "audio-technica": "Audio-Technica",
    "ninja": "Ninja",
    "vitamix": "Vitamix",
    "weber": "Weber",
    "irobot": "iRobot",
    "roomba": "iRobot",
}

BRAND_SEARCH_URL_TEMPLATES: Dict[str, str] = {
    "Sony": "https://www.sony.com/en/search?q={q}",
    "LG": "https://www.lg.com/us/search?search={q}",
    "Samsung": "https://www.samsung.com/us/search/searchMain/?listType=g&searchTerm={q}",
    "Apple": "https://www.apple.com/us/search/{q}",
    "Dell": "https://www.dell.com/en-us/search/{q}",
    "HP": "https://www.hp.com/us-en/shop/SearchDisplay?searchTerm={q}",
    "Lenovo": "https://www.lenovo.com/us/en/search?text={q}",
    "Asus": "https://www.asus.com/us/searchresult?searchType=products&searchKey={q}",
    "Acer": "https://www.acer.com/us-en/search?q={q}",
    "Netgear": "https://www.netgear.com/search/?q={q}",
    "TP-Link": "https://www.tp-link.com/us/search/?q={q}",
    "Garmin": "https://www.garmin.com/en-US/search/?query={q}",
    "Bose": "https://www.bose.com/search?q={q}",
    "JBL": "https://www.jbl.com/search?q={q}",
    "Yamaha": "https://usa.yamaha.com/search/index.html?search={q}",
    "Canon": "https://www.usa.canon.com/search?q={q}",
    "Nikon": "https://www.nikonusa.com/search?q={q}",
    "DeWalt": "https://www.dewalt.com/search?search={q}",
    "Milwaukee": "https://www.milwaukeetool.com/Search?q={q}",
    "Makita": "https://www.makitatools.com/products/search?q={q}",
    "Bosch": "https://www.boschtools.com/us/en/search/?q={q}",
    "Ryobi": "https://www.ryobitools.com/search?q={q}",
    "KitchenAid": "https://www.kitchenaid.com/results.html?term={q}",
    "Cuisinart": "https://www.cuisinart.com/search/?q={q}",
    "Breville": "https://www.breville.com/us/en/search-results.html?q={q}",
    "Zojirushi": "https://www.zojirushi.com/app/search?q={q}",
    "Dyson": "https://www.dyson.com/search-results?searchText={q}",
    "IKEA": "https://www.ikea.com/us/en/search/?q={q}",
    "Herman Miller": "https://www.hermanmiller.com/search/?q={q}",
    "Steelcase": "https://www.steelcase.com/search/?q={q}",
    "Anker": "https://www.anker.com/search?q={q}",
    "Logitech": "https://www.logitech.com/en-us/search.html?q={q}",
    "Ninja": "https://www.ninjakitchen.com/search?q={q}",
    "Vitamix": "https://www.vitamix.com/us/en_us/search?q={q}",
    "Sonos": "https://www.sonos.com/en-us/search?q={q}",
}

_EXPLICIT_BRAND_RE = re.compile(
    r"(?:^|\n)\s*(?:Brand|Manufacturer|Make)\s*[:\-]\s*([A-Za-z0-9 &.\-]{2,40})",
    re.IGNORECASE,
)

_EXPLICIT_MODEL_RE = re.compile(
    r"(?:Model(?:\s*(?:No\.?|Number|#))?|M/N|P/N|Part\s*(?:No\.?|#)|SKU)\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9\-./]{2,24})",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

_MSRP_RE = re.compile(
    r"(?:MSRP|List\s*Price|Retail(?:\s*Price)?|Original(?:ly)?(?:\s*Price)?|Bought\s*for|Paid)\s*[:\-]?\s*\$\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

_NON_MODEL_TOKEN_RE = re.compile(
    r"^(?:"
    r"\d+(?:p|k|hz|ghz|mhz|hr|hrs|h|v|w|a|mah|wh|gb|tb|mb|in|ft|cm|mm|oz|lb|lbs)|"
    r"\d+-(?:hour|hours|hr|hrs|day|days|week|weeks|month|months|year|years|inch|inches|ft|feet|shelf|shelves|piece|pieces|pack|watt|watts|volt|volts)|"
    r"\d+x\d+(?:x\d+)?|"
    r"(?:wifi|usb|hdmi|bt|mp|gen|ver|v)\d+[a-z]?|"
    r"\d{4}[-/]\d{2}[-/]\d{2}|"
    r"20\d{2}|19\d{2}|"
    r"pxl_\d+.*"
    r")$",
    re.IGNORECASE,
)

_NOISE_LINES = {
    "add to cart",
    "buy now",
    "sign in",
    "skip to main content",
    "skip to content",
    "free shipping",
    "learn more",
    "compare",
}

CONDITION_DROPDOWNS: Dict[str, Dict[str, str]] = {
    "new": {
        "craigslist": "new",
        "facebook": "New",
        "freecycle": "New",
    },
    "like_new": {
        "craigslist": "like new",
        "facebook": "Used - Like New",
        "freecycle": "Like New",
    },
    "good": {
        "craigslist": "good",
        "facebook": "Used - Good",
        "freecycle": "Good",
    },
    "fair": {
        "craigslist": "fair",
        "facebook": "Used - Fair",
        "freecycle": "Fair",
    },
    "salvage": {
        "craigslist": "salvage",
        "facebook": "Used - Fair",
        "freecycle": "For Parts / Repair",
    },
}

CONDITION_DISPLAY_LABELS: Dict[str, str] = {
    "new": "New",
    "like_new": "Like New",
    "good": "Good",
    "fair": "Fair",
    "salvage": "For Parts / Not Working",
}

CONDITION_MULTIPLIERS: Dict[str, Tuple[float, float, float]] = {
    "new": (0.70, 0.60, 0.80),
    "like_new": (0.55, 0.45, 0.65),
    "good": (0.42, 0.32, 0.52),
    "fair": (0.25, 0.18, 0.35),
    "salvage": (0.12, 0.05, 0.18),
}

_CONDITION_CUES: Dict[str, Tuple[str, ...]] = {
    "salvage": (
        "for parts",
        "not working",
        "doesn't work",
        "does not work",
        "doesn't turn on",
        "does not turn on",
        "broken",
        "needs repair",
        "cracked screen",
        "salvage",
        "parts only",
    ),
    "fair": (
        "fair condition",
        "some scratches",
        "scratches",
        "scuffs",
        "cosmetic wear",
        "heavy wear",
        "visible wear",
        "dents",
        "chipped",
        "faded",
        "rust",
        "worn",
        "blemish",
    ),
    "new": (
        "brand new",
        "sealed in box",
        "factory sealed",
        "never opened",
        "unopened",
        "new in box",
        "bnib",
        "nib",
        "new with tags",
        "nwt",
    ),
    "like_new": (
        "like new",
        "barely used",
        "hardly used",
        "used once",
        "used twice",
        "almost new",
        "open box",
        "mint",
        "pristine",
        "spotless",
        "flawless",
    ),
    "good": (
        "good condition",
        "very good",
        "great condition",
        "gently used",
        "well maintained",
        "works great",
        "tested working",
        "normal wear",
        "clean",
    ),
}

_CATEGORY_BASELINES: Tuple[Tuple[Tuple[str, ...], int, str], ...] = (
    (("macbook", "laptop", "notebook", "thinkpad", "chromebook"), 700, "Laptop"),
    (("monitor", "display", "television", "tv", "oled", "qled"), 250, "Monitor / TV"),
    (("headphone", "headphones", "earbud", "earbuds", "wh-1000", "airpods", "speaker", "soundbar"), 180, "Audio"),
    (("router", "modem", "mesh", "switch", "access point"), 120, "Networking"),
    (("camera", "dslr", "mirrorless", "lens"), 400, "Camera / Optics"),
    (("bike", "bicycle", "road bike", "mountain bike", "ebike"), 350, "Bicycle"),
    (("sofa", "couch", "sectional", "recliner"), 500, "Sofa / Seating"),
    (("bookshelf", "bookcase", "desk", "coffee table", "dining table", "dresser", "nightstand", "cabinet"), 180, "Furniture"),
    (("chair", "office chair", "aeron", "ergonomic"), 160, "Chair"),
    (("drill", "saw", "driver", "impact", "grinder", "sander", "router tool", "dewalt", "milwaukee"), 140, "Power Tool"),
    (("mixer", "kitchenaid", "espresso", "breville", "vitamix", "blender", "rice cooker", "zojirushi"), 220, "Kitchen Appliance"),
    (("vacuum", "dyson", "roomba", "microwave", "air purifier", "humidifier"), 150, "Home Appliance"),
    (("lamp", "lantern", "lighting", "fan"), 50, "Home Lighting / Decor"),
)


@dataclass
class ConditionAssessment:
    """Estimated item condition plus mapped platform dropdown selections."""

    canonical: str
    display_label: str
    confidence: str
    matched_cues: List[str] = field(default_factory=list)
    dropdown_values: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical": self.canonical,
            "display_label": self.display_label,
            "confidence": self.confidence,
            "matched_cues": list(self.matched_cues),
            "dropdown_values": dict(self.dropdown_values),
        }


@dataclass
class PriceSuggestion:
    """Suggested listing price range based on retail/category anchor and condition."""

    recommended_price: int
    low_price: int
    high_price: int
    reference_retail_price: int
    reference_source: str
    condition_canonical: str
    condition_multiplier: float
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recommended_price": self.recommended_price,
            "low_price": self.low_price,
            "high_price": self.high_price,
            "reference_retail_price": self.reference_retail_price,
            "reference_source": self.reference_source,
            "condition_canonical": self.condition_canonical,
            "condition_multiplier": self.condition_multiplier,
            "rationale": self.rationale,
        }


def extract_model_and_brand(
    text: str,
    explicit_brand: Optional[str] = None,
    explicit_model: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Detect manufacturer/brand and model number from title/description text."""
    brand: Optional[str] = explicit_brand.strip() if explicit_brand else None
    model: Optional[str] = explicit_model.strip() if explicit_model else None

    if not brand:
        explicit_b = _EXPLICIT_BRAND_RE.search(text)
        if explicit_b:
            brand = explicit_b.group(1).strip()

    if not brand:
        lower_text = text.lower()
        for key in sorted(KNOWN_BRANDS.keys(), key=len, reverse=True):
            if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", lower_text):
                brand = KNOWN_BRANDS[key]
                break

    if not model:
        explicit_m = _EXPLICIT_MODEL_RE.search(text)
        if explicit_m:
            candidate = explicit_m.group(1).strip(".,;:()[]{}")
            if candidate and not _NON_MODEL_TOKEN_RE.match(candidate):
                model = candidate

    if not model:
        tokens = re.findall(r"\b[A-Za-z0-9][A-Za-z0-9\-./]{2,23}\b", text)
        for token in tokens:
            cleaned = token.strip(".,;:()[]{}")
            if len(cleaned) < 4:
                continue
            if _NON_MODEL_TOKEN_RE.match(cleaned):
                continue
            has_alpha = any(c.isalpha() for c in cleaned)
            has_digit = any(c.isdigit() for c in cleaned)
            has_upper = any(c.isupper() for c in cleaned)
            if has_alpha and has_digit and has_upper:
                model = cleaned
                break

    return brand, model


def resolve_manufacturer_links(
    brand: Optional[str],
    model_number: Optional[str],
    explicit_url: Optional[str] = None,
    raw_text: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Build direct manufacturer product/search links and fallback search URLs."""
    found_url: Optional[str] = explicit_url.strip() if explicit_url else None
    if not found_url and raw_text:
        url_match = _URL_RE.search(raw_text)
        if url_match:
            found_url = url_match.group(0).rstrip(".,;)")

    query_parts = [p for p in (brand, model_number) if p]
    if not query_parts and title:
        query_parts = [title.strip()]
    query_str = " ".join(query_parts).strip()

    mfr_site_url: Optional[str] = None
    if brand and brand in BRAND_SEARCH_URL_TEMPLATES and (model_number or title):
        target_q = model_number or (title or "")
        mfr_site_url = BRAND_SEARCH_URL_TEMPLATES[brand].format(
            q=urllib.parse.quote_plus(target_q.strip())
        )

    search_url: Optional[str] = None
    if query_str:
        search_url = (
            "https://duckduckgo.com/?q="
            + urllib.parse.quote_plus(f"{query_str} official product page")
        )

    product_url = found_url or mfr_site_url or search_url
    return {
        "product_url": product_url,
        "manufacturer_search_url": mfr_site_url,
        "web_search_url": search_url,
    }


def _strip_html_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _parse_msrp_from_text(text: str) -> Optional[float]:
    match = _MSRP_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _extract_from_html(html_text: str) -> Dict[str, Any]:
    summary: Optional[str] = None
    bullets: List[str] = []
    detected_msrp: Optional[float] = None
    brand: Optional[str] = None
    model: Optional[str] = None

    for ld_match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    ):
        try:
            payload = json.loads(ld_match.group(1).strip())
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for obj in items:
            if not isinstance(obj, dict):
                continue
            obj_type = str(obj.get("@type", "")).lower()
            if "product" not in obj_type:
                continue
            if isinstance(obj.get("description"), str) and not summary:
                summary = _strip_html_tags(obj["description"])
            if isinstance(obj.get("model"), str) and not model:
                model = obj["model"].strip()
            elif isinstance(obj.get("sku"), str) and not model:
                model = obj["sku"].strip()
            raw_brand = obj.get("brand")
            if isinstance(raw_brand, dict) and isinstance(raw_brand.get("name"), str):
                brand = raw_brand["name"].strip()
            elif isinstance(raw_brand, str):
                brand = raw_brand.strip()
            offers = obj.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict) and "price" in offers:
                try:
                    detected_msrp = float(str(offers["price"]).replace(",", ""))
                except ValueError:
                    pass

    if not summary:
        for meta_pat in (
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
        ):
            m = re.search(meta_pat, html_text, re.IGNORECASE)
            if m:
                summary = html_lib.unescape(m.group(1)).strip()
                break

    for li_match in re.finditer(r"<li[^>]*>(.*?)</li>", html_text, re.IGNORECASE | re.DOTALL):
        cleaned = _strip_html_tags(li_match.group(1))
        if 12 <= len(cleaned) <= 180 and cleaned.lower() not in _NOISE_LINES:
            if cleaned not in bullets:
                bullets.append(cleaned)
        if len(bullets) >= 6:
            break

    if detected_msrp is None:
        detected_msrp = _parse_msrp_from_text(_strip_html_tags(html_text))

    return {
        "summary": summary,
        "bullets": bullets,
        "detected_msrp": detected_msrp,
        "brand": brand,
        "model_number": model,
    }


def _extract_from_plain_text(raw_text: str) -> Dict[str, Any]:
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    filtered: List[str] = []
    bullets: List[str] = []

    for line in lines:
        if line.lower() in _NOISE_LINES:
            continue
        if line.startswith(("-", "*", "•")):
            bullet_body = line.lstrip("-*• ").strip()
            if len(bullet_body) >= 6:
                bullets.append(bullet_body)
        elif ":" in line and len(line) <= 100 and not line.lower().startswith("http"):
            bullets.append(line)
        else:
            filtered.append(line)

    summary = " ".join(filtered[:3]).strip()
    if not summary and bullets:
        summary = bullets[0]
    detected_msrp = _parse_msrp_from_text(raw_text)

    explicit_b = _EXPLICIT_BRAND_RE.search(raw_text)
    explicit_m = _EXPLICIT_MODEL_RE.search(raw_text)
    return {
        "summary": summary or None,
        "bullets": bullets[:6],
        "detected_msrp": detected_msrp,
        "brand": explicit_b.group(1).strip() if explicit_b else None,
        "model_number": explicit_m.group(1).strip(".,;:()[]{}") if explicit_m else None,
    }


def extract_manufacturer_description(
    mfr_text: Optional[str] = None,
    mfr_text_file: Optional[Path] = None,
    mfr_url: Optional[str] = None,
    fetch_online: bool = False,
) -> Dict[str, Any]:
    """Extract manufacturer summary, feature bullets, and MSRP from text, file, or URL."""
    raw = mfr_text or ""
    source = "text" if mfr_text else "none"

    if not raw and mfr_text_file is not None:
        raw = Path(mfr_text_file).expanduser().read_text(encoding="utf-8", errors="ignore")
        source = f"file:{mfr_text_file}"

    if not raw and fetch_online and mfr_url:
        try:
            req = urllib.request.Request(
                mfr_url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) local-sales-posting-tool/0.2"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            source = f"url:{mfr_url}"
        except Exception as exc:
            return {
                "summary": None,
                "bullets": [],
                "detected_msrp": None,
                "brand": None,
                "model_number": None,
                "source": f"url_error:{exc}",
            }

    if not raw.strip():
        return {
            "summary": None,
            "bullets": [],
            "detected_msrp": None,
            "brand": None,
            "model_number": None,
            "source": "none",
        }

    is_html = bool(re.search(r"<(?:html|meta|script|div|p|li)\b", raw, re.IGNORECASE))
    parsed = _extract_from_html(raw) if is_html else _extract_from_plain_text(raw)
    parsed["source"] = source
    return parsed


def estimate_condition(
    text: str, explicit_condition: Optional[str] = None
) -> ConditionAssessment:
    """Estimate canonical condition and map platform-specific dropdown values."""
    if explicit_condition and explicit_condition.strip().lower() not in {"", "auto"}:
        raw_norm = explicit_condition.strip().lower()
        if any(k in raw_norm for k in ("salvage", "parts", "broken", "not working")):
            canonical = "salvage"
        elif "fair" in raw_norm:
            canonical = "fair"
        elif any(k in raw_norm for k in ("like new", "like-new", "mint")):
            canonical = "like_new"
        elif any(k in raw_norm for k in ("new", "sealed", "bnib", "nib")):
            canonical = "new"
        else:
            canonical = "good"

        dropdowns = dict(CONDITION_DROPDOWNS[canonical])
        if "excellent" in raw_norm or "very good" in raw_norm:
            dropdowns["craigslist"] = "excellent"
        return ConditionAssessment(
            canonical=canonical,
            display_label=CONDITION_DISPLAY_LABELS[canonical],
            confidence="explicit",
            matched_cues=[explicit_condition.strip()],
            dropdown_values=dropdowns,
        )

    lower = text.lower()
    matched_by_tier: Dict[str, List[str]] = {}
    for tier in ("salvage", "fair", "new", "like_new", "good"):
        hits = [
            cue
            for cue in _CONDITION_CUES[tier]
            if re.search(rf"(?<![a-z0-9]){re.escape(cue)}(?![a-z0-9])", lower)
        ]
        if hits:
            matched_by_tier[tier] = hits

    for tier in ("salvage", "fair", "new", "like_new", "good"):
        if tier in matched_by_tier:
            cues = matched_by_tier[tier]
            confidence = "high" if len(cues) >= 2 else "medium"
            return ConditionAssessment(
                canonical=tier,
                display_label=CONDITION_DISPLAY_LABELS[tier],
                confidence=confidence,
                matched_cues=cues,
                dropdown_values=dict(CONDITION_DROPDOWNS[tier]),
            )

    return ConditionAssessment(
        canonical="good",
        display_label=CONDITION_DISPLAY_LABELS["good"],
        confidence="default",
        matched_cues=[],
        dropdown_values=dict(CONDITION_DROPDOWNS["good"]),
    )


def _round_clean_price(value: float) -> int:
    if value >= 25:
        return max(5, int(round(value / 5.0) * 5))
    return max(1, int(round(value)))


def suggest_price(
    title: str,
    description: str,
    condition_canonical: str,
    explicit_msrp: Optional[float] = None,
    extracted_mfr_msrp: Optional[float] = None,
) -> PriceSuggestion:
    """Suggest a fair local-pickup resale price from MSRP/category baseline and condition."""
    combined = f"{title}\n{description}"
    text_msrp = _parse_msrp_from_text(combined)

    if explicit_msrp is not None and explicit_msrp > 0:
        ref_retail = float(explicit_msrp)
        ref_source = "explicit_msrp"
        cat_label = "Explicit MSRP"
    elif extracted_mfr_msrp is not None and extracted_mfr_msrp > 0:
        ref_retail = float(extracted_mfr_msrp)
        ref_source = "manufacturer_msrp"
        cat_label = "Manufacturer MSRP"
    elif text_msrp is not None and text_msrp > 0:
        ref_retail = float(text_msrp)
        ref_source = "text_retail_mention"
        cat_label = "Text Retail Mention"
    else:
        lower = combined.lower()
        ref_retail = 75.0
        cat_label = "General Household"
        for keywords, baseline, label in _CATEGORY_BASELINES:
            if any(kw in lower for kw in keywords):
                ref_retail = float(baseline)
                cat_label = label
                break
        ref_source = "category_baseline"

    canon = condition_canonical if condition_canonical in CONDITION_MULTIPLIERS else "good"
    mult, low_mult, high_mult = CONDITION_MULTIPLIERS[canon]

    recommended = _round_clean_price(ref_retail * mult)
    low = min(recommended, _round_clean_price(ref_retail * low_mult))
    high = max(recommended, _round_clean_price(ref_retail * high_mult))

    rationale = (
        f"Estimated {CONDITION_DISPLAY_LABELS[canon]} resale value ({int(mult * 100)}% of "
        f"${int(round(ref_retail))} {cat_label} baseline via {ref_source})."
    )
    return PriceSuggestion(
        recommended_price=recommended,
        low_price=low,
        high_price=high,
        reference_retail_price=int(round(ref_retail)),
        reference_source=ref_source,
        condition_canonical=canon,
        condition_multiplier=mult,
        rationale=rationale,
    )


def enrich_listing_metadata(
    title: str,
    description: str,
    explicit_brand: Optional[str] = None,
    explicit_model: Optional[str] = None,
    explicit_mfr_url: Optional[str] = None,
    mfr_text: Optional[str] = None,
    mfr_text_file: Optional[Path] = None,
    fetch_mfr_online: bool = False,
    explicit_condition: Optional[str] = None,
    explicit_msrp: Optional[float] = None,
) -> Dict[str, Any]:
    """Run full product enrichment: brand/model, manufacturer link/description, condition, and price."""
    mfr_parsed = extract_manufacturer_description(
        mfr_text=mfr_text,
        mfr_text_file=mfr_text_file,
        mfr_url=explicit_mfr_url,
        fetch_online=fetch_mfr_online,
    )

    combined_text = "\n".join(p for p in (title, description, mfr_text or "") if p)
    brand, model_number = extract_model_and_brand(
        combined_text,
        explicit_brand=explicit_brand or mfr_parsed.get("brand"),
        explicit_model=explicit_model or mfr_parsed.get("model_number"),
    )

    links = resolve_manufacturer_links(
        brand=brand,
        model_number=model_number,
        explicit_url=explicit_mfr_url,
        raw_text=combined_text,
        title=title,
    )

    condition = estimate_condition(
        combined_text, explicit_condition=explicit_condition
    )

    price_info = suggest_price(
        title=title,
        description=combined_text,
        condition_canonical=condition.canonical,
        explicit_msrp=explicit_msrp,
        extracted_mfr_msrp=mfr_parsed.get("detected_msrp"),
    )

    formatted_mfr_desc: Optional[str] = None
    if mfr_parsed.get("summary") or mfr_parsed.get("bullets"):
        parts: List[str] = []
        if mfr_parsed.get("summary"):
            parts.append(str(mfr_parsed["summary"]))
        if mfr_parsed.get("bullets"):
            parts.extend(f"- {b}" for b in mfr_parsed["bullets"])
        formatted_mfr_desc = "\n".join(parts)

    return {
        "brand": brand,
        "model_number": model_number,
        "manufacturer_url": links.get("product_url"),
        "manufacturer_links": links,
        "manufacturer_description": formatted_mfr_desc,
        "manufacturer_parsed": mfr_parsed,
        "condition": condition.to_dict(),
        "price_suggestion": price_info.to_dict(),
    }
