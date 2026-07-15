import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
if not app.config["SECRET_KEY"]:
    raise RuntimeError("SECRET_KEY must be set in your host's environment variables.")

basedir = os.path.abspath(os.path.dirname(__file__))
# Prefer a real DB (e.g. Postgres via DATABASE_URL) so signups survive restarts/redeploys.
# SQLite falls back only for local dev.
_db_url = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(basedir, "checkfake_master.db"))
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
CORS(app, resources={r"/analyze": {"origins": os.environ.get("FRONTEND_ORIGIN", "*")}})

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# Reused HTTP session -> connection keep-alive instead of a fresh TCP/TLS handshake per request.
HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
})

SCAN_CACHE_MINUTES = int(os.environ.get("SCAN_CACHE_MINUTES", "360"))  # 6 hours default

# Optional rendering service for JS-heavy stores (Amazon, Myntra, Ajio, Meesho).
# Sign up at a service like scraperapi.com and set RENDER_API_KEY in your host's
# environment variables to enable this. Leave unset to skip it entirely.
RENDER_API_KEY = os.environ.get("RENDER_API_KEY")
RENDER_API_URL = "https://api.scraperapi.com"


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    cart_items = db.relationship("CartItem", backref="owner", lazy=True, cascade="all, delete-orphan")


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    price = db.Column(db.Integer, default=0)
    app = db.Column(db.String(50))
    link = db.Column(db.String(500))
    score = db.Column(db.Integer, default=85)
    img = db.Column(db.String(500))


class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100))
    price = db.Column(db.Integer)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class ScanResult(db.Model):
    """Cache of the last analysis for a given product URL, so repeat scans are instant."""
    id = db.Column(db.Integer, primary_key=True)
    url_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    url = db.Column(db.String(600), nullable=False)
    total = db.Column(db.Integer, nullable=False)
    real = db.Column(db.Integer, nullable=False)
    fake = db.Column(db.Integer, nullable=False)
    score = db.Column(db.Integer, nullable=False)
    verdict = db.Column(db.String(50), nullable=False)
    method = db.Column(db.String(20), nullable=False)  # "ai" or "heuristic"
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def auto_generate_deal(earnkaro_url):
    """Resolve an affiliate link and read its og:title / og:image straight from the HTML.
    No headless browser needed -- these meta tags are almost always present in the raw
    HTML response, which keeps this fast and removes a heavy Selenium/Chrome dependency."""
    try:
        response = HTTP.get(earnkaro_url, allow_redirects=True, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        name = soup.find("meta", property="og:title")
        image = soup.find("meta", property="og:image")
        title = name["content"] if name else "Premium Deal"
        return {
            "name": title.split("|")[0].split("-")[0].strip()[:60],
            "img": image["content"] if image else "https://via.placeholder.com/300",
            "app": next((store for store in ("amazon", "flipkart", "meesho", "myntra", "ajio") if store in response.url.lower()), "store"),
        }
    except Exception:
        return None


# Only fetch known shopping sites. This is both product validation and SSRF protection.
SUPPORTED_STORES = {"amazon.in", "amazon.com", "flipkart.com", "meesho.com", "myntra.com", "ajio.com"}
MAX_REVIEWS = int(os.environ.get("MAX_REVIEWS", "30"))
MAX_REVIEW_CHARS = 800


def validate_product_url(value):
    if not isinstance(value, str) or not value.strip():
        return None, "Enter a product URL."
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None, "Enter a complete URL beginning with https://."
    if parsed.username or parsed.password:
        return None, "URLs containing credentials are not allowed."

    host = parsed.hostname.lower().rstrip(".")
    if not any(host == store or host.endswith("." + store) for store in SUPPORTED_STORES):
        return None, "This store is not supported. Use a product URL from Amazon, Flipkart, Meesho, Myntra, or Ajio."
    try:
        for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(item[4][0]).is_global:
                return None, "The product URL must resolve to a public address."
    except socket.gaierror:
        return None, "The website address could not be resolved."
    return parsed.geturl(), None


def fetch_product_page(product_url):
    """Fetch an allowed page and validate every redirect before following it."""
    current_url = product_url
    for _ in range(6):
        current_url, error = validate_product_url(current_url)
        if error:
            return None, error
        try:
            response = HTTP.get(current_url, timeout=(4, 12), allow_redirects=False)
        except requests.RequestException:
            return None, "The product page could not be reached."
        if response.is_redirect:
            location = response.headers.get("Location")
            if not location:
                return None, "The store returned an invalid redirect."
            current_url = urljoin(current_url, location)
            continue
        if not 200 <= response.status_code < 300:
            return None, f"The store returned HTTP {response.status_code}."
        if "html" not in response.headers.get("Content-Type", "").lower():
            return None, "The URL does not point to a product web page."
        return response.text, None
    return None, "Too many redirects while opening the product page."


def fetch_rendered_page(product_url):
    """Fallback for JS-rendered stores (Amazon, Myntra, Ajio, Meesho): asks a
    rendering service to load the page in a real browser and return the HTML.
    Only called when the fast plain-HTTP fetch finds zero reviews. Returns
    None (silently) if RENDER_API_KEY isn't configured or the call fails."""
    if not RENDER_API_KEY:
        return None
    try:
        resp = HTTP.get(
            RENDER_API_URL,
            params={"api_key": RENDER_API_KEY, "url": product_url, "render": "true"},
            timeout=(5, 30),
        )
        resp.raise_for_status()
        return resp.text
    except requests.RequestException:
        return None


def _add_review_text(value, reviews):
    if isinstance(value, str):
        text = " ".join(value.split())
        if 20 <= len(text) <= 5000 and text not in reviews:
            reviews.append(text[:MAX_REVIEW_CHARS])


def extract_reviews(html):
    soup = BeautifulSoup(html, "html.parser")
    reviews = []
    # Product pages frequently expose reviews in schema.org JSON-LD.
    for script in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if str(item.get("@type", "")).lower() == "review":
                    _add_review_text(item.get("reviewBody") or item.get("description"), reviews)
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)

    selectors = "[itemprop='reviewBody'], [data-hook='review-body'], .review-text, .reviewText, .review-content"
    for node in soup.select(selectors):
        _add_review_text(node.get_text(" ", strip=True), reviews)
    return reviews[:MAX_REVIEWS]


def heuristic_score(reviews):
    """Fast, no-network scorer based on duplication and low-effort text patterns.
    Used as the primary safety net when the AI call is unavailable or fails,
    so the site's core feature never goes fully offline."""
    total = len(reviews)
    if total == 0:
        return {"score": 50, "real": 0, "fake": 0, "verdict": "Mixed signals", "method": "heuristic"}

    normalized = [re.sub(r"\s+", " ", r.strip().lower()) for r in reviews]
    counts = {}
    for text in normalized:
        counts[text] = counts.get(text, 0) + 1
    duplicate_reviews = sum(1 for text in normalized if counts[text] > 1)

    lengths = [len(r) for r in reviews]
    avg_len = sum(lengths) / total
    short_reviews = sum(1 for l in lengths if l < 25)

    fake_signal = 0.0
    fake_signal += (duplicate_reviews / total) * 0.5
    fake_signal += (short_reviews / total) * 0.3
    if avg_len < 40:
        fake_signal += 0.2
    fake_signal = min(fake_signal, 1.0)

    score = round((1 - fake_signal) * 100)
    fake = round(fake_signal * total)
    real = total - fake
    verdict = "Likely authentic" if score >= 80 else "Mixed signals" if score >= 50 else "Likely suspicious"
    return {"score": score, "real": real, "fake": fake, "verdict": verdict, "method": "heuristic"}


def score_reviews_with_ai(reviews):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("AI analysis is not configured.")
    try:
        from openai import OpenAI  # imported lazily: if the package isn't installed,
        # the whole app still boots fine and just falls back to heuristic_score().
    except ImportError:
        raise RuntimeError("openai package is not installed.")

    total = len(reviews)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "score": {"type": "integer", "minimum": 0, "maximum": 100},
            "real": {"type": "integer", "minimum": 0, "maximum": total},
            "fake": {"type": "integer", "minimum": 0, "maximum": total},
            "verdict": {"type": "string", "enum": ["Likely authentic", "Mixed signals", "Likely suspicious"]},
        },
        "required": ["score", "real", "fake", "verdict"],
    }
    client = OpenAI(api_key=api_key, timeout=20.0)
    response = client.responses.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        store=False,
        instructions=(
            "Assess only whether the supplied review writing appears authentic. "
            "Do not claim that the product or seller is genuine. Score is confidence from 0 to 100. "
            "Count every supplied review exactly once as real or fake. Treat uncertainty as suspicious."
        ),
        input=json.dumps({"reviews": reviews}),
        text={"format": {"type": "json_schema", "name": "review_authenticity", "strict": True, "schema": schema}},
    )
    result = json.loads(response.output_text)
    if result["real"] + result["fake"] != total:
        raise RuntimeError("AI returned inconsistent review counts.")
    result["method"] = "ai"
    return result


def get_cached_scan(product_url):
    url_hash = hashlib.sha256(product_url.encode()).hexdigest()
    row = ScanResult.query.filter_by(url_hash=url_hash).first()
    if not row:
        return None
    if datetime.now(timezone.utc) - row.created_at.replace(tzinfo=timezone.utc) > timedelta(minutes=SCAN_CACHE_MINUTES):
        return None
    return row


def save_scan(product_url, total, result):
    url_hash = hashlib.sha256(product_url.encode()).hexdigest()
    row = ScanResult.query.filter_by(url_hash=url_hash).first() or ScanResult(url_hash=url_hash, url=product_url[:600])
    row.total = total
    row.real = result["real"]
    row.fake = result["fake"]
    row.score = result["score"]
    row.verdict = result["verdict"]
    row.method = result["method"]
    row.created_at = datetime.now(timezone.utc)
    db.session.add(row)
    db.session.commit()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Send JSON with a 'url' field."}), 400
    product_url, error = validate_product_url(data.get("url"))
    if error:
        return jsonify({"error": error}), 400

    cached = get_cached_scan(product_url)
    if cached:
        return jsonify({
            "total": cached.total, "score": cached.score, "real": cached.real,
            "fake": cached.fake, "verdict": cached.verdict, "method": cached.method, "cached": True,
        })

    page, error = fetch_product_page(product_url)
    if error:
        return jsonify({"error": error, "message": "No score was generated."}), 422
    reviews = extract_reviews(page)

    # Plain HTTP found nothing -- likely a JS-rendered store (Amazon/Myntra/Ajio/Meesho).
    # Try a rendering service if one is configured, then re-extract.
    if not reviews:
        rendered_page = fetch_rendered_page(product_url)
        if rendered_page:
            reviews = extract_reviews(rendered_page)

    if not reviews:
        return jsonify({"error": "No public reviews could be retrieved from this product page.", "message": "No score was generated."}), 422

    # Always compute the heuristic score first -- it's instant and becomes the
    # answer if the AI call is unavailable or fails, so the feature never fully breaks.
    result = heuristic_score(reviews)
    try:
        result = score_reviews_with_ai(reviews)
    except Exception:
        app.logger.warning("AI analysis unavailable, using heuristic fallback", exc_info=True)

    save_scan(product_url, len(reviews), result)
    return jsonify({"total": len(reviews), "cached": False, **result})


@app.route("/store")
def store():
    app_filter = request.args.get("app", "all").lower()
    search_query = request.args.get("q", "").strip()
    query = Product.query
    if search_query:
        query = query.filter(Product.name.contains(search_query))
    if app_filter != "all":
        query = query.filter(~Product.app.in_(["amazon", "flipkart", "meesho", "myntra", "ajio"]) if app_filter == "others" else Product.app == app_filter)
    return render_template("store.html", products=query.order_by(Product.id.desc()).all(), search_query=search_query)


@app.route("/admin-add", methods=["POST"])
@login_required
def admin_add():
    if current_user.username != "admin":
        return redirect("/")
    details = auto_generate_deal(request.form.get("url", ""))
    if details:
        db.session.add(Product(name=details["name"], price=int(request.form.get("price") or 0), app=details["app"], link=request.form.get("url"), img=details["img"]))
        db.session.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form.get("email")).first()
        if user and check_password_hash(user.password, request.form.get("password", "")):
            login_user(user)
            return redirect(url_for("store"))
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        user = User(username=request.form.get("username", ""), email=request.form.get("email", ""), password=generate_password_hash(request.form.get("password", ""), method="pbkdf2:sha256"))
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return render_template("signup.html", error="That email is already registered."), 409
        return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/admin-dashboard")
@login_required
def admin_dashboard():
    if current_user.username != "admin":
        return redirect("/")
    return render_template("admin_dashboard.html", stats={"users": User.query.count(), "items": Product.query.count(), "recent": User.query.order_by(User.id.desc()).limit(5).all()})


@app.route("/add-to-cart", methods=["POST"])
@login_required
def add_to_cart():
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("name"), str) or not isinstance(data.get("price"), (int, float)):
        return jsonify({"error": "Invalid item."}), 400
    db.session.add(CartItem(product_name=data["name"][:100], price=int(data["price"]), user_id=current_user.id))
    db.session.commit()
    return jsonify({"message": "Successfully added to your Vault!"})


@app.route("/cart")
@login_required
def view_cart():
    return render_template("cart.html", items=CartItem.query.filter_by(user_id=current_user.id).all())


@app.route("/about")
def about(): return render_template("about.html")
@app.route("/contact")
def contact(): return render_template("contact.html")
@app.route("/privacy")
def privacy(): return render_template("privacy.html")
@app.route("/terms")
def terms(): return render_template("terms.html")
@app.route("/logout")
def logout():
    logout_user()
    return redirect("/")


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
