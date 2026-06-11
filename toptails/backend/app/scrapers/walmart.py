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

SEARCH_URL = "https://www.walmart.com/search?q={query}"

_PRODUCT_PATH_RE = re.compile(r"/ip/[a-z0-9-]+/\d+", re.IGNORECASE)
_SPONSORED_MARKERS = ("sponsored=1", "spQs=", "wmlspartner", "TCID=OGS")

# Env (backend/.env) — Walmart via ScraperAPI only:
#   SCRAPERAPI_KEY — required
#   WALMART_SCRAPERAPI_ULTRA_PREMIUM — default true
#   WALMART_SCRAPERAPI_RENDER — default false (render returns HTTP 500; __NEXT_DATA__ is in SSR HTML)
#   WALMART_SCRAPERAPI_PREMIUM — fallback if ultra disabled
#   WALMART_SCRAPERAPI_TIMEOUT — seconds (default 180)
#   WALMART_SCRAPERAPI_COUNTRY — e.g. us (default us)
#   WALMART_SCRAPERAPI_MAX_PAGES — pagination cap (default 2)


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _walmart_scraperapi_country() -> str:
    return (os.environ.get("WALMART_SCRAPERAPI_COUNTRY") or "us").strip() or "us"


def _walmart_scraperapi_extra_params() -> dict[str, str]:
    params: dict[str, str] = {}
    if _env_truthy("WALMART_SCRAPERAPI_ULTRA_PREMIUM", default=True):
        params["ultra_premium"] = "true"
    elif _env_truthy("WALMART_SCRAPERAPI_PREMIUM"):
        params["premium"] = "true"
    if _env_truthy("WALMART_SCRAPERAPI_RENDER", default=False):
        params["render"] = "true"
    return params


def _walmart_scraperapi_timeout_sec() -> float:
    try:
        return float(os.environ.get("WALMART_SCRAPERAPI_TIMEOUT") or "180")
    except ValueError:
        return 180.0


def _walmart_scraperapi_max_pages() -> int:
    try:
        n = int((os.environ.get("WALMART_SCRAPERAPI_MAX_PAGES") or "2").strip())
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


def _is_sponsored_context(snippet: str) -> bool:
    return any(marker in (snippet or "") for marker in _SPONSORED_MARKERS)


def _product_url_from_path(path: str) -> str:
    path = path.split("?")[0].split("#")[0]
    if path.startswith("http"):
        return path
    return f"https://www.walmart.com{path}"


def _normalize_price_value(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    m = re.search(r"\d+\.?\d*", str(raw).replace(",", "").replace("$", ""))
    return float(m.group()) if m else None


def _normalize_walmart_image_url(raw: str) -> Optional[str]:
    if not raw or not str(raw).strip():
        return None
    url = str(raw).strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"https://www.walmart.com{url}"
    if url.startswith("http"):
        return url
    return None


def _walmart_parent_key(item: dict[str, Any]) -> str:
    parent = item.get("catalogProductId") or item.get("id")
    if parent is not None and str(parent).strip():
        return str(parent).strip()
    us_item = item.get("usItemId")
    return str(us_item).strip() if us_item is not None else ""


def _best_tiles_by_parent(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    no_parent: list[dict[str, Any]] = []

    for item in items:
        pk = str(item.get("variant_group_id") or "").strip()
        if not pk:
            no_parent.append(item)
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

    return [best[pk] for pk in order] + no_parent


def _item_stacks_from_next_data(nd: dict[str, Any]) -> list[dict[str, Any]]:
    stacks = (
        nd.get("props", {})
        .get("pageProps", {})
        .get("initialData", {})
        .get("searchResult", {})
        .get("itemStacks", [])
    )
    if not isinstance(stacks, list):
        return []
    items: list[dict[str, Any]] = []
    for stack in stacks:
        if not isinstance(stack, dict):
            continue
        for item in stack.get("items") or []:
            if isinstance(item, dict):
                items.append(item)
    return items


def _tile_from_item(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    if item.get("isSponsoredFlag"):
        return None

    typename = str(item.get("__typename") or "")
    us_item_id = item.get("usItemId")
    if typename and typename not in ("Product", "SearchProduct") and not us_item_id:
        return None

    title = html_module.unescape((item.get("name") or "").strip())
    if not title or len(title) < 3:
        return None

    url = item.get("canonicalUrl") or ""
    if not url and us_item_id:
        url = f"/ip/-/{us_item_id}"
    if not url or "/ip/" not in str(url):
        return None
    product_url = _product_url_from_path(str(url))
    if _is_sponsored_context(product_url):
        return None

    price_info = item.get("priceInfo") or {}
    price = None
    if isinstance(price_info, dict):
        cur = price_info.get("currentPrice") or {}
        if isinstance(cur, dict):
            price = _normalize_price_value(cur.get("price"))
        if price is None:
            price = _normalize_price_value(price_info.get("linePrice"))

    image_info = item.get("imageInfo") or {}
    image = ""
    if isinstance(image_info, dict):
        image = str(image_info.get("thumbnailUrl") or image_info.get("imageUrl") or "")

    review_count = 0
    try:
        review_count = int(item.get("numberOfReviews") or 0)
    except (TypeError, ValueError):
        review_count = 0

    avg_rating = item.get("averageRating")
    if avg_rating is not None:
        try:
            avg_rating = float(avg_rating)
        except (TypeError, ValueError):
            avg_rating = None

    parent_key = _walmart_parent_key(item)

    return {
        "href": product_url,
        "title": title[:200],
        "price": price,
        "avg_rating": avg_rating,
        "review_count": review_count,
        "imageUrl": _normalize_walmart_image_url(image) if image else None,
        "variant_group_id": parent_key or None,
    }


def _product_from_raw(
    scraper: BaseScraper, raw: dict, seen_urls: set[str]
) -> ProductRaw | None:
    href = raw.get("href") or raw.get("product_url") or ""
    if not href or _is_sponsored_context(str(href)):
        return None

    product_url = _product_url_from_path(str(href))
    if product_url in seen_urls:
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

    variant_group_id = raw.get("variant_group_id")
    if variant_group_id is not None:
        variant_group_id = str(variant_group_id).strip() or None

    image = raw.get("imageUrl") or raw.get("image_url")
    image_url = _normalize_walmart_image_url(str(image)) if image else None

    return ProductRaw(
        source_site=scraper.SITE_NAME,
        title=title[:200],
        price=price,
        avg_rating=avg_rating,
        review_count=review_count,
        product_url=product_url,
        image_url=image_url,
        variant_group_id=variant_group_id,
        scrape_status="ok",
    )


def _rows_from_item_stacks(
    items: list[dict[str, Any]],
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    tiles: list[dict[str, Any]] = []
    for item in items:
        raw = _tile_from_item(item)
        if raw:
            tiles.append(raw)

    products: list[ProductRaw] = []
    for item in _best_tiles_by_parent(tiles):
        if len(products) >= limit:
            break
        row = _product_from_raw(scraper, item, seen_urls)
        if row:
            products.append(row)
    return products


def _products_from_next_data_html(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        nd = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    return _rows_from_item_stacks(
        _item_stacks_from_next_data(nd), scraper, seen_urls, limit
    )


def _products_from_ld_json(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    products: list[ProductRaw] = []
    for blob in re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue

        items: list[dict[str, Any]] = []
        if isinstance(obj, dict):
            if obj.get("@type") == "ItemList":
                items = [
                    e.get("item") or e
                    for e in obj.get("itemListElement", [])
                    if isinstance(e, dict)
                ]
            elif obj.get("@type") == "Product":
                items = [obj]

        for item in items:
            if len(products) >= limit or not isinstance(item, dict):
                continue
            url_p = item.get("url") or item.get("@id") or ""
            if not isinstance(url_p, str) or "/ip/" not in url_p:
                continue
            row = _product_from_raw(
                scraper,
                {
                    "href": url_p,
                    "title": (item.get("name") or "")[:200],
                    "price": _normalize_price_value(
                        (item.get("offers") or {}).get("price")
                        if isinstance(item.get("offers"), dict)
                        else None
                    ),
                    "avg_rating": _normalize_price_value(
                        (item.get("aggregateRating") or {}).get("ratingValue")
                        if isinstance(item.get("aggregateRating"), dict)
                        else None
                    ),
                    "review_count": int(
                        (item.get("aggregateRating") or {}).get("reviewCount") or 0
                    )
                    if isinstance(item.get("aggregateRating"), dict)
                    else 0,
                    "imageUrl": item.get("image")
                    if isinstance(item.get("image"), str)
                    else None,
                },
                seen_urls,
            )
            if row:
                products.append(row)
    return products


def _products_from_product_cards(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    products: list[ProductRaw] = []
    for card in re.split(r'data-item-id="', html)[1:]:
        if len(products) >= limit:
            break
        title_m = re.search(
            r'data-automation-id="product-title"[^>]*>([^<]{5,200})<',
            card,
            re.I,
        )
        link_m = re.search(r'href="(/ip/[^"]+)"', card)
        if not title_m or not link_m:
            continue
        rating_m = re.search(
            r'aria-label="([\d.]+) out of 5[^"]*".*?aria-label="([\d,]+) reviews"',
            card,
            re.I | re.S,
        )
        prices = re.findall(r"\$[\d.]+", card)
        row = _product_from_raw(
            scraper,
            {
                "href": link_m.group(1),
                "title": title_m.group(1).strip(),
                "price": scraper.normalize_price(prices[0]) if prices else None,
                "avg_rating": float(rating_m.group(1)) if rating_m else None,
                "review_count": int(rating_m.group(2).replace(",", ""))
                if rating_m
                else 0,
            },
            seen_urls,
        )
        if row:
            products.append(row)
    return products


def _products_from_path_regex(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    products: list[ProductRaw] = []
    for path in dict.fromkeys(_PRODUCT_PATH_RE.findall(html)):
        if len(products) >= limit:
            break
        idx = html.find(path)
        window = html[max(0, idx - 120) : idx + len(path) + 200] if idx >= 0 else path
        if _is_sponsored_context(window):
            continue
        slug = path.split("/ip/")[-1].rsplit("/", 1)[0].replace("-", " ").title()
        row = _product_from_raw(
            scraper,
            {
                "href": path,
                "title": slug,
                "priceText": "",
                "avg_rating": None,
                "review_count": 0,
            },
            seen_urls,
        )
        if row:
            products.append(row)
    return products


def _parse_walmart_html(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    for parser in (
        _products_from_next_data_html,
        _products_from_ld_json,
        _products_from_product_cards,
        _products_from_path_regex,
    ):
        batch = parser(html, scraper, seen_urls, limit)
        if batch:
            return batch[:limit]
    return []


def _walmart_block_detected(html: str) -> bool:
    low = (html or "").lower()
    if len(html or "") < 8_000:
        return True
    if "access denied" in low:
        return True
    # "captcha" may appear in JSON even when itemStacks is populated
    if ("robot check" in low or "verify you are human" in low) and len(html) < 50_000:
        return True
    return False


async def _walmart_scraperapi_get(
    client: httpx.AsyncClient,
    api_key: str,
    target_url: str,
) -> tuple[Optional[str], int]:
    params: dict[str, str] = {
        "api_key": api_key,
        "url": target_url,
        "country_code": _walmart_scraperapi_country(),
        **_walmart_scraperapi_extra_params(),
    }
    resp = await client.get("https://api.scraperapi.com/", params=params)
    body = resp.text if resp.content else ""
    if resp.status_code != 200:
        snippet = body[:260].replace("\n", " ")
        logger.warning(
            "Walmart ScraperAPI HTTP %s for %s ... %s",
            resp.status_code,
            target_url[:100],
            snippet,
        )
        return None, resp.status_code
    return body, resp.status_code


def products_from_next_data_for_tests(data: dict[str, Any], limit: int = 20) -> list[ProductRaw]:
    """Public for tests: parse __NEXT_DATA__ dict into ProductRaw rows."""
    scraper = WalmartScraper()
    seen: set[str] = set()
    return _rows_from_item_stacks(_item_stacks_from_next_data(data), scraper, seen, limit)


def products_from_html_for_tests(html: str, limit: int = 20) -> list[ProductRaw]:
    """Public for tests: parse Walmart PLP HTML into ProductRaw rows."""
    scraper = WalmartScraper()
    seen: set[str] = set()
    return _parse_walmart_html(html, scraper, seen, limit)


class WalmartScraper(BaseScraper):
    SITE_NAME = "walmart"

    async def _fetch_listings_via_scraperapi(
        self,
        query: str,
        limit: int,
        listing_pages: int = 1,
    ) -> list[ProductRaw]:
        api_key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if not api_key:
            self._empty_scrape_note = "Walmart requires SCRAPERAPI_KEY in backend/.env"
            return []

        listing_base = _listing_url(query)
        seen_urls: set[str] = set()
        products: list[ProductRaw] = []
        last_status = 0
        tout = _walmart_scraperapi_timeout_sec()
        timeout_cfg = httpx.Timeout(tout, connect=min(30.0, tout))
        max_pages = min(max(1, listing_pages), _walmart_scraperapi_max_pages())

        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                for page_ix in range(1, max_pages + 1):
                    page_url = _listing_url_page(listing_base, page_ix)
                    body, last_status = await _walmart_scraperapi_get(
                        client, api_key, page_url
                    )
                    if not body:
                        break
                    if _walmart_block_detected(body):
                        self._empty_scrape_note = (
                            "Walmart ScraperAPI: block or empty shell detected. "
                            "Use WALMART_SCRAPERAPI_ULTRA_PREMIUM=true and keep WALMART_SCRAPERAPI_RENDER=false."
                        )
                        logger.warning(self._empty_scrape_note)
                        break
                    batch = _parse_walmart_html(
                        body, self, seen_urls, _FETCH_PAGE_CAP
                    )
                    if not batch:
                        break
                    products.extend(batch)
        except httpx.HTTPError as exc:
            note = (
                "Walmart ScraperAPI HTTP error (%s). Check timeouts and connectivity."
                % type(exc).__name__
            )
            logger.exception(note)
            self._empty_scrape_note = "%s Details: %s" % (note, exc)
            return []

        if not products:
            self._empty_scrape_note = (
                "Walmart ScraperAPI: 0 products parsed "
                f"(last_http={last_status}). "
                "Use WALMART_SCRAPERAPI_ULTRA_PREMIUM=true and WALMART_SCRAPERAPI_RENDER=false."
            )
            logger.warning(self._empty_scrape_note)
        else:
            logger.info(
                "Walmart ScraperAPI: fetched %s product(s) across %s page(s)",
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
        api_key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if not api_key:
            self._empty_scrape_note = "Walmart requires SCRAPERAPI_KEY in backend/.env"
            return []
        return await self._fetch_listings_via_scraperapi(query, limit, listing_pages)
