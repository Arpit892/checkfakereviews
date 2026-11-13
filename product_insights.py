"""product_insights.py — good and bad points about a product.

Reads the review texts and the seller's description bullets and returns
plain-language pros, cons and "what the seller claims". Rule-based (no paid AI),
so it runs free on your PC. Every point comes from real review sentences;
nothing is invented.
"""
import re
from typing import Dict, List

# topic -> words that mean a sentence is about it
ASPECTS = {
    "Battery": ["battery", "charging", "charge", "backup", "mah"],
    "Camera": ["camera", "photo", "picture", "video", "selfie", "lens"],
    "Display": ["display", "screen", "brightness", "resolution"],
    "Performance": ["performance", "speed", "fast", "slow", "lag", "smooth", "hang", "processor"],
    "Sound": ["sound", "audio", "speaker", "bass", "volume", "mic"],
    "Build quality": ["build", "quality", "material", "plastic", "sturdy", "durable", "broke", "broken", "cheap feel"],
    "Heating": ["heat", "heating", "hot", "warm"],
    "Comfort & fit": ["comfort", "comfortable", "fit", "fits", "size", "weight", "light", "heavy"],
    "Value for money": ["price", "value", "worth", "money", "expensive", "costly", "cheap", "budget"],
    "Delivery & packaging": ["delivery", "delivered", "packaging", "package", "box", "courier", "late"],
    "Seller & service": ["seller", "service", "warranty", "return", "replacement", "refund", "customer care"],
    "Design & looks": ["design", "look", "looks", "colour", "color", "style", "premium"],
}
POSITIVE = set("""good great excellent awesome amazing best nice love loved perfect superb fantastic
smooth fast sturdy solid premium worth happy satisfied impressive beautiful bright clear
comfortable recommended recommend reliable decent value lightweight crisp""".split())
NEGATIVE = set("""bad poor worst terrible awful hate useless broken broke slow lag lags laggy heating
hot issue issues problem problems defective damaged fake disappointed disappointing waste
cheap expensive costly drain drains noisy late refund return stopped faulty weak dull hang hangs""".split())
NEGATORS = {"not", "no", "never", "dont", "don't", "isn't", "isnt", "wasn't", "doesn't", "didn't", "hardly"}


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|,\s+(?:but|however)\s+", text or "")
    return [p.strip(" .,-") for p in parts if 12 <= len(p.strip()) <= 220]


def sentence_tone(sentence: str) -> int:
    """+1 positive, -1 negative, 0 unclear. 'not good' counts as negative."""
    words = re.findall(r"[a-z']+", sentence.lower())
    score = 0
    for i, w in enumerate(words):
        flip = any(x in NEGATORS for x in words[max(0, i - 2):i])
        if w in POSITIVE:
            score += -1 if flip else 1
        elif w in NEGATIVE:
            score += 1 if flip else -1
    return (score > 0) - (score < 0)


def _aspect(sentence: str) -> str:
    low = sentence.lower()
    for name, words in ASPECTS.items():
        if any(re.search(r"\b" + re.escape(w) + r"\b", low) for w in words):
            return name
    return ""


def analyze(review_texts: List[str], description: List[str] = None, limit: int = 4) -> Dict:
    """Return {"pros": [...], "cons": [...], "claims": [...]}.

    Each pro/con is {"topic", "mentions", "quote"}. A topic is a pro when more
    review sentences about it are positive than negative, and a con otherwise.
    """
    buckets: Dict[str, Dict] = {}
    for text in review_texts:
        seen_here = set()
        for s in _sentences(text):
            topic, tone = _aspect(s), sentence_tone(s)
            if not topic or not tone or (topic, tone) in seen_here:
                continue
            seen_here.add((topic, tone))
            b = buckets.setdefault(topic, {"pos": 0, "neg": 0, "pos_q": "", "neg_q": ""})
            key = "pos" if tone > 0 else "neg"
            b[key] += 1
            if not b[key + "_q"]:
                b[key + "_q"] = s
    pros, cons = [], []
    for topic, b in buckets.items():
        if b["pos"] > b["neg"]:
            pros.append({"topic": topic, "mentions": b["pos"], "quote": b["pos_q"]})
        elif b["neg"] > 0:
            cons.append({"topic": topic, "mentions": b["neg"], "quote": b["neg_q"]})
    pros.sort(key=lambda x: -x["mentions"])
    cons.sort(key=lambda x: -x["mentions"])
    claims = [re.sub(r"\s+", " ", d).strip()[:160] for d in (description or []) if d and len(d.strip()) > 8][:5]
    return {"pros": pros[:limit], "cons": cons[:limit], "claims": claims}
