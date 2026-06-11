import html as html_module
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx
from dotenv import load_dotenv

from app.scrapers.base import BaseScraper, ProductRaw, _FETCH_PAGE_CAP

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_BACKEND_ROOT / ".env")

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.amazon.com/s?k={query}"
STRUCTURED_SEARCH_URL = "https://api.scraperapi.com/structured/amazon/search"

_ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.I)
_SPONSORED_MARKERS = ("spons", "sspa", "picassoredirect")
_SIZE_PHRASES = re.compile(
    r"\b(?:"
    r"extra[- ]?large|x[- ]?large|xx[- ]?large|xlarge|"
    r"large|medium|small|mini|jumbo|queen|king|"
    r"xl|xxl|xs|"
    r"\d+[\"']?\s*[x×]\s*\d+[\"']?|"
    r"\d+[\"']?\s*(?:inch|in\.?|in)\b"
    r")\b",
    re.I,
)

# Env (backend/.env) — Amazon via ScraperAPI only:
#   SCRAPERAPI_KEY — required
#   AMAZON_SCRAPERAPI_USE_STRUCTURED — default true (structured JSON endpoint)
#   AMAZON_SCRAPERAPI_TLD — default com
#   AMAZON_SCRAPERAPI_COUNTRY — default us
#   AMAZON_SCRAPERAPI_TIMEOUT — seconds (default 180)
#   AMAZON_SCRAPERAPI_MAX_PAGES — pagination cap (default 2)
# Fallback generic fetch (if structured disabled or empty):
#   AMAZON_SCRAPERAPI_ULTRA_PREMIUM — default true
#   AMAZON_SCRAPERAPI_RENDER — default false
#   AMAZON_SCRAPERAPI_PREMIUM — fallback if ultra disabled


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _amazon_scraperapi_country() -> str:
    return (os.environ.get("AMAZON_SCRAPERAPI_COUNTRY") or "us").strip() or "us"


def _amazon_scraperapi_tld() -> str:
    return (os.environ.get("AMAZON_SCRAPERAPI_TLD") or "com").strip() or "com"


def _amazon_scraperapi_extra_params() -> dict[str, str]:
    params: dict[str, str] = {}
    if _env_truthy("AMAZON_SCRAPERAPI_ULTRA_PREMIUM", default=True):
        params["ultra_premium"] = "true"
    elif _env_truthy("AMAZON_SCRAPERAPI_PREMIUM"):
        params["premium"] = "true"
    if _env_truthy("AMAZON_SCRAPERAPI_RENDER", default=False):
        params["render"] = "true"
    return params


def _amazon_scraperapi_timeout_sec() -> float:
    try:
        return float(os.environ.get("AMAZON_SCRAPERAPI_TIMEOUT") or "180")
    except ValueError:
        return 180.0


def _amazon_scraperapi_max_pages() -> int:
    try:
        n = int((os.environ.get("AMAZON_SCRAPERAPI_MAX_PAGES") or "2").strip())
        return max(1, min(n, 10))
    except ValueError:
        return 2


def _listing_url(query: str) -> str:
    q = (query or "").strip().lower()
    if not q or q in ("dog bed", "dog beds", "dog_beds"):
        return SEARCH_URL.format(query="dog+bed")
    return SEARCH_URL.format(query=quote_plus(q.replace(" ", "+")))


def _listing_url_page(listing_base: str, page: int) -> str:
    listing_base = listing_base.strip()
    if page <= 1:
        return listing_base
    joiner = "&" if "?" in listing_base else "?"
    return f"{listing_base}{joiner}page={page}"


def _extract_asin(url: str) -> str:
    m = _ASIN_RE.search(url or "")
    return m.group(1).upper() if m else ""


def _canonical_amazon_url(asin: str) -> str:
    return f"https://www.amazon.com/dp/{asin.upper()}"


def _amazon_variant_group_id(title: str, asin: str) -> str:
    """Group size/color variants (distinct ASINs) under one parent key for ranking."""
    t = html_module.unescape(title or "").lower()
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(
        r"\d+[\"']?\s*[x×]\s*\d+[\"']?(?:\s*(?:inch|in\.?|in))?",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(r"\d+[\"']?\s*(?:inch|in\.?)\b", " ", t, flags=re.I)
    t = _SIZE_PHRASES.sub(" ", t)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    t = re.sub(r"-+", "-", t)
    if len(t) >= 6:
        return t[:100]
    return asin.upper()


def _is_sponsored_url(url: str) -> bool:
    low = (url or "").lower()
    return any(marker in low for marker in _SPONSORED_MARKERS)


def _normalize_amazon_image_url(raw: str) -> Optional[str]:
    if not raw or not str(raw).strip():
        return None
    url = str(raw).strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"https://www.amazon.com{url}"
    if url.startswith("http"):
        return url
    return None


def _normalize_price_value(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    m = re.search(r"\d+\.?\d*", str(raw).replace(",", "").replace("$", ""))
    return float(m.group()) if m else None


def _best_by_variant_group(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    no_key: list[dict[str, Any]] = []

    for item in items:
        pk = str(item.get("variant_group_id") or "").strip()
        if not pk:
            no_key.append(item)
            continue
        if pk not in best:
            best[pk] = item
            order.append(pk)
            continue
        cur = best[pk]
        cur_reviews = int(cur.get("review_count") or 0)
        new_reviews = int(item.get("review_count") or 0)
        if new_reviews > cur_reviews:
            best[pk] = item

    return [best[pk] for pk in order] + no_key


def _collapse_products_by_variant_group(products: list[ProductRaw]) -> list[ProductRaw]:
    """After multi-page merge: one row per product family (highest review count wins)."""
    best: dict[str, ProductRaw] = {}
    order: list[str] = []
    for p in products:
        key = (p.variant_group_id or p.product_url or p.title or "").strip()
        if not key:
            continue
        if key not in best:
            best[key] = p
            order.append(key)
            continue
        cur = best[key]
        if (p.review_count or 0) > (cur.review_count or 0):
            best[key] = p
    return [best[k] for k in order]


def _product_from_raw(
    scraper: BaseScraper, raw: dict, seen_urls: set[str]
) -> ProductRaw | None:
    asin = str(raw.get("asin") or raw.get("variant_group_id") or "").strip().upper()
    href = raw.get("href") or raw.get("product_url") or ""
    if not asin and href:
        asin = _extract_asin(str(href))
    if not asin:
        return None

    product_url = _canonical_amazon_url(asin)
    if product_url in seen_urls or _is_sponsored_url(str(href)):
        return None
    seen_urls.add(product_url)

    title = html_module.unescape((raw.get("title") or "").strip())
    if not title or len(title) < 3:
        return None

    review_count = raw.get("review_count") or 0
    try:
        review_count = int(review_count)
    except (TypeError, ValueError):
        review_count = 0

    avg_rating = raw.get("avg_rating")
    if avg_rating is not None:
        try:
            avg_rating = float(avg_rating)
        except (TypeError, ValueError):
            avg_rating = scraper.normalize_rating(str(avg_rating))

    price = raw.get("price")
    if price is None:
        price = scraper.normalize_price(raw.get("priceText") or "")

    image = raw.get("imageUrl") or raw.get("image_url")
    image_url = _normalize_amazon_image_url(str(image)) if image else None

    return ProductRaw(
        source_site=scraper.SITE_NAME,
        title=title[:200],
        price=price,
        avg_rating=avg_rating,
        review_count=review_count,
        product_url=product_url,
        image_url=image_url,
        variant_group_id=raw.get("variant_group_id") or _amazon_variant_group_id(title, asin),
        scrape_status="ok",
    )


def _tile_from_structured_item(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    if str(item.get("type") or "search_product") != "search_product":
        return None

    title = html_module.unescape((item.get("name") or "").strip())
    if not title or len(title) < 3:
        return None

    asin = str(item.get("asin") or "").strip().upper()
    url = str(item.get("url") or "")
    if not asin:
        asin = _extract_asin(url)
    if not asin or _is_sponsored_url(url):
        return None

    review_count = 0
    try:
        review_count = int(item.get("total_reviews") or 0)
    except (TypeError, ValueError):
        review_count = 0

    stars = item.get("stars")
    avg_rating = None
    if stars is not None:
        try:
            avg_rating = float(stars)
        except (TypeError, ValueError):
            avg_rating = None

    image = str(item.get("image") or "")
    parent_key = _amazon_variant_group_id(title, asin)
    return {
        "title": title[:200],
        "price": _normalize_price_value(item.get("price")),
        "avg_rating": avg_rating,
        "review_count": review_count,
        "imageUrl": _normalize_amazon_image_url(image) if image else None,
        "asin": asin,
        "variant_group_id": parent_key,
        "href": _canonical_amazon_url(asin),
    }


def _products_from_structured(
    data: dict[str, Any],
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    results = data.get("results") or []
    if not isinstance(results, list):
        return []

    tiles: list[dict[str, Any]] = []
    for item in results:
        if isinstance(item, dict):
            raw = _tile_from_structured_item(item)
            if raw:
                tiles.append(raw)

    products: list[ProductRaw] = []
    for item in _best_by_variant_group(tiles):
        if len(products) >= limit:
            break
        row = _product_from_raw(scraper, item, seen_urls)
        if row:
            products.append(row)
    return products


def _products_from_search_html(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    tiles: list[dict[str, Any]] = []

    for card in re.split(r'data-component-type="s-search-result"', html, flags=re.I)[1:]:
        asin_m = re.search(r'data-asin="([A-Z0-9]{10})"', card, re.I)
        if not asin_m:
            continue
        asin = asin_m.group(1).upper()
        if asin in ("", "0000000000"):
            continue

        title_m = re.search(
            r'<h2[^>]*>.*?<span[^>]*>([^<]{5,200})</span>',
            card,
            re.I | re.S,
        )
        if not title_m:
            title_m = re.search(
                r'data-cy="title-recipe"[^>]*>.*?<span[^>]*>([^<]{5,200})</span>',
                card,
                re.I | re.S,
            )
        if not title_m:
            continue
        title = html_module.unescape(title_m.group(1).strip())

        link_m = re.search(r'href="(/[^"]+/dp/[^"]+)"', card, re.I)
        href = link_m.group(1) if link_m else ""
        if _is_sponsored_url(href) or _is_sponsored_url(card):
            continue

        price_m = re.search(
            r'class="a-offscreen"[^>]*>\$?([\d,.]+)<',
            card,
            re.I,
        )
        rating_m = re.search(
            r'aria-label="([\d.]+)\s+out of 5 stars"',
            card,
            re.I,
        )
        review_m = re.search(
            r'aria-label="([\d,]+)\s+ratings?"',
            card,
            re.I,
        )
        if not review_m:
            review_m = re.search(
                r'class="a-size-base s-underline-text"[^>]*>([\d,]+)<',
                card,
                re.I,
            )

        img_m = re.search(r'class="s-image"[^>]+src="([^"]+)"', card, re.I)
        if not img_m:
            img_m = re.search(
                r'<img[^>]+class="[^"]*s-image[^"]*"[^>]+src="([^"]+)"',
                card,
                re.I,
            )

        parent_key = _amazon_variant_group_id(title, asin)
        tiles.append(
            {
                "title": title[:200],
                "price": _normalize_price_value(price_m.group(1)) if price_m else None,
                "avg_rating": float(rating_m.group(1)) if rating_m else None,
                "review_count": int(review_m.group(1).replace(",", ""))
                if review_m
                else 0,
                "imageUrl": img_m.group(1) if img_m else None,
                "asin": asin,
                "variant_group_id": parent_key,
                "href": _canonical_amazon_url(asin),
            }
        )

    products: list[ProductRaw] = []
    for item in _best_by_variant_group(tiles):
        if len(products) >= limit:
            break
        row = _product_from_raw(scraper, item, seen_urls)
        if row:
            products.append(row)
    return products


def _parse_amazon_html(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    return _products_from_search_html(html, scraper, seen_urls, limit)


def _amazon_block_detected(html: str) -> bool:
    low = (html or "").lower()
    if len(html or "") < 8_000:
        return True
    if "robot check" in low or "type the characters you see" in low:
        return True
    if "captcha" in low and 'data-component-type="s-search-result"' not in low:
        return True
    return False


async def _amazon_structured_get(
    client: httpx.AsyncClient,
    api_key: str,
    query: str,
    *,
    page: int = 1,
) -> tuple[Optional[dict[str, Any]], int]:
    params = {
        "api_key": api_key,
        "query": query,
        "country_code": _amazon_scraperapi_country(),
        "tld": _amazon_scraperapi_tld(),
        "page": str(page),
    }
    resp = await client.get(STRUCTURED_SEARCH_URL, params=params)
    if resp.status_code != 200:
        snippet = (resp.text or "")[:260].replace("\n", " ")
        logger.warning(
            "Amazon structured ScraperAPI HTTP %s for query=%r ... %s",
            resp.status_code,
            query[:80],
            snippet,
        )
        return None, resp.status_code
    try:
        return resp.json(), resp.status_code
    except json.JSONDecodeError as exc:
        logger.warning("Amazon structured JSON decode failed: %s", exc)
        return None, resp.status_code


async def _amazon_generic_get(
    client: httpx.AsyncClient,
    api_key: str,
    target_url: str,
) -> tuple[Optional[str], int]:
    params: dict[str, str] = {
        "api_key": api_key,
        "url": target_url,
        "country_code": _amazon_scraperapi_country(),
        **_amazon_scraperapi_extra_params(),
    }
    resp = await client.get("https://api.scraperapi.com/", params=params)
    body = resp.text if resp.content else ""
    if resp.status_code != 200:
        snippet = body[:260].replace("\n", " ")
        logger.warning(
            "Amazon generic ScraperAPI HTTP %s for %s ... %s",
            resp.status_code,
            target_url[:100],
            snippet,
        )
        return None, resp.status_code
    return body, resp.status_code


def products_from_structured_for_tests(
    data: dict[str, Any], limit: int = 20
) -> list[ProductRaw]:
    """Public for tests: parse structured Amazon search JSON into ProductRaw rows."""
    scraper = AmazonScraper()
    seen: set[str] = set()
    return _products_from_structured(data, scraper, seen, limit)


def products_from_html_for_tests(html: str, limit: int = 20) -> list[ProductRaw]:
    """Public for tests: parse Amazon search HTML into ProductRaw rows."""
    scraper = AmazonScraper()
    seen: set[str] = set()
    return _parse_amazon_html(html, scraper, seen, limit)


class AmazonScraper(BaseScraper):
    SITE_NAME = "amazon"

    async def _fetch_listings_via_scraperapi(
        self,
        query: str,
        limit: int,
        listing_pages: int = 1,
    ) -> list[ProductRaw]:
        api_key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if not api_key:
            self._empty_scrape_note = "Amazon requires SCRAPERAPI_KEY in backend/.env"
            return []

        seen_urls: set[str] = set()
        products: list[ProductRaw] = []
        last_status = 0
        tout = _amazon_scraperapi_timeout_sec()
        timeout_cfg = httpx.Timeout(tout, connect=min(30.0, tout))
        listing_base = _listing_url(query)
        search_query = (query or "dog bed").strip() or "dog bed"
        max_pages = min(max(1, listing_pages), _amazon_scraperapi_max_pages())

        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                if _env_truthy("AMAZON_SCRAPERAPI_USE_STRUCTURED", default=True):
                    for page_ix in range(1, max_pages + 1):
                        data, last_status = await _amazon_structured_get(
                            client, api_key, search_query, page=page_ix
                        )
                        if not data:
                            break
                        batch = _products_from_structured(
                            data, self, seen_urls, _FETCH_PAGE_CAP
                        )
                        if not batch:
                            break
                        products.extend(batch)

                if not products:
                    for page_ix in range(1, max_pages + 1):
                        page_url = _listing_url_page(listing_base, page_ix)
                        body, last_status = await _amazon_generic_get(
                            client, api_key, page_url
                        )
                        if not body:
                            break
                        if _amazon_block_detected(body):
                            self._empty_scrape_note = (
                                "Amazon ScraperAPI: block or CAPTCHA detected on generic fetch. "
                                "Keep AMAZON_SCRAPERAPI_USE_STRUCTURED=true."
                            )
                            logger.warning(self._empty_scrape_note)
                            break
                        batch = _parse_amazon_html(
                            body, self, seen_urls, _FETCH_PAGE_CAP
                        )
                        if not batch:
                            break
                        products.extend(batch)
        except httpx.HTTPError as exc:
            note = (
                "Amazon ScraperAPI HTTP error (%s). Check timeouts and connectivity."
                % type(exc).__name__
            )
            logger.exception(note)
            self._empty_scrape_note = "%s Details: %s" % (note, exc)
            return []

        if not products:
            self._empty_scrape_note = (
                "Amazon ScraperAPI: 0 products parsed "
                f"(last_http={last_status}). "
                "Use AMAZON_SCRAPERAPI_USE_STRUCTURED=true (default)."
            )
            logger.warning(self._empty_scrape_note)
        else:
            before = len(products)
            products = _collapse_products_by_variant_group(products)
            logger.info(
                "Amazon ScraperAPI: fetched %s product(s) across %s page(s) "
                "(%s after variant collapse)",
                before,
                max_pages,
                len(products),
            )

        return products

    async def fetch_listings(
        self,
        query: str = "dog bed",
        limit: int = 20,
        *,
        listing_pages: int = 1,
    ) -> list[ProductRaw]:
        api_key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if not api_key:
            self._empty_scrape_note = "Amazon requires SCRAPERAPI_KEY in backend/.env"
            return []
        return await self._fetch_listings_via_scraperapi(query, limit, listing_pages)
