"""Compares this run with the one before it."""
from __future__ import annotations

SHOWN = 40  # how many changed rows of each kind are kept for the page


def book_changes(previous: list[dict] | None, current: list[dict]) -> dict:
    """What appeared, disappeared or changed since the last run. None means there was no last run."""
    if previous is None:
        return {"first_run": True, "new": 0, "removed": 0, "price": 0, "stock": 0, "examples": []}
    before = {row["upc"]: row for row in previous}
    now = {row["upc"]: row for row in current}
    examples = []
    new = [upc for upc in now if upc not in before]
    removed = [upc for upc in before if upc not in now]
    price = stock = 0
    for upc in new:
        examples.append({"kind": "new", "title": now[upc]["title"], "to": now[upc]["price_incl_tax"]})
    for upc in removed:
        examples.append({"kind": "removed", "title": before[upc]["title"],
                         "from": before[upc]["price_incl_tax"]})
    for upc, row in now.items():
        old = before.get(upc)
        if old is None:
            continue
        if abs(old["price_incl_tax"] - row["price_incl_tax"]) > 0.005:
            price += 1
            examples.append({"kind": "price", "title": row["title"],
                             "from": old["price_incl_tax"], "to": row["price_incl_tax"]})
        if old["stock"] != row["stock"]:
            stock += 1
            examples.append({"kind": "stock", "title": row["title"],
                             "from": old["stock"], "to": row["stock"]})
    return {"first_run": False, "new": len(new), "removed": len(removed), "price": price,
            "stock": stock, "examples": examples[:SHOWN]}
