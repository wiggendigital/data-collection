"""The quality rules, the comparison with the last run, the price summary and the files."""
import datetime as dt
import json
import pathlib
import tempfile
import unittest

from collector import power, store
from collector.diff import book_changes
from collector.validate import BOOK_FIELDS, book_gates, book_problems, check_books, power_gates


def book(**changes) -> dict:
    row = {"upc": "a897fe39b1053632", "title": "A Light in the Attic", "category": "Poetry",
           "price_excl_tax": 51.77, "price_incl_tax": 51.77, "tax": 0.0, "in_stock": True,
           "stock": 22, "rating": 3, "reviews": 0, "description_chars": 1200,
           "description_start": "It's hard to imagine", "image_url": "https://books.toscrape.com/media/a.jpg",
           "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"}
    row.update(changes)
    return row


class BookRules(unittest.TestCase):
    def test_a_good_row_breaks_no_rule(self):
        self.assertEqual(book_problems(book()), [])

    def test_each_rule(self):
        cases = {
            "UPC": book(upc="not-a-upc"),
            "title": book(title="  "),
            "category": book(category=""),
            "outside": book(price_incl_tax=0, price_excl_tax=0),
            "negative": book(tax=-1, price_incl_tax=50.77),
            "does not equal": book(price_excl_tax=40.0),
            "rating": book(rating=6),
            "stock is not": book(stock=-1),
            "disagrees": book(in_stock=False),
            "review count": book(reviews=None),
            "address": book(url="https://elsewhere.test/x"),
        }
        for expected, row in cases.items():
            with self.subTest(expected):
                self.assertTrue(any(expected in problem for problem in book_problems(row)),
                                book_problems(row))

    def test_a_price_that_is_text_is_caught_not_crashed_on(self):
        self.assertIn("a price is not a number", book_problems(book(price_incl_tax="51.77")))


class CheckBooks(unittest.TestCase):
    def test_rejects_bad_rows_and_says_why(self):
        result = check_books([book(), book(upc="b" * 16, rating=9)])
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rejected"][0]["problems"], ["rating is not 1 to 5"])

    def test_removes_duplicates_and_counts_them(self):
        result = check_books([book(), book(), book(upc="b" * 16)])
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["duplicates_removed"], 1)

    def test_counts_missing_optional_fields_without_rejecting(self):
        result = check_books([book(description_start="", description_chars=0)])
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["notes"]["description_start"]["missing"], 1)

    def test_rows_come_out_in_a_stable_order(self):
        rows = [book(upc="c" * 16, category="Travel", title="B"),
                book(upc="b" * 16, category="Art", title="Z"),
                book(upc="d" * 16, category="art", title="A")]
        ordered = [row["title"] for row in check_books(rows)["rows"]]
        self.assertEqual(ordered, ["A", "Z", "B"])


class Gates(unittest.TestCase):
    def checked(self, n):
        return check_books([book(upc=f"{i:016x}") for i in range(n)])

    def test_a_complete_run_passes_every_gate(self):
        gates = book_gates(shop_total=1000, listed=1000, checked=self.checked(1000),
                           failed_pages=0, limited=False)
        self.assertTrue(all(g["passed"] for g in gates))
        self.assertEqual(len(gates), 5)

    def test_a_run_that_lost_two_percent_fails(self):
        gates = book_gates(shop_total=1000, listed=1000, checked=self.checked(980),
                           failed_pages=20, limited=False)
        failed = [g["name"] for g in gates if not g["passed"]]
        self.assertIn("Every product the shop lists was collected", failed)
        self.assertIn("No page was given up on", failed)

    def test_a_list_count_that_differs_from_the_shop_fails(self):
        gates = book_gates(shop_total=1000, listed=990, checked=self.checked(990),
                           failed_pages=0, limited=False)
        self.assertFalse(next(g for g in gates if "own count" in g["name"])["passed"])


class Changes(unittest.TestCase):
    def test_first_run_has_nothing_to_compare(self):
        self.assertTrue(book_changes(None, [book()])["first_run"])

    def test_no_change(self):
        changes = book_changes([book()], [book()])
        self.assertEqual((changes["new"], changes["removed"], changes["price"], changes["stock"]),
                         (0, 0, 0, 0))

    def test_finds_each_kind_of_change(self):
        before = [book(), book(upc="b" * 16, title="Gone")]
        after = [book(price_incl_tax=49.0, price_excl_tax=49.0, stock=20),
                 book(upc="c" * 16, title="Arrived")]
        changes = book_changes(before, after)
        self.assertEqual((changes["new"], changes["removed"], changes["price"], changes["stock"]),
                         (1, 1, 1, 1))
        kinds = sorted(example["kind"] for example in changes["examples"])
        self.assertEqual(kinds, ["new", "price", "removed", "stock"])
        price = next(e for e in changes["examples"] if e["kind"] == "price")
        self.assertEqual((price["from"], price["to"]), (51.77, 49.0))


def day_payload(day: str, prices) -> list:
    return [{"NOK_per_kWh": price, "time_start": f"{day}T{hour:02d}:00:00+01:00",
             "time_end": f"{day}T{hour:02d}:59:59+01:00"} for hour, price in enumerate(prices)]


class Power(unittest.TestCase):
    DAY = dt.date(2026, 11, 18)

    def test_address(self):
        self.assertEqual(power.url_for(self.DAY, "NO2"),
                         "https://www.hvakosterstrommen.no/api/v1/prices/2026/11-18_NO2.json")

    def test_summary_of_a_day(self):
        prices = [1.0] * 22 + [0.5, 3.0]
        row = power.summarise(self.DAY, "NO2", day_payload("2026-11-18", prices))
        self.assertEqual(row["points"], 24)
        self.assertEqual(row["avg_nok"], round(sum(prices) / 24, 4))
        self.assertEqual((row["min_nok"], row["min_at"]), (0.5, "22:00"))
        self.assertEqual((row["max_nok"], row["max_at"]), (3.0, "23:00"))

    def test_refuses_half_a_day(self):
        with self.assertRaises(ValueError):
            power.summarise(self.DAY, "NO2", day_payload("2026-11-18", [1.0] * 11))

    def test_refuses_another_days_prices(self):
        with self.assertRaises(ValueError):
            power.summarise(self.DAY, "NO2", day_payload("2026-11-17", [1.0] * 24))

    def test_refuses_an_empty_answer(self):
        with self.assertRaises(ValueError):
            power.summarise(self.DAY, "NO2", [])

    def test_gates(self):
        rows = [{"min_nok": 0.5, "avg_nok": 1.0, "max_nok": 3.0}] * 150
        self.assertTrue(all(g["passed"] for g in power_gates(rows=rows, expected=150, problems=[])))
        short = power_gates(rows=rows[:100], expected=150, problems=[{"problem": "x"}])
        self.assertEqual([g["passed"] for g in short], [False, False, True])


class Files(unittest.TestCase):
    def test_csv_has_a_header_and_quotes_commas(self):
        text = store.to_csv([book(title='Eat, Pray, "Love"')], BOOK_FIELDS)
        lines = text.splitlines()
        self.assertEqual(lines[0], ",".join(BOOK_FIELDS))
        self.assertIn('"Eat, Pray, ""Love"""', lines[1])

    def test_books_round_trip_and_the_page_table_matches(self):
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder)
            self.assertIsNone(store.load_books(out))
            store.save_books(out, [book()])
            self.assertEqual(store.load_books(out), [book()])
            table = json.loads((out / "books.table.json").read_text(encoding="utf-8"))
            self.assertEqual(table["columns"], BOOK_FIELDS)
            self.assertEqual(dict(zip(table["columns"], table["rows"][0])), book())
            raw = (out / "downloads" / "books.csv").read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))  # so Excel reads it correctly

    def test_run_history_keeps_the_newest(self):
        with tempfile.TemporaryDirectory() as folder:
            out = pathlib.Path(folder)
            for number in range(store.KEEP_RUNS + 5):
                runs = store.append_run(out, {"started": str(number)})
            self.assertEqual(len(runs), store.KEEP_RUNS)
            self.assertEqual(runs[-1]["started"], str(store.KEEP_RUNS + 4))
            self.assertEqual(runs[0]["started"], "5")


if __name__ == "__main__":
    unittest.main()
