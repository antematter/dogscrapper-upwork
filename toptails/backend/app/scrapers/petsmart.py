import re

from playwright.async_api import Page, async_playwright

from app.scrapers.base import BaseScraper, ProductRaw, _FETCH_PAGE_CAP

SEARCH_URL = "https://www.petsmart.com/search?q={query}"

_GRID_SELECTOR = "[data-testid='product-card'], .sparky-c-product-card"

_CARD_EXTRACT_JS = """(card) => {
    const titleEl = card.querySelector(
        'h2 a, .sparky-c-product-card__title a, .product-name, [class*="product-card__title"]'
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


def _listing_url(query: str, page: int = 1) -> str:
    q = (query or "").strip() or "dog bed"
    base = SEARCH_URL.format(query=q.replace(" ", "+"))
    if page <= 1:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}page={page}"


def _parse_review_count(raw: str) -> int:
    if not raw:
        return 0
    m = re.search(r"\d+", raw.replace(",", ""))
    return int(m.group()) if m else 0


class PetsmartScraper(BaseScraper):
    SITE_NAME = "petsmart"

    async def _parse_cards_on_page(
        self, page: Page, seen_urls: set[str]
    ) -> list[ProductRaw]:
        await self.soft_wait_for_listing_grid(page, _GRID_SELECTOR, timeout_ms=25_000)

        cards = await page.query_selector_all(_GRID_SELECTOR)

        products: list[ProductRaw] = []
        for card in cards[:_FETCH_PAGE_CAP]:
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
                if norm in seen_urls:
                    continue
                seen_urls.add(norm)

                rating_raw = raw.get("ratingRaw") or ""
                review_raw = raw.get("reviewRaw") or "0"

                products.append(
                    ProductRaw(
                        source_site=self.SITE_NAME,
                        title=raw["title"],
                        price=self.normalize_price(raw.get("priceText") or ""),
                        avg_rating=self.normalize_rating(rating_raw),
                        review_count=_parse_review_count(review_raw),
                        product_url=product_url,
                        image_url=raw.get("imageUrl"),
                    )
                )
            except Exception:
                continue

        return products

    async def fetch_listings(
        self,
        query: str = "dog bed",
        limit: int = 20,
        *,
        listing_pages: int = 1,
    ) -> list[ProductRaw]:
        products: list[ProductRaw] = []
        seen_urls: set[str] = set()
        pages = max(1, listing_pages)

        async with async_playwright() as pw:
            async with self._stealth_page(pw) as page:
                for page_ix in range(1, pages + 1):
                    url = _listing_url(query, page_ix)
                    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    await self.human_delay()
                    batch = await self._parse_cards_on_page(page, seen_urls)
                    products.extend(batch)
                    if not batch:
                        break

        return products
