

import os
import time
import traceback

import requests

from analyze_live import analyze_reviews
from scraper import ScrapeError, detect_site, scrape_product

try:
    from browser_scraper import PLAYWRIGHT_AVAILABLE, scrape_via_browser
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
USE_BROWSER = _clean_env("USE_BROWSER", "1") == "1"

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
        if USE_BROWSER:
            if not PLAYWRIGHT_AVAILABLE:
                raise ScrapeError("BROWSER_MISSING", "Install requirements-worker.txt and run: python -m playwright install chromium")
            page = scrape_via_browser(url, max_reviews=MAX_REVIEWS)
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
    print("Handles sites: all")
    print(
        f"Amazon + Flipkart via PC browser: "
        f"{'ON' if (USE_BROWSER and PLAYWRIGHT_AVAILABLE) else 'OFF'}"
        + ("" if PLAYWRIGHT_AVAILABLE else " (playwright not installed)")
    )
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
            resp.raise_for_status()
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

            # Keep the result until Render confirms it was saved.
            while True:
                try:
                    posted = requests.post(
                        f"{BACKEND_URL}/api/worker/result",
                        json={"token": WORKER_TOKEN, "job_id": data["job_id"], "result": result},
                        timeout=30,
                    )
                    if posted.status_code == 401:
                        raise SystemExit("Server rejected the worker token.")
                    posted.raise_for_status()
                    break
                except requests.RequestException:
                    print("  result upload failed; retrying in 5s...")
                    time.sleep(5)

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
