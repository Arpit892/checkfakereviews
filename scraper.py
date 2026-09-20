"""
scraper.py
Live product + review scraping for Amazon.in and Flipkart.
"""

import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

SCRAPE_TIMEOUT = int(os.environ.get("SCRAPE_TIMEOUT", "20"))
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "").strip()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

BLOCK_MARKERS = (
    "enter the characters you see below",
    "type the characters you see in this image",
    "api-services-support@amazon.com",
    "to discuss automated access",
    "robot check",
    "are you a human",
    "access denied",
    "request blocked",
)


class ScrapeError(Exception):
    """Raised when scraping fails. `reason` is a stable machine-readable code."""

    def __init__(self, reason: str, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.reason = reason  # BLOCKED | HTTP_ERROR | NETWORK | NO_REVIEWS | UNSUPPORTED_SITE
        self.message = message
        self.status = status


@dataclass
class Review:
    reviewer_name: str
    text: str
    rating: Optional[float] = None
    verified: bool = False
    meta: dict = field(default_factory=dict)


@dataclass
class ProductPage:
    site: str
    product_id: str
    product_name: str
    reviews: List[Review]
    source_url: str


# --------------------------------------------------------------------------
# Helper utilities
# --------------------------------------------------------------------------

def _clean(text: Optional[str]) -> str:
    """Normalize whitespace and strip text."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# URL handling
# --------------------------------------------------------------------------

def detect_site(url: str) -> str:
    low = url.lower()
    if "amazon." in low:
        return "amazon"
    if "flipkart." in low:
        return "flipkart"
    return "unknown"


def extract_product_id(url: str) -> Optional[str]:
    """
    Amazon: /dp/ASIN, /gp/product/ASIN, /product-reviews/ASIN
    Flipkart: ?pid=ITM... or /p/itm...
    """
    site = detect_site(url)

    if site == "amazon":
        m = (
            re.search(r"/dp/([A-Z0-9]{10})", url, re.I)
            or re.search(r"/gp/product/([A-Z0-9]{10})", url, re.I)
            or re.search(r"/product-reviews/([A-Z0-9]{10})", url, re.I)
            or re.search(r"[?&]asin=([A-Z0-9]{10})", url, re.I)
        )
        return m.group(1).upper() if m else None

    if site == "flipkart":
        m = re.search(r"/p/(itm[A-Za-z0-9]+)", url)
        if m:
            return m.group(1)
        m = re.search(r"[?&]pid=([A-Za-z0-9]+)", url)
        return m.group(1) if m else None

    return None


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }


def proxy_mode() -> str:
    if SCRAPERAPI_KEY:
        return "scraperapi"
    if PROXY_URL:
        return "proxy"
    return "direct"


def fetch_html(url: str, retries: int = 2) -> str:
    last_err = None
    for attempt in range(retries + 1):
        try:
            if SCRAPERAPI_KEY:
                target = (
                    "http://api.scraperapi.com/"
                    f"?api_key={SCRAPERAPI_KEY}&country_code=in&url={quote_plus(url)}"
                )
                resp = requests.get(target, timeout=SCRAPE_TIMEOUT + 40)
            else:
                proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
                resp = requests.get(
                    url, headers=_headers(), timeout=SCRAPE_TIMEOUT, proxies=proxies
                )

            if resp.status_code in (403, 429, 503):
                raise ScrapeError(
                    "BLOCKED",
                    f"Store returned HTTP {resp.status_code} — the request was refused, "
                    "almost certainly bot detection on this server's IP.",
                    resp.status_code,
                )
            if resp.status_code >= 400:
                raise ScrapeError(
                    "HTTP_ERROR", f"Store returned HTTP {resp.status_code}.", resp.status_code
                )

            html = resp.text
            low = html[:20000].lower()
            if any(m in low for m in BLOCK_MARKERS):
                raise ScrapeError(
                    "BLOCKED",
                    "Served a CAPTCHA / bot-check page instead of the product page.",
                    resp.status_code,
                )
            if len(html) < 2000:
                raise ScrapeError(
                    "BLOCKED", "Response was suspiciously small — likely an interstitial.", resp.status_code
                )
            return html

        except ScrapeError as e:
            last_err = e
            if e.reason == "BLOCKED" and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except requests.RequestException as e:
            last_err = ScrapeError("NETWORK", f"Network error contacting the store: {e}")
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_err

    raise last_err


# --------------------------------------------------------------------------
# Amazon parsing
# --------------------------------------------------------------------------

def parse_amazon(html: str) -> Tuple[str, List[Review]]:
    soup = BeautifulSoup(html, "html.parser")

    # Extract Product Name
    product_name = ""
    for sel in ("#productTitle", "h1#title", "h1", "a[data-hook='product-link']"):
        el = soup.select_one(sel)
        if el and _clean(el.get_text()):
            product_name = _clean(el.get_text())
            break
    if not product_name:
        og = soup.find("meta", property="og:title")
        product_name = _clean(og["content"]) if og and og.get("content") else "Amazon Product"

    reviews: List[Review] = []

    # Amazon review containers
    review_elements = soup.find_all("div", {"data-hook": "review"})
    if not review_elements:
        review_elements = soup.select(".review, #cm-cr-dp-review-list .a-section, div[id^='customer_review-']")

    for el in review_elements:
        # Review text
        body_el = el.find("span", {"data-hook": "review-body"}) or el.select_one(".review-text-content, .review-text")
        text = _clean(body_el.get_text()) if body_el else ""
        text = re.sub(r"\s*(Read more)\s*$", "", text, flags=re.I)
        if not text or len(text) < 4:
            continue

        # Reviewer name
        author_el = el.find("span", class_="a-profile-name")
        reviewer_name = _clean(author_el.get_text()) if author_el else "Amazon Customer"

        # Rating
        rating = None
        rating_el = el.find("i", {"data-hook": "review-star-rating"}) or el.find("i", {"data-hook": "cmps-review-star-rating"}) or el.select_one(".a-icon-alt")
        if rating_el:
            rm = re.search(r"([1-5](?:\.[0-9])?)", rating_el.get_text())
            if rm:
                rating = float(rm.group(1))

        # Verified purchase
        verified = bool(el.find("span", {"data-hook": "avp-badge"}) or "Verified Purchase" in el.get_text())

        reviews.append(
            Review(
                reviewer_name=reviewer_name,
                text=text,
                rating=rating,
                verified=verified,
            )
        )

    # Deduplicate on text
    seen, deduped = set(), []
    for r in reviews:
        key = r.text.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    return product_name, deduped


# Alias for backward compatibility
parse_amazon_reviews = parse_amazon


# --------------------------------------------------------------------------
# Flipkart parsing
# --------------------------------------------------------------------------

def parse_flipkart(html: str) -> Tuple[str, List[Review]]:
    soup = BeautifulSoup(html, "html.parser")

    product_name = ""
    for sel in ("span.VU-ZEz", "span.B_NuCI", "h1 span", "h1"):
        el = soup.select_one(sel)
        if el and _clean(el.get_text()):
            product_name = _clean(el.get_text())
            break
    if not product_name:
        og = soup.find("meta", property="og:title")
        product_name = _clean(og["content"]) if og and og.get("content") else "Unknown product"

    reviews: List[Review] = []

    for sel in ("div.ZmyHeo div div", "div.ZmyHeo", "div.t-ZTKy div div", "div.t-ZTKy", "div.qwjRop div"):
        nodes = soup.select(sel)
        if len(nodes) >= 2:
            for n in nodes:
                text = _clean(n.get_text(" "))
                text = re.sub(r"\s*(READ MORE|\.\.\.more)\s*$", "", text, flags=re.I)
                if len(text) < 4:
                    continue
                container = n.find_parent("div")
                name = "Flipkart Customer"
                rating = None
                verified = False
                hop = container
                for _ in range(5):
                    if hop is None:
                        break
                    for cand in ("p._2NsDsF", "p._2sc7ZR", "p.AwS1CA"):
                        nm = hop.select_one(cand)
                        if nm and _clean(nm.get_text()):
                            name = _clean(nm.get_text())
                            break
                    blob = hop.get_text(" ")
                    if "Certified Buyer" in blob:
                        verified = True
                    rm = re.search(r"\b([1-5])\s*★", blob) or re.search(r"^\s*([1-5])\b", blob)
                    if rm and rating is None:
                        rating = float(rm.group(1))
                    if name != "Flipkart Customer":
                        break
                    hop = hop.parent
                reviews.append(
                    Review(reviewer_name=name, text=text, rating=rating, verified=verified)
                )
            if reviews:
                break

    if not reviews:
        for div in soup.find_all("div"):
            blob = _clean(div.get_text(" "))
            if "Certified Buyer" not in blob or len(blob) > 600 or len(blob) < 30:
                continue
            body = re.split(r"Certified Buyer", blob)[0]
            body = re.sub(r"^\s*[1-5]\s*", "", body)
            body = re.sub(r"\s*(READ MORE)\s*$", "", body, flags=re.I)
            if len(body) < 8:
                continue
            reviews.append(Review(reviewer_name="Flipkart Customer", text=_clean(body), verified=True))
            if len(reviews) >= 10:
                break

    seen, deduped = set(), []
    for r in reviews:
        key = r.text.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    return product_name, deduped


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def scrape_product(url: str, max_reviews: int = 5) -> ProductPage:
    site = detect_site(url)
    if site == "unknown":
        raise ScrapeError(
            "UNSUPPORTED_SITE", "Only Amazon and Flipkart product links are supported right now."
        )

    product_id = extract_product_id(url)
    if not product_id:
        raise ScrapeError(
            "UNSUPPORTED_SITE",
            "Couldn't find a product ID in that link. Amazon links need /dp/<ASIN>; "
            "Flipkart links need /p/itm... or ?pid=...",
        )

    if site == "amazon":
        domain = re.search(r"https?://([^/]+)", url)
        host = domain.group(1) if domain else "www.amazon.in"
        review_url = f"https://{host}/product-reviews/{product_id}/?sortBy=recent&pageNumber=1"
        html = fetch_html(review_url)
        product_name, reviews = parse_amazon(html)
        if not reviews:
            html = fetch_html(f"https://{host}/dp/{product_id}")
            product_name, reviews = parse_amazon(html)
        source = review_url
    else:
        source = url
        html = fetch_html(url)
        product_name, reviews = parse_flipkart(html)

    if not reviews:
        raise ScrapeError(
            "NO_REVIEWS",
            "Page loaded but no reviews could be parsed. Either the product has no "
            "reviews, or the site changed its markup and the selectors need updating.",
        )

    return ProductPage(
        site=site,
        product_id=product_id,
        product_name=product_name,
        reviews=reviews[:max_reviews],
        source_url=source,
    )
