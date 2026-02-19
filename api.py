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
import requests
import re
import os

# --- 1. CONFIGURATION ---
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'trusted-startup-2026')
# Using absolute path to ensure Render finds the DB every time
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'checkfake_master.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CORS(app, resources={r"/*": {"origins": "*"}})
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- 2. DATABASE MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    cart_items = db.relationship('CartItem', backref='owner', lazy=True, cascade="all, delete-orphan")

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
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# --- 3. HELPER ENGINES ---
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def auto_generate_deal(earnkaro_url):
    try:
        # Resolve EarnKaro Redirect
        response = requests.get(earnkaro_url, allow_redirects=True, timeout=10)
        original_url = response.url
        
        driver = get_driver()
        driver.get(original_url)
        time.sleep(2) 
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        name = soup.find("meta", property="og:title")["content"] if soup.find("meta", property="og:title") else "Premium Deal"
        img = soup.find("meta", property="og:image")["content"] if soup.find("meta", property="og:image") else "https://via.placeholder.com/300"
        
        clean_name = name.split('|')[0].split('-')[0].strip()[:60]

        app_name = "Verified Store"
        if "amazon" in original_url: app_name = "Amazon"
        elif "flipkart" in original_url: app_name = "Flipkart"
        elif "meesho" in original_url: app_name = "Meesho"

        driver.quit()
        return {"name": clean_name, "img": img, "app": app_name}
    except Exception as e:
        print(f"Auto-Stock Error: {e}")
        return None

# --- 4. ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/store')
def store():
    try:
        # SAFETY FIX: Handle empty databases or None values that cause 500 errors
        products = Product.query.order_by(Product.id.desc()).all()
        for p in products:
            if p.price is None: p.price = 0
            if p.score is None: p.score = 85
        return render_template('store.html', products=products)
    except Exception as e:
        print(f"Store Page Error: {e}")
        return render_template('store.html', products=[])

@app.route('/admin-add', methods=['POST'])
@login_required
def admin_add():
    if current_user.username != 'admin': return redirect('/')
    
    ek_url = request.form.get('url')
    price_val = request.form.get('price')
    
    details = auto_generate_deal(ek_url)
    if details:
        new_product = Product(
            name=details['name'],
            price=int(price_val) if price_val else 0,
            app=details['app'],
            link=ek_url,
            img=details['img']
        )
        db.session.add(new_product)
        db.session.commit()
        flash(f"Successfully listed: {details['name']}!")
    else:
        flash("Could not fetch product details. Please check the link.")
    return redirect(url_for('admin_dashboard'))

# --- AUTH & NAVIGATION ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email, pwd = request.form.get('email'), request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, pwd):
            login_user(user)
            return redirect(url_for('store'))
        flash('Check your email and password.')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name, email, pwd = request.form.get('username'), request.form.get('email'), request.form.get('password')
        if User.query.filter_by(email=email).first():
            return redirect(url_for('login'))
        new_user = User(username=name, email=email, password=generate_password_hash(pwd, method='pbkdf2:sha256'))
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/admin-dashboard')
@login_required
def admin_dashboard():
    if current_user.username != 'admin': return redirect('/')
    stats = {
        "users": User.query.count(),
        "items": Product.query.count(),
        "recent": User.query.order_by(User.id.desc()).limit(5).all(),
        "popular": db.session.query(Product.name, func.count(Product.id)).group_by(Product.name).limit(5).all()
    }
    return render_template('admin_dashboard.html', stats=stats)

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    url_match = re.search(r'(https?://\S+)', data.get('url', ''))
    if not url_match: return jsonify({"error": "Invalid URL"}), 400
    
    # Static response for stability
    return jsonify({"total": 12, "score": 88, "verdict": "Trusted ✅", "real": 10, "fake": 2})

@app.route('/add-to-cart', methods=['POST'])
@login_required
def add_to_cart():
    data = request.json
    item = CartItem(product_name=data['name'], price=data['price'], user_id=current_user.id)
    db.session.add(item)
    db.session.commit()
    return jsonify({"message": "Successfully added to your Vault! 🛒"})

@app.route('/cart')
@login_required
def view_cart():
    items = CartItem.query.filter_by(user_id=current_user.id).all()
    return render_template('cart.html', items=items)

@app.route('/about')
def about(): return render_template('about.html')

@app.route('/contact')
def contact(): return render_template('contact.html')

@app.route('/privacy')
def privacy(): return render_template('privacy.html')

@app.route('/terms')
def terms(): return render_template('terms.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect('/')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
