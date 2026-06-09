import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Numeric, Integer, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_site = Column(String(50), nullable=False)
    category = Column(String(50), nullable=False)
    title = Column(Text, nullable=False)
    price = Column(Numeric(10, 2))
    product_url = Column(Text)
    image_url = Column(Text)
    avg_rating = Column(Numeric(4, 2))
    review_count = Column(Integer)
    verified_ratio = Column(Numeric(4, 3))
    five_star_ratio = Column(Numeric(4, 3))
    rating_distribution = Column(JSONB)
    review_dates = Column(JSONB)
    trust_score = Column(Numeric(5, 4))
    scrape_status = Column(String(20), default="ok")
    scrape_notes = Column(Text)
    scraped_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
