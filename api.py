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

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)

# --- SPEED OPTIMIZED BROWSER ---
def get_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Disable images and heavy UI elements for speed
    prefs = {"profile.managed_default_content_settings.images": 2}
    options.add_experimental_option("prefs", prefs)
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def smart_scrape(url):
    driver = get_driver()
    reviews = []
    try:
        driver.get(url)
        # Scroll 3 times to load approx 100 reviews without excessive wait
        for _ in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        elements = soup.find_all(['p', 'div', 'span'])
        for e in elements:
            text = e.get_text(strip=True)
            if 30 < len(text) < 500:
                reviews.append(text)
        # Return up to 100 unique reviews
        return list(dict.fromkeys(reviews))[:100]
    except: return []
    finally: driver.quit()

@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        data = request.json
        raw_input = data.get('url', '')
        url_match = re.search(r'(https?://\S+)', raw_input)
        
        if not url_match:
            return jsonify({"total": 0, "score": 0, "verdict": "Invalid Link 🚫"}), 200

        url = url_match.group(1).split('?')[0].rstrip('.,;:)')
        
        # Scrape 100 reviews
        reviews = smart_scrape(url)
        total = len(reviews)
        
        fake_count = 0
        bad_count = 0
        # Keywords to detect genuinely bad customer experiences
        bad_keywords = ["worst", "waste", "useless", "broken", "fake", "bad quality", "not working"]

        for r in reviews:
            text_low = r.lower()
            # Identify Fake (short/generic)
            if len(r) < 45 and any(word in text_low for word in ["good", "nice", "best", "ok"]):
                fake_count += 1
            # Identify Bad (negative reviews)
            elif any(word in text_low for word in bad_keywords):
                bad_count += 1
        
        # New Trust Score: subtracts both Fakes and Bad reviews from the total
        real_trusted = total - (fake_count + bad_count)
        trust_score = int((real_trusted / total) * 100) if total > 0 else 0
        if trust_score < 0: trust_score = 0

        # Updated Verdicts for the stricter score
        if total == 0: verdict = "Insufficient Data"
        elif trust_score >= 80: verdict = "Highly Trusted ✅"
        elif trust_score >= 50: verdict = "Caution: Mixed/Average ⚠️"
        else: verdict = "High Risk / Poor Quality 🚫"

        return jsonify({
            "total": total, 
            "real": real_trusted, 
            "fake": fake_count, 
            "bad": bad_count, # New field for dashboard
            "score": trust_score, 
            "verdict": verdict
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500
