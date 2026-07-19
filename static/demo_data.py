"""
demo_data.py
Precomputed Trust Score results from real Amazon.in products already
analyzed locally (with live scraping + GPT-2 + full spam detection).

Used by the hosted demo (checkfakereviews.online) instead of running
Playwright + GPT-2 on the server, since Render's free tier (512MB RAM)
can't reliably run that stack, and cloud-server IPs get blocked by
Amazon's anti-bot systems even more aggressively than a home connection.

The full live pipeline (scraper.py + ai_detector.py + spam_detector.py)
still exists and runs locally — this file just lets the public website
demonstrate real, honestly-labeled output without needing live compute.
"""

DEMO_PRODUCTS = {
    "B0GYCT961V": {
        "product_name": "Reducing Brightening Glowing Sandalwood Saffron Face Wash",
        "trust_score": 83,
        "label": "Likely Genuine",
        "reviews_analyzed": 5,
        "per_review": [
            {
                "reviewer_name": "Babu bhai",
                "review_text_preview": "Its Good product",
                "ai_human_likeness_score": 0.85,
                "spam_genuineness_score": 0.908,
                "uniqueness_score": 0.9,
                "ai_leftover_detected": False,
                "review_score": 0.882,
            },
            {
                "reviewer_name": "sudip medya",
                "review_text_preview": "Excellent Face Wash and perfume of Face Wash just awesome,it's work at the first",
                "ai_human_likeness_score": 0.85,
                "spam_genuineness_score": 0.907,
                "uniqueness_score": 0.9,
                "ai_leftover_detected": False,
                "review_score": 0.881,
            },
            {
                "reviewer_name": "Swati",
                "review_text_preview": "Good one. Not completely tan removal but it brighten the skin lightly. Nice frag",
                "ai_human_likeness_score": 0.753,
                "spam_genuineness_score": 0.966,
                "uniqueness_score": 0.9,
                "ai_leftover_detected": False,
                "review_score": 0.87,
            },
            {
                "reviewer_name": "Rukmani karmakar",
                "review_text_preview": "The packaging of the product is very good. It came very strongly secured. The fa",
                "ai_human_likeness_score": 0.398,
                "spam_genuineness_score": 0.972,
                "uniqueness_score": 0.9,
                "ai_leftover_detected": False,
                "review_score": 0.714,
            },
            {
                "reviewer_name": "Smitha s m",
                "review_text_preview": "Yes good product. Works for blackheads",
                "ai_human_likeness_score": 0.7,
                "spam_genuineness_score": 0.908,
                "uniqueness_score": 0.9,
                "ai_leftover_detected": False,
                "review_score": 0.814,
            },
        ],
    },
    "B09S6M7JQJ": {
        "product_name": "Sandalwood Saffron Handmade Whitening Soap",
        "trust_score": 73,
        "label": "Mixed — Some Suspicious Reviews",
        "reviews_analyzed": 5,
        "per_review": [
            {
                "reviewer_name": "Varsha N",
                "review_text_preview": "Here's a natural and positive review you can use: > 5/5 I've been using th",
                "ai_human_likeness_score": 0.208,
                "spam_genuineness_score": 0.05,
                "uniqueness_score": 0.9,
                "ai_leftover_detected": True,
                "review_score": 0.121,
            },
            {
                "reviewer_name": "Saurav kumar",
                "review_text_preview": "Very good soap in terms of smell and lather but not a magical soap....the scrub",
                "ai_human_likeness_score": 0.645,
                "spam_genuineness_score": 0.985,
                "uniqueness_score": 0.9,
                "ai_leftover_detected": False,
                "review_score": 0.832,
            },
            {
                "reviewer_name": "Hoora",
                "review_text_preview": "Nice soap it removes my neck tanning",
                "ai_human_likeness_score": 0.85,
                "spam_genuineness_score": 0.935,
                "uniqueness_score": 0.9,
                "ai_leftover_detected": False,
                "review_score": 0.897,
            },
            {
                "reviewer_name": "Arth Pandya",
                "review_text_preview": "I buy these product because of many ads that I watch Soap smell is great but soa",
                "ai_human_likeness_score": 0.85,
                "spam_genuineness_score": 0.978,
                "uniqueness_score": 0.9,
                "ai_leftover_detected": False,
                "review_score": 0.92,
            },
            {
                "reviewer_name": "Satyam pathak",
                "review_text_preview": "Nice effect on skin",
                "ai_human_likeness_score": 0.85,
                "spam_genuineness_score": 0.935,
                "uniqueness_score": 0.9,
                "ai_leftover_detected": False,
                "review_score": 0.897,
            },
        ],
    },
}


def get_demo_result(asin: str):
    """Returns precomputed result dict for a known demo ASIN, or None if not found."""
    return DEMO_PRODUCTS.get(asin)


def list_demo_products():
    """Returns a list of (asin, product_name) for products available in demo mode."""
    return [(asin, data["product_name"]) for asin, data in DEMO_PRODUCTS.items()]
