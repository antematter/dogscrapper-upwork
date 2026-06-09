import asyncio
import os
import random
import re
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from playwright_stealth import stealth_async
from pydantic import BaseModel

from app.scrapers.relevance import dedupe_by_product_url, filter_dog_bed_products

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


class ProductRaw(BaseModel):
    source_site: str
    category: str = "dog_beds"
    title: str = ""
    price: Optional[float] = None
    product_url: Optional[str] = None
    image_url: Optional[str] = None
    avg_rating: Optional[float] = None
    review_count: Optional[int] = None
    verified_ratio: Optional[float] = None
    five_star_ratio: Optional[float] = None
    rating_distribution: Optional[dict] = None
    review_dates: Optional[list] = None
    trust_score: Optional[float] = None
    scrape_status: str = "ok"
    scrape_notes: Optional[str] = None


def listing_pages_to_fetch() -> int:
    """PLP pages to load before dog-bed relevance filtering (env LISTING_PAGES, default 2)."""
    raw = (os.environ.get("LISTING_PAGES") or "2").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 3
    return max(1, min(10, n))


# No per-page cap while aggregating multi-page PLPs (filter applied in run()).
_FETCH_PAGE_CAP = 10_000


class BaseScraper(ABC):
    SITE_NAME: str
    CATEGORY: str = "dog_beds"

    @abstractmethod
    async def fetch_listings(
        self,
        query: str = "dog bed",
        limit: int = 20,
        *,
        listing_pages: int = 1,
    ) -> list[ProductRaw]:
        pass

    def blocked_result(self, notes: str) -> list[ProductRaw]:
        """Single row surfaced in UI as blocked (see scrape_notes in status banner)."""
        return [
            ProductRaw(
                source_site=self.SITE_NAME,
                category=self.CATEGORY,
                scrape_status="blocked",
                scrape_notes=(notes or "Scrape blocked or returned no products.")[:500],
            )
        ]

    async def run(self, query: str = "dog bed", limit: int = 20) -> list[ProductRaw]:
        self._empty_scrape_note: str | None = None
        try:
            pages = listing_pages_to_fetch()
            products = await self.fetch_listings(
                query=query, limit=limit, listing_pages=pages
            )
            if self.CATEGORY == "dog_beds":
                products = filter_dog_bed_products(products, query=query)
                products = dedupe_by_product_url(products)
            ok = [
                p
                for p in products
                if p.scrape_status == "ok" and (p.title or "").strip()
            ]
            if ok:
                return ok
            blocked = [p for p in products if p.scrape_status != "ok"]
            if blocked:
                return blocked
            note = self._empty_scrape_note or (
                "Zero products returned. Likely bot detection (DataDome/captcha), "
                "geo block, or the PLP never hydrated. See server logs and "
                f"debug_scrapes/{self.SITE_NAME}/."
            )
            return self.blocked_result(note)
        except Exception as e:
            return self.blocked_result(f"{type(e).__name__}: {e}")

    @asynccontextmanager
    async def _stealth_page(
        self,
        pw,
        extra_chromium_args: Optional[list[str]] = None,
    ) -> AsyncIterator:
        launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
        if extra_chromium_args:
            launch_args = launch_args + list(extra_chromium_args)
        browser = await pw.chromium.launch(
            headless=True,
            args=launch_args,
        )
        try:
            context = await browser.new_context(
                viewport={
                    "width": random.randint(1280, 1920),
                    "height": random.randint(768, 1080),
                },
                user_agent=random.choice(USER_AGENTS),
                locale="en-US",
            )
            page = await context.new_page()
            await stealth_async(page)
            yield page
        finally:
            await browser.close()

    @staticmethod
    async def soft_wait_for_listing_grid(page, selector: str, timeout_ms: int = 22_000) -> None:
        """Wait for listing markup without failing the scrape (CSR / slow third parties)."""
        try:
            await page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
        except Exception:
            pass

    @staticmethod
    async def human_delay():
        await asyncio.sleep(random.uniform(1.5, 3.5))

    @staticmethod
    def normalize_price(raw: str) -> Optional[float]:
        match = re.search(r"\d+\.?\d*", raw)
        if match:
            try:
                return float(match.group())
            except (ValueError, TypeError):
                return None
        return None

    @staticmethod
    def normalize_rating(raw: str) -> Optional[float]:
        """Extract a star rating in [0, 5]. Ignores review counts (e.g. 412) unless paired with a real star value."""
        if not raw or not raw.strip():
            return None
        in_range: list[float] = []
        for m in re.finditer(r"\d+\.?\d*", raw):
            try:
                v = float(m.group())
            except (ValueError, TypeError):
                continue
            if 0.0 <= v <= 5.0:
                in_range.append(v)
        if in_range:
            return in_range[0]
        return None
