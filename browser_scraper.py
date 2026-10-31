"""Local PC browser scraper for Amazon and Flipkart reviews.

It does NOT bypass CAPTCHAs. If the store asks for verification, you can
complete it yourself in the open browser window on your PC.
The browser profile stays on your PC; never upload it or commit it to GitHub.
"""
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

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
    return bool(BLOCK_TEXT.search(body) or BLOCK_CODE.search(body))


FK_BLOCK_START = re.compile(r"^([1-5])\.0$")
FK_DATE = re.compile(r"^·?\s*(\d+\s+(?:hours?|days?|weeks?|months?|years?)\s+ago|Today|Yesterday|[A-Za-z]{3,9},?\s+\d{4})$", re.I)


def parse_flipkart_text(text: str):
    """Read Flipkart reviews from the visible page text (class names change too often).

    Each review on the page looks like:
      5.0 / • / Title / [Review for: ...] / review text... / Name / , City / Helpful.. / [Verified Purchase] / · 3 days ago
    """
    from scraper import Review

    lines = [l.strip() for l in text.replace("&rsquo;", "'").splitlines() if l.strip()]
    reviews, i = [], 0
    while i < len(lines) - 4:
        m = FK_BLOCK_START.match(lines[i])
        if not (m and lines[i + 1] == "•"):
            i += 1
            continue
        # find the date line that ends this review (within 40 lines)
        end = next((j for j in range(i + 2, min(i + 40, len(lines))) if FK_DATE.match(lines[j])), None)
        if end is None:
            i += 1
            continue
        block = lines[i + 2:end]
        title = block[0] if block else ""
        # author is the line just before a ", City" line
        city_idx = next((k for k, l in enumerate(block) if l.startswith(",")), None)
        if city_idx is None or city_idx < 2:
            i = end + 1
            continue
        author = block[city_idx - 1]
        body = [l for l in block[1:city_idx - 1] if not l.startswith("Review for:")]
        body_text = re.sub(r"\s*(READ MORE|\.\.\.\s*more)$", "", " ".join(body), flags=re.I).strip() or title
        verified = any(l in ("Verified Purchase", "Certified Buyer") for l in block[city_idx:])
        date = FK_DATE.match(lines[end]).group(1)
        reviews.append(Review(author, body_text, float(m.group(1)), verified, {"date": date, "title": title}))
        i = end + 1
    return reviews


def _read(page, site):
    name, reviews = (parse_amazon if site == "amazon" else parse_flipkart)(page.content())
    if site == "flipkart" and not reviews:
        try:
            reviews = parse_flipkart_text(page.locator("body").inner_text(timeout=5000))
        except Exception:  # noqa: BLE001
            reviews = []
        if not name or name == "Unknown product":
            name = re.split(r"\s+Reviews?\b|\s*[-|:]\s*Flipkart", page.title(), flags=re.I)[0].strip() or "Unknown product"
    return name, reviews


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
            if site == "amazon" and "/ap/signin" in page.url:
                # Amazon now hides the full reviews page behind sign-in. Read the
                # reviews shown on the product page instead (no login needed).
                print("  Amazon wants sign-in for the reviews page; reading the product page reviews instead.")
                host = urlsplit(target).hostname
                page.goto(f"https://{host}/dp/{product_id}", wait_until="domcontentloaded", timeout=45000)
                try:
                    page.locator('[data-hook="review"]').first.scroll_into_view_if_needed(timeout=8000)
                except Exception:  # noqa: BLE001
                    pass
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

            for more in page.get_by_text(re.compile(r"^read more$", re.I)).all()[:max_reviews]:
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
