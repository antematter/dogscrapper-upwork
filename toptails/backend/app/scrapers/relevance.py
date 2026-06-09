"""Relevance helpers for dog_beds category scrapes."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.scrapers.base import ProductRaw

# Strong signals in title
_BED_TITLE = re.compile(
    r"\b("
    r"dog bed|dog beds|pet bed|bolster bed|orthopedic bed|"
    r"donut bed|cuddler bed|nester bed|lounger bed|pillow bed|"
    r"elevated bed|cooling bed|heated bed|"
    r"crate (?:mat|pad|bed)|bed mat|bedding|"
    r"\bbed\b|\bbeds\b|"
    r"bolster|cot\b|daybed"
    r")\b",
    re.IGNORECASE,
)

# Exclude obvious non-bedding (toys, food, cat-only, grooming, etc.)
_EXCLUDE_TITLE = re.compile(
    r"\b("
    r"dog food|cat food|kitten food|"
    r"treats?\b|biscuits?\b|chewy treats|"
    r"dog toy|plush toy|interactive (?:plush )?dog toy|"
    r"squeaky|ball toy|rope toy|"
    r"litter box|litter refill|"
    r"flea comb|nail clipper|trimming shears|ear cleaner|"
    r"training pad\b|pee pad|"
    r"fish |aquarium|bird cage|reptile|"
    r"stroller\b|camera\b|bowl\b|feeder\b|"
    r"shampoo|conditioner|toothbrush|dental chew"
    r")\b",
    re.IGNORECASE,
)

_CAT_NOT_DOG = re.compile(r"\b(cat|kitten|feline)\b", re.IGNORECASE)
_DOG_IN_TITLE = re.compile(r"\bdog\b", re.IGNORECASE)

_BED_URL = re.compile(
    r"(dog[-_]bed|dog[-_]beds|/beds/|beds-and-bedding|beds-and-furniture|/bedding/)",
    re.IGNORECASE,
)


def is_relevant_dog_bed(title: str, product_url: str | None = None) -> bool:
    t = (title or "").strip()
    if len(t) < 3:
        return False
    if _EXCLUDE_TITLE.search(t):
        return False
    if _CAT_NOT_DOG.search(t) and not _DOG_IN_TITLE.search(t):
        return False
    if _BED_TITLE.search(t):
        return True
    u = product_url or ""
    if _BED_URL.search(u):
        return True
    return False


def filter_dog_bed_products(
    products: list[Any],
    *,
    query: str = "dog bed",
) -> list[Any]:
    """Drop off-topic rows; keep blocked status rows unchanged."""
    kept: list[Any] = []
    for p in products:
        if p.scrape_status != "ok":
            kept.append(p)
            continue
        if is_relevant_dog_bed(p.title or "", p.product_url):
            kept.append(p)
    return kept


def dedupe_by_product_url(products: list[Any]) -> list[Any]:
    seen: set[str] = set()
    out: list[Any] = []
    for p in products:
        key = (p.product_url or "").split("?")[0].rstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out
