"""
woocommerce.py
Thin client around the WooCommerce REST API (v3) for direct-sync mode.

Auth strategy:
- HTTPS store  -> HTTP Basic Auth header (consumer_key / consumer_secret),
  the standard/recommended approach.
- HTTP store (e.g. a local WordPress test site at http://localhost:xxxx)
  -> WooCommerce's Basic Auth over plain HTTP is unreliable (some server
  configs strip the Authorization header, and WooCommerce's own docs say
  Basic Auth is only guaranteed over SSL). So for http:// URLs this client
  instead sends consumer_key/consumer_secret as query-string parameters,
  which WooCommerce accepts regardless of SSL. This makes local testing
  against a plain-HTTP WordPress install work out of the box.

Products are matched and deduplicated by SKU: if a product with the same
SKU exists it's updated, otherwise it's created.
"""

import json
import time
from urllib.parse import urlparse

import requests


class WooCommerceError(Exception):
    pass


class WooCommerceClient:
    def __init__(self, store_url: str, consumer_key: str, consumer_secret: str, logger=None,
                 timeout=30, verify_ssl=True):
        self.base = store_url.rstrip("/") + "/wp-json/wc/v3"
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.is_https = urlparse(store_url).scheme == "https"
        self.logger = logger
        self.timeout = timeout
        self.session = requests.Session()
        # local dev sites (Local by Flywheel, XAMPP+mkcert, etc.) sometimes use
        # self-signed/local CA certs that Python's default trust store won't
        # recognize; verify_ssl=False lets you test against those.
        self.verify_ssl = verify_ssl

    def _auth_kwargs(self):
        """Returns the requests kwargs needed for auth, based on scheme."""
        if self.is_https:
            return {"auth": (self.consumer_key, self.consumer_secret)}
        return {"params": {"consumer_key": self.consumer_key, "consumer_secret": self.consumer_secret}}

    def _log(self, msg, level="info"):
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    def _merge_params(self, extra_params=None):
        kwargs = self._auth_kwargs()
        if not self.is_https and extra_params:
            kwargs["params"].update(extra_params)
        elif extra_params:
            kwargs["params"] = extra_params
        return kwargs

    def test_connection(self):
        try:
            kwargs = self._merge_params({"per_page": 1})
            resp = self.session.get(f"{self.base}/products", timeout=self.timeout,
                                     verify=self.verify_ssl, **kwargs)
            if resp.status_code == 200:
                mode = "HTTPS Basic Auth" if self.is_https else "HTTP query-string auth (local/dev mode)"
                return True, f"Connected successfully using {mode}."
            if resp.status_code == 401:
                return False, ("HTTP 401 Unauthorized - check the Consumer Key/Secret and that the "
                               "key has Read/Write permissions.")
            return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
        except requests.exceptions.SSLError as e:
            return False, (f"SSL error - if this is a local dev site with a self-signed certificate, "
                            f"try the plain http:// URL instead. Details: {e}")
        except requests.exceptions.ConnectionError as e:
            return False, (f"Could not connect to {self.base} - is the local WordPress site running "
                            f"and is the URL/port correct? Details: {e}")
        except Exception as e:
            return False, str(e)

    def find_by_sku(self, sku: str):
        kwargs = self._merge_params({"sku": sku})
        resp = self.session.get(f"{self.base}/products", timeout=self.timeout,
                                 verify=self.verify_ssl, **kwargs)
        resp.raise_for_status()
        results = resp.json()
        return results[0] if results else None

    def upsert_product(self, product: dict, retry=3) -> dict:
        """Create or update (matched by SKU) a single WooCommerce product."""
        payload = self._to_wc_payload(product)
        sku = payload.get("sku")

        existing = self.find_by_sku(sku) if sku else None
        last_err = None
        for attempt in range(1, retry + 1):
            try:
                kwargs = self._merge_params()
                if existing:
                    resp = self.session.put(
                        f"{self.base}/products/{existing['id']}",
                        json=payload, timeout=self.timeout, verify=self.verify_ssl, **kwargs,
                    )
                else:
                    resp = self.session.post(
                        f"{self.base}/products",
                        json=payload, timeout=self.timeout, verify=self.verify_ssl, **kwargs,
                    )
                if resp.status_code in (200, 201):
                    self._log(f"WooCommerce {'updated' if existing else 'created'}: {payload.get('name')}")
                    return resp.json()
                last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
            except Exception as e:
                last_err = str(e)
            self._log(f"[Attempt {attempt}/{retry}] WooCommerce sync failed for SKU {sku}: {last_err}", "warning")
            time.sleep(1.5 * attempt)

        raise WooCommerceError(f"Failed to sync product SKU={sku}: {last_err}")

    @staticmethod
    def _to_wc_payload(product: dict) -> dict:
        attributes = product.get("attributes") or {}
        if isinstance(attributes, str):
            attributes = json.loads(attributes or "{}")
        categories = product.get("categories") or []
        if isinstance(categories, str):
            categories = json.loads(categories or "[]")
        images = product.get("images") or []
        if isinstance(images, str):
            images = json.loads(images or "[]")

        payload = {
            "name": product.get("name", ""),
            "sku": product.get("sku", ""),
            "type": "variable" if product.get("variants") else "simple",
            "regular_price": str(product.get("regular_price", "") or ""),
            "sale_price": str(product.get("sale_price", "") or ""),
            "description": product.get("full_description", ""),
            "short_description": product.get("short_description", ""),
            "categories": [{"name": c} for c in categories],
            "images": [{"src": img.get("url")} for img in images if img.get("url")],
            "manage_stock": False,
            "stock_status": product.get("stock_status", "instock"),
            "attributes": [
                {"name": k, "options": [v], "visible": True}
                for k, v in list(attributes.items())[:10]
            ],
        }
        brand = product.get("brand")
        if brand:
            payload["categories"].append({"name": brand})
        return payload
