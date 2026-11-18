"""The HTTP client, against a pretend server. No test here touches the network."""
import unittest

import httpx

from collector.fetch import Disallowed, FetchFailed, Fetcher


class Clock:
    """A clock that only moves when the client sleeps, so tests take no real time."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


def make(handler, **options):
    clock = Clock()
    fetcher = Fetcher(user_agent="test-agent/1.0", transport=httpx.MockTransport(handler),
                      sleep=clock.sleep, clock=clock.time, **options)
    return fetcher, clock


class Fetching(unittest.IsolatedAsyncioTestCase):
    async def test_returns_the_body_and_counts_it(self):
        def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content=b"hello")

        fetcher, _ = make(handler)
        async with fetcher:
            self.assertEqual(await fetcher.get("https://shop.test/a"), b"hello")
        self.assertEqual(fetcher.stats.ok, 1)
        self.assertEqual(fetcher.stats.bytes, 5)
        self.assertEqual(fetcher.stats.failed, 0)

    async def test_sends_its_user_agent(self):
        seen = []

        def handler(request):
            seen.append(request.headers["user-agent"])
            return httpx.Response(404 if request.url.path == "/robots.txt" else 200)

        fetcher, _ = make(handler)
        async with fetcher:
            await fetcher.get("https://shop.test/a")
        self.assertEqual(set(seen), {"test-agent/1.0"})

    async def test_retries_a_server_error_then_succeeds(self):
        answers = iter([503, 503, 200])

        def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(next(answers), content=b"ok")

        fetcher, clock = make(handler, backoff=1.0, min_interval=0)
        async with fetcher:
            self.assertEqual(await fetcher.get("https://shop.test/a"), b"ok")
        self.assertEqual(fetcher.stats.retries, 2)
        self.assertEqual(clock.sleeps, [1.0, 2.0])  # the pause doubles

    async def test_obeys_retry_after(self):
        answers = iter([(429, {"retry-after": "7"}), (200, {})])

        def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            status, headers = next(answers)
            return httpx.Response(status, headers=headers)

        fetcher, clock = make(handler, backoff=1.0, min_interval=0)
        async with fetcher:
            await fetcher.get("https://shop.test/a")
        self.assertEqual(clock.sleeps, [7.0])

    async def test_gives_up_after_the_last_retry(self):
        def handler(request):
            return httpx.Response(404 if request.url.path == "/robots.txt" else 500)

        fetcher, _ = make(handler, retries=2, min_interval=0)
        async with fetcher:
            with self.assertRaises(FetchFailed) as caught:
                await fetcher.get("https://shop.test/a")
        self.assertEqual(caught.exception.reason, "HTTP 500")
        self.assertEqual(fetcher.stats.failed, 1)
        self.assertEqual(fetcher.stats.by_status["500"], 3)

    async def test_does_not_retry_a_missing_page(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            return httpx.Response(404)

        fetcher, _ = make(handler, min_interval=0)
        async with fetcher:
            with self.assertRaises(FetchFailed):
                await fetcher.get("https://shop.test/gone")
        self.assertEqual(calls.count("/gone"), 1)

    async def test_retries_a_timeout(self):
        attempts = []

        def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(200, content=b"late")

        fetcher, _ = make(handler, min_interval=0)
        async with fetcher:
            self.assertEqual(await fetcher.get("https://shop.test/a"), b"late")
        self.assertEqual(fetcher.stats.retries, 1)

    async def test_spaces_requests_out(self):
        def handler(request):
            return httpx.Response(404 if request.url.path == "/robots.txt" else 200)

        fetcher, clock = make(handler, min_interval=0.5)
        async with fetcher:
            for name in "abc":
                await fetcher.get(f"https://shop.test/{name}")
        self.assertEqual(clock.sleeps, [0.5, 0.5])  # none before the first, then one per request


class Robots(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_what_robots_txt_disallows(self):
        calls = []

        def handler(request):
            calls.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
            return httpx.Response(200)

        fetcher, _ = make(handler, min_interval=0)
        async with fetcher:
            with self.assertRaises(Disallowed):
                await fetcher.get("https://shop.test/private/list")
            await fetcher.get("https://shop.test/public")
        self.assertNotIn("/private/list", calls)       # never requested
        self.assertEqual(calls.count("/robots.txt"), 1)  # read once per host

    async def test_no_robots_txt_means_nothing_is_disallowed(self):
        def handler(request):
            return httpx.Response(404 if request.url.path == "/robots.txt" else 200)

        fetcher, _ = make(handler, min_interval=0)
        async with fetcher:
            self.assertTrue(await fetcher.allowed("https://shop.test/anything"))

    async def test_a_forbidden_robots_txt_means_stay_out(self):
        def handler(request):
            return httpx.Response(403)

        fetcher, _ = make(handler, min_interval=0)
        async with fetcher:
            self.assertFalse(await fetcher.allowed("https://shop.test/anything"))


if __name__ == "__main__":
    unittest.main()
