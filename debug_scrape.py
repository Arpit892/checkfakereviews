"""
debug_scrape.py
Test one URL directly from your machine, no Render/worker involved. Fastest
way to check whether a NO_REVIEWS or BLOCKED error is fixable, and always
saves the raw HTML so selectors can be adjusted against real markup.

Usage:
    python debug_scrape.py "https://www.amazon.in/dp/B0XXXXXXXXX"
"""

import sys

from scraper import ScrapeError, scrape_product


def main():
    if len(sys.argv) < 2:
        print('Usage: python debug_scrape.py "<product url>"')
        raise SystemExit(1)

    url = sys.argv[1]
    print(f"Fetching: {url}\n")
    try:
        page = scrape_product(url, max_reviews=5)
        print(f"OK — {page.product_name}")
        print(f"{len(page.reviews)} reviews parsed:\n")
        for r in page.reviews:
            print(f"  [{r.rating}] {r.reviewer_name}: {r.text[:90]}")
    except ScrapeError as e:
        print(f"FAILED — {e.reason}")
        print(e.message)
        print("\nIf a debug_dumps/*.html file was saved, share it — that's the exact "
              "page the scraper saw, and it's what's needed to fix the selectors.")


if __name__ == "__main__":
    main()
