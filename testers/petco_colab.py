# TopTails — Petco dog beds (Google Colab / Jupyter)
#
# Copy each "# CELL N" block into its own notebook cell.
# Run cells in order. US Colab runtime recommended.
#
# Get credentials (US browser):
#   1. Open https://www.petco.com/shop/en/petcostore/category/dog/dog-beds-and-bedding
#   2. F12 → Network → filter "cnstrc" → open browse request
#   3. Copy Cookie header → PETCO_COOKIES below
#   4. Copy query param key= (NOT ConstructorioID_client_id) → PETCO_CONSTRUCTOR_KEY

# =============================================================================
# CELL 1 — Install dependencies
# =============================================================================
# !pip install -q requests pydantic pandas

# =============================================================================
# CELL 2 — Paste credentials & settings
# =============================================================================

PETCO_COOKIES = ""  # paste full cookie string here
PETCO_CONSTRUCTOR_KEY = ""  # paste key= from ac.cnstrc.com request
LIMIT = 24
OUTPUT_JSON = "petco_colab_results.json"

CATEGORY_URL = (
    "https://www.petco.com/shop/en/petcostore/category/dog/dog-beds-and-bedding"
)
GROUP_ID = "dog-beds-and-bedding"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# =============================================================================
# CELL 3 — Scraper logic (run this cell once)
# =============================================================================

import json
import re
import time
from typing import Any, Optional

import requests
from pydantic import BaseModel


class Product(BaseModel):
    source_site: str = "petco"
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0
    brand: str = ""


def parse_cookies(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.strip().split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


def normalize_price(raw: str) -> Optional[float]:
    m = re.search(r"\d+\.?\d*", raw or "")
    return float(m.group()) if m else None


def parse_constructor(data: dict, limit: int) -> list[Product]:
    products: list[Product] = []
    seen: set[str] = set()
    for item in data.get("response", {}).get("results", []):
        if len(products) >= limit:
            break
        d = item.get("data", {})
        title = (item.get("value") or d.get("name") or "").strip()
        if not title:
            continue
        url = d.get("url", "")
        product_url = url if url.startswith("http") else f"https://www.petco.com{url}"
        if product_url in seen:
            continue
        seen.add(product_url)
        price = None
        for pk in ("sale_price", "price", "list_price"):
            if d.get(pk):
                price = normalize_price(str(d[pk]))
                if price is not None:
                    break
        rating = None
        for rk in ("avg_rating", "average_rating", "rating"):
            if d.get(rk) is not None:
                try:
                    rating = float(d[rk])
                    break
                except (TypeError, ValueError):
                    pass
        reviews = 0
        for ck in ("review_count", "num_reviews", "total_reviews"):
            if d.get(ck) is not None:
                try:
                    reviews = int(d[ck])
                    break
                except (TypeError, ValueError):
                    pass
        products.append(
            Product(
                title=title[:200],
                price=price,
                product_url=product_url,
                image_url=d.get("image_url") or "",
                avg_rating=rating,
                review_count=reviews,
                brand=d.get("brand") or "",
            )
        )
    return products


def fetch_petco(cookies_raw: str, api_key: str, limit: int = 24) -> tuple[list[Product], str]:
    if not cookies_raw.strip():
        return [], "PETCO_COOKIES is empty — paste cookies in CELL 2"
    if not api_key.strip():
        return [], (
            "PETCO_CONSTRUCTOR_KEY is empty — copy key= from Network tab "
            "(not ConstructorioID_client_id)"
        )

    cookies = parse_cookies(cookies_raw)
    country = cookies.get("Edgescape-Country", "?")
    if country and country.upper() != "US":
        print(f"[warn] Edgescape-Country={country!r} — use cookies from a US browser session")

    client_id = cookies.get("ConstructorioID_client_id", "")
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": CATEGORY_URL,
            "Origin": "https://www.petco.com",
        }
    )

    ts = int(time.time() * 1000)
    url = (
        f"https://ac.cnstrc.com/browse/group_id/{GROUP_ID}"
        f"?key={api_key.strip()}&c={client_id}"
        f"&num_results_per_page={limit}&page=1&sort_by=relevance&_dt={ts}"
    )
    print(f"[petco] GET Constructor browse (limit={limit})...")
    r = session.get(url, timeout=30)
    print(f"[petco] HTTP {r.status_code}")

    if r.status_code == 400:
        try:
            msg = r.json().get("message", r.text[:300])
        except Exception:
            msg = r.text[:300]
        return [], f"Invalid Constructor key: {msg}"

    if r.status_code == 403:
        return [], "403 — refresh cookies/datadome from US browser"

    if r.status_code != 200:
        return [], f"HTTP {r.status_code}: {r.text[:200]}"

    products = parse_constructor(r.json(), limit)
    if not products:
        return [], "200 OK but no products parsed — check JSON shape"
    return products, "constructor-browse"


print("Petco Colab helpers loaded.")

# =============================================================================
# CELL 4 — Run scrape & save
# =============================================================================

products, source = fetch_petco(PETCO_COOKIES, PETCO_CONSTRUCTOR_KEY, limit=LIMIT)

if not products:
    print("[error]", source)
else:
    print(f"[ok] {len(products)} products via {source}\n")
    for i, p in enumerate(products[:10], 1):
        print(f"{i}. {p.title[:70]}")
        print(f"   ${p.price}  |  {p.review_count} reviews  |  {p.product_url[:80]}")

    payload = [p.model_dump() for p in products]
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\nSaved → {OUTPUT_JSON}")

    try:
        import pandas as pd

        display(pd.DataFrame(payload))  # noqa: F821 — Colab display
    except ImportError:
        pass

    try:
        from google.colab import files  # type: ignore

        files.download(OUTPUT_JSON)
    except ImportError:
        pass

# =============================================================================
# CELL 5 (optional) — Auto-discover Constructor key via Playwright
# Run CELL 1 deps first with playwright lines uncommented if key is missing
# =============================================================================
# !pip install -q playwright playwright-stealth
# !playwright install chromium

# import asyncio
# from urllib.parse import parse_qs, urlparse, unquote
# from playwright.async_api import async_playwright
#
# async def discover_key_and_scrape():
#     if not PETCO_COOKIES.strip():
#         print("Set PETCO_COOKIES first")
#         return
#     key_found = None
#     async with async_playwright() as pw:
#         browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
#         ctx = await browser.new_context(user_agent=USER_AGENT, locale="en-US")
#         await ctx.add_cookies([
#             {"name": k, "value": v, "domain": ".petco.com", "path": "/"}
#             for k, v in parse_cookies(PETCO_COOKIES).items()
#         ])
#         page = await ctx.new_page()
#         try:
#             from playwright_stealth import stealth_async
#             await stealth_async(page)
#         except ImportError:
#             pass
#         def on_req(req):
#             nonlocal key_found
#             if "ac.cnstrc.com" in req.url and "key=" in req.url:
#                 k = parse_qs(urlparse(req.url).query).get("key", [None])[0]
#                 if k:
#                     key_found = unquote(k)
#         page.on("request", on_req)
#         await page.goto(CATEGORY_URL, wait_until="domcontentloaded", timeout=90000)
#         await asyncio.sleep(10)
#         print("title:", await page.title())
#         print("discovered key:", key_found[:20] + "..." if key_found else None)
#         await browser.close()
#     if key_found:
#         global PETCO_CONSTRUCTOR_KEY
#         PETCO_CONSTRUCTOR_KEY = key_found
#         prods, src = fetch_petco(PETCO_COOKIES, key_found, LIMIT)
#         print(len(prods), "products", src)
#     return key_found
#
# await discover_key_and_scrape()
