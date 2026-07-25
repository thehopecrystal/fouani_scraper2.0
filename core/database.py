"""
database.py
SQLite persistence layer. Handles:
 - discovered product URL queue (for resumable crawling)
 - scraped product records (deduplicated by SKU / URL / name+brand)
 - downloaded image tracking (dedup by source URL and by file hash)
 - simple key/value run-state table so a scrape can be paused/resumed/stopped
   and picked back up from the last successfully processed product.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime


SCHEMA = """
CREATE TABLE IF NOT EXISTS product_queue (
    url TEXT PRIMARY KEY,
    category TEXT,
    status TEXT DEFAULT 'pending',   -- pending | done | error
    discovered_at TEXT,
    processed_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT UNIQUE,
    sku TEXT,
    name TEXT,
    brand TEXT,
    model TEXT,
    regular_price TEXT,
    sale_price TEXT,
    currency TEXT,
    stock_status TEXT,
    stock_quantity TEXT,
    categories TEXT,
    short_description TEXT,
    full_description TEXT,
    attributes_json TEXT,     -- structured spec table as JSON
    variants_json TEXT,       -- variant list as JSON
    images_json TEXT,         -- list of {url, local_path, is_featured}
    raw_json TEXT,            -- full extracted payload, for debugging / re-export
    scraped_at TEXT
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_sku TEXT,
    source_url TEXT UNIQUE,
    local_path TEXT,
    downloaded_at TEXT
);

CREATE TABLE IF NOT EXISTS run_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def cursor(self):
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---------------- queue / resume ----------------

    def enqueue_urls(self, urls, category=""):
        now = datetime.utcnow().isoformat()
        with self.cursor() as cur:
            for u in urls:
                cur.execute(
                    "INSERT OR IGNORE INTO product_queue (url, category, status, discovered_at) "
                    "VALUES (?, ?, 'pending', ?)",
                    (u, category, now),
                )

    def pending_urls(self):
        with self.cursor() as cur:
            cur.execute("SELECT url, category FROM product_queue WHERE status = 'pending'")
            return cur.fetchall()

    def mark_done(self, url):
        with self.cursor() as cur:
            cur.execute(
                "UPDATE product_queue SET status='done', processed_at=? WHERE url=?",
                (datetime.utcnow().isoformat(), url),
            )

    def mark_error(self, url, error):
        with self.cursor() as cur:
            cur.execute(
                "UPDATE product_queue SET status='error', error=?, processed_at=? WHERE url=?",
                (str(error)[:500], datetime.utcnow().isoformat(), url),
            )

    def queue_counts(self):
        with self.cursor() as cur:
            cur.execute("SELECT status, COUNT(*) FROM product_queue GROUP BY status")
            return dict(cur.fetchall())

    def set_state(self, key, value):
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO run_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def get_state(self, key, default=None):
        with self.cursor() as cur:
            cur.execute("SELECT value FROM run_state WHERE key=?", (key,))
            row = cur.fetchone()
            return json.loads(row[0]) if row else default

    # ---------------- products ----------------

    def is_duplicate(self, sku, url, name, brand):
        with self.cursor() as cur:
            cur.execute(
                "SELECT id FROM products WHERE (sku != '' AND sku = ?) OR source_url = ? "
                "OR (name = ? AND brand = ?)",
                (sku or "", url or "", name or "", brand or ""),
            )
            return cur.fetchone() is not None

    def upsert_product(self, product: dict):
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO products (
                    source_url, sku, name, brand, model, regular_price, sale_price,
                    currency, stock_status, stock_quantity, categories,
                    short_description, full_description, attributes_json,
                    variants_json, images_json, raw_json, scraped_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_url) DO UPDATE SET
                    sku=excluded.sku, name=excluded.name, brand=excluded.brand,
                    model=excluded.model, regular_price=excluded.regular_price,
                    sale_price=excluded.sale_price, currency=excluded.currency,
                    stock_status=excluded.stock_status, stock_quantity=excluded.stock_quantity,
                    categories=excluded.categories, short_description=excluded.short_description,
                    full_description=excluded.full_description, attributes_json=excluded.attributes_json,
                    variants_json=excluded.variants_json, images_json=excluded.images_json,
                    raw_json=excluded.raw_json, scraped_at=excluded.scraped_at
                """,
                (
                    product.get("source_url"),
                    product.get("sku", ""),
                    product.get("name", ""),
                    product.get("brand", ""),
                    product.get("model", ""),
                    str(product.get("regular_price", "")),
                    str(product.get("sale_price", "")),
                    product.get("currency", "NGN"),
                    product.get("stock_status", ""),
                    str(product.get("stock_quantity", "")),
                    json.dumps(product.get("categories", [])),
                    product.get("short_description", ""),
                    product.get("full_description", ""),
                    json.dumps(product.get("attributes", {})),
                    json.dumps(product.get("variants", [])),
                    json.dumps(product.get("images", [])),
                    json.dumps(product, default=str),
                    datetime.utcnow().isoformat(),
                ),
            )

    def all_products(self):
        with self.cursor() as cur:
            cur.execute("SELECT raw_json FROM products ORDER BY id")
            return [json.loads(r[0]) for r in cur.fetchall()]

    def product_count(self):
        with self.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products")
            return cur.fetchone()[0]

    # ---------------- images ----------------

    def image_already_downloaded(self, source_url):
        with self.cursor() as cur:
            cur.execute("SELECT local_path FROM images WHERE source_url=?", (source_url,))
            row = cur.fetchone()
            return row[0] if row else None

    def record_image(self, product_sku, source_url, local_path):
        with self.cursor() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO images (product_sku, source_url, local_path, downloaded_at) "
                "VALUES (?, ?, ?, ?)",
                (product_sku, source_url, local_path, datetime.utcnow().isoformat()),
            )

    def image_count(self):
        with self.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM images")
            return cur.fetchone()[0]

    def close(self):
        self._conn.close()
