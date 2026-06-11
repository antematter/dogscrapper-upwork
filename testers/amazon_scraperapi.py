# TopTails — Amazon dog beds via ScraperAPI (Google Colab / local)
#
# Probes structured Amazon search endpoint and generic URL fetch tiers.
#
# Install: pip install requests pydantic
# Credentials: SCRAPERAPI_KEY in env or toptails/backend/.env

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

import requests
from pydantic import BaseModel

_ENV_FILE = Path(__file__).resolve().parent.parent / "toptails" / "backend" / ".env"
if _ENV_FILE.is_file():
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE)
    except ImportError:
        pass

SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "").strip()

DEFAULT_QUERY = "dog bed"
SEARCH_URL = "https://www.amazon.com/s?k={query}"
COUNTRY_CODE = "us"
TLD = "com"
REQUEST_TIMEOUT = 180
OUTPUT_JSON = "amazon_scraperapi_results.json"
LIMIT = 24

_ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.I)
_SPONSORED_MARKERS = ("spons", "sspa", "picassoRedirect")


class Product(BaseModel):
    source_site: str = "amazon"
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0
    asin: str = ""


def normalize_price(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    m = re.search(r"\d+\.?\d*", str(raw).replace(",", "").replace("$", ""))
    return float(m.group()) if m else None


def normalize_rating(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    for m in re.finditer(r"\d+\.?\d*", str(raw)):
        v = float(m.group())
        if 0.0 <= v <= 5.0:
            return v
    return None


def is_relevant_dog_bed(title: str, product_url: str = "") -> bool:
    t = (title or "").strip()
    if len(t) < 3:
        return False
    bed = re.compile(
        r"\b(dog bed|dog beds|pet bed|bolster|orthopedic|donut|cuddler|"
        r"elevated bed|cooling bed|\bbed\b|\bbeds\b|cot\b)\b",
        re.I,
    )
    exclude = re.compile(
        r"\b(dog food|cat food|treats?\b|dog toy|litter box|training pad)\b",
        re.I,
    )
    if exclude.search(t):
        return False
    if bed.search(t):
        return True
    u = product_url or ""
    return bool(re.search(r"(dog[-_]bed|dog[-_]beds)", u, re.I))


def extract_asin(url: str) -> str:
    m = _ASIN_RE.search(url or "")
    return m.group(1).upper() if m else ""


def canonical_amazon_url(asin: str) -> str:
    return f"https://www.amazon.com/dp/{asin.upper()}"


def is_sponsored_url(url: str) -> bool:
    low = (url or "").lower()
    return any(m in low for m in _SPONSORED_MARKERS)


def fetch_structured(
    api_key: str,
    query: str,
    *,
    country_code: str = COUNTRY_CODE,
    tld: str = TLD,
    timeout: int = REQUEST_TIMEOUT,
    quiet: bool = False,
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    if not api_key:
        return None, {"error": "SCRAPERAPI_KEY is empty"}

    params = {
        "api_key": api_key,
        "query": query,
        "country_code": country_code,
        "tld": tld,
    }
    if not quiet:
        print(f"[structured] query={query!r} tld={tld} country={country_code}")

    try:
        r = requests.get(
            "https://api.scraperapi.com/structured/amazon/search",
            params=params,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return None, {"error": str(e), "mode": "structured"}

    dbg: dict[str, Any] = {
        "mode": "structured",
        "status_code": r.status_code,
        "body_len": len(r.content or b""),
    }

    if not quiet:
        print(f"[structured] HTTP {r.status_code} body_len={dbg['body_len']}")

    if r.status_code != 200:
        dbg["error"] = (r.text or "")[:500]
        return None, dbg

    try:
        data = r.json()
    except json.JSONDecodeError as ex:
        dbg["error"] = f"JSON decode: {ex}"
        dbg["snippet"] = (r.text or "")[:300]
        return None, dbg

    return data, dbg


def fetch_via_scraperapi(
    target_url: str,
    api_key: str,
    *,
    render: bool = False,
    country_code: str = COUNTRY_CODE,
    premium: bool = False,
    ultra_premium: bool = False,
    timeout: int = REQUEST_TIMEOUT,
    quiet: bool = False,
) -> tuple[Optional[str], dict[str, Any]]:
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

    if not quiet:
        print(
            f"[generic] GET {target_url[:90]}… "
            f"render={render} premium={premium} ultra={ultra_premium}"
        )

    try:
        r = requests.get("https://api.scraperapi.com/", params=params, timeout=timeout)
    except requests.RequestException as e:
        return None, {"error": str(e), "url": target_url, "mode": "generic"}

    dbg: dict[str, Any] = {
        "mode": "generic",
        "url": target_url,
        "status_code": r.status_code,
        "body_len": len(r.content or b""),
        "render": render,
        "premium": premium,
        "ultra_premium": ultra_premium,
    }

    if not quiet:
        print(f"[generic] HTTP {r.status_code} body_len={dbg['body_len']}")

    if r.status_code != 200:
        dbg["error"] = (r.text or "")[:500]
        if r.status_code in (403, 500) and "premium" in (r.text or "").lower():
            dbg["needs_plan_upgrade"] = True
        return None, dbg

    text = r.text or ""
    low = text.lower()
    if len(text) < 8_000:
        dbg["likely_shell_or_block"] = True
    if "captcha" in low or "robot check" in low or "type the characters" in low:
        dbg["likely_block"] = True

    return text, dbg


def parse_structured_response(
    data: dict[str, Any], limit: int, *, apply_relevance: bool = True
) -> tuple[list[Product], dict[str, Any]]:
    stats: dict[str, Any] = {"parse_source": "structured"}
    products: list[Product] = []
    seen: set[str] = set()

    results = data.get("results") or []
    stats["results_count"] = len(results) if isinstance(results, list) else 0
    stats["ads_count"] = len(data.get("ads") or [])

    if not isinstance(results, list):
        return [], stats

    for item in results:
        if len(products) >= limit or not isinstance(item, dict):
            continue
        if str(item.get("type") or "search_product") != "search_product":
            continue

        title = (item.get("name") or "").strip()
        if not title:
            continue

        asin = str(item.get("asin") or "").strip().upper()
        url = str(item.get("url") or "")
        if not asin:
            asin = extract_asin(url)
        if not asin:
            continue

        product_url = canonical_amazon_url(asin)
        if product_url in seen or is_sponsored_url(url):
            continue
        if apply_relevance and not is_relevant_dog_bed(title, product_url):
            continue

        seen.add(product_url)
        products.append(
            Product(
                title=title[:200],
                price=normalize_price(item.get("price")),
                product_url=product_url,
                image_url=str(item.get("image") or ""),
                avg_rating=normalize_rating(item.get("stars")),
                review_count=int(item.get("total_reviews") or 0),
                asin=asin,
            )
        )

    return products, stats


def parse_amazon_html(
    html: str, limit: int, *, apply_relevance: bool = True
) -> tuple[list[Product], dict[str, Any]]:
    stats: dict[str, Any] = {"parse_source": "html"}
    products: list[Product] = []
    seen: set[str] = set()

    cards = re.split(
        r'data-component-type="s-search-result"',
        html,
        flags=re.I,
    )[1:]
    stats["card_splits"] = len(cards)

    for card in cards:
        if len(products) >= limit:
            break

        asin_m = re.search(r'data-asin="([A-Z0-9]{10})"', card, re.I)
        if not asin_m:
            continue
        asin = asin_m.group(1).upper()
        if asin in ("", "0000000000"):
            continue

        title_m = re.search(
            r'<h2[^>]*>.*?<span[^>]*>([^<]{5,200})</span>',
            card,
            re.I | re.S,
        )
        if not title_m:
            title_m = re.search(
                r'data-cy="title-recipe"[^>]*>.*?<span[^>]*>([^<]{5,200})</span>',
                card,
                re.I | re.S,
            )
        if not title_m:
            continue
        title = title_m.group(1).strip()

        link_m = re.search(r'href="(/[^"]+/dp/[^"]+)"', card, re.I)
        href = link_m.group(1) if link_m else ""
        if is_sponsored_url(href) or is_sponsored_url(card):
            continue

        product_url = canonical_amazon_url(asin)
        if product_url in seen:
            continue

        price_m = re.search(
            r'class="a-offscreen"[^>]*>\$?([\d,.]+)<',
            card,
            re.I,
        )
        rating_m = re.search(
            r'aria-label="([\d.]+)\s+out of 5 stars"',
            card,
            re.I,
        )
        review_m = re.search(
            r'aria-label="([\d,]+)\s+ratings?"',
            card,
            re.I,
        )
        if not review_m:
            review_m = re.search(
                r'class="a-size-base s-underline-text"[^>]*>([\d,]+)<',
                card,
                re.I,
            )

        img_m = re.search(r'class="s-image"[^>]+src="([^"]+)"', card, re.I)
        if not img_m:
            img_m = re.search(r'<img[^>]+class="[^"]*s-image[^"]*"[^>]+src="([^"]+)"', card, re.I)

        if apply_relevance and not is_relevant_dog_bed(title, product_url):
            continue

        seen.add(product_url)
        products.append(
            Product(
                title=title[:200],
                price=normalize_price(price_m.group(1)) if price_m else None,
                product_url=product_url,
                image_url=img_m.group(1) if img_m else "",
                avg_rating=normalize_rating(rating_m.group(1)) if rating_m else None,
                review_count=int(review_m.group(1).replace(",", "")) if review_m else 0,
                asin=asin,
            )
        )

    if products:
        return products, stats

    # path regex fallback for /dp/ASIN
    for asin in dict.fromkeys(re.findall(r"/dp/([A-Z0-9]{10})", html, re.I)):
        if len(products) >= limit:
            break
        product_url = canonical_amazon_url(asin)
        if product_url in seen:
            continue
        idx = html.lower().find(f"/dp/{asin.lower()}")
        window = html[max(0, idx - 200) : idx + 200] if idx >= 0 else ""
        if is_sponsored_url(window):
            continue
        if apply_relevance:
            continue
        seen.add(product_url)
        products.append(Product(title=asin, product_url=product_url, asin=asin.upper()))

    if products:
        stats["parse_source"] = "path_regex"
    return products, stats


def analyze_html(html: str) -> dict[str, Any]:
    low = html.lower()
    return {
        "body_len": len(html),
        "search_results": len(
            re.findall(r'data-component-type="s-search-result"', html, re.I)
        ),
        "asin_mentions": len(re.findall(r'data-asin="[A-Z0-9]{10}"', html, re.I)),
        "captcha": "captcha" in low or "robot check" in low,
        "title_tag": (
            re.search(r"<title>([^<]+)", html, re.I).group(1)[:100]
            if re.search(r"<title>", html, re.I)
            else None
        ),
    }


COMPARE_MODES: list[tuple[str, str, dict[str, bool]]] = [
    ("structured", "structured", {}),
    ("generic_ultra", "generic", {"render": False, "premium": False, "ultra_premium": True}),
    (
        "generic_ultra_render",
        "generic",
        {"render": True, "premium": False, "ultra_premium": True},
    ),
    ("generic_premium", "generic", {"render": False, "premium": True, "ultra_premium": False}),
]


def compare_all_modes(
    api_key: str,
    query: str = DEFAULT_QUERY,
    *,
    apply_relevance: bool = True,
) -> None:
    listing_url = SEARCH_URL.format(query=quote_plus(query))
    print(f"\n{'=' * 72}\nCompare modes — query={query!r}\n{'=' * 72}")

    for name, mode, flags in COMPARE_MODES:
        if mode == "structured":
            data, dbg = fetch_structured(api_key, query, quiet=True)
            if not data:
                err = (dbg.get("error") or "")[:120]
                print(f"  {name:22} HTTP {dbg.get('status_code')} — {err}")
                continue
            rel_p, _ = parse_structured_response(
                data, LIMIT, apply_relevance=apply_relevance
            )
            all_p, _ = parse_structured_response(data, LIMIT, apply_relevance=False)
            rated = sum(1 for p in rel_p if p.avg_rating and p.review_count)
            print(
                f"  {name:22} results={len(data.get('results') or []):>3} "
                f"ads={len(data.get('ads') or []):>2} "
                f"parsed={len(all_p):>2} relevant={len(rel_p):>2} rated={rated:>2}"
            )
            if rel_p:
                print(
                    f"    sample: {rel_p[0].title[:65]} | "
                    f"{rel_p[0].avg_rating} | {rel_p[0].review_count} rev"
                )
            continue

        html, dbg = fetch_via_scraperapi(listing_url, api_key, quiet=True, **flags)
        if not html:
            err = (dbg.get("error") or "")[:120]
            upgrade = " [NEEDS PREMIUM]" if dbg.get("needs_plan_upgrade") else ""
            print(f"  {name:22} HTTP {dbg.get('status_code')} — {err}{upgrade}")
            continue
        analysis = analyze_html(html)
        all_p, _ = parse_amazon_html(html, LIMIT, apply_relevance=False)
        rel_p, _ = parse_amazon_html(html, LIMIT, apply_relevance=apply_relevance)
        rated = sum(1 for p in rel_p if p.avg_rating and p.review_count)
        print(
            f"  {name:22} len={analysis['body_len']:>8} cards={analysis['search_results']:>3} "
            f"captcha={analysis['captcha']} "
            f"parsed={len(all_p):>2} relevant={len(rel_p):>2} rated={rated:>2}"
        )
        if rel_p:
            print(
                f"    sample: {rel_p[0].title[:65]} | "
                f"{rel_p[0].avg_rating} | {rel_p[0].review_count} rev"
            )


def scrape_amazon(
    api_key: str,
    *,
    query: str = DEFAULT_QUERY,
    limit: int = LIMIT,
    use_structured: bool = True,
    render: bool = False,
    premium: bool = False,
    ultra_premium: bool = False,
    apply_relevance: bool = True,
) -> tuple[list[Product], dict[str, Any]]:
    debug: dict[str, Any] = {"query": query}

    if use_structured:
        data, fetch_dbg = fetch_structured(api_key, query)
        debug["fetch"] = fetch_dbg
        if data:
            products, parse_stats = parse_structured_response(
                data, limit, apply_relevance=apply_relevance
            )
            debug["parse"] = parse_stats
            debug["product_count"] = len(products)
            if products:
                return products, debug

    listing_url = SEARCH_URL.format(query=quote_plus(query))
    html, fetch_dbg = fetch_via_scraperapi(
        listing_url,
        api_key,
        render=render,
        premium=premium,
        ultra_premium=ultra_premium,
    )
    debug["fetch_fallback"] = fetch_dbg
    if not html:
        return [], debug

    debug["html_analysis"] = analyze_html(html)
    products, parse_stats = parse_amazon_html(html, limit, apply_relevance=apply_relevance)
    debug["parse"] = parse_stats
    debug["product_count"] = len(products)
    return products, debug


def main() -> None:
    parser = argparse.ArgumentParser(description="Amazon dog beds via ScraperAPI tester")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--limit", type=int, default=LIMIT)
    parser.add_argument("--structured", action="store_true", default=True)
    parser.add_argument("--no-structured", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--premium", action="store_true")
    parser.add_argument("--ultra", action="store_true")
    parser.add_argument("--no-relevance", action="store_true")
    args = parser.parse_args()

    key = SCRAPERAPI_KEY
    if not key:
        print("[error] Set SCRAPERAPI_KEY in env or toptails/backend/.env")
        return

    if args.compare:
        compare_all_modes(key, args.query, apply_relevance=not args.no_relevance)
        return

    use_structured = not args.no_structured
    products, dbg = scrape_amazon(
        key,
        query=args.query,
        limit=args.limit,
        use_structured=use_structured,
        render=args.render,
        premium=args.premium,
        ultra_premium=args.ultra or not args.premium,
        apply_relevance=not args.no_relevance,
    )

    print("debug:", json.dumps({k: v for k, v in dbg.items() if "fetch" not in k}, default=str)[:800])

    if not products:
        print("\n[NO PRODUCTS] Try: python amazon_scraperapi.py --compare")
    else:
        print(f"\n[ok] {len(products)} products\n")
        for i, p in enumerate(products[:10], 1):
            print(f"{i}. {p.title[:70]}")
            print(f"   ${p.price} | {p.avg_rating} | {p.review_count} rev | {p.product_url}")

        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump([p.model_dump() for p in products], f, indent=2)
        print(f"\nSaved → {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
