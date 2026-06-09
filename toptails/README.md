# TopTails

Dog bed scraper + trust scoring pipeline. Scrapes 7 retailers, scores every product, shows the top 2 per site.

---

## Running locally

You need two terminals — one for the backend, one for the frontend.

### Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL running (see below if you need one quick)

---

### 1. Set up the database

You need a local PostgreSQL with a `toptails` DB. Run these **once**:

```bash
sudo -u postgres psql -c "CREATE USER \"user\" WITH PASSWORD 'password';"
sudo -u postgres psql -c "CREATE DATABASE toptails OWNER \"user\";"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE toptails TO \"user\";"
```

A `.env` file already exists at `toptails/backend/.env` with the right `DATABASE_URL`.

---

### 2. Start the backend (Terminal 1)

**First time only:**
```bash
cd toptails/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

**Every time after that:**
```bash
cd toptails/backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

Backend is ready at **http://localhost:8000**

---

### 3. Start the frontend (Terminal 2)

```bash
cd toptails/frontend
npm install
npm run dev
```

Frontend is ready at **http://localhost:3000**

---

## Trigger a scrape

Either click **"Run scrape"** in the top-right of the frontend, or from the terminal:

```bash
curl -X POST http://localhost:8000/scrape/run \
  -H "Content-Type: application/json" \
  -d '{"category": "dog_beds", "top_n": 2}'
```

Scraping takes **3–8 minutes** (Playwright opens real browsers). Refresh the frontend when done.

---

## API

| Method | Endpoint | What it does |
|--------|----------|--------------|
| `GET` | `/health` | Service status + last scrape time |
| `POST` | `/scrape/run` | Kick off a scrape for all 7 sites |
| `GET` | `/products?category=dog_beds&top_n=2` | Top N products per site by trust score |

---

## Sites

| Site | Reliability |
|------|-------------|
| Tractor Supply | ✅ Good |
| Target | ✅ Good |
| Chewy | ⚠️ Usually works |
| PetSmart | ⚠️ Usually works |
| Petco | ⚠️ Usually works |
| Amazon | ❌ Often blocked — bot detection |
| Walmart | ❌ Often blocked — bot detection |

Amazon and Walmart will show "Site unavailable" in the UI when blocked. That's expected — both have official APIs that are the clean upgrade path.

---

## Tests

```bash
cd toptails/backend
pytest -v
```

33 tests covering the scoring formula and scraper base class.

---

## Trust score formula

```
trust_score = (avg_rating / 5.0)
            × volume_weight        # sigmoid; < 15 reviews → score 0, excluded from ranking
            × distribution_penalty # penalises suspiciously perfect ratings
            × verified_bonus       # rewards verified purchase reviews
            × velocity_penalty     # flags review-burst spam patterns
```

Final score is clamped to 0.0–1.0. Displayed as a % badge on each product card.
