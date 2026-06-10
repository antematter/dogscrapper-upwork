import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()
from sqlalchemy.orm import sessionmaker
from app.models.product import Base, Product
from app.scrapers.base import ProductRaw


def _clamp_star_rating(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if 0.0 <= value <= 5.0:
        return value
    return None

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    Base.metadata.create_all(bind=engine)


def save_products(products: list[ProductRaw]) -> None:
    """Persist scored products to the database. Skips empty-title records."""
    db = SessionLocal()
    batch_at = datetime.now(timezone.utc)
    try:
        for p in products:
            if not p.title:
                continue
            row = Product(
                source_site=p.source_site,
                category=p.category,
                title=p.title,
                price=p.price,
                product_url=p.product_url,
                image_url=p.image_url,
                avg_rating=_clamp_star_rating(p.avg_rating),
                review_count=p.review_count,
                verified_ratio=p.verified_ratio,
                five_star_ratio=p.five_star_ratio,
                rating_distribution=p.rating_distribution,
                review_dates=p.review_dates,
                trust_score=p.trust_score,
                scrape_status=p.scrape_status,
                scrape_notes=p.scrape_notes,
                scraped_at=batch_at,
            )
            db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
