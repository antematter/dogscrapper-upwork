import html
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

LISTING_URL = "https://www.target.com/c/dog-beds-pet-supplies/-/N-5xt44"
SEARCH_URL = "https://www.target.com/s?searchTerm={query}"

_PRODUCT_PATH_RE = re.compile(r"/p/[a-z0-9-]+/-/A-\d+", re.IGNORECASE)
_SPONSORED_MARKERS = ("TCID=OGS", "AFID=google", "sponsored=1")
_CARD_WRAPPER_SPLIT = 'data-test="@web/ProductCard/ProductCardVariantWrapper"'
_PRODUCT_CARD_TITLE_RE = re.compile(
    r'<a aria-label="([^"]+)"\s+data-test="@web/ProductCard/title"[^>]*href="(/p/[^"]+/A-\d+)',
    re.I | re.S,
)
_RATING_ARIA_RE = re.compile(
    r'aria-label="([\d.]+) stars with ([\d,]+) ratings"',
    re.I,
)

# Env (backend/.env) — Target via ScraperAPI only:
#   SCRAPERAPI_KEY — required
#   TARGET_SCRAPERAPI_ULTRA_PREMIUM — default true
#   TARGET_SCRAPERAPI_RENDER — default true (Target PLP needs JS render unlike Chewy)
#   TARGET_SCRAPERAPI_PREMIUM — fallback if ultra disabled
#   TARGET_SCRAPERAPI_TIMEOUT — seconds (default 180)
#   TARGET_SCRAPERAPI_COUNTRY — e.g. us (default us)
#   TARGET_SCRAPERAPI_TRY_REDSKY — default true


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _target_scraperapi_country() -> str:
    return (os.environ.get("TARGET_SCRAPERAPI_COUNTRY") or "us").strip() or "us"


def _target_scraperapi_extra_params() -> dict[str, str]:
    params: dict[str, str] = {}
    if _env_truthy("TARGET_SCRAPERAPI_ULTRA_PREMIUM", default=True):
        params["ultra_premium"] = "true"
    elif _env_truthy("TARGET_SCRAPERAPI_PREMIUM"):
        params["premium"] = "true"
    if _env_truthy("TARGET_SCRAPERAPI_RENDER", default=True):
        params["render"] = "true"
    return params


def _target_scraperapi_timeout_sec() -> float:
    try:
        return float(os.environ.get("TARGET_SCRAPERAPI_TIMEOUT") or "180")
    except ValueError:
        return 180.0


def _target_scraperapi_try_redsky() -> bool:
    return _env_truthy("TARGET_SCRAPERAPI_TRY_REDSKY", default=True)


def _listing_url(query: str) -> str:
    """Search URL works reliably with render; category PLP often yields 0 relevant rows."""
    q = (query or "").strip().lower()
    if not q or q in ("dog bed", "dog beds", "dog_beds"):
        return SEARCH_URL.format(query="dog+bed")
    return SEARCH_URL.format(query=quote_plus(q.replace(" ", "+")))


def _is_sponsored_context(snippet: str) -> bool:
    return any(marker in snippet for marker in _SPONSORED_MARKERS)


def _title_from_path(path: str) -> str:
    slug = path.split("/p/", 1)[-1].split("/-/")[0]
    return slug.replace("-", " ").strip().title()


def _product_url_from_path(path: str) -> str:
    path = path.split("?")[0].split("#")[0]
    return path if path.startswith("http") else f"https://www.target.com{path}"


def _normalize_target_image_url(raw: str) -> Optional[str]:
    if not raw or not str(raw).strip():
        return None
    url = str(raw).strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"https://www.target.com{url}"
    if url.startswith("http"):
        return url
    return None


def _normalize_price_value(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    m = re.search(r"\d+\.?\d*", str(raw).replace(",", ""))
    return float(m.group()) if m else None


def _target_parent_key(item: dict[str, Any]) -> str:
    parent = item.get("parent_tcin") or item.get("parent")
    if parent is not None and str(parent).strip():
        return str(parent).strip()
    tcin = item.get("tcin") or item.get("product_id") or item.get("productId")
    return str(tcin).strip() if tcin is not None else ""


def _extract_ratings_from_obj(o: dict[str, Any]) -> tuple[Optional[float], int]:
    avg_rating: Optional[float] = None
    review_count = 0

    rar = o.get("ratings_and_reviews") or o.get("ratingsAndReviews")
    if isinstance(rar, dict):
        stats = rar.get("statistics") or rar.get("rating") or {}
        if isinstance(stats, dict):
            rating_block = stats.get("rating") if isinstance(stats.get("rating"), dict) else stats
            if isinstance(rating_block, dict):
                avg_rating = _normalize_price_value(
                    rating_block.get("average") or rating_block.get("value")
                )
                rc = rating_block.get("count") or rating_block.get("total_count")
                if rc is not None:
                    try:
                        review_count = int(rc)
                    except (TypeError, ValueError):
                        pass

    agg = o.get("aggregateRating")
    if isinstance(agg, dict):
        if avg_rating is None:
            avg_rating = _normalize_price_value(agg.get("ratingValue"))
        if not review_count:
            try:
                review_count = int(agg.get("reviewCount") or 0)
            except (TypeError, ValueError):
                pass

    return avg_rating, review_count


def _walk_redsky_products(
    data: Any,
    out: list[dict[str, Any]],
) -> None:
    """Collect product dicts from nested Target/redsky JSON."""

    def visit(o: Any, depth: int = 0) -> None:
        if depth > 40:
            return
        if isinstance(o, dict):
            tcin = o.get("tcin") or o.get("product_id") or o.get("productId")
            title = None
            item = o.get("item") if isinstance(o.get("item"), dict) else o
            if isinstance(item, dict):
                desc = item.get("product_description") or item.get("productDescription")
                if isinstance(desc, dict):
                    title = desc.get("title") or desc.get("downstream_description")
                if not title:
                    title = item.get("title") or item.get("name")
            if not title:
                title = o.get("title") or o.get("name") or o.get("product_title")

            url = o.get("canonical_url") or o.get("canonicalUrl") or o.get("url")
            if isinstance(url, str) and "/p/" in url and "/A-" in url:
                pass
            elif tcin:
                slug = ""
                if isinstance(o.get("product_description"), dict):
                    slug = str(o["product_description"].get("title") or "")
                if slug:
                    slug_part = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:80]
                    url = f"/p/{slug_part}/-/A-{tcin}" if slug_part else f"/p/-/A-{tcin}"
                else:
                    url = f"/p/-/A-{tcin}"

            price = None
            price_obj = o.get("price") or o.get("current_retail") or o.get("formatted_current_price")
            if isinstance(price_obj, dict):
                price = _normalize_price_value(
                    price_obj.get("current_retail")
                    or price_obj.get("value")
                    or price_obj.get("formatted_current_price")
                )
            else:
                price = _normalize_price_value(price_obj)

            img = ""
            for ik in ("primary_image_url", "image_url", "imageUrl", "image"):
                iv = o.get(ik)
                if isinstance(iv, str) and iv:
                    img = iv
                    break
                if isinstance(iv, dict) and isinstance(iv.get("url"), str):
                    img = iv["url"]
                    break

            avg_rating, review_count = _extract_ratings_from_obj(o)

            if isinstance(title, str) and title.strip() and isinstance(url, str) and "/A-" in url:
                full = _product_url_from_path(url)
                parent_key = _target_parent_key(o)
                out.append(
                    {
                        "title": title.strip()[:200],
                        "href": full,
                        "price": price,
                        "imageUrl": _normalize_target_image_url(img),
                        "avg_rating": avg_rating,
                        "review_count": review_count,
                        "variant_group_id": parent_key or None,
                    }
                )

            for v in o.values():
                visit(v, depth + 1)
        elif isinstance(o, list):
            for x in o:
                visit(x, depth + 1)

    visit(data)


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

    return [best[pk] for pk in order] + no_parent


def _tile_to_raw(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    href = item.get("href") or item.get("product_url") or ""
    if not href or _is_sponsored_context(str(href)):
        return None
    title = (item.get("title") or "").strip()
    if not title or len(title) < 3:
        return None

    review_count = item.get("review_count") or 0
    try:
        review_count = int(review_count)
    except (TypeError, ValueError):
        review_count = 0

    avg_rating = item.get("avg_rating")
    if avg_rating is not None:
        try:
            avg_rating = float(avg_rating)
        except (TypeError, ValueError):
            avg_rating = None

    return {
        "href": _product_url_from_path(str(href)),
        "title": title[:200],
        "price": item.get("price"),
        "avg_rating": avg_rating,
        "review_count": review_count,
        "imageUrl": item.get("imageUrl") or item.get("image_url"),
        "variant_group_id": item.get("variant_group_id"),
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

    title = html.unescape((raw.get("title") or "").strip())
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
    image_url = _normalize_target_image_url(str(image)) if image else None

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


def _rows_from_redsky_walk(
    data: Any,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    tiles: list[dict[str, Any]] = []
    _walk_redsky_products(data, tiles)
    products: list[ProductRaw] = []
    for item in _best_tiles_by_parent(tiles):
        if len(products) >= limit:
            break
        raw = _tile_to_raw(item)
        if not raw:
            continue
        row = _product_from_raw(scraper, raw, seen_urls)
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
    return _rows_from_redsky_walk(nd, scraper, seen_urls, limit)


def _products_from_json_blobs(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    products: list[ProductRaw] = []
    for blob in re.findall(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S
    ):
        if len(blob) < 200 or "tcin" not in blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        batch = _rows_from_redsky_walk(data, scraper, seen_urls, limit - len(products))
        products.extend(batch)
        if len(products) >= limit:
            break
    return products[:limit]


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
            if not isinstance(url_p, str) or "/A-" not in url_p:
                continue
            agg = item.get("aggregateRating") or {}
            offer = item.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            row = _product_from_raw(
                scraper,
                {
                    "href": url_p,
                    "title": (item.get("name") or "")[:200],
                    "price": _normalize_price_value(
                        offer.get("price") if isinstance(offer, dict) else None
                    ),
                    "avg_rating": _normalize_price_value(agg.get("ratingValue")),
                    "review_count": int(agg.get("reviewCount") or 0),
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
    """Parse hydrated ProductCard markup from rendered PLP HTML."""
    products: list[ProductRaw] = []
    if _CARD_WRAPPER_SPLIT not in html:
        return products

    for card in html.split(_CARD_WRAPPER_SPLIT)[1:]:
        if len(products) >= limit:
            break
        title_m = _PRODUCT_CARD_TITLE_RE.search(card)
        if not title_m:
            continue
        title = title_m.group(1).strip()
        href = title_m.group(2).split("?")[0].split("#")[0]
        rating_m = _RATING_ARIA_RE.search(card)
        avg_rating: Optional[float] = None
        review_count = 0
        if rating_m:
            avg_rating = _normalize_price_value(rating_m.group(1))
            try:
                review_count = int(rating_m.group(2).replace(",", ""))
            except (TypeError, ValueError):
                review_count = 0

        prices = re.findall(r"\$[\d.]+", card)
        price = scraper.normalize_price(prices[0]) if prices else None
        img_m = re.search(r'src="(https://target\.scene7\.com[^"]+)"', card)
        image_url = _normalize_target_image_url(img_m.group(1)) if img_m else None

        row = _product_from_raw(
            scraper,
            {
                "href": href,
                "title": title,
                "price": price,
                "avg_rating": avg_rating,
                "review_count": review_count,
                "imageUrl": image_url,
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
        title = _title_from_path(path)
        row = _product_from_raw(
            scraper,
            {
                "href": path,
                "title": title,
                "priceText": "",
                "avg_rating": None,
                "review_count": 0,
            },
            seen_urls,
        )
        if row:
            products.append(row)
    return products


def _parse_target_html(
    html: str,
    scraper: BaseScraper,
    seen_urls: set[str],
    limit: int,
) -> list[ProductRaw]:
    for parser in (
        _products_from_next_data_html,
        _products_from_json_blobs,
        _products_from_ld_json,
        _products_from_product_cards,
        _products_from_path_regex,
    ):
        batch = parser(html, scraper, seen_urls, limit)
        if batch:
            return batch[:limit]
    return []


def _extract_redsky_key(html: str) -> Optional[str]:
    for pat in (
        r'"apiKey"\s*:\s*"([a-f0-9-]{8,})"',
        r"key=([a-f0-9-]{8,})",
        r'"key"\s*:\s*"([a-f0-9-]{8,})"',
    ):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return None


def _build_redsky_search_url(key: str, *, keyword: str = "dog bed", count: int = 24) -> str:
    q = quote_plus(keyword)
    return (
        "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2"
        f"?key={key}&channel=WEB&count={count}&keyword={q}"
        "&offset=0&page=%2Fs&platform=desktop&visitor_id=0"
        "&pricing_store_id=3991&store_ids=3991"
    )


def _target_block_detected(html: str) -> bool:
    low = (html or "").lower()
    if len(html or "") < 8_000:
        return True
    if "access denied" in low or "robot or human" in low:
        return True
    if "captcha" in low and len(html) < 50_000:
        return True
    return False


async def _target_scraperapi_get(
    client: httpx.AsyncClient,
    api_key: str,
    target_url: str,
) -> tuple[Optional[str], int]:
    params: dict[str, str] = {
        "api_key": api_key,
        "url": target_url,
        "country_code": _target_scraperapi_country(),
        **_target_scraperapi_extra_params(),
    }
    resp = await client.get("https://api.scraperapi.com/", params=params)
    body = resp.text if resp.content else ""
    if resp.status_code != 200:
        snippet = body[:260].replace("\n", " ")
        logger.warning(
            "Target ScraperAPI HTTP %s for %s ... %s",
            resp.status_code,
            target_url[:100],
            snippet,
        )
        return None, resp.status_code
    return body, resp.status_code


def products_from_html_for_tests(html: str, limit: int = 20) -> list[ProductRaw]:
    """Public for tests: parse Target PLP HTML into ProductRaw rows."""
    scraper = TargetScraper()
    seen: set[str] = set()
    return _parse_target_html(html, scraper, seen, limit)


def products_from_redsky_for_tests(data: dict[str, Any], limit: int = 20) -> list[ProductRaw]:
    """Public for tests: parse redsky JSON dict into ProductRaw rows."""
    scraper = TargetScraper()
    seen: set[str] = set()
    return _rows_from_redsky_walk(data, scraper, seen, limit)


class TargetScraper(BaseScraper):
    SITE_NAME = "target"

    async def _fetch_via_redsky(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        html: str,
        seen_urls: set[str],
        limit: int,
    ) -> list[ProductRaw]:
        if not _target_scraperapi_try_redsky():
            return []
        key = _extract_redsky_key(html)
        if not key:
            return []
        rs_url = _build_redsky_search_url(key, keyword="dog bed", count=max(limit, 24))
        body, status = await _target_scraperapi_get(client, api_key, rs_url)
        if not body or status != 200:
            return []
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return []
        return _rows_from_redsky_walk(data, self, seen_urls, limit)

    async def _fetch_listings_via_scraperapi(
        self,
        query: str,
        limit: int,
    ) -> list[ProductRaw]:
        api_key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if not api_key:
            self._empty_scrape_note = "Target requires SCRAPERAPI_KEY in backend/.env"
            return []

        listing_url = _listing_url(query)
        seen_urls: set[str] = set()
        products: list[ProductRaw] = []
        last_status = 0
        tout = _target_scraperapi_timeout_sec()
        timeout_cfg = httpx.Timeout(tout, connect=min(30.0, tout))

        try:
            async with httpx.AsyncClient(timeout=timeout_cfg) as client:
                body, last_status = await _target_scraperapi_get(
                    client, api_key, listing_url
                )
                if body and not _target_block_detected(body):
                    products = _parse_target_html(
                        body, self, seen_urls, _FETCH_PAGE_CAP
                    )
                    if not products:
                        products = await self._fetch_via_redsky(
                            client, api_key, body, seen_urls, _FETCH_PAGE_CAP
                        )
                elif body:
                    self._empty_scrape_note = (
                        "Target ScraperAPI: block or empty shell detected. "
                        "Use TARGET_SCRAPERAPI_RENDER=true and TARGET_SCRAPERAPI_ULTRA_PREMIUM=true."
                    )
                    logger.warning(self._empty_scrape_note)
        except httpx.HTTPError as exc:
            note = (
                "Target ScraperAPI HTTP error (%s). Check timeouts and connectivity."
                % type(exc).__name__
            )
            logger.exception(note)
            self._empty_scrape_note = "%s Details: %s" % (note, exc)
            return []

        if not products:
            self._empty_scrape_note = (
                "Target ScraperAPI: 0 products parsed "
                f"(last_http={last_status}). "
                "Use TARGET_SCRAPERAPI_ULTRA_PREMIUM=true and TARGET_SCRAPERAPI_RENDER=true."
            )
            logger.warning(self._empty_scrape_note)
        else:
            logger.info(
                "Target ScraperAPI: fetched %s product(s) from %s",
                len(products),
                listing_url[:80],
            )

        return products[:limit]

    async def fetch_listings(
        self,
        query: str = "dog bed",
        limit: int = 20,
        *,
        listing_pages: int = 1,
    ) -> list[ProductRaw]:
        api_key = (os.environ.get("SCRAPERAPI_KEY") or "").strip()
        if not api_key:
            self._empty_scrape_note = "Target requires SCRAPERAPI_KEY in backend/.env"
            return []
        return await self._fetch_listings_via_scraperapi(query, limit)
