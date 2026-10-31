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
