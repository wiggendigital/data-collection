"""Job 1: the full catalogue of books.toscrape.com.

The site is a public practice shop made for this purpose. The job walks every list page,
then opens every product page, because seven of the fields exist only there.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .fetch import Disallowed, FetchFailed, Fetcher

START_URL = "https://books.toscrape.com/catalogue/page-1.html"
RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
_MONEY = re.compile(r"(\d+(?:\.\d+)?)")
_STOCK = re.compile(r"\((\d+) available\)")


class ParseError(ValueError):
    """The page does not look the way the parser expects."""


def decode(body: bytes) -> str:
    """The shop sends no charset header and its pages are UTF-8.

    Decoding by guesswork turns the pound sign into two wrong characters, so the
    encoding is stated here and a test checks the sign.
    """
    return body.decode("utf-8")


def money(text: str) -> float:
    found = _MONEY.search(text.replace(",", ""))
    if not found:
        raise ParseError(f"no amount in {text!r}")
    return float(found.group(1))


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def parse_listing(html: str, page_url: str) -> dict:
    """One list page: its products, the link to the next page and the shop's own total."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for pod in soup.select("article.product_pod"):
        link = pod.select_one("h3 a")
        if link is None or not link.get("href"):
            raise ParseError(f"a product without a link on {page_url}")
        rating_class = [c for c in pod.select_one("p.star-rating").get("class", []) if c in RATINGS]
        items.append({
            "url": urljoin(page_url, link["href"]),
            "title": link.get("title") or _text(link),
            "price": money(_text(pod.select_one(".price_color"))),
            "in_stock": "in stock" in _text(pod.select_one(".availability")).lower(),
            "rating": RATINGS[rating_class[0]] if rating_class else None,
        })
    next_link = soup.select_one("li.next a")
    total = soup.select_one("form.form-horizontal strong")
    return {
        "items": items,
        "next_url": urljoin(page_url, next_link["href"]) if next_link else None,
        "shop_total": int(_text(total)) if total and _text(total).isdigit() else None,
    }


def parse_detail(html: str, page_url: str) -> dict:
    """One product page: everything the shop publishes about the product."""
    soup = BeautifulSoup(html, "html.parser")
    table = {_text(row.th): _text(row.td) for row in soup.select("table.table-striped tr")}
    if "UPC" not in table:
        raise ParseError(f"no product table on {page_url}")
    crumbs = soup.select("ul.breadcrumb li")
    main = soup.select_one(".product_main")
    rating_node = main.select_one("p.star-rating") if main else None
    rating_class = [c for c in (rating_node.get("class", []) if rating_node else []) if c in RATINGS]
    stock = _STOCK.search(table.get("Availability", ""))
    heading = soup.select_one("#product_description")
    paragraph = heading.find_next_sibling("p") if heading else None
    description = _text(paragraph)
    image = soup.select_one("#product_gallery img")
    return {
        "upc": table["UPC"],
        "title": _text(main.select_one("h1")) if main else "",
        "category": _text(crumbs[2]) if len(crumbs) >= 4 else "",
        "price_excl_tax": money(table.get("Price (excl. tax)", "")),
        "price_incl_tax": money(table.get("Price (incl. tax)", "")),
        "tax": money(table.get("Tax", "")),
        "in_stock": "in stock" in table.get("Availability", "").lower(),
        "stock": int(stock.group(1)) if stock else 0,
        "rating": RATINGS[rating_class[0]] if rating_class else None,
        "reviews": int(table["Number of reviews"]) if table.get("Number of reviews", "").isdigit() else None,
        "description_chars": len(description),
        "description_start": description[:160],
        "image_url": urljoin(page_url, image["src"]) if image and image.get("src") else "",
        "url": page_url,
    }


async def crawl(fetcher: Fetcher, *, start_url: str = START_URL, limit: int | None = None,
                log=print) -> dict:
    """Walks the whole shop. Returns rows plus everything that went wrong on the way."""
    listed: list[dict] = []
    problems: list[dict] = []
    shop_total = None
    list_pages = 0
    url = start_url
    seen_pages = set()
    while url and url not in seen_pages:
        seen_pages.add(url)
        try:
            page = parse_listing(decode(await fetcher.get(url)), url)
        except (FetchFailed, Disallowed, ParseError) as error:
            problems.append({"stage": "list page", "url": url, "problem": str(error)})
            break
        list_pages += 1
        shop_total = page["shop_total"] or shop_total
        listed.extend(page["items"])
        url = page["next_url"]
        if limit and len(listed) >= limit:
            listed = listed[:limit]
            break
    log(f"books: {list_pages} list pages, {len(listed)} products listed")

    async def one(item: dict):
        try:
            row = parse_detail(decode(await fetcher.get(item["url"])), item["url"])
        except (FetchFailed, Disallowed, ParseError) as error:
            problems.append({"stage": "product page", "url": item["url"], "problem": str(error)})
            return None
        # The list page and the product page must agree; a mismatch is a finding.
        if abs(row["price_incl_tax"] - item["price"]) > 0.005:
            problems.append({"stage": "cross-check", "url": item["url"],
                             "problem": f"list price {item['price']} differs from page price "
                                        f"{row['price_incl_tax']}"})
        return row

    rows = [row for row in await asyncio.gather(*(one(item) for item in listed)) if row]
    log(f"books: {len(rows)} product pages read, {len(problems)} problems")
    return {"rows": rows, "listed": len(listed), "list_pages": list_pages,
            "shop_total": shop_total, "problems": problems, "limited": bool(limit)}
