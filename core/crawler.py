"""
crawler.py
Handles talking to fouanistore.com with a real (headless) browser via Playwright.

Why Playwright instead of plain requests?
Fouani's storefront is a Nuxt/Vue app. The server-rendered HTML for category
and product pages does NOT include prices, stock status or variant data -
those are filled in client-side after the JS bundle runs. A requests+BeautifulSoup
scraper will silently produce empty prices. Playwright renders the page like a
real browser so `parser.py` can read the final DOM.

This module only handles navigation / pagination / raw HTML retrieval.
All field extraction lives in parser.py.
"""

import re
import time
from urllib.parse import urljoin, urlparse, parse_qs

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PRODUCT_URL_RE = re.compile(r"/nigeria-en/product/(\d+)")


class StopRequested(Exception):
    pass


class Crawler:
    def __init__(self, config, logger, control):
        """
        control: an object with .should_stop() -> bool and .wait_if_paused() -> None
                 (see gui/worker.py ScrapeControl)
        """
        self.config = config
        self.logger = logger
        self.control = control
        self._pw = None
        self._browser = None
        self._context = None

    # ---------------- lifecycle ----------------

    def start(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.config.headless_browser)
        self._context = self._browser.new_context(
            user_agent=self.config.user_agent,
            viewport={"width": 1400, "height": 1000},
        )
        self._context.set_default_timeout(self.config.timeout_seconds * 1000)

    def stop(self):
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception as e:
            self.logger.warning(f"Error shutting down browser: {e}")

    # ---------------- helpers ----------------

    def _check_control(self):
        if self.control.should_stop():
            raise StopRequested()
        self.control.wait_if_paused()

    def _get_page_html(self, url, wait_selector=None, extra_wait_ms=800):
        """Navigate to a URL and return fully-rendered HTML, with retries."""
        last_err = None
        for attempt in range(1, self.config.retry_count + 1):
            self._check_control()
            page = self._context.new_page()
            try:
                page.goto(url, wait_until="networkidle")
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=8000)
                    except PWTimeout:
                        pass
                # small settle delay for late client-side price/stock renders
                page.wait_for_timeout(extra_wait_ms)
                html = page.content()
                page.close()
                time.sleep(self.config.delay_between_requests_ms / 1000)
                return html
            except (PWTimeout, Exception) as e:
                last_err = e
                self.logger.warning(f"[Attempt {attempt}/{self.config.retry_count}] Failed to load {url}: {e}")
                try:
                    page.close()
                except Exception:
                    pass
                time.sleep(1.5 * attempt)
        raise RuntimeError(f"Giving up on {url} after {self.config.retry_count} attempts: {last_err}")

    # ---------------- discovery ----------------

    def discover_category(self, category_name, category_id, on_page_scanned=None):
        """
        Crawl every paginated page of a category, return the set of unique
        absolute product URLs found. Stops when a page yields zero new
        product links (end of pagination) or when 404 / no results.
        """
        found = set()
        base = self.config.base_url
        page_num = 1
        empty_streak = 0

        while True:
            self._check_control()
            url = f"{base}/shop?category_id={category_id}&category_name={category_name.lower().replace(' ', '-')}&page={page_num}"
            try:
                html = self._get_page_html(url, wait_selector="a[href*='/product/']")
            except RuntimeError as e:
                self.logger.error(f"Category '{category_name}' page {page_num}: {e}")
                break

            links = set(PRODUCT_URL_RE.findall(html))
            page_product_urls = self._extract_product_links(html, base)
            new_links = page_product_urls - found
            found |= page_product_urls

            self.logger.info(
                f"Category '{category_name}': page {page_num} scanned, "
                f"{len(page_product_urls)} products on page, {len(found)} unique so far."
            )
            if on_page_scanned:
                on_page_scanned(category_name, page_num, len(found))

            if not page_product_urls:
                empty_streak += 1
            else:
                empty_streak = 0

            if empty_streak >= 1 or not new_links and page_num > 1:
                # No new products found -> end of pagination
                if not page_product_urls:
                    break

            # detect max page number from pagination links if present, as a safety net
            max_page = self._max_pagination_page(html)
            if max_page and page_num >= max_page:
                break

            if not new_links and page_num > 1:
                break

            page_num += 1
            if page_num > 500:  # hard safety cap
                self.logger.warning(f"Category '{category_name}': hit 500-page safety cap, stopping.")
                break

        return found

    @staticmethod
    def _extract_product_links(html, base):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        links = set()
        for a in soup.select("a[href*='/product/']"):
            href = a.get("href")
            if not href:
                continue
            if not PRODUCT_URL_RE.search(href):
                continue
            full = urljoin(base, href)
            links.add(full)
        return links

    @staticmethod
    def _max_pagination_page(html):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        pages = []
        for a in soup.find_all("a", href=True):
            qs = parse_qs(urlparse(a["href"]).query)
            if "page" in qs:
                try:
                    pages.append(int(qs["page"][0]))
                except ValueError:
                    pass
        return max(pages) if pages else None

    # ---------------- product page ----------------

    def get_product_html(self, url):
        return self._get_page_html(url, wait_selector="h1", extra_wait_ms=1200)
