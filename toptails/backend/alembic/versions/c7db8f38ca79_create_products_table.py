"""create products table

Revision ID: c7db8f38ca79
Revises: 
Create Date: 2026-05-14 18:19:05.387738

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7db8f38ca79'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source_site", sa.String(50), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=True),
        sa.Column("product_url", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("avg_rating", sa.Numeric(3, 2), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("verified_ratio", sa.Numeric(4, 3), nullable=True),
        sa.Column("five_star_ratio", sa.Numeric(4, 3), nullable=True),
        sa.Column("rating_distribution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("review_dates", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("trust_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("scrape_status", sa.String(20), server_default="ok", nullable=True),
        sa.Column("scrape_notes", sa.Text(), nullable=True),
        sa.Column("scraped_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("products")
