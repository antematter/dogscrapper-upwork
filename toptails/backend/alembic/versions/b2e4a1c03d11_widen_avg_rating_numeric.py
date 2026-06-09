"""widen avg_rating to avoid overflow on bad parses

Revision ID: b2e4a1c03d11
Revises: c7db8f38ca79
Create Date: 2026-05-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2e4a1c03d11"
down_revision: Union[str, None] = "c7db8f38ca79"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "products",
        "avg_rating",
        existing_type=sa.Numeric(3, 2),
        type_=sa.Numeric(4, 2),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "products",
        "avg_rating",
        existing_type=sa.Numeric(4, 2),
        type_=sa.Numeric(3, 2),
        existing_nullable=True,
    )
