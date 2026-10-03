"""
remote_worker.py
Run this on YOUR machine (laptop, home PC, Raspberry Pi). It polls your Render
backend for scrape jobs, does the scraping from your home IP — the one that
already works — and posts the results back.

This is the free way around the CAPTCHA/IP-block. Render never talks to
Amazon or Flipkart directly for jobs it hands off; it only holds the queue
and the results. Your IP does the fetching.

Setup:
    1. On Render, set an env var:   WORKER_TOKEN=<some long random string>
    2. On your machine (PowerShell — use $env:, NOT `set`, which leaves
       literal quote characters in the value and breaks the URL):
           pip install requests beautifulsoup4
           $env:BACKEND_URL="https://checkfakereviews.online"
           $env:WORKER_TOKEN="<the same string>"
           python remote_worker.py

    Optional, for Flipkart: Flipkart's block is a real reCAPTCHA challenge,
    not just IP reputation, so plain requests almost never get through, even
    from a home IP. A real headless browser has a better (not guaranteed)
    chance:
           pip install playwright
           playwright install chromium
    With that installed, Flipkart jobs automatically use the browser path.
    Without it, they fall back to the plain scraper, which will likely fail
    with BLOCKED — that's expected, not a bug.

    Optional, for a second worker on a cloud VM (e.g. one that only handles
    Flipkart since Amazon blocks datacenter IPs):
           $env:WORKER_SITES="flipkart"

Keep it running. /api/health on the server will show "worker_online": true
(and a per-site breakdown under "worker_sites_online"). When no worker for a
given site is online, the server falls back to trying directly from its own
IP (and usually failing) or to demo data.

Caveats, stated plainly:
    * Your site only analyzes new products while this process is running.
      Closing this terminal (or restarting your computer) stops it — it is
      not a background service.
    * Your home IP is now doing the scraping for every visitor. If the site
      gets real traffic, Amazon will eventually rate-limit your home
      connection too. Throttle with POLL_INTERVAL and MIN_GAP_SECONDS.
    * Flipkart is not guaranteed to work even with Playwright — reCAPTCHA
      Enterprise is specifically built to detect headless automation too.
      The paste-HTML option on the site remains the reliable fallback.
    * This is fine for a portfolio/demo project. It is not a scaling plan.
"""

import os
import time
import traceback

import requests

from analyze_live import analyze_reviews
from scraper import ScrapeError, detect_site, scrape_product

try:
    from browser_scraper import PLAYWRIGHT_AVAILABLE, scrape_flipkart_via_browser
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


def _clean_env(name: str, default: str = "") -> str:
    """
    Reads an env var and strips accidental wrapping quotes. `cmd.exe`'s `set
    VAR="value"` keeps the quote characters as part of the value (unlike
    PowerShell's $env: or a real shell's export), which silently breaks any
    URL or token built from it — e.g. requests fails with "No connection
    adapters were found for '\"https://...\"/path'". Stripping here means a
    pasted value with stray quotes still works instead of failing confusingly.
    """
    val = os.environ.get(name, default).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        val = val[1:-1].strip()
    return val


BACKEND_URL = _clean_env("BACKEND_URL", "http://localhost:8000").rstrip("/")
WORKER_TOKEN = _clean_env("WORKER_TOKEN")
POLL_INTERVAL = float(_clean_env("POLL_INTERVAL", "5") or "5")
MIN_GAP_SECONDS = float(_clean_env("MIN_GAP_SECONDS", "8") or "8")
MAX_REVIEWS = int(_clean_env("MAX_REVIEWS", "5") or "5")
USE_BROWSER_FOR_FLIPKART = _clean_env("USE_BROWSER_FOR_FLIPKART", "1") == "1"

# Which sites this worker will take jobs for. Leave unset on your home machine
# (handles everything). On a cloud VM set WORKER_SITES=flipkart — datacenter
# IPs get blocked by Amazon, so that worker shouldn't claim Amazon jobs.
WORKER_SITES = _clean_env("WORKER_SITES").lower()

_last_scrape = 0.0


def process(job: dict) -> dict:
    global _last_scrape

    gap = MIN_GAP_SECONDS - (time.time() - _last_scrape)
    if gap > 0:
        time.sleep(gap)          # be polite; avoid burning your home IP
    _last_scrape = time.time()

    url = job["url"]
    site = detect_site(url)
    try:
        if site == "flipkart" and USE_BROWSER_FOR_FLIPKART and PLAYWRIGHT_AVAILABLE:
            page = scrape_flipkart_via_browser(url, max_reviews=MAX_REVIEWS)
        else:
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
        return {"error": e.message, "error_code": e.reason, "product_url": url, "site": site}
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return {"error": f"Worker failure: {e}", "error_code": "INTERNAL", "product_url": url, "site": site}


def main():
    if not WORKER_TOKEN:
        raise SystemExit("Set WORKER_TOKEN (must match the value on the server).")
    if not BACKEND_URL.startswith(("http://", "https://")):
        raise SystemExit(
            f"BACKEND_URL doesn't look like a URL: {BACKEND_URL!r} — check for stray quotes "
            "or a missing https:// (this is usually a `set` vs `$env:` quoting issue)."
        )

    print(f"Worker started. Backend={BACKEND_URL}  poll={POLL_INTERVAL}s")
    print(f"Handles sites: {WORKER_SITES or 'all'}")
    print(
        f"Flipkart via real browser: "
        f"{'ON' if (USE_BROWSER_FOR_FLIPKART and PLAYWRIGHT_AVAILABLE) else 'OFF'}"
        + ("" if PLAYWRIGHT_AVAILABLE else " (playwright not installed)")
    )
    idle_logged = False

    while True:
        try:
            resp = requests.get(
                f"{BACKEND_URL}/api/worker/next",
                params={"token": WORKER_TOKEN, "sites": WORKER_SITES},
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
