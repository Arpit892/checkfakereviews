"""apply_compare.py - adds "Compare the last two products" + Telegram alert button.

Run ONCE from inside the checkfakereview-main folder:   python apply_compare.py
It edits static/index.html, browser_scraper.py, scraper.py and remote_worker.py
(a .bak copy of each is saved first). If a spot can't be found it stops and
changes nothing in that file.
"""
import shutil
import sys

MARK = "cfr-compare-v5"

SPECS_CODE = r'''SPEC_KEYS = {
    "colour": "Colour", "color": "Colour", "brand": "Brand", "model name": "Model", "model": "Model",
    "material": "Material", "material type": "Material", "item weight": "Weight", "weight": "Weight",
    "size": "Size", "warranty": "Warranty", "warranty summary": "Warranty", "warranty type": "Warranty",
    "battery capacity": "Battery", "battery life": "Battery", "display size": "Display size",
    "screen size": "Display size", "ram": "RAM", "ram size": "RAM", "ram memory installed size": "RAM",
    "internal storage": "Storage", "memory storage capacity": "Storage",
    "connectivity technology": "Connectivity", "connectivity": "Connectivity",
    "processor": "Processor", "processor type": "Processor", "water resistance level": "Water resistance",
}


def parse_specs_text(text, limit=8):
    """Pick colour, brand, material... out of the visible page text.
    Handles 'Colour<TAB>Black' table rows (Amazon) and 'Colour' / 'Black' on two lines (Flipkart).
    Only keys in SPEC_KEYS are read, so nothing is invented."""
    specs = {}
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    for i, line in enumerate(lines):
        key = val = None
        if "\t" in line:
            k, _, v = line.partition("\t")
            key, val = k.strip().rstrip(":").lower(), v.strip()
        elif line.rstrip(":").lower() in SPEC_KEYS and i + 1 < len(lines):
            key, val = line.rstrip(":").lower(), lines[i + 1]
        label = SPEC_KEYS.get(key or "")
        if label and val and len(val) <= 80 and label not in specs and val.lower() not in SPEC_KEYS:
            specs[label] = val
        if len(specs) >= limit:
            break
    return specs


def read_specs(page, site):
    """Key specs (colour, brand, material...) from the product page. Never fails the scan."""
    try:
        page.mouse.wheel(0, 1400)
        page.wait_for_timeout(700)
        return parse_specs_text(page.locator("body").inner_text(timeout=5000))
    except Exception:  # noqa: BLE001
        return {}


'''

CSS = r"""
    /* cfr-compare-v5: side-by-side comparison */
    .vs { position:relative; margin-top:18px; padding-top:6px; }
    .vs::before { content:""; position:absolute; left:50%; top:0; bottom:0; width:2px; margin-left:-1px;
      background:var(--ink); transform:scaleY(0); transform-origin:top; transition:transform 1.2s ease; }
    .vs.draw::before { transform:scaleY(1); }
    .vs-head, .vs-cells { display:grid; grid-template-columns:1fr 1fr; }
    .vs-head .h { padding:30px 16px 12px; text-align:center; font-weight:800; font-size:16px; line-height:1.3; }
    .vs-badge { position:absolute; left:50%; top:0; transform:translateX(-50%); z-index:2; background:var(--ink);
      color:#fff; font-size:12px; font-weight:800; padding:6px 9px; border-radius:99px; }
    .vs-label { position:relative; z-index:1; width:max-content; max-width:80%; margin:14px auto 6px; padding:3px 12px;
      background:#fff; border:1px solid var(--line); border-radius:99px; color:var(--muted); font-size:11px;
      font-weight:800; letter-spacing:.8px; text-transform:uppercase; text-align:center; }
    .vs-cell { margin:0 8px; padding:9px 14px; border-radius:10px; text-align:center; font-size:15px; font-weight:700;
      opacity:0; transform:translateY(6px); animation:vsIn .4s ease forwards; word-break:break-word; }
    @keyframes vsIn { to { opacity:1; transform:none; } }
    .vs-cell.win { background:var(--mint); color:var(--green); }
    .vs-cell.win::after { content:" \2713 better"; font-size:11px; font-weight:800; }
    .vs-cell small { display:block; margin-top:3px; font-size:12px; font-weight:400; font-style:italic; color:var(--muted); }
    #vs-alert-links .tg-btn { margin-top:8px; font-size:15px; }
    @media (max-width:700px) { .vs-cell { margin:0 3px; padding:8px 6px; font-size:13px; } .vs-head .h { font-size:14px; padding:30px 6px 10px; } }
"""

HTML = r"""<!-- cfr-compare-v5 -->
      <section class="extra-card" id="vs-area" style="display:none">
        <div class="eyebrow">Compare</div>
        <h3>Compare your last two scans</h3>
        <p class="hint" id="vs-sub"></p>
        <button class="submit-btn" id="vs-open">Compare the last two products</button>
        <div id="vs-result"></div>
        <div id="vs-alert-wrap" style="display:none; margin-top:18px">
          <button class="submit-btn" id="vs-alert-btn" style="background:#229ED9">Get price drop alert on Telegram</button>
          <p class="hint" id="vs-alert-status"></p>
          <div id="vs-alert-links"></div>
        </div>
      </section>

      """

JS = r"""
  <script>
  // cfr-compare-v5: remember the last two scans, compare them side by side, then offer Telegram alerts
  (function () {
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const recent = [];
    let current = [];

    window.cfrOnReport = function (d) {
      if (!d || d.error) return;
      const key = d.product_url || d.product_name;
      const i = recent.findIndex(x => (x.product_url || x.product_name) === key);
      if (i >= 0) recent.splice(i, 1);
      recent.push(d);
      while (recent.length > 2) recent.shift();
      if (recent.length === 2) {
        $('vs-area').style.display = 'block';
        $('vs-sub').textContent = short(recent[0].product_name) + '  vs  ' + short(recent[1].product_name);
      }
    };

    function short(n) { n = String(n || 'Product'); return n.length > 45 ? n.slice(0, 44) + '\u2026' : n; }
    const money = (v) => v == null ? null : '\u20B9' + Number(v).toLocaleString('en-IN');
    function avg(d) {
      const r = (d.per_review || []).map(x => x.rating).filter(x => x != null);
      return r.length ? r.reduce((a, b) => a + b, 0) / r.length : null;
    }
    function aspect(d, topic) {
      const ins = d.insights || {};
      const p = (ins.pros || []).find(x => x.topic === topic);
      const c = (ins.cons || []).find(x => x.topic === topic);
      if (p) return { n: p.mentions, text: '\uD83D\uDC4D Praised (' + p.mentions + '\u00D7)', quote: p.quote };
      if (c) return { n: -c.mentions, text: '\uD83D\uDC4E Complaints (' + c.mentions + '\u00D7)', quote: c.quote };
      return { n: null, text: 'Not mentioned' };
    }
    function better(a, b, higher) {
      if (a.n == null || b.n == null || a.n === b.n) return null;
      return (a.n > b.n) === higher ? 'a' : 'b';
    }

    function buildRows(a, b) {
      const rows = [];
      rows.push({ label: 'Trust score', a: { text: a.trust_score + '/100', n: a.trust_score }, b: { text: b.trust_score + '/100', n: b.trust_score }, hi: true });
      rows.push({ label: 'Verdict', a: { text: a.label }, b: { text: b.label } });
      rows.push({ label: 'Price', a: { text: money(a.price) || 'Not found', n: a.price }, b: { text: money(b.price) || 'Not found', n: b.price }, hi: false });
      const ra = avg(a), rb = avg(b);
      rows.push({ label: 'Avg. recent rating', a: { text: ra == null ? '\u2013' : ra.toFixed(1) + ' \u2605', n: ra }, b: { text: rb == null ? '\u2013' : rb.toFixed(1) + ' \u2605', n: rb }, hi: true });
      rows.push({ label: 'Durability (from reviews)', a: aspect(a, 'Build quality'), b: aspect(b, 'Build quality'), hi: true });
      const topics = [];
      [a, b].forEach(d => { const ins = d.insights || {}; (ins.pros || []).concat(ins.cons || []).forEach(x => { if (x.topic !== 'Build quality' && !topics.includes(x.topic)) topics.push(x.topic); }); });
      topics.forEach(t => rows.push({ label: t, a: aspect(a, t), b: aspect(b, t), hi: true }));
      const sa = a.specs || {}, sb = b.specs || {};
      const keys = Object.keys(sa); Object.keys(sb).forEach(k => { if (!keys.includes(k)) keys.push(k); });
      keys.forEach(k => rows.push({ label: k, a: { text: sa[k] || '\u2014' }, b: { text: sb[k] || '\u2014' } }));
      rows.push({ label: 'Store', a: { text: a.site || '' }, b: { text: b.site || '' } });
      return { rows, hasSpecs: keys.length > 0 };
    }

    $('vs-open').addEventListener('click', () => {
      if (recent.length < 2) return;
      const a = recent[0], b = recent[1];
      current = [a, b];
      const { rows, hasSpecs } = buildRows(a, b);
      let winsA = 0, winsB = 0, delay = 0;
      const cell = (side, r, w) => {
        const winner = w === side;
        if (winner) { side === 'a' ? winsA++ : winsB++; }
        const c = r[side];
        return '<div class="vs-cell' + (winner ? ' win' : '') + '" style="animation-delay:' + delay.toFixed(2) + 's">' + esc(c.text) +
          (c.quote ? '<small>\u201C' + esc(c.quote) + '\u201D</small>' : '') + '</div>';
      };
      let html = '<div class="vs" id="vs"><span class="vs-badge">VS</span><div class="vs-head"><div class="h">' + esc(short(a.product_name)) +
        '</div><div class="h">' + esc(short(b.product_name)) + '</div></div>';
      rows.forEach(r => {
        const w = r.hi === undefined ? null : better(r.a, r.b, r.hi);
        delay += 0.12;
        html += '<div class="vs-label">' + esc(r.label) + '</div><div class="vs-cells">' + cell('a', r, w) + cell('b', r, w) + '</div>';
      });
      html += '</div>';
      const total = winsA + winsB;
      html += '<p class="cmp-winner" style="margin-top:18px">' +
        (total === 0 ? 'No clear winner on the rows that can be compared.'
          : winsA === winsB ? 'Tied: each product is better on ' + winsA + ' row' + (winsA > 1 ? 's' : '') + '.'
          : esc(short(winsA > winsB ? a.product_name : b.product_name)) + ' is better on ' + Math.max(winsA, winsB) + ' of ' + total + ' comparable rows.') + '</p>';
      if (!hasSpecs) html += '<p class="hint">Colour and other specs could not be read from these pages, so only price, rating and review points are compared.</p>';
      $('vs-result').innerHTML = html;
      $('vs-alert-wrap').style.display = 'none';
      $('vs-alert-links').innerHTML = ''; $('vs-alert-status').textContent = '';
      requestAnimationFrame(() => requestAnimationFrame(() => $('vs').classList.add('draw')));
      setTimeout(() => { $('vs-alert-wrap').style.display = 'block'; }, 1300 + delay * 1000);
      $('vs-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });

    $('vs-alert-btn').addEventListener('click', async () => {
      const btn = $('vs-alert-btn'), status = $('vs-alert-status'), box = $('vs-alert-links');
      btn.disabled = true; box.innerHTML = ''; status.textContent = 'Creating alerts\u2026';
      let made = 0;
      for (const d of current) {
        if (!d.product_url) continue;
        try {
          const res = await fetch('/api/alerts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: d.product_url, product_name: d.product_name || '', current_price: d.price || null, target_price: null }) });
          const r = await res.json();
          if (r.error) { status.textContent = r.error; btn.disabled = false; return; }
          made++;
          const a = document.createElement('a');
          a.className = 'tg-btn'; a.target = '_blank'; a.rel = 'noopener'; a.href = r.telegram_link;
          a.textContent = 'Open Telegram for: ' + short(d.product_name);
          box.appendChild(a);
          watch(r.alert_id, a, d);
        } catch (e) { status.textContent = 'Could not reach the server: ' + e.message; btn.disabled = false; return; }
      }
      status.textContent = made ? 'Tap each button, then press START in Telegram. We check prices every 6 hours.' : 'No product link available for an alert.';
      btn.disabled = false;
    });

    async function watch(alertId, linkEl, d) {
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 5000));
        try {
          const s = await (await fetch('/api/alerts/' + alertId)).json();
          if (s.linked) { linkEl.textContent = '\u2705 Alert is on for: ' + short(d.product_name); linkEl.style.background = '#08775c'; linkEl.removeAttribute('href'); return; }
        } catch (e) { /* keep waiting */ }
      }
    }
  })();
  </script>
"""


def edit(path, old, new, count=1):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if MARK in src and path.endswith("index.html"):
        return "already patched"
    n = src.count(old)
    if n != count:
        sys.exit(f"STOP: in {path} I expected to find this text {count}x but found it {n}x:\n  {old[:90]!r}\nNothing was changed in that file.")
    return src.replace(old, new, 1)


def write(path, text):
    shutil.copyfile(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("patched", path)


def main():
    # ---- index.html: all edits computed first, written only if every anchor was found
    p = "static/index.html"
    with open(p, encoding="utf-8") as f:
        if MARK in f.read():
            print("index.html already patched; skipping")
        else:
            s = edit(p, "  </style>", CSS + "  </style>")
            s2 = s
            for old, new in [
                ("      <!-- ============ Compare two products ============ -->", HTML + "<!-- ============ Compare two products ============ -->"),
                ("    function renderReport(data) {\n", "    function renderReport(data) {\n      if (window.cfrOnReport) window.cfrOnReport(data);\n"),
                ("</body>", JS + "</body>"),
            ]:
                if s2.count(old) != 1:
                    sys.exit(f"STOP: could not find a unique spot in {p}: {old[:70]!r}. Nothing changed.")
                s2 = s2.replace(old, new, 1)
            write(p, s2)

    # ---- backend: read specs
    p = "browser_scraper.py"
    with open(p, encoding="utf-8") as f:
        src = f.read()
    if "def read_specs" in src:
        print("browser_scraper.py already patched; skipping")
    else:
        for old in ["def _read(page, site):\n",
                    "            description = read_description(page, site)\n            if site_for_url(page.url) != site:",
                    "return ProductPage(site, product_id, name, reviews[:max_reviews], page.url, price, description)"]:
            if src.count(old) != 1:
                sys.exit(f"STOP: could not find a unique spot in {p}: {old[:70]!r}. Nothing changed.")
        src = src.replace("def _read(page, site):\n", SPECS_CODE + "def _read(page, site):\n", 1)
        src = src.replace("            description = read_description(page, site)\n            if site_for_url(page.url) != site:",
                          "            description = read_description(page, site)\n            specs = read_specs(page, site)\n            if site_for_url(page.url) != site:", 1)
        src = src.replace("return ProductPage(site, product_id, name, reviews[:max_reviews], page.url, price, description)",
                          "return ProductPage(site, product_id, name, reviews[:max_reviews], page.url, price, description, specs)", 1)
        write(p, src)

    p = "scraper.py"
    with open(p, encoding="utf-8") as f:
        src = f.read()
    if "specs: dict" in src:
        print("scraper.py already patched; skipping")
    else:
        old = "    description: List[str] = field(default_factory=list)\n"
        if src.count(old) != 1:
            sys.exit(f"STOP: could not find a unique spot in {p}. Nothing changed.")
        write(p, src.replace(old, old + "    specs: dict = field(default_factory=dict)\n", 1))

    p = "remote_worker.py"
    with open(p, encoding="utf-8") as f:
        src = f.read()
    if '"specs"' in src:
        print("remote_worker.py already patched; skipping")
    else:
        old = '                "price": getattr(page, "price", None),\n'
        if src.count(old) != 1:
            sys.exit(f"STOP: could not find a unique spot in {p}. Nothing changed.")
        write(p, src.replace(old, old + '                "specs": getattr(page, "specs", {}),\n', 1))

    print("\nDone. Restart remote_worker.py on your PC, then commit + push to GitHub so Render redeploys.")


if __name__ == "__main__":
    main()
