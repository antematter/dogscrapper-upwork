# ⚠️  SCRAPING RISK: Walmart uses aggressive bot detection including
# fingerprinting, CAPTCHA, and IP rate limiting. This scraper will likely
# fail in production without residential proxies or official API access.
# FUTURE: Replace with Walmart Affiliate API.
# scrape_status will be set to 'blocked' on failure — surfaces in API response.
import re
from playwright.async_api import async_playwright
from app.scrapers.base import BaseScraper, ProductRaw

SEARCH_URL = "https://www.walmart.com/search?q={query}"


class WalmartScraper(BaseScraper):
    SITE_NAME = "walmart"

    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        products = []
        url = SEARCH_URL.format(query=query.replace(" ", "+"))
        async with async_playwright() as pw:
            async with self._stealth_page(pw) as page:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self.human_delay()

                page_content = await page.content()
                if "captcha" in page_content.lower() or "robot check" in page_content.lower():
                    raise RuntimeError("Bot detection triggered on Walmart search page.")

                cards = await page.query_selector_all(
                    "[data-item-id], [class*='search-result-gridview-item'], [class*='ProductCard']"
                )

                for card in cards[:limit]:
                    try:
                        title_el = await card.query_selector(
                            "[class*='product-title'], [class*='title'], span[data-automation-id='product-title']"
                        )
                        price_el = await card.query_selector(
                            "[itemprop='price'], [class*='price-main']"
                        )
                        rating_el = await card.query_selector(
                            "[aria-label*='stars'], [class*='stars']"
                        )
                        review_el = await card.query_selector(
                            "[class*='review-count'], [aria-label*='reviews']"
                        )
                        link_el = await card.query_selector("a")
                        img_el = await card.query_selector("img")

                        title = await title_el.inner_text() if title_el else ""
                        if not title.strip():
                            continue

                        price_raw = await price_el.inner_text() if price_el else ""
                        rating_raw = await rating_el.get_attribute("aria-label") if rating_el else ""
                        if not rating_raw and rating_el:
                            rating_raw = await rating_el.inner_text()
                        review_raw = await review_el.inner_text() if review_el else "0"
                        href = await link_el.get_attribute("href") if link_el else None
                        product_url = (
                            f"https://www.walmart.com{href}"
                            if href and href.startswith("/")
                            else href
                        )
                        image_url = await img_el.get_attribute("src") if img_el else None

                        review_count = 0
                        m = re.search(r"\d+", review_raw.replace(",", ""))
                        if m:
                            review_count = int(m.group())

                        products.append(
                            ProductRaw(
                                source_site=self.SITE_NAME,
                                title=title.strip(),
                                price=self.normalize_price(price_raw),
                                avg_rating=self.normalize_rating(rating_raw or ""),
                                review_count=review_count,
                                product_url=product_url,
                                image_url=image_url,
                            )
                        )
                    except Exception:
                        continue

        return products
