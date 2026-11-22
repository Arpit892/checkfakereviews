"""telegram_bot.py — talks to Telegram bot @checkfakereviews_bot.


"""
import hashlib
import os

import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
API = f"https://api.telegram.org/bot{TOKEN}"
_offset = 0

HELP = ("Hi! I send price-drop alerts for CheckFakeReviews.\n"
        "Scan a product on checkfakereviews.online, tap \"Get price drop alert on Telegram\", "
        "then press START here.\n\n/list — your alerts\n/stop — turn all alerts off")


def enabled() -> bool:
    return bool(TOKEN)


def send(chat_id, text: str) -> bool:
    if not TOKEN or not chat_id:
        return False
    try:
        r = requests.post(f"{API}/sendMessage", timeout=20, json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        if not r.ok:
            print(f"[telegram] sendMessage failed [{r.status_code}]: {r.text[:200]}")
        return r.ok
    except requests.RequestException as e:
        print(f"[telegram] sendMessage failed: {e}")
        return False


def fmt_rupees(v) -> str:
    if v is None:
        return "?"
    return "₹" + (f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}")


def esc(s) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------ messages -----------------------------------

def alert_line(a: dict) -> str:
    target = f" (alert at {fmt_rupees(a['target_price'])} or less)" if a.get("target_price") else ""
    now = f" — now {fmt_rupees(a['last_price'])}" if a.get("last_price") else ""
    return f"• {esc(a.get('product_name') or a.get('url'))}{now}{target}"


def linked_text(a: dict, hours: float) -> str:
    return ("✅ Price alert is on for:\n" + alert_line(a)
            + f"\n\nI check the price every {hours:g} hours and message you when it drops."
            + "\n/list — your alerts   /stop — turn them off")


def tracking_text(name: str, price) -> str:
    return f"📌 Now tracking <b>{esc(name or 'your product')}</b> at {fmt_rupees(price)}."


def drop_text(name: str, old, new, url: str) -> str:
    return (f"🔻 <b>Price drop!</b>\n{esc(name or 'Your product')}\n"
            f"{fmt_rupees(old)} → <b>{fmt_rupees(new)}</b> (you save {fmt_rupees(old - new)})\n{url}")


def reply_for(text: str, link, list_alerts, stop, hours: float) -> str:
    """Works out the bot's answer to one message.
    link(code) -> alert dict or None, list_alerts() -> [alerts], stop() -> count."""
    cmd, _, arg = (text or "").strip().partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/start" and arg.strip():
        a = link(arg.strip()[:40])
        return linked_text(a, hours) if a else \
            "That alert link is invalid or was already used by another Telegram account. Create a new one on the website."
    if cmd == "/list":
        alerts = list_alerts()
        return ("Your price alerts:\n" + "\n".join(alert_line(a) for a in alerts)) if alerts \
            else "You have no active price alerts."
    if cmd == "/stop":
        return f"Stopped {stop()} price alert(s)."
    return HELP


def message_from_update(update: dict):
    """(chat_id, text) from a Telegram update, or (None, None)."""
    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = msg.get("text")
    if chat_id and isinstance(text, str):
        return chat_id, text.strip()
    return None, None


# ------------------------------ webhook (Render) ---------------------------

def webhook_secret() -> str:
    """Secret Telegram sends back in every webhook call, derived from the token
    so nothing extra has to be configured."""
    return hashlib.sha256(f"cfr-telegram-webhook:{TOKEN}".encode()).hexdigest()[:48]


def set_webhook(url: str) -> dict:
    r = requests.post(f"{API}/setWebhook", timeout=20, json={
        "url": url, "secret_token": webhook_secret(),
        "allowed_updates": ["message", "edited_message"], "drop_pending_updates": False})
    return r.json()


def webhook_info() -> dict:
    return requests.get(f"{API}/getWebhookInfo", timeout=20).json()


# ------------------------------ polling (PC, old setup) --------------------

def get_updates(timeout: int = 25) -> list:
    """Long-poll Telegram. Returns [(chat_id, text), ...]."""
    global _offset
    r = requests.get(f"{API}/getUpdates", params={"offset": _offset, "timeout": timeout,
                                                   "allowed_updates": '["message"]'}, timeout=timeout + 10)
    if r.status_code == 409:
        # Render owns the bot through a webhook. Never delete it from here.
        raise RuntimeError("Render is answering the bot (webhook is set); the PC will not poll Telegram.")
    r.raise_for_status()
    out = []
    for upd in r.json().get("result", []):
        _offset = upd["update_id"] + 1
        chat_id, text = message_from_update(upd)
        if chat_id:
            out.append((chat_id, text))
    return out
