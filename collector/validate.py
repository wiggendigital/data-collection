"""Checks every collected row before it is published.

A row that breaks a rule is not dropped silently: it goes to the rejected list with the
reason, and the report counts it. Missing optional fields are counted as notes.
"""
from __future__ import annotations

import re

_UPC = re.compile(r"^[0-9a-f]{16}$")
BOOK_FIELDS = ["upc", "title", "category", "price_excl_tax", "price_incl_tax", "tax", "in_stock",
               "stock", "rating", "reviews", "description_chars", "description_start",
               "image_url", "url"]
# Fields a row may lack without being wrong. Each miss is counted and shown.
OPTIONAL = {"description_start": "no description on the product page",
            "image_url": "no product image"}


def book_problems(row: dict) -> list[str]:
    """Returns the rules this row breaks. An empty list means the row is good."""
    found = []
    if not _UPC.match(str(row.get("upc", ""))):
        found.append("UPC is not 16 hexadecimal characters")
    if not str(row.get("title", "")).strip():
        found.append("title is empty")
    if not str(row.get("category", "")).strip():
        found.append("category is empty")
    excl, incl, tax = row.get("price_excl_tax"), row.get("price_incl_tax"), row.get("tax")
    if not all(isinstance(value, (int, float)) for value in (excl, incl, tax)):
        found.append("a price is not a number")
    else:
        if not 0 < incl < 1000:
            found.append("price is outside 0 to 1,000")
        if tax < 0:
            found.append("tax is negative")
        if abs(excl + tax - incl) > 0.011:
            found.append("price without tax plus tax does not equal price with tax")
    if row.get("rating") not in (1, 2, 3, 4, 5):
        found.append("rating is not 1 to 5")
    stock = row.get("stock")
    if not isinstance(stock, int) or stock < 0:
        found.append("stock is not a whole number from 0 up")
    elif bool(row.get("in_stock")) != (stock > 0):
        found.append("the in-stock flag disagrees with the stock count")
    reviews = row.get("reviews")
    if not isinstance(reviews, int) or reviews < 0:
        found.append("review count is not a whole number from 0 up")
    if not str(row.get("url", "")).startswith("https://books.toscrape.com/catalogue/"):
        found.append("the address is outside the shop's catalogue")
    return found


def check_books(rows: list[dict]) -> dict:
    """Splits rows into good and rejected, removes duplicates and counts what is missing."""
    good, rejected, seen = [], [], set()
    duplicates = 0
    for row in rows:
        problems = book_problems(row)
        if problems:
            rejected.append({"url": row.get("url", ""), "title": row.get("title", ""),
                             "problems": problems})
            continue
        if row["upc"] in seen:
            duplicates += 1
            continue
        seen.add(row["upc"])
        good.append(row)
    notes = {}
    for field, meaning in OPTIONAL.items():
        missing = sum(1 for row in good if not row.get(field))
        notes[field] = {"missing": missing, "meaning": meaning}
    good.sort(key=lambda row: (row["category"].lower(), row["title"].lower(), row["upc"]))
    return {"rows": good, "rejected": rejected, "duplicates_removed": duplicates, "notes": notes}


def gate(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def book_gates(*, shop_total: int | None, listed: int, checked: dict, failed_pages: int,
               limited: bool) -> list[dict]:
    """The pass or fail checks that decide whether a run may be published."""
    kept = len(checked["rows"])
    rejected = len(checked["rejected"])
    target = listed if limited else (shop_total or listed)
    coverage = kept / target if target else 0.0
    gates = [
        gate("Every product the shop lists was collected", coverage >= 0.99,
             f"{kept:,} of {target:,} products ({coverage:.1%}); the limit is 99%"),
        gate("No page was given up on", failed_pages == 0,
             f"{failed_pages} pages failed after every retry"),
        gate("Rows that break a rule stay under 1%", rejected <= max(0, int(0.01 * max(target, 1))),
             f"{rejected} rows rejected"),
        gate("No product appears twice", len({r['upc'] for r in checked['rows']}) == kept,
             f"{checked['duplicates_removed']} duplicates removed"),
    ]
    if not limited and shop_total is not None:
        gates.insert(1, gate("The list pages add up to the shop's own count", listed == shop_total,
                             f"{listed:,} listed; the shop says {shop_total:,}"))
    return gates


def power_gates(*, rows: list[dict], expected: int, problems: list[dict]) -> list[dict]:
    coverage = len(rows) / expected if expected else 0.0
    sane = all(-5 < row["min_nok"] <= row["avg_nok"] <= row["max_nok"] < 100 for row in rows)
    return [
        gate("Every zone has every day in the window", coverage >= 0.97,
             f"{len(rows)} of {expected} zone-days ({coverage:.1%}); the limit is 97%"),
        gate("Every day is complete", not problems, f"{len(problems)} days refused as incomplete or unreadable"),
        gate("Prices are in a believable range", sane,
             "lowest, average and highest are in order, and between -5 and 100 NOK per kWh"),
    ]
