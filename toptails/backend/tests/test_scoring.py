import pytest
from app.scoring.trust_score import (
    compute_trust_score,
    volume_weight,
    distribution_penalty,
    verified_bonus,
    velocity_penalty,
)
from app.scoring.ranker import rank_products, score_products
from app.scrapers.base import ProductRaw


# --- volume_weight ---

def test_volume_weight_below_15_is_zero():
    assert volume_weight(0) == 0.0
    assert volume_weight(14) == 0.0


def test_volume_weight_at_15_is_nonzero():
    w = volume_weight(15)
    assert w > 0.0
    assert w < 1.0


def test_volume_weight_at_50_is_approx_half():
    w = volume_weight(50)
    assert abs(w - 0.5) < 0.2


def test_volume_weight_at_200_approaches_1():
    w = volume_weight(200)
    assert w > 0.9


# --- distribution_penalty ---

def test_distribution_penalty_normal():
    assert distribution_penalty(0.70) == 1.0


def test_distribution_penalty_suspicious():
    assert distribution_penalty(0.85) == 0.75


def test_distribution_penalty_likely_fake():
    assert distribution_penalty(0.95) == 0.5


def test_distribution_penalty_boundary_80():
    assert distribution_penalty(0.80) == 1.0    # exactly 0.80 is not > 0.80
    assert distribution_penalty(0.801) == 0.75


def test_distribution_penalty_boundary_90():
    assert distribution_penalty(0.90) == 0.75   # exactly 0.90 is not > 0.90
    assert distribution_penalty(0.901) == 0.5


# --- verified_bonus ---

def test_verified_bonus_zero_verified():
    assert verified_bonus(0.0) == 1.0


def test_verified_bonus_fully_verified():
    assert verified_bonus(1.0) == 1.3


def test_verified_bonus_half_verified():
    assert abs(verified_bonus(0.5) - 1.15) < 0.001


# --- velocity_penalty ---

def test_velocity_penalty_empty_dates_returns_1():
    assert velocity_penalty([]) == 1.0


def test_velocity_penalty_single_date_returns_1():
    assert velocity_penalty(["2024-01-01"]) == 1.0


def test_velocity_penalty_no_burst():
    # 10 reviews spread over 30 days — no burst
    dates = [f"2024-01-{d:02d}" for d in range(1, 31, 3)]  # 10 dates
    assert velocity_penalty(dates) == 1.0


def test_velocity_penalty_burst_detected():
    # 9 out of 10 reviews in 3 days = 90% > 30% threshold
    dates = ["2024-01-01"] * 9 + ["2024-02-15"]
    assert velocity_penalty(dates) == 0.6


def test_velocity_penalty_too_few_dates_returns_1():
    # Only 2 dates — not enough signal, should not penalize
    assert velocity_penalty(["2024-01-01", "2024-01-02"]) == 1.0
    assert velocity_penalty(["2024-01-01", "2024-09-15"]) == 1.0  # 2 dates far apart


def test_velocity_penalty_malformed_dates_returns_1():
    # Malformed dates should not crash — return 1.0 (no penalty)
    dates = ["2024-01-01"] * 9 + ["not-a-date"]
    assert velocity_penalty(dates) == 1.0


def test_velocity_penalty_exactly_30pct_no_penalty():
    # 3 out of 10 in a 3-day window = exactly 30%, not > 30%, so no penalty
    dates = ["2024-01-01", "2024-01-02", "2024-01-03"] + [
        f"2024-02-{d:02d}" for d in range(1, 8)
    ]  # 10 total
    assert velocity_penalty(dates) == 1.0


# --- compute_trust_score ---

def test_trust_score_below_15_reviews_is_zero():
    score = compute_trust_score(
        avg_rating=4.8,
        review_count=10,
        five_star_ratio=0.6,
        verified_ratio=0.8,
        review_dates=[],
    )
    assert score == 0.0


def test_trust_score_reasonable_product():
    score = compute_trust_score(
        avg_rating=4.3,
        review_count=150,
        five_star_ratio=0.65,
        verified_ratio=0.75,
        review_dates=[f"2024-01-{d:02d}" for d in range(1, 28, 3)],
    )
    assert 0.0 < score <= 1.0


def test_trust_score_clamped_to_1():
    score = compute_trust_score(
        avg_rating=5.0,
        review_count=1000,
        five_star_ratio=0.7,
        verified_ratio=1.0,
        review_dates=[f"2024-01-{d:02d}" for d in range(1, 28, 3)],
    )
    assert score <= 1.0


def test_trust_score_none_rating_returns_zero():
    score = compute_trust_score(
        avg_rating=None,
        review_count=200,
        five_star_ratio=0.6,
        verified_ratio=0.8,
        review_dates=[],
    )
    assert score == 0.0


def test_trust_score_none_review_count_returns_zero():
    score = compute_trust_score(
        avg_rating=4.5,
        review_count=None,
        five_star_ratio=0.6,
        verified_ratio=0.8,
        review_dates=[],
    )
    assert score == 0.0


# --- ranker ---

def _make_product(site: str, trust: float, status: str = "ok") -> ProductRaw:
    return ProductRaw(
        source_site=site,
        title=f"Product {trust}",
        trust_score=trust,
        scrape_status=status,
        review_count=100,
        avg_rating=4.8,
    )


def test_ranker_returns_top_n_per_site():
    products = [
        _make_product("chewy", 0.9),
        _make_product("chewy", 0.7),
        _make_product("chewy", 0.5),
        _make_product("amazon", 0.8),
        _make_product("amazon", 0.6),
    ]
    ranked = rank_products(products, top_n=2)
    chewy = ranked["chewy"]
    assert len(chewy) == 2
    assert chewy[0].trust_score >= chewy[1].trust_score
    assert len(ranked["amazon"]) == 2


def test_ranker_excludes_blocked():
    products = [
        _make_product("amazon", 0.0, status="blocked"),
    ]
    ranked = rank_products(products, top_n=2)
    assert ranked["amazon"] == []


def test_ranker_handles_fewer_than_n_products():
    products = [_make_product("petco", 0.8)]
    ranked = rank_products(products, top_n=2)
    assert len(ranked["petco"]) == 1


def test_ranker_includes_zero_trust_score_for_sorting():
    """Low-volume products (trust 0) still rank after higher-trust peers."""
    products = [
        _make_product("chewy", 0.0),
        _make_product("chewy", 0.7),
    ]
    ranked = rank_products(products, top_n=2)
    assert len(ranked["chewy"]) == 2
    assert ranked["chewy"][0].trust_score == 0.7
    assert ranked["chewy"][1].trust_score == 0.0


def test_ranker_excludes_below_min_rating_or_reviews():
    products = [
        ProductRaw(
            source_site="petsmart",
            title="Low rating",
            trust_score=0.9,
            scrape_status="ok",
            avg_rating=4.2,
            review_count=200,
        ),
        ProductRaw(
            source_site="petsmart",
            title="Few reviews",
            trust_score=0.9,
            scrape_status="ok",
            avg_rating=4.9,
            review_count=5,
        ),
        ProductRaw(
            source_site="petsmart",
            title="Qualified",
            trust_score=0.5,
            scrape_status="ok",
            avg_rating=4.8,
            review_count=50,
        ),
    ]
    ranked = rank_products(products, top_n=2)
    assert len(ranked["petsmart"]) == 1
    assert ranked["petsmart"][0].title == "Qualified"


def test_ranker_dedupes_same_product_url():
    products = [
        ProductRaw(
            source_site="chewy",
            title="Bedsure Large",
            trust_score=0.93,
            scrape_status="ok",
            avg_rating=4.69,
            review_count=265,
            product_url="https://www.chewy.com/bedsure/dp/2461134",
        ),
        ProductRaw(
            source_site="chewy",
            title="Bedsure Large duplicate",
            trust_score=0.93,
            scrape_status="ok",
            avg_rating=4.69,
            review_count=265,
            product_url="https://www.chewy.com/bedsure/dp/2461134",
        ),
        ProductRaw(
            source_site="chewy",
            title="Lesure Medium",
            trust_score=0.91,
            scrape_status="ok",
            avg_rating=4.65,
            review_count=797,
            product_url="https://www.chewy.com/lesure/dp/1815798",
        ),
    ]
    ranked = rank_products(products, top_n=2)
    assert len(ranked["chewy"]) == 2
    urls = {p.product_url for p in ranked["chewy"]}
    assert len(urls) == 2


def test_ranker_dedupes_chewy_variants_by_parent():
    products = [
        ProductRaw(
            source_site="chewy",
            title="Lesure Medium",
            trust_score=0.93,
            scrape_status="ok",
            avg_rating=4.65,
            review_count=797,
            product_url="https://www.chewy.com/lesure/dp/1815798",
            variant_group_id="1815686",
        ),
        ProductRaw(
            source_site="chewy",
            title="Lesure Large",
            trust_score=0.93,
            scrape_status="ok",
            avg_rating=4.65,
            review_count=797,
            product_url="https://www.chewy.com/lesure/dp/1815806",
            variant_group_id="1815686",
        ),
        ProductRaw(
            source_site="chewy",
            title="Bedsure Large",
            trust_score=0.94,
            scrape_status="ok",
            avg_rating=4.69,
            review_count=265,
            product_url="https://www.chewy.com/bedsure/dp/2461134",
            variant_group_id="2461000",
        ),
    ]
    ranked = rank_products(products, top_n=2)
    assert len(ranked["chewy"]) == 2
    titles = {p.title for p in ranked["chewy"]}
    assert "Bedsure Large" in titles
    assert ("Lesure Medium" in titles) ^ ("Lesure Large" in titles)


def test_score_products_computes_trust_scores():
    products = [
        ProductRaw(
            source_site="chewy",
            title="Test Bed",
            avg_rating=4.3,
            review_count=150,
            five_star_ratio=0.65,
            verified_ratio=0.75,
            review_dates=[f"2024-01-{d:02d}" for d in range(1, 28, 3)],
        )
    ]
    scored = score_products(products)
    assert scored[0].trust_score is not None
    assert scored[0].trust_score > 0.0
