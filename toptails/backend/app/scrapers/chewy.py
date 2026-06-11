import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx
from dotenv import load_dotenv

from app.scrapers.base import BaseScraper, ProductRaw, _FETCH_PAGE_CAP

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_BACKEND_ROOT / ".env")

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.chewy.com/b/dog-beds-365"
SEARCH_URL = "https://www.chewy.com/s?query={query}"

_PRODUCT_PATH_RE = re.compile(
    r"(?:https://www\.chewy\.com)?(/[a-z0-9][a-z0-9-]*/dp/\d+)",
    re.IGNORECASE,
)

_SPONSORED_MARKERS = ("sponsored=1", "cm_mmc=", "adId=", "utm_medium=cpc")

# Env (backend/.env) — Chewy via ScraperAPI only:
#   SCRAPERAPI_KEY — required
#   CHEWY_SCRAPERAPI_ULTRA_PREMIUM — default true (Chewy needs ultra on most plans)
#   CHEWY_SCRAPERAPI_RENDER — default false (render+Chewy often returns HTTP 500)
#   CHEWY_SCRAPERAPI_PREMIUM — only if ultra disabled
#   CHEWY_SCRAPERAPI_TIMEOUT — seconds (default 120)
#   CHEWY_SCRAPERAPI_COUNTRY — e.g. us (default us)
#   CHEWY_SCRAPERAPI_MAX_PAGES — pagination cap (default 2)


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _chewy_scraperapi_country() -> str:
    return (os.environ.get("CHEWY_SCRAPERAPI_COUNTRY") or "us").strip() or "us"


def _chewy_scraperapi_extra_params() -> dict[str, str]:
    params: dict[str, str] = {}
    if _env_truthy("CHEWY_SCRAPERAPI_ULTRA_PREMIUM", default=True):
        params["ultra_premium"] = "true"
    elif _env_truthy("CHEWY_SCRAPERAPI_PREMIUM"):
        params["premium"] = "true"
    # SSR __NEXT_DATA__ is available without JS render; render often causes HTTP 500.
    if _env_truthy("CHEWY_SCRAPERAPI_RENDER", default=False):
        params["render"] = "true"
    return params


def _chewy_scraperapi_timeout_sec() -> float:
    try:
        return float(os.environ.get("CHEWY_SCRAPERAPI_TIMEOUT") or "120")
    except ValueError:
        return 180.0


def _chewy_scraperapi_max_pages() -> int:
    try:
        n = int((os.environ.get("CHEWY_SCRAPERAPI_MAX_PAGES") or "2").strip())
        return max(1, min(n, 10))
    except ValueError:
        return 2


def _listing_url(query: str) -> str:
    q = (query or "").strip().lower()
    if not q or q in ("dog bed", "dog beds", "dog_beds"):
        return LISTING_URL
    return SEARCH_URL.format(query=query.replace(" ", "+"))


def _listing_url_page(listing_base: str, page: int) -> str:
    listing_base = listing_base.strip()
    if page <= 1:
        return listing_base
    joiner = "&" if "?" in listing_base else "?"
    return f"{listing_base}{joiner}page={page}"


def _is_sponsored_context(snippet: str) -> bool:
    return any(marker in snippet for marker in _SPONSORED_MARKERS)


def _title_from_path(path: str) -> str:
    slug = path.strip("/").split("/dp/")[0].split("/")[-1]
    return slug.replace("-", " ").strip().title()


def _normalize_chewy_url(href: str) -> str:
    href = href.split("?")[0]
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://www.chewy.com{href}"
    return f"https://www.chewy.com/{href.lstrip('/')}"


_CHEWY_IMAGE_SIZE_SUFFIX = "._SX500_SY400_QL75_V1_.jpg"


def _normalize_chewy_image_url(raw: str) -> Optional[str]:
    """Chewy PLP uses protocol-relative moe IDs; expand to a loadable JPEG URL."""
    if not raw or not str(raw).strip():
        return None
    url = str(raw).strip()
    if url.startswith("//"):
        url = f"https:{url}"
    elif url.startswith("/"):
        url = f"https://image.chewy.com{url}"
    elif not url.startswith("http"):
        return None
    if "._SX" not in url and re.search(r",\d+$", url):
        url = re.sub(r",\d+$", _CHEWY_IMAGE_SIZE_SUFFIX, url)
    return url


def _chewy_parent_key(item: dict[str, Any]) -> str:
    parent = item.get("parentPartNumber")
    if parent is not None and str(parent).strip():
        return str(parent).strip()
    part = item.get("partNumber") or item.get("part")
    return str(part).strip() if part is not None else ""


def _chewy_href_is_ad(href: str) -> bool:
    return "api/event" in (href or "")


def _chewy_best_tiles_by_parent(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One tile per parent SKU — prefer direct /dp/ links over ad redirect URLs."""
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    no_parent: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        pk = _chewy_parent_key(item)
        if not pk:
            no_parent.append(item)
            continue
        href = str(item.get("href") or "")
        if pk not in best:
            best[pk] = item
            order.append(pk)
            continue
        cur = best[pk]
        cur_href = str(cur.get("href") or "")
        if _chewy_href_is_ad(cur_href) and not _chewy_href_is_ad(href):
            best[pk] = item

    return [best[pk] for pk in order] + no_parent


def _chewy_product_url(href: str) -> Optional[str]:
    """Resolve PLP tile href to a canonical /dp/ product URL."""
    if not href or not str(href).strip():
        return None
    href = str(href).strip()
    if "/dp/" in href and "api/event" not in href:
        return _normalize_chewy_url(href)
    if "redirect=" in href:
        redirect = (parse_qs(urlparse(href).query).get("redirect") or [None])[0]
        if redirect and "/dp/" in redirect:
            return _normalize_chewy_url(redirect)
    return None


def _chewy_tile_to_raw(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    href = item.get("href") or item.get("canonicalUrl") or item.get("url") or ""
    product_url = _chewy_product_url(str(href))
    if not product_url:
        part = item.get("partNumber") or item.get("part") or item.get("id")
        if part:
            product_url = f"https://www.chewy.com/dp/{part}"
        else:
            return None

    title = (item.get("name") or item.get("title") or "").strip()
    if not title:
        return None

    review_count = item.get("reviewCount") or item.get("ratingCount") or 0
    try:
        review_count = int(review_count)
    except (TypeError, ValueError):
        review_count = 0

    price = item.get("price") or item.get("salePrice") or item.get("advertisedPrice")
    if price is None and item.get("displayPrice"):
        price = item.get("displayPrice")

    rating = item.get("rating") or item.get("averageRating")
    avg_rating: Optional[float] = None
    if rating is not None:
        try:
            avg_rating = float(rating)
        except (TypeError, ValueError):
            avg_rating = None

    image = item.get("image") or item.get("imageUrl") or ""
    if not image and isinstance(item.get("images"), list) and item["images"]:
        first = item["images"][0]
        if isinstance(first, dict):
            image = first.get("url") or first.get("src") or ""
        elif isinstance(first, str):
            image = first

    parent_key = _chewy_parent_key(item)

    return {
        "href": product_url,
        "title": title[:200],
        "price": price,
        "avg_rating": avg_rating,
        "review_count": review_count,
        "imageUrl": _normalize_chewy_image_url(str(image)) if image else None,
        "variant_group_id": parent_key or None,
    }


def _product_from_raw(
    scraper: BaseScraper, raw: dict, seen_urls: set[str]
) -> ProductRaw | None:
    href = raw.get("href") or raw.get("product_url") or ""
    if not href or _is_sponsored_context(href):
        return None

    product_url = _normalize_chewy_url(href)
    if product_url in seen_urls:
        return None
    seen_urls.add(product_url)

    title = (raw.get("title") or "").strip()
    if not title or len(title) < 3:
        return None

    review_raw = raw.get("reviewRaw") or raw.get("review_count") or "0"
    review_count = 0
    if isinstance(review_raw, int):
        review_count = review_raw
    else:
        m = re.search(r"\d+", str(review_raw).replace(",", ""))
        if m:
            review_count = int(m.group())

    rating_raw = raw.get("ratingRaw") or ""
    avg_rating = raw.get("avg_rating")
    if avg_rating is None and rating_raw:
        avg_rating = scraper.normalize_rating(str(rating_raw))
    elif avg_rating is not None:
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

    return ProductRaw(
        source_site=scraper.SITE_NAME,
        title=title[:200],
        price=price,
        avg_rating=avg_rating,
        review_count=review_count,
        product_url=product_url,
        image_url=raw.get("imageUrl") or raw.get("image_url"),
        variant_group_id=variant_group_id,
        scrape_status="ok",
    )


def _next_data_products(nd: dict[str, Any]) -> list[dict[str, Any]]:
    props = nd.get("props", {}).get("pageProps", {})
    initial_state = props.get("initialState") or {}
    products = (
        initial_state.get("searchSlice", {}).get("plpData", {}).get("products")
        or props.get("initialData", {}).get("searchResult", {}).get("products")
        or props.get("products")
        or []
    )
    return products if isinstance(products, list) else []


def products_from_next_data_for_tests(data: dict[str, Any]) -> list[ProductRaw]:
    """Public for tests: parse __NEXT_DATA__ dict into ProductRaw rows."""
    scraper = ChewyScraper()
    seen: set[str] = set()
    rows: list[ProductRaw] = []
    tiles = _chewy_best_tiles_by_parent(_next_data_products(data))
    for item in tiles:
        raw = _chewy_tile_to_raw(item)
        if not raw:
            continue
        row = _product_from_raw(scraper, raw, seen)
        if row:
            rows.append(row)
    return rows


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

    products: list[ProductRaw] = []
    tiles = _chewy_best_tiles_by_parent(_next_data_products(nd))
    for item in tiles:
        if len(products) >= limit:
            break
        raw = _chewy_tile_to_raw(item)
        if not raw:
            continue
        row = _product_from_raw(scraper, raw, seen_urls)
        if row:
            products.append(row)
    return products


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
            url_p = item.get("url") or ""
            if not url_p:
                continue
            row = _product_from_raw(
                scraper,
                {
                    "href": url_p,
                    "title": (item.get("name") or "")[:200],
                    "priceText": str(item.get("offers", {}).get("price", ""))
                    if isinstance(item.get("offers"), dict)
                    else "",
                },
                seen_urls,
            )
            if row:
                products.append(row)
    return products


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


def _parse_chewy_html(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    products = _products_from_next_data_html(html, scraper, seen_urls, limit)
    if products:
        return products

    remaining = limit - len(products)
    if remaining > 0:
        products.extend(
            _products_from_ld_json(html, scraper, seen_urls, remaining)
        )

    remaining = limit - len(products)
    if remaining > 0:
        products.extend(
            _extract_embedded_from_html(html, remaining, seen_urls, scraper)
        )

    return products[:limit]


def _chewy_block_detected(html: str) -> bool:
    low = (html or "").lower()
    return "no treats" in low or (
        len(html or "") < 1000 and "chewy" in low
    )


async def _chewy_scraperapi_get(
    client: httpx.AsyncClient,
    api_key: str,
    target_url: str,
) -> tuple[Optional[str], int]:
    params: dict[str, str] = {
        "api_key": api_key,
        "url": target_url,
        "country_code": _chewy_scraperapi_country(),
        **_chewy_scraperapi_extra_params(),
    }
    resp = await client.get("https://api.scraperapi.com/", params=params)
    body = resp.text if resp.content else ""
    if resp.status_code != 200:
        snippet = body[:260].replace("\n", " ")
        logger.warning(
            "Chewy ScraperAPI HTTP %s for %s ... %s",
            resp.status_code,
            target_url[:100],
            snippet,
        )
        return None, resp.status_code
    return body, resp.status_code


class ChewyScraper(BaseScraper):
    SITE_NAME = "chewy"

    async def _fetch_listings_via_scraperapi(
        self,
        query: str,
        limit: int,
        listing_pages: int = 1,
    ) -> list[ProductRaw]:
        api_key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if not api_key:
            self._empty_scrape_note = "Chewy requires SCRAPERAPI_KEY in backend/.env"
            return []

        listing_base = _listing_url(query)
        seen_urls: set[str] = set()
        products: list[ProductRaw] = []
        tout = _chewy_scraperapi_timeout_sec()
        timeout_cfg = httpx.Timeout(tout, connect=min(30.0, tout))
        last_status = 0
        max_pages = min(max(1, listing_pages), _chewy_scraperapi_max_pages())

        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                for page_ix in range(1, max_pages + 1):
                    page_url = _listing_url_page(listing_base, page_ix)
                    body, last_status = await _chewy_scraperapi_get(
                        client, api_key, page_url
                    )
                    if not body:
                        break
                    if _chewy_block_detected(body):
                        self._empty_scrape_note = (
                            "Chewy ScraperAPI: block page detected (no treats / short body). "
                            "Try CHEWY_SCRAPERAPI_ULTRA_PREMIUM=true or check credits."
                        )
                        logger.warning(self._empty_scrape_note)
                        break

                    batch = _parse_chewy_html(
                        body, self, seen_urls, _FETCH_PAGE_CAP
                    )
                    if not batch:
                        break
                    products.extend(batch)
        except httpx.HTTPError as exc:
            note = (
                "Chewy ScraperAPI HTTP error (%s). Check timeouts and connectivity."
                % type(exc).__name__
            )
            logger.exception(note)
            self._empty_scrape_note = "%s Details: %s" % (note, exc)
            return []

        if not products:
            self._empty_scrape_note = (
                "Chewy ScraperAPI: 0 products parsed "
                f"(last_http={last_status}). "
                "Use CHEWY_SCRAPERAPI_ULTRA_PREMIUM=true and keep CHEWY_SCRAPERAPI_RENDER=false."
            )
            logger.warning(self._empty_scrape_note)
        else:
            logger.info(
                "Chewy ScraperAPI: fetched %s product(s) across %s page(s)",
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
            self._empty_scrape_note = "Chewy requires SCRAPERAPI_KEY in backend/.env"
            return []
        return await self._fetch_listings_via_scraperapi(query, limit, listing_pages)
