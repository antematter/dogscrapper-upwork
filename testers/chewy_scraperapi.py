# TopTails — Chewy dog beds via ScraperAPI (Google Colab / local)
#
# Bypasses Akamai/429 on Colab by fetching through ScraperAPI's proxy network.
# Get a key: https://www.scraperapi.com/
#
# ⚠️  Never commit your API key. Use CELL 2 paste or env SCRAPERAPI_KEY only.

# =============================================================================
# CELL 1 — Install dependencies
# =============================================================================
# !pip install -q requests pydantic pandas

# =============================================================================
# CELL 2 — API key & settings
# =============================================================================

import os

# Paste key here for Colab OR: os.environ["SCRAPERAPI_KEY"] = "your-key"
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "").strip()

LISTING_URL = "https://www.chewy.com/b/dog-beds-365"
LIMIT = 20
OUTPUT_JSON = "chewy_scraperapi_results.json"

# ScraperAPI options (more credits if premium/render enabled)
RENDER_JS = True          # Chewy is CSR — usually required
COUNTRY_CODE = "us"
# Chewy is a protected domain — ScraperAPI requires paid Premium or Ultra Premium tier
USE_PREMIUM = True          # Needs plan upgrade if you get 403 on premium
USE_ULTRA_PREMIUM = False   # Try True if you have Ultra on your ScraperAPI plan
REQUEST_TIMEOUT = 180       # render + premium can take 60–120s

# =============================================================================
# CELL 3 — Scraper + parser
# =============================================================================

import json
import re
from typing import Any, Optional
from urllib.parse import quote

import requests
from pydantic import BaseModel


class Product(BaseModel):
    source_site: str = "chewy"
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0


def normalize_price(raw) -> Optional[float]:
    if raw is None:
        return None
    m = re.search(r"\d+\.?\d*", str(raw))
    return float(m.group()) if m else None


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
    """
    Fetch URL through ScraperAPI.
    Returns (html_text, debug_info).
    """
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

    api_endpoint = "http://api.scraperapi.com"
    print(f"[scraperapi] GET {target_url}")
    print(
        f"[scraperapi] render={render} country={country_code} "
        f"premium={premium} ultra_premium={ultra_premium}"
    )

    try:
        r = requests.get(api_endpoint, params=params, timeout=timeout)
    except requests.RequestException as e:
        return None, {"error": str(e)}

    debug = {
        "status_code": r.status_code,
        "final_url": r.url[:200] if r.url else "",
        "body_len": len(r.text or ""),
        "scraperapi_headers": {
            k: v
            for k, v in r.headers.items()
            if k.lower().startswith("sa-") or "scraper" in k.lower()
        },
    }

    print(f"[scraperapi] HTTP {r.status_code} body_len={debug['body_len']}")

    if r.status_code != 200:
        snippet = (r.text or "")[:500]
        debug["error"] = snippet
        print(f"[scraperapi] Error body: {snippet[:300]}")
        if r.status_code in (403, 500) and "premium" in snippet.lower():
            debug["needs_plan_upgrade"] = True
            print(
                "[scraperapi] Chewy requires a ScraperAPI plan with Premium/Ultra Premium "
                "(free/hobby plans cannot scrape protected domains)."
            )
        return None, debug

    text = r.text or ""
    low = text.lower()
    if "no treats" in low or len(text) < 1000:
        debug["likely_blocked"] = True
        print("[scraperapi] Warning: response looks like a Chewy block page")

    return text, debug


def parse_chewy_html(html: str, limit: int) -> list[Product]:
    products: list[Product] = []
    seen: set[str] = set()

    # __NEXT_DATA__
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            nd = json.loads(m.group(1))
            pages = (
                nd.get("props", {})
                .get("pageProps", {})
                .get("initialData", {})
                .get("searchResult", {})
                .get("products")
                or nd.get("props", {}).get("pageProps", {}).get("products")
                or []
            )
            for item in pages:
                if len(products) >= limit:
                    break
                part = item.get("part") or item.get("id") or ""
                slug = item.get("canonicalUrl") or item.get("url") or f"/dp/{part}"
                url_p = slug if str(slug).startswith("http") else f"https://www.chewy.com{slug}"
                if url_p in seen:
                    continue
                seen.add(url_p)
                products.append(
                    Product(
                        title=(item.get("name") or item.get("title") or "")[:200],
                        price=normalize_price(
                            item.get("price") or item.get("salePrice")
                        ),
                        avg_rating=(
                            float(item["rating"])
                            if item.get("rating") is not None
                            else None
                        ),
                        review_count=int(item.get("reviewCount") or 0),
                        product_url=url_p,
                        image_url=item.get("imageUrl") or item.get("image") or "",
                    )
                )
            if products:
                print(f"[parse] {len(products)} from __NEXT_DATA__")
                return products
        except Exception as ex:
            print(f"[parse] __NEXT_DATA__ error: {ex}")

    # application/ld+json
    for blob in re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            obj = json.loads(blob)
            items = []
            if obj.get("@type") == "ItemList":
                items = [e.get("item") or e for e in obj.get("itemListElement", [])]
            elif obj.get("@type") == "Product":
                items = [obj]
            for item in items:
                if len(products) >= limit:
                    break
                url_p = item.get("url") or ""
                if not url_p or url_p in seen:
                    continue
                seen.add(url_p)
                products.append(
                    Product(
                        title=(item.get("name") or "")[:200],
                        product_url=url_p,
                    )
                )
        except Exception:
            continue
    if products:
        print(f"[parse] {len(products)} from JSON-LD")
        return products

    # Regex /dp/ paths
    paths = list(
        dict.fromkeys(
            re.findall(
                r"(?:https://www\.chewy\.com)?(/[a-z0-9][a-z0-9-]*/dp/\d+)",
                html,
                re.I,
            )
        )
    )
    print(f"[parse] {len(paths)} /dp/ paths via regex")
    for path in paths[:limit]:
        url_p = f"https://www.chewy.com{path}"
        if url_p in seen:
            continue
        seen.add(url_p)
        slug = path.strip("/").split("/dp/")[0].split("/")[-1]
        products.append(
            Product(title=slug.replace("-", " ").title(), product_url=url_p)
        )

    return products


def scrape_chewy_scraperapi(
    api_key: str,
    limit: int = 20,
) -> tuple[list[Product], dict]:
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
    products = parse_chewy_html(html, limit)
    debug["product_count"] = len(products)
    return products, debug


print("Chewy ScraperAPI helpers loaded.")

# =============================================================================
# CELL 4 — Run
# =============================================================================

if not SCRAPERAPI_KEY:
    print(
        "[error] Set SCRAPERAPI_KEY in CELL 2 or:\n"
        "  import os; os.environ['SCRAPERAPI_KEY'] = 'your-key'"
    )
else:
    products, debug = scrape_chewy_scraperapi(SCRAPERAPI_KEY, limit=LIMIT)

    print("debug:", {k: v for k, v in debug.items() if k != "error"})
    if debug.get("error"):
        print("error:", str(debug["error"])[:400])

    if not products:
        if debug.get("needs_plan_upgrade"):
            print(
                "\n[PLAN LIMIT] Your ScraperAPI account cannot use premium proxies.\n"
                "Chewy is a protected domain — upgrade at https://www.scraperapi.com/pricing\n"
                "or use a different data source (Colab free proxies won't work either)."
            )
        else:
            print(
                "\n[BLOCKED] No products parsed.\n"
                "• Chewy needs premium=true or ultra_premium=true on ScraperAPI.\n"
                "• Check dashboard for credits: https://www.scraperapi.com/dashboard\n"
                "• Keep RENDER_JS=True for Chewy CSR."
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
