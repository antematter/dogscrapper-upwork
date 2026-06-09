# TopTails — Petco category pages via ScraperAPI
#
# Petco serves DataDome in-page, but standard ScraperAPI (US residential) often returns
# full HTML (~1.8MB) with embedded __NEXT_DATA__ product tiles (`itemname`, `url`,
# `image_url`). Plain hrefs may be absent — parse JSON, not `<a>` tags alone.
#
# Install: pip install httpx pydantic
# Credentials: SCRAPERAPI_KEY in env or CELL 2 (never commit keys).

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import httpx
from pydantic import BaseModel

_ENV_FILE = Path(__file__).resolve().parent.parent / "toptails" / "backend" / ".env"
if _ENV_FILE.is_file():
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE)
    except ImportError:
        pass

SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "").strip()

# Works with canonical short URL or legacy /shop/en/petcostore/... — same SSR payload size.
DEFAULT_LISTING_URL = "https://www.petco.com/category/dog/dog-beds-and-bedding"

COUNTRY_CODE = "us"
REQUEST_TIMEOUT = 120
OUTPUT_JSON = "petco_scraperapi_results.json"
LIMIT = 24
MAX_PAGES = 8


class Product(BaseModel):
    source_site: str = "petco"
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0


def _env_premium_params() -> dict[str, str]:
    p: dict[str, str] = {}
    if os.environ.get("PETCO_SCRAPERAPI_ULTRA_PREMIUM", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        p["ultra_premium"] = "true"
    elif os.environ.get("PETCO_SCRAPERAPI_PREMIUM", "").lower() in ("1", "true", "yes"):
        p["premium"] = "true"
    return p


async def fetch_via_scraperapi(
    target_url: str,
    api_key: str,
    *,
    timeout: float = REQUEST_TIMEOUT,
) -> tuple[Optional[str], dict[str, Any]]:
    if not api_key:
        return None, {"error": "SCRAPERAPI_KEY is empty"}

    params: dict[str, str] = {
        "api_key": api_key,
        "url": target_url,
        "country_code": COUNTRY_CODE,
        **_env_premium_params(),
    }

    dbg: dict[str, Any] = {"url": target_url}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(25.0, timeout))) as client:
        r = await client.get("https://api.scraperapi.com/", params=params)

    dbg["status_code"] = r.status_code
    dbg["body_len"] = len(r.content or b"")

    if r.status_code != 200:
        dbg["error"] = r.text[:500]
        return None, dbg

    txt = r.text or ""
    low = txt.lower()
    if "captcha-delivery" in low or txt.startswith("<html><head><meta name=\"robots\""):
        dbg["likely_block"] = True
    # DataDome script alone is OK if __NEXT_DATA__ is huge — check size
    if len(txt) < 50_000:
        dbg["likely_shell_or_block"] = True

    return txt, dbg


_TILE_SKU_TAIL = re.compile(r"-\d{6,9}$")


def _is_petco_product_path(val: str) -> bool:
    return "/product/" in val or "/shop/en/petcostore/product/" in val


def _best_tile_url(tile: dict[str, Any]) -> Optional[str]:
    candidates: list[str] = []
    for k in ("url", "itemurl", "itemUrl"):
        val = tile.get(k)
        if isinstance(val, str) and _is_petco_product_path(val):
            candidates.append(val.split("?")[0])
    if not candidates:
        return None

    with_sku = [
        c
        for c in candidates
        if _TILE_SKU_TAIL.search(c.rstrip("/").rsplit("/", 1)[-1])
    ]
    pool = with_sku or candidates
    return sorted(pool, key=len, reverse=True)[0]


def _normalized_url_from_candidates(raw: str) -> Optional[str]:
    raw = raw.split("?")[0].strip()
    if raw.startswith("http"):
        return raw.rstrip("/")
    if raw.startswith("/product/"):
        return f"https://www.petco.com{raw}".rstrip("/")
    idx = raw.find("/shop/en/petcostore/product/")
    if idx < 0:
        return None
    tail = raw[idx:]
    if not tail.startswith("/"):
        tail = "/" + tail
    return ("https://www.petco.com" + tail).rstrip("/")


def _tile_price(tile: dict[str, Any]) -> Optional[float]:
    for pk in ("rdprice", "offerprice", "offer_price", "price", "listprice"):
        val = tile.get(pk)
        if val is not None and str(val).strip():
            m = re.search(r"\d+\.?\d*", str(val))
            if m:
                return float(m.group())
    return None


def _tile_rating(tile: dict[str, Any]) -> tuple[Optional[float], int]:
    rating: Optional[float] = None
    for rk in ("AverageRating", "averagerating", "avg_rating", "rating"):
        val = tile.get(rk)
        if val is not None and str(val).strip():
            try:
                rating = float(val)
            except ValueError:
                pass
            break
    reviews = 0
    for ck in ("TotalReviewCount", "reviewcount", "review_count", "num_reviews"):
        val = tile.get(ck)
        if val is not None:
            try:
                reviews = int(val)
            except (ValueError, TypeError):
                pass
            break
    return rating, reviews


def _iterate_next_data_products(
    data: Any,
) -> list[tuple[str, str, Optional[str], Optional[float], Optional[float], int]]:
    """Yield (title, url, image, price, rating, reviews)."""

    out: list[tuple[str, str, Optional[str], Optional[float], Optional[float], int]] = []

    def visit(o: Any, depth: int = 0) -> None:
        if depth > 35:
            return
        if isinstance(o, dict):
            nm = o.get("itemname") or o.get("itemName")
            if isinstance(nm, str) and nm.strip():
                pu = _best_tile_url(o)
                if pu:
                    img = None
                    for ik in ("image_url", "itemimg", "image"):
                        iv = o.get(ik)
                        if isinstance(iv, str) and iv.startswith("http"):
                            img = iv
                            break
                    price = _tile_price(o)
                    rating, reviews = _tile_rating(o)
                    out.append((nm.strip(), pu, img, price, rating, reviews))
            for v in o.values():
                visit(v, depth + 1)
        elif isinstance(o, list):
            for item in o:
                visit(item, depth + 1)

    visit(data)

    normalized: list[tuple[str, str, Optional[str], Optional[float], Optional[float], int]] = []
    for title, pu, ig, price, rating, reviews in out:
        u = _normalized_url_from_candidates(pu)
        if u:
            normalized.append((title, u, ig, price, rating, reviews))
    return normalized


def parse_petco_listing_html(
    html: str, *, max_tiles_per_page: int = 240
) -> list[Product]:
    m = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.S,
    )
    if not m:
        return []

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    seen: set[str] = set()
    rows: list[Product] = []
    for title, url, img, price, rating, reviews in _iterate_next_data_products(data):
        if len(rows) >= max_tiles_per_page:
            break
        if url in seen:
            continue
        seen.add(url)
        rows.append(
            Product(
                title=title[:200],
                product_url=url,
                image_url=img or "",
                price=price,
                avg_rating=rating,
                review_count=reviews,
            )
        )
    return rows


def _listing_url_page(listing_url: str, page: int) -> str:
    listing_url = listing_url.strip()
    if page <= 1:
        return listing_url
    join = "&" if "?" in listing_url else "?"
    return f"{listing_url}{join}page={page}"


async def scrape_petco_category(
    api_key: str,
    *,
    listing_url: str = DEFAULT_LISTING_URL,
    limit: int = 24,
) -> tuple[list[Product], dict[str, Any]]:
    combined: list[Product] = []
    seen_urls: set[str] = set()
    debug_pages: list[dict[str, Any]] = []

    for page in range(1, MAX_PAGES + 1):
        page_url = _listing_url_page(listing_url, page)
        html, dbg = await fetch_via_scraperapi(page_url, api_key)
        debug_pages.append(dbg)

        if not html:
            break

        batch = parse_petco_listing_html(html)
        new_count = 0
        for p in batch:
            if p.product_url in seen_urls:
                continue
            seen_urls.add(p.product_url)
            combined.append(p)
            new_count += 1

        if len(combined) >= limit or new_count == 0:
            break

    merged = {"pages_fetched": len(debug_pages)}
    merged["last_status"] = debug_pages[-1].get("status_code") if debug_pages else None
    return combined[:limit], merged


async def main() -> None:
    key = SCRAPERAPI_KEY
    if not key:
        print(
            "[error] Set SCRAPERAPI_KEY env or assign SCRAPERAPI_KEY = '...' at top.",
        )
        return

    items, dbg = await scrape_petco_category(key, listing_url=DEFAULT_LISTING_URL, limit=LIMIT)
    print("debug:", dbg)
    print(f"[ok] {len(items)} products")
    for i, p in enumerate(items[:10], 1):
        print(f"{i}. {p.title[:70]}")
        print(f"   {p.product_url[:90]}")

    out = [p.model_dump() for p in items]
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"Saved → {OUTPUT_JSON}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
