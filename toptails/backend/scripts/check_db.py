#!/usr/bin/env python3
"""Print product counts and latest rows. Run from repo root:

  cd "petproject upwork/toptails/backend" && source .venv/bin/activate && python scripts/check_db.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> None:
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text

    load_dotenv(os.path.join(ROOT, ".env"))
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set (expected in backend/.env)", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as c:
        n = c.execute(text("select count(*) from products")).scalar()
        print("products.total_rows:", n)
        rows = c.execute(
            text(
                """
                select source_site, scrape_status, trust_score,
                       left(title, 55) as title, scraped_at
                from products
                order by scraped_at desc nulls last
                limit 15
                """
            )
        ).fetchall()
        print("latest 15:")
        for r in rows:
            print(" ", r)

    print(
        "\nIf `alembic upgrade head` fails with DuplicateTable on products, run:\n"
        "  python scripts/alembic_sync.py\n"
        "or manually: alembic stamp c7db8f38ca79 && alembic upgrade head"
    )


if __name__ == "__main__":
    main()
