# checkfakereviews.online — going from 3 demo products to any product

## What was actually wrong

Nothing to do with hosting. `analyze_demo.py` looked up the URL in a
3-item dictionary and returned a "coming soon" string for everything else.
No scrape was ever attempted on the hosted build. Splitting the stack across
Vercel + Render + a hosted DB would have produced the identical message.

## What these files change

| File | Status | What it does |
|---|---|---|
| `scraper.py` | new | Live Amazon/Flipkart fetch + review parsing, proxy-aware, fails with a specific reason code |
| `analyze_live.py` | new | Scores reviews without GPT-2 so it fits in 512MB; optional GPT-2 perplexity via `USE_GPT2=1` |
| `storage.py` | new | Result cache + job store; SQLite by default, Postgres when `DATABASE_URL` is set |
| `app_render.py` | rewritten | Analyzes any product; demo data is now only a fallback |
| `requirements-render.txt` | new | Deps, with notes on what must *not* be installed on the free tier |
| `analyze_demo.py` | retired | No longer imported; `demo_data.py` is still used for fallback |

Response shape is unchanged (`trust_score`, `label`, `reviews_analyzed`,
`per_review[...]`), so `static/index.html` works without edits. New optional
fields: `source` (`live` / `cache` / `demo-fallback`), `error_code`, `hint`.

## Do it in this order

**1. Deploy this and check `/api/health`.**
It reports `proxy_mode` and warns when scrapes go out from a bare datacenter IP.

**2. Try a product that isn't one of the three.**
- Real result → you're done with the hard part.
- `error_code: BLOCKED` → expected from Render. Go to step 3.
- `error_code: NO_REVIEWS` → the page loaded but selectors missed; Flipkart
  rotates its obfuscated CSS classes every few weeks, so `parse_flipkart`
  needs its selector list refreshed.

**3. Add a proxy — this is the step that actually unlocks "any product".**
Set `SCRAPERAPI_KEY` (simplest) or `PROXY_URL` (Bright Data, Smartproxy,
Oxylabs). Amazon and Flipkart block known cloud IP ranges; your laptop works
because it's on a residential connection. There is no free workaround, and
this is almost certainly why the allowlist got written in the first place.

**4. Then, and only then, the architecture split you asked about.**
- **Vercel** — frontend only. Set `ALLOW_ORIGINS` on the backend to your
  Vercel domain. Don't put the scraper on Vercel: serverless functions cap
  out at 10–60s and a proxied scrape can exceed that.
- **Render** — keeps the API. Note the free tier sleeps after ~15 min idle,
  so the first request takes ~50s to wake. That looks like a hang to users.
- **Hosted Postgres** (Neon/Supabase free tier) — set `DATABASE_URL`. This is
  what makes the cache survive restarts; Render's disk is wiped on every cold
  start. Cache hits also mean fewer requests to the stores, which means fewer
  chances to get blocked.
- If scrapes run long, switch the frontend to `POST /api/check/async` →
  poll `GET /api/jobs/{job_id}` instead of the synchronous `/api/check`.

## Environment variables

```
ALLOW_ORIGINS=https://checkfakereviews.online,https://your-app.vercel.app
SCRAPERAPI_KEY=...          # or PROXY_URL=http://user:pass@host:port
DATABASE_URL=postgresql://...
CACHE_TTL_SECONDS=21600
DEMO_FALLBACK=1
MAX_REVIEWS=5
USE_GPT2=0                  # 1 only on >=2GB RAM
```

## Honest limitations

- **I could not test live scraping.** This sandbox blocks amazon.in and
  flipkart.com, so the parsers were verified against saved HTML fixtures, and
  the error paths were verified against the real 403s. Expect to adjust
  selectors on first contact with live pages.
- **The heuristic engine is not your GPT-2 perplexity model.** It approximates
  the same signals (predictable phrasing, low burstiness, generic filler,
  duplicate text, AI leftovers) with regex and statistics. On your demo soap
  product it scores 74 where your pipeline scored 73, and it flags the same
  review as AI-generated. Different products will diverge more. If the
  perplexity number is central to the project's claim, run the GPT-2 backend
  on a paid instance and keep heuristics as the free-tier fallback.
- **Account-age / reviewer-history checks aren't implemented here.** Those need
  the reviewer profile page, which is a second request per review — five times
  the block risk. Add it behind a flag once proxying works.
- **Scraping these sites is against their terms of service.** Rate-limit
  yourself, cache aggressively, and be aware this is a real constraint on
  scaling the project publicly.
