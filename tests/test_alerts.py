import os, tempfile, unittest
from price_tracker import parse_price, first_price_after, should_alert

class Alerts(unittest.TestCase):
    def test_rupee_price_with_indian_commas(self):
        self.assertEqual(parse_price("₹1,29,999.00"), 129999.0)
    def test_flipkart_price_is_first_amount_after_name(self):
        txt = "Login Cart ₹99 offer\nApple iPhone 15 (Black, 128 GB)\n4.6★\n₹61,999\n₹69,900 13% off"
        self.assertEqual(first_price_after(txt, "Apple iPhone 15 (Black, 128 GB)"), 61999.0)
    def test_any_drop_alerts_when_no_target(self):
        self.assertTrue(should_alert(1000, 950, None))
    def test_price_rise_or_same_never_alerts(self):
        self.assertFalse(should_alert(1000, 1000, None)); self.assertFalse(should_alert(1000, 1100, None))
    def test_drop_above_target_does_not_alert(self):
        self.assertFalse(should_alert(1000, 950, 900))
    def test_drop_to_target_alerts(self):
        self.assertTrue(should_alert(1000, 900, 900))
    def test_alert_link_code_binds_one_chat_only(self):
        import storage
        storage.SQLITE_PATH = os.path.join(tempfile.mkdtemp(), "a.db"); storage.init(); storage.init_alerts()
        a = storage.create_alert("https://www.amazon.in/dp/B0CHX1W1XY", "amazon", 900.0)
        self.assertEqual(storage.due_alerts(6 * 3600), [])          # not linked yet
        self.assertEqual(storage.link_alert(a["link_code"], 111)["chat_id"], "111")
        self.assertIsNone(storage.link_alert(a["link_code"], 222))  # someone else can't steal it
        self.assertEqual(len(storage.due_alerts(6 * 3600)), 1)
        storage.record_price(a["alert_id"], 950.0)
        self.assertEqual(storage.due_alerts(6 * 3600), [])          # checked, wait 6 hours
        self.assertEqual(storage.stop_chat(111), 1)

if __name__ == "__main__":
    unittest.main()
