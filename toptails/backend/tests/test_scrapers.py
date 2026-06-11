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


def test_tractor_supply_image_src_before_id():
    from app.scrapers.tractor_supply import _image_url_for_catalog_entry

    html = (
        '<a id="catalogEntry_img99" href="/tsc/product/test-bed-99" title="Test Bed">'
        '<img src="//media.tractorsupply.com/is/image/TractorSupplyCompany/placeholder" '
        'data-src="//media.tractorsupply.com/is/image/TractorSupplyCompany/real-99" '
        'id="img1_99" />'
        "</a>"
    )
    url = _image_url_for_catalog_entry(html, "99")
    assert url == "https://media.tractorsupply.com/is/image/TractorSupplyCompany/real-99"


def test_tractor_supply_image_from_pdp_html():
    from app.scrapers.tractor_supply import _image_url_from_pdp_html

    html = (
        '<meta property="og:image" content="//media.tractorsupply.com/is/image/x/pdp-1" />'
        '<script id="__NEXT_DATA__">{"props":{"pageProps":{"imageUrl":"ignored"}}}</script>'
    )
    url = _image_url_from_pdp_html(html)
    assert url == "https://media.tractorsupply.com/is/image/x/pdp-1"


def test_chewy_parse_next_data_products():
    from app.scrapers.chewy import products_from_next_data_for_tests

    nd = {
        "props": {
            "pageProps": {
                "initialState": {
                    "searchSlice": {
                        "plpData": {
                            "products": [
                                {
                                    "name": "FurHaven Orthopedic Dog Bed",
                                    "href": "https://www.chewy.com/furhaven-orthopedic-dog-bed/dp/12345",
                                    "advertisedPrice": "49.99",
                                    "rating": 4.7,
                                    "ratingCount": 128,
                                    "image": "//image.chewy.com/catalog/general/images/moe/abc-uuid,1",
                                }
                            ]
                        }
                    }
                }
            }
        }
    }
    products = products_from_next_data_for_tests(nd)
    assert len(products) == 1
    assert products[0].title == "FurHaven Orthopedic Dog Bed"
    assert products[0].product_url.endswith("/dp/12345")
    assert products[0].price == 49.99
    assert products[0].avg_rating == 4.7
    assert products[0].review_count == 128
    assert products[0].image_url == (
        "https://image.chewy.com/catalog/general/images/moe/"
        "abc-uuid._SX500_SY400_QL75_V1_.jpg"
    )


def test_chewy_normalize_image_url_moe_format():
    from app.scrapers.chewy import _normalize_chewy_image_url

    raw = "//image.chewy.com/catalog/general/images/moe/069d51ae-44c3-7c80-8000-6bba79200015,1"
    url = _normalize_chewy_image_url(raw)
    assert url == (
        "https://image.chewy.com/catalog/general/images/moe/"
        "069d51ae-44c3-7c80-8000-6bba79200015._SX500_SY400_QL75_V1_.jpg"
    )


def test_chewy_parse_next_data_dedupes_parent_variants():
    from app.scrapers.chewy import products_from_next_data_for_tests

    nd = {
        "props": {
            "pageProps": {
                "initialState": {
                    "searchSlice": {
                        "plpData": {
                            "products": [
                                {
                                    "name": "Lesure Dog Bed Medium",
                                    "href": "https://www.chewy.com/lesure-dog-bed/dp/1815798",
                                    "parentPartNumber": 1815686,
                                    "rating": 4.65,
                                    "ratingCount": 797,
                                },
                                {
                                    "name": "Lesure Dog Bed Large",
                                    "href": "https://www.chewy.com/lesure-dog-bed/dp/1815806",
                                    "parentPartNumber": 1815686,
                                    "rating": 4.65,
                                    "ratingCount": 797,
                                },
                                {
                                    "name": "FurHaven Orthopedic Dog Bed",
                                    "href": "https://www.chewy.com/furhaven-orthopedic-dog-bed/dp/12345",
                                    "parentPartNumber": 99999,
                                    "rating": 4.7,
                                    "ratingCount": 128,
                                },
                            ]
                        }
                    }
                }
            }
        }
    }
    products = products_from_next_data_for_tests(nd)
    assert len(products) == 2
    urls = {p.product_url for p in products}
    assert "https://www.chewy.com/lesure-dog-bed/dp/1815798" in urls
    assert "https://www.chewy.com/furhaven-orthopedic-dog-bed/dp/12345" in urls


def test_chewy_parse_next_data_ad_redirect_url():
    from app.scrapers.chewy import products_from_next_data_for_tests

    nd = {
        "props": {
            "pageProps": {
                "initialState": {
                    "searchSlice": {
                        "plpData": {
                            "products": [
                                {
                                    "name": "Ad Tile Dog Bed",
                                    "href": "https://www.chewy.com/api/event/p/sar/click?redirect=https://www.chewy.com/ad-tile-dog-bed/dp/99999",
                                    "rating": 4.6,
                                    "ratingCount": 50,
                                }
                            ]
                        }
                    }
                }
            }
        }
    }
    products = products_from_next_data_for_tests(nd)
    assert len(products) == 1
    assert products[0].product_url.endswith("/dp/99999")


def test_chewy_scraperapi_extra_params_defaults():
    import os

    from app.scrapers.chewy import _chewy_scraperapi_extra_params

    saved = {
        k: os.environ.get(k)
        for k in (
            "CHEWY_SCRAPERAPI_PREMIUM",
            "CHEWY_SCRAPERAPI_RENDER",
            "CHEWY_SCRAPERAPI_ULTRA_PREMIUM",
        )
    }
    try:
        for k in saved:
            os.environ.pop(k, None)
        params = _chewy_scraperapi_extra_params()
        assert params.get("ultra_premium") == "true"
        assert "render" not in params
        assert "premium" not in params
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_chewy_parse_html_ld_json_fallback():
    from app.scrapers.chewy import ChewyScraper, _parse_chewy_html

    html = """
    <html><body>
    <script type="application/ld+json">
    {"@type":"ItemList","itemListElement":[
      {"item":{"@type":"Product","name":"Cooling Dog Bed Mat",
       "url":"https://www.chewy.com/cooling-dog-bed-mat/dp/99999"}}
    ]}
    </script>
    </body></html>
    """
    scraper = ChewyScraper()
    products = _parse_chewy_html(html, scraper, set(), 10)
    assert len(products) == 1
    assert "Cooling Dog Bed Mat" in products[0].title
    assert products[0].product_url.endswith("/dp/99999")


def test_target_normalize_image_url():
    from app.scrapers.target import _normalize_target_image_url

    assert _normalize_target_image_url(
        "//target.scene7.com/is/image/Target/GUEST_abc"
    ) == "https://target.scene7.com/is/image/Target/GUEST_abc"
    assert _normalize_target_image_url(
        "https://target.scene7.com/is/image/Target/GUEST_abc"
    ) == "https://target.scene7.com/is/image/Target/GUEST_abc"


def test_target_parse_product_cards_html():
    from app.scrapers.target import products_from_html_for_tests

    html = """
    <div data-test="@web/ProductCard/ProductCardVariantWrapper">
    <span>$29.99 - $49.99</span>
    <img src="https://target.scene7.com/is/image/Target/GUEST_testimg" />
    <a aria-label="4.6 stars with 42 ratings" href="#"></a>
    <a aria-label="FurHaven Orthopedic Dog Bed"
       data-test="@web/ProductCard/title"
       href="/p/furhaven-orthopedic-dog-bed/-/A-12345678">FurHaven</a>
    </div>
    """
    products = products_from_html_for_tests(html)
    assert len(products) == 1
    assert "Orthopedic Dog Bed" in products[0].title
    assert products[0].product_url.endswith("/A-12345678")
    assert products[0].price == 29.99
    assert products[0].avg_rating == 4.6
    assert products[0].review_count == 42
    assert products[0].image_url and "scene7.com" in products[0].image_url


def test_target_parse_json_ld():
    from app.scrapers.target import products_from_html_for_tests

    html = """
    <html><body>
    <script type="application/ld+json">
    {"@type":"ItemList","itemListElement":[
      {"item":{"@type":"Product","name":"Bolster Dog Bed",
       "url":"https://www.target.com/p/bolster-dog-bed/-/A-99999",
       "aggregateRating":{"ratingValue":4.8,"reviewCount":120},
       "offers":{"price":"39.99"},
       "image":"https://target.scene7.com/is/image/Target/GUEST_x"}}
    ]}
    </script>
    </body></html>
    """
    products = products_from_html_for_tests(html)
    assert len(products) == 1
    assert products[0].title == "Bolster Dog Bed"
    assert products[0].avg_rating == 4.8
    assert products[0].review_count == 120
    assert products[0].price == 39.99


def test_target_redsky_walker():
    from app.scrapers.target import products_from_redsky_for_tests

    data = {
        "data": {
            "search": {
                "products": [
                    {
                        "tcin": "111",
                        "parent_tcin": "999",
                        "title": "Dog Bolster Bed Small",
                        "canonical_url": "/p/dog-bolster-bed/-/A-111",
                        "price": {"current_retail": 24.99},
                        "primary_image_url": "https://target.scene7.com/is/image/Target/GUEST_a",
                        "ratings_and_reviews": {
                            "statistics": {
                                "rating": {"average": 4.7, "count": 88}
                            }
                        },
                    },
                    {
                        "tcin": "222",
                        "parent_tcin": "999",
                        "title": "Dog Bolster Bed Large",
                        "canonical_url": "/p/dog-bolster-bed/-/A-222",
                        "price": {"current_retail": 34.99},
                    },
                ]
            }
        }
    }
    products = products_from_redsky_for_tests(data)
    assert len(products) == 1
    assert products[0].title.startswith("Dog Bolster")
    assert products[0].variant_group_id == "999"
    assert products[0].avg_rating == 4.7
    assert products[0].review_count == 88


def test_target_scraperapi_extra_params_defaults():
    import os

    from app.scrapers.target import _target_scraperapi_extra_params

    saved = {
        k: os.environ.get(k)
        for k in (
            "TARGET_SCRAPERAPI_PREMIUM",
            "TARGET_SCRAPERAPI_RENDER",
            "TARGET_SCRAPERAPI_ULTRA_PREMIUM",
        )
    }
    try:
        for k in saved:
            os.environ.pop(k, None)
        params = _target_scraperapi_extra_params()
        assert params.get("ultra_premium") == "true"
        assert params.get("render") == "true"
        assert "premium" not in params
        redsky_params = _target_scraperapi_extra_params(render=False)
        assert "render" not in redsky_params
        assert redsky_params.get("ultra_premium") == "true"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_target_resolve_redsky_key_fallback():
    import os

    from app.scrapers.target import (
        _FALLBACK_REDSKY_API_KEY,
        _resolve_redsky_key,
    )

    saved = {
        k: os.environ.get(k)
        for k in ("TARGET_REDSKY_API_KEY", "TARGET_REDSKY_FALLBACK_KEY")
    }
    try:
        for k in saved:
            os.environ.pop(k, None)
        assert _resolve_redsky_key("") == _FALLBACK_REDSKY_API_KEY
        assert _resolve_redsky_key('<span data-apiKey="abc123def456"></span>') == (
            _FALLBACK_REDSKY_API_KEY
        )
        html = '{"apiKey":"abcdef1234567890abcdef1234567890"}'
        assert _resolve_redsky_key(html) == "abcdef1234567890abcdef1234567890"
        os.environ["TARGET_REDSKY_API_KEY"] = "env-override-key"
        assert _resolve_redsky_key(html) == "env-override-key"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _walmart_item(
    *,
    name: str,
    us_item_id: str,
    catalog_product_id: str | None = None,
    price: float = 29.99,
    rating: float = 4.6,
    reviews: int = 100,
    sponsored: bool = False,
) -> dict:
    return {
        "__typename": "Product",
        "name": name,
        "usItemId": us_item_id,
        "catalogProductId": catalog_product_id or us_item_id,
        "canonicalUrl": f"/ip/{name.lower().replace(' ', '-')}/{us_item_id}",
        "averageRating": rating,
        "numberOfReviews": reviews,
        "isSponsoredFlag": sponsored,
        "priceInfo": {"currentPrice": {"price": price}},
        "imageInfo": {"thumbnailUrl": "//i5.walmartimages.com/asr/test.jpeg"},
    }


def test_walmart_parse_next_data_item_stacks():
    from app.scrapers.walmart import products_from_next_data_for_tests

    nd = {
        "props": {
            "pageProps": {
                "initialData": {
                    "searchResult": {
                        "itemStacks": [
                            {
                                "items": [
                                    _walmart_item(
                                        name="Orthopedic Dog Bed Large",
                                        us_item_id="111",
                                        rating=4.8,
                                        reviews=250,
                                    ),
                                    _walmart_item(
                                        name="Bolster Dog Bed Medium",
                                        us_item_id="222",
                                        rating=4.7,
                                        reviews=180,
                                    ),
                                ]
                            }
                        ]
                    }
                }
            }
        }
    }
    products = products_from_next_data_for_tests(nd)
    assert len(products) == 2
    assert products[0].avg_rating == 4.8
    assert products[0].review_count == 250
    assert products[0].price == 29.99
    assert products[0].image_url == "https://i5.walmartimages.com/asr/test.jpeg"


def test_walmart_skips_sponsored_items():
    from app.scrapers.walmart import products_from_next_data_for_tests

    nd = {
        "props": {
            "pageProps": {
                "initialData": {
                    "searchResult": {
                        "itemStacks": [
                            {
                                "items": [
                                    _walmart_item(
                                        name="Sponsored Dog Bed",
                                        us_item_id="999",
                                        sponsored=True,
                                    ),
                                    _walmart_item(
                                        name="Real Dog Bed",
                                        us_item_id="888",
                                    ),
                                ]
                            }
                        ]
                    }
                }
            }
        }
    }
    products = products_from_next_data_for_tests(nd)
    assert len(products) == 1
    assert "Real Dog Bed" in products[0].title


def test_walmart_dedupes_parent_variants():
    from app.scrapers.walmart import products_from_next_data_for_tests

    nd = {
        "props": {
            "pageProps": {
                "initialData": {
                    "searchResult": {
                        "itemStacks": [
                            {
                                "items": [
                                    _walmart_item(
                                        name="Dog Bed Small",
                                        us_item_id="100",
                                        catalog_product_id="555",
                                        reviews=50,
                                    ),
                                    _walmart_item(
                                        name="Dog Bed Large",
                                        us_item_id="101",
                                        catalog_product_id="555",
                                        reviews=200,
                                    ),
                                ]
                            }
                        ]
                    }
                }
            }
        }
    }
    products = products_from_next_data_for_tests(nd)
    assert len(products) == 1
    assert products[0].review_count == 200
    assert products[0].variant_group_id == "555"


def test_walmart_normalize_image_url():
    from app.scrapers.walmart import _normalize_walmart_image_url

    assert _normalize_walmart_image_url(
        "//i5.walmartimages.com/asr/abc.jpeg"
    ) == "https://i5.walmartimages.com/asr/abc.jpeg"


def test_walmart_parse_json_ld():
    from app.scrapers.walmart import products_from_html_for_tests

    html = """
    <html><body>
    <script type="application/ld+json">
    {"@type":"ItemList","itemListElement":[
      {"item":{"@type":"Product","name":"Memory Foam Dog Bed",
       "url":"https://www.walmart.com/ip/memory-foam-dog-bed/12345",
       "aggregateRating":{"ratingValue":4.9,"reviewCount":88},
       "offers":{"price":"49.99"},
       "image":"https://i5.walmartimages.com/asr/ld.jpeg"}}
    ]}
    </script>
    </body></html>
    """
    products = products_from_html_for_tests(html)
    assert len(products) == 1
    assert products[0].title == "Memory Foam Dog Bed"
    assert products[0].avg_rating == 4.9
    assert products[0].review_count == 88


def test_walmart_scraperapi_extra_params_defaults():
    import os

    from app.scrapers.walmart import _walmart_scraperapi_extra_params

    saved = {
        k: os.environ.get(k)
        for k in (
            "WALMART_SCRAPERAPI_PREMIUM",
            "WALMART_SCRAPERAPI_RENDER",
            "WALMART_SCRAPERAPI_ULTRA_PREMIUM",
        )
    }
    try:
        for k in saved:
            os.environ.pop(k, None)
        params = _walmart_scraperapi_extra_params()
        assert params.get("ultra_premium") == "true"
        assert "render" not in params
        assert "premium" not in params
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _amazon_structured_item(
    *,
    name: str,
    asin: str,
    price: float = 39.99,
    stars: float = 4.7,
    reviews: int = 500,
    url: str | None = None,
    item_type: str = "search_product",
) -> dict:
    return {
        "type": item_type,
        "asin": asin,
        "name": name,
        "stars": stars,
        "total_reviews": reviews,
        "price": price,
        "price_string": f"${price:.2f}",
        "url": url or f"https://www.amazon.com/dp/{asin}/ref=sr_1_1",
        "image": f"https://m.media-amazon.com/images/I/{asin}.jpg",
    }


def test_amazon_parse_structured_results():
    from app.scrapers.amazon import products_from_structured_for_tests

    data = {
        "ads": [],
        "results": [
            _amazon_structured_item(
                name="Orthopedic Dog Bed Large",
                asin="B001TEST01",
                stars=4.8,
                reviews=1200,
                price=49.99,
            ),
            _amazon_structured_item(
                name="Bolster Dog Bed Medium",
                asin="B002TEST02",
                stars=4.6,
                reviews=800,
            ),
        ],
    }
    products = products_from_structured_for_tests(data)
    assert len(products) == 2
    assert products[0].avg_rating == 4.8
    assert products[0].review_count == 1200
    assert products[0].price == 49.99
    assert products[0].product_url == "https://www.amazon.com/dp/B001TEST01"
    assert products[0].variant_group_id == "orthopedic-dog-bed"
    assert "m.media-amazon.com" in (products[0].image_url or "")


def test_amazon_skips_ads_only_parses_results():
    from app.scrapers.amazon import products_from_structured_for_tests

    data = {
        "ads": [
            _amazon_structured_item(
                name="Sponsored Dog Bed Ad",
                asin="B099SPONS0",
                item_type="editorial_recommendation",
            )
        ],
        "results": [
            _amazon_structured_item(
                name="Real Dog Bed",
                asin="B008REAL08",
            ),
        ],
    }
    products = products_from_structured_for_tests(data)
    assert len(products) == 1
    assert "Real Dog Bed" in products[0].title
    assert products[0].variant_group_id == "real-dog-bed"


def test_amazon_dedupes_by_asin():
    from app.scrapers.amazon import products_from_structured_for_tests

    data = {
        "results": [
            _amazon_structured_item(
                name="Dog Bed Small",
                asin="B00DUPED01",
                reviews=50,
            ),
            _amazon_structured_item(
                name="Dog Bed Large",
                asin="B00DUPED01",
                reviews=300,
            ),
        ],
    }
    products = products_from_structured_for_tests(data)
    assert len(products) == 1
    assert products[0].review_count == 300
    assert products[0].variant_group_id == "dog-bed"


def test_amazon_dedupes_size_variants_by_title():
    from app.scrapers.amazon import products_from_structured_for_tests

    data = {
        "results": [
            _amazon_structured_item(
                name="EHEYCIGA Orthopedic Dog Beds for Extra Large Dogs 44x32Inch",
                asin="B00SIZE001",
                reviews=5000,
            ),
            _amazon_structured_item(
                name="EHEYCIGA Orthopedic Dog Beds for Large Dogs 36x27Inch",
                asin="B00SIZE002",
                reviews=8000,
            ),
        ],
    }
    products = products_from_structured_for_tests(data)
    assert len(products) == 1
    assert products[0].review_count == 8000
    assert "eheyciga" in products[0].variant_group_id


def test_amazon_skips_sponsored_html_urls():
    from app.scrapers.amazon import products_from_html_for_tests

    html = """
    <div data-component-type="s-search-result" data-asin="B00SPONS01">
      <h2><a href="/gp/slredirect/picassoRedirect.html?url=%2Fdp%2FB00SPONS01"><span>Sponsored Bed</span></a></h2>
      <span class="a-offscreen">$29.99</span>
      <span aria-label="4.5 out of 5 stars"></span>
      <span aria-label="100 ratings"></span>
      <img class="s-image" src="https://m.media-amazon.com/images/I/sp.jpg"/>
    </div>
    <div data-component-type="s-search-result" data-asin="B00REALDOG">
      <h2><a href="/Orthopedic-Dog-Bed/dp/B00REALDOG/ref=sr_1_2"><span>Orthopedic Dog Bed</span></a></h2>
      <span class="a-offscreen">$39.99</span>
      <span aria-label="4.7 out of 5 stars"></span>
      <span aria-label="250 ratings"></span>
      <img class="s-image" src="https://m.media-amazon.com/images/I/real.jpg"/>
    </div>
    """
    products = products_from_html_for_tests(html)
    assert len(products) == 1
    assert products[0].variant_group_id == "orthopedic-dog-bed"
    assert products[0].product_url == "https://www.amazon.com/dp/B00REALDOG"


def test_amazon_normalize_image_url():
    from app.scrapers.amazon import _normalize_amazon_image_url

    assert _normalize_amazon_image_url(
        "//m.media-amazon.com/images/I/test.jpg"
    ) == "https://m.media-amazon.com/images/I/test.jpg"


def test_amazon_scraperapi_extra_params_defaults():
    import os

    from app.scrapers.amazon import _amazon_scraperapi_extra_params

    saved = {
        k: os.environ.get(k)
        for k in (
            "AMAZON_SCRAPERAPI_PREMIUM",
            "AMAZON_SCRAPERAPI_RENDER",
            "AMAZON_SCRAPERAPI_ULTRA_PREMIUM",
        )
    }
    try:
        for k in saved:
            os.environ.pop(k, None)
        params = _amazon_scraperapi_extra_params()
        assert params.get("ultra_premium") == "true"
        assert "render" not in params
        assert "premium" not in params
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_amazon_listing_url_page():
    from app.scrapers.amazon import _listing_url, _listing_url_page

    base = _listing_url("dog bed")
    assert _listing_url_page(base, 1) == base
    assert _listing_url_page(base, 2).endswith("page=2")


def test_amazon_multi_page_structured_merge():
    from app.scrapers.amazon import AmazonScraper, _products_from_structured

    scraper = AmazonScraper()
    seen: set[str] = set()
    page1 = {"results": [_amazon_structured_item(name="Dog Bed A", asin="B00PAGE001")]}
    page2 = {"results": [_amazon_structured_item(name="Dog Bed B", asin="B00PAGE002")]}
    products: list = []
    for data in (page1, page2):
        products.extend(_products_from_structured(data, scraper, seen, 10_000))
    assert len(products) == 2
    assert {p.variant_group_id for p in products} == {"dog-bed-a", "dog-bed-b"}


def test_walmart_listing_url_page():
    from app.scrapers.walmart import _listing_url, _listing_url_page

    base = _listing_url("dog bed")
    assert _listing_url_page(base, 1) == base
    assert _listing_url_page(base, 2).endswith("page=2")


def test_walmart_multi_page_merge():
    from app.scrapers.walmart import (
        WalmartScraper,
        _item_stacks_from_next_data,
        _rows_from_item_stacks,
    )

    def _nd(us_item_id: str, name: str) -> dict:
        return {
            "props": {
                "pageProps": {
                    "initialData": {
                        "searchResult": {
                            "itemStacks": [
                                {
                                    "items": [
                                        _walmart_item(name=name, us_item_id=us_item_id)
                                    ]
                                }
                            ]
                        }
                    }
                }
            }
        }

    scraper = WalmartScraper()
    seen: set[str] = set()
    products: list = []
    for nd in (_nd("111", "Walmart Bed Page 1"), _nd("222", "Walmart Bed Page 2")):
        products.extend(
            _rows_from_item_stacks(
                _item_stacks_from_next_data(nd), scraper, seen, 10_000
            )
        )
    assert len(products) == 2


def test_chewy_listing_url_page():
    from app.scrapers.chewy import LISTING_URL, _listing_url_page

    assert _listing_url_page(LISTING_URL, 1) == LISTING_URL
    assert _listing_url_page(LISTING_URL, 2).endswith("page=2")


def test_chewy_multi_page_aggregation():
    from app.scrapers.chewy import (
        ChewyScraper,
        _chewy_best_tiles_by_parent,
        _chewy_tile_to_raw,
        _next_data_products,
        _product_from_raw,
    )

    def _chewy_nd(href: str, name: str) -> dict:
        return {
            "props": {
                "pageProps": {
                    "initialState": {
                        "searchSlice": {
                            "plpData": {
                                "products": [
                                    {
                                        "name": name,
                                        "href": href,
                                        "advertisedPrice": "29.99",
                                        "rating": 4.6,
                                        "ratingCount": 50,
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        }

    scraper = ChewyScraper()
    seen: set[str] = set()
    products: list = []
    for nd in (
        _chewy_nd("https://www.chewy.com/bed-a/dp/10001", "Chewy Bed A"),
        _chewy_nd("https://www.chewy.com/bed-b/dp/10002", "Chewy Bed B"),
    ):
        for item in _chewy_best_tiles_by_parent(_next_data_products(nd)):
            raw = _chewy_tile_to_raw(item)
            if raw:
                row = _product_from_raw(scraper, raw, seen)
                if row:
                    products.append(row)
    assert len(products) == 2


def test_target_listing_url_nao():
    from app.scrapers.target import _listing_url, _listing_url_nao

    base = _listing_url("dog bed")
    assert _listing_url_nao(base, 0) == base
    assert _listing_url_nao(base, 24).endswith("Nao=24")


def test_target_redsky_offset():
    from app.scrapers.target import _build_redsky_search_url

    url = _build_redsky_search_url("test-key", keyword="dog bed", offset=24)
    assert "offset=24" in url
    assert "count=24" in url


def test_target_plp_offsets_two_pages():
    from app.scrapers.target import _target_plp_offsets

    assert _target_plp_offsets(2) == [0, 24]
    assert _target_plp_offsets(1) == [0]


@pytest.mark.asyncio
async def test_target_always_fetches_both_redsky_pages(monkeypatch):
    from app.scrapers.base import ProductRaw
    from app.scrapers.target import TargetScraper

    offsets_called: list[int] = []

    async def fake_redsky(
        self,
        client,
        api_key,
        html,
        seen_urls,
        limit,
        *,
        keyword="dog bed",
        offset=0,
    ):
        offsets_called.append(offset)
        return [
            ProductRaw(
                source_site="target",
                title=f"Bed offset {offset}",
                product_url=f"https://www.target.com/p/bed/-/A-{offset}",
                scrape_status="ok",
            )
        ]

    async def fake_get(client, api_key, target_url):
        shell = "x" * 9000 + '{"apiKey":"abcdef1234567890abcdef1234567890"}'
        return f"<html>{shell}</html>", 200

    monkeypatch.setenv("SCRAPERAPI_KEY", "test-key")
    scraper = TargetScraper()
    monkeypatch.setattr(TargetScraper, "_fetch_redsky_at_offset", fake_redsky)
    monkeypatch.setattr("app.scrapers.target._target_scraperapi_get", fake_get)
    monkeypatch.setattr("app.scrapers.target._parse_target_html", lambda *a, **k: [])

    products = await scraper._fetch_listings_via_scraperapi(
        "dog bed", 20, listing_pages=2
    )
    assert offsets_called == [0, 24]
    assert len(products) == 2
