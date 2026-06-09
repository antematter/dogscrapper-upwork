import math
from datetime import date, timedelta
from typing import Optional


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
    if not review_dates or len(review_dates) < 10:
        return 1.0

    try:
        parsed = sorted(
            date.fromisoformat(d)
            for d in review_dates
            if isinstance(d, str) and len(d) == 10
        )
    except ValueError:
        return 1.0

    if len(parsed) < 10:
        return 1.0

    total = len(parsed)
    for anchor in parsed:
        window_end = anchor + timedelta(days=2)
        count_in_window = sum(1 for d in parsed if anchor <= d <= window_end)
        if count_in_window / total > 0.30:
            return 0.6

    return 1.0


def compute_trust_score(
    avg_rating: Optional[float],
    review_count: Optional[int],
    five_star_ratio: Optional[float],
    verified_ratio: Optional[float],
    review_dates: Optional[list[str]],
) -> float:
    if avg_rating is None or review_count is None:
        return 0.0

    vw = volume_weight(review_count)
    if vw == 0.0:
        return 0.0

    score = (
        (avg_rating / 5.0)
        * vw
        * distribution_penalty(five_star_ratio or 0.0)
        * verified_bonus(verified_ratio or 0.0)
        * velocity_penalty(review_dates or [])
    )
    return min(max(score, 0.0), 1.0)
