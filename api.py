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
import os
from datetime import datetime

# TEMPLATE & STATIC FOLDER CONFIG
app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)

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

# --- BROWSER ---
def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")
    # Speed optimization: Don't load images
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def smart_scrape(url):
    driver = get_driver()
    reviews = []
    try:
        driver.get(url)
        # Scroll 3 times to load up to 100 reviews quickly
        for _ in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1) 
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        paragraphs = soup.find_all(['p', 'div', 'span'])
        for p in paragraphs:
            text = p.get_text(strip=True)
            if 30 < len(text) < 500:
                reviews.append(text)
        # Unique list capped at 100
        return list(dict.fromkeys(reviews))[:100]
    except: return []
    finally: driver.quit()

# --- ROUTES ---
@app.route('/')
def home(): return render_template('index.html')

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
        raw_input = data.get('url', '')
        url_match = re.search(r'(https?://\S+)', raw_input)
        
        if not url_match:
            return jsonify({"total": 0, "score": 0, "verdict": "Invalid Link 🚫"}), 200

        url = url_match.group(1).split('?')[0].rstrip('.,;:)')
        
        # Check Cache first
        conn = sqlite3.connect('reviews_v3.db')
        c = conn.cursor()
        c.execute("SELECT * FROM cache WHERE url=?", (url,))
        cached = c.fetchone()
        if cached:
            conn.close()
            return jsonify({"total": cached[1]+cached[2], "real": cached[1], "fake": cached[2], "score": cached[3], "verdict": cached[4]})

        # Scrape 100
        reviews = smart_scrape(url)
        if not reviews:
            return jsonify({"total": 0, "score": 0, "verdict": "No reviews found"}), 200

        fake_count = 0
        bad_count = 0
        # Keywords for genuinely bad customer experiences
        bad_keywords = ["worst", "waste", "useless", "broken", "cheap quality", "not working"]

        for r in reviews:
            text_low = r.lower()
            # Count Fakes (short/generic)
            if len(r) < 45 and any(word in text_low for word in ["good", "nice", "ok"]):
                fake_count += 1
            # Count Bad reviews
            elif any(word in text_low for word in bad_keywords):
                bad_count += 1
        
        total = len(reviews)
        # Trust Score treats both Fake and Bad reviews as negatives
        real_count = total - (fake_count + bad_count)
        trust_score = int((real_count / total) * 100) if total > 0 else 0
        if trust_score < 0: trust_score = 0

        if trust_score >= 80: verdict = "Highly Trusted ✅"
        elif trust_score >= 55: verdict = "Safe / Average ⚠️"
        else: verdict = "High Risk / Poor Quality 🚫"

        c.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?, ?, ?)", 
                  (url, real_count, fake_count + bad_count, trust_score, verdict, datetime.now()))
        conn.commit()
        conn.close()

        return jsonify({"total": total, "real": real_count, "fake": fake_count + bad_count, "score": trust_score, "verdict": verdict})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000)) 
    app.run(host='0.0.0.0', port=port)
