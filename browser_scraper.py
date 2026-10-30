"""Local PC browser scraper for Amazon and Flipkart reviews.

It does NOT bypass CAPTCHAs. If the store asks for verification, you can
complete it yourself in the open browser window on your PC.
The browser profile stays on your PC; never upload it or commit it to GitHub.
"""
import os
import re
import time
from pathlib import Path

from review_targets import reviews_url, site_for_url
from scraper import ProductPage, ScrapeError, parse_amazon, parse_flipkart

try:
    from playwright.sync_api import TimeoutError as BrowserTimeout
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

BLOCK_TEXT = re.compile(
    r"enter the characters you see|robot check|captcha|access denied|request blocked|are you a human",
    re.I,
)
BLOCK_CODE = re.compile(r"\bE002\b")  # case-sensitive on purpose


def _blocked(page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:  # noqa: BLE001
        return False
    return bool(BLOCK_TEXT.search(body) or BLOCK_CODE.search(body)) or "/ap/signin" in page.url


def _read(page, site):
    return (parse_amazon if site == "amazon" else parse_flipkart)(page.content())


def _wait_for_reviews(page, site, headless):
    wait = 15 if headless else int(os.environ.get("BROWSER_MANUAL_WAIT_SECONDS", "90"))
    deadline = time.monotonic() + wait
    told = False
    while time.monotonic() < deadline:
        if not _blocked(page):
            _, reviews = _read(page, site)
            if reviews:
                return
            if site == "flipkart" and page.get_by_text("Latest", exact=True).count():
                return
        elif not told and not headless:
            print("  ! The store wants verification/sign-in. Complete it in the open browser window.")
            told = True
        page.wait_for_timeout(1000)
    if _blocked(page):
        raise ScrapeError("BLOCKED", "The store asked for verification and it was not completed. No score was generated.")
    raise ScrapeError("NO_REVIEWS", "No written reviews appeared on the reviews page. No score was generated.")


def scrape_via_browser(url: str, max_reviews: int = 5) -> ProductPage:
    if not PLAYWRIGHT_AVAILABLE:
        raise ScrapeError(
            "BROWSER_MISSING",
            "Install requirements-worker.txt and run: python -m playwright install chromium",
        )
    site = site_for_url(url)
    if site == "unknown":
        raise ScrapeError("UNSUPPORTED_SITE", "Only Amazon and Flipkart links are supported.")

    headless = os.environ.get("BROWSER_HEADLESS", "0") == "1"
    profile = Path(
        os.environ.get("BROWSER_PROFILE_DIR", str(Path.home() / ".checkfakereviews-browser"))
    ).expanduser()
    profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile), headless=headless, viewport={"width": 1280, "height": 1800}, locale="en-IN"
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()

            # Never let a link take the browser to a non-store site.
            def guard(route):
                req = route.request
                if req.is_navigation_request() and req.frame == page.main_frame and site_for_url(req.url) == "unknown":
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", guard)

            # Opening the pasted link first also resolves short links (amzn.in, fkrt.it ...).
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if site_for_url(page.url) != site:
                raise ScrapeError("UNSUPPORTED_SITE", "The link redirected outside the store.")
            try:
                target, product_id = reviews_url(page.url)
            except ValueError as exc:
                raise ScrapeError("INVALID_URL", str(exc)) from exc

            page.goto(target, wait_until="domcontentloaded", timeout=45000)
            _wait_for_reviews(page, site, headless)

            if site == "flipkart":
                # Flipkart ignores ?sort=recent; the "Latest" control must be clicked.
                latest = page.get_by_text("Latest", exact=True).first
                try:
                    latest.wait_for(state="visible", timeout=10000)
                    latest.click(timeout=10000)
                    page.wait_for_timeout(3000)
                    _wait_for_reviews(page, site, headless)
                except BrowserTimeout as exc:
                    raise ScrapeError(
                        "SORT_UNAVAILABLE",
                        "Could not switch Flipkart to Latest reviews, so no score was generated.",
                    ) from exc

            for more in page.get_by_text("READ MORE", exact=True).all()[:max_reviews]:
                try:
                    more.click(timeout=1000)
                except Exception:  # noqa: BLE001
                    pass

            name, reviews = _read(page, site)
            if not reviews:
                raise ScrapeError("NO_REVIEWS", "The reviews page loaded but no reviews could be read.")
            return ProductPage(site, product_id, name, reviews[:max_reviews], page.url)
        except BrowserTimeout as exc:
            raise ScrapeError("NETWORK", "The browser timed out loading the store. Try again.") from exc
        finally:
            context.close()


# Backwards-compatible name used by older remote_worker.py versions.
def scrape_flipkart_via_browser(url: str, max_reviews: int = 5) -> ProductPage:
    return scrape_via_browser(url, max_reviews)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Test one product scrape on this PC.")
    ap.add_argument("url")
    args = ap.parse_args()
    try:
        prod = scrape_via_browser(args.url)
    except ScrapeError as e:
        raise SystemExit(f"{e.reason}: {e.message}")
    print(prod.product_name, "\nSource:", prod.source_url)
    for r in prod.reviews:
        print(f"- {r.reviewer_name} | {r.rating} | {r.meta.get('date', '')}\n  {r.text[:200]}")
    print(f"Read {len(prod.reviews)} reviews.")
