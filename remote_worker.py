"""
remote_worker.py
Run this on YOUR machine (laptop, home PC, Raspberry Pi). It polls your Render
backend for scrape jobs, does the scraping from your home IP — the one that
already works — and posts the results back.

This is the free way around the CAPTCHA. Render never talks to Amazon at all;
it only holds the queue and the results. Your IP does the fetching, exactly
like when you run the terminal version.

Setup:
    1. On Render, set an env var:   WORKER_TOKEN=<some long random string>
    2. On your machine:
           pip install requests beautifulsoup4
           export BACKEND_URL=https://checkfakereviews.online
           export WORKER_TOKEN=<the same string>
           python remote_worker.py

Keep it running. /api/health on the server will show "worker_online": true.
When the worker is offline, the site falls back to demo data as before.

Caveats, stated plainly:
    * Your site only analyzes new products while this process is running.
    * Your home IP is now doing the scraping for every visitor. If the site
      gets real traffic, Amazon will eventually rate-limit your home
      connection too. Throttle with POLL_INTERVAL and MIN_GAP_SECONDS.
    * This is fine for a portfolio/demo project. It is not a scaling plan.
"""

import os
import time
import traceback

import requests

from analyze_live import analyze_reviews
from scraper import ScrapeError, scrape_product

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "").strip()
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "5"))
MIN_GAP_SECONDS = float(os.environ.get("MIN_GAP_SECONDS", "8"))
MAX_REVIEWS = int(os.environ.get("MAX_REVIEWS", "5"))

_last_scrape = 0.0


def process(job: dict) -> dict:
    global _last_scrape

    gap = MIN_GAP_SECONDS - (time.time() - _last_scrape)
    if gap > 0:
        time.sleep(gap)          # be polite; avoid burning your home IP
    _last_scrape = time.time()

    url = job["url"]
    try:
        page = scrape_product(url, max_reviews=MAX_REVIEWS)
        result = analyze_reviews(page.product_name, page.reviews)
        result.update(
            {
                "product_url": url,
                "product_id": page.product_id,
                "site": page.site,
                "source": "worker",
            }
        )
        print(f"  ✓ {page.product_name[:50]} -> {result['trust_score']}/100")
        return result
    except ScrapeError as e:
        print(f"  ✗ {e.reason}: {e.message}")
        if e.reason == "NO_REVIEWS":
            print("    -> send that saved HTML file back so the selectors can be fixed")
        return {"error": e.message, "error_code": e.reason, "product_url": url}
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return {"error": f"Worker failure: {e}", "error_code": "INTERNAL", "product_url": url}


def main():
    if not WORKER_TOKEN:
        raise SystemExit("Set WORKER_TOKEN (must match the value on the server).")

    print(f"Worker started. Backend={BACKEND_URL}  poll={POLL_INTERVAL}s")
    idle_logged = False

    while True:
        try:
            resp = requests.get(
                f"{BACKEND_URL}/api/worker/next",
                params={"token": WORKER_TOKEN},
                timeout=30,
            )
            if resp.status_code == 401:
                raise SystemExit("Server rejected the worker token.")
            data = resp.json()

            if not data or not data.get("job_id"):
                if not idle_logged:
                    print("waiting for jobs…")
                    idle_logged = True
                time.sleep(POLL_INTERVAL)
                continue

            idle_logged = False
            print(f"job {data['job_id']}: {data['url'][:70]}")
            result = process(data)

            requests.post(
                f"{BACKEND_URL}/api/worker/result",
                json={"token": WORKER_TOKEN, "job_id": data["job_id"], "result": result},
                timeout=30,
            )

        except SystemExit:
            raise
        except requests.RequestException as e:
            print(f"backend unreachable ({e}); retrying…")
            time.sleep(min(POLL_INTERVAL * 3, 30))
        except KeyboardInterrupt:
            print("\nworker stopped.")
            return
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
