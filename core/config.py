"""
config.py
Central configuration for the Fouani -> WooCommerce scraper.
Everything is loaded from / saved to config.json in the project root.
No credentials or paths are hardcoded anywhere else in the app.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import List

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")

DEFAULT_CATEGORIES = [
    {"name": "Promotions", "category_id": 114},
    {"name": "Refrigerator", "category_id": 15},
    {"name": "Freezer", "category_id": 174},
    {"name": "Washing Machines", "category_id": 18},
    {"name": "TVs", "category_id": 5},
    {"name": "Audio", "category_id": 9},
    {"name": "ACs", "category_id": 44},
    {"name": "Cookers/Microwave", "category_id": 177},
    {"name": "Small Appliances/Fans", "category_id": 49},
    {"name": "Power Solution", "category_id": 39},
    {"name": "Furniture", "category_id": 33},
    {"name": "Others", "category_id": 183},
]


@dataclass
class WooCommerceConfig:
    store_url: str = ""
    consumer_key: str = ""
    consumer_secret: str = ""
    timeout_seconds: int = 60  # separate timeout for WC API
    verify_ssl: bool = True  # turn off for local dev sites with self-signed certs


@dataclass
class AppConfig:
    base_url: str = "https://fouanistore.com/nigeria-en"
    categories: List[dict] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))

    # networking / performance
    threads: int = 4
    timeout_seconds: int = 30
    retry_count: int = 3
    delay_between_requests_ms: int = 400
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    headless_browser: bool = True

    # images
    download_images: bool = True
    max_image_resolution: int = 0  # 0 = original / no resize
    skip_existing_images: bool = True

    # export
    export_csv: bool = True
    export_json: bool = True
    export_sqlite: bool = True
    woocommerce_sync: bool = False

    csv_folder: str = os.path.join(ROOT_DIR, "exports", "csv")
    json_folder: str = os.path.join(ROOT_DIR, "exports", "json")
    db_path: str = os.path.join(ROOT_DIR, "exports", "database", "fouani_products.db")
    images_folder: str = os.path.join(ROOT_DIR, "downloads", "images")
    logs_folder: str = os.path.join(ROOT_DIR, "logs")

    woocommerce: WooCommerceConfig = field(default_factory=WooCommerceConfig)

    def save(self, path: str = CONFIG_PATH):
        data = asdict(self)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str = CONFIG_PATH) -> "AppConfig":
        if not os.path.exists(path):
            cfg = cls()
            cfg.save(path)
            return cfg
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        wc_raw = raw.pop("woocommerce", {}) or {}
        cfg = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        # handle legacy configs without the new timeout field
        wc_fields = WooCommerceConfig.__dataclass_fields__
        wc_args = {k: v for k, v in wc_raw.items() if k in wc_fields}
        cfg.woocommerce = WooCommerceConfig(**wc_args)
        return cfg

    def ensure_dirs(self):
        for d in [self.csv_folder, self.json_folder, self.images_folder,
                  self.logs_folder, os.path.dirname(self.db_path)]:
            os.makedirs(d, exist_ok=True)
