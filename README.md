# Daily data collection

Two collection jobs that run every morning, check their own output and publish it as a web page with files to download.

A sample project by Wiggen Digital.

## What it collects

| Job | Source | What comes out |
| --- | --- | --- |
| **Catalogue** | [books.toscrape.com](https://books.toscrape.com/), a public practice shop made for this purpose | All 1,000 products with 14 fields each, from 50 list pages and 1,000 product pages |
| **Prices** | The open API of [hvakosterstrommen.no](https://www.hvakosterstrommen.no) | Electricity spot prices for Norway's five price zones, one row per zone and day, for the last 30 days |

The shop's owner assigns its prices and ratings at random, so the catalogue is a test of the method and not a picture of a book market.

The price job uses an API and not the web pages. The publisher offers the API freely and asks for a link back. When a site offers its data that way, that is the right way to collect it.

## Run it

It needs Python 3.11 or newer.

```
python -m venv .venv
.venv\Scripts\activate          (on macOS and Linux: source .venv/bin/activate)
pip install -r requirements.txt

python -m unittest              51 tests, about two seconds, no network
python -m collector.run         both jobs; about two and a half minutes
```

Other ways to run it:

```
python -m collector.run --job books       one job only
python -m collector.run --limit 40        a short trial with 40 products
python -m collector.run --out some/folder write the files somewhere else
```

A trial run with `--limit`, or a run of one job, updates `status.json` but adds no line to the run history.

To look at the page, serve the `site` folder: `python -m http.server --directory site`.

## What it writes

Everything goes into `site/data/`.

| File | What it is |
| --- | --- |
| `downloads/books.csv`, `downloads/books.json` | The catalogue. The CSV opens in Excel with the right characters. |
| `downloads/power.csv`, `downloads/power.json` | One row per zone and day. |
| `books.table.json` | The same catalogue in a compact form the page loads. |
| `power.today.json` | Today's prices through the day, per zone. |
| `status.json` | The last run in full: every check, what was missing, what changed, request counts. |
| `runs.json` | One line per full run, newest last. The last 120 are kept. |

## How it behaves

- **It says who it is.** Every request carries one User-Agent string. Set it with the `COLLECTOR_AGENT` environment variable.
- **It reads the rules first.** It fetches `robots.txt` once per site and never requests an address the file disallows. No `robots.txt` means nothing is disallowed; a forbidden one means stay out.
- **It takes its time.** The catalogue job runs at most four requests at once, a tenth of a second apart. The price job runs two at once, a quarter of a second apart.
- **It retries with patience.** A timeout, a 429 or a 5xx answer gets three more tries. The pause doubles each time, and a `Retry-After` header is obeyed up to 60 seconds. A 404 is not retried.
- **It reads each price twice.** The list page and the product page must show the same price. A mismatch is reported.
- **It checks every row.** Twelve rules per product. A row that breaks one is set aside with the reason and counted. Rows are never dropped silently.
- **It does not fetch what it has.** A finished day of electricity prices does not change, so it is fetched once.

## When a run may be published

A job's files are replaced only if every check passes.

Catalogue:

1. At least 99% of the products the shop lists were collected.
2. The list pages add up to the shop's own count.
3. No page was given up on.
4. Fewer than 1% of the rows broke a rule.
5. No product appears twice.

Prices:

1. At least 97% of the zone-days in the window are present.
2. Every day is complete (24 hourly points, 23 or 25 when the clocks change, or 96 quarter-hours).
3. The lowest, average and highest price of each day are in order and inside a believable range.

If a check fails, yesterday's files stay in place, `status.json` and `runs.json` record the failure, and the command exits with code 1 so the scheduler shows a failed run.

## The schedule

`.github/workflows/daily.yml` runs the tests and both jobs every morning at 05:17 UTC on GitHub Actions, commits the new data files and rebuilds the page. It can also be started by hand from the Actions tab.

## How it is put together

```
collector/fetch.py      The HTTP client: pacing, retries, robots.txt, counters
collector/books.py      The catalogue job and its two parsers
collector/power.py      The price job
collector/validate.py   The row rules and the pass-or-fail checks
collector/diff.py       What changed since the last run
collector/store.py      Reading and writing the files
collector/run.py        The command that ties it together

site/index.html, style.css, app.js   The page. No build step and no libraries.
tests/                  51 tests; tests/fixtures holds four pages saved from the shop
```

## Tests

```
python -m unittest
```

51 tests. They cover both parsers against saved pages, the pound sign surviving the download, the HTTP client against a pretend server (retries, the growing pause, `Retry-After`, timeouts, pacing, `robots.txt`), every row rule, the checks, the comparison between runs, the price summary, the files, and whole runs against a pretend shop: a clean run, a run where ten pages fail and nothing is published, and a run that finds price changes.

No test touches the network. Tested with Python 3.13 on Windows 11.

## Known limits

- **One shop, one layout.** The parsers are written for this shop. Another site needs its own parsers; the client, the rules engine and the publishing step can be reused.
- **No JavaScript rendering.** Both sources answer with complete HTML or JSON. A site that builds its pages in the browser needs a headless browser, which this sample does not include.
- **No proxy rotation or login.** The sample collects only what an anonymous visitor may read, at a gentle pace.
- **The shop never changes.** Its catalogue is fixed, so the daily comparison reports no changes. The comparison itself is tested against a pretend shop that does change.
- **Today's prices only after they are published.** The market publishes the next day's prices in the early afternoon. The morning run therefore always has today's.
- **Run history is short at first.** The history starts on the day the schedule was switched on.

## What I do not collect

Personal data about private people, anything behind a login without the account owner's permission, and sites whose terms or `robots.txt` forbid it.
