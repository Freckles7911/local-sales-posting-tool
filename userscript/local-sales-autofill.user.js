// ==UserScript==
// @name         Local Sales Posting Autofill (Craigslist / FB Marketplace / Freecycle)
// @namespace    https://github.com/Freckles7911/local-sales-posting-tool
// @version      0.1.0
// @description  Autofill service-specific + common listing descriptions from listing_bundle.json on Craigslist, Facebook Marketplace, and Freecycle.
// @author       Freckles7911
// @match        *://post.craigslist.org/*
// @match        *://*.craigslist.org/post/*
// @match        *://*.facebook.com/marketplace/create/*
// @match        *://*.freecycle.org/*
// @grant        none
// ==/UserScript==

(function () {
  "use strict";

  const DETECTED_SERVICE = (() => {
    const host = window.location.hostname.toLowerCase();
    if (host.includes("craigslist.org")) return "craigslist";
    if (host.includes("facebook.com")) return "facebook";
    if (host.includes("freecycle.org")) return "freecycle";
    return null;
  })();

  if (!DETECTED_SERVICE) return;

  function setNativeValue(element, value) {
    if (!element || value == null) return;
    const proto =
      element.tagName === "TEXTAREA"
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (descriptor && descriptor.set) {
      descriptor.set.call(element, value);
    } else {
      element.value = value;
    }
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function fillFirst(selectors, value) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        setNativeValue(el, value);
        return true;
      }
    }
    return false;
  }

  function selectConditionDropdown(service, listing) {
    const condValue = (listing.condition_dropdown || listing.condition || "").trim();
    if (!condValue) return false;
    const want = condValue.toLowerCase();

    const selectSelectors =
      service === "craigslist"
        ? ['select[name="sale_condition"]', "#sale_condition"]
        : service === "freecycle"
        ? ['select[name*="condition" i]']
        : [];

    for (const sel of selectSelectors) {
      const selectEl = document.querySelector(sel);
      if (!selectEl) continue;
      for (const opt of Array.from(selectEl.options || [])) {
        const text = (opt.textContent || "").trim().toLowerCase();
        const val = (opt.value || "").trim().toLowerCase();
        if (text.includes(want) || val === want) {
          selectEl.value = opt.value;
          selectEl.dispatchEvent(new Event("change", { bubbles: true }));
          return true;
        }
      }
    }

    if (service === "facebook") {
      const trigger =
        document.querySelector('[aria-label="Condition"]') ||
        Array.from(document.querySelectorAll("label")).find((el) =>
          (el.textContent || "").includes("Condition")
        );
      if (trigger) {
        trigger.click();
        window.setTimeout(() => {
          const opt = Array.from(document.querySelectorAll('[role="option"]')).find(
            (el) => (el.textContent || "").toLowerCase().includes(want)
          );
          if (opt) opt.click();
        }, 250);
        return true;
      }
    }
    return false;
  }

  function applyListing(bundle) {
    const listing = bundle?.listings?.[DETECTED_SERVICE];
    if (!listing) {
      alert(`No '${DETECTED_SERVICE}' listing found inside listing_bundle.json.`);
      return;
    }

    if (DETECTED_SERVICE === "craigslist") {
      fillFirst(['input[name="PostingTitle"]', "#PostingTitle"], listing.title);
      fillFirst(['input[name="price"]', 'input[name="Ask"]'], String(listing.price));
      fillFirst(['input[name="postal"]', "#postal_code"], listing.postal_code);
      fillFirst(['textarea[name="PostingBody"]', "#PostingBody"], listing.description);
    } else if (DETECTED_SERVICE === "facebook") {
      fillFirst(['input[aria-label="Title"]'], listing.title);
      fillFirst(['input[aria-label="Price"]'], String(listing.price));
      fillFirst(['input[aria-label="Location"]'], listing.location);
      fillFirst(['textarea[aria-label="Description"]'], listing.description);
    } else if (DETECTED_SERVICE === "freecycle") {
      fillFirst(['input[name="subject"]', 'input[name="title"]'], listing.title);
      fillFirst(['input[name="location"]', 'input[name="postcode"]'], listing.location);
      fillFirst(['textarea[name="description"]', 'textarea[name="body"]', "textarea"], listing.description);
    }

    selectConditionDropdown(DETECTED_SERVICE, listing);

    const photoCount = (listing.photos || []).length;
    const albumDir = bundle?.album?.album_dir || "";
    const extraInfo = [
      listing.model_number ? `Model: ${listing.model_number}` : "",
      listing.condition_dropdown ? `Condition: ${listing.condition_dropdown}` : "",
      listing.price_suggestion?.recommended_price != null
        ? `Suggested: $${listing.price_suggestion.recommended_price}`
        : "",
    ]
      .filter(Boolean)
      .join(" | ");
    statusEl.textContent =
      `Filled ${listing.display_name}${extraInfo ? " (" + extraInfo + ")" : ""}. ` +
      `Upload ${photoCount} EXIF-stripped photo(s) from: ${albumDir}/photos`;
  }

  const container = document.createElement("div");
  container.style.cssText = [
    "position:fixed",
    "bottom:16px",
    "right:16px",
    "z-index:999999",
    "background:#18181b",
    "color:#f4f4f5",
    "border:1px solid #3f3f46",
    "border-radius:10px",
    "padding:10px 12px",
    "font-family:system-ui,sans-serif",
    "font-size:12px",
    "max-width:300px",
    "box-shadow:0 8px 24px rgba(0,0,0,0.4)",
  ].join(";");

  const header = document.createElement("div");
  header.textContent = `Sales Autofill (${DETECTED_SERVICE})`;
  header.style.cssText = "font-weight:700;margin-bottom:6px;";
  container.appendChild(header);

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "application/json,.json";
  fileInput.style.cssText = "display:block;width:100%;margin-bottom:6px;font-size:11px;";
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    try {
      const bundle = JSON.parse(await file.text());
      applyListing(bundle);
    } catch (err) {
      statusEl.textContent = `Invalid JSON: ${err.message}`;
    }
  });
  container.appendChild(fileInput);

  const statusEl = document.createElement("div");
  statusEl.textContent = "Select listing_bundle.json to autofill this form.";
  statusEl.style.cssText = "opacity:0.85;word-break:break-word;";
  container.appendChild(statusEl);

  document.body.appendChild(container);
})();
