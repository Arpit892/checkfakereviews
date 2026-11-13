"""price_tracker.py — reads a product's current price (runs on YOUR PC).

Used for the side-by-side comparison and for Telegram price-drop alerts
(checked every PRICE_CHECK_HOURS, default 6, by remote_worker.py).
Never bypasses CAPTCHAs: solve any check yourself in the open browser window.
"""
import os
import re
from pathlib import Path
from typing import Optional, Tuple

from review_targets import reviews_url, site_for_url

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

PRICE_RE = re.compile(r"₹\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
AMAZON_PRICE_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-price-whole",
    "#corePrice_feature_div .a-offscreen",
    ".priceToPay .a-offscreen",
    "#priceblock_dealprice",
    "#priceblock_ourprice",
    "#apex_desktop .a-price .a-offscreen",
]


def parse_price(text: str) -> Optional[float]:
    """'₹1,29,999.00' -> 129999.0 ; text with no number -> None."""
    if not text:
        return None
    m = PRICE_RE.search(text) or re.search(r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def first_price_after(text: str, anchor: str = "") -> Optional[float]:
    """Flipkart class names change constantly, so read page text: the selling
    price is the first ₹ amount shown after the product name."""
    if not text:
        return None
    start = text.find(anchor[:40]) if anchor else -1
    window = text[start:start + 3000] if start >= 0 else text[:6000]
    return parse_price(window) if PRICE_RE.search(window) else None


def should_alert(old_price: Optional[float], new_price: Optional[float], target: Optional[float]) -> bool:
    """Alert when the price went DOWN since the last check and, if the user
    set a target, the new price is at or below it."""
    if new_price is None or old_price is None or new_price >= old_price:
        return False
    return target is None or new_price <= target


def read_price_on_page(page, site: str) -> Optional[float]:
    if site == "amazon":
        for sel in AMAZON_PRICE_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count():
                    value = parse_price(loc.text_content(timeout=2000) or "")
                    if value:
                        return value
            except Exception:  # noqa: BLE001
                continue
        return None
    try:
        title = page.title() or ""
        body = page.locator("body").inner_text(timeout=5000)
    except Exception:  # noqa: BLE001
        return None
    name = re.split(r"\s+Reviews?\b|\s*[-|:]\s*(?:Buy|Flipkart)", title, flags=re.I)[0].strip()
    return first_price_after(body, name)


def fetch_price(url: str) -> Tuple[str, Optional[float]]:
    """Open the product in the PC browser and return (product name, price)."""
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Install requirements-worker.txt and run: python -m playwright install chromium")
    site = site_for_url(url)
    if site == "unknown":
        raise ValueError("Only Amazon and Flipkart links are supported.")
    headless = os.environ.get("BROWSER_HEADLESS", "0") == "1"
    profile = Path(os.environ.get("BROWSER_PROFILE_DIR", str(Path.home() / ".checkfakereviews-browser"))).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile), headless=headless,
                                                   viewport={"width": 1280, "height": 1800}, locale="en-IN")
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            price = read_price_on_page(page, site)
            if price is None and site == "flipkart":
                # Product page is often bot-blocked; the reviews page shows the price too.
                try:
                    page.goto(reviews_url(page.url)[0], wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(3000)
                    price = read_price_on_page(page, site)
                except Exception:  # noqa: BLE001
                    pass
            name = re.split(r"\s*[-|:]\s*(?:Buy|Flipkart|Amazon)", page.title() or "", flags=re.I)[0].strip()
            return name or "Product", price
        finally:
            ctx.close()


if __name__ == "__main__":
    import sys
    print(fetch_price(sys.argv[1]))
