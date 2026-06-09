"""
chewy_scraper.py — Scrapes top dog beds from Chewy, scores them, prints results.

Usage:
    python chewy_scraper.py

Setup (run once):
    python -m venv venv
    source venv/bin/activate          # Windows: venv\Scripts\activate
    pip install playwright playwright-stealth pydantic
    playwright install chromium
"""

import asyncio
import json
import math
import random
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from playwright.async_api import async_playwright

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
    print("[warn] playwright-stealth not installed — running without it")


# ── Data model ────────────────────────────────────────────────────────────────

class Product(BaseModel):
    title: str = ""
    price: Optional[float] = None
    product_url: str = ""
    image_url: str = ""
    avg_rating: Optional[float] = None
    review_count: int = 0
    five_star_ratio: float = 0.0
    verified_ratio: float = 0.8       # Chewy doesn't expose this; assume decent
    review_dates: list[str] = []
    trust_score: float = 0.0
    scrape_status: str = "ok"
    scrape_notes: str = ""


# ── Trust score formula ───────────────────────────────────────────────────────

def volume_weight(review_count: int) -> float:
    if review_count < 15:
        return 0.0
    return 1 / (1 + math.exp(-0.05 * (review_count - 50)))


def distribution_penalty(five_star_ratio: float) -> float:
    if five_star_ratio > 0.90:
        return 0.5
    if five_star_ratio > 0.80:
        return 0.75
    return 1.0


def verified_bonus(verified_ratio: float) -> float:
    return 1.0 + (0.3 * verified_ratio)


def velocity_penalty(review_dates: list[str]) -> float:
    """Flag if >30% of reviews landed in any 3-day window."""
    if len(review_dates) < 5:
        return 1.0
    try:
        dates = sorted(datetime.fromisoformat(d) for d in review_dates)
        total = len(dates)
        for i, start in enumerate(dates):
            window = sum(1 for d in dates[i:] if (d - start).days <= 3)
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


# ── Chewy scraper ─────────────────────────────────────────────────────────────

async def scrape_chewy(limit: int = 20) -> list[Product]:
    products: list[Product] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": random.randint(1200, 1440), "height": random.randint(700, 900)},
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

        print("[chewy] Loading search results…")
        await page.goto(
            "https://www.chewy.com/s?query=dog+beds&sort=4",  # sort=4 = top rated
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await asyncio.sleep(random.uniform(2.0, 3.5))

        # Scroll to trigger lazy loads
        for _ in range(3):
            await page.keyboard.press("End")
            await asyncio.sleep(random.uniform(0.8, 1.5))

        # Product cards
        cards = await page.query_selector_all("[data-testid='product-card']")
        if not cards:
            # Fallback selector
            cards = await page.query_selector_all(".kib-product-card")
        print(f"[chewy] Found {len(cards)} product cards")

        for card in cards[:limit]:
            try:
                p = Product()

                # Title
                title_el = await card.query_selector("[data-testid='product-card-title'], .kib-product-card__title")
                if title_el:
                    p.title = (await title_el.inner_text()).strip()

                # Price
                price_el = await card.query_selector("[data-testid='product-price'], .kib-product-card__price")
                if price_el:
                    raw = (await price_el.inner_text()).replace("$", "").replace(",", "").strip()
                    try:
                        p.price = float(raw.split()[0])
                    except ValueError:
                        pass

                # URL
                link_el = await card.query_selector("a")
                if link_el:
                    href = await link_el.get_attribute("href")
                    if href:
                        p.product_url = href if href.startswith("http") else f"https://www.chewy.com{href}"

                # Image
                img_el = await card.query_selector("img")
                if img_el:
                    p.image_url = await img_el.get_attribute("src") or ""

                # Rating
                rating_el = await card.query_selector("[aria-label*='out of 5'], [data-testid='product-rating']")
                if rating_el:
                    aria = await rating_el.get_attribute("aria-label") or ""
                    for part in aria.split():
                        try:
                            val = float(part)
                            if 0 < val <= 5:
                                p.avg_rating = val
                                break
                        except ValueError:
                            pass

                # Review count
                count_el = await card.query_selector("[data-testid='ratings-count'], .kib-rating__count")
                if count_el:
                    raw = (await count_el.inner_text()).replace(",", "").strip("() ")
                    try:
                        p.review_count = int("".join(filter(str.isdigit, raw)))
                    except ValueError:
                        pass

                if p.title:
                    products.append(p)

            except Exception as e:
                print(f"[chewy] Card parse error: {e}")
                continue

        # Fetch rating distribution from first few product detail pages
        for p in products[:8]:
            if not p.product_url:
                continue
            try:
                await asyncio.sleep(random.uniform(1.5, 2.5))
                detail = await context.new_page()
                if HAS_STEALTH:
                    await stealth_async(detail)
                await detail.goto(p.product_url, wait_until="domcontentloaded", timeout=20_000)

                # 5-star ratio from distribution bar
                bars = await detail.query_selector_all("[data-testid='rating-bar'], .kib-rating-histogram__bar")
                if bars and len(bars) >= 5:
                    try:
                        # bars are ordered 5→1 typically
                        texts = [await b.get_attribute("aria-label") or "" for b in bars[:5]]
                        counts = []
                        for t in texts:
                            nums = [int(x) for x in t.split() if x.isdigit()]
                            counts.append(nums[0] if nums else 0)
                        total = sum(counts) or 1
                        p.five_star_ratio = round(counts[0] / total, 3)
                    except Exception:
                        pass

                # Review dates (grab visible reviews)
                date_els = await detail.query_selector_all("[data-testid='review-date'], time")
                dates = []
                for el in date_els[:30]:
                    raw = (await el.inner_text()).strip()
                    try:
                        dt = datetime.strptime(raw, "%m/%d/%Y")
                        dates.append(dt.isoformat())
                    except ValueError:
                        pass
                if dates:
                    p.review_dates = dates

                await detail.close()
                print(f"[chewy] Detail scraped: {p.title[:50]}")

            except Exception as e:
                print(f"[chewy] Detail page error for {p.product_url}: {e}")

        await browser.close()

    return products


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("TopTails — Chewy Dog Beds Scraper")
    print("=" * 60)

    try:
        products = await scrape_chewy(limit=20)
    except Exception as e:
        print(f"[error] Scrape failed: {e}")
        return

    if not products:
        print("[error] No products scraped. Chewy may have changed their layout.")
        return

    # Score
    for p in products:
        p.trust_score = compute_trust_score(p)

    # Filter out unscoreable, rank, take top 2
    scoreable = [p for p in products if p.trust_score > 0]
    scoreable.sort(key=lambda x: x.trust_score, reverse=True)
    top2 = scoreable[:2]

    if not top2:
        print("[warn] No products met the minimum review threshold (15 reviews).")
        print(f"       Scraped {len(products)} products total but none qualified.")
        for p in products[:5]:
            print(f"  - {p.title[:60]} | reviews: {p.review_count} | rating: {p.avg_rating}")
        return

    print(f"\nScraped {len(products)} products → {len(scoreable)} scored → Top 2:\n")
    print("-" * 60)

    for i, p in enumerate(top2, 1):
        print(f"#{i}  {p.title}")
        print(f"    Price:        ${p.price}" if p.price else "    Price:        N/A")
        print(f"    Rating:       {p.avg_rating} ⭐  ({p.review_count} reviews)")
        print(f"    Trust Score:  {p.trust_score}")
        print(f"    5-star ratio: {p.five_star_ratio:.1%}")
        print(f"    URL:          {p.product_url}")
        print()

    # Also dump full JSON for inspection
    out_file = "chewy_results.json"
    with open(out_file, "w") as f:
        json.dump(
            [p.model_dump() for p in scoreable],
            f,
            indent=2,
        )
    print(f"Full scored results saved to: {out_file}")


if __name__ == "__main__":
    asyncio.run(main())