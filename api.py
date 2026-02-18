from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import sqlite3
import re
import os
from datetime import datetime

# --- 1. APP & CORE CONFIG ---
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'checkfake-pro-ai-v3')

# FIX: Dynamic Database Path for Render
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'checkfake_master.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Email Config (SMTP) - Using Environment Variables for safety
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USER', 'your-email@gmail.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASS', 'your-app-password')

CORS(app, resources={r"/*": {"origins": "*"}})
db = SQLAlchemy(app)
mail = Mail(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- 2. DATABASE MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    cart_items = db.relationship('CartItem', backref='owner', lazy=True, cascade="all, delete-orphan")

class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100))
    price = db.Column(db.Integer)
    current_price = db.Column(db.Integer)
    img = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    alert_triggered = db.Column(db.Boolean, default=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- 3. SCRAPER ENGINE (Optimized for Render Free Tier) ---
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def smart_scrape(url):
    driver = get_driver()
    reviews = []
    try:
        driver.get(url)
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        paragraphs = soup.find_all(['p', 'div', 'span'])
        for p in paragraphs:
            text = p.get_text(strip=True)
            if 30 < len(text) < 500: reviews.append(text)
        return list(set(reviews))[:50]
    except Exception as e:
        print(f"Scrape Error: {e}")
        return []
    finally:
        driver.quit()

# --- 4. AUTH & NAVIGATION ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name, email, pwd = request.form.get('username'), request.form.get('email'), request.form.get('password')
        if User.query.filter_by(email=email).first():
            flash('Account already exists.')
            return redirect(url_for('login'))
        new_user = User(username=name, email=email, password=generate_password_hash(pwd, method='pbkdf2:sha256'))
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email, pwd = request.form.get('email'), request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, pwd):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('store'))
        flash('Check your email and password.')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# --- 5. THE STORE EXPERIENCE ---
MANUAL_DEALS = [
    {"name": "Meesho Designer Saree", "price": 499, "app": "Meesho", "link": "#", "score": 98, "img": "https://via.placeholder.com/300"},
    {"name": "Noise Smartwatch", "price": 1299, "app": "Flipkart", "link": "#", "score": 92, "img": "https://via.placeholder.com/300"},
    {"name": "Premium Shirt", "price": 2499, "app": "Myntra", "link": "#", "score": 95, "img": "https://via.placeholder.com/300"}
]

@app.route('/store')
def store():
    app_f = request.args.get('app', 'all').lower()
    price_f = request.args.get('price', 'all')
    filtered = MANUAL_DEALS
    if app_f != 'all': filtered = [p for p in filtered if p['app'].lower() == app_f]
    
    if price_f == 'under500': filtered = [p for p in filtered if p['price'] < 500]
    elif price_f == '500to1000': filtered = [p for p in filtered if 500 <= p['price'] <= 1000]
    elif price_f == '1000to2000': filtered = [p for p in filtered if 1000 < p['price'] <= 2000]
    elif price_f == 'above2000': filtered = [p for p in filtered if p['price'] > 2000]
    
    return render_template('store.html', products=filtered)

@app.route('/add-to-cart', methods=['POST'])
@login_required
def add_to_cart():
    data = request.json
    item = CartItem(product_name=data['name'], price=data['price'], current_price=data['price'], img=data['img'], user_id=current_user.id)
    db.session.add(item)
    db.session.commit()
    return jsonify({"message": "Successfully added to your Vault! 🛒"})

@app.route('/cart')
@login_required
def view_cart():
    items = CartItem.query.filter_by(user_id=current_user.id).all()
    total = sum(i.current_price for i in items)
    return render_template('cart.html', items=items, total=total)

# --- 6. OWNER UTILITIES ---
@app.route('/admin-dashboard')
@login_required
def admin_dashboard():
    if current_user.username != 'admin': return redirect(url_for('store'))
    stats = {
        "users": User.query.count(),
        "items": CartItem.query.count(),
        "popular": db.session.query(CartItem.product_name, func.count(CartItem.product_name)).group_by(CartItem.product_name).limit(5).all()
    }
    return render_template('admin_dashboard.html', stats=stats)

# --- 7. AI ENGINE ---
@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    url_match = re.search(r'(https?://\S+)', data.get('url', ''))
    if not url_match: return jsonify({"verdict": "Invalid URL"}), 200
    url = url_match.group(1).split('?')[0].rstrip('.,;:)')
    
    reviews = smart_scrape(url)
    fake = sum(1 for r in reviews if len(r) < 40 or "good" in r.lower())
    score = int(((len(reviews)-fake)/len(reviews))*100) if reviews else 0
    verdict = "Trusted ✅" if score >= 75 else "Risk 🚫"
    return jsonify({"total": len(reviews), "score": score, "verdict": verdict})

# --- 8. STARTUP ---
if __name__ == '__main__':
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            print(f"Database Init Error: {e}")
            
    # Render manages the PORT; fallback to 5000 for local testing
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
