"""
image_downloader.py
Concurrent image downloader with:
 - skip-if-already-downloaded (via DB source_url lookup, and skip_existing_images
   checking the file already on disk)
 - intelligent, collision-safe filenames derived from the product SKU/name
 - retry on failure
 - optional max-resolution downscale (Pillow)
"""

import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from utils.logger import log_download

SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9\-_.]+")


def safe_filename(base_name: str, index: int, ext: str) -> str:
    base = SAFE_CHARS_RE.sub("-", base_name).strip("-")[:80] or "product"
    suffix = "" if index == 1 else f"-{index}"
    return f"{base}{suffix}.{ext}"


class ImageDownloader:
    def __init__(self, config, db, logger, control):
        self.config = config
        self.db = db
        self.logger = logger
        self.control = control
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.user_agent})

    def download_product_images(self, product: dict) -> list:
        """
        Downloads every image listed in product['images'], returns the updated
        image list with 'local_path' filled in. Runs sequentially per-product
        but products themselves are processed with a thread pool in the worker.
        """
        if not self.config.download_images:
            return product.get("images", [])

        sku = product.get("sku") or product.get("product_id") or "product"
        product_folder = os.path.join(self.config.images_folder)
        os.makedirs(product_folder, exist_ok=True)

        updated_images = []
        for idx, img in enumerate(product.get("images", []), start=1):
            if self.control.should_stop():
                break
            self.control.wait_if_paused()

            url = img["url"]
            existing = self.db.image_already_downloaded(url)
            ext = self._guess_ext(url)
            filename = safe_filename(sku, idx, ext)
            local_path = os.path.join(product_folder, filename)

            if existing and os.path.exists(existing):
                log_download(self.logger, f"Skipping duplicate image (already downloaded): {url}")
                img["local_path"] = existing
                updated_images.append(img)
                continue

            if self.config.skip_existing_images and os.path.exists(local_path):
                log_download(self.logger, f"Skipping existing file: {filename}")
                img["local_path"] = local_path
                self.db.record_image(sku, url, local_path)
                updated_images.append(img)
                continue

            ok = self._download_one(url, local_path)
            if ok:
                log_download(self.logger, f"Downloaded image: {filename}")
                img["local_path"] = local_path
                self.db.record_image(sku, url, local_path)
            else:
                log_download(self.logger, f"FAILED to download image: {url}", level=40)
                img["local_path"] = ""
            updated_images.append(img)

        return updated_images

    def _download_one(self, url, dest_path) -> bool:
        for attempt in range(1, self.config.retry_count + 1):
            try:
                resp = self.session.get(url, timeout=self.config.timeout_seconds, stream=True)
                resp.raise_for_status()
                tmp_path = dest_path + ".part"
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)
                self._maybe_resize(tmp_path)
                os.replace(tmp_path, dest_path)
                return True
            except Exception as e:
                self.logger.warning(f"[Attempt {attempt}/{self.config.retry_count}] Image download failed ({url}): {e}")
                time.sleep(1.0 * attempt)
        return False

    def _maybe_resize(self, path):
        if not self.config.max_image_resolution:
            return
        try:
            from PIL import Image
            with Image.open(path) as im:
                max_dim = self.config.max_image_resolution
                if max(im.size) > max_dim:
                    im.thumbnail((max_dim, max_dim))
                    im.save(path)
        except Exception as e:
            self.logger.warning(f"Could not resize image {path}: {e}")

    @staticmethod
    def _guess_ext(url):
        path = url.split("?")[0]
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        return ext if ext in ("jpg", "jpeg", "png", "webp", "gif") else "jpg"
