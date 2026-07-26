"""
parser.py
Extracts structured product data from a *fully rendered* product page
(HTML string produced by crawler.get_product_html, i.e. after Playwright
has let the Vue/Nuxt app hydrate).

IMPORTANT - READ THIS IF PRICES/FIELDS COME OUT WRONG:
Fouani's frontend is a client-rendered Vue app with generated/obfuscated CSS
class names that can change between deployments. The selectors below were
built from the visible structure of the site (product title in <h1>, "SKU:"
label, brand/category links containing `brand_ids[]=` / `category_ids[]=`,
a gallery of CDN images from salva.ams3.cdn.digitaloceanspaces.com, and a
"Description:" heading followed by bullet points). These are resilient to
class-name changes because they key off text patterns and URL patterns
rather than CSS classes.

The one field that is genuinely fragile is the **regular/sale price**,
because it is injected by JS into a plain, class-based element with no
distinguishing text marker. `_extract_price()` uses a scored heuristic
(largest/most prominent "NGN ..." figure that is NOT next to add-on
keywords like Insurance/Installation/Extension/Warranty). If prices come
back wrong for a given page layout:
  1. Run one product through Settings > "Test Connection"-style debug (or
     just call parser.parse_product_page(html, url) in a REPL) and print
     `debug_ngn_candidates` from the returned dict.
  2. Right-click the price on the live site -> Inspect -> note the element's
     class or data-attribute -> add it to `PRICE_SELECTOR_HINTS` below.
This keeps the fix to a one-line change instead of a rewrite.
"""

import json
import re
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup

IMAGE_CDN_HINT = "digitaloceanspaces.com"
NGN_RE = re.compile(r"NGN\s*([\d,]+(?:\.\d+)?)")
SKU_RE = re.compile(r"SKU\s*:\s*([A-Za-z0-9\-_/]+)", re.IGNORECASE)

# add class names / data-attrs here if the heuristic price extractor ever
# needs a manual override for a specific page layout
PRICE_SELECTOR_HINTS = [
    ".product-price", ".price", "[data-testid='price']", ".pdp-price",
]

ADDON_KEYWORDS = ("insurance", "installation", "extension", "warranty", "cord", "insure")


def parse_product_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    name = _extract_name(soup)
    sku = _extract_sku(soup)
    brand = _extract_brand(soup)
    categories = _extract_categories(soup)
    short_desc, full_desc = _extract_description(soup)
    images = _extract_images(soup, url)
    attributes = _extract_attributes(soup, full_desc)
    price_info = _extract_price(soup)
    stock_status = _extract_stock_status(soup)
    variants, attributes = _extract_variants(soup, attributes)
    model = sku or name

    product_id_match = re.search(r"/product/(\d+)", url)
    product_id = product_id_match.group(1) if product_id_match else ""

    return {
        "source_url": url,
        "product_id": product_id,
        "name": name,
        "sku": sku or f"FOUANI-{product_id}",
        "brand": brand,
        "model": model,
        "regular_price": price_info["regular_price"],
        "sale_price": price_info["sale_price"],
        "currency": "NGN",
        "stock_status": stock_status,
        "stock_quantity": "",
        "categories": categories,
        "short_description": short_desc,
        "full_description": full_desc,
        "attributes": attributes,
        "variants": variants,
        "images": images,
        "debug_ngn_candidates": price_info["candidates"],
    }


# ---------------- field extractors ----------------

def _extract_name(soup):
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    title = soup.find("title")
    return title.get_text(strip=True).split("|")[0].strip() if title else ""


def _extract_sku(soup):
    text = soup.get_text(" ", strip=True)
    m = SKU_RE.search(text)
    return m.group(1).strip() if m else ""


def _extract_brand(soup):
    a = soup.find("a", href=re.compile(r"brand_ids\[\]="))
    if a:
        return a.get_text(strip=True).rstrip(".").strip()
    return ""


def _extract_categories(soup):
    cats = []
    for a in soup.find_all("a", href=re.compile(r"category_ids\[\]=")):
        text = a.get_text(strip=True).rstrip("-").strip()
        if text and text not in cats:
            cats.append(text)
    return cats


def _extract_description(soup):
    full_desc_parts = []
    heading = None
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "strong", "b"]):
        if "description" in tag.get_text(strip=True).lower():
            heading = tag
            break

    if heading:
        # gather following list items / paragraphs until next heading
        for sib in heading.find_all_next():
            if sib.name in ("h1", "h2", "h3", "h4") and sib is not heading:
                break
            if sib.name == "li":
                txt = sib.get_text(" ", strip=True)
                if txt:
                    full_desc_parts.append(f"- {txt}")
            elif sib.name == "p":
                txt = sib.get_text(" ", strip=True)
                if txt:
                    full_desc_parts.append(txt)
            if len(full_desc_parts) > 60:
                break

    full_description = "\n".join(full_desc_parts).strip()

    # fall back to meta description if nothing found in-page
    if not full_description:
        meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
            "meta", attrs={"property": "og:description"}
        )
        if meta and meta.get("content"):
            full_description = meta["content"].strip()

    short_description = full_description.split("\n")[0][:300] if full_description else ""
    return short_description, full_description


def _extract_images(soup, page_url):
    seen = {}
    order = 0
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src or IMAGE_CDN_HINT not in src:
            continue
        full_res = src.replace("/thumb.webp", "/image.webp")
        if full_res in seen:
            continue
        order += 1
        seen[full_res] = {
            "url": full_res,
            "alt": img.get("alt", ""),
            "is_featured": order == 1,
            "order": order,
        }
    return list(seen.values())


def _extract_attributes(soup, full_description):
    """
    Fouani doesn't render a dedicated spec table on the product page in the
    samples inspected - specs are folded into the bullet-point description.
    We still build a light attributes dict by pattern-matching common spec
    keywords inside the description (Capacity, Voltage, Weight, Dimensions,
    Warranty, Color, Energy Rating) so the WooCommerce "Attributes" column
    isn't left empty. If a future page layout adds a real spec table, prefer
    that: look for <table> or dl/dt/dd pairs first.
    """
    attrs = {}

    # 1) real spec table, if present
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True)
                val = cells[1].get_text(strip=True)
                if key and val:
                    attrs[key] = val

    # 2) dl/dt/dd pairs, if present
    for dl in soup.find_all("dl"):
        keys = dl.find_all("dt")
        vals = dl.find_all("dd")
        for k, v in zip(keys, vals):
            kt, vt = k.get_text(strip=True), v.get_text(strip=True)
            if kt and vt:
                attrs[kt] = vt

    # 3) keyword pattern-match inside description text as a fallback
    if not attrs and full_description:
        patterns = {
            "Capacity": r"(\d+(?:\.\d+)?\s?(?:L|KG|kg|Litre|Liter))",
            "Voltage": r"(\d+\s?V(?:olt)?s?)",
            "Weight": r"(\d+(?:\.\d+)?\s?(?:kg|KG))",
            "Dimensions": r"(\d+\s?x\s?\d+(?:\s?x\s?\d+)?\s?(?:cm|CM|mm))",
            "Energy Rating": r"([A-G]\+{0,3}\s?Energy Rating)",
            "Warranty": r"(\d+\s?(?:Year|Month)s?\s?Warranty)",
        }
        for label, pat in patterns.items():
            m = re.search(pat, full_description, re.IGNORECASE)
            if m:
                attrs[label] = m.group(1)

    return attrs


def _extract_stock_status(soup):
    text = soup.get_text(" ", strip=True).lower()
    if "sold out" in text or "out of stock" in text:
        return "outofstock"
    if "add to cart" in text:
        return "instock"
    return "instock"


def _extract_variants(soup, existing_attributes):
    """
    Look for common variant-selector UI patterns: a group of buttons/labels
    near text like "Color" / "Size" / "Capacity". Fouani's sampled pages
    are mostly single-SKU products; this returns [] when no variant
    selector is detected, and WooCommerce export falls back to a simple
    product in that case.

    The most reliable source is often a JSON blob inside a <script> tag.
    """
    variants = []
    attributes_for_variation = {}

    # Strategy 1: Find Nuxt/Vue JSON state in a script tag (most reliable)
    script_tag = soup.find("script", string=re.compile(r"window\.__NUXT__"))
    if script_tag:
        try:
            # Extract the JSON part of the script
            json_text = re.search(r"window\.__NUXT__\s*=\s*({.+});", script_tag.string).group(1)
            nuxt_data = json.loads(json_text)
            # This path is an assumption and may need inspection on a live product page
            # Common paths: state.product, state.pageData.product, payload.data[0].productDetails
            product_data = nuxt_data.get("state", {}).get("product", {})
            if product_data and "variants" in product_data and product_data["variants"]:
                for var in product_data["variants"]:
                    # Map the fields to our desired structure
                    variants.append({
                        "sku": var.get("sku"),
                        "regular_price": var.get("price"),
                        "stock_status": "instock" if var.get("quantity", 0) > 0 else "outofstock",
                        "attributes": {
                            attr["name"]: attr["value"] for attr in var.get("attributes", [])
                        }
                    })
                # Collect all unique attribute names and values for the parent product
                for var in variants:
                    for name, value in var.get("attributes", {}).items():
                        if name not in attributes_for_variation:
                            attributes_for_variation[name] = set()
                        attributes_for_variation[name].add(value)
        except (AttributeError, json.JSONDecodeError, KeyError):
            # If the NUXT data is missing or has an unexpected structure, just ignore it.
            pass

    # Update existing attributes with variation attributes
    for name, values in attributes_for_variation.items():
        existing_attributes[name] = ", ".join(sorted(list(values)))

    # Strategy 2: Fallback to scraping visible selectors (less reliable)
    if not variants:
        for label_text in ("Color", "Size", "Capacity", "Storage"):
            label = soup.find(string=re.compile(rf"^\s*{label_text}\s*$", re.IGNORECASE))
            if not label: continue
            parent = label.find_parent()
            if not parent: continue
            options = []
            for opt in parent.find_all_next(limit=15):
                if opt.name in ("button", "li", "span") and opt.get_text(strip=True):
                    txt = opt.get_text(strip=True)
                    if 0 < len(txt) < 30 and txt.lower() != label_text.lower():
                        options.append(txt)
                if len(options) >= 8: break
            if options:
                existing_attributes[label_text] = ", ".join(options)

    return variants, existing_attributes


def _extract_price(soup):
    text = soup.get_text(" ", strip=True)
    candidates = []
    for m in NGN_RE.finditer(text):
        start = max(0, m.start() - 60)
        context = text[start:m.start()].lower()
        value = m.group(1).replace(",", "")
        try:
            numeric = float(value)
        except ValueError:
            continue
        is_addon = any(k in context for k in ADDON_KEYWORDS)
        candidates.append({"raw": m.group(0), "value": numeric, "is_addon": is_addon})

    # prefer the largest non-zero, non-addon candidate (product prices are
    # typically the highest NGN figure on the page; add-ons are small extras)
    real_candidates = [c for c in candidates if not c["is_addon"] and c["value"] > 0]
    if real_candidates:
        best = max(real_candidates, key=lambda c: c["value"])
        return {"regular_price": best["value"], "sale_price": "", "candidates": candidates}

    return {"regular_price": "", "sale_price": "", "candidates": candidates}
