from product_insights import analyze, sentence_tone

def test_not_good_is_negative():
    assert sentence_tone("The camera is not good at night") == -1

def test_pros_and_cons_from_reviews():
    out = analyze([
        "Battery life is excellent, lasts two days. The phone heating is a big problem while gaming.",
        "Great battery backup. Gets hot quickly, heating issue.",
        "Camera is amazing in daylight.",
    ], ["6.1 inch Super Retina display", "48MP camera"])
    pros = {p["topic"]: p["mentions"] for p in out["pros"]}
    cons = {c["topic"]: c["mentions"] for c in out["cons"]}
    assert pros["Battery"] == 2 and "Camera" in pros
    assert cons["Heating"] == 2
    assert out["claims"] == ["6.1 inch Super Retina display", "48MP camera"]

def test_nothing_invented_from_empty_reviews():
    out = analyze([], [])
    assert out == {"pros": [], "cons": [], "claims": []}
