"""A polite HTTP client.

One Fetcher is shared by a whole job. It:

- identifies itself with one User-Agent string,
- reads robots.txt once per host and refuses URLs it disallows,
- spaces requests out (min_interval) and caps how many run at once,
- retries timeouts, 429 and 5xx answers with a growing pause, and obeys Retry-After,
- counts everything it does, so the run report can show it.
"""
from __future__ import annotations

import asyncio
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

RETRY_STATUSES = {429, 500, 502, 503, 504}


class Disallowed(Exception):
    """robots.txt does not allow this URL for our user agent."""


class FetchFailed(Exception):
    """The URL could not be fetched after every retry."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


@dataclass
class Stats:
    requests: int = 0          # every attempt that went on the wire
    ok: int = 0                # URLs that ended with a 200
    retries: int = 0           # extra attempts after a first failure
    failed: int = 0            # URLs given up on
    bytes: int = 0
    slowest_ms: int = 0
    by_status: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"requests": self.requests, "ok": self.ok, "retries": self.retries,
                "failed": self.failed, "bytes": self.bytes, "slowest_ms": self.slowest_ms,
                "by_status": dict(sorted(self.by_status.items()))}


class Fetcher:
    def __init__(self, *, user_agent: str, max_concurrency: int = 4, min_interval: float = 0.12,
                 retries: int = 3, timeout: float = 20.0, backoff: float = 1.0,
                 transport: httpx.AsyncBaseTransport | None = None,
                 sleep=asyncio.sleep, clock=time.monotonic):
        self.user_agent = user_agent
        self.retries = retries
        self.backoff = backoff
        self.min_interval = min_interval
        self.stats = Stats()
        self._sleep = sleep
        self._clock = clock
        self._gate = asyncio.Semaphore(max_concurrency)
        self._pace = asyncio.Lock()
        self._next_slot = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._robots_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept-Language": "en"},
            timeout=timeout, follow_redirects=True, transport=transport)

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    # -- robots.txt ---------------------------------------------------------

    async def _robots_for(self, url: str):
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        async with self._robots_lock:
            if host not in self._robots:
                parser = None
                try:
                    answer = await self._client.get(host + "/robots.txt")
                    self.stats.requests += 1
                    self._count(answer.status_code)
                    if answer.status_code == 200:
                        parser = urllib.robotparser.RobotFileParser()
                        parser.parse(answer.text.splitlines())
                    elif answer.status_code in (401, 403):
                        # The convention: a forbidden robots.txt means "stay out".
                        parser = urllib.robotparser.RobotFileParser()
                        parser.parse(["User-agent: *", "Disallow: /"])
                    # 404 and other answers: no rules published, nothing is disallowed.
                except httpx.HTTPError:
                    parser = None
                self._robots[host] = parser
            return self._robots[host]

    async def allowed(self, url: str) -> bool:
        parser = await self._robots_for(url)
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    # -- fetching -----------------------------------------------------------

    def _count(self, status: int) -> None:
        key = str(status)
        self.stats.by_status[key] = self.stats.by_status.get(key, 0) + 1

    async def _wait_for_slot(self) -> None:
        async with self._pace:
            now = self._clock()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self.min_interval
        if wait > 0:
            await self._sleep(wait)

    async def get(self, url: str) -> bytes:
        """Returns the body of a 200 answer, or raises Disallowed / FetchFailed."""
        if not await self.allowed(url):
            raise Disallowed(url)
        reason = "no attempt made"
        for attempt in range(self.retries + 1):
            if attempt:
                self.stats.retries += 1
            pause = self.backoff * (2 ** attempt)
            async with self._gate:
                await self._wait_for_slot()
                started = self._clock()
                try:
                    answer = await self._client.get(url)
                except httpx.HTTPError as error:
                    self.stats.requests += 1
                    self._count(0)
                    reason = type(error).__name__
                else:
                    self.stats.requests += 1
                    self._count(answer.status_code)
                    took = int((self._clock() - started) * 1000)
                    self.stats.slowest_ms = max(self.stats.slowest_ms, took)
                    if answer.status_code == 200:
                        self.stats.ok += 1
                        self.stats.bytes += len(answer.content)
                        return answer.content
                    reason = f"HTTP {answer.status_code}"
                    if answer.status_code not in RETRY_STATUSES:
                        break
                    retry_after = answer.headers.get("retry-after", "")
                    if retry_after.isdigit():
                        pause = max(pause, min(int(retry_after), 60))
            if attempt < self.retries:
                await self._sleep(pause)
        self.stats.failed += 1
        raise FetchFailed(url, reason)
