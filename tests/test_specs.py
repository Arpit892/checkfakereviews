import unittest
from browser_scraper import parse_specs_text


class Specs(unittest.TestCase):
    def test_amazon_table_rows(self):
        txt = "Brand\tApple\nColour\tBlack Titanium\nSomething else\tx"
        self.assertEqual(parse_specs_text(txt), {"Brand": "Apple", "Colour": "Black Titanium"})

    def test_flipkart_two_line_rows(self):
        txt = "Specifications\nColor\nBlue\nWarranty Summary\n1 Year Brand Warranty"
        self.assertEqual(parse_specs_text(txt), {"Colour": "Blue", "Warranty": "1 Year Brand Warranty"})

    def test_nothing_invented(self):
        self.assertEqual(parse_specs_text("Nothing useful here"), {})
        self.assertEqual(parse_specs_text(""), {})

    def test_label_followed_by_label_is_ignored(self):
        self.assertEqual(parse_specs_text("Colour\nBrand\nApple"), {"Brand": "Apple"})


if __name__ == "__main__":
    unittest.main()
