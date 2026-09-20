"""
app_render.py
Backend for checkfakereviews.online.

What changed from the previous version: the hosted build no longer refuses
unknown products. /api/check now runs the real pipeline — scrape the product
page, parse the 5 most recent reviews, score each one — for ANY Amazon.in or
Flipkart URL. The three precomputed products are kept only as a fallback for
when a live scrape is blocked, so the demo never hard-fails.

Response shape is unchanged, so the existing static/index.html keeps working.

Deploy (Render):
    Build:  pip install -r requirements-render.txt
    Start:  uvicorn app_render:app --host 0.0.0.0 --port $PORT

Environment variables:
    ALLOW_ORIGINS     comma-separated origins for a Vercel-hosted frontend,
                      e.g. https://checkfakereviews.online,https://cfr.vercel.app
    SCRAPERAPI_KEY    or PROXY_URL — strongly recommended, see scraper.py
    DATABASE_URL      Postgres for a cache that survives restarts
    DEMO_FALLBACK     "1" (default) to serve precomputed data when blocked
    USE_GPT2          "1" to use real GPT-2 perplexity (needs >=2GB RAM)
"""

import os
import time

from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import storage
from analyze_live import analyze_reviews
from scraper import (
    ScrapeError,
    detect_site,
    extract_product_id,
    is_short_link,
    parse_amazon,
    parse_flipkart,
    proxy_mode,
    scrape_product,
)

# A home worker (remote_worker.py) runs the scrape from a residential IP and
# posts the result back. Set WORKER_TOKEN on the server and on the worker.
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "").strip()
WORKER_STALE_SECONDS = int(os.environ.get("WORKER_STALE_SECONDS", "120"))
_worker = {"last_seen": 0.0}


def worker_online() -> bool:
    return bool(WORKER_TOKEN) and (time.time() - _worker["last_seen"]) < WORKER_STALE_SECONDS

try:
    from demo_data import get_demo_result, list_demo_products
except Exception:  # noqa: BLE001
    def get_demo_result(_):
        return None

    def list_demo_products():
        return []

app = FastAPI(title="Review Trust Checker")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DEMO_FALLBACK = os.environ.get("DEMO_FALLBACK", "1") == "1"
MAX_REVIEWS = int(os.environ.get("MAX_REVIEWS", "5"))

_origins = [o.strip() for o in os.environ.get("ALLOW_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


storage.init()
print(f"[startup] storage={storage.backend_name()} proxy={proxy_mode()}")


class CheckRequest(BaseModel):
    url: str


BLOCK_HINTS = {
    "BLOCKED": (
        "The store blocked this server's request. That's bot detection on the "
        "hosting provider's IP range, not a bug in the analyzer — set PROXY_URL "
        "or SCRAPERAPI_KEY to route scrapes through a residential proxy."
    ),
    "NO_REVIEWS": (
        "The page loaded but no reviews could be read from it. Either the product "
        "genuinely has no reviews, or the site changed its markup."
    ),
    "NETWORK": "Couldn't reach the store. This is usually a timeout; try again.",
    "HTTP_ERROR": "The store returned an error for that product page.",
    "UNSUPPORTED_SITE": "Only Amazon and Flipkart product links are supported right now.",
}


def run_analysis(url: str) -> dict:
    """
    The whole pipeline: cache -> live scrape -> score. Falls back to
    precomputed demo data only if live scraping fails for a known product.
    """
    url = url.strip()
    site = detect_site(url)
    product_id = extract_product_id(url)

    if site == "unknown":
        return {
            "error": "That doesn't look like an Amazon or Flipkart link. "
                     "Amazon links contain /dp/<ASIN> (or are amzn.in/a.co short links); "
                     "Flipkart links contain /p/itm... or ?pid=... (or are fkrt.cc short links)."
        }

    # Short links (amzn.in, a.co, fkrt.cc) carry no product ID until the
    # redirect resolves — that happens inside scrape_product, so don't
    # reject or cache-check yet.
    if not product_id and not is_short_link(url):
        return {
            "error": "That doesn't look like a valid Amazon or Flipkart product link. "
                     "Amazon links contain /dp/<ASIN>; Flipkart links contain /p/itm... or ?pid=..."
        }

    cache_key = f"{site}:{product_id}" if product_id else None
    cached = storage.get_cached(cache_key) if cache_key else None
    if cached:
        cached["source"] = "cache"
        cached["product_url"] = url
        return cached

    started = time.time()
    try:
        page = scrape_product(url, max_reviews=MAX_REVIEWS)
        result = analyze_reviews(page.product_name, page.reviews)
        result.update(
            {
                "product_url": url,
                "product_id": page.product_id,
                "site": page.site,
                "source": "live",
                "scrape_seconds": round(time.time() - started, 2),
            }
        )
        storage.put_cached(f"{page.site}:{page.product_id}", page.product_id, page.site, result)
        return result

    except ScrapeError as e:
        # Blocked from this datacenter IP, but a residential worker is
        # connected: queue the job for it instead of giving up.
        if e.reason in ("BLOCKED", "NETWORK", "HTTP_ERROR") and worker_online():
            job_id = storage.create_job(url)
            return {
                "status": "queued",
                "job_id": job_id,
                "poll": f"/api/jobs/{job_id}",
                "message": "This server is blocked by the store, so the scrape was "
                           "handed to the connected worker. Poll the job for the result.",
                "product_id": product_id,
                "site": site,
            }

        fallback = get_demo_result(product_id) if DEMO_FALLBACK else None
        if fallback:
            out = dict(fallback)
            out.update(
                {
                    "product_url": url,
                    "product_id": product_id,
                    "site": site,
                    "source": "demo-fallback",
                    "notice": "Live scrape failed for this product, so a previously "
                              "computed result is being shown.",
                    "scrape_error": e.reason,
                }
            )
            return out

        return {
            "error": e.message,
            "error_code": e.reason,
            "hint": BLOCK_HINTS.get(e.reason, ""),
            "product_id": product_id,
            "site": site,
            "proxy_mode": proxy_mode(),
        }

    except Exception as e:  # noqa: BLE001
        return {
            "error": f"Unexpected failure while analyzing: {e}",
            "error_code": "INTERNAL",
            "product_id": product_id,
            "site": site,
        }


# ------------------------------ routes ------------------------------------

@app.get("/")
def serve_frontend():
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({"status": "ok", "note": "API only; frontend is hosted separately."})


@app.get("/api/health")
def health():
    """Tells you at a glance whether live scraping can work in this environment."""
    return {
        "status": "ok",
        "storage": storage.backend_name(),
        "proxy_mode": proxy_mode(),
        "live_scraping": True,
        "demo_fallback": DEMO_FALLBACK,
        "worker_configured": bool(WORKER_TOKEN),
        "worker_online": worker_online(),
        "warning": None if proxy_mode() != "direct" else
        "No proxy configured — scrapes go out from this host's IP and are likely "
        "to be blocked by Amazon/Flipkart from a datacenter.",
    }


@app.get("/api/demo-products")
def demo_products():
    return {"products": list_demo_products()}


@app.post("/api/check")
def check_product(payload: CheckRequest):
    """Synchronous path — kept for the existing frontend."""
    if not payload.url or not payload.url.strip():
        return {"error": "Please provide a product URL."}
    return run_analysis(payload.url)


@app.post("/api/check/async")
def check_async(payload: CheckRequest, background: BackgroundTasks):
    """
    Use this if a scrape ever runs past the platform's request timeout
    (Vercel functions cap out well before a slow proxy round-trip does).
    Returns a job_id immediately; poll /api/jobs/{job_id}.
    """
    if not payload.url or not payload.url.strip():
        return {"error": "Please provide a product URL."}
    job_id = storage.create_job(payload.url.strip())

    def _work(jid: str, url: str):
        storage.update_job(jid, "running")
        result = run_analysis(url)
        storage.update_job(jid, "error" if result.get("error") else "done", result)

    background.add_task(_work, job_id, payload.url.strip())
    return {"job_id": job_id, "status": "queued", "poll": f"/api/jobs/{job_id}"}


class HtmlRequest(BaseModel):
    url: str
    html: str


@app.post("/api/analyze-html")
def analyze_html(payload: HtmlRequest):
    """
    Zero-blocking path: the browser already has the page, so let it send the
    HTML. The user opens the product page, saves it (Ctrl+S) or copies
    view-source, and pastes it in. No scraping, nothing to block.
    """
    url = (payload.url or "").strip()
    html = payload.html or ""
    if len(html) < 500:
        return {"error": "That HTML looks too short to be a product page."}

    site = detect_site(url) if url else ("amazon" if "data-hook=\"review\"" in html else "flipkart")
    product_id = extract_product_id(url) if url else None

    product_name, reviews = parse_amazon(html) if site == "amazon" else parse_flipkart(html)
    if not reviews:
        return {
            "error": "No reviews found in that HTML. Make sure you saved the reviews "
                     "section of the page (scroll it into view before saving).",
            "error_code": "NO_REVIEWS",
        }

    result = analyze_reviews(product_name, reviews[:MAX_REVIEWS])
    result.update({"product_url": url, "product_id": product_id, "site": site, "source": "pasted-html"})
    if product_id:
        storage.put_cached(f"{site}:{product_id}", product_id, site, result)
    return result


def _check_worker_auth(token: str) -> bool:
    return bool(WORKER_TOKEN) and token == WORKER_TOKEN


@app.get("/api/worker/next")
def worker_next(token: str = ""):
    """Home worker polls this for queued scrape jobs."""
    if not _check_worker_auth(token):
        return JSONResponse({"error": "Bad worker token."}, status_code=401)
    _worker["last_seen"] = time.time()
    job = storage.claim_next_job()
    return job or {"job": None}


class WorkerResult(BaseModel):
    token: str
    job_id: str
    result: dict


@app.post("/api/worker/result")
def worker_result(payload: WorkerResult):
    if not _check_worker_auth(payload.token):
        return JSONResponse({"error": "Bad worker token."}, status_code=401)
    _worker["last_seen"] = time.time()

    result = payload.result or {}
    status = "error" if result.get("error") else "done"
    storage.update_job(payload.job_id, status, result)

    pid, site = result.get("product_id"), result.get("site")
    if status == "done" and pid and site:
        result["source"] = "worker"
        storage.put_cached(f"{site}:{pid}", pid, site, result)
    return {"ok": True, "status": status}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = storage.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Unknown job id."}, status_code=404)
    return job
