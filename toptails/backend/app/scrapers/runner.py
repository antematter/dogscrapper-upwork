import asyncio
from app.scrapers.tractor_supply import TractorSupplyScraper
from app.scrapers.target import TargetScraper
from app.scrapers.chewy import ChewyScraper
from app.scrapers.petsmart import PetsmartScraper
from app.scrapers.petco import PetcoScraper
from app.scrapers.amazon import AmazonScraper
from app.scrapers.walmart import WalmartScraper
from app.scrapers.base import ProductRaw

ALL_SCRAPERS = [
    TractorSupplyScraper,
    TargetScraper,
    ChewyScraper,
    PetsmartScraper,
    PetcoScraper,
    AmazonScraper,
    WalmartScraper,
]

SCRAPER_BY_SITE: dict[str, type] = {cls.SITE_NAME: cls for cls in ALL_SCRAPERS}


async def run_scraper(site: str) -> list[ProductRaw]:
    scraper_cls = SCRAPER_BY_SITE.get(site)
    if not scraper_cls:
        raise ValueError(f"Unknown site: {site}")
    result = await scraper_cls().run()
    if isinstance(result, BaseException):
        return []
    return result


async def run_all_scrapers() -> list[ProductRaw]:
    tasks = [scraper_cls().run() for scraper_cls in ALL_SCRAPERS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    flat: list[ProductRaw] = []
    for site_results in results:
        if isinstance(site_results, BaseException):
            # BaseScraper.run() should catch all exceptions — this is a safety net
            continue
        flat.extend(site_results)
    return flat
