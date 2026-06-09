# TopTails — Tractor Supply dog beds (Google Colab)
# curl_cffi HTTP/1.1 + free-proxy rotation (avoids Colab HTTP/2 errors + thin shell pages).

# =============================================================================
# CELL 1 — Install deps
# =============================================================================
# !pip install -q curl_cffi httpx pydantic pandas

# =============================================================================
# CELL 2 — Settings
# =============================================================================

LISTING_URL = "https://www.tractorsupply.com/tsc/catalog/dog-beds"
LIMIT = 20
OUTPUT_JSON = "tractor_colab_results.json"

PROXY_URL = ""  # e.g. http://user:pass@host:port
AUTO_FIND_PROXY = True
MAX_PROXIES_TO_TRY = 15

# =============================================================================
# CELL 3 — Helpers + scraper
# =============================================================================

import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel

try:
    from curl_cffi import requests as cffi_requests

    _USE_CFFI = True
    print("[tractor] curl_cffi available")
except ImportError:
    import httpx

    cffi_requests = None
    _USE_CFFI = False
    print("[tractor] using httpx")

for _p in (Path("."), Path("/content")):
    if (_p / "colab_proxy.py").is_file():
        sys.path.insert(0, str(_p.resolve()))
        break

try:
    from colab_proxy import ProxyConfig, get_with_proxy_rotation

    _HAS_PROXY_MOD = True
except ImportError:
    _HAS_PROXY_MOD = False
    print("[tractor] colab_proxy.py not found — inline proxy helpers")

    class ProxyConfig:
        def __init__(self):
            self.proxy_url = PROXY_URL
            self.auto_discover = AUTO_FIND_PROXY
            self.country = "US"
            self.max_proxies_to_try = MAX_PROXIES_TO_TRY
            self.validate_min_body = 8000
            self.impersonate = "chrome124"

    def fetch_free_proxies(country: str = "US", limit: int = 30) -> list[str]:
        sources = [
            f"https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=8000&country={country}",
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        ]
        seen: set[str] = set()
        out: list[str] = []
        getter = cffi_requests if _USE_CFFI else httpx
        for url in sources:
            try:
                text = (
                    getter.get(url, timeout=20, impersonate="chrome124").text
                    if _USE_CFFI
                    else httpx.get(url, timeout=20).text
                )
            except Exception:
                continue
            for line in text.splitlines():
                line = line.strip()
                if re.match(r"^[\d.\w\-]+:\d+$", line) or "@" in line:
                    p = line if "://" in line else f"http://{line}"
                    if p not in seen:
                        seen.add(p)
                        out.append(p)
                if len(out) >= limit:
                    break
        random.shuffle(out)
        return out

    def get_with_proxy(url, *, proxy_url, headers=None, timeout=45, impersonate="chrome124"):
        h = dict(headers or {})
        p = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
        px = {"http": p, "https": p}
        if _USE_CFFI:
            return cffi_requests.get(url, headers=h, proxies=px, impersonate=impersonate, timeout=timeout)
        with httpx.Client(proxies=px, follow_redirects=True, timeout=timeout, http2=False) as c:
            return c.get(url, headers=h)

    def get_with_proxy_rotation(url, config, *, headers=None, timeout=45, is_success=None):
        tried = []
        if config.proxy_url:
            tried.append(config.proxy_url if "://" in config.proxy_url else f"http://{config.proxy_url}")
        if config.auto_discover:
            tried.extend(fetch_free_proxies(limit=config.max_proxies_to_try * 2))
        seen: set[str] = set()
        n = 0
        for proxy in tried:
            if not proxy or proxy in seen:
                continue
            seen.add(proxy)
            n += 1
            if n > config.max_proxies_to_try + 1:
                break
            try:
                r = get_with_proxy(url, proxy_url=proxy, headers=headers, timeout=timeout)
                ok = is_success(r) if is_success else False
                print(f"[proxy] {urlparse(proxy).hostname}: status={r.status_code} len={len(r.text or '')} ok={ok}")
                if ok:
                    return r, proxy
            except Exception as e:
                print(f"[proxy] {urlparse(proxy).hostname}: {e}")
            time.sleep(random.uniform(0.3, 0.9))
        return None, None


class Product(BaseModel):
    source_site: str = "tractor_supply"
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0


BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


def proxy_config() -> ProxyConfig:
    if _HAS_PROXY_MOD:
        return ProxyConfig(
            proxy_url=PROXY_URL,
            auto_discover=AUTO_FIND_PROXY,
            max_proxies_to_try=MAX_PROXIES_TO_TRY,
            validate_min_body=8000,
        )
    c = ProxyConfig()
    c.proxy_url = PROXY_URL
    c.auto_discover = AUTO_FIND_PROXY
    c.max_proxies_to_try = MAX_PROXIES_TO_TRY
    return c


def normalize_price(raw) -> Optional[float]:
    if raw is None:
        return None
    m = re.search(r"\d+\.?\d*", str(raw).replace(",", ""))
    return float(m.group()) if m else None


def normalize_rating(raw) -> Optional[float]:
    if not raw:
        return None
    for m in re.finditer(r"\d+\.?\d*", str(raw)):
        v = float(m.group())
        if 0 <= v <= 5:
            return v
    return None


def _tractor_html_ok(r: Any) -> bool:
    if r.status_code not in (200, 206):
        return False
    text = r.text or ""
    low = text.lower()
    if "genericerror" in low and len(text) < 8000:
        return False
    # Real PLP has product links or JSON-LD products
    if re.search(r"/tsc/product/[a-z0-9-]+", text, re.I):
        return True
    if "application/ld+json" in low and "product" in low:
        return True
    return len(text) > 25_000


def fetch_listing_html() -> tuple[Optional[str], Optional[str]]:
    cfg = proxy_config()
    headers = {
        **BASE_HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "upgrade-insecure-requests": "1",
        "Referer": "https://www.tractorsupply.com/",
    }
    print("=== Fetch listing (proxy rotation) ===")
    r, proxy = get_with_proxy_rotation(
        LISTING_URL,
        cfg,
        headers=headers,
        timeout=50,
        is_success=_tractor_html_ok,
    )
    if r:
        return r.text, proxy

    print("=== Direct fallback ===")
    try:
        if _USE_CFFI:
            r2 = cffi_requests.get(
                LISTING_URL, headers=headers, impersonate="chrome124", timeout=50
            )
        else:
            r2 = httpx.get(
                LISTING_URL, headers=headers, timeout=50, follow_redirects=True
            )
        print(f"[tractor-direct] status={r2.status_code} len={len(r2.text or '')}")
        if _tractor_html_ok(r2):
            return r2.text, None
    except Exception as e:
        print(f"[tractor-direct] {e}")
    return None, None


def parse_html(html: str, limit: int) -> list[Product]:
    products: list[Product] = []
    seen: set[str] = set()

    for blob in re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            obj = json.loads(blob)
            if isinstance(obj, list):
                candidates = obj
            else:
                candidates = [obj]
            for obj in candidates:
                if obj.get("@type") == "ItemList":
                    items = obj.get("itemListElement") or []
                elif obj.get("@type") == "Product":
                    items = [{"item": obj}]
                else:
                    continue
                for entry in items:
                    item = entry.get("item") or entry
                    url_p = item.get("url") or item.get("@id") or ""
                    if not url_p or "/tsc/product/" not in url_p:
                        continue
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
                            image_url=item.get("image") or "",
                        )
                    )
                    if len(products) >= limit:
                        break
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
            for item in items[:limit]:
                slug = item.get("url") or item.get("canonicalUrl") or ""
                url_p = (
                    slug
                    if slug.startswith("http")
                    else f"https://www.tractorsupply.com{slug}"
                )
                if url_p in seen:
                    continue
                seen.add(url_p)
                products.append(
                    Product(
                        title=(item.get("name") or "")[:200],
                        price=normalize_price(item.get("price")),
                        product_url=url_p,
                    )
                )
            if products:
                print(f"[parse] {len(products)} from __NEXT_DATA__")
                return products
        except Exception as ex:
            print(f"[parse] __NEXT_DATA__ error: {ex}")

    paths = list(
        dict.fromkeys(
            re.findall(r"/tsc/product/[a-z0-9][a-z0-9-]*-\d+", html, re.I)
        )
    )
    print(f"[parse] {len(paths)} /tsc/product/ paths")
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


def scrape_tractor(limit: int = 20) -> tuple[list[Product], dict]:
    debug: dict = {}
    html, proxy = fetch_listing_html()
    debug["proxy"] = proxy
    if not html:
        return [], debug
    debug["html_len"] = len(html)
    products = parse_html(html, limit)
    debug["product_count"] = len(products)
    return products, debug


print("Tractor Supply Colab helpers loaded.")

# =============================================================================
# CELL 4 — Run
# =============================================================================

products, debug = scrape_tractor(limit=LIMIT)

print("debug:", debug)
if not products:
    print(
        "\n[BLOCKED] No products.\n"
        "• Best: set PROXY_URL to a paid residential proxy.\n"
        "• AUTO_FIND_PROXY tries free public lists (unreliable, not true residential).\n"
        "• len≈2595 without /tsc/product/ = bot shell, not the real catalog.\n"
        "• Run locally or use ScraperAPI if free proxies all fail."
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
