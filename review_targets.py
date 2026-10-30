"""Validated store URLs shared by the HTTP and PC-browser scrapers."""
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

AMAZON_DOMAINS = (
    "amazon.in", "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.de", "amazon.fr",
    "amazon.it", "amazon.es", "amazon.co.jp", "amazon.com.au",
)
SHORT_HOSTS = ("amzn.in", "amzn.to", "a.co", "fkrt.it", "fkrt.cc", "flipkart.it", "dl.flipkart.com")


def site_for_url(url: str) -> str:
    try:
        u = urlsplit((url or "").strip())
        host = (u.hostname or "").lower()
        if u.scheme not in ("http", "https") or u.username or u.password or u.port not in (None, 80, 443):
            return "unknown"
    except ValueError:
        return "unknown"
    if host in ("amzn.in", "amzn.to", "a.co") or any(host == d or host.endswith("." + d) for d in AMAZON_DOMAINS):
        return "amazon"
    if host in ("fkrt.it", "fkrt.cc", "flipkart.it", "flipkart.com") or host.endswith(".flipkart.com"):
        return "flipkart"
    return "unknown"


def is_short_url(url: str) -> bool:
    u = urlsplit(url)
    return (u.hostname or "").lower() in SHORT_HOSTS or bool(re.fullmatch(r"/s/[A-Za-z0-9]+/?", u.path))


def reviews_url(url: str):
    """Return (reviews_page_url, product_id). Raises ValueError if no ID is found."""
    u = urlsplit(url)
    site = site_for_url(url)
    if site == "amazon":
        m = re.search(r"/(?:dp|gp/product|gp/aw/d|product-reviews)/([A-Z0-9]{10})(?:/|$)", u.path, re.I)
        asin = m.group(1).upper() if m else (parse_qs(u.query).get("asin") or [""])[0].upper()
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            raise ValueError("Could not find the Amazon product ID. Paste the full product link.")
        return urlunsplit(("https", u.hostname, f"/product-reviews/{asin}/", "sortBy=recent&pageNumber=1", "")), asin
    if site == "flipkart":
        parts = [p for p in u.path.split("/") if p]
        q = parse_qs(u.query)
        for marker in ("p", "product-reviews", "product"):
            if marker in parts:
                i = parts.index(marker)
                if i + 1 < len(parts) and re.fullmatch(r"[A-Za-z0-9]+", parts[i + 1]):
                    pid = parts[i + 1]
                    slug = "/".join(parts[:i])
                    path = (f"/{slug}" if slug else "") + f"/product-reviews/{pid}"
                    query = urlencode({"pid": q["pid"][0]}) if q.get("pid") else ""
                    return urlunsplit(("https", "www.flipkart.com", path, query, "")), pid
        pid = (q.get("pid") or [""])[0]
        if re.fullmatch(r"[A-Za-z0-9]+", pid):
            return f"https://www.flipkart.com/product-reviews/{pid}?pid={pid}", pid
        raise ValueError("Could not find the Flipkart product ID. Paste the full product link.")
    raise ValueError("Only Amazon and Flipkart links are supported.")
