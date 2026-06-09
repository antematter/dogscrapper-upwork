import asyncio
import json
import logging
import os
import re
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from playwright.async_api import ElementHandle, Page, async_playwright

from app.scrapers.base import BaseScraper, ProductRaw

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_BACKEND_ROOT / ".env")

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.petco.com/category/dog/dog-beds-and-bedding"
LEGACY_LISTING_URL = (
    "https://www.petco.com/shop/en/petcostore/category/dog/dog-beds-and-bedding"
)
SEARCH_URL = "https://www.petco.com/shop/en/petcostore/search?q={query}"
_CONSTRUCTOR_GROUP = "dog-beds-and-bedding"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_PRODUCT_LINK_SELECTOR = "a[href*='/product/']"
_PRODUCT_PATH_RE = re.compile(
    r"(?:https://www\.petco\.com)?(/shop/en/petcostore/product/[^\s\"'<>?#]+|/product/[^\s\"'<>?#]+)",
    re.IGNORECASE,
)
_GRID_WAIT_SELECTORS = (
    _PRODUCT_LINK_SELECTOR,
    "[class*='product-card']",
    "[class*='ProductCard']",
    ".product-item",
)

_DEBUG_DIR = Path(__file__).resolve().parents[2] / "debug_scrapes" / "petco"

# Env (backend/.env) — Petco via ScraperAPI (after Constructor, before Playwright):
#   SCRAPERAPI_KEY — fetches SSR HTML (~__NEXT_DATA__ tiles; titles/URLs/images; price sparse).
#   PETCO_USE_SCRAPERAPI — "false"/"0" to skip even if SCRAPERAPI_KEY is set.
#   PETCO_SCRAPERAPI_TIMEOUT — seconds (default 120).
#   PETCO_SCRAPERAPI_COUNTRY — e.g. "us" (default us).
#   PETCO_SCRAPERAPI_PREMIUM / PETCO_SCRAPERAPI_ULTRA_PREMIUM — pass through to ScraperAPI.
#   PETCO_SCRAPERAPI_MAX_PAGES — category/search pagination (?page=N), default 8.

_CONSENT_SELECTORS = (
    "#onetrust-accept-btn-handler",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
    "[data-testid='privacy-banner-accept']",
)

_SPONSORED_MARKERS = ("sponsored=1", "cm_mmc=", "adId=", "utm_medium=cpc")

_LINK_EXTRACT_JS = """(el) => {
    const href = el.href || el.getAttribute('href') || '';
    if (!href.includes('/product/')) return null;
    if (/sponsored=1|cm_mmc=|adId=|utm_medium=cpc/i.test(href)) return null;

    let title = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    const img = el.querySelector('img') || el.closest('li, article, div, section')?.querySelector('img');
    if ((!title || title.length < 4) && img?.alt) {
        title = (img.alt || '').trim();
    }
    if (!title) {
        const labelled = el.getAttribute('aria-label');
        if (labelled) title = labelled.trim();
    }

    let root = el.closest('li, article, [class*="product-card"], [class*="ProductCard"]') || el.parentElement;
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
    slug = path.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").strip().title()


def _normalize_petco_url(href: str) -> str:
    href = href.split("?")[0]
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://www.petco.com{href}"
    return f"https://www.petco.com/{href.lstrip('/')}"


def _product_from_raw(
    scraper: BaseScraper, raw: dict, seen_urls: set[str]
) -> ProductRaw | None:
    href = raw.get("href") or ""
    if not href or _is_sponsored_context(href):
        return None

    product_url = _normalize_petco_url(href)
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


def _env_truthy_pet(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _petco_should_use_scraperapi() -> bool:
    if os.environ.get("PETCO_USE_SCRAPERAPI", "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False
    key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
    return bool(key)


def _petco_should_use_constructor() -> bool:
    """Optional legacy path; off unless explicitly enabled and creds are set."""
    if os.environ.get("PETCO_USE_CONSTRUCTOR", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    return bool(
        os.environ.get("PETCO_COOKIES", "").strip()
        and os.environ.get("PETCO_CONSTRUCTOR_KEY", "").strip()
    )


def _petco_scraperapi_country() -> str:
    return (os.environ.get("PETCO_SCRAPERAPI_COUNTRY") or "us").strip() or "us"


def _petco_scraperapi_extra_params() -> dict[str, str]:
    if _env_truthy_pet("PETCO_SCRAPERAPI_ULTRA_PREMIUM"):
        return {"ultra_premium": "true"}
    if _env_truthy_pet("PETCO_SCRAPERAPI_PREMIUM"):
        return {"premium": "true"}
    return {}


def _petco_scraperapi_timeout_sec() -> float:
    try:
        return float(os.environ.get("PETCO_SCRAPERAPI_TIMEOUT") or "120")
    except ValueError:
        return 120.0


def _petco_scraperapi_max_pages() -> int:
    try:
        n = int((os.environ.get("PETCO_SCRAPERAPI_MAX_PAGES") or "8").strip())
        return max(1, min(n, 20))
    except ValueError:
        return 8


_TILE_URL_SKU_TAIL = re.compile(r"-\d{6,9}$")


def _is_petco_product_path(val: str) -> bool:
    return "/product/" in val or "/shop/en/petcostore/product/" in val


def _best_petco_tile_url(tile: dict[str, Any]) -> Optional[str]:
    candidates: list[str] = []
    for k in ("url", "itemurl", "itemUrl"):
        val = tile.get(k)
        if isinstance(val, str) and _is_petco_product_path(val):
            candidates.append(val.split("?")[0])
    if not candidates:
        return None
    with_sku = [
        c for c in candidates if _TILE_URL_SKU_TAIL.search(c.rstrip("/").rsplit("/", 1)[-1])
    ]
    pool = with_sku or candidates
    return sorted(pool, key=len, reverse=True)[0]


def _absolute_pdp_url_from_tile_path(candidate: str) -> Optional[str]:
    raw = candidate.split("?")[0].strip()
    if raw.startswith("http"):
        return raw.rstrip("/")
    if raw.startswith("/product/"):
        return f"https://www.petco.com{raw}".rstrip("/")
    ix = raw.find("/shop/en/petcostore/product/")
    if ix < 0:
        return None
    tail = raw[ix:]
    if not tail.startswith("/"):
        tail = "/" + tail
    return ("https://www.petco.com" + tail).rstrip("/")


def _next_data_dict_from_html(html: str) -> Optional[dict[str, Any]]:
    m = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.S,
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _tile_price_from_dict(tile: dict[str, Any]) -> Optional[str]:
    """Petco __NEXT_DATA__ tiles use lowercase keys (rdprice, offerprice, listprice)."""
    lower: dict[str, Any] = {}
    for k, v in tile.items():
        if isinstance(k, str):
            lower[k.lower()] = v

    for pk in (
        "rdprice",
        "sale_price",
        "offerprice",
        "offer_price",
        "price",
        "listprice",
        "list_price",
        "itemprice",
        "currentprice",
        "minprice",
    ):
        val = lower.get(pk)
        if val is not None and str(val).strip():
            return str(val)

    for nested_key in ("price", "pricing", "itempricing", "offer"):
        nested = tile.get(nested_key) or lower.get(nested_key)
        if isinstance(nested, dict):
            found = _tile_price_from_dict(nested)
            if found:
                return found
    return None


def _tile_rating_from_dict(tile: dict[str, Any]) -> tuple[str, str]:
    rating_raw = ""
    for rk in (
        "AverageRating",
        "averagerating",
        "avg_rating",
        "averageRating",
        "AverageRat",
        "rating",
    ):
        val = tile.get(rk)
        if val is not None and str(val).strip():
            rating_raw = str(val)
            break
    review_raw = "0"
    for ck in (
        "TotalReviewCount",
        "reviewcount",
        "review_count",
        "num_reviews",
        "total_reviews",
    ):
        val = tile.get(ck)
        if val is not None:
            review_raw = str(val)
            break
    return rating_raw, review_raw


def tiles_from_next_data_for_tests(data: dict[str, Any]) -> list[tuple[str, str, Optional[str]]]:
    """Public for tests: (title, absolute_url, image_url_or_none)."""
    return [(t, u, img) for t, u, img, *_ in _walk_next_data_tiles(data)]


def _walk_next_data_tiles(
    data: Any,
    *,
    max_tiles: int = 420,
) -> list[tuple[str, str, Optional[str], str, str, str]]:
    ordered: list[tuple[str, str, Optional[str], str, str, str]] = []
    seen_url: set[str] = set()

    def visit(o: Any, depth: int = 0) -> None:
        if depth > 35 or len(ordered) >= max_tiles:
            return
        if isinstance(o, dict):
            nm = o.get("itemname") or o.get("itemName")
            if isinstance(nm, str) and nm.strip():
                pu = _best_petco_tile_url(o)
                if pu:
                    full = _absolute_pdp_url_from_tile_path(pu)
                    if full and full not in seen_url:
                        seen_url.add(full)
                        img_u: Optional[str] = None
                        for ik in ("image_url", "itemimg", "image"):
                            iv = o.get(ik)
                            if isinstance(iv, str) and iv.startswith("http"):
                                img_u = iv
                                break
                        price_txt = _tile_price_from_dict(o) or ""
                        rating_raw, review_raw = _tile_rating_from_dict(o)
                        ordered.append(
                            (nm.strip(), full, img_u, price_txt, rating_raw, review_raw)
                        )
            for v in o.values():
                visit(v, depth + 1)
        elif isinstance(o, list):
            for item in o:
                visit(item, depth + 1)

    visit(data, 0)
    return ordered


def _products_from_next_data_html(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
) -> list[ProductRaw]:
    nd = _next_data_dict_from_html(html)
    if not nd:
        return []

    rows: list[ProductRaw] = []
    for title, url, img, price_txt, rating_raw, review_raw in _walk_next_data_tiles(nd):
        item = _product_from_raw(
            scraper,
            {
                "href": url,
                "title": title,
                "priceText": price_txt,
                "ratingRaw": rating_raw,
                "reviewRaw": review_raw,
                "imageUrl": img,
            },
            seen_urls,
        )
        if item:
            rows.append(item)
    return rows


def _listing_url_page(listing_base: str, page: int) -> str:
    listing_base = listing_base.strip()
    if page <= 1:
        return listing_base
    joiner = "&" if "?" in listing_base else "?"
    return f"{listing_base}{joiner}page={page}"


async def _petco_scraperapi_get(
    client: httpx.AsyncClient,
    api_key: str,
    target_url: str,
) -> tuple[Optional[str], int]:
    params: dict[str, str] = {
        "api_key": api_key,
        "url": target_url,
        "country_code": _petco_scraperapi_country(),
        **(_petco_scraperapi_extra_params()),
    }
    resp = await client.get("https://api.scraperapi.com/", params=params)
    body = resp.text if resp.content else ""
    if resp.status_code != 200:
        snippet = body[:260].replace("\n", " ")
        logger.warning(
            "Petco ScraperAPI HTTP %s for %s ... %s",
            resp.status_code,
            target_url[:100],
            snippet,
        )
        return None, resp.status_code
    return body, resp.status_code


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
        """() => document.querySelectorAll("a[href*='/product/']").length"""
    )
    product_anchor_count = await _count_product_links(page)
    embedded_paths = len(dict.fromkeys(_PRODUCT_PATH_RE.findall(html)))
    html_l = html.lower()
    has_next_tiles = bool(_next_data_dict_from_html(html))
    bot_wall = (
        content_len < 4000
        or "captcha-delivery" in html_l
        or "please enable js" in html_l
        or (content_len < 50_000 and not has_next_tiles)
    )

    screenshot_path: str | None = None
    try:
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = _DEBUG_DIR / f"{label}_{ts}.png"
        await page.screenshot(path=str(path), full_page=False)
        screenshot_path = str(path)
    except Exception as exc:
        logger.warning("Petco debug screenshot failed: %s", exc)

    logger.info(
        "Petco scrape debug [%s]: content_len=%s anchors(/product/)=%s "
        "unique_product_links=%s embedded_paths=%s bot_wall=%s screenshot=%s",
        label,
        content_len,
        anchor_count,
        product_anchor_count,
        embedded_paths,
        bot_wall,
        screenshot_path,
    )
    return {
        "content_length": content_len,
        "anchors_product_pattern": anchor_count,
        "unique_product_links": product_anchor_count,
        "embedded_paths": embedded_paths,
        "blocked": bot_wall,
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
    for _ in range(3):
        await page.keyboard.press("End")
        await asyncio.sleep(random.uniform(0.6, 1.0))


async def _count_product_links(page: Page) -> int:
    return await page.evaluate(
        """() => {
            const seen = new Set();
            for (const a of document.querySelectorAll("a[href*='/product/']")) {
                const h = a.href || a.getAttribute('href') || '';
                if (/sponsored=1|cm_mmc=|adId=|utm_medium=cpc/i.test(h)) continue;
                const path = h.split('?')[0];
                if (!path || !path.includes('/product/') || seen.has(path)) continue;
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
                for (const a of document.querySelectorAll("a[href*='/product/']")) {
                    const h = a.href || a.getAttribute('href') || '';
                    if (/sponsored=1|cm_mmc=|adId=|utm_medium=cpc/i.test(h)) continue;
                    const path = h.split('?')[0];
                    if (path && path.includes('/product/')) seen.add(path);
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
                "el => el.tagName === 'A' && (el.href || '').includes('/product/')"
            )
            target = handle
            if not is_anchor:
                inner = await handle.query_selector("a[href*='/product/']")
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


def _parse_cookie_string(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in raw.strip().split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


def _parse_constructor_results(data: dict, scraper: BaseScraper, limit: int) -> list[ProductRaw]:
    products: list[ProductRaw] = []
    seen: set[str] = set()
    for item in data.get("response", {}).get("results", []):
        if len(products) >= limit:
            break
        try:
            d = item.get("data", {})
            title = (item.get("value") or d.get("name") or "").strip()
            if not title:
                continue
            url = d.get("url", "")
            product_url = (
                url if url.startswith("http") else f"https://www.petco.com{url}"
            )
            if product_url in seen:
                continue
            seen.add(product_url)

            price = None
            price_raw = _tile_price_from_dict(d)
            if price_raw:
                price = scraper.normalize_price(price_raw)
            if price is None:
                for pk in ("sale_price", "price", "list_price", "rdprice", "offerprice"):
                    val = d.get(pk)
                    if val:
                        price = scraper.normalize_price(str(val))
                        if price is not None:
                            break

            rating = None
            for rk in ("avg_rating", "average_rating", "rating"):
                val = d.get(rk)
                if val is not None:
                    try:
                        rating = float(val)
                        break
                    except (TypeError, ValueError):
                        pass

            review_count = 0
            for ck in ("review_count", "num_reviews", "total_reviews"):
                val = d.get(ck)
                if val is not None:
                    try:
                        review_count = int(val)
                        break
                    except (TypeError, ValueError):
                        pass

            products.append(
                ProductRaw(
                    source_site=scraper.SITE_NAME,
                    title=title[:200],
                    price=price,
                    avg_rating=rating,
                    review_count=review_count,
                    product_url=product_url,
                    image_url=d.get("image_url"),
                    scrape_status="ok",
                )
            )
        except Exception:
            continue
    return products


def _fetch_via_constructor_api(
    scraper: BaseScraper,
    limit: int,
    listing_pages: int = 1,
) -> list[ProductRaw]:
    """Bypass DataDome when PETCO_COOKIES + PETCO_CONSTRUCTOR_KEY are set (US session)."""
    cookie_raw = os.environ.get("PETCO_COOKIES", "").strip()
    api_key = os.environ.get("PETCO_CONSTRUCTOR_KEY", "").strip()
    if not cookie_raw or not api_key:
        return []

    cookies = _parse_cookie_string(cookie_raw)
    client_id = cookies.get("ConstructorioID_client_id", "")
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Referer": LISTING_URL,
        "Origin": "https://www.petco.com",
        "Cookie": cookie_raw,
    }

    from app.scrapers.base import _FETCH_PAGE_CAP

    per_page = 48
    pages = max(1, listing_pages)
    combined: list[ProductRaw] = []
    seen_urls: set[str] = set()

    for page_ix in range(1, pages + 1):
        ts = int(time.time() * 1000)
        url = (
            f"https://ac.cnstrc.com/browse/group_id/{_CONSTRUCTOR_GROUP}"
            f"?key={api_key}&c={client_id}"
            f"&num_results_per_page={per_page}&page={page_ix}&sort_by=relevance&_dt={ts}"
        )
        try:
            r = httpx.get(url, headers=headers, timeout=25.0)
            if r.status_code != 200:
                logger.warning(
                    "Petco Constructor API HTTP %s (page=%s): %s",
                    r.status_code,
                    page_ix,
                    r.text[:200],
                )
                break
            batch = _parse_constructor_results(r.json(), scraper, _FETCH_PAGE_CAP)
        except Exception as e:
            logger.warning("Petco Constructor API failed (page=%s): %s", page_ix, e)
            break

        new = 0
        for row in batch:
            u = (row.product_url or "").split("?")[0]
            if not u or u in seen_urls:
                continue
            seen_urls.add(u)
            combined.append(row)
            new += 1
        if not batch or new == 0:
            break

    return combined


class PetcoScraper(BaseScraper):
    SITE_NAME = "petco"

    async def _fetch_listings_via_scraperapi(
        self,
        query: str,
        limit: int,
        listing_pages: int = 1,
    ) -> list[ProductRaw]:
        api_key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if not api_key:
            return []

        listing_base = _listing_url(query)
        seen_urls: set[str] = set()
        products: list[ProductRaw] = []
        tout = _petco_scraperapi_timeout_sec()
        timeout_cfg = httpx.Timeout(tout, connect=min(30.0, tout))
        last_status = 0

        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                max_pages = min(max(1, listing_pages), _petco_scraperapi_max_pages())
                for page_ix in range(1, max_pages + 1):
                    page_url = _listing_url_page(listing_base, page_ix)
                    body, last_status = await _petco_scraperapi_get(
                        client, api_key, page_url
                    )
                    if not body:
                        break
                    batch = _products_from_next_data_html(body, self, seen_urls)
                    if not batch:
                        break
                    for row in batch:
                        products.append(row)
        except httpx.HTTPError as exc:
            note = (
                "Petco ScraperAPI HTTP error (%s). Check timeouts and connectivity."
                % type(exc).__name__
            )
            logger.exception(note)
            self._empty_scrape_note = "%s Details: %s" % (note, exc)
            return []

        if not products:
            self._empty_scrape_note = (
                "Petco ScraperAPI: 0 tiles from __NEXT_DATA__ "
                f"(last_http={last_status}). "
                "SCRAPERAPI_KEY tier/credits may have failed; try PETCO_SCRAPERAPI_PREMIUM=true."
            )
            logger.warning(self._empty_scrape_note)
        else:
            logger.info(
                "Petco ScraperAPI: fetched %s product tile(s) across %s page(s)",
                len(products),
                max_pages,
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
        from app.scrapers.base import _FETCH_PAGE_CAP

        if _petco_should_use_scraperapi():
            sa_products = await self._fetch_listings_via_scraperapi(
                query, limit, pages
            )
            if sa_products:
                return sa_products

        if _petco_should_use_constructor():
            api_products = await asyncio.to_thread(
                _fetch_via_constructor_api, self, limit, pages
            )
            if api_products:
                logger.info(
                    "Petco: %s products via Constructor API (%s page(s))",
                    len(api_products),
                    pages,
                )
                return api_products

        listing_base = _listing_url(query)
        seen_urls: set[str] = set()
        products: list[ProductRaw] = []
        n_links = 0
        after_hydrate: dict = {}

        async with async_playwright() as pw:
            async with self._stealth_page(pw) as page:
                for page_ix in range(1, pages + 1):
                    page_url = _listing_url_page(listing_base, page_ix)
                    await page.goto(
                        page_url, wait_until="domcontentloaded", timeout=70_000
                    )
                    await asyncio.sleep(random.uniform(1.5, 2.5))
                    await _dismiss_consent(page)

                    after_load = await _debug_instrumentation(page, "after_load")
                    if after_load.get("blocked"):
                        self._empty_scrape_note = (
                            f"Petco: bot/captcha wall (html_len="
                            f"{after_load.get('content_length')}). "
                            "Set SCRAPERAPI_KEY in backend/.env (ScraperAPI path)."
                        )
                        return products

                    n_links = await _wait_for_hydrated_grid(page)
                    await self.human_delay()
                    after_hydrate = await _debug_instrumentation(page, "after_hydrate")

                    links = await _collect_product_links(page, _FETCH_PAGE_CAP)
                    n_links = max(n_links, await _count_product_links(page))
                    batch = await _parse_dom_links(
                        links, self, seen_urls, _FETCH_PAGE_CAP
                    )
                    products.extend(batch)

                    html = await page.content()
                    embedded = _extract_embedded_from_html(
                        html, _FETCH_PAGE_CAP, seen_urls, self
                    )
                    if embedded:
                        logger.info(
                            "Petco page %s: DOM %s + embedded %s",
                            page_ix,
                            len(batch),
                            len(embedded),
                        )
                        products.extend(embedded)

                    if not batch and not embedded:
                        break

                if not products:
                    self._empty_scrape_note = (
                        f"Petco: 0 products (dom_links={n_links}, "
                        f"bot_wall={after_hydrate.get('blocked')}). "
                        "Set SCRAPERAPI_KEY in backend/.env."
                    )
                    logger.warning(self._empty_scrape_note)

        return products
