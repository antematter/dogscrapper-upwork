from collections import defaultdict
from app.scrapers.base import ProductRaw
from app.scoring.trust_score import compute_trust_score

ALL_SITES = [
    "amazon",
    "walmart",
    "chewy",
    "petsmart",
    "petco",
    "target",
    "tractor_supply",
]

# MVP listing picks: must meet both before trust-score ranking.
MIN_AVG_RATING = 4.5
MIN_REVIEW_COUNT = 10


def _product_dedupe_key(p: ProductRaw) -> str:
    if p.variant_group_id:
        return f"{p.source_site}:{p.variant_group_id}"
    url = (p.product_url or "").split("?")[0].rstrip("/")
    if url:
        return f"{p.source_site}:{url}"
    return f"{p.source_site}:title:{(p.title or '').strip().lower()}"


def _dedupe_for_ranking(products: list[ProductRaw]) -> list[ProductRaw]:
    """Keep the highest trust-score row per URL or variant group (e.g. Chewy parent SKU)."""
    best: dict[str, ProductRaw] = {}
    for p in products:
        key = _product_dedupe_key(p)
        cur = best.get(key)
        if cur is None or (p.trust_score or 0) > (cur.trust_score or 0):
            best[key] = p
    return list(best.values())


def _meets_listing_criteria(p: ProductRaw) -> bool:
    return (
        p.avg_rating is not None
        and p.avg_rating >= MIN_AVG_RATING
        and p.review_count is not None
        and p.review_count >= MIN_REVIEW_COUNT
    )


def score_products(products: list[ProductRaw]) -> list[ProductRaw]:
    """Mutates each ProductRaw in-place by setting trust_score. Returns the same list."""
    for p in products:
        if p.scrape_status != "ok":
            continue
        p.trust_score = compute_trust_score(
            avg_rating=p.avg_rating,
            review_count=p.review_count,
            five_star_ratio=p.five_star_ratio,
            verified_ratio=p.verified_ratio,
            review_dates=p.review_dates,
        )
    return products


def rank_products(
    products: list[ProductRaw], top_n: int = 2
) -> dict[str, list[ProductRaw]]:
    ranked: dict[str, list[ProductRaw]] = {site: [] for site in ALL_SITES}

    by_site: dict[str, list[ProductRaw]] = defaultdict(list)
    for p in products:
        by_site[p.source_site].append(p)

    for site, site_products in by_site.items():
        blocked = [p for p in site_products if p.scrape_status != "ok"]
        if blocked:
            ranked[site] = []
            continue

        eligible = _dedupe_for_ranking(
            [
                p
                for p in site_products
                if p.scrape_status == "ok"
                and p.trust_score is not None
                and _meets_listing_criteria(p)
            ]
        )
        top = sorted(
            eligible,
            key=lambda p: (
                -(p.trust_score or 0),
                -(p.review_count or 0),
                -(p.avg_rating or 0),
                (p.product_url or ""),
            ),
        )[:top_n]
        ranked[site] = top

    return ranked
