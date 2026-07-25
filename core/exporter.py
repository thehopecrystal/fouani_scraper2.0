"""
exporter.py
Writes scraped products to a WooCommerce-import-compatible CSV and/or a
plain JSON backup. SQLite is handled directly by database.py (it's already
the source of truth during a run); this module reads from it for the final
export pass.
"""

import csv
import json
import os
from datetime import datetime

WC_CSV_COLUMNS = [
    "Name", "SKU", "Regular price", "Sale price", "Description",
    "Short description", "Categories", "Images", "Brand", "In stock?",
    "Stock", "Attribute 1 name", "Attribute 1 value(s)",
    "Attribute 2 name", "Attribute 2 value(s)", "Attribute 3 name",
    "Attribute 3 value(s)", "Published", "Visibility in catalog",
    "Tax status", "Tax class", "Weight (kg)", "Length (cm)",
    "Width (cm)", "Height (cm)", "Type",
]


def _attrs_to_columns(attributes: dict):
    """Flatten the first 3 attributes into WooCommerce's Attribute N columns."""
    cols = {}
    for i, (k, v) in enumerate(list(attributes.items())[:3], start=1):
        cols[f"Attribute {i} name"] = k
        cols[f"Attribute {i} value(s)"] = v
    return cols


def _dimensions(attributes: dict):
    dims = attributes.get("Dimensions", "")
    length = width = height = ""
    if dims:
        parts = [p.strip() for p in dims.lower().replace("cm", "").replace("mm", "").split("x")]
        if len(parts) >= 2:
            length, width = parts[0], parts[1]
        if len(parts) >= 3:
            height = parts[2]
    return length, width, height


def product_to_wc_row(product: dict) -> dict:
    attributes = product.get("attributes") or {}
    if isinstance(attributes, str):
        attributes = json.loads(attributes or "{}")
    categories = product.get("categories") or []
    if isinstance(categories, str):
        categories = json.loads(categories or "[]")
    images = product.get("images") or []
    if isinstance(images, str):
        images = json.loads(images or "[]")

    image_urls = ", ".join(img.get("url", "") for img in images if img.get("url"))
    variants = product.get("variants") or []
    if isinstance(variants, str):
        variants = json.loads(variants or "[]")
    product_type = "variable" if variants else "simple"

    length, width, height = _dimensions(attributes)

    row = {
        "Name": product.get("name", ""),
        "SKU": product.get("sku", ""),
        "Regular price": product.get("regular_price", ""),
        "Sale price": product.get("sale_price", ""),
        "Description": product.get("full_description", ""),
        "Short description": product.get("short_description", ""),
        "Categories": ", ".join(categories),
        "Images": image_urls,
        "Brand": product.get("brand", ""),
        "In stock?": "1" if product.get("stock_status") == "instock" else "0",
        "Stock": product.get("stock_quantity", ""),
        "Published": "1",
        "Visibility in catalog": "visible",
        "Tax status": "taxable",
        "Tax class": "",
        "Weight (kg)": attributes.get("Weight", "").replace("kg", "").replace("KG", "").strip(),
        "Length (cm)": length,
        "Width (cm)": width,
        "Height (cm)": height,
        "Type": product_type,
    }
    row.update(_attrs_to_columns(attributes))
    return row


def export_csv(products, csv_folder, filename=None) -> str:
    os.makedirs(csv_folder, exist_ok=True)
    filename = filename or f"fouani_woocommerce_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path = os.path.join(csv_folder, filename)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=WC_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for p in products:
            writer.writerow(product_to_wc_row(p))
    return path


def export_json(products, json_folder, filename=None) -> str:
    os.makedirs(json_folder, exist_ok=True)
    filename = filename or f"fouani_products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    path = os.path.join(json_folder, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False, default=str)
    return path
