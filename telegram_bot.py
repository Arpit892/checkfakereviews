"""telegram_bot.py — talks to your Telegram bot (runs on YOUR PC only).

The bot token (TELEGRAM_BOT_TOKEN) never goes to Render. New messages are read
with getUpdates, so Render's free plan sleeping doesn't break anything.

Commands people can send the bot:
  /start <code>  sent automatically when they tap "Get alert on Telegram"
  /list          show their active alerts
  /stop          turn off all their alerts
"""
import os
import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip().strip('"').strip("'")
API = f"https://api.telegram.org/bot{TOKEN}"
_offset = 0


def enabled() -> bool:
    return bool(TOKEN)


def send(chat_id, text: str) -> bool:
    try:
        r = requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=20)
        return r.ok
    except requests.RequestException:
        return False


def get_updates(timeout: int = 25) -> list:
    """Long-poll Telegram. Returns [(chat_id, text), ...]."""
    global _offset
    r = requests.get(f"{API}/getUpdates", params={"offset": _offset, "timeout": timeout,
                                                   "allowed_updates": '["message"]'}, timeout=timeout + 10)
    if r.status_code == 409:  # a webhook is set; getUpdates needs it removed
        requests.post(f"{API}/deleteWebhook", timeout=20)
        return []
    r.raise_for_status()
    out = []
    for upd in r.json().get("result", []):
        _offset = upd["update_id"] + 1
        msg = upd.get("message") or {}
        if msg.get("chat", {}).get("id") and isinstance(msg.get("text"), str):
            out.append((msg["chat"]["id"], msg["text"].strip()))
    return out


def fmt_rupees(v) -> str:
    if v is None:
        return "?"
    return "₹" + (f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}")
