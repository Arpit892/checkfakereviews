"""v7: Render answers @checkfakereviews_bot and sends the price-drop messages."""
import os
import tempfile
import unittest
from unittest import mock

try:
    from fastapi.testclient import TestClient
    HAVE_FASTAPI = True
except Exception:  # noqa: BLE001  (fastapi/httpx not installed on this PC)
    HAVE_FASTAPI = False


@unittest.skipUnless(HAVE_FASTAPI, "needs fastapi + httpx")
class TelegramOnRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["WORKER_TOKEN"] = "test-worker-token"
        os.environ.pop("RENDER_EXTERNAL_URL", None)
        os.environ.pop("PUBLIC_URL", None)
        import storage
        import telegram_bot
        storage.SQLITE_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        telegram_bot.TOKEN = "123456:fake-token"
        import app_render
        app_render.WORKER_TOKEN = "test-worker-token"
        app_render.TELEGRAM_ON_SERVER = True
        storage.init(); storage.init_alerts()
        cls.app_render, cls.storage, cls.bot = app_render, storage, telegram_bot
        cls.client = TestClient(app_render.app)

    def setUp(self):
        self.sent = []
        p = mock.patch.object(self.bot, "send", side_effect=lambda chat, text: self.sent.append((chat, text)) or True)
        p.start(); self.addCleanup(p.stop)

    def hook(self, text, chat=555, secret=None):
        return self.client.post("/api/telegram/webhook",
                                headers={"X-Telegram-Bot-Api-Secret-Token": secret or self.bot.webhook_secret()},
                                json={"update_id": 1, "message": {"chat": {"id": chat}, "text": text}})

    def new_alert(self, target=None, price=1000.0):
        r = self.client.post("/api/alerts", json={"url": "https://www.amazon.in/dp/B0CHX1W1XY",
                                                  "product_name": "Test Phone", "current_price": price,
                                                  "target_price": target}).json()
        code = r["telegram_link"].split("start=")[1]
        self.assertTrue(r["telegram_link"].startswith("https://t.me/checkfakereviews_bot?start="))
        return r["alert_id"], code

    def price(self, alert_id, value):
        return self.client.post("/api/worker/alert-price", json={
            "token": "test-worker-token", "alert_id": alert_id, "price": value, "product_name": "Test Phone"}).json()

    def test_webhook_rejects_wrong_secret(self):
        self.assertEqual(self.hook("/list", secret="wrong").status_code, 401)
        self.assertEqual(self.sent, [])

    def test_start_links_the_chat_and_confirms(self):
        alert_id, code = self.new_alert()
        self.assertEqual(self.hook(f"/start {code}", chat=777).status_code, 200)
        self.assertTrue(self.client.get(f"/api/alerts/{alert_id}").json()["linked"])
        self.assertEqual(self.sent[-1][0], 777)
        self.assertIn("Price alert is on", self.sent[-1][1])

    def test_drop_sends_message_from_render(self):
        alert_id, code = self.new_alert(price=1000.0)
        self.hook(f"/start {code}", chat=888); self.sent.clear()
        self.assertTrue(self.price(alert_id, 950.0)["notified"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Price drop", self.sent[0][1])
        self.assertIn("₹1,000 → <b>₹950</b>", self.sent[0][1])

    def test_rise_sends_nothing(self):
        alert_id, code = self.new_alert(price=1000.0)
        self.hook(f"/start {code}", chat=889); self.sent.clear()
        self.price(alert_id, 1100.0)
        self.assertEqual(self.sent, [])

    def test_drop_above_target_sends_nothing(self):
        alert_id, code = self.new_alert(target=900.0, price=1000.0)
        self.hook(f"/start {code}", chat=890); self.sent.clear()
        self.price(alert_id, 950.0)
        self.assertEqual(self.sent, [])
        self.price(alert_id, 899.0)
        self.assertIn("Price drop", self.sent[-1][1])

    def test_stop_turns_alerts_off(self):
        alert_id, code = self.new_alert()
        self.hook(f"/start {code}", chat=999)
        self.hook("/stop", chat=999)
        self.assertIn("Stopped 1", self.sent[-1][1])
        self.assertFalse(self.client.get(f"/api/alerts/{alert_id}").json()["active"])

    def test_unlinked_alert_never_messages(self):
        alert_id, _ = self.new_alert()
        self.assertFalse(self.price(alert_id, 500.0)["notified"])
        self.assertEqual(self.sent, [])
