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
    proxy_mode,
    scrape_product,
)

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

    if site == "unknown" or not product_id:
        return {
            "error": "That doesn't look like a valid Amazon or Flipkart product link. "
                     "Amazon links contain /dp/<ASIN>; Flipkart links contain /p/itm... or ?pid=..."
        }

    cache_key = f"{site}:{product_id}"
    cached = storage.get_cached(cache_key)
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
        storage.put_cached(cache_key, page.product_id, page.site, result)
        return result

    except ScrapeError as e:
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


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = storage.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Unknown job id."}, status_code=404)
    return job
