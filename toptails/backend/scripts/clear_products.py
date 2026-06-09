#!/usr/bin/env python3
"""Wipe products and reset scrape/debug state for a clean dev run.

  cd "/path/to/toptails/backend" && source .venv/bin/activate && python scripts/clear_products.py

- Truncates `products` (DATABASE_URL from backend/.env).
- POSTs to the API at API_URL (default http://localhost:8000) to clear in-memory
  global + per-site scrape debug (what the UI polls). If the API is down, only the
  DB is cleared — restart uvicorn to clear stale banners.

No confirmation prompt — intended for dev.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def truncate_products() -> int:
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text

    load_dotenv(os.path.join(ROOT, ".env"))
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL missing", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as conn:
        n = conn.execute(text("select count(*) from products")).scalar()
        conn.execute(text("truncate table products restart identity cascade"))
    return int(n or 0)


def reset_api_scrape_state(api_url: str) -> tuple[bool, str]:
    endpoint = f"{api_url.rstrip('/')}/scrape/reset-state"
    req = urllib.request.Request(endpoint, method="POST", data=b"")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            if resp.status != 200:
                return False, f"HTTP {resp.status}: {body}"
            try:
                data = json.loads(body)
                return True, data.get("message", "OK")
            except json.JSONDecodeError:
                return True, body or "OK"
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else str(e)
        return False, f"HTTP {e.code}: {detail}"
    except urllib.error.URLError as e:
        return False, str(e.reason if hasattr(e, "reason") else e)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    api_url = os.environ.get("API_URL", "http://localhost:8000")

    n = truncate_products()
    print(f"Truncated products table ({n} rows removed).")

    ok, msg = reset_api_scrape_state(api_url)
    if ok:
        print(f"Reset API scrape state: {msg}")
        print("Refresh the frontend — product cards and per-site debug should be cleared.")
    else:
        print(
            f"Could not reset API scrape state ({msg}).\n"
            "  • Is uvicorn running? Start it, then run this script again, or\n"
            "  • Restart the API after truncate — that also clears in-memory debug.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
