# ⚠️  SCRAPING RISK: Amazon uses aggressive bot detection including
# fingerprinting, CAPTCHA, and IP rate limiting. This scraper will likely
# fail in production without residential proxies or official API access.
# FUTURE: Replace with Amazon Product Advertising API.
# scrape_status will be set to 'blocked' on failure — surfaces in API response.
import re
from playwright.async_api import async_playwright
from app.scrapers.base import BaseScraper, ProductRaw

SEARCH_URL = "https://www.amazon.com/s?k={query}"


class AmazonScraper(BaseScraper):
    SITE_NAME = "amazon"

    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        products = []
        url = SEARCH_URL.format(query=query.replace(" ", "+"))
        async with async_playwright() as pw:
            async with self._stealth_page(pw) as page:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self.human_delay()

                # Detect CAPTCHA / bot wall
                page_content = await page.content()
                if "captcha" in page_content.lower() or "robot" in page_content.lower():
                    raise RuntimeError(
                        "CAPTCHA encountered on Amazon search results page. "
                        "Amazon bot detection triggered."
                    )

                cards = await page.query_selector_all(
                    "[data-component-type='s-search-result']"
                )

                for card in cards[:limit]:
                    try:
                        title_el = await card.query_selector(
                            "h2 a span, [data-cy='title-recipe'] h2"
                        )
                        price_el = await card.query_selector(".a-price .a-offscreen")
                        rating_el = await card.query_selector("[aria-label*='stars']")
                        review_el = await card.query_selector(
                            "[aria-label*='ratings'], .a-size-base.s-underline-text"
                        )
                        link_el = await card.query_selector("h2 a")
                        img_el = await card.query_selector(".s-image")

                        title = await title_el.inner_text() if title_el else ""
                        if not title.strip():
                            continue

                        price_raw = await price_el.inner_text() if price_el else ""
                        rating_raw = await rating_el.get_attribute("aria-label") if rating_el else ""
                        review_raw = await review_el.get_attribute("aria-label") if review_el else ""
                        if not review_raw and review_el:
                            review_raw = await review_el.inner_text()
                        href = await link_el.get_attribute("href") if link_el else None
                        product_url = (
                            f"https://www.amazon.com{href}"
                            if href and href.startswith("/")
                            else href
                        )
                        image_url = await img_el.get_attribute("src") if img_el else None

                        review_count = 0
                        m = re.search(r"[\d,]+", review_raw or "")
                        if m:
                            review_count = int(m.group().replace(",", ""))

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
