from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
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

# --- APP CONFIG ---
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = 'loot-vault-secret-2026' 
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CORS(app, resources={r"/*": {"origins": "*"}})

# --- DATABASE SETUP (Auth & Cart) ---
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    cart_items = db.relationship('CartItem', backref='owner', lazy=True)

class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100))
    price = db.Column(db.Integer)
    img = db.Column(db.String(255))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- SCRAPER CACHE DATABASE ---
def init_scraper_db():
    conn = sqlite3.connect('reviews_v3.db') 
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS cache 
                 (url TEXT PRIMARY KEY, real_count INTEGER, fake_count INTEGER, 
                 trust_score INTEGER, verdict TEXT, timestamp DATETIME)''')
    conn.commit()
    conn.close()

init_scraper_db()

# --- 🛍️ MANUAL DEALS (Updated for Myntra/Ajio) ---
MANUAL_DEALS = [
    {"name": "Meesho Designer Saree", "price": 499, "app": "Meesho", "link": "https://ekaro.in/1", "score": 98, "img": "https://via.placeholder.com/300"},
    {"name": "Noise Smartwatch", "price": 1299, "app": "Flipkart", "link": "https://ekaro.in/2", "score": 92, "img": "https://via.placeholder.com/300"},
    {"name": "RGB Gaming Mouse", "price": 449, "app": "Amazon", "link": "https://amzn.to/3", "score": 85, "img": "https://via.placeholder.com/300"},
    {"name": "Premium Linen Shirt", "price": 2499, "app": "Myntra", "link": "#", "score": 95, "img": "https://via.placeholder.com/300"},
    {"name": "Denim Jacket", "price": 1800, "app": "Ajio", "link": "#", "score": 89, "img": "https://via.placeholder.com/300"}
]

# --- SCRAPER CONFIG ---
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0")
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
    except: return []
    finally: driver.quit()

# --- AUTH ROUTES ---
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name, email, pwd = request.form.get('username'), request.form.get('email'), request.form.get('password')
        if User.query.filter_by(email=email).first():
            flash('Email already registered!')
            return redirect(url_for('signup'))
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
            return redirect(url_for('store'))
        flash('Invalid Credentials')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('store'))

# --- STORE & CART ROUTES ---
@app.route('/')
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
    item = CartItem(product_name=data['name'], price=data['price'], img=data['img'], user_id=current_user.id)
    db.session.add(item)
    db.session.commit()
    return jsonify({"message": "Successfully added to Loot Vault! 🛒"})

@app.route('/cart')
@login_required
def view_cart():
    items = CartItem.query.filter_by(user_id=current_user.id).all()
    total = sum(i.price for i in items)
    return render_template('cart.html', items=items, total=total)

# --- ANALYSIS LOGIC ---
@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        data = request.json
        raw_input = data.get('url', '')
        url_match = re.search(r'(https?://\S+)', raw_input)
        if not url_match: return jsonify({"verdict": "Invalid Link 🚫"}), 200
        
        url = url_match.group(1).split('?')[0].rstrip('.,;:)')
        conn = sqlite3.connect('reviews_v3.db')
        c = conn.cursor()
        c.execute("SELECT * FROM cache WHERE url=?", (url,))
        cached = c.fetchone()
        if cached:
            conn.close()
            return jsonify({"total": cached[1]+cached[2], "real": cached[1], "fake": cached[2], "score": cached[3], "verdict": cached[4]})

        reviews = smart_scrape(url)
        fake_count = sum(1 for r in reviews if len(r) < 40 or "good" in r.lower())
        real_count = len(reviews) - fake_count
        score = int((real_count / len(reviews)) * 100) if reviews else 0
        
        verdict = "Highly Trusted ✅" if score >= 80 else "Safe ⚠️" if score >= 60 else "High Risk 🚫"
        if not reviews: verdict = "Insufficient Data"

        c.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?, ?, ?)", (url, real_count, fake_count, score, verdict, datetime.now()))
        conn.commit()
        conn.close()
        return jsonify({"total": len(reviews), "real": real_count, "fake": fake_count, "score": score, "verdict": verdict})
    except Exception as e: return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000)
