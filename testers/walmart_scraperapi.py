# TopTails — Walmart dog beds via ScraperAPI (Google Colab / local)
#
# Walmart search PLPs embed product data in __NEXT_DATA__ itemStacks when not blocked.
# This tester probes ScraperAPI tiers and parses search results.
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

DEFAULT_SEARCH_URL = "https://www.walmart.com/search?q=dog+bed"
COUNTRY_CODE = "us"
REQUEST_TIMEOUT = 180
OUTPUT_JSON = "walmart_scraperapi_results.json"
LIMIT = 24

_PRODUCT_PATH_RE = re.compile(r"/ip/[a-z0-9-]+/\d+", re.IGNORECASE)
_SPONSORED_MARKERS = ("sponsored=1", "spQs=", "wmlspartner")


class Product(BaseModel):
    source_site: str = "walmart"
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0


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
            f"[scraperapi] GET {target_url[:90]}… "
            f"render={render} premium={premium} ultra={ultra_premium}"
        )

    try:
        r = requests.get("https://api.scraperapi.com/", params=params, timeout=timeout)
    except requests.RequestException as e:
        return None, {"error": str(e), "url": target_url}

    dbg: dict[str, Any] = {
        "url": target_url,
        "status_code": r.status_code,
        "body_len": len(r.content or b""),
        "render": render,
        "premium": premium,
        "ultra_premium": ultra_premium,
    }

    if not quiet:
        print(f"[scraperapi] HTTP {r.status_code} body_len={dbg['body_len']}")

    if r.status_code != 200:
        dbg["error"] = (r.text or "")[:500]
        if r.status_code in (403, 500) and "premium" in (r.text or "").lower():
            dbg["needs_plan_upgrade"] = True
        return None, dbg

    text = r.text or ""
    low = text.lower()
    if len(text) < 8_000:
        dbg["likely_shell_or_block"] = True
    if "captcha" in low or "robot check" in low:
        dbg["likely_block"] = True

    return text, dbg


def _product_url_from_path(path: str) -> str:
    path = path.split("?")[0].split("#")[0]
    if path.startswith("http"):
        return path
    return f"https://www.walmart.com{path}"


def _is_sponsored(snippet: str) -> bool:
    return any(m in (snippet or "") for m in _SPONSORED_MARKERS)


def _item_stacks_from_next_data(nd: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        stacks = (
            nd.get("props", {})
            .get("pageProps", {})
            .get("initialData", {})
            .get("searchResult", {})
            .get("itemStacks", [])
        )
    except (AttributeError, TypeError):
        return []
    if not isinstance(stacks, list):
        return []
    items: list[dict[str, Any]] = []
    for stack in stacks:
        if not isinstance(stack, dict):
            continue
        for item in stack.get("items") or []:
            if isinstance(item, dict):
                items.append(item)
    return items


def _tile_from_item(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    if item.get("isSponsoredFlag"):
        return None
    typename = str(item.get("__typename") or "")
    us_item_id = item.get("usItemId")
    if typename and typename not in ("Product", "SearchProduct") and not us_item_id:
        return None

    title = (item.get("name") or "").strip()
    if not title:
        return None

    url = item.get("canonicalUrl") or ""
    if not url and us_item_id:
        url = f"/ip/-/{us_item_id}"
    if not url or "/ip/" not in str(url):
        return None
    product_url = _product_url_from_path(str(url))

    price_info = item.get("priceInfo") or {}
    price = None
    if isinstance(price_info, dict):
        cur = price_info.get("currentPrice") or {}
        if isinstance(cur, dict):
            price = normalize_price(cur.get("price"))
        if price is None:
            price = normalize_price(price_info.get("linePrice"))

    image_info = item.get("imageInfo") or {}
    image = ""
    if isinstance(image_info, dict):
        image = str(image_info.get("thumbnailUrl") or image_info.get("imageUrl") or "")

    variant_group = (
        item.get("catalogProductId")
        or item.get("id")
        or item.get("usItemId")
    )

    return {
        "title": title[:200],
        "href": product_url,
        "price": price,
        "imageUrl": image,
        "avg_rating": normalize_rating(item.get("averageRating")),
        "review_count": int(item.get("numberOfReviews") or 0),
        "variant_group_id": str(variant_group) if variant_group else None,
    }


def parse_walmart_html(
    html: str, limit: int, *, apply_relevance: bool = True
) -> tuple[list[Product], dict[str, Any]]:
    stats: dict[str, Any] = {}
    products: list[Product] = []
    seen: set[str] = set()

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        stats["next_data_len"] = len(m.group(1))
        try:
            nd = json.loads(m.group(1))
            items = _item_stacks_from_next_data(nd)
            stats["item_stack_items"] = len(items)
            for item in items:
                if len(products) >= limit:
                    break
                raw = _tile_from_item(item)
                if not raw:
                    continue
                url = raw["href"]
                if url in seen or _is_sponsored(url):
                    continue
                if apply_relevance and not is_relevant_dog_bed(raw["title"], url):
                    continue
                seen.add(url)
                products.append(
                    Product(
                        title=raw["title"],
                        price=raw["price"],
                        product_url=url,
                        image_url=raw.get("imageUrl") or "",
                        avg_rating=raw.get("avg_rating"),
                        review_count=raw.get("review_count") or 0,
                    )
                )
            if products:
                stats["parse_source"] = "__NEXT_DATA__"
                return products, stats
        except json.JSONDecodeError as ex:
            stats["next_data_error"] = str(ex)

    for blob in re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        items_list: list[Any] = []
        if isinstance(obj, dict):
            if obj.get("@type") == "ItemList":
                items_list = [e.get("item") or e for e in obj.get("itemListElement", [])]
            elif obj.get("@type") == "Product":
                items_list = [obj]
        for item in items_list:
            if len(products) >= limit or not isinstance(item, dict):
                continue
            url_p = item.get("url") or item.get("@id") or ""
            if not isinstance(url_p, str) or "/ip/" not in url_p:
                continue
            url_p = _product_url_from_path(url_p.split("?")[0])
            if url_p in seen:
                continue
            title = (item.get("name") or "")[:200]
            if apply_relevance and not is_relevant_dog_bed(title, url_p):
                continue
            seen.add(url_p)
            offer = item.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            agg = item.get("aggregateRating") or {}
            products.append(
                Product(
                    title=title,
                    price=normalize_price(offer.get("price") if isinstance(offer, dict) else None),
                    avg_rating=normalize_rating(agg.get("ratingValue")),
                    review_count=int(agg.get("reviewCount") or 0),
                    product_url=url_p,
                    image_url=item.get("image") if isinstance(item.get("image"), str) else "",
                )
            )
    if products:
        stats["parse_source"] = "json-ld"
        return products, stats

    for card in re.split(r'data-item-id="', html)[1:]:
        if len(products) >= limit:
            break
        title_m = re.search(
            r'data-automation-id="product-title"[^>]*>([^<]{5,200})<',
            card,
            re.I,
        )
        link_m = re.search(r'href="(/ip/[^"]+)"', card)
        if not title_m or not link_m:
            continue
        url_p = _product_url_from_path(link_m.group(1))
        if url_p in seen:
            continue
        title = title_m.group(1).strip()
        rating_m = re.search(
            r'aria-label="([\d.]+) out of 5[^"]*".*?aria-label="([\d,]+) reviews"',
            card,
            re.I | re.S,
        )
        prices = re.findall(r'\$[\d.]+', card)
        if apply_relevance and not is_relevant_dog_bed(title, url_p):
            continue
        seen.add(url_p)
        products.append(
            Product(
                title=title[:200],
                price=normalize_price(prices[0]) if prices else None,
                avg_rating=normalize_rating(rating_m.group(1)) if rating_m else None,
                review_count=int(rating_m.group(2).replace(",", "")) if rating_m else 0,
                product_url=url_p,
            )
        )
    if products:
        stats["parse_source"] = "product_cards"
        return products, stats

    paths = list(dict.fromkeys(_PRODUCT_PATH_RE.findall(html)))
    stats["raw_paths"] = len(paths)
    for path in paths:
        if len(products) >= limit:
            break
        url_p = _product_url_from_path(path)
        if url_p in seen:
            continue
        slug = path.split("/ip/")[-1].rsplit("/", 1)[0].replace("-", " ").title()
        if apply_relevance and not is_relevant_dog_bed(slug, url_p):
            continue
        seen.add(url_p)
        products.append(Product(title=slug[:200], product_url=url_p))

    if products:
        stats["parse_source"] = "path_regex"
    return products, stats


def analyze_html(html: str) -> dict[str, Any]:
    paths = list(dict.fromkeys(_PRODUCT_PATH_RE.findall(html)))
    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    item_count = 0
    if nd:
        try:
            item_count = len(_item_stacks_from_next_data(json.loads(nd.group(1))))
        except json.JSONDecodeError:
            pass
    low = html.lower()
    return {
        "body_len": len(html),
        "product_paths": len(paths),
        "next_data_len": len(nd.group(1)) if nd else 0,
        "item_stack_items": item_count,
        "usItemId_mentions": len(re.findall(r'"usItemId"', html)),
        "captcha": "captcha" in low or "robot check" in low,
        "title_tag": (
            re.search(r"<title>([^<]+)", html, re.I).group(1)[:100]
            if re.search(r"<title>", html, re.I)
            else None
        ),
    }


COMPARE_MODES: list[tuple[str, dict[str, bool]]] = [
    ("standard", {"render": False, "premium": False, "ultra_premium": False}),
    ("render", {"render": True, "premium": False, "ultra_premium": False}),
    ("premium", {"render": False, "premium": True, "ultra_premium": False}),
    ("premium+render", {"render": True, "premium": True, "ultra_premium": False}),
    ("ultra", {"render": False, "premium": False, "ultra_premium": True}),
    ("ultra+render", {"render": True, "premium": False, "ultra_premium": True}),
]


def compare_all_modes(api_key: str, listing_url: str, *, apply_relevance: bool = True) -> None:
    print(f"\n{'=' * 72}\nCompare modes — {listing_url}\n{'=' * 72}")
    for name, flags in COMPARE_MODES:
        html, dbg = fetch_via_scraperapi(listing_url, api_key, quiet=True, **flags)
        if not html:
            err = (dbg.get("error") or "")[:120]
            upgrade = " [NEEDS PREMIUM]" if dbg.get("needs_plan_upgrade") else ""
            print(f"  {name:16} HTTP {dbg.get('status_code')} — {err}{upgrade}")
            continue
        analysis = analyze_html(html)
        all_p, _ = parse_walmart_html(html, LIMIT, apply_relevance=False)
        rel_p, _ = parse_walmart_html(html, LIMIT, apply_relevance=apply_relevance)
        rated = sum(1 for p in rel_p if p.avg_rating and p.review_count)
        print(
            f"  {name:16} len={analysis['body_len']:>8} items={analysis['item_stack_items']:>3} "
            f"paths={analysis['product_paths']:>3} captcha={analysis['captcha']} "
            f"parsed={len(all_p):>2} relevant={len(rel_p):>2} rated={rated:>2}"
        )
        if rel_p:
            print(f"    sample: {rel_p[0].title[:65]} | {rel_p[0].avg_rating} | {rel_p[0].review_count} rev")


def scrape_walmart(
    api_key: str,
    *,
    listing_url: str = DEFAULT_SEARCH_URL,
    limit: int = LIMIT,
    render: bool = False,
    premium: bool = False,
    ultra_premium: bool = False,
    apply_relevance: bool = True,
) -> tuple[list[Product], dict[str, Any]]:
    debug: dict[str, Any] = {"listing_url": listing_url}
    html, fetch_dbg = fetch_via_scraperapi(
        listing_url,
        api_key,
        render=render,
        premium=premium,
        ultra_premium=ultra_premium,
    )
    debug["fetch"] = fetch_dbg
    if not html:
        return [], debug

    debug["html_analysis"] = analyze_html(html)
    products, parse_stats = parse_walmart_html(html, limit, apply_relevance=apply_relevance)
    debug["parse"] = parse_stats
    debug["product_count"] = len(products)
    return products, debug


def main() -> None:
    parser = argparse.ArgumentParser(description="Walmart dog beds via ScraperAPI tester")
    parser.add_argument("--url", default=DEFAULT_SEARCH_URL)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--limit", type=int, default=LIMIT)
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
        compare_all_modes(key, args.url, apply_relevance=not args.no_relevance)
        return

    products, dbg = scrape_walmart(
        key,
        listing_url=args.url,
        limit=args.limit,
        render=args.render,
        premium=args.premium,
        ultra_premium=args.ultra,
        apply_relevance=not args.no_relevance,
    )

    print("debug:", json.dumps({k: v for k, v in dbg.items() if k != "fetch"}, default=str)[:800])
    if dbg.get("fetch"):
        print("fetch:", {k: v for k, v in dbg["fetch"].items() if k != "error"})

    if not products:
        print("\n[NO PRODUCTS] Try: python walmart_scraperapi.py --compare")
        print("  or --ultra --render for CSR grid.")
    else:
        print(f"\n[ok] {len(products)} products\n")
        for i, p in enumerate(products[:10], 1):
            print(f"{i}. {p.title[:70]}")
            print(f"   ${p.price} | {p.avg_rating} | {p.review_count} rev | {p.product_url[:80]}")

        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump([p.model_dump() for p in products], f, indent=2)
        print(f"\nSaved → {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
