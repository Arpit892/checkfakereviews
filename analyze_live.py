"""
analyze_live.py
Scores scraped reviews and produces the SAME output schema as demo_data.py,
so the existing frontend keeps working unchanged.

Why this doesn't just import your local GPT-2 pipeline:
    GPT-2 small needs ~600MB-1.2GB resident once torch is loaded. Render's
    free tier gives you 512MB. Loading it there will OOM-kill the worker
    mid-request, which looks to the user like a random 502.

So there are two scoring backends:
    * "heuristic" (default) - pure-Python signals, ~1ms per review, no model.
    * "gpt2" (opt-in via USE_GPT2=1) - real perplexity, for when you run this
      locally or on a paid instance with >=2GB RAM. Falls back to heuristic
      automatically if torch/transformers aren't importable.

Score conventions (all 0..1, higher = more trustworthy):
    ai_human_likeness_score  1.0 = reads like a real person, 0.0 = machine-written
    spam_genuineness_score   1.0 = genuine buyer, 0.0 = spam / incentivised
    uniqueness_score         1.0 = original, 0.0 = duplicated from other reviews
    review_score             weighted blend; heavily penalised on AI leftovers
    trust_score              mean(review_score) * 100, rounded
"""

import math
import os
import re
from typing import List

USE_GPT2 = os.environ.get("USE_GPT2", "0") == "1"

_gpt2 = {"loaded": False, "model": None, "tok": None, "ok": False}


# --------------------------------------------------------------------------
# AI leftover detection: text an LLM emitted that the fake reviewer forgot
# to strip out. These are the highest-confidence signal there is.
# --------------------------------------------------------------------------

AI_LEFTOVER_PATTERNS = [
    r"\bhere(?:'s| is) (?:a|an|your|the)\b.{0,40}\breview\b",
    r"\breview you can use\b",
    r"\bas an ai (?:language )?model\b",
    r"\bi(?:'m| am) an ai\b",
    r"\b(?:sure|certainly|of course)[,!]? (?:here|i(?:'ll| can))\b",
    r"\bcertainly!\s",
    r"\bfeel free to (?:modify|adjust|customi[sz]e|tweak)\b",
    r"\blet me know if you(?:'d| would) like\b",
    r"\bi hope this helps\b",
    r"\b(?:option|version|draft)\s*[123]\s*:",
    r"\[(?:insert|your|product name|brand)\b",
    r"\bword count\b",
    r"^\s*>\s",                       # markdown quote marker left in
    r"\*\*[^*]{2,40}\*\*",            # markdown bold left in
    r"\bwrite a (?:positive|natural|genuine) review\b",
]
AI_LEFTOVER_RE = [re.compile(p, re.I | re.M) for p in AI_LEFTOVER_PATTERNS]

# Phrasing that is *characteristic* of LLM prose but not proof on its own.
LLM_STYLE_MARKERS = [
    r"\boverall,",
    r"\bin conclusion\b",
    r"\bfurthermore\b",
    r"\bmoreover\b",
    r"\badditionally,",
    r"\bhighly recommend(?:ed)? (?:this|to anyone)\b",
    r"\bgame[- ]changer\b",
    r"\bexceeded (?:my|all) expectations\b",
    r"\bwhat (?:i|I) (?:love|like) (?:most )?about\b",
    r"\bpros\s*:",
    r"\bcons\s*:",
    r"\bvalue for money\b.{0,30}\bquality\b",
    r"\bi(?:'ve| have) been using (?:this|it) for\b",
    r"\bnot only\b.{0,40}\bbut also\b",
]
LLM_STYLE_RE = [re.compile(p, re.I) for p in LLM_STYLE_MARKERS]

# Human noise: typos, texting style, Hinglish, emotional asymmetry.
HUMAN_MARKERS = [
    r"\.{3,}",                        # ellipsis abuse
    r"[!?]{2,}",
    r"\b(?:bhai|yaar|paisa|paise|accha|acha|bahut|kharab|sahi|thoda|mast)\b",
    r"\b(?:pls|plz|thx|nd|bt|gud|vry|ok ok|okk+)\b",
    r"\b(?:i|its|dont|cant|wont|didnt|doesnt)\b(?![’'])",   # missing apostrophes
    r"\b(?:worst|waste|refund|return|fake|defective|broke|damaged)\b",
    r"\b(?:delivery|deliverd|packing|packaging) (?:was|is) \w+",
    r"\brs\.?\s?\d+",
    r"\b\d+\s?(?:days?|months?)\b",
]
HUMAN_RE = [re.compile(p, re.I) for p in HUMAN_MARKERS]

GENERIC_NAMES = {
    "amazon customer", "flipkart customer", "customer", "kindle customer",
    "anonymous", "user", "buyer", "a", "aa", "abc",
}

GENERIC_PHRASES = {
    "good", "good product", "nice", "nice product", "very good", "very nice",
    "excellent", "awesome", "best", "best product", "super", "ok", "okay",
    "value for money", "worth it", "must buy", "5 star", "fine", "good one",
    "nice one", "very good product", "excellent product", "good quality",
}

# Content words that carry no product-specific information.
GENERIC_VOCAB = {
    "good", "nice", "best", "super", "excellent", "awesome", "great", "fine",
    "product", "quality", "value", "money", "worth", "buy", "must", "star",
    "stars", "amazing", "perfect", "satisfied", "happy", "love", "loved",
    "item", "thanks", "recommended", "recommend", "ok", "okay", "cool",
}

PROMO_RE = re.compile(
    r"(https?://|www\.|whatsapp|telegram|contact me|dm me|cashback|"
    r"free product|paid review|in exchange for)",
    re.I,
)

STOPWORDS = {
    "the", "a", "an", "is", "it", "its", "and", "or", "but", "to", "of", "in",
    "for", "on", "with", "this", "that", "i", "my", "me", "was", "were", "are",
    "am", "be", "been", "very", "so", "too", "at", "as", "from", "by", "not",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z']+", text.lower())


def _content_tokens(text: str) -> set:
    return {t for t in _tokens(text) if t not in STOPWORDS and len(t) > 2}


def _sentences(text: str) -> List[str]:
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    return parts or [text.strip()]


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _burstiness(text: str) -> float:
    """
    Coefficient of variation of sentence lengths. Humans vary wildly
    (one-word sentence, then a rambling one). LLMs produce even lengths.
    Returns 0..1 where higher = more human-like variation.
    """
    lens = [len(_tokens(s)) for s in _sentences(text)]
    lens = [l for l in lens if l > 0]
    if len(lens) < 2:
        return 0.5
    mean = sum(lens) / len(lens)
    if mean == 0:
        return 0.5
    var = sum((l - mean) ** 2 for l in lens) / len(lens)
    cv = math.sqrt(var) / mean
    return _clamp(cv / 0.8)


def _type_token_ratio(text: str) -> float:
    toks = _tokens(text)
    if len(toks) < 8:
        return 0.5
    return len(set(toks)) / len(toks)


# --------------------------------------------------------------------------
# Optional GPT-2 perplexity backend
# --------------------------------------------------------------------------

def _load_gpt2():
    if _gpt2["loaded"]:
        return _gpt2["ok"]
    _gpt2["loaded"] = True
    try:
        import torch  # noqa: F401
        from transformers import GPT2LMHeadModel, GPT2TokenizerFast

        _gpt2["tok"] = GPT2TokenizerFast.from_pretrained("gpt2")
        _gpt2["model"] = GPT2LMHeadModel.from_pretrained("gpt2").eval()
        _gpt2["ok"] = True
    except Exception:
        _gpt2["ok"] = False
    return _gpt2["ok"]


def gpt2_perplexity(text: str):
    """Returns perplexity float, or None if the model isn't available."""
    if not _load_gpt2():
        return None
    import torch

    tok, model = _gpt2["tok"], _gpt2["model"]
    ids = tok(text, return_tensors="pt", truncation=True, max_length=256).input_ids
    if ids.shape[1] < 2:
        return None
    with torch.no_grad():
        loss = model(ids, labels=ids).loss
    return float(torch.exp(loss))


# --------------------------------------------------------------------------
# Individual signals
# --------------------------------------------------------------------------

def detect_ai_leftover(text: str) -> bool:
    return any(r.search(text) for r in AI_LEFTOVER_RE)


def score_human_likeness(text: str) -> float:
    """
    0.85 is the ceiling for short casual reviews — a three-word review carries
    too little signal to certify as human, so it sits at 'probably fine'.
    Long, polished, evenly-structured, connective-heavy text drops fast.
    """
    toks = _tokens(text)
    n = len(toks)

    if n < 4:
        return 0.70

    score = 0.85

    style_hits = sum(1 for r in LLM_STYLE_RE if r.search(text))
    human_hits = sum(1 for r in HUMAN_RE if r.search(text))

    score -= 0.085 * style_hits
    score += 0.045 * min(human_hits, 4)

    # Long text with low burstiness = machine cadence.
    if n >= 35:
        b = _burstiness(text)
        score -= (1.0 - b) * 0.28
        ttr = _type_token_ratio(text)
        if ttr > 0.82:          # unnaturally low word repetition
            score -= 0.10

    # Perfectly clean long text: no typos, no lowercase 'i', full punctuation.
    if n >= 30 and human_hits == 0:
        if re.search(r"[.,] [A-Z]", text) and text[0:1].isupper() and text.rstrip().endswith((".", "!")):
            score -= 0.14

    # Structured pros/cons blocks are near-universally generated.
    if re.search(r"\bpros\b.{0,80}\bcons\b", text, re.I | re.S):
        score -= 0.18

    if detect_ai_leftover(text):
        score -= 0.55

    if USE_GPT2:
        ppl = gpt2_perplexity(text)
        if ppl is not None:
            # Very low perplexity = highly predictable = model-generated.
            # ~20 is flat/machine-like, ~90+ is messy human text.
            ppl_component = _clamp((ppl - 18.0) / 70.0)
            score = 0.5 * score + 0.5 * (0.25 + 0.65 * ppl_component)

    return round(_clamp(score, 0.02, 0.95), 3)


def score_spam_genuineness(review, all_texts: List[str]) -> float:
    text = review.text
    toks = _tokens(text)
    n = len(toks)
    score = 0.93

    name = (review.reviewer_name or "").strip().lower()
    if name in GENERIC_NAMES:
        score -= 0.06
    if len(name.replace(" ", "")) <= 2:
        score -= 0.05

    if review.verified:
        score += 0.05
    else:
        score -= 0.07

    normalized = re.sub(r"[^a-z ]", "", text.lower()).strip()
    content = _content_tokens(text)
    if normalized in GENERIC_PHRASES:
        score -= 0.22
    elif n <= 14 and content and content <= GENERIC_VOCAB:
        # Whole review is generic praise words ("best product best product
        # very good quality") — padded filler, common in incentivised batches.
        score -= 0.18
    elif n <= 3:
        score -= 0.14
    elif n <= 6:
        score -= 0.05

    if PROMO_RE.search(text):
        score -= 0.45

    if detect_ai_leftover(text):
        score -= 0.80

    letters = [c for c in text if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7 and n > 3:
        score -= 0.12

    if re.search(r"(.)\1{5,}", text):          # keyboard mashing
        score -= 0.20

    # Near-duplicate of another review in the same batch = review farm.
    mine = _content_tokens(text)
    if mine:
        for other in all_texts:
            if other == text:
                continue
            theirs = _content_tokens(other)
            if not theirs:
                continue
            jac = len(mine & theirs) / len(mine | theirs)
            if jac > 0.6:
                score -= 0.30
                break

    if review.rating is not None and review.rating >= 5 and n <= 3:
        score -= 0.05

    return round(_clamp(score, 0.02, 0.99), 3)


def score_uniqueness(text: str, all_texts: List[str]) -> float:
    mine = _content_tokens(text)
    if not mine:
        return 0.9
    worst = 0.0
    for other in all_texts:
        if other == text:
            continue
        theirs = _content_tokens(other)
        if not theirs:
            continue
        worst = max(worst, len(mine & theirs) / len(mine | theirs))
    return round(_clamp(0.9 - worst * 0.85, 0.05, 0.9), 3)


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

W_AI, W_SPAM, W_UNIQ = 0.40, 0.40, 0.20
LEFTOVER_PENALTY = 0.43   # multiplier applied when AI leftovers are found


def label_for(trust_score: int) -> str:
    if trust_score >= 80:
        return "Likely Genuine"
    if trust_score >= 60:
        return "Mixed — Some Suspicious Reviews"
    if trust_score >= 40:
        return "Suspicious — Several Likely Fake Reviews"
    return "Likely Manipulated"


def analyze_reviews(product_name: str, reviews) -> dict:
    all_texts = [r.text for r in reviews]
    per_review = []

    for r in reviews:
        leftover = detect_ai_leftover(r.text)
        ai = score_human_likeness(r.text)
        spam = score_spam_genuineness(r, all_texts)
        uniq = score_uniqueness(r.text, all_texts)

        blended = W_AI * ai + W_SPAM * spam + W_UNIQ * uniq
        if leftover:
            blended *= LEFTOVER_PENALTY

        per_review.append(
            {
                "reviewer_name": r.reviewer_name,
                "review_text_preview": r.text[:80],
                "ai_human_likeness_score": ai,
                "spam_genuineness_score": spam,
                "uniqueness_score": uniq,
                "ai_leftover_detected": leftover,
                "review_score": round(_clamp(blended), 3),
                "verified_purchase": r.verified,
                "rating": r.rating,
            }
        )

    if per_review:
        trust = round(sum(p["review_score"] for p in per_review) / len(per_review) * 100)
    else:
        trust = 0

    return {
        "product_name": product_name,
        "trust_score": int(trust),
        "label": label_for(int(trust)),
        "reviews_analyzed": len(per_review),
        "per_review": per_review,
        "engine": "gpt2" if (USE_GPT2 and _gpt2.get("ok")) else "heuristic",
    }
