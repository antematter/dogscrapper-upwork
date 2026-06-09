import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db, save_products
from app.scrapers.runner import run_all_scrapers, run_scraper
from app.scoring.ranker import score_products, rank_products, ALL_SITES

router = APIRouter()
_scrape_lock = asyncio.Lock()
logger = logging.getLogger(__name__)

# In-process scrape observability (same worker as Playwright jobs).
_scrape_job: dict = {
    "started_at": None,
    "last_finished_at": None,
    "last_saved_rows": None,
    "last_error": None,
    "last_scrape_debug": None,
}
# "__all__" = full scrape, else a site key from ALL_SITES
_active_scrape_target: Optional[str] = None
_site_jobs: dict[str, dict] = {}


def _default_site_job() -> dict:
    return {
        "running": False,
        "started_at": None,
        "last_finished_at": None,
        "last_saved_rows": None,
        "last_error": None,
        "last_debug": None,
    }


def _site_job(site: str) -> dict:
    if site not in _site_jobs:
        _site_jobs[site] = _default_site_job()
    return _site_jobs[site]


def reset_scrape_state() -> None:
    """Clear in-memory global + per-site scrape/debug state (UI banners and status polls)."""
    global _active_scrape_target
    _active_scrape_target = None
    _scrape_job["started_at"] = None
    _scrape_job["last_finished_at"] = None
    _scrape_job["last_saved_rows"] = None
    _scrape_job["last_error"] = None
    _scrape_job["last_scrape_debug"] = None
    _site_jobs.clear()


# --- Request/Response models ---

class ScrapeRequest(BaseModel):
    category: str = "dog_beds"  # only "dog_beds" supported in MVP
    top_n: int = Field(default=2, ge=1, le=20)


class ScrapeResponse(BaseModel):
    job_id: str
    status: str
    sites_queued: list[str]


class ScrapeStatusOut(BaseModel):
    scrape_running: bool
    job_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_saved_rows: Optional[int] = None
    last_error: Optional[str] = None
    last_scrape_debug: Optional[dict[str, Any]] = None
    active_target: Optional[str] = None


class SiteScrapeStatusOut(BaseModel):
    site: str
    scrape_running: bool
    job_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_saved_rows: Optional[int] = None
    last_error: Optional[str] = None
    debug: Optional[dict[str, Any]] = None
    global_scrape_running: bool
    active_target: Optional[str] = None


class ProductOut(BaseModel):
    source_site: Optional[str] = None
    title: str
    price: Optional[float] = None
    avg_rating: Optional[float] = None
    review_count: Optional[int] = None
    trust_score: Optional[float] = None
    product_url: Optional[str] = None
    image_url: Optional[str] = None


class SiteResult(BaseModel):
    site: str
    scrape_status: str
    scrape_notes: Optional[str] = None
    top_products: list[ProductOut]


class ProductsResponse(BaseModel):
    category: str
    generated_at: str
    results: list[SiteResult]


# --- Background task ---


def _build_scrape_debug(raw: list, to_save: list, top_n: int) -> dict[str, Any]:
    """Per-site snapshot: what each scraper returned vs rows queued for INSERT."""
    from collections import defaultdict

    by_site_raw: dict[str, list] = defaultdict(list)
    for p in raw:
        by_site_raw[p.source_site].append(p)

    by_site_save: dict[str, int] = defaultdict(int)
    for p in to_save:
        by_site_save[p.source_site] += 1

    sites_out: dict[str, Any] = {}
    for site in ALL_SITES:
        rows = by_site_raw.get(site, [])
        ok_n = sum(1 for r in rows if r.scrape_status == "ok")
        bad_rows = [r for r in rows if r.scrape_status != "ok"]
        note = None
        if bad_rows:
            sn = bad_rows[0].scrape_notes
            if sn:
                note = sn[:220]
        sites_out[site] = {
            "scraper_returned_rows": len(rows),
            "ok_product_rows": ok_n,
            "blocked": bool(bad_rows),
            "scrape_notes": note,
            "rows_queued_for_db": by_site_save.get(site, 0),
        }
    return {"top_n": top_n, "sites": sites_out}


def _build_single_site_debug(raw: list, to_save: list, site: str, top_n: int) -> dict[str, Any]:
    rows = [p for p in raw if p.source_site == site]
    ok_n = sum(1 for r in rows if r.scrape_status == "ok")
    bad_rows = [r for r in rows if r.scrape_status != "ok"]
    note = None
    if bad_rows and bad_rows[0].scrape_notes:
        note = bad_rows[0].scrape_notes[:220]
    saved_n = sum(1 for p in to_save if p.source_site == site)
    silent_empty = len(rows) == 0 and ok_n == 0 and not bad_rows
    return {
        "top_n": top_n,
        "scraper_returned_rows": len(rows),
        "ok_product_rows": ok_n,
        "blocked": (bool(bad_rows) and ok_n == 0) or silent_empty,
        "silent_empty": silent_empty,
        "scrape_notes": note,
        "rows_queued_for_db": saved_n,
    }


def _products_to_save(scored: list, ranked: dict, top_n: int, sites: Optional[set[str]] = None) -> list:
    to_save: list = []
    seen_sites: set = set()
    site_filter = sites

    for site, site_products in ranked.items():
        if site_filter is not None and site not in site_filter:
            continue
        if site_products:
            to_save.extend(site_products)
            seen_sites.add(site)

    for p in scored:
        if site_filter is not None and p.source_site not in site_filter:
            continue
        if p.scrape_status != "ok" and p.source_site not in seen_sites:
            to_save.append(p)
            seen_sites.add(p.source_site)

    return to_save


async def _run_scrape(category: str, top_n: int) -> None:
    if _scrape_lock.locked():
        logger.warning(
            "Scrape skipped: another job already holds the lock "
            "(duplicate POST or overlapping background task). "
            "Poll GET /scrape/status until scrape_running is false."
        )
        return
    global _active_scrape_target
    async with _scrape_lock:
        started = datetime.now(timezone.utc).isoformat()
        _active_scrape_target = "__all__"
        _scrape_job["started_at"] = started
        _scrape_job["last_error"] = None
        _scrape_job["last_scrape_debug"] = None
        logger.info("Scrape started (Playwright); this may take several minutes")
        try:
            # category is hardcoded to "dog_beds" for MVP — scrapers don't accept dynamic categories yet
            raw = await run_all_scrapers()
            scored = score_products(raw)
            ranked = rank_products(scored, top_n=top_n)
            to_save = _products_to_save(scored, ranked, top_n)

            save_products(to_save)
            finished = datetime.now(timezone.utc).isoformat()
            _scrape_job["last_finished_at"] = finished
            _scrape_job["last_saved_rows"] = len(to_save)
            _scrape_job["last_error"] = None
            debug = _build_scrape_debug(raw, to_save, top_n)
            _scrape_job["last_scrape_debug"] = debug
            for site in ALL_SITES:
                sj = _site_job(site)
                sj["last_finished_at"] = finished
                sj["last_saved_rows"] = debug["sites"][site]["rows_queued_for_db"]
                sj["last_error"] = None
                sj["last_debug"] = debug["sites"][site]
            logger.info("Scrape finished: saved %s DB rows", len(to_save))
        except Exception as e:
            finished = datetime.now(timezone.utc).isoformat()
            _scrape_job["last_finished_at"] = finished
            _scrape_job["last_saved_rows"] = None
            _scrape_job["last_error"] = f"{type(e).__name__}: {e}"
            _scrape_job["last_scrape_debug"] = None
            logger.exception("Scrape failed")
        finally:
            _scrape_job["started_at"] = None
            _active_scrape_target = None


async def _run_scrape_site(category: str, top_n: int, site: str) -> None:
    global _active_scrape_target
    if _scrape_lock.locked():
        logger.warning(
            "Single-site scrape skipped for %s: lock held by %s",
            site,
            _active_scrape_target,
        )
        return
    sj = _site_job(site)
    async with _scrape_lock:
        started = datetime.now(timezone.utc).isoformat()
        _active_scrape_target = site
        sj["running"] = True
        sj["started_at"] = started
        sj["last_error"] = None
        sj["last_debug"] = None
        logger.info("Single-site scrape started: %s", site)
        try:
            raw = await run_scraper(site)
            scored = score_products(raw)
            ranked = rank_products(scored, top_n=top_n)
            to_save = _products_to_save(scored, ranked, top_n, sites={site})

            save_products(to_save)
            finished = datetime.now(timezone.utc).isoformat()
            saved_n = len(to_save)
            sj["last_finished_at"] = finished
            sj["last_saved_rows"] = saved_n
            sj["last_error"] = None
            sj["last_debug"] = _build_single_site_debug(raw, to_save, site, top_n)
            logger.info("Single-site scrape finished %s: saved %s rows", site, saved_n)
        except Exception as e:
            finished = datetime.now(timezone.utc).isoformat()
            sj["last_finished_at"] = finished
            sj["last_saved_rows"] = None
            sj["last_error"] = f"{type(e).__name__}: {e}"
            sj["last_debug"] = None
            logger.exception("Single-site scrape failed: %s", site)
        finally:
            sj["running"] = False
            sj["started_at"] = None
            _active_scrape_target = None


# --- Endpoints ---


@router.post("/scrape/reset-state")
async def scrape_reset_state():
    """Clear in-memory scrape job/debug state. Pair with DB truncate (see scripts/clear_products.py)."""
    if _scrape_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="A scrape is still running. Wait for it to finish before resetting state.",
        )
    reset_scrape_state()
    return {"ok": True, "message": "Scrape status and per-site debug cleared"}


@router.get("/scrape/status", response_model=ScrapeStatusOut)
async def scrape_status():
    """Scrape lock + last job summary (read from the API process running Playwright)."""
    return ScrapeStatusOut(
        scrape_running=_scrape_lock.locked(),
        job_started_at=_scrape_job["started_at"],
        last_finished_at=_scrape_job["last_finished_at"],
        last_saved_rows=_scrape_job["last_saved_rows"],
        last_error=_scrape_job["last_error"],
        last_scrape_debug=_scrape_job["last_scrape_debug"],
        active_target=_active_scrape_target,
    )


@router.get("/scrape/status/{site}", response_model=SiteScrapeStatusOut)
async def scrape_status_site(site: str):
    if site not in ALL_SITES:
        raise HTTPException(status_code=404, detail=f"Unknown site: {site}")
    sj = _site_job(site)
    running = _scrape_lock.locked() and _active_scrape_target == site
    return SiteScrapeStatusOut(
        site=site,
        scrape_running=running,
        job_started_at=sj["started_at"],
        last_finished_at=sj["last_finished_at"],
        last_saved_rows=sj["last_saved_rows"],
        last_error=sj["last_error"],
        debug=sj["last_debug"],
        global_scrape_running=_scrape_lock.locked(),
        active_target=_active_scrape_target,
    )


@router.post("/scrape/run", response_model=ScrapeResponse)
async def scrape_run(req: ScrapeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    background_tasks.add_task(_run_scrape, req.category, req.top_n)
    logger.info(
        "POST /scrape/run accepted job_id=%s (background). "
        "Watch logs or GET /scrape/status for progress.",
        job_id,
    )
    return ScrapeResponse(
        job_id=job_id,
        status="running",
        sites_queued=ALL_SITES,
    )


@router.post("/scrape/run/{site}", response_model=ScrapeResponse)
async def scrape_run_site(
    site: str,
    req: ScrapeRequest,
    background_tasks: BackgroundTasks,
):
    if site not in ALL_SITES:
        raise HTTPException(status_code=404, detail=f"Unknown site: {site}")
    job_id = str(uuid.uuid4())
    background_tasks.add_task(_run_scrape_site, req.category, req.top_n, site)
    logger.info(
        "POST /scrape/run/%s accepted job_id=%s (background)",
        site,
        job_id,
    )
    return ScrapeResponse(
        job_id=job_id,
        status="running",
        sites_queued=[site],
    )


@router.get("/products", response_model=ProductsResponse)
def get_products(
    category: str = "dog_beds",
    top_n: int = Query(default=2, ge=1, le=20),
    db: Session = Depends(get_db),
):
    try:
        # Get the most recent scrape_status per site
        status_rows = db.execute(
            text("""
                SELECT DISTINCT ON (source_site)
                    source_site, scrape_status, scrape_notes
                FROM products
                WHERE category = :category
                ORDER BY source_site, scraped_at DESC
            """),
            {"category": category},
        ).fetchall()

        # Get top products scoped to the most recent scrape window
        product_rows = db.execute(
            text("""
                SELECT source_site, title, price, avg_rating, review_count,
                       trust_score, product_url, image_url
                FROM products
                WHERE category = :category
                  AND scrape_status = 'ok'
                  AND trust_score IS NOT NULL
                  AND scraped_at >= (
                      SELECT COALESCE(
                          MAX(scraped_at),
                          TIMESTAMPTZ 'epoch'
                      ) - INTERVAL '60 minutes'
                      FROM products WHERE category = :category
                  )
                ORDER BY source_site, trust_score DESC
            """),
            {"category": category},
        ).fetchall()
    except Exception:
        status_rows = []
        product_rows = []

    # Build site status map
    site_status: dict[str, tuple[str, Optional[str]]] = {
        site: ("ok", None) for site in ALL_SITES
    }
    for row in status_rows:
        site_status[row.source_site] = (row.scrape_status, row.scrape_notes)

    # Group products by site, taking top_n
    by_site: dict[str, list] = {site: [] for site in ALL_SITES}
    for row in product_rows:
        site = row.source_site
        if site in by_site and len(by_site[site]) < top_n:
            by_site[site].append(row)

    results = []
    for site in ALL_SITES:
        status, notes = site_status[site]
        results.append(
            SiteResult(
                site=site,
                scrape_status=status,
                scrape_notes=notes,
                top_products=[
                    ProductOut(
                        source_site=r.source_site,
                        title=r.title,
                        price=float(r.price) if r.price is not None else None,
                        avg_rating=float(r.avg_rating) if r.avg_rating is not None else None,
                        review_count=r.review_count,
                        trust_score=float(r.trust_score) if r.trust_score is not None else None,
                        product_url=r.product_url,
                        image_url=r.image_url,
                    )
                    for r in by_site[site]
                ],
            )
        )

    return ProductsResponse(
        category=category,
        generated_at=datetime.now(timezone.utc).isoformat(),
        results=results,
    )


@router.get("/health")
async def health(db: Session = Depends(get_db)):
    try:
        last_scrape = db.execute(
            text("SELECT MAX(scraped_at) FROM products")
        ).scalar()
    except Exception:
        last_scrape = None
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "last_scrape": last_scrape.isoformat() if last_scrape else None,
        "scrape_running": _scrape_lock.locked(),
        "scrape_job_started_at": _scrape_job["started_at"],
        "last_scrape_job_finished_at": _scrape_job["last_finished_at"],
        "last_scrape_job_saved_rows": _scrape_job["last_saved_rows"],
        "last_scrape_job_error": _scrape_job["last_error"],
        "last_scrape_job_debug": _scrape_job["last_scrape_debug"],
    }
