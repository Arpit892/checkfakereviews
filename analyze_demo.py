import re
from demo_data import get_demo_result, list_demo_products


def extract_asin(url: str):
    """Extracts a product identifier from either an Amazon or Flipkart URL."""
    match = (
        re.search(r"/dp/([A-Z0-9]{10})", url)
        or re.search(r"/product/([A-Z0-9]{10})", url)
        or re.search(r"/p/(itm[a-zA-Z0-9]+)", url)
    )
    return match.group(1) if match else None


def analyze_product_demo(product_url: str) -> dict:
    """
    Returns a precomputed result for known demo products, or a friendly
    message pointing to available demo products otherwise.
    """
    asin = extract_asin(product_url)

    if not asin:
        return {"error": "That doesn't look like a valid Amazon.in product URL (expected a link containing /dp/)."}

    result = get_demo_result(asin)

    if result is None:
        available = list_demo_products()
        product_list = "; ".join(f"{name}" for _, name in available)
        return {
            "error": (
                "Live analysis for arbitrary products isn't available on the "
                "hosted demo yet (coming soon as the project scales). "
                f"Try one of these demo products instead: {product_list}"
            )
        }

    output = dict(result)
    output["product_url"] = product_url
    return output
