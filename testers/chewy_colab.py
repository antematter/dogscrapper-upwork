# TopTails — Chewy dog beds (Google Colab)
# Uses curl_cffi + optional free-proxy rotation (better than raw Colab IP for Akamai).
#
# Copy each "# CELL N" block into separate notebook cells.

# =============================================================================
# CELL 1 — Install deps
# =============================================================================
# !pip install -q curl_cffi httpx pydantic pandas

# =============================================================================
# CELL 2 — Settings
# =============================================================================

LISTING_URL = "https://www.chewy.com/b/dog-beds-365"
LIMIT = 20
OUTPUT_JSON = "chewy_colab_results.json"

# Optional: your own residential/datacenter proxy (best option)
# Example: "http://user:pass@gate.provider.com:10000"
PROXY_URL = ""

# When True, pulls public US HTTP proxy lists and tests them against Chewy
AUTO_FIND_PROXY = True
MAX_PROXIES_TO_TRY = 15

# =============================================================================
# CELL 3 — Helpers + scraper (paste colab_proxy.py here OR upload it beside notebook)
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
    print("[chewy] curl_cffi available")
except ImportError:
    import httpx

    cffi_requests = None  # type: ignore
    _USE_CFFI = False
    print("[chewy] using httpx (install curl_cffi for better TLS)")

# Load shared proxy module from repo if present
for _p in (Path("."), Path("/content")):
    if (_p / "colab_proxy.py").is_file():
        sys.path.insert(0, str(_p.resolve()))
        break

try:
    from colab_proxy import ProxyConfig, get_with_proxy, get_with_proxy_rotation

    _HAS_PROXY_MOD = True
except ImportError:
    _HAS_PROXY_MOD = False
    print("[chewy] colab_proxy.py not found — using inline proxy helpers")

    class ProxyConfig:  # noqa: D101
        def __init__(self):
            self.proxy_url = PROXY_URL
            self.auto_discover = AUTO_FIND_PROXY
            self.country = "US"
            self.max_proxies_to_try = MAX_PROXIES_TO_TRY
            self.validate_min_body = 5000
            self.impersonate = "chrome124"

    def _proxy_dict(proxy_url: str) -> dict[str, str]:
        p = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
        return {"http": p, "https": p}

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
                if _USE_CFFI:
                    text = getter.get(url, timeout=20, impersonate="chrome124").text
                else:
                    text = httpx.get(url, timeout=20).text
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

    def get_with_proxy(url, *, proxy_url, headers=None, json_mode=False, timeout=40, impersonate="chrome124"):
        h = dict(headers or {})
        px = _proxy_dict(proxy_url)
        if _USE_CFFI:
            return cffi_requests.get(url, headers=h, proxies=px, impersonate=impersonate, timeout=timeout)
        with httpx.Client(proxies=px, follow_redirects=True, timeout=timeout, http2=False) as c:
            return c.get(url, headers=h)

    def get_with_proxy_rotation(url, config, *, headers=None, json_mode=False, timeout=40, is_success=None):
        tried_urls = []
        if config.proxy_url:
            tried_urls.append(config.proxy_url if "://" in config.proxy_url else f"http://{config.proxy_url}")
        if config.auto_discover:
            tried_urls.extend(fetch_free_proxies(limit=config.max_proxies_to_try * 2))
        seen: set[str] = set()
        n = 0
        for proxy in tried_urls:
            if not proxy or proxy in seen:
                continue
            seen.add(proxy)
            n += 1
            if n > config.max_proxies_to_try + 1:
                break
            try:
                r = get_with_proxy(url, proxy_url=proxy, headers=headers, json_mode=json_mode, timeout=timeout)
                ok = is_success(r) if is_success else r.status_code == 200 and len(r.text or "") > 3000
                print(f"[proxy] {urlparse(proxy).hostname}: status={r.status_code} len={len(r.text or '')} ok={ok}")
                if ok:
                    return r, proxy
            except Exception as e:
                print(f"[proxy] {urlparse(proxy).hostname}: {e}")
            time.sleep(random.uniform(0.3, 0.9))
        return None, None


class Product(BaseModel):
    source_site: str = "chewy"
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
            validate_min_body=5000,
        )
    c = ProxyConfig()
    c.proxy_url = PROXY_URL
    c.auto_discover = AUTO_FIND_PROXY
    c.max_proxies_to_try = MAX_PROXIES_TO_TRY
    return c


def normalize_price(raw) -> Optional[float]:
    if raw is None:
        return None
    m = re.search(r"\d+\.?\d*", str(raw))
    return float(m.group()) if m else None


def _chewy_html_ok(r: Any) -> bool:
    if r.status_code in (403, 429, 503):
        return False
    text = (r.text or "").lower()
    if "no treats" in text or "access denied" in text:
        return False
    if "/dp/" in text or "__next_data__" in text:
        return True
    return r.status_code == 200 and len(r.text or "") > 12_000


def _direct_get(url: str, *, html: bool = True) -> Any:
    h = dict(BASE_HEADERS)
    if html:
        h["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        h["sec-fetch-dest"] = "document"
        h["sec-fetch-mode"] = "navigate"
    if _USE_CFFI:
        return cffi_requests.get(url, headers=h, impersonate="chrome124", timeout=40)
    return httpx.get(url, headers=h, timeout=40, follow_redirects=True)


def fetch_listing_html() -> tuple[Optional[str], Optional[str]]:
    """Returns (html, proxy_used)."""
    cfg = proxy_config()
    print("=== Fetch listing (proxy rotation) ===")
    r, proxy = get_with_proxy_rotation(
        LISTING_URL,
        cfg,
        headers={
            **BASE_HEADERS,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "upgrade-insecure-requests": "1",
        },
        timeout=45,
        is_success=_chewy_html_ok,
    )
    if r:
        return r.text, proxy

    print("=== Direct (no proxy) fallback ===")
    try:
        r2 = _direct_get(LISTING_URL)
        print(f"[chewy-direct] status={r2.status_code} len={len(r2.text or '')}")
        if _chewy_html_ok(r2):
            return r2.text, None
    except Exception as e:
        print(f"[chewy-direct] {e}")
    return None, None


def parse_html(html: str, limit: int) -> list[Product]:
    products: list[Product] = []
    seen: set[str] = set()

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
            for item in pages[:limit]:
                part = item.get("part") or item.get("id") or ""
                slug = item.get("canonicalUrl") or f"/dp/{part}"
                url_p = slug if slug.startswith("http") else f"https://www.chewy.com{slug}"
                if url_p in seen:
                    continue
                seen.add(url_p)
                products.append(
                    Product(
                        title=(item.get("name") or "")[:200],
                        price=normalize_price(item.get("price") or item.get("salePrice")),
                        avg_rating=item.get("rating"),
                        review_count=int(item.get("reviewCount") or 0),
                        product_url=url_p,
                        image_url=item.get("imageUrl") or "",
                    )
                )
            if products:
                print(f"[parse] {len(products)} from __NEXT_DATA__")
                return products
        except Exception as ex:
            print(f"[parse] __NEXT_DATA__ error: {ex}")

    paths = list(
        dict.fromkeys(
            re.findall(r"(?:https://www\.chewy\.com)?(/[a-z0-9-]+/dp/\d+)", html, re.I)
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


def scrape_chewy(limit: int = 20) -> tuple[list[Product], dict]:
    debug: dict = {}
    html, proxy = fetch_listing_html()
    debug["proxy"] = proxy
    if not html:
        return [], debug
    debug["html_len"] = len(html)
    products = parse_html(html, limit)
    debug["product_count"] = len(products)
    return products, debug


print("Chewy Colab helpers loaded.")

# =============================================================================
# CELL 4 — Run
# =============================================================================

products, debug = scrape_chewy(limit=LIMIT)

print("debug:", debug)
if not products:
    print(
        "\n[BLOCKED] No products.\n"
        "• Set PROXY_URL to a residential proxy you trust (best).\n"
        "• Or keep AUTO_FIND_PROXY=True (free lists are datacenter — hit or miss).\n"
        "• Chewy blocks Colab + most free proxies (429/403).\n"
        "• Alternatives: run locally, ScraperAPI, or affiliate product API."
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
