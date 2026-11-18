"""Whole runs against a pretend shop: what gets published, and what does not."""
import datetime as dt
import json
import pathlib
import tempfile
import unittest

import httpx

from collector import run, store

SHOP = "https://books.toscrape.com/catalogue/"


def listing(numbers, next_page, total):
    pods = "".join(
        f'<article class="product_pod"><p class="star-rating Four"></p>'
        f'<h3><a href="book_{n}/index.html" title="Book {n}">Book {n}</a></h3>'
        f'<p class="price_color">£{10 + n}.50</p><p class="instock availability">In stock</p></article>'
        for n in numbers)
    pager = f'<li class="next"><a href="page-{next_page}.html">next</a></li>' if next_page else ""
    return (f'<form class="form-horizontal"><strong>{total}</strong> results</form>{pods}'
            f'<ul class="pager">{pager}</ul>').encode("utf-8")


def detail(n, price=None):
    price = price if price is not None else f"{10 + n}.50"
    return f"""<ul class="breadcrumb"><li>Home</li><li>Books</li><li>Travel</li><li>Book {n}</li></ul>
<div class="product_main"><h1>Book {n}</h1><p class="star-rating Four"></p></div>
<div id="product_gallery"><img src="../../media/{n}.jpg"></div>
<div id="product_description"><h2>Product Description</h2></div><p>About book {n}.</p>
<table class="table table-striped"><tr><th>UPC</th><td>{n:016x}</td></tr>
<tr><th>Price (excl. tax)</th><td>£{price}</td></tr><tr><th>Price (incl. tax)</th><td>£{price}</td></tr>
<tr><th>Tax</th><td>£0.00</td></tr><tr><th>Availability</th><td>In stock ({n} available)</td></tr>
<tr><th>Number of reviews</th><td>0</td></tr></table>""".encode("utf-8")


def shop(*, broken=(), prices=None, per_page=20, total=60):
    """A shop with `total` books. Pages for numbers in `broken` always answer 500."""
    prices = prices or {}

    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if "/page-" in path:
            page = int(path.rsplit("page-", 1)[1].split(".")[0])
            first = (page - 1) * per_page + 1
            numbers = range(first, min(first + per_page, total + 1))
            more = page + 1 if first + per_page <= total else None
            return httpx.Response(200, content=listing(numbers, more, total))
        number = int(path.split("book_")[1].split("/")[0])
        if number in broken:
            return httpx.Response(500)
        return httpx.Response(200, content=detail(number, prices.get(number)))

    return httpx.MockTransport(handler)


async def no_sleep(_seconds):
    return None


class WholeRuns(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self._folder.name)
        self.addCleanup(self._folder.cleanup)
        # Retries would otherwise wait for real seconds.
        import collector.fetch as fetch_module
        self._real_sleep = fetch_module.asyncio.sleep
        fetch_module.asyncio.sleep = no_sleep
        self.addCleanup(setattr, fetch_module.asyncio, "sleep", self._real_sleep)

    async def books(self, transport):
        from collector.fetch import Fetcher
        original = Fetcher.__init__

        def quick(fetcher, **options):
            options.update(sleep=no_sleep, min_interval=0)
            original(fetcher, **options)

        Fetcher.__init__ = quick
        try:
            return await run.run_books(self.out, None, lambda *_: None, transport=transport)
        finally:
            Fetcher.__init__ = original

    async def test_a_clean_run_is_published(self):
        result = await self.books(shop())
        self.assertTrue(result["published"])
        self.assertEqual(result["rows"], 60)
        self.assertEqual(result["list_pages"], 3)
        self.assertTrue(result["changes"]["first_run"])
        self.assertEqual(len(store.load_books(self.out)), 60)
        self.assertTrue((self.out / "downloads" / "books.csv").exists())

    async def test_a_failed_run_leaves_yesterdays_files_alone(self):
        await self.books(shop())
        before = (self.out / "downloads" / "books.json").read_bytes()
        result = await self.books(shop(broken=set(range(1, 11))))
        self.assertFalse(result["published"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["rows"], 50)
        self.assertEqual(result["problem_count"], 10)
        self.assertEqual(result["fetch"]["failed"], 10)
        self.assertEqual((self.out / "downloads" / "books.json").read_bytes(), before)

    async def test_the_next_run_reports_what_changed(self):
        await self.books(shop())
        result = await self.books(shop(prices={7: "99.00", 8: "1.00"}, total=61))
        self.assertTrue(result["published"])
        changes = result["changes"]
        self.assertEqual((changes["new"], changes["removed"], changes["price"]), (1, 0, 2))

    async def test_a_price_that_differs_between_list_and_page_is_reported(self):
        # The list page says 17.50 for book 7; the product page says 99.00.
        result = await self.books(shop(prices={7: "99.00"}))
        cross = [p for p in result["problems"] if p["stage"] == "cross-check"]
        self.assertEqual(len(cross), 1)
        self.assertIn("book_7", cross[0]["url"])
        self.assertTrue(result["published"])  # a finding, not a failure


class PowerRun(unittest.IsolatedAsyncioTestCase):
    async def test_known_days_are_not_fetched_again(self):
        today = dt.date(2026, 11, 18)
        asked = []

        def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            asked.append(request.url.path)
            day = request.url.path.split("/prices/")[1][:10].replace("/", "-")
            points = [{"NOK_per_kWh": 1.0 + hour / 100,
                       "time_start": f"{day}T{hour:02d}:00:00+01:00"} for hour in range(24)]
            return httpx.Response(200, content=json.dumps(points).encode())

        from collector.fetch import Fetcher
        original = Fetcher.__init__

        def quick(fetcher, **options):
            options.update(sleep=no_sleep, min_interval=0)
            original(fetcher, **options)

        Fetcher.__init__ = quick
        try:
            with tempfile.TemporaryDirectory() as folder:
                out = pathlib.Path(folder)
                transport = httpx.MockTransport(handler)
                first = await run.run_power(out, 5, today, lambda *_: None, transport=transport)
                self.assertTrue(first["published"])
                self.assertEqual(first["rows"], 25)      # 5 days, 5 zones
                self.assertEqual(len(asked), 25)
                asked.clear()
                second = await run.run_power(out, 5, today, lambda *_: None, transport=transport)
                self.assertEqual(len(asked), 5)          # only today, once per zone
                self.assertEqual(second["rows"], 25)
                hours = json.loads((out / "power.today.json").read_text(encoding="utf-8"))
                self.assertEqual(sorted(hours), ["NO1", "NO2", "NO3", "NO4", "NO5"])
                self.assertEqual(len(hours["NO2"]), 24)
        finally:
            Fetcher.__init__ = original


if __name__ == "__main__":
    unittest.main()
