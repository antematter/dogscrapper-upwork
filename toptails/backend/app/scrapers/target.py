import asyncio
import logging
import re
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import ElementHandle, Page, async_playwright

from app.scrapers.base import BaseScraper, ProductRaw

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.target.com/c/dog-beds-pet-supplies/-/N-5xt44"
SEARCH_URL = "https://www.target.com/s?searchTerm={query}"

_PRODUCT_LINK_SELECTOR = "a[href*='/A-']"
_PRODUCT_PATH_RE = re.compile(r"/p/[a-z0-9-]+/-/A-\d+", re.IGNORECASE)
_GRID_WAIT_SELECTORS = (
    _PRODUCT_LINK_SELECTOR,
    "[class*='ProductCard']",
    "[class*='product-card']",
)

_DEBUG_DIR = Path(__file__).resolve().parents[2] / "debug_scrapes" / "target"

_CONSENT_SELECTORS = (
    "#onetrust-accept-btn-handler",
    "button:has-text('Accept cookies')",
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
)

_SPONSORED_MARKERS = ("TCID=OGS", "AFID=google", "sponsored=1")

_LINK_EXTRACT_JS = """(el) => {
    const href = el.href || el.getAttribute('href') || '';
    if (!href.includes('/A-')) return null;
    if (/TCID=OGS|AFID=google|sponsored=1/i.test(href)) return null;

    let title = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    const img = el.querySelector('img') || el.closest('motion.div, li, article, div')?.querySelector('img');
    if ((!title || title.length < 4) && img?.alt) {
        title = (img.alt || '').trim();
    }
    if (!title) {
        const labelled = el.getAttribute('aria-label');
        if (labelled) title = labelled.trim();
    }

    let root = el.closest('li, article, [class*="ProductCard"], [class*="product"]') || el.parentElement;
    if (!root) root = el;
    let priceText = '';
    let ratingRaw = '';
    let reviewRaw = '';
    const imageUrl = img?.src || img?.getAttribute('data-src') || null;

    for (let depth = 0; depth < 10 && root; depth++) {
        const stars = root.querySelector('[aria-label*="out of"], [aria-label*="star"]');
        if (stars && !ratingRaw) {
            ratingRaw = stars.getAttribute('aria-label') || stars.textContent || '';
        }
        if (!priceText) {
            const text = root.innerText || '';
            const m = text.match(/\\$\\s?\\d+[\\d,.]*/);
            if (m) priceText = m[0];
        }
        if (!reviewRaw) {
            const rev = root.querySelector('[class*="review"], [class*="Review"]');
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
    return SEARCH_URL.format(query=query.replace(" ", "+"))


def _is_sponsored_context(snippet: str) -> bool:
    return any(marker in snippet for marker in _SPONSORED_MARKERS)


def _title_from_path(path: str) -> str:
    slug = path.split("/p/", 1)[-1].split("/-/")[0]
    return slug.replace("-", " ").strip().title()


def _product_from_raw(
    scraper: BaseScraper, raw: dict, seen_urls: set[str]
) -> ProductRaw | None:
    href = (raw.get("href") or "").split("?")[0]
    if not href or _is_sponsored_context(href):
        return None

    product_url = (
        href if href.startswith("http") else f"https://www.target.com{href}"
    )
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
    """Fallback when CSR grid never mounts anchors — parse /p/.../A- paths from HTML."""
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
            re.escape(path) + r"[^>]*\\u003e([^\\u003c]{5,120})\\u003c",
            html[idx : idx + 600],
        )
        if title_m:
            candidate = title_m.group(1).strip()
            if len(candidate.split()) >= 2:
                title = candidate

        product_url = f"https://www.target.com{path}"
        if product_url in seen_urls:
            continue

        item = _product_from_raw(
            scraper,
            {"href": product_url, "title": title, "priceText": "", "ratingRaw": "", "reviewRaw": "0"},
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
    """Log page shell vs hydrated grid signals; save screenshot for inspection."""
    html = await page.content()
    content_len = len(html)
    anchor_count = await page.evaluate(
        """() => document.querySelectorAll("a[href*='/A-']").length"""
    )
    product_anchor_count = await _count_product_links(page)
    embedded_paths = len(dict.fromkeys(_PRODUCT_PATH_RE.findall(html)))

    screenshot_path: str | None = None
    try:
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = _DEBUG_DIR / f"{label}_{ts}.png"
        await page.screenshot(path=str(path), full_page=False)
        screenshot_path = str(path)
    except Exception as exc:
        logger.warning("Target debug screenshot failed: %s", exc)

    logger.info(
        "Target scrape debug [%s]: content_len=%s anchors(/A-)=%s "
        "unique_product_links=%s embedded_paths=%s screenshot=%s url=%s",
        label,
        content_len,
        anchor_count,
        product_anchor_count,
        embedded_paths,
        screenshot_path,
        page.url,
    )
    return {
        "label": label,
        "content_length": content_len,
        "anchors_a_pattern": anchor_count,
        "unique_product_links": product_anchor_count,
        "embedded_paths": embedded_paths,
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
            for (const a of document.querySelectorAll("a[href*='/A-']")) {
                const h = a.href || a.getAttribute('href') || '';
                if (/TCID=OGS|AFID=google|sponsored=1/i.test(h)) continue;
                const path = h.split('?')[0];
                if (!path || seen.has(path)) continue;
                if (path.includes('/p/') || /\\/A-\\d+/i.test(path)) seen.add(path);
            }
            return seen.size;
        }"""
    )


async def _wait_for_hydrated_grid(page: Page, timeout_sec: float = 50) -> int:
    """Wait for CSR product links; poll scroll until /A- anchors appear."""
    for sel in _GRID_WAIT_SELECTORS:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=12_000)
            break
        except Exception:
            continue

    try:
        await page.wait_for_function(
            """() => {
                let n = 0;
                const seen = new Set();
                for (const a of document.querySelectorAll("a[href*='/A-']")) {
                    const h = a.href || a.getAttribute('href') || '';
                    if (/TCID=OGS|AFID=google|sponsored=1/i.test(h)) continue;
                    const path = h.split('?')[0];
                    if (!path || seen.has(path)) continue;
                    if (path.includes('/p/') || /\\/A-\\d+/i.test(path)) {
                        seen.add(path); n++;
                    }
                }
                return n >= 3;
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
    """Product anchors and card wrappers — no data-test attributes."""
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
    for link in links:
        if len(products) >= limit:
            break
        try:
            raw = await link.evaluate(_LINK_EXTRACT_JS)
            if not raw:
                continue
            item = _product_from_raw(scraper, raw, seen_urls)
            if item:
                products.append(item)
        except Exception:
            continue
    return products


class TargetScraper(BaseScraper):
    SITE_NAME = "target"

    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        url = _listing_url(query)
        seen_urls: set[str] = set()
        products: list[ProductRaw] = []
        n_links = 0

        async with async_playwright() as pw:
            async with self._stealth_page(pw) as page:
                try:
                    async with page.expect_response(
                        lambda r: "listing-page-product-list" in r.url
                        and r.status == 200,
                        timeout=40_000,
                    ):
                        await page.goto(
                            url, wait_until="domcontentloaded", timeout=70_000
                        )
                except Exception:
                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=70_000
                    )

                await asyncio.sleep(random.uniform(1.5, 2.5))
                await _dismiss_consent(page)
                await _debug_instrumentation(page, "after_load")

                n_links = await _wait_for_hydrated_grid(page)
                await self.human_delay()
                await _debug_instrumentation(page, "after_hydrate")

                links = await _collect_product_links(page, limit)
                n_links = max(n_links, await _count_product_links(page))
                products = await _parse_dom_links(links, self, seen_urls, limit)

                # Do not merge sitewide /A- paths from full HTML — causes toys, cat, treats.

                if not products:
                    diag = await _debug_instrumentation(page, "empty_result")
                    self._empty_scrape_note = (
                        f"Target: 0 products (dom_links={n_links}, "
                        f"html_len={diag.get('content_length')}, "
                        f"embedded_paths={diag.get('embedded_paths')}). "
                        "Grid may not have hydrated."
                    )
                    logger.warning(self._empty_scrape_note)

        return products[:limit]
