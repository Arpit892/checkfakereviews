"""
It polls render
backend for scrape jobs, does the scraping from your home IP — the one that
already works — and posts the results back.

This is the free way around the CAPTCHA/IP-block. Render never talks to
Amazon or Flipkart directly for jobs it hands off.. it only holds the queue
and the results. Your IP does the fetching.


"""

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
import threading

import product_insights


_gap_lock = threading.Lock()
PARALLEL = max(1, min(3, int(_clean_env("PARALLEL_SCANS", "2") or "2")))


def process(job: dict, slot: int = 0) -> dict:
    global _last_scrape

    with _gap_lock:  # stagger starts a little; two scans still overlap
        gap = min(MIN_GAP_SECONDS, 3) - (time.time() - _last_scrape)
        if gap > 0:
            time.sleep(gap)
        _last_scrape = time.time()

    url = job["url"]
    site = detect_site(url)
    try:
        if USE_BROWSER:
            if not PLAYWRIGHT_AVAILABLE:
                raise ScrapeError("BROWSER_MISSING", "Install requirements-worker.txt and run: python -m playwright install chromium")
            page = scrape_via_browser(url, max_reviews=MAX_REVIEWS, slot=slot)
        else:
            page = scrape_product(url, max_reviews=MAX_REVIEWS)

        result = analyze_reviews(page.product_name, page.reviews)
        result.update(
            {
                "product_url": url,
                "product_id": page.product_id,
                "site": page.site,
                "source": "worker",
                "price": getattr(page, "price", None),
                "specs": getattr(page, "specs", {}),
                "insights": product_insights.analyze(
                    [r.text for r in page.reviews], getattr(page, "description", [])
                ),
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


# ------------------------- Telegram price alerts ----------------------------
import threading

import telegram_bot
from price_tracker import fetch_price, should_alert

PRICE_CHECK_HOURS = float(_clean_env("PRICE_CHECK_HOURS", "6") or "6")
ALERTS_PER_ROUND = 5
_last_alert_round = 0.0


def _api(path: str, **body):
    r = requests.post(f"{BACKEND_URL}{path}", json={"token": WORKER_TOKEN, **body}, timeout=60)
    r.raise_for_status()
    return r.json()


SERVER_TELEGRAM = False  # set in main(): True when Render owns the bot (v7)


def _server_config() -> dict:
    try:
        return requests.get(f"{BACKEND_URL}/api/config", timeout=90).json()
    except Exception as e:  # noqa: BLE001
        print(f"could not read {BACKEND_URL}/api/config ({e}); assuming the old setup")
        return {}


def telegram_loop():
    """Old setup only (bot token on the PC): answers /start, /list and /stop."""
    while True:
        try:
            for chat_id, text in telegram_bot.get_updates():
                cid = str(chat_id)
                reply = telegram_bot.reply_for(
                    text,
                    link=lambda code: _api("/api/worker/telegram-link", chat_id=cid, code=code).get("alert"),
                    list_alerts=lambda: _api("/api/worker/telegram-list", chat_id=cid).get("alerts", []),
                    stop=lambda: _api("/api/worker/telegram-stop", chat_id=cid).get("stopped", 0),
                    hours=PRICE_CHECK_HOURS,
                )
                telegram_bot.send(chat_id, reply)
        except RuntimeError as e:  # Render has the webhook: stop polling for good
            print(f"telegram: {e}")
            return
        except Exception as e:  # noqa: BLE001
            print(f"telegram: {e}; retrying in 10s")
            time.sleep(10)


def run_price_alerts():
    """Checks prices that are due and sends drop alerts. Looks every 10 min;
    each alert itself is only re-checked every PRICE_CHECK_HOURS."""
    global _last_alert_round, _last_scrape
    if time.time() - _last_alert_round < 600:
        return
    _last_alert_round = time.time()
    r = requests.get(f"{BACKEND_URL}/api/worker/alerts-due",
                     params={"token": WORKER_TOKEN, "every": int(PRICE_CHECK_HOURS * 3600)}, timeout=60)
    r.raise_for_status()
    for a in r.json().get("alerts", [])[:ALERTS_PER_ROUND]:
        gap = MIN_GAP_SECONDS - (time.time() - _last_scrape)
        if gap > 0:
            time.sleep(gap)
        _last_scrape = time.time()
        try:
            name, price = fetch_price(a["url"])
        except Exception as e:  # noqa: BLE001
            print(f"  price check failed for {a['url'][:60]}: {e}")
            name, price = "", None
        old = a.get("last_price")
        print(f"  price {telegram_bot.fmt_rupees(price)} (was {telegram_bot.fmt_rupees(old)}) {name[:40]}")
        res = _api("/api/worker/alert-price", alert_id=a["alert_id"], price=price, product_name=name)
        if res.get("notified") or not telegram_bot.enabled():
            continue  # Render already sent the Telegram message (v7)
        if old is None and price is not None:
            telegram_bot.send(a["chat_id"], telegram_bot.tracking_text(name, price))
        elif should_alert(old, price, a.get("target_price")):
            telegram_bot.send(a["chat_id"], telegram_bot.drop_text(name or a.get("product_name"), old, price, a["url"]))


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
    global SERVER_TELEGRAM
    SERVER_TELEGRAM = bool(_server_config().get("telegram_on_server"))
    if SERVER_TELEGRAM:
        print(f"Telegram price alerts: ON — Render answers the bot; this PC reads prices every {PRICE_CHECK_HOURS:g} h")
    elif telegram_bot.enabled():
        threading.Thread(target=telegram_loop, daemon=True).start()
        print(f"Telegram price alerts: ON via this PC (checks every {PRICE_CHECK_HOURS:g} h)")
    else:
        print("Telegram price alerts: OFF (add TELEGRAM_BOT_TOKEN in Render -> Environment)")
    if (SERVER_TELEGRAM or telegram_bot.enabled()) and not PLAYWRIGHT_AVAILABLE:
        print("  ! prices can't be read: install requirements-worker.txt and run python -m playwright install chromium")
    print(f"Parallel scans: {PARALLEL} (set PARALLEL_SCANS to change)")
    for slot in range(1, PARALLEL):
        threading.Thread(target=job_loop, args=(slot,), daemon=True).start()
    try:
        job_loop(0)
    except KeyboardInterrupt:
        print("\nworker stopped.")


def job_loop(slot: int = 0):
    idle_logged = False

    while True:
        try:
            if slot == 0 and (SERVER_TELEGRAM or telegram_bot.enabled()) and PLAYWRIGHT_AVAILABLE:
                try:
                    run_price_alerts()
                except requests.RequestException as e:
                    print(f"price alerts: backend unreachable ({e})")
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
            print(f"[scan {slot + 1}] job {data['job_id']}: {data['url'][:70]}")
            result = process(data, slot)

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
