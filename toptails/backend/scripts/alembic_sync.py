#!/usr/bin/env python3
"""Align Alembic with a DB where `products` was created via SQLAlchemy create_all().

Then run pending migrations (e.g. widen avg_rating).

  cd "/path/to/toptails/backend" && source .venv/bin/activate && python scripts/alembic_sync.py

If you see DuplicateTable on `alembic upgrade head`, run this once.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_exe() -> str:
    candidate = Path(sys.executable).parent / "alembic"
    if candidate.is_file():
        return str(candidate)
    print(
        "Could not find `alembic` next to this Python. Activate the backend venv "
        "that has alembic installed, then run this script again.",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text

    load_dotenv(os.path.join(ROOT, ".env"))
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL missing (set in backend/.env)", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as c:
        has_products = c.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = 'products'
                )
                """
            )
        ).scalar()
        has_version = c.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = 'alembic_version'
                )
                """
            )
        ).scalar()
        current = None
        if has_version:
            row = c.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            ).fetchone()
            current = row[0] if row else None

    def run(args: list[str]) -> None:
        subprocess.run(args, cwd=ROOT, check=True, env={**os.environ})

    alembic = _alembic_exe()

    if has_products and not current:
        print(
            "Database has `products` but no Alembic revision recorded; "
            "stamping baseline c7db8f38ca79 (no DDL)."
        )
        run([alembic, "stamp", "c7db8f38ca79"])

    print("Running: alembic upgrade head")
    run([alembic, "upgrade", "head"])
    print("Alembic is up to date.")


if __name__ == "__main__":
    main()
