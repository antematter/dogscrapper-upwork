"""
tractor.py — Test Tractor Supply dog-bed scrape + top-2 filter (rating/reviews).

Uses SearchDisplay via ScraperAPI (same path as production).

Run:
    cd testers && source venv/bin/activate
    export SCRAPERAPI_KEY=your-key   # or copy from toptails/backend/.env
    python tractor.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tractor_scraperapi import (
    LISTING_URL,
    Product,
    build_search_display_url,
    catalog_slug_to_search_term,
    extract_category_id_from_html,
    fetch_via_scraperapi,
    parse_search_display_html,
)

MIN_RATING = 4.5
MIN_REVIEWS = 10
LISTING_PAGES = 2
PAGE_SIZE = 48
TOP_N = 2


def qualifies(p: Product) -> bool:
    return (
        p.avg_rating is not None
        and p.avg_rating >= MIN_RATING
        and p.review_count >= MIN_REVIEWS
    )


def scrape_tractor_pages(api_key: str) -> tuple[list[Product], dict]:
    debug: dict = {}
    slug = LISTING_URL.rstrip("/").split("/")[-1]
    search_term = catalog_slug_to_search_term(slug)
    category_id = None

    shell_html, shell_debug = fetch_via_scraperapi(
        LISTING_URL,
        api_key,
        render=False,
        timeout=180,
    )
    debug["catalog_fetch"] = shell_debug
    if shell_html:
        category_id = extract_category_id_from_html(shell_html)

    all_products: list[Product] = []
    seen: set[str] = set()

    for page_ix in range(LISTING_PAGES):
        begin = page_ix * PAGE_SIZE
        sd_url = build_search_display_url(
            search_term,
            begin_index=begin,
            page_size=PAGE_SIZE,
            category_id=category_id,
        )
        html, sd_debug = fetch_via_scraperapi(
            sd_url, api_key, render=False, timeout=180
        )
        debug[f"page_{page_ix + 1}"] = sd_debug
        if not html:
            break
        batch = parse_search_display_html(html, limit=PAGE_SIZE)
        for p in batch:
            if p.product_url in seen:
                continue
            seen.add(p.product_url)
            all_products.append(p)
        if len(batch) < PAGE_SIZE:
            break

    return all_products, debug


def main() -> None:
    api_key = os.environ.get("SCRAPERAPI_KEY", "").strip()
    print("=" * 60)
    print("TopTails — Tractor Supply tester")
    print(f"Filter: rating >= {MIN_RATING}, reviews >= {MIN_REVIEWS}")
    print(f"Pages: {LISTING_PAGES} x {PAGE_SIZE}")
    print("=" * 60)

    if not api_key:
        print("[error] Set SCRAPERAPI_KEY")
        return

    products, debug = scrape_tractor_pages(api_key)
    if not products:
        print("[error] No products parsed.", debug)
        return

    with_rating = sum(1 for p in products if p.avg_rating is not None)
    with_reviews = sum(1 for p in products if p.review_count > 0)
    eligible = [p for p in products if qualifies(p)]
    eligible.sort(
        key=lambda p: (p.avg_rating or 0, p.review_count),
        reverse=True,
    )
    top = eligible[:TOP_N]

    print(
        f"\nScraped {len(products)} | ratings: {with_rating} | "
        f"with reviews: {with_reviews} | eligible: {len(eligible)}"
    )
    print("-" * 60)

    if not top:
        print("[warn] No products met criteria. Sample:\n")
        for p in products[:8]:
            print(f"  {p.title[:55]}")
            print(f"    rating={p.avg_rating} reviews={p.review_count}")
    else:
        for i, p in enumerate(top, 1):
            print(f"#{i}  {p.title}")
            print(f"    {p.avg_rating}★  ({p.review_count} reviews)")
            print(f"    {p.product_url[:85]}")
            print()

    out = Path(__file__).resolve().parent / "tractor_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "criteria": {
                    "min_rating": MIN_RATING,
                    "min_reviews": MIN_REVIEWS,
                    "pages": LISTING_PAGES,
                },
                "all": [p.model_dump() for p in products],
                "eligible": [p.model_dump() for p in eligible],
                "top": [p.model_dump() for p in top],
            },
            f,
            indent=2,
        )
    print(f"Saved → {out}")


if __name__ == "__main__":
    main()
