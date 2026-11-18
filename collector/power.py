"""Job 2: Norwegian electricity spot prices, by price zone and day.

The publisher, hvakosterstrommen.no, offers an open and free API and asks for a link back.
When a site offers its data like that, the API is the right way to collect it: it is what
the publisher wants, and it does not break when the page design changes.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json

from .fetch import Disallowed, FetchFailed, Fetcher

ZONES = {"NO1": "Oslo", "NO2": "Kristiansand", "NO3": "Trondheim", "NO4": "Tromsø", "NO5": "Bergen"}
API = "https://www.hvakosterstrommen.no/api/v1/prices/{year}/{month:02d}-{day:02d}_{zone}.json"
SOURCE = {"name": "hvakosterstrommen.no", "url": "https://www.hvakosterstrommen.no"}


def url_for(day: dt.date, zone: str) -> str:
    return API.format(year=day.year, month=day.month, day=day.day, zone=zone)


def summarise(day: dt.date, zone: str, payload: list) -> dict:
    """Turns one day's price points into one row. Raises ValueError on a payload we cannot trust."""
    if not isinstance(payload, list) or not payload:
        raise ValueError("empty answer")
    points = []
    for entry in payload:
        price = entry.get("NOK_per_kWh")
        start = entry.get("time_start", "")
        if not isinstance(price, (int, float)) or not start.startswith(day.isoformat()):
            raise ValueError("a price point is missing its price or belongs to another day")
        points.append((start, float(price)))
    points.sort()
    # A normal day has 24 hourly points (23 or 25 when the clocks change), or 96 when the
    # market publishes quarter-hours. Anything else is not a complete day.
    if len(points) not in (23, 24, 25, 92, 96, 100):
        raise ValueError(f"{len(points)} price points is not a complete day")
    prices = [price for _, price in points]
    low = min(points, key=lambda p: p[1])
    high = max(points, key=lambda p: p[1])
    return {
        "date": day.isoformat(), "zone": zone, "points": len(points),
        "avg_nok": round(sum(prices) / len(prices), 4),
        "min_nok": round(low[1], 4), "min_at": low[0][11:16],
        "max_nok": round(high[1], 4), "max_at": high[0][11:16],
    }


def hourly(payload: list) -> list[dict]:
    return [{"time": entry["time_start"][11:16], "nok": round(float(entry["NOK_per_kWh"]), 4)}
            for entry in sorted(payload, key=lambda e: e["time_start"])]


async def collect(fetcher: Fetcher, *, today: dt.date, days: int = 30,
                  known: dict | None = None, log=print) -> dict:
    """Fetches every day in the window that is not stored yet, for all five zones.

    `known` maps "date|zone" to rows from earlier runs. Finished days never change, so they
    are not fetched again; today is always fetched.
    """
    known = dict(known or {})
    window = [today - dt.timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    problems: list[dict] = []
    today_hours: dict[str, list] = {}
    fetched = 0

    async def one(day: dt.date, zone: str):
        nonlocal fetched
        url = url_for(day, zone)
        try:
            payload = json.loads(await fetcher.get(url))
            row = summarise(day, zone, payload)
        except (FetchFailed, Disallowed, ValueError) as error:
            problems.append({"stage": "price day", "url": url, "problem": str(error)})
            return
        fetched += 1
        known[f"{day.isoformat()}|{zone}"] = row
        if day == today:
            today_hours[zone] = hourly(payload)

    wanted = [(day, zone) for day in window for zone in ZONES
              if day == today or f"{day.isoformat()}|{zone}" not in known]
    await asyncio.gather(*(one(day, zone) for day, zone in wanted))
    first = window[0].isoformat()
    rows = sorted((row for row in known.values() if row["date"] >= first),
                  key=lambda r: (r["date"], r["zone"]))
    log(f"power: {fetched} zone-days fetched, {len(rows)} rows in the window, {len(problems)} problems")
    return {"rows": rows, "today_hours": today_hours, "fetched": fetched,
            "expected": len(window) * len(ZONES), "problems": problems,
            "window": [window[0].isoformat(), window[-1].isoformat()]}
