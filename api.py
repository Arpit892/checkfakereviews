from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import sqlite3
import re
import os  # <--- IMPORTANT: Added this so Render works!
from datetime import datetime
from urllib.parse import urlparse

# TEMPLATE & STATIC FOLDER CONFIG
app = Flask(__name__, template_folder='templates', static_folder='static')

# NUCLEAR CORS FIX: Allow ALL connections from ANYWHERE
CORS(app, resources={r"/*": {"origins": "*"}})

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('reviews_v3.db') 
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS cache 
                 (url TEXT PRIMARY KEY, real_count INTEGER, fake_count INTEGER, 
                  trust_score INTEGER, verdict TEXT, timestamp DATETIME)''')
    conn.commit()
    conn.close()

init_db()

# --- CONFIG ---
SITE_CONFIG = {
    "amazon": {"tag": "span", "class": "review-text-content"},
    "flipkart": {"tag": "div", "class": "t-ZTKy"},
    "myntra": {"tag": "div", "class": "user-review-main"},
    "ajio": {"tag": "div", "class": "review-content"},
    "nykaa": {"tag": "div", "class": "message"},
    "meesho": {"tag": "p", "class": "Comment__CommentText"}
}

# --- BROWSER ---
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def smart_scrape(url):
    driver = get_driver()
    reviews = []
    try:
        driver.get(url)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # UNIVERSAL GRABBER
        paragraphs = soup.find_all(['p', 'div', 'span'])
        for p in paragraphs:
            text = p.get_text(strip=True)
            if 30 < len(text) < 500:
                reviews.append(text)
        return list(set(reviews))[:50]
    except: return []
    finally: driver.quit()

# --- ROUTES ---
@app.route('/')
def home(): return render_template('index.html')

@app.route('/login')
def login(): return render_template('login.html')

@app.route('/signup')
def signup(): return render_template('signup.html')

@app.route('/about')
def about(): return render_template('about.html')

@app.route('/contact')
def contact(): return render_template('contact.html')

@app.route('/privacy')
def privacy(): return render_template('privacy.html')

@app.route('/terms')
def terms(): return render_template('terms.html')


@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        data = request.json
        url = data.get('url', '').split('?')[0]
        
        # DATABASE CONNECTION
        conn = sqlite3.connect('reviews_v3.db')
        c = conn.cursor()
        
        # CHECK CACHE
        c.execute("SELECT * FROM cache WHERE url=?", (url,))
        cached = c.fetchone()
        if cached:
            conn.close()
            return jsonify({"total": cached[1]+cached[2], "real": cached[1], "fake": cached[2], "score": cached[3], "verdict": cached[4]})

        # SCRAPE
        reviews = smart_scrape(url)
        
        # ANALYZE
        fake_count = 0
        for r in reviews:
            if len(r) < 40 or "good" in r.lower():
                fake_count += 1
        
        real_count = len(reviews) - fake_count
        trust_score = int((real_count / len(reviews)) * 100) if reviews else 0
        
        # VERDICT
        if len(reviews) == 0: verdict = "Insufficient Data"
        elif trust_score >= 80: verdict = "Highly Trusted ✅"
        elif trust_score >= 60: verdict = "Safe / Average ⚠️"
        else: verdict = "High Risk / Scam 🚫"
        
        # SAVE
        c.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?, ?, ?)", 
                  (url, real_count, fake_count, trust_score, verdict, datetime.now()))
        conn.commit()
        conn.close()

        return jsonify({"total": len(reviews), "real": real_count, "fake": fake_count, "score": trust_score, "verdict": verdict})
        
    except Exception as e:
        print(f"SERVER ERROR: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Use the port Render gives us, or default to 5000 for testing
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host='0.0.0.0', port=port)