"""Playwright persistent-profile posting automation for Craigslist, Facebook Marketplace, and Freecycle."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .templates import normalize_services


DEFAULT_BROWSER_PROFILE_DIR = (
    Path.home() / ".config" / "local-sales-posting-tool" / "browser-profile"
)

VALID_SUBMIT_MODES = ("review", "draft", "publish")


@dataclass(frozen=True)
class PostingTarget:
    """Platform-specific form metadata and CSS/ARIA selectors."""

    service: str
    display_name: str
    post_url: str
    title_selectors: Sequence[str]
    price_selectors: Sequence[str]
    postal_selectors: Sequence[str]
    description_selectors: Sequence[str]
    file_input_selectors: Sequence[str]
    publish_selectors: Sequence[str]
    login_indicators: Sequence[str]


POSTING_TARGETS: Dict[str, PostingTarget] = {
    "craigslist": PostingTarget(
        service="craigslist",
        display_name="Craigslist",
        post_url="https://post.craigslist.org",
        title_selectors=(
            'input[name="PostingTitle"]',
            '#PostingTitle',
        ),
        price_selectors=(
            'input[name="price"]',
            'input[name="Ask"]',
        ),
        postal_selectors=(
            'input[name="postal"]',
            '#postal_code',
        ),
        description_selectors=(
            'textarea[name="PostingBody"]',
            '#PostingBody',
        ),
        file_input_selectors=(
            'input[type="file"]',
        ),
        publish_selectors=(
            'button[name="go"]',
            'button[type="submit"]',
        ),
        login_indicators=(
            "accounts.craigslist.org",
            'input[name="inputEmailHandle"]',
        ),
    ),
    "facebook": PostingTarget(
        service="facebook",
        display_name="Facebook Marketplace",
        post_url="https://www.facebook.com/marketplace/create/item",
        title_selectors=(
            'label:has-text("Title") input',
            'input[aria-label="Title"]',
        ),
        price_selectors=(
            'label:has-text("Price") input',
            'input[aria-label="Price"]',
        ),
        postal_selectors=(
            'label:has-text("Location") input',
            'input[aria-label="Location"]',
        ),
        description_selectors=(
            'label:has-text("Description") textarea',
            'textarea[aria-label="Description"]',
        ),
        file_input_selectors=(
            'input[type="file"][accept*="image"]',
            'input[type="file"]',
        ),
        publish_selectors=(
            'div[aria-label="Publish"]',
            'button:has-text("Publish")',
        ),
        login_indicators=(
            "facebook.com/login",
            'input[name="email"]',
        ),
    ),
    "freecycle": PostingTarget(
        service="freecycle",
        display_name="Freecycle",
        post_url="https://www.freecycle.org/town/ArlingtonVA",
        title_selectors=(
            'input[name="subject"]',
            'input[name="title"]',
            'input[placeholder*="item" i]',
        ),
        price_selectors=(),
        postal_selectors=(
            'input[name="location"]',
            'input[name="postcode"]',
            'input[placeholder*="location" i]',
        ),
        description_selectors=(
            'textarea[name="description"]',
            'textarea[name="body"]',
            "textarea",
        ),
        file_input_selectors=(
            'input[type="file"]',
        ),
        publish_selectors=(
            'button[type="submit"]',
            'button:has-text("Post")',
        ),
        login_indicators=(
            "freecycle.org/login",
            'input[name="password"]',
        ),
    ),
}


def load_listing_bundle(bundle_or_album_dir: Path) -> Dict[str, Any]:
    """Load `listing_bundle.json` from either a file path or an album directory."""
    path = Path(bundle_or_album_dir).expanduser().resolve()
    if path.is_dir():
        path = path / "listing_bundle.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"Could not find listing_bundle.json at {path}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def is_playwright_available() -> bool:
    """Return True when Playwright Python bindings are importable."""
    try:
        import playwright.sync_api  # noqa: F401

        return True
    except ImportError:
        return False


class PlaywrightPoster:
    """Automates browser form filling and photo upload inside an isolated WSL profile."""

    def __init__(
        self,
        profile_dir: Optional[Path] = None,
        headless: bool = False,
        submit_mode: str = "review",
        slow_mo_ms: int = 100,
    ) -> None:
        if submit_mode not in VALID_SUBMIT_MODES:
            raise ValueError(
                f"Invalid submit_mode '{submit_mode}'. Choose from {VALID_SUBMIT_MODES}."
            )
        self.profile_dir = (
            Path(profile_dir).expanduser().resolve()
            if profile_dir is not None
            else DEFAULT_BROWSER_PROFILE_DIR
        )
        self.headless = headless
        self.submit_mode = submit_mode
        self.slow_mo_ms = slow_mo_ms

    @staticmethod
    def _fill_first_match(page: Any, selectors: Sequence[str], value: str) -> bool:
        if not value:
            return False
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible(timeout=1200):
                    loc.fill(value)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _upload_photos(
        page: Any, selectors: Sequence[str], photo_paths: Sequence[str]
    ) -> bool:
        existing = [p for p in photo_paths if Path(p).is_file()]
        if not existing:
            return False
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0:
                    loc.set_input_files(existing)
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _needs_human_auth_or_challenge(page: Any, target: PostingTarget) -> bool:
        url = (page.url or "").lower()
        for indicator in target.login_indicators:
            if "." in indicator and indicator.lower() in url:
                return True
            try:
                if page.locator(indicator).count() > 0:
                    return True
            except Exception:
                continue
        return False

    def post_bundle(
        self,
        bundle: Dict[str, Any],
        services: Optional[Sequence[str]] = None,
        pause_for_review: bool = True,
    ) -> Dict[str, Any]:
        """Open each chosen service in a persistent WSL browser profile and fill/upload the listing."""
        chosen = (
            normalize_services(services)
            if services
            else list(bundle.get("selected_services", []))
        )
        if not chosen:
            raise ValueError("No target services specified for posting.")

        album_dir = Path(bundle["album"]["album_dir"])
        launchpad = album_dir / "launchpad.html"

        if not is_playwright_available():
            return {
                "status": "playwright_not_installed",
                "message": (
                    "Playwright is not installed in the active Python environment. "
                    "To enable browser upload automation inside WSL, run:\n"
                    "  pip install playwright && playwright install chromium\n"
                    f"In the meantime, open {launchpad} for 1-click copy & upload."
                ),
                "launchpad_html": str(launchpad),
                "targets": chosen,
            }

        from playwright.sync_api import sync_playwright  # type: ignore

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        results: Dict[str, Any] = {}

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
            )
            try:
                for svc in chosen:
                    listing = bundle["listings"].get(svc)
                    if not listing:
                        results[svc] = {
                            "status": "skipped",
                            "reason": "Service not generated in listing_bundle.json",
                        }
                        continue

                    target = POSTING_TARGETS[svc]
                    target_url = listing.get("post_url") or target.post_url
                    page = context.new_page()
                    page.goto(target_url, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)

                    if self._needs_human_auth_or_challenge(page, target):
                        results[svc] = {
                            "status": "auth_required",
                            "url": page.url,
                            "message": (
                                f"{target.display_name} requires interactive login or "
                                "challenge completion in the browser window before form fill."
                            ),
                        }
                        continue

                    filled_title = self._fill_first_match(
                        page, target.title_selectors, listing["title"]
                    )
                    filled_price = self._fill_first_match(
                        page, target.price_selectors, str(listing["price"])
                    )
                    filled_postal = self._fill_first_match(
                        page,
                        target.postal_selectors,
                        listing["postal_code"]
                        if svc == "craigslist"
                        else listing["location"],
                    )
                    filled_desc = self._fill_first_match(
                        page, target.description_selectors, listing["description"]
                    )
                    uploaded_photos = self._upload_photos(
                        page, target.file_input_selectors, listing["photos"]
                    )

                    submitted = False
                    if self.submit_mode == "publish":
                        for sel in target.publish_selectors:
                            try:
                                btn = page.locator(sel).first
                                if btn.count() > 0 and btn.is_enabled():
                                    btn.click()
                                    submitted = True
                                    break
                            except Exception:
                                continue

                    results[svc] = {
                        "status": "published" if submitted else f"ready_{self.submit_mode}",
                        "url": page.url,
                        "filled": {
                            "title": filled_title,
                            "price": filled_price,
                            "location_or_postal": filled_postal,
                            "description": filled_desc,
                            "photos_uploaded": uploaded_photos,
                        },
                    }

                if pause_for_review and not self.headless:
                    print(
                        "\nBrowser tabs remain open in your WSL Playwright profile "
                        f"({self.profile_dir})."
                    )
                    print(
                        "Review each filled form & uploaded photo batch, then press Enter here to close the browser."
                    )
                    try:
                        input()
                    except EOFError:
                        pass
            finally:
                context.close()

        return {
            "status": "completed",
            "submit_mode": self.submit_mode,
            "profile_dir": str(self.profile_dir),
            "results": results,
        }
