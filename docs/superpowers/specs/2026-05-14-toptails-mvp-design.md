# TopTails MVP — Design Document

**Date:** 2026-05-14  
**Scope:** Dog beds · 7 sites · Top 2 per site (14 products total) · Manual trigger · No paid APIs  
**Status:** Approved

---

## 1. Overview

TopTails is a backend data pipeline that scrapes dog bed listings and reviews from 7 pet/retail websites, scores each product using a deterministic trust score formula, and surfaces the top 2 products per site via a FastAPI REST API. A minimal Next.js frontend serves as a placeholder for a future designer.

This is a **data pipeline project**, not a UI project. The frontend is intentionally thin.

---

## 2. Architecture

### System Components

```
[Playwright Scrapers] → [Raw Product Data] → [Trust Score Engine] → [PostgreSQL]
                                                                          ↓
                                                              [FastAPI REST API]
                                                                          ↓
                                                            [Next.js Frontend (placeholder)]
```

### Monorepo Layout

```
toptails/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app entry
│   │   ├── api/
│   │   │   └── routes.py            # POST /scrape/run, GET /products, GET /health
│   │   ├── scrapers/
│   │   │   ├── base.py              # Abstract scraper interface
│   │   │   ├── amazon.py            # ⚠️ High bot-detection risk
│   │   │   ├── walmart.py           # ⚠️ High bot-detection risk
│   │   │   ├── chewy.py
│   │   │   ├── petsmart.py
│   │   │   ├── petco.py
│   │   │   ├── target.py
│   │   │   └── tractor_supply.py
│   │   ├── scoring/
│   │   │   ├── trust_score.py       # Core formula
│   │   │   └── ranker.py            # Sorts and returns top N per site
│   │   ├── models/
│   │   │   └── product.py           # SQLAlchemy ORM models
│   │   └── db/
│   │       └── session.py           # PostgreSQL connection + session factory
│   ├── tests/
│   │   ├── test_scrapers.py
│   │   └── test_scoring.py
│   ├── alembic/                     # DB migrations
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   └── page.tsx             # Single page, calls /products
│   │   └── components/
│   │       └── ProductCard.tsx      # Minimal card — designer replaces this
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
└── .env.example
```

### Runtime

- **Local Docker only** for MVP
- Services: `db` (Postgres 16), `backend` (FastAPI), `frontend` (Next.js)
- Package manager: `pip + requirements.txt`

---

## 3. Data Schema

### PostgreSQL — `products` table

```sql
CREATE TABLE products (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_site           VARCHAR(50) NOT NULL,       -- 'amazon', 'chewy', etc.
    category              VARCHAR(50) NOT NULL,       -- 'dog_beds' for MVP
    title                 TEXT NOT NULL,
    price                 NUMERIC(10,2),
    product_url           TEXT,
    image_url             TEXT,
    avg_rating            NUMERIC(3,2),               -- e.g. 4.3
    review_count          INTEGER,
    verified_ratio        NUMERIC(4,3),               -- 0.0 to 1.0
    five_star_ratio       NUMERIC(4,3),               -- for spike detection
    rating_distribution   JSONB,                      -- {1:n, 2:n, 3:n, 4:n, 5:n}
    review_dates          JSONB,                      -- list of ISO date strings
    trust_score           NUMERIC(5,4),               -- computed, 0.0 to 1.0
    scrape_status         VARCHAR(20) DEFAULT 'ok',   -- 'ok' | 'blocked' | 'partial'
    scrape_notes          TEXT,                       -- human-readable failure reason
    scraped_at            TIMESTAMPTZ DEFAULT NOW()
);
```

`scrape_status` and `scrape_notes` surface Amazon/Walmart blocking cleanly — failures are transparent, not silent.

### Python Model — `ProductRaw` (internal pipeline DTO)

Used by scrapers before DB persistence. Mirrors the DB schema but is a plain Pydantic model, not ORM. This keeps scrapers decoupled from the DB layer.

---

## 4. Scraper Architecture

### 4a. Base Interface (`scrapers/base.py`)

```python
class BaseScraper(ABC):
    SITE_NAME: str
    CATEGORY = "dog_beds"

    @abstractmethod
    async def fetch_listings(self, query: str = "dog bed", limit: int = 20) -> List[ProductRaw]:
        pass

    async def run(self) -> List[ProductRaw]:
        try:
            return await self.fetch_listings()
        except Exception as e:
            return [ProductRaw(
                source_site=self.SITE_NAME,
                scrape_status="blocked",
                scrape_notes=str(e)
            )]
```

Each scraper is independently swappable — replacing `amazon.py` with an API-based implementation requires no changes elsewhere.

### 4b. Playwright Setup (all scrapers)

- `playwright-stealth` applied on all instances
- Randomized viewport, user-agent, locale per session
- Human-like delays: `asyncio.sleep(random.uniform(1.5, 3.5))` between page actions

### 4c. Per-Site Strategy

| Site | Approach | Risk |
|---|---|---|
| Amazon | Playwright stealth, search + review modal | 🔴 High |
| Walmart | Playwright stealth, search + review overlay | 🔴 High |
| Chewy | Playwright, search + review tab | 🟡 Medium |
| Petsmart | Playwright, search + product detail pages | 🟡 Medium |
| Petco | Playwright, search + review section | 🟡 Medium |
| Target | Playwright, search + Target review API endpoint | 🟢 Low |
| Tractor Supply | Playwright, simple product pages | 🟢 Low |

### 4d. Data Collected Per Product (top 20 per site before scoring)

- Title, price, product URL, image URL
- Average rating, total review count
- Rating distribution (1–5 star counts)
- Review dates (last 30 reviews minimum — for velocity detection)
- Verified purchase flag per review where available

### 4e. Amazon + Walmart Failure Handling

Both files include a prominent warning block:

```python
# ⚠️  SCRAPING RISK: Amazon/Walmart use aggressive bot detection including
# fingerprinting, CAPTCHA, and IP rate limiting. This scraper may fail in
# production without residential proxies or official API access.
# FUTURE: Replace with Amazon Product Advertising API / Walmart Affiliate API.
# scrape_status will be set to 'blocked' on failure — this surfaces in API response.
```

---

## 5. Trust Score Formula

```
trust_score = avg_rating_normalized
            × volume_weight
            × distribution_penalty
            × verified_bonus
            × velocity_penalty
```

Clamped to `[0.0, 1.0]`. Products with `volume_weight == 0.0` (< 15 reviews) are excluded from ranking entirely.

### Components

**`avg_rating_normalized`** = `avg_rating / 5.0`

**`volume_weight`** — sigmoid, soft floor at 15 reviews, hard filter below:
```python
def volume_weight(review_count: int) -> float:
    if review_count < 15:
        return 0.0  # hard filter
    return 1 / (1 + math.exp(-0.05 * (review_count - 50)))
    # ~0.38 at 15, ~0.62 at 50, ~0.88 at 100, ~1.0 at 200+
```

**`distribution_penalty`** — flags suspiciously uniform high ratings:
```python
def distribution_penalty(five_star_ratio: float) -> float:
    if five_star_ratio > 0.90: return 0.5    # likely fake
    if five_star_ratio > 0.80: return 0.75   # suspicious
    return 1.0
```

**`verified_bonus`** — rewards verified purchases (1.0x → 1.3x):
```python
def verified_bonus(verified_ratio: float) -> float:
    return 1.0 + (0.3 * verified_ratio)
```

**`velocity_penalty`** — flags review burst spam:
```python
def velocity_penalty(review_dates: list[str]) -> float:
    # >30% of all reviews in any 3-day window → 0.6 penalty
    # Otherwise → 1.0
    # Defaults to 1.0 if review_dates unavailable
```

---

## 6. API Endpoints

### `POST /scrape/run`

Triggers scraping pipeline for all 7 sites concurrently. **Fire-and-forget in MVP** — there is no job status endpoint. The `job_id` is a UUID logged server-side for traceability; clients poll `GET /products` to see results once scraping completes. No jobs table is created for MVP.

**Request:**
```json
{ "category": "dog_beds", "top_n": 2 }
```

**Response:**
```json
{
  "job_id": "uuid",
  "status": "running",
  "sites_queued": ["amazon", "chewy", "walmart", "petsmart", "petco", "target", "tractor_supply"]
}
```

### `GET /products?category=dog_beds&top_n=2`

Returns top N products per site, sorted by trust score descending.

**Response:**
```json
{
  "category": "dog_beds",
  "generated_at": "2026-05-14T...",
  "results": [
    {
      "site": "chewy",
      "scrape_status": "ok",
      "scrape_notes": null,
      "top_products": [
        {
          "title": "...",
          "price": 49.99,
          "avg_rating": 4.6,
          "review_count": 312,
          "trust_score": 0.847,
          "product_url": "...",
          "image_url": "..."
        }
      ]
    },
    {
      "site": "amazon",
      "scrape_status": "blocked",
      "scrape_notes": "CAPTCHA encountered on search results page.",
      "top_products": []
    }
  ]
}
```

### `GET /health`

Returns service status + last scrape timestamp.

---

## 7. Frontend (Placeholder)

**Purpose:** Prove the API works. Designer rebuilds later.

- Framework: Next.js with Tailwind CSS
- Single page (`/`) calls `GET /products?category=dog_beds&top_n=2` on load
- Renders one section per site
- Shows `scrape_status` — `blocked` shows "Site unavailable" state (not an error)
- `ProductCard`: image, title, price, rating, trust_score badge, link
- Neutral grays only via CSS variables — no hardcoded colors

---

## 8. Environment Variables

```
DATABASE_URL=postgresql://user:password@localhost:5432/toptails
PLAYWRIGHT_HEADLESS=true
LOG_LEVEL=INFO
```

No API keys required — fully self-hosted.

---

## 9. Docker Compose

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: toptails
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
    ports:
      - "5432:5432"

  backend:
    build: ./backend
    depends_on: [db]
    env_file: .env
    ports:
      - "8000:8000"

  frontend:
    build: ./frontend
    depends_on: [backend]
    ports:
      - "3000:3000"
```

---

## 10. Build Order

| Task | Scope |
|---|---|
| **Task 1 — Scaffold & DB** | Monorepo structure, Docker Compose, PostgreSQL, SQLAlchemy models, FastAPI shell + `/health`, Alembic migrations |
| **Task 2 — Base Scraper + 2 Low-Risk Sites** | `base.py`, `tractor_supply.py`, `target.py`, Playwright stealth setup, raw data validation |
| **Task 3 — Remaining Scrapers** | `chewy.py`, `petsmart.py`, `petco.py`, `amazon.py`, `walmart.py` with failure handling, concurrent runner |
| **Task 4 — Scoring Engine** | `trust_score.py`, `ranker.py`, unit tests (edge cases: <15 reviews, 100% five-star, velocity burst) |
| **Task 5 — API Layer** | `POST /scrape/run`, `GET /products`, wire scoring into pipeline |
| **Task 6 — Frontend Placeholder** | Next.js scaffold, single page, `ProductCard`, blocked/partial state |

---

## 11. Known Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Amazon scraper blocked | Very High | Document in code + API response; recommend Product Advertising API for V2 |
| Walmart scraper blocked | High | Document in code; Walmart Affiliate API is upgrade path |
| Chewy scraper breaks | Medium | Rebuild on break — no API alternative currently |
| Review dates unavailable | Medium | `velocity_penalty` defaults to `1.0` if dates missing |
| Price/title format variance | Low | Normalizer layer in `base.py` post-fetch |

**Client note (Abhishek):** Amazon and Walmart are best-effort for MVP. The system clearly reports when they're blocked via `scrape_status: "blocked"`. Both sites have good official APIs — clean upgrade path once budget allows.

---

## 12. Testing Strategy

- **Scoring unit tests:** Edge cases: fewer than 15 reviews (hard-filtered), 100% five-star rating (0.5 penalty), review velocity burst (0.6 penalty), missing review dates (default 1.0)
- **Scraper tests:** Mock Playwright responses; verify raw data shape matches `ProductRaw` schema
- **No integration tests in MVP** — scrapers are tested against live sites manually on first run
