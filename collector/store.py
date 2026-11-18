"""Reads and writes the published files."""
from __future__ import annotations

import csv
import io
import json
import pathlib

from .validate import BOOK_FIELDS

KEEP_RUNS = 120


def read_json(path: pathlib.Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: pathlib.Path, value, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":") if compact else None,
                      indent=None if compact else 2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)  # a reader never sees a half-written file


def to_csv(rows: list[dict], fields: list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def write_csv(path: pathlib.Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel opens the file with the right characters without being told.
    path.write_text(to_csv(rows, fields), encoding="utf-8-sig", newline="")


def load_books(out: pathlib.Path) -> list[dict] | None:
    return read_json(out / "downloads" / "books.json")


def save_books(out: pathlib.Path, rows: list[dict]) -> None:
    write_json(out / "downloads" / "books.json", rows)
    write_csv(out / "downloads" / "books.csv", rows, BOOK_FIELDS)
    # The page loads a compact table: one list of column names, then one list per row.
    write_json(out / "books.table.json",
               {"columns": BOOK_FIELDS, "rows": [[row[f] for f in BOOK_FIELDS] for row in rows]},
               compact=True)


POWER_FIELDS = ["date", "zone", "points", "avg_nok", "min_nok", "min_at", "max_nok", "max_at"]


def load_power(out: pathlib.Path) -> dict:
    rows = read_json(out / "downloads" / "power.json", default=[]) or []
    return {f"{row['date']}|{row['zone']}": row for row in rows}


def save_power(out: pathlib.Path, rows: list[dict], today_hours: dict) -> None:
    write_json(out / "downloads" / "power.json", rows)
    write_csv(out / "downloads" / "power.csv", rows, POWER_FIELDS)
    write_json(out / "power.today.json", today_hours, compact=True)


def append_run(out: pathlib.Path, run: dict) -> list[dict]:
    runs = read_json(out / "runs.json", default=[]) or []
    runs.append(run)
    runs = runs[-KEEP_RUNS:]
    write_json(out / "runs.json", runs, compact=True)
    return runs
