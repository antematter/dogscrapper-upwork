# TopTails — Local Setup Guide

This document records the local development setup for TopTails, including workarounds encountered during initial installation.

## Project Overview

**TopTails** is a dog-bed product scraper and trust-scoring pipeline. It scrapes 7 retailers, scores products by rating/review quality, and displays the top 2 per site in a web UI.

| Path | Role |
|------|------|
| `toptails/backend/` | FastAPI app, 7 retailer scrapers, trust scoring, PostgreSQL persistence |
| `toptails/frontend/` | Next.js 16 UI — per-site scrape buttons + product cards |
| `testers/` | Standalone scraper prototypes (for debugging individual retailers) |

**Scraper reliability:**

| Retailer | Status | Method |
|----------|--------|--------|
| Tractor Supply | Done | ScraperAPI primary |
| Petco | Done | ScraperAPI + Playwright fallback |
| PetSmart | Done | Playwright |
| Target | Done | ScraperAPI only (ultra + render) |
| Chewy | Done | ScraperAPI only (ultra, no render) |
| Amazon | Implemented, fragile | Playwright (often blocked) |
| Walmart | Done | ScraperAPI only (ultra, no render) |

---

## Prerequisites

- **Python 3.11+** (tested with 3.12.3)
- **Node.js 20+** (tested with v24.10.0)
- **PostgreSQL** running on port **5432**
- **npm** (comes with Node)

Verify:

```bash
python3 --version
node --version
systemctl is-active postgresql   # or: pg_isready -h localhost -p 5432
```

---

## Step 1: Create PostgreSQL Database

Run once. If `sudo` is available:

```bash
sudo -u postgres psql -c "CREATE USER \"user\" WITH PASSWORD 'password';"
sudo -u postgres psql -c "CREATE DATABASE toptails OWNER \"user\";"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE toptails TO \"user\";"
```

If the user already exists, skip the first command.

**Alternative** (when `sudo` is not available but you have the `postgres` superuser password):

```bash
export PGPASSWORD=postgres
psql -h localhost -p 5432 -U postgres -d postgres -c "CREATE USER \"user\" WITH PASSWORD 'password';"
psql -h localhost -p 5432 -U postgres -d postgres -c "CREATE DATABASE toptails OWNER \"user\";"
psql -h localhost -p 5432 -U postgres -d postgres -c "GRANT ALL PRIVILEGES ON DATABASE toptails TO \"user\";"
```

Verify connection:

```bash
PGPASSWORD=password psql -h localhost -p 5432 -U user -d toptails -c "SELECT current_database(), current_user;"
```

---

## Step 2: Configure Backend Environment

Create `toptails/backend/.env` from the example:

```bash
cd toptails/backend
cp .env.example .env
```

Set these values in `.env`:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/toptails
SCRAPERAPI_KEY=<your-scraperapi-key>
```

`SCRAPERAPI_KEY` improves reliability for **Petco** and **Tractor Supply** scrapers. Other sites use Playwright.

> **Note:** `toptails/.env.example` (with `PLAYWRIGHT_HEADLESS`, `LOG_LEVEL`) is only needed for full Docker Compose — not required for native local dev.

---

## Step 3: Set Up Python Backend

### First-time setup

```bash
cd toptails/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Workaround: `python3-venv` not installed

If `python3 -m venv` fails with "ensurepip is not available", bootstrap manually:

```bash
cd toptails/backend
python3 -m venv .venv --without-pip
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

Or install the system package (requires sudo):

```bash
sudo apt install python3.12-venv
```

### Start the server (every session)

```bash
cd toptails/backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

Backend runs at **http://localhost:8000**.

**Database schema:** On startup, `app/main.py` auto-creates the `products` table. If Alembic is needed later:

```bash
alembic upgrade head
# If "DuplicateTable" error (table already exists from create_all):
python scripts/alembic_sync.py
```

---

## Step 4: Set Up Next.js Frontend

```bash
cd toptails/frontend
npm install
npm run dev
```

Frontend runs at **http://localhost:3000**.

No frontend `.env` file is needed — `API_URL` defaults to `http://localhost:8000` server-side. To override, create `toptails/frontend/.env.local`:

```env
API_URL=http://localhost:8000
```

---

## Step 5: Verify the Stack

```bash
# Backend health
curl http://localhost:8000/health

# Database connection
cd toptails/backend && source .venv/bin/activate
python scripts/check_db.py

# Backend tests (expect ~41 passing)
pytest -v

# Frontend (expect HTTP 200)
curl -o /dev/null -w "%{http_code}" http://localhost:3000
```

---

## Step 6: Run Scrapes

### Per-site (recommended)

Open http://localhost:3000 and click **"Scrape site"** on individual retailer sections. Start with reliable sites:

1. Tractor Supply or Target
2. PetSmart, Petco, Chewy
3. Amazon / Walmart last (often blocked)

### All sites at once (3–8 minutes)

```bash
curl -X POST http://localhost:8000/scrape/run \
  -H "Content-Type: application/json" \
  -d '{"category": "dog_beds", "top_n": 2}'
```

### Single site via API

```bash
curl -X POST http://localhost:8000/scrape/run/tractor_supply \
  -H "Content-Type: application/json" \
  -d '{"category": "dog_beds", "top_n": 2}'
```

### Reset between test sessions

```bash
cd toptails/backend
source .venv/bin/activate
python scripts/clear_products.py
```

Truncates the `products` table and resets in-memory scrape state. Backend must be running for the API reset call.

---

## Quick Reference — Daily Workflow

**Terminal 1 — Backend:**
```bash
cd toptails/backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

**Terminal 2 — Frontend:**
```bash
cd toptails/frontend
npm run dev
```

**Open:** http://localhost:3000

---

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Service status |
| POST | `/scrape/run` | Scrape all 7 sites |
| POST | `/scrape/run/{site}` | Scrape one site |
| GET | `/scrape/status` | Global scrape progress |
| GET | `/scrape/status/{site}` | Per-site scrape status |
| GET | `/products?category=dog_beds&top_n=2` | Top products per site |

Valid site keys: `amazon`, `walmart`, `chewy`, `petsmart`, `petco`, `target`, `tractor_supply`

---

## Chewy Scraper (ScraperAPI Only)

Chewy uses **ScraperAPI exclusively** — no Playwright. Add to `toptails/backend/.env`:

```env
SCRAPERAPI_KEY=your-key
CHEWY_SCRAPERAPI_ULTRA_PREMIUM=true
CHEWY_SCRAPERAPI_RENDER=false
CHEWY_SCRAPERAPI_TIMEOUT=120
```

Notes:

- Chewy requires **Ultra Premium** on most ScraperAPI plans (`ultra_premium=true`)
- Keep **`render=false`** — `render=true` often returns HTTP 500 for Chewy; product data is in SSR `__NEXT_DATA__`
- Each scrape costs credits and takes ~30-60 seconds
- Do not set `CHEWY_SCRAPERAPI_RENDER=true` unless ScraperAPI support advises it
- Prototype tester: `python testers/chewy_scraperapi.py`

Test Chewy scrape:

```bash
curl -X POST http://localhost:8000/scrape/run/chewy \
  -H "Content-Type: application/json" \
  -d '{"category": "dog_beds", "top_n": 2}'
```

Chewy, Target, and Walmart use **on-demand loading** in the frontend — products appear only after you click **Scrape site** for that retailer.

---

## Target Scraper (ScraperAPI Only)

Target uses **ScraperAPI exclusively** — no Playwright. Add to `toptails/backend/.env`:

```env
SCRAPERAPI_KEY=your-key
TARGET_SCRAPERAPI_ULTRA_PREMIUM=true
TARGET_SCRAPERAPI_RENDER=true
TARGET_SCRAPERAPI_TIMEOUT=180
TARGET_SCRAPERAPI_TRY_REDSKY=true
```

Notes:

- Unlike Chewy, Target PLPs need **`render=true`** — CSR product cards do not appear in plain HTML
- **Ultra Premium** is recommended (`ultra_premium=true`)
- Listing uses the **search URL** (`searchTerm=dog+bed`) — the category PLP often returns 0 relevant rows
- Product data is parsed from hydrated `ProductCard` markup (title, price, star ratings, images)
- Redsky API fallback runs when embedded JSON is empty (if an API key is found in page HTML)
- Each scrape costs credits and takes ~45-90 seconds
- Probe tiers locally: `python testers/target_scraperapi.py --compare`
- Prototype tester: `python testers/target_scraperapi.py --search --render --ultra`

Test Target scrape:

```bash
curl -X POST http://localhost:8000/scrape/run/target \
  -H "Content-Type: application/json" \
  -d '{"category": "dog_beds", "top_n": 2}'
```

---

## Walmart Scraper (ScraperAPI Only)

Walmart uses **ScraperAPI exclusively** — no Playwright. Add to `toptails/backend/.env`:

```env
SCRAPERAPI_KEY=your-key
WALMART_SCRAPERAPI_ULTRA_PREMIUM=true
WALMART_SCRAPERAPI_RENDER=false
WALMART_SCRAPERAPI_TIMEOUT=180
```

Notes:

- Product data is in SSR `__NEXT_DATA__` at `searchResult.itemStacks` — **keep `render=false`** (`render=true` often returns HTTP 500, same as Chewy)
- **Ultra Premium** is recommended (`ultra_premium=true`)
- Parser extracts `name`, `canonicalUrl`, `averageRating`, `numberOfReviews`, `priceInfo`, `imageInfo.thumbnailUrl`
- Sponsored tiles (`isSponsoredFlag`) are skipped; variants deduped by `catalogProductId`
- Each scrape costs credits and takes ~30-90 seconds
- Probe tiers: `python testers/walmart_scraperapi.py --compare`

Test Walmart scrape:

```bash
curl -X POST http://localhost:8000/scrape/run/walmart \
  -H "Content-Type: application/json" \
  -d '{"category": "dog_beds", "top_n": 2}'
```

---

## Amazon Scraper (ScraperAPI Only)

Amazon uses **ScraperAPI exclusively** — no Playwright. Add to `toptails/backend/.env`:

```env
SCRAPERAPI_KEY=your-key
AMAZON_SCRAPERAPI_USE_STRUCTURED=true
AMAZON_SCRAPERAPI_TLD=com
AMAZON_SCRAPERAPI_COUNTRY=us
AMAZON_SCRAPERAPI_TIMEOUT=180
```

Notes:

- Primary path is ScraperAPI's **structured Amazon search** endpoint (`/structured/amazon/search`) — returns JSON with `stars`, `total_reviews`, `price`, `asin`, `image`
- Generic HTML fallback uses `AMAZON_SCRAPERAPI_ULTRA_PREMIUM=true` and `AMAZON_SCRAPERAPI_RENDER=false` if structured is disabled or empty
- Sponsored listings are in a separate `ads[]` array — parser uses `results[]` only; HTML fallback skips `sspa`/`spons` URLs
- Variants deduped by ASIN (`variant_group_id`); product URLs canonicalized to `https://www.amazon.com/dp/{asin}`
- Each structured scrape costs ~5 ScraperAPI credits and takes ~30-60 seconds
- Probe tiers: `python testers/amazon_scraperapi.py --compare`

Test Amazon scrape:

```bash
curl -X POST http://localhost:8000/scrape/run/amazon \
  -H "Content-Type: application/json" \
  -d '{"category": "dog_beds", "top_n": 2}'
```

---

## Common Issues

| Problem | Fix |
|---------|-----|
| `DATABASE_URL` not set / connection refused | Ensure PostgreSQL is running; verify `.env` has `localhost:5432` |
| `python3-venv` not available | Use `--without-pip` bootstrap (see Step 3) or `apt install python3.12-venv` |
| `playwright` browser not found | Run `playwright install chromium` inside activated venv |
| Frontend shows "Backend unreachable" | Start backend first on port 8000 |
| Scrape returns 409 Conflict | Another scrape is running — wait or restart uvicorn |
| Amazon scrape returns 0 products | Use `AMAZON_SCRAPERAPI_USE_STRUCTURED=true` and verify `SCRAPERAPI_KEY` |
| Walmart scrape returns 0 products | Use `WALMART_SCRAPERAPI_ULTRA_PREMIUM=true` and `WALMART_SCRAPERAPI_RENDER=false` |
| Alembic `DuplicateTable` | Run `python scripts/alembic_sync.py` |
| Stale UI after clearing DB | Restart uvicorn if `clear_products.py` couldn't reach the API |
| `sudo` not available for DB setup | Use `postgres` superuser with `PGPASSWORD` (see Step 1) |
| Chewy scrape blocked / HTTP 500 | Use `CHEWY_SCRAPERAPI_ULTRA_PREMIUM=true` and `CHEWY_SCRAPERAPI_RENDER=false` |
| Target scrape returns 0 products | Use `TARGET_SCRAPERAPI_ULTRA_PREMIUM=true` and `TARGET_SCRAPERAPI_RENDER=true` |
| Target shows no ratings in top 2 | Products need `avg_rating >= 4.5` and `review_count >= 10`; unscored PLP rows are filtered out |

---

## Docker Alternative (optional)

For a full containerized stack instead of native setup:

```bash
cd toptails
cp .env.example .env
docker compose up --build
```

- Postgres on host port **5433** (not 5432)
- Backend: http://localhost:8000
- Frontend: http://localhost:3000

If using Docker DB with a native backend, set `DATABASE_URL=postgresql://user:password@localhost:5433/toptails` in `backend/.env`.

---

## Verified Setup (2026-06-10)

Initial setup was verified with:

- Python 3.12.3, Node v24.10.0, PostgreSQL on port 5432
- 41/41 backend tests passing
- Tractor Supply test scrape: 96 products scraped, 2 saved to DB in ~60 seconds
- Top product: FurHaven Plush and Suede Orthopedic Sofa Dog Bed (trust score 96%)
