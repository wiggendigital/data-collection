"""The parsers, run against pages saved from the shop on 18 November 2026."""
import pathlib
import unittest

from collector import books

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
LIST_URL = "https://books.toscrape.com/catalogue/page-1.html"
ATTIC_URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"


def page(name: str) -> str:
    return books.decode((FIXTURES / name).read_bytes())


class Decode(unittest.TestCase):
    def test_pound_sign_survives(self):
        html = page("detail-attic.html")
        self.assertIn("£51.77", html)
        self.assertNotIn("Â£", html)


class Money(unittest.TestCase):
    def test_reads_amounts(self):
        self.assertEqual(books.money("£51.77"), 51.77)
        self.assertEqual(books.money("£1,051.00"), 1051.0)
        self.assertEqual(books.money("£0.00"), 0.0)

    def test_refuses_text_without_an_amount(self):
        with self.assertRaises(books.ParseError):
            books.money("free")


class Listing(unittest.TestCase):
    def setUp(self):
        self.first = books.parse_listing(page("listing-page-1.html"), LIST_URL)

    def test_twenty_products_on_a_page(self):
        self.assertEqual(len(self.first["items"]), 20)

    def test_first_product(self):
        item = self.first["items"][0]
        self.assertEqual(item["title"], "A Light in the Attic")
        self.assertEqual(item["price"], 51.77)
        self.assertEqual(item["rating"], 3)
        self.assertTrue(item["in_stock"])
        self.assertEqual(item["url"], ATTIC_URL)

    def test_full_title_not_the_shortened_one(self):
        titles = [item["title"] for item in self.first["items"]]
        self.assertFalse(any(title.endswith("...") for title in titles))

    def test_next_page_and_shop_total(self):
        self.assertEqual(self.first["next_url"], "https://books.toscrape.com/catalogue/page-2.html")
        self.assertEqual(self.first["shop_total"], 1000)

    def test_last_page_has_no_next(self):
        last = books.parse_listing(page("listing-page-50.html"),
                                   "https://books.toscrape.com/catalogue/page-50.html")
        self.assertIsNone(last["next_url"])
        self.assertEqual(len(last["items"]), 20)

    def test_a_product_without_a_link_is_an_error(self):
        broken = '<article class="product_pod"><h3></h3></article>'
        with self.assertRaises(books.ParseError):
            books.parse_listing(broken, LIST_URL)


class Detail(unittest.TestCase):
    def setUp(self):
        self.row = books.parse_detail(page("detail-attic.html"), ATTIC_URL)

    def test_every_field(self):
        row = self.row
        self.assertEqual(row["upc"], "a897fe39b1053632")
        self.assertEqual(row["title"], "A Light in the Attic")
        self.assertEqual(row["category"], "Poetry")
        self.assertEqual(row["price_excl_tax"], 51.77)
        self.assertEqual(row["price_incl_tax"], 51.77)
        self.assertEqual(row["tax"], 0.0)
        self.assertTrue(row["in_stock"])
        self.assertEqual(row["stock"], 22)
        self.assertEqual(row["rating"], 3)
        self.assertEqual(row["reviews"], 0)
        self.assertTrue(row["description_start"].startswith("It's hard to imagine a world without"))
        self.assertGreater(row["description_chars"], 200)
        self.assertEqual(row["url"], ATTIC_URL)

    def test_image_address_is_absolute(self):
        self.assertTrue(self.row["image_url"].startswith("https://books.toscrape.com/media/"))

    def test_a_page_without_a_description(self):
        url = ("https://books.toscrape.com/catalogue/"
               "alice-in-wonderland-alices-adventures-in-wonderland-1_5/index.html")
        row = books.parse_detail(page("detail-no-description.html"), url)
        self.assertEqual(row["description_chars"], 0)
        self.assertEqual(row["description_start"], "")
        self.assertEqual(len(row["upc"]), 16)
        self.assertTrue(row["category"])

    def test_a_page_that_is_not_a_product_is_an_error(self):
        with self.assertRaises(books.ParseError):
            books.parse_detail("<html><body><h1>Not found</h1></body></html>", ATTIC_URL)


if __name__ == "__main__":
    unittest.main()
