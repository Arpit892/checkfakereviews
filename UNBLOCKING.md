# Getting past "Served a CAPTCHA / bot-check page"

That message means the code worked and the store refused the request. Amazon
and Flipkart fingerprint datacenter IP ranges; Render's belong to one. Nothing
you change inside `scraper.py` — headers, user-agents, delays — reliably fixes
this, because the block happens before your parser ever sees a product page.

First, confirm it's the IP: run `scrape_product()` on your laptop. Works at
home, CAPTCHAs on Render → IP-based, and only these three options help.

---

## Option A — Home worker (free, implemented, recommended for now)

Render holds the queue; **your machine does the scraping** from the same
residential IP that already works in your terminal.

```
Browser → Render /api/check → blocked → job queued
                                   ↓
        your laptop (remote_worker.py) polls, scrapes, posts result back
                                   ↓
Browser polls /api/jobs/{id} → result
```

Setup:

1. Render → Environment → add `WORKER_TOKEN` = any long random string. Redeploy.
2. On your machine, in the project folder:
   ```bash
   pip install requests beautifulsoup4
   export BACKEND_URL=https://checkfakereviews.online
   export WORKER_TOKEN=<same string>
   python remote_worker.py
   ```
3. Check `https://checkfakereviews.online/api/health` → `"worker_online": true`.

Now any product works — while your worker is running. When you close it, the
site falls back to demo data exactly as before. Honest limits: it's your home
IP serving every visitor, so throttle via `MIN_GAP_SECONDS`, and don't expect
it to survive real traffic. For a college/portfolio project it's fine.

## Option B — Paste HTML (free, implemented, always works)

`POST /api/analyze-html` with `{url, html}`. The visitor's own browser already
loaded the page, so there's nothing to block. Ask them to open the product
page, scroll the reviews into view, Ctrl+S (or view-source → copy), and paste.

Clunky, but it never fails and it works with zero infrastructure. Good as the
"it's blocked right now" fallback in your UI.

## Option C — Proxy / scraping API (paid, the only real scaling answer)

Set one env var on Render and everything works server-side with no worker:

```
SCRAPERAPI_KEY=...        # ScraperAPI — free trial ~1,000 requests
# or
PROXY_URL=http://user:pass@host:port    # Bright Data, Smartproxy, Oxylabs
```

`scraper.py` already routes through whichever is set. Free trials will carry a
demo; sustained use costs money. There is no free residential proxy worth
trusting with your traffic.

---

## Frontend change you still need

`/api/check` can now return a queued job instead of a result. Handle it:

```js
async function check(url) {
  const r = await fetch(`${API}/api/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  }).then(r => r.json());

  if (r.status !== "queued") return r;      // live, cache, demo or error

  for (let i = 0; i < 40; i++) {            // ~2 min ceiling
    await new Promise(s => setTimeout(s, 3000));
    const j = await fetch(`${API}/api/jobs/${r.job_id}`).then(r => r.json());
    if (j.status === "done" || j.status === "error") return j.result;
  }
  return { error: "Analysis timed out. The scraping worker may be offline." };
}
```

Show a spinner with something like "scraping the product page…" while polling —
the first request after Render wakes from sleep can take ~50s on the free tier.

## What each `source` value in the response means

| `source` | Meaning |
|---|---|
| `live` | Scraped from Render directly (needs Option C) |
| `worker` | Scraped by your home worker (Option A) |
| `pasted-html` | Parsed from HTML the user supplied (Option B) |
| `cache` | Previously analyzed, served from the database |
| `demo-fallback` | Everything failed; precomputed result for a known demo product |
