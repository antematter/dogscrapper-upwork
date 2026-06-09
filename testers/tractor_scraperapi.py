# TopTails — Tractor Supply dog beds via ScraperAPI (Google Colab / local)
#
# TSC's React catalog page (/tsc/catalog/...) returns a shell with skeleton cards unless
# JS renders the grid. On standard ScraperAPI (no render/premium), use the legacy
# SearchDisplay endpoint instead — it returns full server-rendered product HTML.
#
# Get a key: https://www.scraperapi.com/
#
# ⚠️  Never commit your API key. Use CELL 2 or env SCRAPERAPI_KEY only.

# =============================================================================
# CELL 1 — Install dependencies
# =============================================================================
# !pip install -q requests pydantic pandas

# =============================================================================
# CELL 2 — API key & settings
# =============================================================================

import os

SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "").strip()

LISTING_URL = "https://www.tractorsupply.com/tsc/catalog/dog-beds"
SEARCH_TERM = "dog bed"  # used when USE_SEARCH_DISPLAY=True
LIMIT = 20
OUTPUT_JSON = "tractor_scraperapi_results.json"

# Legacy SearchDisplay HTML has products on standard ScraperAPI (no render/premium).
USE_SEARCH_DISPLAY = True
SEARCH_DISPLAY_PAGE_SIZE = 48

RENDER_JS = False
COUNTRY_CODE = "us"
USE_PREMIUM = False
USE_ULTRA_PREMIUM = False
REQUEST_TIMEOUT = 180

# =============================================================================
# CELL 3 — Scraper + parser
# =============================================================================

import json
import re
from typing import Any, Optional
from urllib.parse import quote_plus

import requests
from pydantic import BaseModel


class Product(BaseModel):
    source_site: str = "tractor_supply"
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0


def normalize_price(raw) -> Optional[float]:
    if raw is None:
        return None
    m = re.search(r"\d+\.?\d*", str(raw).replace(",", ""))
    return float(m.group()) if m else None


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


def rating_review_for_entry(html: str, entry_id: str) -> tuple[Optional[float], int]:
    """Extract PLP star rating and review count from SearchDisplay entry block."""
    anchor = f"catalogEntry_img{entry_id}"
    idx = html.find(anchor)
    if idx < 0:
        return None, 0
    chunk = html[idx : idx + 14_000]
    rating: Optional[float] = None
    m = _RATING_TITLE_RE.search(chunk)
    if m:
        rating = normalize_rating(m.group(1))
    if rating is None:
        m = re.search(
            r'<div class="rating">.*?<span>([\d.]+)</span>',
            chunk,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            rating = normalize_rating(m.group(1))
    review_count = 0
    m = _RATING_REVIEW_SPAN_RE.search(chunk)
    if m:
        review_count = int(m.group(1).replace(",", ""))
    return rating, review_count


def catalog_slug_to_search_term(slug: str) -> str:
    """dog-beds -> dog bed for SearchDisplay."""
    return slug.strip("/").split("/")[-1].replace("-", " ").strip() or "dog bed"


def build_search_display_url(
    search_term: str,
    *,
    begin_index: int = 0,
    page_size: int = 48,
    category_id: Optional[str] = None,
) -> str:
    q = quote_plus(search_term)
    url = (
        "https://www.tractorsupply.com/SearchDisplay"
        f"?searchTerm={q}&beginIndex={begin_index}&pageSize={page_size}"
    )
    if category_id:
        url += f"&filterTerm={category_id}"
    return url


def extract_category_id_from_html(html: str) -> Optional[str]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        nd = json.loads(m.group(1))
        pp = nd.get("props", {}).get("pageProps", {})
        inner = pp.get("pageProps") or pp
        cd = (inner.get("content") or {}).get("categoryDetails") or {}
        entry = cd.get("selectedEntry") or {}
        return entry.get("identifier_ntk") or entry.get("identifier")
    except Exception:
        return None


def normalize_rating(raw) -> Optional[float]:
    if not raw:
        return None
    for m in re.finditer(r"\d+\.?\d*", str(raw)):
        v = float(m.group())
        if 0.0 <= v <= 5.0:
            return v
    return None


def fetch_via_scraperapi(
    target_url: str,
    api_key: str,
    *,
    render: bool = True,
    country_code: str = "us",
    premium: bool = False,
    ultra_premium: bool = False,
    timeout: int = 120,
) -> tuple[Optional[str], dict]:
    if not api_key:
        return None, {"error": "SCRAPERAPI_KEY is empty"}

    params: dict[str, Any] = {
        "api_key": api_key,
        "url": target_url,
        "country_code": country_code,
    }
    if render:
        params["render"] = "true"
    if ultra_premium:
        params["ultra_premium"] = "true"
    elif premium:
        params["premium"] = "true"

    print(f"[scraperapi] GET {target_url}")
    print(
        f"[scraperapi] render={render} country={country_code} "
        f"premium={premium} ultra_premium={ultra_premium}"
    )

    try:
        r = requests.get("http://api.scraperapi.com", params=params, timeout=timeout)
    except requests.RequestException as e:
        return None, {"error": str(e)}

    debug = {
        "status_code": r.status_code,
        "body_len": len(r.text or ""),
    }

    print(f"[scraperapi] HTTP {r.status_code} body_len={debug['body_len']}")

    if r.status_code != 200:
        snippet = (r.text or "")[:500]
        debug["error"] = snippet
        print(f"[scraperapi] Error: {snippet[:300]}")
        if r.status_code in (403, 500) and "premium" in snippet.lower():
            debug["needs_plan_upgrade"] = True
        return None, debug

    text = r.text or ""
    low = text.lower()
    if "genericerror" in low and len(text) < 8000:
        debug["likely_shell"] = True
        print("[scraperapi] Warning: looks like TSC error/shell page")
    elif not re.search(r"/tsc/product/", text, re.I) and len(text) < 15_000:
        debug["likely_shell"] = True
        print("[scraperapi] Warning: no /tsc/product/ links in HTML")

    return text, debug


def parse_search_display_html(html: str, limit: int) -> list[Product]:
    """Parse IBM Commerce SearchDisplay PLP (works without JS render)."""
    products: list[Product] = []
    seen: set[str] = set()

    for entry_id, path, title in _CATALOG_ENTRY_RE.findall(html):
        if len(products) >= limit:
            break
        url_p = f"https://www.tractorsupply.com{path.split('?')[0]}"
        if url_p in seen:
            continue
        seen.add(url_p)
        avg_rating, review_count = rating_review_for_entry(html, entry_id)
        products.append(
            Product(
                title=title.strip()[:200],
                product_url=url_p,
                avg_rating=avg_rating,
                review_count=review_count,
            )
        )

    if products:
        print(f"[parse] {len(products)} from SearchDisplay catalogEntry")
        return products

    for path in dict.fromkeys(_PRODUCT_PATH_RE.findall(html)):
        if len(products) >= limit:
            break
        url_p = f"https://www.tractorsupply.com{path}"
        if url_p in seen:
            continue
        seen.add(url_p)
        slug = path.split("/tsc/product/")[-1]
        products.append(
            Product(
                title=slug.replace("-", " ").title(),
                product_url=url_p,
            )
        )
    if products:
        print(f"[parse] {len(products)} from SearchDisplay /tsc/product/ paths")
    return products


def parse_tractor_html(html: str, limit: int) -> list[Product]:
    if 'id="catalogEntry_img' in html:
        return parse_search_display_html(html, limit)

    products: list[Product] = []
    seen: set[str] = set()

    for blob in re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            raw = json.loads(blob)
            candidates = raw if isinstance(raw, list) else [raw]
            for obj in candidates:
                if obj.get("@type") == "ItemList":
                    entries = obj.get("itemListElement") or []
                elif obj.get("@type") == "Product":
                    entries = [{"item": obj}]
                else:
                    continue
                for entry in entries:
                    if len(products) >= limit:
                        break
                    item = entry.get("item") or entry
                    url_p = item.get("url") or item.get("@id") or ""
                    if not url_p or "/tsc/product/" not in url_p:
                        continue
                    if not url_p.startswith("http"):
                        url_p = f"https://www.tractorsupply.com{url_p}"
                    if url_p in seen:
                        continue
                    seen.add(url_p)
                    offer = item.get("offers") or {}
                    if isinstance(offer, list):
                        offer = offer[0] if offer else {}
                    agg = item.get("aggregateRating") or {}
                    products.append(
                        Product(
                            title=(item.get("name") or "")[:200],
                            price=normalize_price(offer.get("price")),
                            avg_rating=normalize_rating(agg.get("ratingValue")),
                            review_count=int(agg.get("reviewCount") or 0),
                            product_url=url_p,
                            image_url=(
                                item.get("image")
                                if isinstance(item.get("image"), str)
                                else ""
                            ),
                        )
                    )
        except Exception:
            continue
    if products:
        print(f"[parse] {len(products)} from JSON-LD")
        return products

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            nd = json.loads(m.group(1))
            items = (
                nd.get("props", {}).get("pageProps", {}).get("products")
                or nd.get("props", {})
                .get("pageProps", {})
                .get("initialData", {})
                .get("products")
                or []
            )
            for item in items:
                if len(products) >= limit:
                    break
                slug = item.get("url") or item.get("canonicalUrl") or ""
                url_p = (
                    slug
                    if str(slug).startswith("http")
                    else f"https://www.tractorsupply.com{slug}"
                )
                if url_p in seen:
                    continue
                seen.add(url_p)
                products.append(
                    Product(
                        title=(item.get("name") or "")[:200],
                        price=normalize_price(item.get("price") or item.get("salePrice")),
                        product_url=url_p,
                        image_url=item.get("imageUrl") or "",
                    )
                )
            if products:
                print(f"[parse] {len(products)} from __NEXT_DATA__")
                return products
        except Exception as ex:
            print(f"[parse] __NEXT_DATA__ error: {ex}")

    paths = list(dict.fromkeys(_PRODUCT_PATH_RE.findall(html)))
    print(f"[parse] {len(paths)} /tsc/product/ paths via regex")
    for path in paths[:limit]:
        url_p = f"https://www.tractorsupply.com{path}"
        if url_p in seen:
            continue
        seen.add(url_p)
        slug = path.split("/tsc/product/")[-1]
        products.append(
            Product(
                title=slug.replace("-", " ").title(),
                product_url=url_p,
            )
        )

    return products


def scrape_tractor_scraperapi(api_key: str, limit: int = 20) -> tuple[list[Product], dict]:
    debug: dict[str, Any] = {}

    if USE_SEARCH_DISPLAY:
        slug = LISTING_URL.rstrip("/").split("/")[-1]
        search_term = SEARCH_TERM or catalog_slug_to_search_term(slug)
        category_id: Optional[str] = None

        shell_html, shell_debug = fetch_via_scraperapi(
            LISTING_URL,
            api_key,
            render=False,
            country_code=COUNTRY_CODE,
            premium=USE_PREMIUM,
            ultra_premium=USE_ULTRA_PREMIUM,
            timeout=REQUEST_TIMEOUT,
        )
        debug["catalog_fetch"] = shell_debug
        if shell_html:
            category_id = extract_category_id_from_html(shell_html)
            if category_id:
                print(f"[tractor] category id from __NEXT_DATA__: {category_id}")

        sd_url = build_search_display_url(
            search_term,
            begin_index=0,
            page_size=max(limit, SEARCH_DISPLAY_PAGE_SIZE),
            category_id=category_id,
        )
        print(f"[tractor] SearchDisplay: {sd_url}")
        html, sd_debug = fetch_via_scraperapi(
            sd_url,
            api_key,
            render=False,
            country_code=COUNTRY_CODE,
            premium=USE_PREMIUM,
            ultra_premium=USE_ULTRA_PREMIUM,
            timeout=REQUEST_TIMEOUT,
        )
        debug["search_display_fetch"] = sd_debug
        if not html:
            return [], debug
        products = parse_search_display_html(html, limit)
        debug["fetch_mode"] = "search_display"
        debug["search_term"] = search_term
        debug["category_id"] = category_id
    else:
        html, debug = fetch_via_scraperapi(
            LISTING_URL,
            api_key,
            render=RENDER_JS,
            country_code=COUNTRY_CODE,
            premium=USE_PREMIUM,
            ultra_premium=USE_ULTRA_PREMIUM,
            timeout=REQUEST_TIMEOUT,
        )
        if not html:
            return [], debug
        products = parse_tractor_html(html, limit)
        debug["fetch_mode"] = "catalog"

    debug["product_count"] = len(products)
    return products, debug


print("Tractor Supply ScraperAPI helpers loaded.")

# =============================================================================
# CELL 4 — Run
# =============================================================================

if not SCRAPERAPI_KEY:
    print(
        "[error] Set SCRAPERAPI_KEY in CELL 2:\n"
        "  import os; os.environ['SCRAPERAPI_KEY'] = 'your-key'"
    )
else:
    products, debug = scrape_tractor_scraperapi(SCRAPERAPI_KEY, limit=LIMIT)

    print("debug:", {k: v for k, v in debug.items() if k != "error"})
    if debug.get("error"):
        print("error:", str(debug["error"])[:400])

    if not products:
        if debug.get("needs_plan_upgrade"):
            print(
                "\n[PLAN LIMIT] Enable premium on your ScraperAPI plan, or set "
                "USE_PREMIUM=True / USE_ULTRA_PREMIUM=True in CELL 2."
            )
        elif debug.get("likely_shell"):
            print(
                "\n[SHELL PAGE] React catalog returned no product grid.\n"
                "• Set USE_SEARCH_DISPLAY=True (default) for legacy SearchDisplay HTML.\n"
                "• Or try RENDER_JS=True + premium if you must scrape /tsc/catalog/ directly."
            )
        else:
            print(
                "\n[NO PRODUCTS] Parser found nothing.\n"
                "• USE_SEARCH_DISPLAY=True uses SearchDisplay?searchTerm=… (standard plan).\n"
                "• Catalog-only needs RENDER_JS=True (often needs premium on TSC).\n"
                "• Check credits: https://www.scraperapi.com/dashboard"
            )
    else:
        print(f"\n[ok] {len(products)} products\n")
        for i, p in enumerate(products[:10], 1):
            print(f"{i}. {p.title[:70]}")
            print(f"   ${p.price}  ⭐{p.avg_rating}  |  {p.product_url[:80]}")

        payload = [p.model_dump() for p in products]
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\nSaved → {OUTPUT_JSON}")

        try:
            import pandas as pd

            display(pd.DataFrame(payload))  # noqa: F821
        except ImportError:
            pass

        try:
            from google.colab import files  # type: ignore

            files.download(OUTPUT_JSON)
        except ImportError:
            pass
