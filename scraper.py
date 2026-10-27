"""
scraper.py
Live product + review scraping for Amazon.in and Flipkart.
"""

import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

SCRAPE_TIMEOUT = int(os.environ.get("SCRAPE_TIMEOUT", "20"))
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "").strip()
DEBUG_DUMP = os.environ.get("DEBUG_DUMP", "1") == "1"
DEBUG_DIR = os.environ.get("DEBUG_DIR", "debug_dumps")

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
    def __init__(self, reason: str, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.reason = reason
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


def detect_site(url: str) -> str:
    low = url.lower()
    if "amazon." in low or "amzn.in" in low or "amzn.to" in low or "a.co" in low:
        return "amazon"
    if "flipkart." in low or "fkrt." in low or "dl.flipkart.com" in low:
        return "flipkart"
    return "unknown"


def is_short_link(url: str) -> bool:
    low = url.lower()
    if any(d in low for d in ("amzn.in", "amzn.to", "a.co", "fkrt.", "dl.flipkart.com")):
        return True
    if "flipkart.com" in low and re.search(r"/s/[A-Za-z0-9]+", url):
        return True
    return False


def proxy_mode() -> str:
    """Reports which scraping path is active, for /api/health and error hints."""
    if SCRAPERAPI_KEY:
        return "scraperapi"
    if PROXY_URL:
        return "proxy"
    return "direct"


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


def resolve_short_link(url: str) -> str:
    """
    Phone share buttons produce short links (amzn.in/d/xxx, dl.flipkart.com/s/xxx)
    with no ASIN/pid anywhere in them. HEAD first; GET fallback; then scan the
    body for a meta-refresh / canonical / embedded product URL, since some
    short-link services (dl.flipkart.com) redirect via JS, not HTTP 3xx.
    """
    try:
        r = requests.head(url, headers=_headers(), timeout=10, allow_redirects=True)
        if r.url and r.url != url:
            return r.url
    except requests.RequestException:
        pass

    body = ""
    try:
        r = requests.get(url, headers=_headers(), timeout=15, allow_redirects=True)
        if r.url and r.url != url:
            return r.url
        body = r.text
    except requests.RequestException:
        pass

    if body:
        m = re.search(r'http-equiv=["\']refresh["\'][^>]*content=["\'][^"\']*url=([^"\']+)', body, re.I)
        if m:
            return m.group(1)
        m = (
            re.search(r'<link rel=["\']canonical["\'] href=["\']([^"\']+)["\']', body, re.I)
            or re.search(r'property=["\']og:url["\'][^>]*content=["\']([^"\']+)["\']', body, re.I)
            or re.search(r'(https?://(?:www\.)?flipkart\.com/[^\s"\'\\]+/p/itm[A-Za-z0-9]+[^\s"\'\\]*)', body)
        )
        if m:
            return m.group(1)

    return url


def _dump_debug_html(html: str, tag: str) -> Optional[str]:
    if not DEBUG_DUMP:
        return None
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        path = os.path.join(DEBUG_DIR, f"{tag}_{int(time.time())}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path
    except OSError:
        return None


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

            # 403/429/503 are classic bot-detection responses. The rest of this
            # set (500/502/504, and the 520-530 Cloudflare-style "overloaded"
            # range, which 529 falls in) usually means a WAF or rate-limiter in
            # front of the store is throttling this IP, not that the page
            # itself is broken — same underlying problem, worth retrying and
            # backing off rather than giving up immediately.
            THROTTLE_CODES = {403, 429, 500, 502, 503, 504} | set(range(520, 531))
            if resp.status_code in THROTTLE_CODES:
                dump = _dump_debug_html(resp.text, f"blocked_http{resp.status_code}")
                if resp.status_code in (403, 429, 503):
                    reason = "the request was refused, almost certainly bot detection on this server's IP."
                else:
                    reason = (
                        "the store (or a filter in front of it) is reporting overload/throttling — "
                        "usually temporary, and often caused by making many requests in a short "
                        "time from the same IP."
                    )
                raise ScrapeError(
                    "BLOCKED",
                    f"Store returned HTTP {resp.status_code} — {reason}"
                    + (f" Raw response saved to {dump}." if dump else ""),
                    resp.status_code,
                )
            if resp.status_code >= 400:
                raise ScrapeError(
                    "HTTP_ERROR", f"Store returned HTTP {resp.status_code}.", resp.status_code
                )

            html = resp.text
            low = html[:20000].lower()
            if any(m in low for m in BLOCK_MARKERS):
                dump = _dump_debug_html(html, "blocked_captcha")
                raise ScrapeError(
                    "BLOCKED",
                    "Served a CAPTCHA / bot-check page instead of the product page."
                    + (f" Raw response saved to {dump}." if dump else ""),
                    resp.status_code,
                )
            if len(html) < 2000:
                dump = _dump_debug_html(html, "blocked_tiny")
                raise ScrapeError(
                    "BLOCKED",
                    "Response was suspiciously small — likely an interstitial."
                    + (f" Raw response saved to {dump}." if dump else ""),
                    resp.status_code,
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

    raise last_err  # pragma: no cover


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def extract_product_id(url: str) -> Optional[str]:
    site = detect_site(url)

    if site == "amazon":
        m = (
            re.search(r"/dp/([A-Z0-9]{10})", url, re.I)
            or re.search(r"/gp/product/([A-Z0-9]{10})", url, re.I)
            or re.search(r"/gp/aw/d/([A-Z0-9]{10})", url, re.I)
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


def parse_amazon(html: str) -> tuple:
    soup = BeautifulSoup(html, "html.parser")

    title_el = (
        soup.select_one("#productTitle")
        or soup.select_one("span#productTitle")
        or soup.select_one('h1[data-hook="product-link"]')
    )
    product_name = _clean(title_el.get_text()) if title_el else ""
    if not product_name:
        og = soup.find("meta", property="og:title")
        product_name = _clean(og["content"]) if og and og.get("content") else "Unknown product"

    blocks = (
        soup.select('div[data-hook="review"]')
        or soup.select("div.review")
        or soup.select('li[data-hook="review"]')
        or soup.select("div.a-section.review")
    )
    reviews = []
    for b in blocks:
        body_el = (
            b.select_one('div[data-hook="reviewRichContentContainer"]')
            or b.select_one('span[data-hook="review-body"] span')
            or b.select_one('span[data-hook="review-body"]')
            or b.select_one(".review-text-content span")
            or b.select_one(".review-text-content")
        )
        text = _clean(body_el.get_text(" ")) if body_el else ""
        if not text:
            continue

        name_el = b.select_one("span.a-profile-name")
        name = _clean(name_el.get_text()) if name_el else "Amazon Customer"

        rating = None
        r_el = b.select_one('i[data-hook="review-star-rating"] span') or b.select_one(
            'i[data-hook="cmps-review-star-rating"] span'
        )
        if r_el:
            m = re.search(r"([\d.]+)", r_el.get_text())
            if m:
                rating = float(m.group(1))

        verified = b.select_one('span[data-hook="avp-badge"]') is not None

        date_el = b.select_one('span[data-hook="review-date"]')
        reviews.append(
            Review(
                reviewer_name=name,
                text=text,
                rating=rating,
                verified=verified,
                meta={"date": _clean(date_el.get_text()) if date_el else ""},
            )
        )

    return product_name, reviews


# Flipkart's real classes are hashed/auto-generated and rotate on every
# deploy — not worth targeting. Flipkart also serves at least two different
# text renderings of the same review widget. The one thing common to both:
# every review ends with "Verified Purchase · Mon, Year" (verified badge
# optional, date/bullet always renders), preceded by "Name , Location".
_FK_NAME_WORD = r"[A-Z][a-zA-Z.'\-]*"
_FK_NAME = _FK_NAME_WORD + r"(?:\s+" + _FK_NAME_WORD + r"){0,3}"

FK_NAME_LOCATION_RE = re.compile(r"(?P<name>" + _FK_NAME + r")\s*,\s*(?P<location>" + _FK_NAME + r")")
FK_REVIEW_END_RE = re.compile(r"(?P<verified>Verified Purchase\s*)?·\s*(?P<date>[A-Za-z]+,?\s*\d{4})")
FK_REVIEWS_START_RE = re.compile(r"reviews?\s+sorted\s+by\s*", re.I)
FK_RATING_PREFIX_RE = re.compile(r"^\s*(\d\.\d)\s*•\s*")

# Overall-rating badge e.g. "4.3 | 6" — total ratings count for the product,
# as distinct from the number of WRITTEN reviews (which can be zero even
# when ratings exist). Useful for telling "no reviews to find" apart from
# "scraper broke."
FK_RATING_BADGE_RE = re.compile(r"(\d\.\d)\s*\|\s*(\d+)\b")
FK_RATINGS_AND_REVIEWS_RE = re.compile(r"([\d,]+)\s+ratings?\s+and\s+([\d,]+)\s+reviews?", re.I)

# Below this many total ratings, Flipkart very often has zero WRITTEN reviews
# at all (just star ratings) — the review feed section doesn't render
# anything. The fallback CSS-selector scans below are also extremely
# expensive on a large, deeply-nested PDP (a full product page with many
# "Similar Products" cards can take 60+ seconds to walk with soupsieve), so
# skip them entirely once we already know there's unlikely to be anything to
# find — this is both a correctness and a performance fix.
MIN_RATINGS_FOR_REVIEWS = int(os.environ.get("MIN_RATINGS_FOR_REVIEWS", "10"))


def _parse_flipkart_reviews_from_text(soup: BeautifulSoup) -> List[Review]:
    text = soup.get_text(" ", strip=True)

    start_m = FK_REVIEWS_START_RE.search(text)
    start_idx = start_m.end() if start_m else 0

    reviews = []
    prev_end = start_idx
    for m in FK_REVIEW_END_RE.finditer(text):
        if m.start() < start_idx:
            continue
        chunk = text[prev_end:m.start()]
        prev_end = m.end()

        nl_matches = list(FK_NAME_LOCATION_RE.finditer(chunk))
        if not nl_matches:
            continue
        nl = nl_matches[-1]

        body = chunk[: nl.start()]
        rating_m = FK_RATING_PREFIX_RE.match(body)
        rating = float(rating_m.group(1)) if rating_m else None
        if rating_m:
            body = body[rating_m.end():]
        body = re.sub(r"Review for:\s*\S+\s+\S+\s*", "", body, count=1).strip()

        if len(body) < 3 or len(body) > 2000:
            continue

        reviews.append(
            Review(
                reviewer_name=_clean(nl.group("name")),
                text=_clean(body),
                rating=rating,
                verified=bool(m.group("verified")),
                meta={"date": m.group("date"), "location": _clean(nl.group("location"))},
            )
        )
    return reviews


def flipkart_total_ratings_count(soup: BeautifulSoup) -> Optional[int]:
    """
    Best-effort read of how many total ratings the product has, from
    whichever summary text is present. Returns None if neither pattern is
    found (don't guess).
    """
    text = soup.get_text(" ", strip=True)
    m = FK_RATINGS_AND_REVIEWS_RE.search(text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = FK_RATING_BADGE_RE.search(text)
    if m:
        try:
            return int(m.group(2))
        except ValueError:
            pass
    return None


def parse_flipkart(html: str) -> tuple:
    soup = BeautifulSoup(html, "html.parser")

    product_name = ""
    for sel in ("span.VU-ZEz", "span.B_NuCI", "h1 span", "h1"):
        el = soup.select_one(sel)
        if el and _clean(el.get_text()):
            product_name = _clean(el.get_text())
            break
    if not product_name:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            product_name = _clean(og["content"])
            product_name = re.split(r"\s+Reviews:\s+Latest Review of", product_name)[0].strip()
    if not product_name:
        product_name = "Unknown product"

    reviews = _parse_flipkart_reviews_from_text(soup)
    if reviews:
        return product_name, reviews

    # The fast text-pattern method found nothing. Before trying the much
    # slower CSS-selector fallbacks below, check whether the product simply
    # doesn't have enough ratings to have a written-review feed in the first
    # place — if so, stop here rather than spending up to a minute walking a
    # large page's DOM for something that was never going to be there.
    ratings_count = flipkart_total_ratings_count(soup)
    if ratings_count is not None and ratings_count < MIN_RATINGS_FOR_REVIEWS:
        return product_name, []

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
                    if "Certified Buyer" in blob or "Verified Purchase" in blob:
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
            badge = "Certified Buyer" if "Certified Buyer" in blob else (
                "Verified Purchase" if "Verified Purchase" in blob else None
            )
            if not badge or len(blob) > 600 or len(blob) < 30:
                continue
            body = re.split(badge, blob)[0]
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


def scrape_product(url: str, max_reviews: int = 5) -> ProductPage:
    site = detect_site(url)
    if site == "unknown":
        raise ScrapeError(
            "UNSUPPORTED_SITE", "Only Amazon and Flipkart product links are supported right now."
        )

    if is_short_link(url):
        url = resolve_short_link(url)
        site = detect_site(url)

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
        dump_path = _dump_debug_html(html, f"{site}_{product_id}")
        msg = (
            "Page loaded but no reviews could be parsed. Either the product has no "
            "reviews, or the site changed its markup and the selectors need updating."
        )
        if dump_path:
            msg += f" Raw page saved to {dump_path} for inspection."
        raise ScrapeError("NO_REVIEWS", msg)

    return ProductPage(
        site=site,
        product_id=product_id,
        product_name=product_name,
        reviews=reviews[:max_reviews],
        source_url=source,
    )
