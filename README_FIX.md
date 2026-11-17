# CheckFakeReviews — scraping fix

## Why scans were failing
1. `remote_worker.py` imports `browser_scraper.py`, but that file was missing from the repo, so the PC worker silently used plain HTTP requests, which Amazon/Flipkart usually block.
2. Flipkart's product page is bot-protected (E002). Reviews must be read from `/product-reviews/<id>` with the **Latest** sort clicked.
3. Render only handed jobs to your PC if it had "seen" the worker in the last 2 minutes (kept in memory, lost on every Render restart/sleep). Otherwise Render tried scraping itself and got blocked.
4. The web page gave up after 2 minutes; jobs stuck in "running" were never retried; results were lost if posting back to Render failed once.
5. Flipkart parsing guessed reviews from the whole page text, which could mix unrelated text into reviews.

## What changed
- NEW `browser_scraper.py` — real Chrome on your PC, Amazon (recent sort) + Flipkart (reviews page + Latest click). If a CAPTCHA appears, you solve it in the window (not bypassed).
- NEW `review_targets.py` — safe link checking + review-page URLs (rejects look-alike domains).
- NEW `requirements-worker.txt` — install on your PC only.
- `scraper.py` — Flipkart parsing reads real review cards only; Render no longer pretends to scrape Flipkart.
- `remote_worker.py` — uses the browser for both stores; retries posting results until Render confirms.
- `app_render.py` — always queues for the PC worker when `WORKER_TOKEN` is set; constant-time token check.
- `storage.py` — stuck jobs are re-queued after 5 minutes.
- `static/index.html` — waits up to 10 minutes (Render free tier wakes slowly).
- `requirements.txt` — Postgres packages enabled (only used if `DATABASE_URL` is set).

## Setup
Render: set `WORKER_TOKEN` (long random string). Strongly recommended: a free Postgres (e.g. Neon) and set `DATABASE_URL`, so queued jobs survive Render restarts.

Your PC (PowerShell, inside the project folder):
```
pip install -r requirements-worker.txt
python -m playwright install chromium
python browser_scraper.py "https://www.flipkart.com/....../p/itm....."   # test one product
$env:BACKEND_URL="https://your-site.onrender.com"
$env:WORKER_TOKEN="same value as on Render"
python remote_worker.py
```
Leave the browser window visible the first time; if Amazon asks you to sign in or solve a check, do it once — the login stays in the PC profile.

Run tests: `python -m unittest discover -s tests`

Limits: stores can still block or change their pages; when that happens the site shows an error instead of a fake score.

## Update v2
- Flipkart changed its page layout, so reviews couldn't be read. `browser_scraper.py` now reads reviews from the visible page text (works with the new layout; tested live).
- Amazon now asks for sign-in on its full reviews page. The scraper automatically falls back to the reviews shown on the product page (no login). Optional: sign in once in the opened browser window to get the "most recent" list; the login is remembered on your PC.

## Update v3 — Compare two products + Telegram price-drop alerts
New files: `price_tracker.py` (reads the price), `telegram_bot.py` (your bot), `tests/test_alerts.py`.

**Compare:** the new "Compare two products" box checks both links and shows trust score, price, average rating and which one looks better.

**Price alerts (phone, via Telegram):**
1. In Telegram, open **@BotFather** → `/newbot` → pick a name. It gives you a **bot token** and a **username** (ends in `bot`).
2. Render → Environment: add `TELEGRAM_BOT_USERNAME=<your bot username>` (no @). Keep `WORKER_TOKEN`. Use a free Postgres as `DATABASE_URL`, otherwise alerts are lost when Render restarts.
3. Your PC, before starting the worker:
```
$env:TELEGRAM_BOT_TOKEN="token from BotFather"
python remote_worker.py
```
   The token stays only on your PC. Never put it in GitHub.
4. On the site: paste a link (optional target price) → **Create alert** → **Open Telegram** → press **START**. The bot confirms.
5. Your PC checks each price every 6 hours (`PRICE_CHECK_HOURS` to change) and sends "🔻 Price drop!" when it falls (and is at/below the target, if one was set). Users can send `/list` or `/stop` to the bot.

Limit: alerts are checked and Telegram replies are sent only while `remote_worker.py` is running on your PC.

## Update v4 — scan two products at once + good/bad points

- Your PC now runs **2 scans at the same time** (set `PARALLEL_SCANS`, 1–3). Each scan uses its own browser window and profile (`.checkfakereviews-browser` and `.checkfakereviews-browser-2`). If Amazon asks you to sign in, do it once in each window.
- Every result now lists **good things** and **bad things** buyers mention (battery, camera, heating, delivery, value…), each with a real quote from a review, plus **what the seller says** from the product description. Nothing is invented: if reviews don't mention a topic, it isn't shown.
- New file: `product_insights.py`. Changed: `browser_scraper.py`, `remote_worker.py`, `scraper.py`, `static/index.html`. New tests: `tests/test_insights.py`.

## Update v7 — Telegram bot runs on Render (24/7) + better compare

**Why:** before, @checkfakereviews_bot only replied while `remote_worker.py` was running on your PC with the bot token. If the PC was off, pressing START in Telegram did nothing.

**Now:** Render answers the bot (START, /list, /stop) any time and sends the "🔻 Price drop!" messages. Your PC only reads prices, because Amazon/Flipkart block Render.

Setup (once):
1. Telegram → @BotFather → `/mybots` → checkfakereviews_bot → **API Token** → copy it.
2. Render → your service → **Environment** → add `TELEGRAM_BOT_TOKEN` = that token → Save (Render redeploys). Do NOT put the token in GitHub.
3. Check it: open `https://checkfakereviews.online/api/telegram/setup?token=<your WORKER_TOKEN>` — it should say `"ok": true`.
4. On your PC you can remove `TELEGRAM_BOT_TOKEN`; just run `python remote_worker.py` as usual. It prints "Render answers the bot".

Notes:
- Prices are still checked only while `remote_worker.py` runs on your PC (every 6 h, `PRICE_CHECK_HOURS`).
- Render free plan sleeps; the first Telegram reply after a long idle can take ~1 minute.
- Compare (two links box) now also shows specs (colour, RAM, storage…) and which product is better rated.
- New test: `tests/test_telegram_server.py` (needs `pip install fastapi httpx`).
