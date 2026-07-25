# Fouani Store → WooCommerce Product Scraper

A desktop app (PySide6) that crawls https://fouanistore.com/nigeria-en, extracts
product data, downloads images, and exports a WooCommerce-ready CSV/JSON — with
an optional direct WooCommerce REST API sync.

## ⚠️ Read this first — why the engine is Playwright, not `requests`

Fouani's storefront is a client-rendered Vue/Nuxt app. I checked both a category
listing page and a product page: **the server-rendered HTML contains no prices**
— they're filled in by JavaScript after the page loads in a real browser. A
`requests + BeautifulSoup` scraper (the "simple" approach) would silently give
you every field except price.

So this app drives a real headless Chromium browser via **Playwright** to render
each page fully before extracting data. That's slower than raw HTTP requests but
it's the only approach that actually captures prices, stock status and any
JS-driven variant selectors reliably.

**One consequence:** I built and unit-tested this in a sandboxed environment
with no live network access, so the parsing logic (`core/parser.py`) has been
verified against the real page *structure* I fetched from the site (product
title, SKU label, brand/category links, image CDN pattern, description
formatting, pagination) but not against a live, fully-hydrated browser render.
The one field most likely to need a one-line tweak after your first real run is
price extraction — see the big comment at the top of `core/parser.py` for exactly
what to check and where to adjust it if `regular_price` comes out empty or wrong
for some product template.

## Setup

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
playwright install chromium  # downloads the headless browser binary
python main.py
```

## How to use

1. **Settings** → set threads, timeout, retry count, request delay, and (if you
   want image resizing) a max resolution. Point CSV/JSON/image/database folders
   wherever you like.
2. **Category Selection** → check the categories you want (or "All Categories").
3. **Export Options** → choose CSV / JSON / SQLite / image download / WooCommerce
   sync.
4. **Scan Website** → discovery-only pass: paginates every selected category and
   populates the resumable URL queue, without scraping product details yet.
   Good for a quick "how many products is this" check.
5. **Start Scraping** → discovers (if not already done) + visits every pending
   product URL + downloads images + writes to SQLite, then exports CSV/JSON and
   (if enabled) syncs to WooCommerce.
6. **Pause / Resume / Stop** work at any point. The crawl state lives in
   `exports/database/fouani_products.db` (`product_queue` table), so if you
   close the app mid-run, the *next* "Start Scraping" call picks up only the
   URLs still marked `pending` — it does not restart from zero.

## WooCommerce sync

Settings → WooCommerce tab. Needs a store URL (must be HTTPS) plus a REST API
Consumer Key/Secret (WooCommerce → Settings → Advanced → REST API → Add key,
with Read/Write permissions). "Test Connection" hits `/wp-json/wc/v3/products`.
Products are matched/deduplicated by SKU — an existing SKU gets updated, a new
one gets created.

## Duplicate detection

A product is treated as a duplicate (and skipped) if it matches an existing
record by: same SKU, OR same source URL, OR same Name+Brand combination. Images
are deduplicated by source URL, and existing files on disk are skipped when
"Skip existing images" is on.

## Project layout

```
main.py                  entry point
core/
  config.py               AppConfig dataclass, loads/saves config.json
  database.py              SQLite: product_queue (resume), products, images
  crawler.py               Playwright: category pagination + rendered HTML fetch
  parser.py                field extraction from rendered product HTML
  image_downloader.py       concurrent-safe image download w/ dedup + resize
  exporter.py               WooCommerce CSV + JSON writers
  woocommerce.py             REST API v3 client (upsert by SKU)
gui/
  main_window.py            full GUI (per original spec layout)
  settings_dialog.py        General/Images/WooCommerce/Export tabs
  worker.py                 QThread pipeline + pause/resume/stop control
utils/
  logger.py                 scraper.log / errors.log / downloads.log + GUI log feed
downloads/images/          downloaded product images land here by default
exports/csv, exports/json, exports/database  default export locations
logs/                       scraper.log, errors.log, downloads.log
```

## Notes on scale & performance

- The category discovery loop follows `?page=N` pagination and stops when a
  page returns zero product links or the pagination control's highest page
  number is reached (hard safety cap: 500 pages per category).
- Product scraping is sequential per browser context by default (safer for a
  JS-heavy site than hammering it with many parallel headless tabs); the
  `threads` setting is honored by the image downloader's retry/backoff logic
  and reserved for a future multi-context crawl if you need to scrape faster.
- Every network call (page load, image download) retries with backoff up to
  `retry_count` times before being logged as an error and skipped — a handful
  of failures won't kill a 5,000-product run.

## Respect the source site

Set a reasonable `delay_between_requests_ms` (400ms+ default) and don't run
excessive parallel threads — this keeps the tool from behaving like a denial-of-
service attack against Fouani's servers, and is just good practice when scraping
any third-party retailer's storefront for repricing/import purposes.
