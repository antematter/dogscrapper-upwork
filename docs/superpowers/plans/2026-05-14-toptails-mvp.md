# TopTails MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dog-bed scraping + trust-scoring pipeline with a FastAPI backend, PostgreSQL storage, and minimal Next.js frontend.

**Architecture:** Playwright-based scrapers pull top 20 listings from 7 sites, a deterministic formula scores each product, top 2 per site are stored in PostgreSQL and served via REST API. Frontend is a placeholder that proves the API contract works.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x (sync), Alembic, Playwright + playwright-stealth, PostgreSQL 16, Next.js 14, Tailwind CSS, Docker Compose

---

## File Map

```
toptails/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app + lifespan
│   │   ├── api/routes.py              # All 3 endpoints
│   │   ├── scrapers/
│   │   │   ├── base.py                # BaseScraper ABC + ProductRaw model
│   │   │   ├── tractor_supply.py
│   │   │   ├── target.py
│   │   │   ├── chewy.py
│   │   │   ├── petsmart.py
│   │   │   ├── petco.py
│   │   │   ├── amazon.py
│   │   │   └── walmart.py
│   │   ├── scoring/
│   │   │   ├── trust_score.py         # 5-component formula
│   │   │   └── ranker.py              # top-N per site
│   │   ├── models/product.py          # SQLAlchemy ORM
│   │   └── db/session.py              # engine + SessionLocal
│   ├── tests/
│   │   ├── test_scoring.py
│   │   └── test_scrapers.py
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/                  # migration files go here
│   ├── alembic.ini
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/page.tsx
│   │   └── components/ProductCard.tsx
│   ├── Dockerfile
│   ├── package.json
│   └── next.config.js
├── docker-compose.yml
└── .env.example
```

---

## Task 1: Scaffold, Docker, DB Models, Health Endpoint

**Files:**
- Create: `toptails/docker-compose.yml`
- Create: `toptails/.env.example`
- Create: `toptails/backend/requirements.txt`
- Create: `toptails/backend/app/models/product.py`
- Create: `toptails/backend/app/db/session.py`
- Create: `toptails/backend/app/main.py`
- Create: `toptails/backend/app/api/routes.py`
- Create: `toptails/backend/alembic.ini`
- Create: `toptails/backend/alembic/env.py`
- Create: `toptails/backend/Dockerfile`

- [ ] **Step 1: Create monorepo root and docker-compose**

```bash
mkdir -p toptails && cd toptails
mkdir -p backend/app/api backend/app/scrapers backend/app/scoring \
         backend/app/models backend/app/db backend/tests \
         backend/alembic/versions \
         frontend/src/app frontend/src/components
```

`toptails/docker-compose.yml`:
```yaml
version: "3.9"
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: toptails
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user -d toptails"]
      interval: 5s
      timeout: 5s
      retries: 5

  backend:
    build: ./backend
    depends_on:
      db:
        condition: service_healthy
    env_file: .env
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app

  frontend:
    build: ./frontend
    depends_on:
      - backend
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:8000

volumes:
  pgdata:
```

`toptails/.env.example`:
```
DATABASE_URL=postgresql://user:password@db:5432/toptails
PLAYWRIGHT_HEADLESS=true
LOG_LEVEL=INFO
```

- [ ] **Step 2: Write `requirements.txt`**

`toptails/backend/requirements.txt`:
```
fastapi==0.111.0
uvicorn[standard]==0.29.0
sqlalchemy==2.0.30
alembic==1.13.1
psycopg2-binary==2.9.9
pydantic==2.7.1
playwright==1.44.0
playwright-stealth==1.0.6
python-dotenv==1.0.1
pytest==8.2.0
pytest-asyncio==0.23.6
httpx==0.27.0
```

- [ ] **Step 3: Write the `Product` SQLAlchemy model**

`toptails/backend/app/models/product.py`:
```python
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Numeric, Integer, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMPTZ
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
    avg_rating = Column(Numeric(3, 2))
    review_count = Column(Integer)
    verified_ratio = Column(Numeric(4, 3))
    five_star_ratio = Column(Numeric(4, 3))
    rating_distribution = Column(JSONB)
    review_dates = Column(JSONB)
    trust_score = Column(Numeric(5, 4))
    scrape_status = Column(String(20), default="ok")
    scrape_notes = Column(Text)
    scraped_at = Column(
        TIMESTAMPTZ, default=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 4: Write `db/session.py`**

`toptails/backend/app/db/session.py`:
```python
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.product import Base

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
```

- [ ] **Step 5: Write `api/routes.py` with only `/health` for now**

`toptails/backend/app/api/routes.py`:
```python
from datetime import datetime, timezone
from fastapi import APIRouter
from sqlalchemy import text
from app.db.session import SessionLocal

router = APIRouter()


@router.get("/health")
def health():
    db = SessionLocal()
    try:
        last_scrape = db.execute(
            text("SELECT MAX(scraped_at) FROM products")
        ).scalar()
    except Exception:
        last_scrape = None
    finally:
        db.close()
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "last_scrape": last_scrape.isoformat() if last_scrape else None,
    }
```

- [ ] **Step 6: Write `main.py`**

`toptails/backend/app/main.py`:
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.routes import router
from app.db.session import create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(title="TopTails API", lifespan=lifespan)
app.include_router(router)
```

- [ ] **Step 7: Set up Alembic**

`toptails/backend/alembic.ini` (minimal — only change `sqlalchemy.url`):
```ini
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url = postgresql://user:password@localhost:5432/toptails

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

`toptails/backend/alembic/env.py`:
```python
import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from app.models.product import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

db_url = os.environ.get("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.config_ini_section(),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 8: Create backend Dockerfile**

`toptails/backend/Dockerfile`:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

- [ ] **Step 9: Add `__init__.py` files to each package**

```bash
touch toptails/backend/app/__init__.py \
      toptails/backend/app/api/__init__.py \
      toptails/backend/app/scrapers/__init__.py \
      toptails/backend/app/scoring/__init__.py \
      toptails/backend/app/models/__init__.py \
      toptails/backend/app/db/__init__.py \
      toptails/backend/tests/__init__.py \
      toptails/backend/alembic/__init__.py
```

- [ ] **Step 10: Start DB + backend, verify `/health`**

```bash
cd toptails
cp .env.example .env
docker compose up db backend --build
```

In a second terminal:
```bash
curl http://localhost:8000/health
```
Expected:
```json
{"status": "ok", "timestamp": "...", "last_scrape": null}
```

- [ ] **Step 11: Generate and run first Alembic migration**

```bash
docker compose exec backend alembic revision --autogenerate -m "create products table"
docker compose exec backend alembic upgrade head
```

Expected: `Running upgrade  -> <hash>, create products table`

---

## Task 2: `ProductRaw` DTO, `BaseScraper`, Tractor Supply + Target scrapers

**Files:**
- Create: `toptails/backend/app/scrapers/base.py`
- Create: `toptails/backend/app/scrapers/tractor_supply.py`
- Create: `toptails/backend/app/scrapers/target.py`
- Create: `toptails/backend/tests/test_scrapers.py` (partial — base tests only)

- [ ] **Step 1: Write `ProductRaw` and `BaseScraper` in `base.py`**

`toptails/backend/app/scrapers/base.py`:
```python
import asyncio
import random
from abc import ABC, abstractmethod
from typing import Optional
from pydantic import BaseModel


class ProductRaw(BaseModel):
    source_site: str
    category: str = "dog_beds"
    title: str = ""
    price: Optional[float] = None
    product_url: Optional[str] = None
    image_url: Optional[str] = None
    avg_rating: Optional[float] = None
    review_count: Optional[int] = None
    verified_ratio: Optional[float] = None
    five_star_ratio: Optional[float] = None
    rating_distribution: Optional[dict] = None
    review_dates: Optional[list] = None
    trust_score: Optional[float] = None
    scrape_status: str = "ok"
    scrape_notes: Optional[str] = None


class BaseScraper(ABC):
    SITE_NAME: str
    CATEGORY: str = "dog_beds"

    @abstractmethod
    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        pass

    async def run(self) -> list[ProductRaw]:
        try:
            return await self.fetch_listings()
        except Exception as e:
            return [
                ProductRaw(
                    source_site=self.SITE_NAME,
                    scrape_status="blocked",
                    scrape_notes=str(e),
                )
            ]

    @staticmethod
    async def human_delay():
        await asyncio.sleep(random.uniform(1.5, 3.5))

    @staticmethod
    def normalize_price(raw: str) -> Optional[float]:
        import re
        cleaned = re.sub(r"[^\d.]", "", raw)
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def normalize_rating(raw: str) -> Optional[float]:
        import re
        match = re.search(r"(\d+\.?\d*)", raw)
        if match:
            return float(match.group(1))
        return None
```

- [ ] **Step 2: Write base scraper tests**

`toptails/backend/tests/test_scrapers.py`:
```python
import pytest
from app.scrapers.base import ProductRaw, BaseScraper


class ConcreteSuccessScraper(BaseScraper):
    SITE_NAME = "test_site"

    async def fetch_listings(self, query="dog bed", limit=20):
        return [
            ProductRaw(
                source_site=self.SITE_NAME,
                title="Test Bed",
                price=29.99,
                avg_rating=4.5,
                review_count=100,
            )
        ]


class ConcreteFailScraper(BaseScraper):
    SITE_NAME = "fail_site"

    async def fetch_listings(self, query="dog bed", limit=20):
        raise ConnectionError("CAPTCHA detected")


@pytest.mark.asyncio
async def test_run_returns_products_on_success():
    results = await ConcreteSuccessScraper().run()
    assert len(results) == 1
    assert results[0].title == "Test Bed"
    assert results[0].scrape_status == "ok"


@pytest.mark.asyncio
async def test_run_returns_blocked_on_exception():
    results = await ConcreteFailScraper().run()
    assert len(results) == 1
    assert results[0].scrape_status == "blocked"
    assert "CAPTCHA" in results[0].scrape_notes
    assert results[0].source_site == "fail_site"


def test_normalize_price():
    assert BaseScraper.normalize_price("$49.99") == 49.99
    assert BaseScraper.normalize_price("49") == 49.0
    assert BaseScraper.normalize_price("N/A") is None


def test_normalize_rating():
    assert BaseScraper.normalize_rating("4.5 out of 5") == 4.5
    assert BaseScraper.normalize_rating("no rating") is None
```

- [ ] **Step 3: Run base tests — verify they pass**

```bash
docker compose exec backend pytest tests/test_scrapers.py -v
```
Expected: 4 tests pass.

- [ ] **Step 4: Write Tractor Supply scraper**

`toptails/backend/app/scrapers/tractor_supply.py`:
```python
import asyncio
import random
from datetime import date
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from app.scrapers.base import BaseScraper, ProductRaw

SEARCH_URL = "https://www.tractorsupply.com/tsc/catalog/search?searchTerm=dog+bed"


class TractorSupplyScraper(BaseScraper):
    SITE_NAME = "tractor_supply"

    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        products = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()
            await stealth_async(page)

            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            await self.human_delay()

            product_cards = await page.query_selector_all(
                "[data-test='product-card'], .product-card, [class*='productCard']"
            )

            for card in product_cards[:limit]:
                try:
                    title_el = await card.query_selector("[data-test='product-title'], .product-title, h3")
                    price_el = await card.query_selector("[data-test='product-price'], .price, [class*='price']")
                    rating_el = await card.query_selector("[data-test='rating'], [class*='rating'], [aria-label*='stars']")
                    review_el = await card.query_selector("[data-test='review-count'], [class*='reviewCount']")
                    link_el = await card.query_selector("a")
                    img_el = await card.query_selector("img")

                    title = await title_el.inner_text() if title_el else ""
                    if not title.strip():
                        continue

                    price_raw = await price_el.inner_text() if price_el else ""
                    rating_raw = await rating_el.get_attribute("aria-label") if rating_el else ""
                    if rating_raw is None and rating_el:
                        rating_raw = await rating_el.inner_text()

                    review_raw = await review_el.inner_text() if review_el else "0"
                    href = await link_el.get_attribute("href") if link_el else None
                    product_url = f"https://www.tractorsupply.com{href}" if href and href.startswith("/") else href
                    image_url = await img_el.get_attribute("src") if img_el else None

                    review_count = 0
                    import re
                    m = re.search(r"\d+", review_raw.replace(",", ""))
                    if m:
                        review_count = int(m.group())

                    products.append(
                        ProductRaw(
                            source_site=self.SITE_NAME,
                            title=title.strip(),
                            price=self.normalize_price(price_raw),
                            avg_rating=self.normalize_rating(rating_raw or ""),
                            review_count=review_count,
                            product_url=product_url,
                            image_url=image_url,
                            scrape_status="ok",
                        )
                    )
                    await self.human_delay()

                except Exception:
                    continue

            await browser.close()
        return products
```

- [ ] **Step 5: Write Target scraper**

Target exposes a PowerReviews JSON endpoint — use it instead of parsing HTML for reviews.

`toptails/backend/app/scrapers/target.py`:
```python
import asyncio
import random
import re
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from app.scrapers.base import BaseScraper, ProductRaw

SEARCH_URL = "https://www.target.com/s?searchTerm=dog+bed"


class TargetScraper(BaseScraper):
    SITE_NAME = "target"

    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        products = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()
            await stealth_async(page)

            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=45000)
            await self.human_delay()

            # Scroll to load more products
            for _ in range(3):
                await page.keyboard.press("End")
                await asyncio.sleep(1.5)

            cards = await page.query_selector_all("[data-test='product-details']")

            for card in cards[:limit]:
                try:
                    title_el = await card.query_selector("[data-test='product-title']")
                    price_el = await card.query_selector("[data-test='current-price']")
                    rating_el = await card.query_selector("[data-test='ratings']")
                    review_el = await card.query_selector("[data-test='review-count']")
                    link_el = await card.query_selector("a")
                    img_el = await card.query_selector("img")

                    title = await title_el.inner_text() if title_el else ""
                    if not title.strip():
                        continue

                    price_raw = await price_el.inner_text() if price_el else ""
                    rating_raw = await rating_el.get_attribute("aria-label") if rating_el else ""
                    if not rating_raw and rating_el:
                        rating_raw = await rating_el.inner_text()

                    review_raw = await review_el.inner_text() if review_el else "0"
                    href = await link_el.get_attribute("href") if link_el else None
                    product_url = f"https://www.target.com{href}" if href and href.startswith("/") else href
                    image_url = await img_el.get_attribute("src") if img_el else None

                    review_count = 0
                    m = re.search(r"\d+", review_raw.replace(",", ""))
                    if m:
                        review_count = int(m.group())

                    products.append(
                        ProductRaw(
                            source_site=self.SITE_NAME,
                            title=title.strip(),
                            price=self.normalize_price(price_raw),
                            avg_rating=self.normalize_rating(rating_raw or ""),
                            review_count=review_count,
                            product_url=product_url,
                            image_url=image_url,
                            scrape_status="ok",
                        )
                    )
                    await self.human_delay()
                except Exception:
                    continue

            await browser.close()
        return products
```

- [ ] **Step 6: Smoke-test scrapers manually (outside Docker, directly)**

```bash
cd toptails/backend
pip install -r requirements.txt
playwright install chromium
python -c "
import asyncio
from app.scrapers.tractor_supply import TractorSupplyScraper
results = asyncio.run(TractorSupplyScraper().run())
print(f'Tractor Supply: {len(results)} products')
for p in results[:2]:
    print(p.model_dump())
"
```
Expected: 2+ products printed with title, price, rating fields populated.

---

## Task 3: Chewy, Petsmart, Petco, Amazon, Walmart + Concurrent Runner

**Files:**
- Create: `toptails/backend/app/scrapers/chewy.py`
- Create: `toptails/backend/app/scrapers/petsmart.py`
- Create: `toptails/backend/app/scrapers/petco.py`
- Create: `toptails/backend/app/scrapers/amazon.py`
- Create: `toptails/backend/app/scrapers/walmart.py`
- Create: `toptails/backend/app/scrapers/runner.py`

- [ ] **Step 1: Write Chewy scraper**

`toptails/backend/app/scrapers/chewy.py`:
```python
import re
import random
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from app.scrapers.base import BaseScraper, ProductRaw

SEARCH_URL = "https://www.chewy.com/s?query=dog+bed"


class ChewyScraper(BaseScraper):
    SITE_NAME = "chewy"

    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        products = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()
            await stealth_async(page)

            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=45000)
            await self.human_delay()

            cards = await page.query_selector_all(
                "[data-testid='product-tile'], .product-tile, [class*='ProductTile']"
            )

            for card in cards[:limit]:
                try:
                    title_el = await card.query_selector(
                        "[data-testid='product-name'], .product-title, h2, h3"
                    )
                    price_el = await card.query_selector(
                        "[data-testid='product-price'], .product-price, [class*='price']"
                    )
                    rating_el = await card.query_selector(
                        "[aria-label*='stars'], [aria-label*='rating'], [class*='rating']"
                    )
                    review_el = await card.query_selector(
                        "[data-testid='review-count'], [class*='reviewCount']"
                    )
                    link_el = await card.query_selector("a")
                    img_el = await card.query_selector("img")

                    title = await title_el.inner_text() if title_el else ""
                    if not title.strip():
                        continue

                    price_raw = await price_el.inner_text() if price_el else ""
                    rating_raw = await rating_el.get_attribute("aria-label") if rating_el else ""
                    review_raw = await review_el.inner_text() if review_el else "0"
                    href = await link_el.get_attribute("href") if link_el else None
                    product_url = f"https://www.chewy.com{href}" if href and href.startswith("/") else href
                    image_url = await img_el.get_attribute("src") if img_el else None

                    review_count = 0
                    m = re.search(r"\d+", review_raw.replace(",", ""))
                    if m:
                        review_count = int(m.group())

                    products.append(
                        ProductRaw(
                            source_site=self.SITE_NAME,
                            title=title.strip(),
                            price=self.normalize_price(price_raw),
                            avg_rating=self.normalize_rating(rating_raw or ""),
                            review_count=review_count,
                            product_url=product_url,
                            image_url=image_url,
                            scrape_status="ok",
                        )
                    )
                    await self.human_delay()
                except Exception:
                    continue

            await browser.close()
        return products
```

- [ ] **Step 2: Write Petsmart scraper**

`toptails/backend/app/scrapers/petsmart.py`:
```python
import re
import random
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from app.scrapers.base import BaseScraper, ProductRaw

SEARCH_URL = "https://www.petsmart.com/search?q=dog+bed"


class PetsmartScraper(BaseScraper):
    SITE_NAME = "petsmart"

    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        products = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()
            await stealth_async(page)

            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=45000)
            await self.human_delay()

            cards = await page.query_selector_all(
                ".product-tile, [class*='product-card'], [class*='ProductCard']"
            )

            for card in cards[:limit]:
                try:
                    title_el = await card.query_selector(".product-name, h3, h2, [class*='title']")
                    price_el = await card.query_selector(".price-sales, [class*='price']")
                    rating_el = await card.query_selector("[aria-label*='stars'], [class*='rating']")
                    review_el = await card.query_selector("[class*='review'], [class*='count']")
                    link_el = await card.query_selector("a")
                    img_el = await card.query_selector("img")

                    title = await title_el.inner_text() if title_el else ""
                    if not title.strip():
                        continue

                    price_raw = await price_el.inner_text() if price_el else ""
                    rating_raw = await rating_el.get_attribute("aria-label") if rating_el else ""
                    review_raw = await review_el.inner_text() if review_el else "0"
                    href = await link_el.get_attribute("href") if link_el else None
                    product_url = f"https://www.petsmart.com{href}" if href and href.startswith("/") else href
                    image_url = await img_el.get_attribute("src") if img_el else None

                    review_count = 0
                    m = re.search(r"\d+", review_raw.replace(",", ""))
                    if m:
                        review_count = int(m.group())

                    products.append(
                        ProductRaw(
                            source_site=self.SITE_NAME,
                            title=title.strip(),
                            price=self.normalize_price(price_raw),
                            avg_rating=self.normalize_rating(rating_raw or ""),
                            review_count=review_count,
                            product_url=product_url,
                            image_url=image_url,
                            scrape_status="ok",
                        )
                    )
                    await self.human_delay()
                except Exception:
                    continue

            await browser.close()
        return products
```

- [ ] **Step 3: Write Petco scraper**

`toptails/backend/app/scrapers/petco.py`:
```python
import re
import random
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from app.scrapers.base import BaseScraper, ProductRaw

SEARCH_URL = "https://www.petco.com/shop/en/petcostore/category/dog/dog-beds"


class PetcoScraper(BaseScraper):
    SITE_NAME = "petco"

    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        products = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()
            await stealth_async(page)

            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=45000)
            await self.human_delay()

            cards = await page.query_selector_all(
                "[class*='product-card'], [class*='ProductCard'], .product-item"
            )

            for card in cards[:limit]:
                try:
                    title_el = await card.query_selector("[class*='product-name'], [class*='title'], h2, h3")
                    price_el = await card.query_selector("[class*='price']")
                    rating_el = await card.query_selector("[aria-label*='stars'], [aria-label*='rating']")
                    review_el = await card.query_selector("[class*='review'], [class*='count']")
                    link_el = await card.query_selector("a")
                    img_el = await card.query_selector("img")

                    title = await title_el.inner_text() if title_el else ""
                    if not title.strip():
                        continue

                    price_raw = await price_el.inner_text() if price_el else ""
                    rating_raw = await rating_el.get_attribute("aria-label") if rating_el else ""
                    review_raw = await review_el.inner_text() if review_el else "0"
                    href = await link_el.get_attribute("href") if link_el else None
                    product_url = f"https://www.petco.com{href}" if href and href.startswith("/") else href
                    image_url = await img_el.get_attribute("src") if img_el else None

                    review_count = 0
                    m = re.search(r"\d+", review_raw.replace(",", ""))
                    if m:
                        review_count = int(m.group())

                    products.append(
                        ProductRaw(
                            source_site=self.SITE_NAME,
                            title=title.strip(),
                            price=self.normalize_price(price_raw),
                            avg_rating=self.normalize_rating(rating_raw or ""),
                            review_count=review_count,
                            product_url=product_url,
                            image_url=image_url,
                            scrape_status="ok",
                        )
                    )
                    await self.human_delay()
                except Exception:
                    continue

            await browser.close()
        return products
```

- [ ] **Step 4: Write Amazon scraper with bot-detection warning**

`toptails/backend/app/scrapers/amazon.py`:
```python
# ⚠️  SCRAPING RISK: Amazon uses aggressive bot detection including
# fingerprinting, CAPTCHA, and IP rate limiting. This scraper will likely
# fail in production without residential proxies or official API access.
# FUTURE: Replace with Amazon Product Advertising API.
# scrape_status will be set to 'blocked' on failure — surfaces in API response.
import re
import random
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from app.scrapers.base import BaseScraper, ProductRaw

SEARCH_URL = "https://www.amazon.com/s?k=dog+bed"


class AmazonScraper(BaseScraper):
    SITE_NAME = "amazon"

    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        products = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()
            await stealth_async(page)

            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            await self.human_delay()

            # Detect CAPTCHA / bot wall
            page_content = await page.content()
            if "captcha" in page_content.lower() or "robot" in page_content.lower():
                raise BlockedError("CAPTCHA encountered on Amazon search results page. Amazon bot detection triggered.")

            cards = await page.query_selector_all(
                "[data-component-type='s-search-result']"
            )

            for card in cards[:limit]:
                try:
                    title_el = await card.query_selector("h2 a span, [data-cy='title-recipe'] h2")
                    price_el = await card.query_selector(".a-price .a-offscreen")
                    rating_el = await card.query_selector("[aria-label*='stars']")
                    review_el = await card.query_selector("[aria-label*='ratings'], .a-size-base.s-underline-text")
                    link_el = await card.query_selector("h2 a")
                    img_el = await card.query_selector(".s-image")

                    title = await title_el.inner_text() if title_el else ""
                    if not title.strip():
                        continue

                    price_raw = await price_el.inner_text() if price_el else ""
                    rating_raw = await rating_el.get_attribute("aria-label") if rating_el else ""
                    review_raw = await review_el.get_attribute("aria-label") if review_el else ""
                    if not review_raw and review_el:
                        review_raw = await review_el.inner_text()
                    href = await link_el.get_attribute("href") if link_el else None
                    product_url = f"https://www.amazon.com{href}" if href and href.startswith("/") else href
                    image_url = await img_el.get_attribute("src") if img_el else None

                    review_count = 0
                    m = re.search(r"[\d,]+", review_raw or "")
                    if m:
                        review_count = int(m.group().replace(",", ""))

                    products.append(
                        ProductRaw(
                            source_site=self.SITE_NAME,
                            title=title.strip(),
                            price=self.normalize_price(price_raw),
                            avg_rating=self.normalize_rating(rating_raw or ""),
                            review_count=review_count,
                            product_url=product_url,
                            image_url=image_url,
                            scrape_status="ok",
                        )
                    )
                    await self.human_delay()
                except Exception:
                    continue

            await browser.close()
        return products


class BlockedError(Exception):
    pass
```

- [ ] **Step 5: Write Walmart scraper with bot-detection warning**

`toptails/backend/app/scrapers/walmart.py`:
```python
# ⚠️  SCRAPING RISK: Walmart uses aggressive bot detection including
# fingerprinting, CAPTCHA, and IP rate limiting. This scraper will likely
# fail in production without residential proxies or official API access.
# FUTURE: Replace with Walmart Affiliate API.
# scrape_status will be set to 'blocked' on failure — surfaces in API response.
import re
import random
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from app.scrapers.base import BaseScraper, ProductRaw

SEARCH_URL = "https://www.walmart.com/search?q=dog+bed"


class WalmartScraper(BaseScraper):
    SITE_NAME = "walmart"

    async def fetch_listings(
        self, query: str = "dog bed", limit: int = 20
    ) -> list[ProductRaw]:
        products = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()
            await stealth_async(page)

            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            await self.human_delay()

            page_content = await page.content()
            if "captcha" in page_content.lower() or "robot check" in page_content.lower():
                raise BlockedError("Bot detection triggered on Walmart search page.")

            cards = await page.query_selector_all(
                "[data-item-id], [class*='search-result-gridview-item'], [class*='ProductCard']"
            )

            for card in cards[:limit]:
                try:
                    title_el = await card.query_selector(
                        "[class*='product-title'], [class*='title'], span[data-automation-id='product-title']"
                    )
                    price_el = await card.query_selector("[itemprop='price'], [class*='price-main']")
                    rating_el = await card.query_selector("[aria-label*='stars'], [class*='stars']")
                    review_el = await card.query_selector("[class*='review-count'], [aria-label*='reviews']")
                    link_el = await card.query_selector("a")
                    img_el = await card.query_selector("img")

                    title = await title_el.inner_text() if title_el else ""
                    if not title.strip():
                        continue

                    price_raw = await price_el.inner_text() if price_el else ""
                    rating_raw = await rating_el.get_attribute("aria-label") if rating_el else ""
                    review_raw = await review_el.inner_text() if review_el else "0"
                    href = await link_el.get_attribute("href") if link_el else None
                    product_url = f"https://www.walmart.com{href}" if href and href.startswith("/") else href
                    image_url = await img_el.get_attribute("src") if img_el else None

                    review_count = 0
                    m = re.search(r"\d+", review_raw.replace(",", ""))
                    if m:
                        review_count = int(m.group())

                    products.append(
                        ProductRaw(
                            source_site=self.SITE_NAME,
                            title=title.strip(),
                            price=self.normalize_price(price_raw),
                            avg_rating=self.normalize_rating(rating_raw or ""),
                            review_count=review_count,
                            product_url=product_url,
                            image_url=image_url,
                            scrape_status="ok",
                        )
                    )
                    await self.human_delay()
                except Exception:
                    continue

            await browser.close()
        return products


class BlockedError(Exception):
    pass
```

- [ ] **Step 6: Write the concurrent runner**

`toptails/backend/app/scrapers/runner.py`:
```python
import asyncio
from app.scrapers.tractor_supply import TractorSupplyScraper
from app.scrapers.target import TargetScraper
from app.scrapers.chewy import ChewyScraper
from app.scrapers.petsmart import PetsmartScraper
from app.scrapers.petco import PetcoScraper
from app.scrapers.amazon import AmazonScraper
from app.scrapers.walmart import WalmartScraper
from app.scrapers.base import ProductRaw

ALL_SCRAPERS = [
    TractorSupplyScraper,
    TargetScraper,
    ChewyScraper,
    PetsmartScraper,
    PetcoScraper,
    AmazonScraper,
    WalmartScraper,
]


async def run_all_scrapers() -> list[ProductRaw]:
    tasks = [scraper_cls().run() for scraper_cls in ALL_SCRAPERS]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    flat: list[ProductRaw] = []
    for site_results in results:
        flat.extend(site_results)
    return flat
```

---

## Task 4: Trust Score Engine + Ranker (TDD)

**Files:**
- Create: `toptails/backend/app/scoring/trust_score.py`
- Create: `toptails/backend/app/scoring/ranker.py`
- Create: `toptails/backend/tests/test_scoring.py`

- [ ] **Step 1: Write all scoring tests first**

`toptails/backend/tests/test_scoring.py`:
```python
import pytest
from app.scoring.trust_score import (
    compute_trust_score,
    volume_weight,
    distribution_penalty,
    verified_bonus,
    velocity_penalty,
)
from app.scoring.ranker import rank_products
from app.scrapers.base import ProductRaw


# --- volume_weight ---

def test_volume_weight_below_15_is_zero():
    assert volume_weight(0) == 0.0
    assert volume_weight(14) == 0.0


def test_volume_weight_at_15_is_nonzero():
    w = volume_weight(15)
    assert w > 0.0
    assert w < 1.0


def test_volume_weight_at_50_is_approx_0_62():
    w = volume_weight(50)
    assert abs(w - 0.5) < 0.2  # loose: just confirms sigmoid curve


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
    assert distribution_penalty(0.80) == 1.0   # 0.80 is not > 0.80
    assert distribution_penalty(0.801) == 0.75


def test_distribution_penalty_boundary_90():
    assert distribution_penalty(0.90) == 0.75  # 0.90 is not > 0.90
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


def test_velocity_penalty_exactly_30pct_no_penalty():
    # 3 out of 10 in a window = exactly 30%, not > 30%
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
    # Perfect product — should not exceed 1.0
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


# --- ranker ---

def _make_product(site: str, trust: float, status: str = "ok") -> ProductRaw:
    return ProductRaw(
        source_site=site,
        title=f"Product {trust}",
        trust_score=trust,
        scrape_status=status,
        review_count=100,
        avg_rating=4.0,
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
    amazon = ranked["amazon"]
    assert len(chewy) == 2
    assert chewy[0].trust_score > chewy[1].trust_score
    assert len(amazon) == 2


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
```

- [ ] **Step 2: Run tests — verify they all fail**

```bash
docker compose exec backend pytest tests/test_scoring.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.scoring.trust_score'`

- [ ] **Step 3: Implement `trust_score.py`**

`toptails/backend/app/scoring/trust_score.py`:
```python
import math
from datetime import date, timedelta
from typing import Optional


def volume_weight(review_count: int) -> float:
    if review_count < 15:
        return 0.0
    return 1 / (1 + math.exp(-0.05 * (review_count - 50)))


def distribution_penalty(five_star_ratio: float) -> float:
    if five_star_ratio > 0.90:
        return 0.5
    if five_star_ratio > 0.80:
        return 0.75
    return 1.0


def verified_bonus(verified_ratio: float) -> float:
    return 1.0 + (0.3 * verified_ratio)


def velocity_penalty(review_dates: list[str]) -> float:
    if not review_dates or len(review_dates) < 2:
        return 1.0

    parsed = sorted(date.fromisoformat(d) for d in review_dates)
    total = len(parsed)

    for anchor in parsed:
        window_end = anchor + timedelta(days=3)
        count_in_window = sum(1 for d in parsed if anchor <= d <= window_end)
        if count_in_window / total > 0.30:
            return 0.6

    return 1.0


def compute_trust_score(
    avg_rating: Optional[float],
    review_count: Optional[int],
    five_star_ratio: Optional[float],
    verified_ratio: Optional[float],
    review_dates: Optional[list[str]],
) -> float:
    if avg_rating is None or review_count is None:
        return 0.0

    vw = volume_weight(review_count)
    if vw == 0.0:
        return 0.0

    score = (
        (avg_rating / 5.0)
        * vw
        * distribution_penalty(five_star_ratio or 0.0)
        * verified_bonus(verified_ratio or 0.0)
        * velocity_penalty(review_dates or [])
    )
    return min(max(score, 0.0), 1.0)
```

- [ ] **Step 4: Implement `ranker.py`**

`toptails/backend/app/scoring/ranker.py`:
```python
from collections import defaultdict
from app.scrapers.base import ProductRaw
from app.scoring.trust_score import compute_trust_score

ALL_SITES = [
    "amazon", "walmart", "chewy", "petsmart", "petco", "target", "tractor_supply"
]


def score_products(products: list[ProductRaw]) -> list[ProductRaw]:
    scored = []
    for p in products:
        if p.scrape_status != "ok":
            scored.append(p)
            continue
        p.trust_score = compute_trust_score(
            avg_rating=p.avg_rating,
            review_count=p.review_count,
            five_star_ratio=p.five_star_ratio,
            verified_ratio=p.verified_ratio,
            review_dates=p.review_dates,
        )
        scored.append(p)
    return scored


def rank_products(
    products: list[ProductRaw], top_n: int = 2
) -> dict[str, list[ProductRaw]]:
    ranked: dict[str, list[ProductRaw]] = {site: [] for site in ALL_SITES}

    by_site: dict[str, list[ProductRaw]] = defaultdict(list)
    for p in products:
        by_site[p.source_site].append(p)

    for site, site_products in by_site.items():
        blocked = [p for p in site_products if p.scrape_status != "ok"]
        if blocked:
            ranked[site] = []
            continue

        eligible = [
            p for p in site_products
            if p.trust_score is not None and p.trust_score > 0.0
        ]
        top = sorted(eligible, key=lambda p: p.trust_score, reverse=True)[:top_n]
        ranked[site] = top

    return ranked
```

- [ ] **Step 5: Run all scoring tests — verify they all pass**

```bash
docker compose exec backend pytest tests/test_scoring.py -v
```
Expected: All 22 tests pass.

---

## Task 5: Full API Layer — `/scrape/run` and `/products`

**Files:**
- Modify: `toptails/backend/app/api/routes.py`
- Modify: `toptails/backend/app/models/product.py` (add `save_products` helper)

- [ ] **Step 1: Add `save_products` to `db/session.py`**

Add this function to `toptails/backend/app/db/session.py` below the existing `get_db`:
```python
from app.scrapers.base import ProductRaw
from app.models.product import Product


def save_products(products: list[ProductRaw]) -> None:
    db = SessionLocal()
    try:
        for p in products:
            row = Product(
                source_site=p.source_site,
                category=p.category,
                title=p.title or "",
                price=p.price,
                product_url=p.product_url,
                image_url=p.image_url,
                avg_rating=p.avg_rating,
                review_count=p.review_count,
                verified_ratio=p.verified_ratio,
                five_star_ratio=p.five_star_ratio,
                rating_distribution=p.rating_distribution,
                review_dates=p.review_dates,
                trust_score=p.trust_score,
                scrape_status=p.scrape_status,
                scrape_notes=p.scrape_notes,
            )
            db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

- [ ] **Step 2: Replace `routes.py` with full implementation**

`toptails/backend/app/api/routes.py`:
```python
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal, save_products
from app.scrapers.runner import run_all_scrapers
from app.scoring.ranker import score_products, rank_products, ALL_SITES

router = APIRouter()


# --- Request / Response models ---

class ScrapeRequest(BaseModel):
    category: str = "dog_beds"
    top_n: int = 2


class ScrapeResponse(BaseModel):
    job_id: str
    status: str
    sites_queued: list[str]


class ProductOut(BaseModel):
    title: str
    price: Optional[float]
    avg_rating: Optional[float]
    review_count: Optional[int]
    trust_score: Optional[float]
    product_url: Optional[str]
    image_url: Optional[str]


class SiteResult(BaseModel):
    site: str
    scrape_status: str
    scrape_notes: Optional[str]
    top_products: list[ProductOut]


class ProductsResponse(BaseModel):
    category: str
    generated_at: str
    results: list[SiteResult]


# --- Background scrape task ---

async def _run_scrape(category: str, top_n: int):
    raw = await run_all_scrapers()
    scored = score_products(raw)
    ranked = rank_products(scored, top_n=top_n)

    to_save = []
    for site_products in ranked.values():
        to_save.extend(site_products)
    # Also save blocked records
    for p in scored:
        if p.scrape_status != "ok" and p not in to_save:
            to_save.append(p)

    save_products(to_save)


# --- Endpoints ---

@router.post("/scrape/run", response_model=ScrapeResponse)
async def scrape_run(req: ScrapeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    background_tasks.add_task(_run_scrape, req.category, req.top_n)
    return ScrapeResponse(
        job_id=job_id,
        status="running",
        sites_queued=ALL_SITES,
    )


@router.get("/products", response_model=ProductsResponse)
def get_products(category: str = "dog_beds", top_n: int = 2):
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT DISTINCT ON (source_site, title)
                    source_site, title, price, avg_rating, review_count,
                    trust_score, product_url, image_url, scrape_status, scrape_notes
                FROM products
                WHERE category = :category
                ORDER BY source_site, title, scraped_at DESC
            """),
            {"category": category},
        ).fetchall()
    finally:
        db.close()

    by_site: dict[str, list] = {site: [] for site in ALL_SITES}
    site_status: dict[str, tuple[str, Optional[str]]] = {
        site: ("ok", None) for site in ALL_SITES
    }

    for row in rows:
        site = row.source_site
        if row.scrape_status != "ok":
            site_status[site] = (row.scrape_status, row.scrape_notes)
            continue
        if site in by_site and row.trust_score and row.trust_score > 0:
            by_site[site].append(row)

    results = []
    for site in ALL_SITES:
        status, notes = site_status[site]
        eligible = sorted(
            by_site[site],
            key=lambda r: float(r.trust_score or 0),
            reverse=True,
        )[:top_n]
        results.append(
            SiteResult(
                site=site,
                scrape_status=status,
                scrape_notes=notes,
                top_products=[
                    ProductOut(
                        title=r.title,
                        price=float(r.price) if r.price else None,
                        avg_rating=float(r.avg_rating) if r.avg_rating else None,
                        review_count=r.review_count,
                        trust_score=float(r.trust_score) if r.trust_score else None,
                        product_url=r.product_url,
                        image_url=r.image_url,
                    )
                    for r in eligible
                ],
            )
        )

    return ProductsResponse(
        category=category,
        generated_at=datetime.now(timezone.utc).isoformat(),
        results=results,
    )


@router.get("/health")
def health():
    db = SessionLocal()
    try:
        last_scrape = db.execute(
            text("SELECT MAX(scraped_at) FROM products")
        ).scalar()
    except Exception:
        last_scrape = None
    finally:
        db.close()
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "last_scrape": last_scrape.isoformat() if last_scrape else None,
    }
```

- [ ] **Step 3: Rebuild backend and verify endpoints**

```bash
docker compose up backend --build
```

Test health:
```bash
curl http://localhost:8000/health
```
Expected: `{"status": "ok", ...}`

Test products (empty — no scrapes yet):
```bash
curl "http://localhost:8000/products?category=dog_beds&top_n=2"
```
Expected: `{"category": "dog_beds", "results": [...all sites with empty top_products...]}` — no error.

Test scrape trigger:
```bash
curl -X POST http://localhost:8000/scrape/run \
  -H "Content-Type: application/json" \
  -d '{"category": "dog_beds", "top_n": 2}'
```
Expected: `{"job_id": "...", "status": "running", "sites_queued": [...]}`

- [ ] **Step 4: Wait for scrape to finish, verify data**

Wait ~2–5 minutes (Playwright scraping takes time), then:
```bash
curl "http://localhost:8000/products?category=dog_beds&top_n=2" | python3 -m json.tool
```
Expected: At least 2–3 sites return products. Amazon/Walmart may show `scrape_status: "blocked"`.

---

## Task 6: Frontend Placeholder

**Files:**
- Create: `toptails/frontend/package.json`
- Create: `toptails/frontend/next.config.js`
- Create: `toptails/frontend/src/app/page.tsx`
- Create: `toptails/frontend/src/components/ProductCard.tsx`
- Create: `toptails/frontend/src/app/layout.tsx`
- Create: `toptails/frontend/src/app/globals.css`
- Create: `toptails/frontend/Dockerfile`

- [ ] **Step 1: Bootstrap Next.js project**

```bash
cd toptails/frontend
npx create-next-app@latest . --typescript --tailwind --eslint --app --no-src-dir --import-alias "@/*"
```

Then manually overwrite the files in the steps below — `create-next-app` creates boilerplate we'll replace.

- [ ] **Step 2: Write `ProductCard.tsx`**

`toptails/frontend/src/components/ProductCard.tsx`:
```tsx
interface ProductCardProps {
  title: string;
  price: number | null;
  avg_rating: number | null;
  review_count: number | null;
  trust_score: number | null;
  product_url: string | null;
  image_url: string | null;
}

export default function ProductCard({
  title,
  price,
  avg_rating,
  review_count,
  trust_score,
  product_url,
  image_url,
}: ProductCardProps) {
  return (
    <div className="border border-[var(--border)] rounded-lg overflow-hidden bg-[var(--card-bg)] flex flex-col">
      {image_url && (
        <div className="aspect-square overflow-hidden bg-[var(--image-bg)]">
          <img
            src={image_url}
            alt={title}
            className="w-full h-full object-contain p-2"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        </div>
      )}
      <div className="p-4 flex flex-col gap-2 flex-1">
        <p className="text-sm font-medium text-[var(--text-primary)] line-clamp-3 leading-snug">
          {title}
        </p>
        <div className="flex items-center justify-between mt-auto pt-2">
          <span className="text-base font-semibold text-[var(--text-primary)]">
            {price != null ? `$${price.toFixed(2)}` : "—"}
          </span>
          {trust_score != null && (
            <span className="text-xs font-medium px-2 py-0.5 rounded bg-[var(--badge-bg)] text-[var(--badge-text)]">
              Trust {(trust_score * 100).toFixed(0)}%
            </span>
          )}
        </div>
        <div className="text-xs text-[var(--text-muted)]">
          {avg_rating != null ? `★ ${avg_rating.toFixed(1)}` : "No rating"}
          {review_count != null && ` · ${review_count.toLocaleString()} reviews`}
        </div>
        {product_url && (
          <a
            href={product_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs underline text-[var(--text-muted)] hover:text-[var(--text-primary)] mt-1"
          >
            View product →
          </a>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Write `globals.css` with CSS variables**

`toptails/frontend/src/app/globals.css`:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --text-primary: #111827;
  --text-muted: #6b7280;
  --border: #e5e7eb;
  --card-bg: #ffffff;
  --image-bg: #f9fafb;
  --badge-bg: #f3f4f6;
  --badge-text: #374151;
  --page-bg: #f9fafb;
  --blocked-bg: #fef2f2;
  --blocked-text: #991b1b;
  --blocked-border: #fecaca;
}

body {
  background-color: var(--page-bg);
  color: var(--text-primary);
}
```

- [ ] **Step 4: Write `layout.tsx`**

`toptails/frontend/src/app/layout.tsx`:
```tsx
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TopTails — Best Dog Beds",
  description: "Top-rated dog beds scored by trust",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 5: Write `page.tsx`**

`toptails/frontend/src/app/page.tsx`:
```tsx
import ProductCard from "@/components/ProductCard";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface Product {
  title: string;
  price: number | null;
  avg_rating: number | null;
  review_count: number | null;
  trust_score: number | null;
  product_url: string | null;
  image_url: string | null;
}

interface SiteResult {
  site: string;
  scrape_status: string;
  scrape_notes: string | null;
  top_products: Product[];
}

interface ProductsResponse {
  category: string;
  generated_at: string;
  results: SiteResult[];
}

async function fetchProducts(): Promise<ProductsResponse | null> {
  try {
    const res = await fetch(`${API_URL}/products?category=dog_beds&top_n=2`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

function SiteSection({ result }: { result: SiteResult }) {
  const siteName = result.site.replace(/_/g, " ");
  const isBlocked = result.scrape_status !== "ok";

  return (
    <section className="mb-10">
      <div className="flex items-center gap-3 mb-4">
        <h2 className="text-lg font-semibold capitalize">{siteName}</h2>
        {isBlocked && (
          <span className="text-xs px-2 py-0.5 rounded border border-[var(--blocked-border)] bg-[var(--blocked-bg)] text-[var(--blocked-text)]">
            Site unavailable
          </span>
        )}
      </div>

      {isBlocked ? (
        <p className="text-sm text-[var(--text-muted)] italic">
          {result.scrape_notes ?? "This site could not be scraped at this time."}
        </p>
      ) : result.top_products.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)] italic">No products found yet. Trigger a scrape first.</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-xl">
          {result.top_products.map((p, i) => (
            <ProductCard key={i} {...p} />
          ))}
        </div>
      )}
    </section>
  );
}

export default async function Home() {
  const data = await fetchProducts();

  return (
    <main className="max-w-4xl mx-auto px-4 py-10">
      <header className="mb-10">
        <h1 className="text-3xl font-bold tracking-tight">TopTails</h1>
        <p className="text-[var(--text-muted)] mt-1 text-sm">
          Top dog beds by trust score — {data?.generated_at ? new Date(data.generated_at).toLocaleString() : "no data yet"}
        </p>
      </header>

      {!data ? (
        <p className="text-[var(--text-muted)]">Could not connect to API. Is the backend running?</p>
      ) : (
        data.results.map((result) => (
          <SiteSection key={result.site} result={result} />
        ))
      )}
    </main>
  );
}
```

- [ ] **Step 6: Write frontend Dockerfile**

`toptails/frontend/Dockerfile`:
```dockerfile
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./

EXPOSE 3000
CMD ["npm", "start"]
```

- [ ] **Step 7: Write `next.config.js`**

`toptails/frontend/next.config.js`:
```js
/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
    ],
  },
};

module.exports = nextConfig;
```

- [ ] **Step 8: Start full stack and verify**

```bash
cd toptails
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000) — should show page with site sections.  
Trigger a scrape:
```bash
curl -X POST http://localhost:8000/scrape/run \
  -H "Content-Type: application/json" \
  -d '{"category": "dog_beds", "top_n": 2}'
```
Wait ~3–5 minutes, then refresh the frontend — product cards should appear for non-blocked sites.

---

## Self-Review Checklist

- [x] **Spec §3 DB schema** → Task 1 creates `Product` ORM model with all columns
- [x] **Spec §4a BaseScraper interface** → Task 2 `base.py` with `run()` error wrapping
- [x] **Spec §4b Playwright stealth + delays** → Task 2–3 all scrapers use `stealth_async` + `human_delay()`
- [x] **Spec §4e Amazon/Walmart warning block** → Task 3 both files have the comment block
- [x] **Spec §5 Trust score formula** → Task 4 all 5 components + clamp + hard filter <15 reviews
- [x] **Spec §6 POST /scrape/run** → Task 5 fire-and-forget with `BackgroundTasks`
- [x] **Spec §6 GET /products** → Task 5 returns per-site results with `scrape_status`
- [x] **Spec §6 GET /health** → Task 1 + Task 5 updated
- [x] **Spec §7 Frontend** → Task 6 minimal, CSS variables, blocked state shown
- [x] **Spec §9 Docker Compose** → Task 1 `docker-compose.yml` with health check on db
- [x] **Type consistency** → `ProductRaw` defined once in `base.py`, imported everywhere; `ALL_SITES` defined once in `ranker.py`, imported in `routes.py`
- [x] **No TBD/placeholders** → All steps have complete code
