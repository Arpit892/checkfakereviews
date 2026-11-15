"""apply_compare_v6.py - compare card + Telegram alert button appear below the reviews
only from the SECOND scan onward; bot username set to checkfakereviews_bot.

Run ONCE from inside checkfakereview-main, AFTER apply_compare.py (and apply_compare_remember.py):
    python apply_compare_v6.py
"""
import shutil
import sys

HTML = "static/index.html"
APP = "app_render.py"

with open(HTML, encoding="utf-8") as f:
    h = f.read()
if "cfr-compare-v5" not in h:
    sys.exit("Run apply_compare.py first.")
if "for (const d of current)" not in h:
    sys.exit("v6 already applied; nothing to do.")

OLD_SHOW = """      if (recent.length === 2) {
        $('vs-area').style.display = 'block';
        $('vs-sub').textContent = short(recent[0].product_name) + '  vs  ' + short(recent[1].product_name);
      }
    };"""
NEW_SHOW = """      // new scan finished: drop any old comparison; show the card only once there are 2 scans
      $('vs-result').innerHTML = ''; $('vs-alert-links').innerHTML = ''; $('vs-alert-status').textContent = '';
      $('vs-area').style.display = recent.length === 2 ? 'block' : 'none';
      if (recent.length === 2) {
        $('vs-sub').textContent = short(recent[0].product_name) + '  vs  ' + short(recent[1].product_name);
      }
    };"""

# (old, new, required)
EDITS = [
    (OLD_SHOW, NEW_SHOW, True),
    # from apply_compare_remember.py: don't pop the card up on page load, only after a scan
    ("""    if (recent.length === 2) {
      $('vs-area').style.display = 'block';
      $('vs-sub').textContent = short(recent[0].product_name) + '  vs  ' + short(recent[1].product_name);
    }

    $('vs-open')""", "    $('vs-open')", False),
    ('<div id="vs-alert-wrap" style="display:none; margin-top:18px">', '<div id="vs-alert-wrap" style="margin-top:18px">', True),
    ("      $('vs-alert-wrap').style.display = 'none';\n", "", True),
    ("      setTimeout(() => { $('vs-alert-wrap').style.display = 'block'; }, 1300 + delay * 1000);\n", "", True),
    ("      for (const d of current) {", "      for (const d of recent) {", True),
]
for old, new, required in EDITS:
    n = h.count(old)
    if n == 0 and not required:
        continue
    if n != 1:
        sys.exit(f"STOP: expected 1 match in {HTML} but found {n} for: {old[:70]!r}. Nothing changed.")
    h = h.replace(old, new, 1)

shutil.copyfile(HTML, HTML + ".bak3")
with open(HTML, "w", encoding="utf-8") as f:
    f.write(h)
print("patched", HTML)

# ---- bot username (public name, not a secret; the bot TOKEN stays on your PC only)
with open(APP, encoding="utf-8") as f:
    a = f.read()
old = 'TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")'
new = 'TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "checkfakereviews_bot").strip().lstrip("@")'
if "checkfakereviews_bot" in a:
    print("app_render.py already has the bot name")
elif a.count(old) == 1:
    shutil.copyfile(APP, APP + ".bak")
    with open(APP, "w", encoding="utf-8") as f:
        f.write(a.replace(old, new, 1))
    print("patched", APP)
else:
    sys.exit("STOP: could not find the TELEGRAM_BOT_USERNAME line in app_render.py (index.html was already patched).")

print("\nDone. Push to GitHub so Render redeploys.")
