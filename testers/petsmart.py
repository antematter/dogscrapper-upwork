"""
petsmart.py — Test PetSmart dog-bed PLP scrape: rating, review count, top-2 filter.

Criteria (MVP): avg_rating >= 4.5 AND review_count >= 10, from first 2 PLP pages.

Setup:
    cd testers && source venv/bin/activate
    pip install playwright playwright-stealth pydantic
    playwright install chromium

Run:
    python petsmart.py
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from playwright.async_api import async_playwright

try:
    from playwright_stealth import stealth_async

    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

SEARCH_URL = "https://www.petsmart.com/search?q={query}"
MIN_RATING = 4.5
MIN_REVIEWS = 10
LISTING_PAGES = 2
TOP_N = 2

_CARD_EXTRACT_JS = """(card) => {
    const titleEl = card.querySelector(
        'h2 a, .sparky-c-product-card__title a, .product-name, [class*="product-card__title"]'
    );
    const priceEl = card.querySelector(
        '[data-productprices], .price-sales, [class*="price-sales"]'
    );
    const linkEl = card.querySelector(
        'a.sparky-c-product-card__text-link, a.sparky-c-product-card__image-link, a[href]'
    );
    const imgEl = card.querySelector('img');

    let ratingRaw = '';
    let reviewRaw = '';

    const stars = card.querySelector(
        '.sparky-c-star-rating__icons, [class*="star-rating__icons"]'
    );
    if (stars) {
        ratingRaw = stars.getAttribute('aria-label') || '';
    }

    const reviewEl = card.querySelector(
        '.sparky-c-star-rating__rating-after, [class*="star-rating__rating-after"]'
    );
    if (reviewEl) {
        reviewRaw = reviewEl.getAttribute('aria-label') || (reviewEl.textContent || '').trim();
    }

    let priceText = '';
    const pricesAttr = card.getAttribute('data-productprices');
    if (pricesAttr) {
        const parts = pricesAttr.split(',').map(s => s.trim()).filter(Boolean);
        if (parts.length) priceText = '$' + parts[0];
    } else if (priceEl) {
        priceText = (priceEl.innerText || '').trim();
    }

    return {
        title: titleEl ? (titleEl.innerText || '').trim() : '',
        priceText,
        href: linkEl ? (linkEl.getAttribute('href') || linkEl.href || '') : '',
        imageUrl: imgEl ? (imgEl.src || imgEl.getAttribute('data-src') || '') : '',
        ratingRaw,
        reviewRaw,
    };
}"""


class Product(BaseModel):
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0


def normalize_rating(raw: str) -> Optional[float]:
    if not raw or not raw.strip():
        return None
    in_range: list[float] = []
    for m in re.finditer(r"\d+\.?\d*", raw):
        try:
            v = float(m.group())
        except ValueError:
            continue
        if 0.0 <= v <= 5.0:
            in_range.append(v)
    return in_range[0] if in_range else None


def normalize_price(raw: str) -> Optional[float]:
    m = re.search(r"\d+\.?\d*", raw or "")
    return float(m.group()) if m else None


def parse_review_count(raw: str) -> int:
    if not raw:
        return 0
    cleaned = raw.replace(",", "")
    m = re.search(r"\d+", cleaned)
    return int(m.group()) if m else 0


def listing_url(query: str, page: int) -> str:
    q = (query or "dog bed").strip().replace(" ", "+")
    base = SEARCH_URL.format(query=q)
    if page <= 1:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}page={page}"


def qualifies(p: Product) -> bool:
    return (
        p.avg_rating is not None
        and p.avg_rating >= MIN_RATING
        and p.review_count >= MIN_REVIEWS
    )


async def scrape_petsmart(
    query: str = "dog bed",
    listing_pages: int = LISTING_PAGES,
) -> list[Product]:
    products: list[Product] = []
    seen: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={
                "width": random.randint(1280, 1920),
                "height": random.randint(768, 1080),
            },
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = await context.new_page()
        if HAS_STEALTH:
            await stealth_async(page)

        for page_ix in range(1, listing_pages + 1):
            url = listing_url(query, page_ix)
            print(f"[petsmart] Page {page_ix}: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(random.uniform(2.0, 3.5))

            grid_sel = "[data-testid='product-card'], .sparky-c-product-card"
            try:
                await page.wait_for_selector(grid_sel, state="attached", timeout=25_000)
            except Exception:
                print(f"[petsmart] Warning: grid selector timeout on page {page_ix}")

            cards = await page.query_selector_all(grid_sel)
            print(f"[petsmart] Found {len(cards)} cards on page {page_ix}")

            for card in cards:
                try:
                    raw = await card.evaluate(_CARD_EXTRACT_JS)
                    if not raw or not raw.get("title"):
                        continue

                    href = raw.get("href") or ""
                    if href.startswith("/"):
                        product_url = f"https://www.petsmart.com{href}"
                    else:
                        product_url = href
                    if not product_url:
                        continue
                    norm = product_url.split("?")[0].rstrip("/")
                    if norm in seen:
                        continue
                    seen.add(norm)

                    rating_raw = raw.get("ratingRaw") or ""
                    review_raw = raw.get("reviewRaw") or "0"
                    p = Product(
                        title=raw["title"],
                        price=normalize_price(raw.get("priceText") or ""),
                        product_url=product_url,
                        image_url=raw.get("imageUrl") or "",
                        avg_rating=normalize_rating(rating_raw),
                        review_count=parse_review_count(review_raw),
                    )
                    products.append(p)
                except Exception as e:
                    print(f"[petsmart] card error: {e}")

        await browser.close()

    return products


async def main() -> None:
    print("=" * 60)
    print("TopTails — PetSmart tester (rating + reviews + top 2)")
    print(f"Filter: rating >= {MIN_RATING}, reviews >= {MIN_REVIEWS}")
    print(f"Pages: {LISTING_PAGES}")
    print("=" * 60)

    try:
        products = await scrape_petsmart()
    except Exception as e:
        print(f"[error] Scrape failed: {type(e).__name__}: {e}")
        return

    if not products:
        print("[error] No products scraped.")
        return

    with_rating = sum(1 for p in products if p.avg_rating is not None)
    with_reviews = sum(1 for p in products if p.review_count > 0)
    print(
        f"\nScraped {len(products)} products | "
        f"with rating: {with_rating} | with reviews: {with_reviews}"
    )

    eligible = [p for p in products if qualifies(p)]
    eligible.sort(
        key=lambda p: (p.avg_rating or 0, p.review_count),
        reverse=True,
    )
    top = eligible[:TOP_N]

    print(f"\nEligible (>= {MIN_RATING} stars, >= {MIN_REVIEWS} reviews): {len(eligible)}")
    print("-" * 60)

    if not top:
        print("[warn] No products met criteria. Sample of first 8 scraped:\n")
        for p in products[:8]:
            print(
                f"  - {p.title[:55]}…"
                if len(p.title) > 55
                else f"  - {p.title}"
            )
            print(
                f"    rating={p.avg_rating} reviews={p.review_count} "
                f"price={p.price}"
            )
    else:
        for i, p in enumerate(top, 1):
            print(f"#{i}  {p.title}")
            print(f"    Rating:  {p.avg_rating} ⭐  ({p.review_count} reviews)")
            print(f"    Price:   ${p.price}" if p.price else "    Price:   N/A")
            print(f"    URL:     {p.product_url}")
            print()

    out = Path(__file__).resolve().parent / "petsmart_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "criteria": {
                    "min_rating": MIN_RATING,
                    "min_reviews": MIN_REVIEWS,
                    "listing_pages": LISTING_PAGES,
                },
                "all": [p.model_dump() for p in products],
                "eligible": [p.model_dump() for p in eligible],
                "top": [p.model_dump() for p in top],
            },
            f,
            indent=2,
        )
    print(f"Saved: {out}")


if __name__ == "__main__":
    asyncio.run(main())
