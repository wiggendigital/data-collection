"""Runs the collection jobs and publishes what passes.

    python -m collector.run                 both jobs, written to site/data
    python -m collector.run --job books     one job
    python -m collector.run --limit 40      a short trial run (never published as a full run)

A job's files are replaced only when every check passes. If a check fails, yesterday's
files stay in place, the failure is written to status.json and the exit code is 1, so the
scheduler shows a failed run.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import pathlib
import sys
import time

from . import books, power, store
from .diff import book_changes
from .fetch import Fetcher
from .validate import book_gates, check_books, power_gates

ROOT = pathlib.Path(__file__).resolve().parent.parent
USER_AGENT = os.environ.get("COLLECTOR_AGENT", "portfolio-data-sample/1.0")
SHOWN = 20


def oslo_today(now: dt.datetime) -> dt.date:
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("Europe/Oslo")).date()
    except Exception:  # no time zone data on this machine: Norway is at least UTC+1
        return (now + dt.timedelta(hours=1)).date()


def stamp(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def run_books(out: pathlib.Path, limit: int | None, log, transport=None) -> dict:
    began = time.monotonic()
    async with Fetcher(user_agent=USER_AGENT, max_concurrency=4, min_interval=0.1,
                       transport=transport) as fetcher:
        crawl = await books.crawl(fetcher, limit=limit, log=log)
        fetch = fetcher.stats.as_dict()
    checked = check_books(crawl["rows"])
    failed_pages = sum(1 for p in crawl["problems"] if p["stage"] != "cross-check")
    gates = book_gates(shop_total=crawl["shop_total"], listed=crawl["listed"], checked=checked,
                       failed_pages=failed_pages, limited=crawl["limited"])
    passed = all(g["passed"] for g in gates)
    previous = store.load_books(out)
    changes = book_changes(previous, checked["rows"])
    publish = passed and not crawl["limited"]
    if publish:
        store.save_books(out, checked["rows"])
    return {
        "published": publish, "passed": passed, "partial": crawl["limited"],
        "rows": len(checked["rows"]), "shop_total": crawl["shop_total"],
        "list_pages": crawl["list_pages"], "product_pages": crawl["listed"],
        "categories": len({row["category"] for row in checked["rows"]}),
        "gates": gates, "notes": checked["notes"],
        "duplicates_removed": checked["duplicates_removed"],
        "rejected_count": len(checked["rejected"]), "rejected": checked["rejected"][:SHOWN],
        "problem_count": len(crawl["problems"]), "problems": crawl["problems"][:SHOWN],
        "price_mismatches": sum(1 for p in crawl["problems"] if p["stage"] == "cross-check"),
        "fetch": fetch, "changes": changes, "seconds": round(time.monotonic() - began, 1),
    }


async def run_power(out: pathlib.Path, days: int, today: dt.date, log, transport=None) -> dict:
    began = time.monotonic()
    known = store.load_power(out)
    async with Fetcher(user_agent=USER_AGENT, max_concurrency=2, min_interval=0.25,
                       transport=transport) as fetcher:
        result = await power.collect(fetcher, today=today, days=days, known=known, log=log)
        fetch = fetcher.stats.as_dict()
    gates = power_gates(rows=result["rows"], expected=result["expected"], problems=result["problems"])
    passed = all(g["passed"] for g in gates)
    if passed:
        store.save_power(out, result["rows"], result["today_hours"])
    return {
        "published": passed, "passed": passed, "rows": len(result["rows"]),
        "expected": result["expected"], "fetched": result["fetched"],
        "window": result["window"], "zones": power.ZONES, "source": power.SOURCE,
        "gates": gates, "problem_count": len(result["problems"]),
        "problems": result["problems"][:SHOWN], "fetch": fetch,
        "seconds": round(time.monotonic() - began, 1),
    }


async def main_async(args, log=print) -> int:
    out = pathlib.Path(args.out).resolve()
    started = dt.datetime.now(dt.timezone.utc)
    status = store.read_json(out / "status.json", default={}) or {}
    summary = {}
    if args.job in ("all", "books"):
        status["books"] = await run_books(out, args.limit, log)
        b = status["books"]
        summary["books"] = {"rows": b["rows"], "requests": b["fetch"]["requests"],
                            "retries": b["fetch"]["retries"], "failed": b["fetch"]["failed"],
                            "changes": b["changes"]["new"] + b["changes"]["removed"]
                                       + b["changes"]["price"] + b["changes"]["stock"],
                            "seconds": b["seconds"], "passed": b["passed"]}
    if args.job in ("all", "power"):
        status["power"] = await run_power(out, args.days, oslo_today(started), log)
        p = status["power"]
        summary["power"] = {"rows": p["rows"], "requests": p["fetch"]["requests"],
                            "retries": p["fetch"]["retries"], "failed": p["fetch"]["failed"],
                            "fetched": p["fetched"], "seconds": p["seconds"], "passed": p["passed"]}
    finished = dt.datetime.now(dt.timezone.utc)
    ok = all(job["passed"] for job in summary.values())
    partial = bool(args.limit) or args.job != "all"
    status["run"] = {"started": stamp(started), "finished": stamp(finished),
                     "seconds": round((finished - started).total_seconds(), 1),
                     "ok": ok, "partial": partial, "agent": USER_AGENT}
    store.write_json(out / "status.json", status)
    if not partial:
        store.append_run(out, {"started": stamp(started),
                               "seconds": status["run"]["seconds"], "ok": ok, **summary})
    for name, job in summary.items():
        log(f"{name}: {'passed' if job['passed'] else 'FAILED'}, {job['rows']} rows, "
            f"{job['requests']} requests, {job['seconds']} s")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--job", choices=["all", "books", "power"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="collect only this many products")
    parser.add_argument("--days", type=int, default=30, help="days of prices to keep")
    parser.add_argument("--out", default=str(ROOT / "site" / "data"))
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
