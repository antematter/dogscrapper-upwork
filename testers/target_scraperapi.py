# TopTails — Target dog beds via ScraperAPI (Google Colab / local)
#
# Target PLPs are CSR-heavy; Playwright often sees 0 hydrated cards. This tester
# probes whether ScraperAPI (standard vs premium vs render) returns usable HTML or
# embedded JSON (__NEXT_DATA__, redsky blobs, /p/.../-/A- paths).
#
# Install: pip install requests pydantic
# Credentials: SCRAPERAPI_KEY in env or assign below (never commit keys).

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

DEFAULT_CATEGORY_URL = "https://www.target.com/c/dog-beds-pet-supplies/-/N-5xt44"
DEFAULT_SEARCH_URL = "https://www.target.com/s?searchTerm=dog+bed"

COUNTRY_CODE = "us"
REQUEST_TIMEOUT = 180
OUTPUT_JSON = "target_scraperapi_results.json"
LIMIT = 24

_PRODUCT_PATH_RE = re.compile(r"/p/[a-z0-9-]+/-/A-\d+", re.IGNORECASE)
_SPONSORED_MARKERS = ("TCID=OGS", "AFID=google", "sponsored=1")

_BED_TITLE = re.compile(
    r"\b(dog bed|dog beds|pet bed|bolster bed|orthopedic bed|"
    r"donut bed|cuddler bed|nester bed|lounger bed|pillow bed|"
    r"elevated bed|cooling bed|heated bed|crate (?:mat|pad|bed)|"
    r"bed mat|bedding|\bbed\b|\bbeds\b|bolster|cot\b|daybed)\b",
    re.IGNORECASE,
)
_EXCLUDE_TITLE = re.compile(
    r"\b(dog food|cat food|treats?\b|dog toy|plush toy|litter box|"
    r"training pad|fish |aquarium|stroller|bowl\b|feeder\b)\b",
    re.IGNORECASE,
)
_CAT_NOT_DOG = re.compile(r"\b(cat|kitten|feline)\b", re.IGNORECASE)
_DOG_IN_TITLE = re.compile(r"\bdog\b", re.IGNORECASE)


class Product(BaseModel):
    source_site: str = "target"
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0


def normalize_price(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    m = re.search(r"\d+\.?\d*", str(raw).replace(",", ""))
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
    if _EXCLUDE_TITLE.search(t):
        return False
    if _CAT_NOT_DOG.search(t) and not _DOG_IN_TITLE.search(t):
        return False
    if _BED_TITLE.search(t):
        return True
    u = product_url or ""
    if re.search(r"(dog[-_]bed|dog[-_]beds|/beds/)", u, re.I):
        return True
    return False


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
        r = requests.get(
            "https://api.scraperapi.com/",
            params=params,
            timeout=timeout,
        )
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
        snippet = (r.text or "")[:500]
        dbg["error"] = snippet
        if r.status_code in (403, 500) and "premium" in snippet.lower():
            dbg["needs_plan_upgrade"] = True
        return None, dbg

    text = r.text or ""
    low = text.lower()
    if len(text) < 8_000:
        dbg["likely_shell_or_block"] = True
    if "access denied" in low or "robot or human" in low:
        dbg["likely_block"] = True
    if "captcha" in low and len(text) < 50_000:
        dbg["likely_block"] = True

    return text, dbg


def _title_from_path(path: str) -> str:
    slug = path.split("/p/", 1)[-1].split("/-/")[0]
    return slug.replace("-", " ").strip().title()


def _product_url_from_path(path: str) -> str:
    path = path.split("?")[0]
    return path if path.startswith("http") else f"https://www.target.com{path}"


def _is_sponsored(snippet: str) -> bool:
    return any(m in snippet for m in _SPONSORED_MARKERS)


def _walk_redsky_products(data: Any, out: list[tuple[str, str, Optional[float], str]]) -> None:
    """Collect (title, url, price, image) from nested Target/redsky JSON."""

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
                parent = o.get("parent") or o.get("parent_tcin")
                slug = (
                    o.get("product_description", {}).get("title", "")
                    if isinstance(o.get("product_description"), dict)
                    else ""
                )
                if isinstance(slug, str) and slug:
                    slug_part = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:80]
                    url = f"/p/{slug_part}/-/A-{tcin}" if slug_part else f"/p/-/A-{tcin}"
                else:
                    url = f"/p/-/A-{tcin}"

            price = None
            price_obj = o.get("price") or o.get("current_retail") or o.get("formatted_current_price")
            if isinstance(price_obj, dict):
                price = normalize_price(
                    price_obj.get("current_retail")
                    or price_obj.get("value")
                    or price_obj.get("formatted_current_price")
                )
            else:
                price = normalize_price(price_obj)

            img = ""
            for ik in ("primary_image_url", "image_url", "imageUrl", "image"):
                iv = o.get(ik)
                if isinstance(iv, str) and iv.startswith("http"):
                    img = iv
                    break
                if isinstance(iv, dict) and isinstance(iv.get("url"), str):
                    img = iv["url"]
                    break

            if isinstance(title, str) and title.strip() and isinstance(url, str) and "/A-" in url:
                full = _product_url_from_path(url)
                out.append((title.strip()[:200], full, price, img))

            for v in o.values():
                visit(v, depth + 1)
        elif isinstance(o, list):
            for x in o:
                visit(x, depth + 1)

    visit(data)


def parse_target_html(html: str, limit: int, *, apply_relevance: bool = True) -> tuple[list[Product], dict[str, Any]]:
    stats: dict[str, Any] = {}
    products: list[Product] = []
    seen: set[str] = set()

    # 1) __NEXT_DATA__
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        stats["next_data_len"] = len(m.group(1))
        try:
            nd = json.loads(m.group(1))
            rows: list[tuple[str, str, Optional[float], str]] = []
            _walk_redsky_products(nd, rows)
            stats["next_data_tiles"] = len(rows)
            for title, url, price, img in rows:
                if len(products) >= limit:
                    break
                if url in seen or _is_sponsored(url):
                    continue
                if apply_relevance and not is_relevant_dog_bed(title, url):
                    continue
                seen.add(url)
                products.append(
                    Product(title=title, price=price, product_url=url, image_url=img or "")
                )
            if products:
                stats["parse_source"] = "__NEXT_DATA__"
                return products, stats
        except json.JSONDecodeError as ex:
            stats["next_data_error"] = str(ex)

    # 2) Inline JSON blobs (redsky / product summaries)
    blob_hits = 0
    for blob in re.findall(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.S
    ):
        if len(blob) < 200 or "tcin" not in blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        rows = []
        _walk_redsky_products(data, rows)
        blob_hits += len(rows)
        for title, url, price, img in rows:
            if len(products) >= limit:
                break
            if url in seen or _is_sponsored(url):
                continue
            if apply_relevance and not is_relevant_dog_bed(title, url):
                continue
            seen.add(url)
            products.append(
                Product(title=title, price=price, product_url=url, image_url=img or "")
            )
    stats["json_blob_tiles"] = blob_hits
    if products:
        stats["parse_source"] = "application/json"
        return products, stats

    # 3) JSON-LD
    for blob in re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        items: list[Any] = []
        if obj.get("@type") == "ItemList":
            items = [e.get("item") or e for e in obj.get("itemListElement", [])]
        elif obj.get("@type") == "Product":
            items = [obj]
        for item in items:
            if len(products) >= limit:
                break
            if not isinstance(item, dict):
                continue
            url_p = item.get("url") or item.get("@id") or ""
            if not isinstance(url_p, str) or "/A-" not in url_p:
                continue
            url_p = _product_url_from_path(url_p.split("?")[0])
            if url_p in seen or _is_sponsored(url_p):
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

    # 4) data-test product-title (when present in SSR)
    for title, path in re.findall(
        r'data-test="product-title"[^>]*>([^<]+)</[^>]+>.*?href="(/p/[^"]+/A-\d+)"',
        html,
        re.S | re.I,
    ):
        if len(products) >= limit:
            break
        url_p = _product_url_from_path(path)
        if url_p in seen:
            continue
        t = title.strip()
        if apply_relevance and not is_relevant_dog_bed(t, url_p):
            continue
        seen.add(url_p)
        products.append(Product(title=t[:200], product_url=url_p))
    stats["data_test_titles"] = len(products)
    if products:
        stats["parse_source"] = "data-test"
        return products, stats

    # 5) Raw /p/.../-/A- paths (may include off-category sitewide links)
    all_paths = list(dict.fromkeys(_PRODUCT_PATH_RE.findall(html)))
    stats["raw_paths"] = len(all_paths)
    for path in all_paths:
        if len(products) >= limit:
            break
        idx = html.find(path)
        window = html[max(0, idx - 120) : idx + len(path) + 200] if idx >= 0 else path
        if _is_sponsored(window):
            continue
        url_p = _product_url_from_path(path)
        if url_p in seen:
            continue
        title = _title_from_path(path)
        if apply_relevance and not is_relevant_dog_bed(title, url_p):
            continue
        seen.add(url_p)
        products.append(Product(title=title, product_url=url_p))

    if products:
        stats["parse_source"] = "path_regex"
    return products, stats


def extract_redsky_key(html: str) -> Optional[str]:
    for pat in (
        r'"apiKey"\s*:\s*"([a-f0-9-]{8,})"',
        r'key=([a-f0-9-]{8,})',
        r'"key"\s*:\s*"([a-f0-9-]{8,})"',
    ):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return None


def build_redsky_search_url(key: str, *, keyword: str = "dog bed", count: int = 24) -> str:
    q = quote_plus(keyword)
    return (
        "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2"
        f"?key={key}&channel=WEB&count={count}&keyword={q}"
        "&offset=0&page=%2Fs&platform=desktop&visitor_id=0"
        "&pricing_store_id=3991&store_ids=3991"
    )


def analyze_html(html: str) -> dict[str, Any]:
    paths = list(dict.fromkeys(_PRODUCT_PATH_RE.findall(html)))
    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    return {
        "body_len": len(html),
        "product_paths": len(paths),
        "next_data_len": len(nd.group(1)) if nd else 0,
        "product_card_mentions": len(re.findall(r"ProductCard", html)),
        "product_title_tags": len(re.findall(r'data-test="product-title"', html, re.I)),
        "tcin_mentions": len(re.findall(r'"tcin"', html)),
        "title_tag": (
            re.search(r"<title>([^<]+)", html, re.I).group(1)[:100]
            if re.search(r"<title>", html, re.I)
            else None
        ),
    }


def scrape_target(
    api_key: str,
    *,
    listing_url: str = DEFAULT_CATEGORY_URL,
    limit: int = LIMIT,
    render: bool = False,
    premium: bool = False,
    ultra_premium: bool = False,
    apply_relevance: bool = True,
    try_redsky: bool = True,
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
    products, parse_stats = parse_target_html(html, limit, apply_relevance=apply_relevance)
    debug["parse"] = parse_stats

    if not products and try_redsky and not fetch_dbg.get("needs_plan_upgrade"):
        key = extract_redsky_key(html)
        if key:
            rs_url = build_redsky_search_url(key, keyword="dog bed", count=max(limit, 24))
            debug["redsky_url"] = rs_url[:120] + "…"
            rs_html, rs_dbg = fetch_via_scraperapi(
                rs_url,
                api_key,
                render=False,
                premium=premium,
                ultra_premium=ultra_premium,
                quiet=True,
            )
            debug["redsky_fetch"] = rs_dbg
            if rs_html:
                try:
                    data = json.loads(rs_html)
                    rows: list[tuple[str, str, Optional[float], str]] = []
                    _walk_redsky_products(data, rows)
                    debug["redsky_tiles"] = len(rows)
                    seen = {p.product_url for p in products}
                    for title, url, price, img in rows:
                        if len(products) >= limit:
                            break
                        if url in seen:
                            continue
                        if apply_relevance and not is_relevant_dog_bed(title, url):
                            continue
                        seen.add(url)
                        products.append(
                            Product(
                                title=title,
                                price=price,
                                product_url=url,
                                image_url=img or "",
                            )
                        )
                    if products:
                        debug["parse"]["parse_source"] = "redsky_api"
                except json.JSONDecodeError:
                    debug["redsky_json_error"] = True

    debug["product_count"] = len(products)
    return products, debug


COMPARE_MODES: list[tuple[str, dict[str, bool]]] = [
    ("standard", {"render": False, "premium": False, "ultra_premium": False}),
    ("render", {"render": True, "premium": False, "ultra_premium": False}),
    ("premium", {"render": False, "premium": True, "ultra_premium": False}),
    ("premium+render", {"render": True, "premium": True, "ultra_premium": False}),
    ("ultra", {"render": False, "premium": False, "ultra_premium": True}),
    ("ultra+render", {"render": True, "premium": False, "ultra_premium": True}),
]


def compare_all_modes(
    api_key: str,
    listing_url: str,
    *,
    apply_relevance: bool = True,
) -> None:
    print(f"\n{'=' * 72}\nCompare modes — {listing_url}\n{'=' * 72}")
    for name, flags in COMPARE_MODES:
        html, dbg = fetch_via_scraperapi(
            listing_url,
            api_key,
            quiet=True,
            **flags,
        )
        if not html:
            err = (dbg.get("error") or dbg.get("error", ""))[:120]
            upgrade = " [NEEDS PREMIUM]" if dbg.get("needs_plan_upgrade") else ""
            print(f"  {name:16} HTTP {dbg.get('status_code')} — {err}{upgrade}")
            continue
        analysis = analyze_html(html)
        all_p, _ = parse_target_html(html, LIMIT, apply_relevance=False)
        rel_p, _ = parse_target_html(html, LIMIT, apply_relevance=apply_relevance)
        print(
            f"  {name:16} len={analysis['body_len']:>8} paths={analysis['product_paths']:>3} "
            f"tcin={analysis['tcin_mentions']:>4} nd={analysis['next_data_len']:>6} "
            f"parsed={len(all_p):>2} relevant={len(rel_p):>2}"
        )
        if rel_p:
            print(f"    sample: {rel_p[0].title[:65]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Target dog beds via ScraperAPI tester")
    parser.add_argument(
        "--url",
        default=DEFAULT_CATEGORY_URL,
        help="Category or search URL",
    )
    parser.add_argument("--search", action="store_true", help="Use search URL instead of category")
    parser.add_argument("--compare", action="store_true", help="Run all ScraperAPI tier modes")
    parser.add_argument("--limit", type=int, default=LIMIT)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--premium", action="store_true")
    parser.add_argument("--ultra", action="store_true")
    parser.add_argument("--no-relevance", action="store_true", help="Skip dog-bed title filter")
    parser.add_argument("--no-redsky", action="store_true")
    args = parser.parse_args()

    key = SCRAPERAPI_KEY
    if not key:
        print("[error] Set SCRAPERAPI_KEY in env or toptails/backend/.env")
        return

    url = DEFAULT_SEARCH_URL if args.search else args.url

    if args.compare:
        compare_all_modes(key, url, apply_relevance=not args.no_relevance)
        compare_all_modes(key, DEFAULT_SEARCH_URL, apply_relevance=not args.no_relevance)
        return

    products, dbg = scrape_target(
        key,
        listing_url=url,
        limit=args.limit,
        render=args.render,
        premium=args.premium,
        ultra_premium=args.ultra,
        apply_relevance=not args.no_relevance,
        try_redsky=not args.no_redsky,
    )

    print("debug:", json.dumps({k: v for k, v in dbg.items() if k != "fetch"}, default=str)[:800])
    if dbg.get("fetch"):
        print("fetch:", {k: v for k, v in dbg["fetch"].items() if k != "error"})

    if dbg.get("fetch", {}).get("needs_plan_upgrade"):
        print(
            "\n[PLAN LIMIT] Target may require Premium/Ultra on ScraperAPI "
            "(same class of error as Chewy)."
        )
    elif dbg.get("fetch", {}).get("likely_shell_or_block"):
        print("\n[SHELL/BLOCK] Short HTML — try --render or --premium.")

    if not products:
        print(
            "\n[NO PRODUCTS] Try: python target_scraperapi.py --compare\n"
            "  or --render / --premium for CSR grid."
        )
    else:
        print(f"\n[ok] {len(products)} products (relevance filter on)\n")
        for i, p in enumerate(products[:10], 1):
            print(f"{i}. {p.title[:70]}")
            print(f"   ${p.price}  |  {p.product_url[:85]}")

        payload = [p.model_dump() for p in products]
        out_path = Path(OUTPUT_JSON)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    main()
