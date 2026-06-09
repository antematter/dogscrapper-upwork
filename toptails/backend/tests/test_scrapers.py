import pytest
from app.scrapers.base import ProductRaw, BaseScraper


class ConcreteSuccessScraper(BaseScraper):
    SITE_NAME = "test_site"

    async def fetch_listings(self, query="dog bed", limit=20, *, listing_pages=1):
        return [
            ProductRaw(
                source_site=self.SITE_NAME,
                title="Test Orthopedic Dog Bed",
                product_url="https://example.com/dog/beds/test",
                price=29.99,
                avg_rating=4.5,
                review_count=100,
            )
        ]


class ConcreteFailScraper(BaseScraper):
    SITE_NAME = "fail_site"

    async def fetch_listings(self, query="dog bed", limit=20, *, listing_pages=1):
        raise ConnectionError("CAPTCHA detected")


@pytest.mark.asyncio
async def test_run_returns_products_on_success():
    results = await ConcreteSuccessScraper().run()
    assert len(results) == 1
    assert "Dog Bed" in results[0].title
    assert results[0].scrape_status == "ok"


@pytest.mark.asyncio
async def test_run_returns_blocked_on_exception():
    results = await ConcreteFailScraper().run()
    assert len(results) == 1
    assert results[0].scrape_status == "blocked"
    assert "CAPTCHA" in results[0].scrape_notes
    assert results[0].source_site == "fail_site"


class ConcreteEmptyScraper(BaseScraper):
    SITE_NAME = "empty_site"

    async def fetch_listings(self, query="dog bed", limit=20, *, listing_pages=1):
        self._empty_scrape_note = "Test: zero products from PLP"
        return []


@pytest.mark.asyncio
async def test_run_returns_blocked_on_empty_list():
    results = await ConcreteEmptyScraper().run()
    assert len(results) == 1
    assert results[0].scrape_status == "blocked"
    assert "zero products" in (results[0].scrape_notes or "").lower()
    assert results[0].source_site == "empty_site"


def test_normalize_price():
    assert BaseScraper.normalize_price("$49.99") == 49.99
    assert BaseScraper.normalize_price("49") == 49.0
    assert BaseScraper.normalize_price("N/A") is None
    assert BaseScraper.normalize_price("$49.99 - $79.99") == 49.99
    assert BaseScraper.normalize_price("") is None


def test_normalize_rating():
    assert BaseScraper.normalize_rating("4.5 out of 5") == 4.5
    assert BaseScraper.normalize_rating("no rating") is None
    assert BaseScraper.normalize_rating("412") is None
    assert BaseScraper.normalize_rating("412 reviews") is None
    assert BaseScraper.normalize_rating("Rating 4.2 (412)") == 4.2


def test_dog_bed_relevance_filter():
    from app.scrapers.base import ProductRaw
    from app.scrapers.relevance import filter_dog_bed_products, is_relevant_dog_bed

    assert is_relevant_dog_bed("Top Paw Orthopedic Couch Dog Bed")
    assert not is_relevant_dog_bed("Milk-Bone Dog Treat Biscuits")
    assert not is_relevant_dog_bed("Bonkers Purrpops Cat Treats")
    assert is_relevant_dog_bed(
        "Cooling Mats Dog Bolster Bed",
        "https://www.target.com/p/cooling-mats-dog-bolster-bed/-/A-1",
    )

    rows = filter_dog_bed_products(
        [
            ProductRaw(source_site="x", title="Dog Bolster Bed", scrape_status="ok"),
            ProductRaw(source_site="x", title="Dog Toy Plush", scrape_status="ok"),
        ]
    )
    assert len(rows) == 1
    assert rows[0].title.startswith("Dog Bolster")


def test_petco_tile_price_from_next_data_keys():
    from app.scrapers.petco import _tile_price_from_dict

    assert _tile_price_from_dict({"rdprice": 20.89, "listprice": 21.99}) == "20.89"
    assert _tile_price_from_dict({"offerprice": 42.99}) == "42.99"
    assert _tile_price_from_dict({"itemname": "Bed"}) is None


def test_tractor_pdp_price_from_next_data_snippet():
    from app.scrapers.tractor_supply import TractorSupplyScraper, _price_from_pdp_html

    html = (
        '<script id="__NEXT_DATA__">'
        '{"props":{"pageProps":{"pageProps":{"pdpData":{"productDetails":'
        '{"productDetailsById":{"catalogEntryView":[{"itemPricing":{"maxOfferPrice":44.99}}]}}}}}}}'
        "</script>"
    )
    scraper = TractorSupplyScraper()
    assert _price_from_pdp_html(html, scraper) == 44.99


def test_petco_tiles_from_next_data_json():
    from app.scrapers.petco import tiles_from_next_data_for_tests

    nested = {
        "browse": [
            {"itemName": "", "skipped": True},
            {
                "tiles": [
                    {
                        "itemname": "Test Calming Dog Bed",
                        "url": "/shop/en/petcostore/product/test-calming-bed-3939393",
                        "image_url": "https://assets.petco.com/test.png",
                    }
                ]
            },
        ]
    }

    tuples = tiles_from_next_data_for_tests(nested)
    assert len(tuples) == 1
    title, url, img = tuples[0]
    assert title.startswith("Test Calming")
    assert url.endswith("/test-calming-bed-3939393")
    assert img and "assets.petco.com" in img


def test_petco_short_product_url_tiles():
    from app.scrapers.petco import tiles_from_next_data_for_tests

    nested = {
        "browse": [
            {
                "tiles": [
                    {
                        "itemname": "Short URL Dog Bed",
                        "url": "/product/short-url-dog-bed-1234567",
                        "image_url": "https://assets.petco.com/bed.png",
                        "rdprice": 29.99,
                        "AverageRating": 4.5,
                        "TotalReviewCount": 42,
                    }
                ]
            }
        ]
    }
    tuples = tiles_from_next_data_for_tests(nested)
    assert len(tuples) == 1
    title, url, img = tuples[0]
    assert title == "Short URL Dog Bed"
    assert url == "https://www.petco.com/product/short-url-dog-bed-1234567"
    assert "assets.petco.com" in (img or "")


def test_tractor_supply_parse_search_display_html():
    from app.scrapers.tractor_supply import (
        TractorSupplyScraper,
        _parse_search_display_html,
    )

    html = (
        '<a id="catalogEntry_img42" class="x" '
        'href="/tsc/product/luxury-foam-bed-42" title="Luxury Foam Dog Bed">'
        '<img id="img1_42" data-src="//media.tractorsupply.com/is/image/x/123" />'
        "</a>"
        '<div class="rating">'
        '<a title="Product Rating is 4.7">'
        '<span class="sr-only">Product Rating is 4.7</span>'
        "<span>4.7</span><span>(31)</span></a></div>"
    )
    scraper = TractorSupplyScraper()
    products = _parse_search_display_html(html, scraper, set(), 10)
    assert len(products) == 1
    assert products[0].title == "Luxury Foam Dog Bed"
    assert products[0].product_url and "luxury-foam-bed-42" in products[0].product_url
    assert products[0].image_url and "media.tractorsupply.com" in products[0].image_url
    assert products[0].avg_rating == 4.7
    assert products[0].review_count == 31
