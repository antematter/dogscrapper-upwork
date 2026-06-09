"""
petco_scraper.py — Fetches dog beds from Petco (Constructor.io API + Playwright fallback).

Setup (run once):
    python -m venv venv
    source venv/bin/activate
    pip install requests pydantic
    # optional, for fallback when API cookies expire:
    pip install playwright playwright-stealth
    playwright install chromium

Run:
    python petco.py

Cookies (required for Petco APIs — expire every few hours):
    1. Open https://www.petco.com/shop/en/petcostore/category/dog/dog-beds-and-bedding
    2. F12 → Network → filter "cnstrc" → click a browse request → copy full Cookie header
       OR Console: copy(document.cookie)
    3. Paste into petco_cookies.txt (same folder) OR set env PETCO_COOKIES

Constructor API key (NOT the same as ConstructorioID_client_id in cookies):
    From the same Network request, copy the `key=` query param value.
    Save to petco_constructor_key.txt OR set env PETCO_CONSTRUCTOR_KEY.
    The script can auto-capture this key via Playwright when cookies are still valid.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests
from pydantic import BaseModel

_DIR = Path(__file__).resolve().parent
_COOKIES_FILE = _DIR / "petco_cookies.txt"
_KEY_FILE = _DIR / "petco_constructor_key.txt"

CATEGORY_URL = (
    "https://www.petco.com/shop/en/petcostore/category/dog/dog-beds-and-bedding"
)
CATEGORY_API = (
    "https://www.petco.com/shop/api/2.0/page/category/dog-beds-and-bedding"
)
GROUP_ID = "dog-beds-and-bedding"

# Legacy inline cookies — prefer petco_cookies.txt when present
COOKIES_RAW = (
    "rmn_session_id=da526c6e-a714-4a3d-a462-ff84203b3908|1786566162795; "
    "tntIdV2=abf1778790162991.34_0; sessionIdV2=abf1778790162991; "
    "at_plp_sdd_bopus_message=control; at_plp_phase_4=exp a; "
    "at_atc_style_product_tile_plp=test; at_plp_rd_filter_v1=test; "
    "Edgescape-Country=PK; Edgescape-City=Rawalpindi; "
    "Edgescape-State=Punjab; Edgescape-Zip=46300; "
    "Edgescape-Lat=33.63320; Edgescape-Long=73.04020; "
    "ConstructorioID_client_id=0ca8951a-0b13-40c3-b3ac-14fdcede47dc; "
    "at_check=true; CUSTOMER_ID=-1002; "
    "WC_bopusStoreId=12356; WC_preferredStoreId=1147; "
    "ConstructorioID_session_id=1; "
    "datadome=dzGxeaje3m9siA_misrkjGk2Si_z0JLjHVdZqE9Mv0Lw2EdsQl3tWuG2_pszCZDEWSrtvpbYmEbpdv9jYCj7Ojsw_AEC7rmyeg8CKLKUfI0zpyBJ1f8Mc3khIDx0EByh; "
    "ConstructorioID_session={\"sessionId\":1,\"lastTime\":1778790224616}"
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def parse_cookie_string(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in raw.strip().split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


def load_cookie_string() -> str:
    env = os.environ.get("PETCO_COOKIES", "").strip()
    if env:
        return env
    if _COOKIES_FILE.is_file():
        return _COOKIES_FILE.read_text(encoding="utf-8").strip()
    return COOKIES_RAW


def load_constructor_key(cookies: dict[str, str]) -> str | None:
    env = os.environ.get("PETCO_CONSTRUCTOR_KEY", "").strip()
    if env:
        return env
    if _KEY_FILE.is_file():
        key = _KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    # ConstructorioID_client_id in cookies is NOT the Constructor API key
    return None


def save_constructor_key(key: str) -> None:
    _KEY_FILE.write_text(key.strip(), encoding="utf-8")
    print(f"[petco] Saved Constructor API key to {_KEY_FILE.name}")


def cookies_for_playwright(cookie_dict: dict[str, str]) -> list[dict]:
    return [
        {
            "name": k,
            "value": v,
            "domain": ".petco.com",
            "path": "/",
        }
        for k, v in cookie_dict.items()
    ]


# ── Data model ────────────────────────────────────────────────────────────────


class Product(BaseModel):
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0
    five_star_ratio: float = 0.0
    verified_ratio: float = 0.75
    review_dates: list[str] = []
    trust_score: float = 0.0
    brand: str = ""


# ── Trust score ───────────────────────────────────────────────────────────────


def volume_weight(n: int) -> float:
    if n < 15:
        return 0.0
    return 1 / (1 + math.exp(-0.05 * (n - 50)))


def distribution_penalty(r: float) -> float:
    if r > 0.90:
        return 0.5
    if r > 0.80:
        return 0.75
    return 1.0


def verified_bonus(r: float) -> float:
    return 1.0 + (0.3 * r)


def velocity_penalty(dates: list[str]) -> float:
    if len(dates) < 5:
        return 1.0
    try:
        parsed = sorted(datetime.fromisoformat(d) for d in dates)
        total = len(parsed)
        for i, start in enumerate(parsed):
            window = sum(1 for d in parsed[i:] if (d - start).days <= 3)
            if window / total > 0.30:
                return 0.6
    except Exception:
        pass
    return 1.0


def compute_trust_score(p: Product) -> float:
    if not p.avg_rating or p.review_count < 15:
        return 0.0
    score = (
        (p.avg_rating / 5.0)
        * volume_weight(p.review_count)
        * distribution_penalty(p.five_star_ratio)
        * verified_bonus(p.verified_ratio)
        * velocity_penalty(p.review_dates)
    )
    return round(min(max(score, 0.0), 1.0), 4)


# ── HTTP session ──────────────────────────────────────────────────────────────


def make_session(cookie_raw: str) -> requests.Session:
    s = requests.Session()
    s.cookies.update(parse_cookie_string(cookie_raw))
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": CATEGORY_URL,
            "Origin": "https://www.petco.com",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
    )
    return s


def _constructor_browse_url(api_key: str, client_id: str, limit: int) -> str:
    ts = int(time.time() * 1000)
    return (
        f"https://ac.cnstrc.com/browse/group_id/{GROUP_ID}"
        f"?key={api_key}&c={client_id}"
        f"&num_results_per_page={limit}&page=1&sort_by=relevance&_dt={ts}"
    )


def fetch_products_api(
    session: requests.Session,
    api_key: str,
    client_id: str,
    limit: int = 24,
) -> tuple[dict | None, str | None, str]:
    """Returns (json_data, source_name, diagnostic_message)."""
    endpoints: list[tuple[str, str]] = [
        (
            "constructor-browse",
            _constructor_browse_url(api_key, client_id, limit),
        ),
        (
            "petco-category",
            f"{CATEGORY_API}?pageSize={limit}&page=0&sortBy=topRated",
        ),
        (
            "petco-browse",
            "https://www.petco.com/shop/api/2.0/browse/products"
            f"?category={GROUP_ID}&pageSize={limit}&page=0&sortBy=topRated",
        ),
    ]

    last_diag = ""
    for name, url in endpoints:
        try:
            print(f"[petco] Trying {name}...")
            r = session.get(url, timeout=20)
            print(f"[petco] {name}: HTTP {r.status_code}")

            if r.status_code == 200:
                try:
                    data = r.json()
                    print(
                        f"[petco] {name}: JSON ok — keys: {list(data.keys())[:8]}"
                    )
                    return data, name, ""
                except Exception:
                    last_diag = f"{name}: not JSON — {r.text[:120]}"
                    print(f"[petco] {last_diag}")

            elif r.status_code == 400 and name == "constructor-browse":
                try:
                    msg = r.json().get("message", r.text[:200])
                except Exception:
                    msg = r.text[:200]
                last_diag = f"Constructor key invalid: {msg}"
                print(f"[petco] {last_diag}")

            elif r.status_code == 403:
                last_diag = (
                    f"{name}: 403 — datadome/cookies expired or geo-blocked "
                    f"(check Edgescape-Country in cookies; use US session)"
                )
                print(f"[petco] {last_diag}")

            else:
                last_diag = f"{name}: HTTP {r.status_code} — {r.text[:120]}"
                print(f"[petco] {last_diag}")

            time.sleep(random.uniform(0.6, 1.2))

        except Exception as e:
            last_diag = f"{name}: {e}"
            print(f"[petco] {last_diag}")

    return None, None, last_diag


def parse_products(data: dict, source: str) -> list[Product]:
    products: list[Product] = []

    if source == "constructor-browse":
        items = data.get("response", {}).get("results", [])
        for item in items:
            try:
                d = item.get("data", {})
                p = Product()
                p.title = item.get("value", "") or d.get("name", "")
                p.brand = d.get("brand", "")
                p.image_url = d.get("image_url", "")
                url = d.get("url", "")
                p.product_url = (
                    url if url.startswith("http") else f"https://www.petco.com{url}"
                )

                for pk in ["sale_price", "price", "list_price"]:
                    val = d.get(pk)
                    if val:
                        try:
                            p.price = float(str(val).replace("$", "").replace(",", ""))
                            break
                        except ValueError:
                            pass

                for rk in ["avg_rating", "average_rating", "rating"]:
                    val = d.get(rk)
                    if val:
                        try:
                            p.avg_rating = float(val)
                            break
                        except ValueError:
                            pass

                for ck in ["review_count", "num_reviews", "total_reviews"]:
                    val = d.get(ck)
                    if val:
                        try:
                            p.review_count = int(val)
                            break
                        except (ValueError, TypeError):
                            pass

                if p.title:
                    products.append(p)
            except Exception as e:
                print(f"[parse] {e}")
        return products

    items = (
        data.get("products")
        or data.get("results")
        or data.get("items")
        or []
    )
    if not items and isinstance(data.get("data"), dict):
        items = data["data"].get("products") or []

    for item in items:
        try:
            p = Product()
            p.title = (
                item.get("displayName") or item.get("name") or item.get("title") or ""
            ).strip()
            p.brand = item.get("brandName") or item.get("brand") or ""

            for pk in ["salePrice", "listPrice", "price", "regularPrice"]:
                val = item.get(pk)
                if val:
                    try:
                        p.price = float(str(val).replace("$", "").replace(",", ""))
                        break
                    except ValueError:
                        pass

            slug = item.get("seoURL") or item.get("url") or item.get("pdpUrl") or ""
            if slug:
                p.product_url = (
                    slug if slug.startswith("http") else f"https://www.petco.com{slug}"
                )

            for ik in ["thumbnailImageUrl", "imageUrl", "primaryImageUrl"]:
                img = item.get(ik)
                if img:
                    p.image_url = img if img.startswith("http") else f"https:{img}"
                    break

            for rk in ["averageRating", "avgRating", "rating"]:
                val = item.get(rk)
                if val:
                    try:
                        p.avg_rating = float(val)
                        break
                    except ValueError:
                        pass

            for ck in ["totalReviews", "reviewCount", "numReviews"]:
                val = item.get(ck)
                if val:
                    try:
                        p.review_count = int(val)
                        break
                    except (ValueError, TypeError):
                        pass

            if p.title:
                products.append(p)
        except Exception as e:
            print(f"[parse] {e}")

    return products


def fetch_reviews(p: Product, session: requests.Session) -> None:
    if not p.product_url:
        return
    match = re.search(r"/(\d{6,})", p.product_url)
    if not match:
        return
    product_id = match.group(1)
    try:
        url = (
            "https://api.bazaarvoice.com/data/reviews.json"
            "?apiversion=5.4"
            "&passkey=caBHilVCMiNjqEaVP2PzMWEAXPlBMCStJkCCKRyqJ7g8I"
            f"&Filter=ProductId:{product_id}"
            "&Statistics=Reviews&Limit=20&Sort=SubmissionTime:desc"
        )
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return
        data = r.json()

        dates = []
        for rev in data.get("Results", []):
            dt_str = rev.get("SubmissionTime", "")
            if dt_str:
                try:
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    dates.append(dt.date().isoformat())
                except ValueError:
                    pass
        if dates:
            p.review_dates = dates

        stats = (
            data.get("Includes", {})
            .get("ProductsStats", {})
            .get(product_id, {})
            .get("ReviewStatistics", {})
            .get("RatingDistribution", [])
        )
        if stats:
            counts = {int(d["RatingValue"]): int(d["Count"]) for d in stats}
            total = sum(counts.values()) or 1
            p.five_star_ratio = round(counts.get(5, 0) / total, 3)

    except Exception as e:
        print(f"[reviews] {product_id}: {e}")


# ── Playwright: discover Constructor key + optional JSON intercept ───────────


def _extract_key_from_url(url: str) -> str | None:
    qs = parse_qs(urlparse(url).query)
    key = qs.get("key", [None])[0]
    return unquote(key) if key else None


async def _fetch_via_playwright(
    cookie_dict: dict[str, str], limit: int
) -> tuple[list[Product], str | None]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[petco] Playwright not installed — skip browser fallback")
        return [], None

    try:
        from playwright_stealth import stealth_async
    except ImportError:
        stealth_async = None

    discovered_key: str | None = None
    constructor_json: dict | None = None
    products: list[Product] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1400, "height": 900},
        )
        if cookie_dict:
            await context.add_cookies(cookies_for_playwright(cookie_dict))

        page = await context.new_page()
        if stealth_async:
            await stealth_async(page)

        async def on_request(request) -> None:
            nonlocal discovered_key
            url = request.url
            if "ac.cnstrc.com" in url and "browse" in url:
                key = _extract_key_from_url(url)
                if key and key != discovered_key:
                    discovered_key = key
                    print(f"[petco] Discovered Constructor API key from network")

        async def on_response(response) -> None:
            nonlocal constructor_json
            url = response.url
            if (
                constructor_json is None
                and "ac.cnstrc.com/browse" in url
                and response.status == 200
            ):
                try:
                    constructor_json = await response.json()
                    print("[petco] Captured Constructor browse JSON from browser")
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"[petco] Playwright loading {CATEGORY_URL} ...")
        await page.goto(CATEGORY_URL, wait_until="domcontentloaded", timeout=70_000)
        for _ in range(4):
            await page.keyboard.press("End")
            await asyncio.sleep(1.2)

        title = await page.title()
        html = await page.content()
        if "datadome" in html.lower() or "captcha" in html.lower() or len(html) < 3000:
            print(
                f"[petco] Playwright hit bot wall (title={title!r}, len={len(html)})"
            )
        elif constructor_json:
            products = parse_products(constructor_json, "constructor-browse")
        else:
            # DOM fallback: product links
            raw_items = await page.evaluate(
                """() => {
                    const seen = new Set();
                    const out = [];
                    for (const a of document.querySelectorAll("a[href*='/product/']")) {
                        const href = (a.href || '').split('?')[0];
                        if (!href || seen.has(href)) continue;
                        seen.add(href);
                        let title = (a.innerText || '').replace(/\\s+/g, ' ').trim();
                        const img = a.querySelector('img');
                        if ((!title || title.length < 4) && img?.alt) title = img.alt.trim();
                        let priceText = '';
                        let root = a.closest('li, article, div') || a.parentElement;
                        for (let i = 0; i < 8 && root; i++) {
                            const m = (root.innerText || '').match(/\\$\\s?\\d+[\\d,.]*/);
                            if (m) { priceText = m[0]; break; }
                            root = root.parentElement;
                        }
                        if (title.length >= 3) out.push({ href, title, priceText });
                        if (out.length >= 40) break;
                    }
                    return out;
                }"""
            )
            for row in raw_items[:limit]:
                price = None
                if row.get("priceText"):
                    m = re.search(r"\d+\.?\d*", row["priceText"])
                    if m:
                        price = float(m.group())
                products.append(
                    Product(
                        title=row["title"][:200],
                        price=price,
                        product_url=row["href"],
                    )
                )
            if products:
                print(f"[petco] Parsed {len(products)} products from DOM")

        await browser.close()

    return products, discovered_key


def fetch_products_playwright(
    cookie_dict: dict[str, str], limit: int
) -> tuple[list[Product], str | None]:
    return asyncio.run(_fetch_via_playwright(cookie_dict, limit))


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    print("=" * 60)
    print("TopTails — Petco Dog Beds Scraper")
    print("=" * 60)

    cookie_raw = load_cookie_string()
    cookie_dict = parse_cookie_string(cookie_raw)
    client_id = cookie_dict.get("ConstructorioID_client_id", "")

    if cookie_dict.get("Edgescape-Country", "").upper() not in ("US", ""):
        print(
            f"[warn] Cookies show country={cookie_dict.get('Edgescape-Country')!r} — "
            "Petco often 403s outside US. Re-copy cookies from a US browser session."
        )

    if "datadome" not in cookie_dict:
        print("[warn] No datadome cookie — session may be incomplete.")

    session = make_session(cookie_raw)
    api_key = load_constructor_key(cookie_dict)
    products: list[Product] = []
    data: dict | None = None
    source: str | None = None
    diag = ""

    if api_key:
        data, source, diag = fetch_products_api(session, api_key, client_id, limit=24)
        if data:
            products = parse_products(data, source)

    if not products:
        if not api_key:
            print(
                "\n[petco] No Constructor API key set "
                f"(add {_KEY_FILE.name} or PETCO_CONSTRUCTOR_KEY)."
            )
            print(
                "  Tip: it is the `key=` param on ac.cnstrc.com requests — "
                "NOT ConstructorioID_client_id from cookies."
            )
        print("\n[petco] Trying Playwright fallback (discover key + scrape)...")
        pw_products, discovered_key = fetch_products_playwright(cookie_dict, limit=24)
        if discovered_key:
            save_constructor_key(discovered_key)
            if not pw_products:
                session = make_session(cookie_raw)
                data, source, diag = fetch_products_api(
                    session, discovered_key, client_id, limit=24
                )
                if data:
                    products = parse_products(data, source)
        products = products or pw_products

    if not products:
        print("\n[error] Could not fetch products.")
        if diag:
            print(f"  Last API error: {diag}")
        print("\nFix checklist:")
        print("  1. Fresh cookies → petco_cookies.txt (US browser, after page loads)")
        print("  2. Constructor key → petco_constructor_key.txt (from Network → cnstrc → key=)")
        print("  3. pip install playwright playwright-stealth && playwright install chromium")
        return

    print(f"\n[petco] Got {len(products)} products. Fetching reviews for top 8...")
    for p in products[:8]:
        fetch_reviews(p, session)
        time.sleep(random.uniform(0.4, 0.9))

    for p in products:
        p.trust_score = compute_trust_score(p)

    scoreable = [p for p in products if p.trust_score > 0]
    scoreable.sort(key=lambda x: x.trust_score, reverse=True)
    top2 = scoreable[:2]

    if not top2:
        print(f"\n[warn] No products passed the 15-review minimum.")
        print(f"Got {len(products)} products. Sample:")
        for p in products[:5]:
            print(
                f"  - {p.title[:60]} | reviews: {p.review_count} | rating: {p.avg_rating}"
            )
        return

    print(f"\nScraped {len(products)} -> scored {len(scoreable)} -> Top 2:\n")
    print("-" * 60)

    for i, p in enumerate(top2, 1):
        print(f"#{i}  {p.title}")
        if p.brand:
            print(f"    Brand:        {p.brand}")
        print(f"    Price:        ${p.price}" if p.price else "    Price:        N/A")
        print(f"    Rating:       {p.avg_rating} ({p.review_count} reviews)")
        print(f"    Trust Score:  {p.trust_score}")
        print(f"    5-star ratio: {p.five_star_ratio:.1%}")
        print(f"    URL:          {p.product_url}")
        print()

    out = _DIR / "petco_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump([p.model_dump() for p in scoreable], f, indent=2)
    print(f"Full results saved to: {out}")


if __name__ == "__main__":
    main()
