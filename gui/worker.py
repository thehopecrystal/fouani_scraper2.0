"""
worker.py
Runs the whole scrape pipeline (discover -> scrape product -> download
images -> DB upsert -> optional WooCommerce sync) on a background QThread
so the GUI never freezes. Supports pause/resume/stop via ScrapeControl,
and is resumable: on start it re-enqueues only categories that were asked
for, but re-uses the existing product_queue table so previously 'done'
URLs are skipped automatically.
"""

import threading
import time
import traceback

from PySide6.QtCore import QThread, Signal

from core.crawler import Crawler, StopRequested
from core.parser import parse_product_page
from core.image_downloader import ImageDownloader
from core.exporter import export_csv, export_json
from core.woocommerce import WooCommerceClient, WooCommerceError


class ScrapeControl:
    """Shared pause/stop signal object, safe to poll from a worker thread."""

    def __init__(self):
        self._stop = threading.Event()
        self._paused = threading.Event()  # set == paused

    def stop(self):
        self._stop.set()
        self._paused.clear()

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def should_stop(self):
        return self._stop.is_set()

    def is_paused(self):
        return self._paused.is_set()

    def wait_if_paused(self):
        while self._paused.is_set() and not self._stop.is_set():
            time.sleep(0.2)

    def reset(self):
        self._stop.clear()
        self._paused.clear()


class ScrapeWorker(QThread):
    log = Signal(str, str)                 # level, message
    progress = Signal(dict)                # counters dict
    finished_ok = Signal(dict)             # summary
    failed = Signal(str)

    def __init__(self, config, db, logger, control: ScrapeControl,
                 selected_categories, mode="full"):
        """
        mode: "scan" (discover URLs only, no product scraping)
              "full" (discover + scrape + download + export)
        """
        super().__init__()
        self.config = config
        self.db = db
        self.logger = logger
        self.control = control
        self.selected_categories = selected_categories
        self.mode = mode
        self._counters = {
            "products_found": 0,
            "products_processed": 0,
            "images_downloaded": 0,
            "products_synced": 0,
            "errors": 0,
            "start_time": None,
        }

    def _emit_progress(self):
        self.progress.emit(dict(self._counters))

    def run(self):
        self._counters["start_time"] = time.time()
        crawler = Crawler(self.config, self.logger, self.control)
        try:
            crawler.start()
        except Exception as e:
            self.failed.emit(f"Could not start browser engine (Playwright). "
                              f"Did you run 'playwright install chromium'? Details: {e}")
            return

        try:
            self._discover(crawler)
            if self.mode == "scan":
                self.finished_ok.emit({"mode": "scan", **self._counters})
                return

            self._scrape_products(crawler)
            summary = self._export()
            self.finished_ok.emit({"mode": "full", **self._counters, **summary})

        except StopRequested:
            self.logger.info("Scrape stopped by user.")
            self.finished_ok.emit({"mode": "stopped", **self._counters})
        except Exception as e:
            self.logger.error(f"Fatal error: {e}\n{traceback.format_exc()}")
            self.failed.emit(str(e))
        finally:
            crawler.stop()

    # ---------------- pipeline stages ----------------

    def _discover(self, crawler):
        self.logger.info(f"Starting discovery for {len(self.selected_categories)} categories.")
        for cat in self.selected_categories:
            if self.control.should_stop():
                raise StopRequested()
            self.control.wait_if_paused()
            name, cat_id = cat["name"], cat["category_id"]
            self.logger.info(f"Scanning category: {name}")

            def on_page(cat_name, page_num, total_found):
                self._counters["products_found"] = self.db.queue_counts().get("pending", 0) + total_found
                self._emit_progress()

            urls = crawler.discover_category(name, cat_id, on_page_scanned=on_page)
            self.db.enqueue_urls(urls, category=name)
            self.logger.info(f"Category '{name}': {len(urls)} product URLs discovered.")

        counts = self.db.queue_counts()
        self._counters["products_found"] = sum(counts.values())
        self._emit_progress()

    def _scrape_products(self, crawler):
        downloader = ImageDownloader(self.config, self.db, self.logger, self.control)
        pending = self.db.pending_urls()
        self.logger.info(f"Scraping {len(pending)} pending products (resumed from previous run if any).")

        for url, category in pending:
            if self.control.should_stop():
                raise StopRequested()
            self.control.wait_if_paused()

            try:
                html = crawler.get_product_html(url)
                product = parse_product_page(html, url)
                product["categories"] = product.get("categories") or ([category] if category else [])

                if self.db.is_duplicate(product.get("sku"), url, product.get("name"), product.get("brand")):
                    self.logger.info(f"Duplicate skipped: {product.get('name')} ({product.get('sku')})")
                else:
                    product["images"] = downloader.download_product_images(product)
                    self._counters["images_downloaded"] = self.db.image_count()

                self.db.upsert_product(product)
                self.db.mark_done(url)
                self._counters["products_processed"] += 1
                self.logger.info(f"Scraped: {product.get('name')} [{product.get('sku')}]")

            except Exception as e:
                self._counters["errors"] += 1
                self.db.mark_error(url, e)
                self.logger.error(f"Error scraping {url}: {e}")

            self._emit_progress()

    def _export(self):
        products = self.db.all_products()
        summary = {"csv_path": "", "json_path": ""}

        if self.config.export_csv:
            summary["csv_path"] = export_csv(products, self.config.csv_folder)
            self.logger.info(f"WooCommerce CSV written: {summary['csv_path']}")

        if self.config.export_json:
            summary["json_path"] = export_json(products, self.config.json_folder)
            self.logger.info(f"JSON backup written: {summary['json_path']}")

        if self.config.woocommerce_sync:
            self._sync_woocommerce(products)

        return summary

    def _sync_woocommerce(self, products):
        wc = self.config.woocommerce
        if not (wc.store_url and wc.consumer_key and wc.consumer_secret):
            self.logger.error("WooCommerce sync enabled but credentials are incomplete - skipping sync.")
            return
        client = WooCommerceClient(wc.store_url, wc.consumer_key, wc.consumer_secret, logger=self.logger,
                                    timeout=wc.timeout_seconds, verify_ssl=wc.verify_ssl)
        ok, msg = client.test_connection()
        if not ok:
            self.logger.error(f"WooCommerce connection failed, aborting sync: {msg}")
            return

        self.logger.info(f"Syncing {len(products)} products to WooCommerce...")
        for p in products:
            if self.control.should_stop():
                raise StopRequested()
            self.control.wait_if_paused()
            try:
                client.upsert_product(p)
            except WooCommerceError as e:
                self._counters["errors"] += 1
                self.logger.error(str(e))
            self._counters["products_synced"] += 1
            self._emit_progress()
        self.logger.info("WooCommerce sync complete.")
