import asyncio
import json
import logging
import os
import re
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
from typing import Optional

import httpx
from dotenv import load_dotenv
from playwright.async_api import ElementHandle, Page, async_playwright

from app.scrapers.base import BaseScraper, ProductRaw, _FETCH_PAGE_CAP

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_BACKEND_ROOT / ".env")

logger = logging.getLogger(__name__)

_DEFAULT_QUERY = "dog bed"
LISTING_URL = "https://www.tractorsupply.com/tsc/catalog/dog-beds"
_CATALOG_BASE = "https://www.tractorsupply.com/tsc/catalog/dog-beds"

# Env (set in backend/.env):
#   SCRAPERAPI_KEY — when set (and TRACTOR_USE_SCRAPERAPI is not disabled), TSC uses
#     ScraperAPI + legacy SearchDisplay HTML (works without JS render).
#   TRACTOR_USE_SCRAPERAPI — "false"/"0" to force Playwright even if the key exists.
#   TRACTOR_SCRAPERAPI_TIMEOUT — HTTP timeout seconds (default 120).
#   TRACTOR_SCRAPERAPI_COUNTRY — e.g. "us" (default us).
#   TRACTOR_SCRAPERAPI_PREMIUM / TRACTOR_SCRAPERAPI_ULTRA_PREMIUM — set "true" if required.
#   TRACTOR_SEARCH_DISPLAY_PAGE_SIZE — page size for SearchDisplay (default 48).

_PRODUCT_LINK_SELECTOR = "a[href*='/tsc/product/']"
_PRODUCT_PATH_RE = re.compile(r"/tsc/product/[a-z0-9][a-z0-9/-]+", re.IGNORECASE)
_CATALOG_ENTRY_RE = re.compile(
    r'id="catalogEntry_img(\d+)"[^>]*href="(/tsc/product/[^"?#]+)"[^>]*title="([^"]+)"',
    re.IGNORECASE,
)
_RATING_TITLE_RE = re.compile(r"Product Rating is\s+([\d.]+)", re.IGNORECASE)
_RATING_REVIEW_SPAN_RE = re.compile(
    r'<div class="rating">.*?<span>\((\d[\d,]*)\)</span>',
    re.IGNORECASE | re.DOTALL,
)
_GRID_WAIT_SELECTORS = (
    _PRODUCT_LINK_SELECTOR,
    "[class*='product-card']",
    "[class*='productCard']",
    "[class*='product-tile']",
)

_DEBUG_DIR = Path(__file__).resolve().parents[2] / "debug_scrapes" / "tractor_supply"
_CHROMIUM_EXTRA = [
    "--disable-quic",
    "--disable-http2",
    "--dns-prefetch-disable",
]

_CONSENT_SELECTORS = (
    "#onetrust-accept-btn-handler",
    "button:has-text('Accept All Cookies')",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
)

_SPONSORED_MARKERS = ("sponsored=1", "utm_medium=cpc", "cm_mmc=", "adId=")

_LINK_EXTRACT_JS = """(el) => {
    const href = el.href || el.getAttribute('href') || '';
    if (!href.includes('/tsc/product/')) return null;
    if (/sponsored=1|utm_medium=cpc|cm_mmc=|adId=/i.test(href)) return null;

    let title = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    const img = el.querySelector('img') || el.closest('li, article, div, section')?.querySelector('img');
    if ((!title || title.length < 4) && img?.alt) {
        title = (img.alt || '').trim();
    }
    if (!title) {
        const labelled = el.getAttribute('aria-label');
        if (labelled) title = labelled.trim();
    }

    let root = el.closest('li, article, [class*="product-card"], [class*="productCard"], [class*="product-tile"]') || el.parentElement;
    if (!root) root = el;
    let priceText = '';
    let ratingRaw = '';
    let reviewRaw = '';
    const imageUrl = img?.src || img?.getAttribute('data-src') || null;

    for (let depth = 0; depth < 10 && root; depth++) {
        const stars = root.querySelector('[aria-label*="out of"], [aria-label*="star"], [aria-label*="rating"]');
        if (stars && !ratingRaw) {
            ratingRaw = stars.getAttribute('aria-label') || stars.textContent || '';
        }
        if (!priceText) {
            const text = root.innerText || '';
            const m = text.match(/\\$\\s?\\d+[\\d,.]*/);
            if (m) priceText = m[0];
        }
        if (!reviewRaw) {
            const rev = root.querySelector('[class*="review"], [class*="Review"], [class*="rating"]');
            if (rev) reviewRaw = rev.textContent || '';
        }
        root = root.parentElement;
    }

    return { href, title, priceText, ratingRaw, reviewRaw, imageUrl };
}"""


def _listing_url(query: str) -> str:
    q = (query or "").strip().lower()
    if not q or q in ("dog bed", "dog beds", "dog_beds"):
        return LISTING_URL
    term = (query or _DEFAULT_QUERY).strip() or _DEFAULT_QUERY
    return f"{_CATALOG_BASE}?isIntSrch=written&srch={quote_plus(term)}"


def _is_sponsored_context(snippet: str) -> bool:
    return any(marker in snippet for marker in _SPONSORED_MARKERS)


def _title_from_path(path: str) -> str:
    slug = path.rstrip("/").split("/tsc/product/")[-1]
    return slug.replace("-", " ").strip().title()


def _normalize_tsc_url(href: str) -> str:
    href = href.split("?")[0]
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://www.tractorsupply.com{href}"
    return f"https://www.tractorsupply.com/{href.lstrip('/')}"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _tractor_should_use_scraperapi() -> bool:
    if os.environ.get("TRACTOR_USE_SCRAPERAPI", "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False
    key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
    return bool(key)


def _scraperapi_country() -> str:
    return (os.environ.get("TRACTOR_SCRAPERAPI_COUNTRY") or "us").strip() or "us"


def _scraperapi_extra_params() -> dict[str, str]:
    params: dict[str, str] = {}
    if _env_truthy("TRACTOR_SCRAPERAPI_ULTRA_PREMIUM"):
        params["ultra_premium"] = "true"
    elif _env_truthy("TRACTOR_SCRAPERAPI_PREMIUM"):
        params["premium"] = "true"
    return params


def _search_display_page_size() -> int:
    raw = (os.environ.get("TRACTOR_SEARCH_DISPLAY_PAGE_SIZE") or "48").strip()
    try:
        n = int(raw)
        return max(8, min(n, 96))
    except ValueError:
        return 48


def _scraperapi_timeout_sec() -> float:
    try:
        return float(os.environ.get("TRACTOR_SCRAPERAPI_TIMEOUT") or "120")
    except ValueError:
        return 120.0


def _extract_category_identifier_ntk(html: str) -> Optional[str]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        nd = json.loads(m.group(1))
        pp = nd.get("props", {}).get("pageProps") or {}
        inner = pp.get("pageProps") or pp
        cd = (inner.get("content") or {}).get("categoryDetails") or {}
        entry = cd.get("selectedEntry") or {}
        return entry.get("identifier_ntk") or entry.get("identifier")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def _build_search_display_url(
    search_term: str,
    *,
    begin_index: int,
    page_size: int,
    category_id: Optional[str],
) -> str:
    q = quote_plus((search_term or _DEFAULT_QUERY).strip() or _DEFAULT_QUERY)
    url = (
        "https://www.tractorsupply.com/SearchDisplay"
        f"?searchTerm={q}&beginIndex={begin_index}&pageSize={page_size}"
    )
    if category_id:
        url += f"&filterTerm={category_id}"
    return url


_PDP_OFFER_PRICE_RE = re.compile(
    r'"(?:maxOfferPrice|minOfferPrice|offerPrice|listPrice)"\s*:\s*"?([\d]+(?:\.[\d]+)?)"?',
    re.IGNORECASE,
)


def _pdp_price_enrich_enabled() -> bool:
    return (os.environ.get("TRACTOR_ENRICH_PDP_PRICES") or "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _pdp_price_enrich_max() -> int:
    try:
        return max(1, min(int((os.environ.get("TRACTOR_PDP_PRICE_MAX") or "72").strip()), 200))
    except ValueError:
        return 72


def _price_from_pdp_html(html: str, scraper: BaseScraper) -> Optional[float]:
    """TSC PDP embeds offer prices in __NEXT_DATA__ / itemPricing JSON."""
    for m in _PDP_OFFER_PRICE_RE.finditer(html):
        val = scraper.normalize_price(m.group(1))
        if val is not None:
            return val
    m = re.search(
        r'"itemPricing"\s*:\s*\{[^}]{0,400}?"maxOfferPrice"\s*:\s*([\d.]+)',
        html,
        re.IGNORECASE,
    )
    if m:
        return scraper.normalize_price(m.group(1))
    m = re.search(
        r'itemprop="price"[^>]*content="([\d.]+)"',
        html,
        re.IGNORECASE,
    )
    if m:
        return scraper.normalize_price(m.group(1))
    return None


async def _enrich_tractor_prices_via_pdp(
    client: httpx.AsyncClient,
    api_key: str,
    scraper: BaseScraper,
    products: list[ProductRaw],
) -> None:
    if not _pdp_price_enrich_enabled():
        return

    targets = [p for p in products if p.price is None and p.product_url]
    if not targets:
        return

    targets = targets[: _pdp_price_enrich_max()]
    sem = asyncio.Semaphore(6)

    async def fetch_one(product: ProductRaw) -> None:
        async with sem:
            body, status = await _scraperapi_get(client, api_key, product.product_url or "")
            if not body or status != 200:
                return
            product.price = _price_from_pdp_html(body, scraper)

    await asyncio.gather(*(fetch_one(p) for p in targets))
    filled = sum(1 for p in targets if p.price is not None)
    logger.info(
        "Tractor Supply: PDP price enrich %s/%s products",
        filled,
        len(targets),
    )


def _rating_review_for_entry(html: str, entry_id: str) -> tuple[str, str]:
    """SearchDisplay PLP: rating in title/aria and review count in (N) span."""
    anchor = f"catalogEntry_img{entry_id}"
    idx = html.find(anchor)
    if idx < 0:
        return "", "0"
    chunk = html[idx : idx + 14_000]
    rating_raw = ""
    m = _RATING_TITLE_RE.search(chunk)
    if m:
        rating_raw = m.group(1)
    if not rating_raw:
        m = re.search(
            r'<div class="rating">.*?<span>([\d.]+)</span>',
            chunk,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            rating_raw = m.group(1)
    review_raw = "0"
    m = _RATING_REVIEW_SPAN_RE.search(chunk)
    if m:
        review_raw = m.group(1)
    return rating_raw, review_raw


def _image_url_for_catalog_entry(html: str, entry_id: str) -> Optional[str]:
    m = re.search(rf'id="img1_{re.escape(entry_id)}"[^>]+(?:data-src|src)="([^"]+)"', html, re.I)
    if not m:
        return None
    u = (m.group(1) or "").strip()
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return f"https://www.tractorsupply.com{u}"
    return u or None


def _parse_search_display_html(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    """IBM Commerce SearchDisplay PLP (full HTML without React hydration)."""
    products: list[ProductRaw] = []
    for entry_id, path, title in _CATALOG_ENTRY_RE.findall(html):
        if len(products) >= limit:
            break
        if _is_sponsored_context(title) or _is_sponsored_context(path):
            continue
        img = _image_url_for_catalog_entry(html, entry_id)
        rating_raw, review_raw = _rating_review_for_entry(html, entry_id)
        raw = {
            "href": path.split("?")[0],
            "title": title.strip(),
            "priceText": "",
            "ratingRaw": rating_raw,
            "reviewRaw": review_raw,
            "imageUrl": img,
        }
        item = _product_from_raw(scraper, raw, seen_urls)
        if item:
            products.append(item)
    if products:
        return products
    # Fallback when markup differs slightly
    for path in dict.fromkeys(_PRODUCT_PATH_RE.findall(html)):
        if len(products) >= limit:
            break
        if _is_sponsored_context(path):
            continue
        item = _product_from_raw(
            scraper,
            {
                "href": path.split("?")[0],
                "title": _title_from_path(path),
                "priceText": "",
                "ratingRaw": "",
                "reviewRaw": "0",
            },
            seen_urls,
        )
        if item:
            products.append(item)
    return products


async def _scraperapi_get(
    client: httpx.AsyncClient,
    api_key: str,
    target_url: str,
) -> tuple[Optional[str], int]:
    params: dict[str, str] = {
        "api_key": api_key,
        "url": target_url,
        "country_code": _scraperapi_country(),
        **(_scraperapi_extra_params()),
    }
    resp = await client.get("https://api.scraperapi.com/", params=params)
    body = resp.text if resp.content else ""
    if resp.status_code != 200:
        snippet = body[:280].replace("\n", " ")
        logger.warning(
            "ScraperAPI HTTP %s for %s snippet=%s",
            resp.status_code,
            target_url[:120],
            snippet,
        )
        return None, resp.status_code
    return body, resp.status_code


def _filter_by_query(items: list[ProductRaw], query: str) -> list[ProductRaw]:
    """Prefer titles matching query tokens; do not fall back to unfiltered junk."""
    words = [w.lower() for w in (query or "").split() if len(w) > 1]
    if not words:
        return items
    matched = [p for p in items if all(w in (p.title or "").lower() for w in words)]
    return matched


def _product_from_raw(
    scraper: BaseScraper, raw: dict, seen_urls: set[str]
) -> ProductRaw | None:
    href = raw.get("href") or ""
    if not href or _is_sponsored_context(href):
        return None

    product_url = _normalize_tsc_url(href)
    if product_url in seen_urls:
        return None
    seen_urls.add(product_url)

    title = (raw.get("title") or "").strip()
    if not title or len(title) < 3:
        return None

    review_raw = raw.get("reviewRaw") or "0"
    review_count = 0
    m = re.search(r"\d+", str(review_raw).replace(",", ""))
    if m:
        review_count = int(m.group())

    return ProductRaw(
        source_site=scraper.SITE_NAME,
        title=title[:200],
        price=scraper.normalize_price(raw.get("priceText") or ""),
        avg_rating=scraper.normalize_rating(raw.get("ratingRaw") or ""),
        review_count=review_count,
        product_url=product_url,
        image_url=raw.get("imageUrl"),
        scrape_status="ok",
    )


def _extract_embedded_from_html(
    html: str, limit: int, seen_urls: set[str], scraper: BaseScraper
) -> list[ProductRaw]:
    products: list[ProductRaw] = []
    for path in dict.fromkeys(_PRODUCT_PATH_RE.findall(html)):
        if len(products) >= limit:
            break
        idx = html.find(path)
        if idx < 0:
            continue
        window = html[max(0, idx - 100) : idx + len(path) + 250]
        if _is_sponsored_context(window):
            continue

        title = _title_from_path(path)
        title_m = re.search(
            re.escape(path) + r'[^>]*>([^<]{5,120})<',
            html[idx : idx + 600],
        )
        if title_m:
            candidate = title_m.group(1).strip()
            if len(candidate.split()) >= 2:
                title = candidate

        item = _product_from_raw(
            scraper,
            {
                "href": path,
                "title": title,
                "priceText": "",
                "ratingRaw": "",
                "reviewRaw": "0",
            },
            seen_urls,
        )
        if item:
            products.append(item)
    return products


async def _dismiss_consent(page: Page) -> None:
    for sel in _CONSENT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=1200):
                await loc.click(timeout=3000)
                await asyncio.sleep(random.uniform(0.3, 0.7))
                return
        except Exception:
            continue


async def _debug_instrumentation(page: Page, label: str) -> dict:
    html = await page.content()
    content_len = len(html)
    anchor_count = await page.evaluate(
        """() => document.querySelectorAll("a[href*='/tsc/product/']").length"""
    )
    product_anchor_count = await _count_product_links(page)
    embedded_paths = len(dict.fromkeys(_PRODUCT_PATH_RE.findall(html)))
    title = await page.title()
    blocked = content_len < 500 or not title.strip()

    screenshot_path: str | None = None
    try:
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = _DEBUG_DIR / f"{label}_{ts}.png"
        await page.screenshot(path=str(path), full_page=False)
        screenshot_path = str(path)
    except Exception as exc:
        logger.warning("Tractor Supply debug screenshot failed: %s", exc)

    logger.info(
        "Tractor Supply scrape debug [%s]: content_len=%s anchors(/tsc/product/)=%s "
        "unique_product_links=%s embedded_paths=%s blocked=%s screenshot=%s url=%s",
        label,
        content_len,
        anchor_count,
        product_anchor_count,
        embedded_paths,
        blocked,
        screenshot_path,
        page.url,
    )
    return {
        "label": label,
        "content_length": content_len,
        "anchors_product_pattern": anchor_count,
        "unique_product_links": product_anchor_count,
        "embedded_paths": embedded_paths,
        "blocked": blocked,
        "screenshot": screenshot_path,
        "url": page.url,
    }


async def _scroll_to_hydrate(page: Page) -> None:
    for _ in range(5):
        await page.evaluate(
            "() => window.scrollBy(0, Math.max(400, window.innerHeight * 0.85))"
        )
        await asyncio.sleep(random.uniform(0.35, 0.75))
    await page.keyboard.press("End")
    await asyncio.sleep(random.uniform(0.8, 1.2))
    await page.keyboard.press("Home")
    await asyncio.sleep(random.uniform(0.4, 0.8))
    for _ in range(3):
        await page.keyboard.press("End")
        await asyncio.sleep(random.uniform(0.6, 1.0))


async def _count_product_links(page: Page) -> int:
    return await page.evaluate(
        """() => {
            const seen = new Set();
            for (const a of document.querySelectorAll("a[href*='/tsc/product/']")) {
                const h = a.href || a.getAttribute('href') || '';
                if (/sponsored=1|utm_medium=cpc|cm_mmc=|adId=/i.test(h)) continue;
                const path = h.split('?')[0];
                if (!path || !path.includes('/tsc/product/') || seen.has(path)) continue;
                seen.add(path);
            }
            return seen.size;
        }"""
    )


async def _wait_for_hydrated_grid(page: Page, timeout_sec: float = 50) -> int:
    for sel in _GRID_WAIT_SELECTORS:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=12_000)
            break
        except Exception:
            continue

    try:
        await page.wait_for_function(
            """() => {
                const seen = new Set();
                for (const a of document.querySelectorAll("a[href*='/tsc/product/']")) {
                    const h = a.href || a.getAttribute('href') || '';
                    if (/sponsored=1|utm_medium=cpc|cm_mmc=|adId=/i.test(h)) continue;
                    const path = h.split('?')[0];
                    if (path && path.includes('/tsc/product/')) seen.add(path);
                }
                return seen.size >= 3;
            }""",
            timeout=15_000,
        )
    except Exception:
        pass

    deadline = time.monotonic() + timeout_sec
    best_n = 0
    while time.monotonic() < deadline:
        await _dismiss_consent(page)
        await _scroll_to_hydrate(page)
        n = await _count_product_links(page)
        best_n = max(best_n, n)
        if n >= 3:
            return n
        await asyncio.sleep(random.uniform(1.0, 1.8))
    return best_n


async def _collect_product_links(page: Page, limit: int) -> list[ElementHandle]:
    handles = await page.query_selector_all(_PRODUCT_LINK_SELECTOR)
    filtered: list[ElementHandle] = []
    for handle in handles:
        href = await handle.get_attribute("href") or ""
        if _is_sponsored_context(href):
            continue
        filtered.append(handle)
    if filtered:
        return filtered[: max(limit * 3, limit)]

    for sel in _GRID_WAIT_SELECTORS[1:]:
        cards = await page.query_selector_all(sel)
        if cards:
            return cards[: max(limit * 2, limit)]
    return []


async def _parse_dom_links(
    links: list[ElementHandle],
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    products: list[ProductRaw] = []
    for handle in links:
        if len(products) >= limit:
            break
        try:
            is_anchor = await handle.evaluate(
                "el => el.tagName === 'A' && (el.href || '').includes('/tsc/product/')"
            )
            target = handle
            if not is_anchor:
                inner = await handle.query_selector("a[href*='/tsc/product/']")
                if not inner:
                    continue
                target = inner

            raw = await target.evaluate(_LINK_EXTRACT_JS)
            if not raw:
                continue
            item = _product_from_raw(scraper, raw, seen_urls)
            if item:
                products.append(item)
        except Exception:
            continue
    return products


async def _goto_with_retries(page: Page, url: str) -> bool:
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=70_000)
            return True
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1) + random.uniform(0, 0.5))
    logger.warning(
        "Tractor Supply navigation failed after retries: %s (url=%s)",
        last_err,
        url,
    )
    return False


class TractorSupplyScraper(BaseScraper):
    SITE_NAME = "tractor_supply"

    async def _fetch_listings_via_scraperapi(
        self,
        query: str,
        limit: int,
        listing_pages: int = 1,
    ) -> list[ProductRaw]:
        api_key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if not api_key:
            return []

        catalog_url = _listing_url(query)
        seen_urls: set[str] = set()
        products: list[ProductRaw] = []
        tout = _scraperapi_timeout_sec()
        timeout_cfg = httpx.Timeout(tout, connect=min(30.0, tout))
        shell_st = 0
        last_sd_status = 0
        shell_body = ""
        plp_pages = max(1, listing_pages)

        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                shell_body, shell_st = await _scraperapi_get(
                    client, api_key, catalog_url
                )
                category_id = _extract_category_identifier_ntk(shell_body or "")
                if category_id:
                    logger.info(
                        "Tractor Supply ScraperAPI: SearchDisplay filterTerm=%s",
                        category_id,
                    )
                search_term = (query or _DEFAULT_QUERY).strip() or _DEFAULT_QUERY
                page_size = _search_display_page_size()

                for page_ix in range(plp_pages):
                    begin_index = page_ix * page_size
                    sd_url = _build_search_display_url(
                        search_term,
                        begin_index=begin_index,
                        page_size=page_size,
                        category_id=category_id,
                    )
                    sd_body, last_sd_status = await _scraperapi_get(
                        client, api_key, sd_url
                    )
                    if not sd_body:
                        break
                    batch = _parse_search_display_html(
                        sd_body, self, seen_urls, _FETCH_PAGE_CAP
                    )
                    if not batch:
                        break
                    products.extend(batch)
                    if len(batch) < page_size:
                        break

                await _enrich_tractor_prices_via_pdp(
                    client, api_key, self, products
                )
        except httpx.HTTPError as exc:
            msg = (
                "Tractor Supply ScraperAPI: request failed (%s). "
                "Check network, timeouts, or ScraperAPI status."
                % type(exc).__name__
            )
            logger.exception(msg)
            self._empty_scrape_note = f"{msg} Details: {exc}"
            return []

        if not products:
            self._empty_scrape_note = (
                "Tractor Supply ScraperAPI: SearchDisplay returned 0 products "
                f"(catalog_shell_http={shell_st}, "
                f"last_searchdisplay_http={last_sd_status}). "
                "Verify SCRAPERAPI_KEY and credits, or TRACTOR_SCRAPERAPI_PREMIUM."
            )
            logger.warning(self._empty_scrape_note)
        else:
            logger.info(
                "Tractor Supply ScraperAPI: fetched %s product(s) across %s page(s)",
                len(products),
                plp_pages,
            )

        return products

    async def _fetch_search_display_playwright(
        self,
        page: Page,
        query: str,
        listing_pages: int,
        seen_urls: set[str],
    ) -> list[ProductRaw]:
        catalog_url = _listing_url(query)
        loaded = await _goto_with_retries(page, catalog_url)
        if not loaded:
            return []

        await asyncio.sleep(random.uniform(1.0, 2.0))
        await _dismiss_consent(page)
        shell_html = await page.content()
        category_id = _extract_category_identifier_ntk(shell_html or "")
        search_term = (query or _DEFAULT_QUERY).strip() or _DEFAULT_QUERY
        page_size = _search_display_page_size()
        products: list[ProductRaw] = []

        for page_ix in range(max(1, listing_pages)):
            begin_index = page_ix * page_size
            sd_url = _build_search_display_url(
                search_term,
                begin_index=begin_index,
                page_size=page_size,
                category_id=category_id,
            )
            if not await _goto_with_retries(page, sd_url):
                break
            await asyncio.sleep(random.uniform(0.8, 1.5))
            html = await page.content()
            batch = _parse_search_display_html(
                html, self, seen_urls, _FETCH_PAGE_CAP
            )
            if not batch:
                break
            products.extend(batch)
            if len(batch) < page_size:
                break

        api_key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if api_key and products:
            tout = _scraperapi_timeout_sec()
            timeout_cfg = httpx.Timeout(tout, connect=min(30.0, tout))
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                await _enrich_tractor_prices_via_pdp(
                    client, api_key, self, products
                )

        return products

    async def fetch_listings(
        self,
        query: str = "dog bed",
        limit: int = 20,
        *,
        listing_pages: int = 1,
    ) -> list[ProductRaw]:
        pages = max(1, listing_pages)

        if _tractor_should_use_scraperapi():
            return await self._fetch_listings_via_scraperapi(query, limit, pages)

        seen_urls: set[str] = set()
        products: list[ProductRaw] = []

        async with async_playwright() as pw:
            async with self._stealth_page(
                pw, extra_chromium_args=_CHROMIUM_EXTRA
            ) as page:
                products = await self._fetch_search_display_playwright(
                    page, query, pages, seen_urls
                )

                if not products:
                    self._empty_scrape_note = (
                        "Tractor Supply: SearchDisplay (Playwright) returned 0 products. "
                        "Set SCRAPERAPI_KEY for the HTTP SearchDisplay path."
                    )
                    logger.warning(self._empty_scrape_note)

        return products
