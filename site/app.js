"use strict";
/* Draws the page from the files the collector writes into data/. No libraries. */

const $ = (selector, root = document) => root.querySelector(selector);
const NS = "http://www.w3.org/2000/svg";
const whole = new Intl.NumberFormat("en-GB");
const pounds = (value) => "£" + value.toFixed(2);
const kr = (value) => value.toFixed(2);
const ZONE_DASH = { NO1: "", NO2: "9 5", NO3: "2 5", NO4: "12 4 2 4", NO5: "5 3" };
const PAGE_SIZE = 25;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "text") node.textContent = value;
    else if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

async function load(name) {
  const answer = await fetch("data/" + name, { cache: "no-cache" });
  if (!answer.ok) throw new Error(name + " answered " + answer.status);
  return answer.json();
}

function norwayTime(iso) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Oslo", day: "numeric", month: "long", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  }).format(new Date(iso));
}

function shortDay(isoDate) {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" })
    .format(new Date(isoDate + "T00:00:00Z"));
}

function duration(seconds) {
  const s = Math.round(seconds);
  return s < 60 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s";
}

function megabytes(bytes) {
  return (bytes / 1048576).toFixed(1) + " MB";
}

/* ---- the status card ---------------------------------------------------- */

function renderStatus(status) {
  const gates = [...status.books.gates, ...status.power.gates];
  const failed = gates.filter((gate) => !gate.passed).length;
  const pill = $("#status-pill");
  pill.textContent = failed === 0
    ? "All " + gates.length + " checks passed"
    : failed + " of " + gates.length + " checks failed, so nothing new was published";
  pill.classList.toggle("bad", failed > 0);
  $("#status-when").textContent = "Last run " + norwayTime(status.run.started) +
    ", Norway time. It took " + duration(status.run.seconds) + ".";
  const tiles = [
    [whole.format(status.books.rows), "products collected"],
    [whole.format(status.books.fetch.requests + status.power.fetch.requests), "requests made"],
    [whole.format(status.books.fetch.failed + status.power.fetch.failed), "pages given up on"],
    [whole.format(status.power.rows), "price days kept"],
  ];
  $("#tiles").replaceChildren(...tiles.map(([value, label]) =>
    el("div", {}, el("dt", { text: label }), el("dd", { text: value }))));
}

function checkList(target, gates) {
  target.replaceChildren(...gates.map((gate) => el("li", {},
    el("span", { class: gate.passed ? "mark-ok" : "mark-bad", "aria-hidden": "true", text: gate.passed ? "✓" : "×" }),
    el("div", {},
      el("strong", { text: (gate.passed ? "" : "Failed: ") + gate.name }),
      el("span", { class: "detail", text: gate.detail })))));
}

function finding(title, detail) {
  return el("li", {}, el("span", { class: "dot", "aria-hidden": "true" }),
    el("div", {}, el("strong", { text: title }), el("span", { class: "detail", text: detail })));
}

/* ---- job 1: the catalogue ----------------------------------------------- */

function bookFindings(books) {
  const out = [];
  const changes = books.changes;
  if (changes.first_run) {
    out.push(finding("This was the first run", "There is no earlier run to compare with yet."));
  } else {
    const total = changes.new + changes.removed + changes.price + changes.stock;
    out.push(finding(total === 0 ? "Nothing changed since the last run" : whole.format(total) + " changes since the last run",
      changes.new + " new, " + changes.removed + " removed, " + changes.price + " price changes, " +
      changes.stock + " stock changes."));
    for (const example of changes.examples.slice(0, 4)) {
      const text = example.kind === "price" ? pounds(example.from) + " became " + pounds(example.to)
        : example.kind === "stock" ? "stock went from " + example.from + " to " + example.to
        : example.kind === "new" ? "new in the shop" : "no longer in the shop";
      out.push(finding(example.title, text));
    }
  }
  const noText = books.notes.description_start.missing;
  out.push(finding(noText === 0 ? "Every product has a description"
    : noText + " products have no description",
    noText === 0 ? "Checked on every product page." : "Their product pages have none. They are counted, not hidden."));
  out.push(finding(books.price_mismatches === 0 ? "List price and page price agree everywhere"
    : books.price_mismatches + " products show two different prices",
    "Each price is read twice, on the list page and on the product page, and compared."));
  out.push(finding(books.rejected_count === 0 ? "No row broke a rule" : books.rejected_count + " rows set aside",
    books.rejected_count === 0 ? "Fourteen fields per product, twelve rules per row."
      : "First reason: " + books.rejected[0].problems[0] + "."));
  if (books.odd_categories && books.odd_categories.length) {
    out.push(finding("Two category names look like leftovers",
      books.odd_categories.map((c) => "“" + c.name + "” holds " + c.count + " products").join(", ") +
      ". These are the shop's own names. They are reported, not renamed."));
  }
  out.push(finding(whole.format(books.list_pages) + " list pages and " + whole.format(books.product_pages) + " product pages",
    megabytes(books.fetch.bytes) + " read in " + duration(books.seconds) + ". The slowest answer took " +
    (books.fetch.slowest_ms / 1000).toFixed(1) + " s. " + books.fetch.retries + " retries were needed."));
  return out;
}

function barRows(target, pairs) {
  const top = Math.max(...pairs.map(([, count]) => count), 1);
  target.replaceChildren(...pairs.map(([label, count]) => el("div", { class: "bar-row" },
    el("span", { class: "label", text: label, title: label }),
    el("span", { class: "track" }, el("span", { class: "fill", style: "width:" + (100 * count / top).toFixed(1) + "%" })),
    el("span", { class: "value", text: whole.format(count) }))));
  target.setAttribute("aria-label", target.getAttribute("aria-label").split(":")[0] + ": " +
    pairs.map(([label, count]) => label + " " + count).join(", "));
}

function stars(rating) {
  const cell = el("span", { class: "stars", role: "img", "aria-label": rating + " of 5" });
  cell.append("★".repeat(rating), el("span", { class: "off", text: "★".repeat(5 - rating) }));
  return cell;
}

function renderBooks(status, table) {
  checkList($("#book-checks"), status.books.gates);

  const rows = table.rows.map((values) => Object.fromEntries(table.columns.map((name, i) => [name, values[i]])));

  const bands = new Map();
  for (const row of rows) {
    const low = Math.floor(row.price_incl_tax / 10) * 10;
    bands.set(low, (bands.get(low) || 0) + 1);
  }
  barRows($("#price-bars"), [...bands.entries()].sort((a, b) => a[0] - b[0])
    .map(([low, count]) => ["£" + low + " to £" + (low + 10), count]));

  const perCategory = new Map();
  for (const row of rows) perCategory.set(row.category, (perCategory.get(row.category) || 0) + 1);
  const categories = [...perCategory.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  barRows($("#category-bars"), categories.slice(0, 10));

  status.books.odd_categories = categories.filter(([name]) => ["Default", "Add a comment"].includes(name))
    .map(([name, count]) => ({ name, count }));
  $("#book-findings").replaceChildren(...bookFindings(status.books));

  const select = $("#category");
  for (const [name, count] of [...categories].sort((a, b) => a[0].localeCompare(b[0]))) {
    select.append(el("option", { value: name, text: name + " (" + count + ")" }));
  }

  const state = { sort: "title", descending: false, page: 0 };
  const body = $("#books tbody");
  const headers = [...document.querySelectorAll("#books th[data-sort]")];

  function draw() {
    const query = $("#q").value.trim().toLowerCase();
    const category = select.value;
    const rating = Number($("#rating").value);
    const found = rows.filter((row) =>
      (!query || row.title.toLowerCase().includes(query)) &&
      (!category || row.category === category) &&
      (!rating || row.rating === rating));
    const key = state.sort;
    found.sort((a, b) => {
      const order = typeof a[key] === "number" ? a[key] - b[key]
        : String(a[key]).localeCompare(String(b[key]), "en", { sensitivity: "base" });
      return (state.descending ? -order : order) || a.title.localeCompare(b.title);
    });
    const pages = Math.max(1, Math.ceil(found.length / PAGE_SIZE));
    state.page = Math.min(state.page, pages - 1);
    const first = state.page * PAGE_SIZE;
    const shown = found.slice(first, first + PAGE_SIZE);
    body.replaceChildren(...shown.map((row) => el("tr", {},
      el("td", { class: "title" }, el("a", { href: row.url, text: row.title, rel: "noopener" })),
      el("td", { text: row.category }),
      el("td", { class: "num", text: pounds(row.price_incl_tax) }),
      el("td", { class: "num", text: String(row.stock) }),
      el("td", { class: "num" }, stars(row.rating)),
      el("td", { class: "code", text: row.upc }))));
    if (shown.length === 0) {
      body.append(el("tr", {}, el("td", { class: "none", colspan: "6", text: "No product matches. Try a shorter search." })));
    }
    $("#count").textContent = found.length === 0 ? "0 products"
      : "Showing " + whole.format(first + 1) + " to " + whole.format(first + shown.length) + " of " +
        whole.format(found.length) + (found.length === rows.length ? " products" : " matching products");
    $("#page-label").textContent = "Page " + (state.page + 1) + " of " + pages;
    $("#prev").disabled = state.page === 0;
    $("#next").disabled = state.page >= pages - 1;
    for (const header of headers) {
      if (header.dataset.sort === state.sort) header.setAttribute("aria-sort", state.descending ? "descending" : "ascending");
      else header.removeAttribute("aria-sort");
    }
  }

  for (const header of headers) {
    header.querySelector("button").addEventListener("click", () => {
      if (state.sort === header.dataset.sort) state.descending = !state.descending;
      else { state.sort = header.dataset.sort; state.descending = false; }
      state.page = 0;
      draw();
    });
  }
  for (const control of [$("#q"), select, $("#rating")]) {
    control.addEventListener("input", () => { state.page = 0; draw(); });
  }
  $("#filters").addEventListener("submit", (event) => event.preventDefault());
  $("#prev").addEventListener("click", () => { state.page -= 1; draw(); });
  $("#next").addEventListener("click", () => { state.page += 1; draw(); });
  draw();

  $("#book-downloads").replaceChildren(
    el("a", { href: "data/downloads/books.csv", download: "" }, "Download the catalogue as CSV ", el("small", { text: whole.format(rows.length) + " rows, opens in Excel" })),
    el("a", { href: "data/downloads/books.json", download: "" }, "JSON"));
}

/* ---- job 2: electricity prices ------------------------------------------ */

function niceTop(value) {
  const steps = [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50];
  for (const step of steps) if (value / step <= 5) return { top: Math.ceil(value / step) * step, step };
  return { top: Math.ceil(value / 100) * 100, step: 100 };
}

function renderPower(status, rows, todayHours) {
  const power = status.power;
  checkList($("#power-checks"), power.gates);
  const zones = Object.keys(power.zones);
  const dates = [...new Set(rows.map((row) => row.date))].sort();
  const byKey = new Map(rows.map((row) => [row.date + "|" + row.zone, row]));
  const latest = dates[dates.length - 1];
  $("#price-window").textContent = "from " + shortDay(dates[0]) + " to " + shortDay(latest);

  // The line chart.
  // It is drawn at the width it is shown at, so the labels stay readable on a phone.
  const chart = $("#line-chart");
  const { top: yTop, step } = niceTop(Math.max(...rows.map((row) => row.avg_nok)));
  const hidden = new Set();
  const lines = {};
  let W = 960, left = 52, right = 16, guide = null;
  let x = () => 0;
  function drawLines() {
    while (chart.childNodes.length > 2) chart.lastChild.remove();  // keep the title and description
    W = Math.max(320, Math.min(960, Math.round(chart.parentElement.clientWidth - 20)));
    const narrow = W < 600;
    const H = narrow ? 300 : 380, top = 14, bottom = 38;
    left = narrow ? 40 : 52;
    right = narrow ? 10 : 16;
    chart.setAttribute("viewBox", "0 0 " + W + " " + H);
    x = (index) => left + (W - left - right) * (dates.length === 1 ? 0.5 : index / (dates.length - 1));
    const y = (value) => top + (H - top - bottom) * (1 - value / yTop);
    for (let value = 0; value <= yTop + 1e-9; value += step) {
      chart.append(svg("line", { class: "grid", x1: left, x2: W - right, y1: y(value), y2: y(value) }));
      chart.append(svg("text", { x: left - 8, y: y(value) + 4, "text-anchor": "end" }, value.toFixed(step < 1 ? 2 : 0)));
    }
    const labels = narrow ? 3 : 6;
    const every = Math.max(1, Math.round(dates.length / labels));
    dates.forEach((date, index) => {
      const last = index === dates.length - 1;
      if (!last && (index % every !== 0 || dates.length - 1 - index < every / 2)) return;
      chart.append(svg("text", { x: x(index), y: H - 12, "text-anchor": last ? "end" : index === 0 ? "start" : "middle" }, shortDay(date)));
    });
    zones.forEach((zone, number) => {
      let path = "";
      let pen = "M";
      dates.forEach((date, index) => {
        const row = byKey.get(date + "|" + zone);
        if (!row) { pen = "M"; return; }
        path += pen + x(index).toFixed(1) + " " + y(row.avg_nok).toFixed(1) + " ";
        pen = "L";
      });
      lines[zone] = svg("path", { class: "zone", d: path.trim(), stroke: "var(--z" + (number + 1) + ")", "stroke-dasharray": ZONE_DASH[zone] });
      if (hidden.has(zone)) lines[zone].style.display = "none";
      chart.append(lines[zone]);
    });
    guide = svg("line", { class: "guide", y1: top, y2: H - bottom, x1: -10, x2: -10 });
    chart.append(guide);
  }
  drawLines();

  const legend = $("#legend");
  zones.forEach((zone, number) => {
    const sample = svg("svg", { viewBox: "0 0 30 10", "aria-hidden": "true" });
    sample.append(svg("line", { x1: 1, x2: 29, y1: 5, y2: 5, stroke: "var(--z" + (number + 1) + ")", "stroke-width": 3, "stroke-dasharray": ZONE_DASH[zone], "stroke-linecap": "round" }));
    const button = el("button", { type: "button", "aria-pressed": "true" }, sample, zone + " " + power.zones[zone]);
    button.addEventListener("click", () => {
      const show = hidden.has(zone);
      if (show) hidden.delete(zone); else hidden.add(zone);
      button.setAttribute("aria-pressed", String(show));
      lines[zone].style.display = show ? "" : "none";
    });
    legend.append(button);
  });

  const readout = $("#readout");
  function read(event) {
    const box = chart.getBoundingClientRect();
    const inside = (event.clientX - box.left) / box.width * W;
    const index = Math.max(0, Math.min(dates.length - 1, Math.round((inside - left) / (W - left - right) * (dates.length - 1))));
    guide.setAttribute("x1", x(index));
    guide.setAttribute("x2", x(index));
    const parts = zones.filter((zone) => !hidden.has(zone)).map((zone) => {
      const row = byKey.get(dates[index] + "|" + zone);
      return power.zones[zone] + " " + (row ? kr(row.avg_nok) : "no data");
    });
    readout.replaceChildren(el("b", { text: shortDay(dates[index]) + ": " }), parts.join(" · "));
  }
  chart.addEventListener("pointermove", read);
  chart.addEventListener("pointerdown", read);

  // The latest day, zone by zone.
  $("#zones tbody").replaceChildren(...zones.map((zone) => {
    const row = byKey.get(latest + "|" + zone);
    return el("tr", {},
      el("th", { scope: "row" }, power.zones[zone], el("small", { text: zone })),
      el("td", { class: "num", text: row ? kr(row.avg_nok) : "no data" }),
      el("td", { class: "num", text: row ? kr(row.min_nok) + " at " + row.min_at : "" }),
      el("td", { class: "num", text: row ? kr(row.max_nok) + " at " + row.max_at : "" }));
  }));

  // Today, through the day.
  const pick = $("#zone-pick");
  for (const zone of zones) pick.append(el("option", { value: zone, text: power.zones[zone] + " (" + zone + ")" }));
  pick.value = todayHours.NO2 ? "NO2" : zones.find((zone) => todayHours[zone]) || zones[0];
  const dayChart = $("#day-chart");
  function drawDay() {
    const points = todayHours[pick.value] || [];
    while (dayChart.childNodes.length > 1) dayChart.lastChild.remove();
    if (points.length === 0) { $("#day-note").textContent = "No prices for today yet."; return; }
    const w = Math.max(300, Math.min(560, Math.round(dayChart.parentElement.clientWidth - 20)));
    const h = 300, l = 44, r = 10, t = 12, b = 34;
    dayChart.setAttribute("viewBox", "0 0 " + w + " " + h);
    const scale = niceTop(Math.max(...points.map((point) => point.nok)));
    const yy = (value) => t + (h - t - b) * (1 - Math.max(0, value) / scale.top);
    for (let value = 0; value <= scale.top + 1e-9; value += scale.step) {
      dayChart.append(svg("line", { class: "grid", x1: l, x2: w - r, y1: yy(value), y2: yy(value) }));
      dayChart.append(svg("text", { x: l - 6, y: yy(value) + 4, "text-anchor": "end" }, value.toFixed(scale.step < 1 ? 2 : 0)));
    }
    const low = points.reduce((a, c) => (c.nok < a.nok ? c : a));
    const high = points.reduce((a, c) => (c.nok > a.nok ? c : a));
    const slot = (w - l - r) / points.length;
    points.forEach((point, index) => {
      const cls = point === low ? "bar low" : point === high ? "bar high" : "bar";
      dayChart.append(svg("rect", { class: cls, x: (l + index * slot + slot * 0.12).toFixed(1), width: (slot * 0.76).toFixed(1), y: yy(point.nok).toFixed(1), height: Math.max(1, yy(0) - yy(point.nok)).toFixed(1) }));
      if (point.time.endsWith(":00") && Number(point.time.slice(0, 2)) % 6 === 0) {
        dayChart.append(svg("text", { x: l + index * slot, y: h - 12 }, point.time));
      }
    });
    $("#day-title").textContent = "Price through today in " + power.zones[pick.value] + ": lowest " + kr(low.nok) +
      " at " + low.time + ", highest " + kr(high.nok) + " at " + high.time;
    $("#day-note").textContent = "Lowest " + kr(low.nok) + " at " + low.time + " (blue), highest " + kr(high.nok) +
      " at " + high.time + " (orange). NOK per kWh, " + points.length + " price points.";
  }
  pick.addEventListener("change", drawDay);
  drawDay();

  let resizeTimer = 0;
  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    if (window.innerWidth === lastWidth) return;  // a phone's address bar changes only the height
    lastWidth = window.innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { drawLines(); drawDay(); }, 150);
  });

  // The same thirty days as a table.
  $("#power-table thead").replaceChildren(el("tr", {}, el("th", { scope: "col", text: "Day" }),
    ...zones.map((zone) => el("th", { scope: "col", class: "num", text: power.zones[zone] }))));
  $("#power-table tbody").replaceChildren(...[...dates].reverse().map((date) => el("tr", {},
    el("th", { scope: "row", text: shortDay(date) }),
    ...zones.map((zone) => {
      const row = byKey.get(date + "|" + zone);
      return el("td", { class: "num", text: row ? kr(row.avg_nok) : "" });
    }))));

  $("#power-downloads").replaceChildren(
    el("a", { href: "data/downloads/power.csv", download: "" }, "Download the prices as CSV ", el("small", { text: whole.format(rows.length) + " rows" })),
    el("a", { href: "data/downloads/power.json", download: "" }, "JSON"));
}

/* ---- the run history ---------------------------------------------------- */

function renderRuns(runs) {
  const newestFirst = [...runs].reverse().slice(0, 30);
  $("#run-table tbody").replaceChildren(...newestFirst.map((run, index) => {
    const books = run.books || {};
    const power = run.power || {};
    const isFirst = index === newestFirst.length - 1 && runs.length <= 30;
    return el("tr", {},
      el("th", { scope: "row", text: norwayTime(run.started) }),
      el("td", { class: "num", text: duration(run.seconds) }),
      el("td", { class: "num", text: whole.format(books.rows || 0) }),
      el("td", { class: "num", text: isFirst ? "first run" : whole.format(books.changes || 0) }),
      el("td", { class: "num", text: whole.format(power.fetched || 0) }),
      el("td", { class: "num", text: whole.format((books.requests || 0) + (power.requests || 0)) }),
      el("td", { class: "num", text: whole.format((books.retries || 0) + (power.retries || 0)) }),
      el("td", { class: "num", text: whole.format((books.failed || 0) + (power.failed || 0)) }),
      el("td", { text: run.ok ? "Published" : "Failed, nothing published" }));
  }));
}

async function main() {
  try {
    const [status, table, runs, power, today] = await Promise.all([
      load("status.json"), load("books.table.json"), load("runs.json"),
      load("downloads/power.json"), load("power.today.json"),
    ]);
    renderStatus(status);
    renderBooks(status, table);
    renderPower(status, power, today);
    renderRuns(runs);
  } catch (error) {
    const pill = $("#status-pill");
    pill.textContent = "The data files could not be loaded";
    pill.classList.add("bad");
    $("#status-when").textContent = String(error.message || error);
  }
}

main();
