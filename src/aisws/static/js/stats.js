// Statistiekenpagina: haalt /api/stats/* op en tekent grafieken, heatmap en records.

import { barChart, columnChart, hideTip, showTip, tableView } from "./charts.js";
import { colorClass, CLASSES, groupLabel } from "./shiptypes.js";
import * as fmt from "./format.js";

const $ = (id) => document.getElementById(id);
const DAYS = ["ma", "di", "wo", "do", "vr", "za", "zo"];
const DAY_NAMES = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"];
const FOOTBALL_PITCH_M = 105;

const ships = (n) => `${fmt.number(n)} ${n === 1 ? "schip" : "schepen"}`;
const shipsAvg = (n) => `${fmt.number(n, 1)} schepen`;

function empty(container, text) {
  const p = document.createElement("p");
  p.className = "muted";
  p.textContent = text;
  container.replaceChildren(p);
}

// --- verkeer ------------------------------------------------------------------------

function renderHourly(rows) {
  if (!rows.some((r) => r.ships > 0)) {
    empty($("hourly"), "Nog geen schepen in de laatste 48 uur.");
    $("hourly-table").replaceChildren();
    return;
  }
  const points = rows.map((r) => {
    const [day, time] = r.local.split("T");
    const date = new Date(`${day}T00:00`);
    return {
      value: r.ships,
      tick: time === "00:00" || time === "12:00" ? time.slice(0, 5) : null,
      label: `${DAYS[(date.getDay() + 6) % 7]} ${date.getDate()} ${date.toLocaleDateString("nl-NL", { month: "short" })}, ${time}`,
    };
  });
  columnChart($("hourly"), points, {
    name: "Aantal schepen per uur, laatste 48 uur",
    format: (v, axis) => (axis ? fmt.number(v) : ships(v)),
  });
  tableView($("hourly-table"), ["Uur", "Schepen"], rows.map((r) => [r.local.replace("T", " "), fmt.number(r.ships)]));
}

function renderDaily(rows) {
  if (!rows.some((r) => r.ships > 0)) {
    empty($("daily"), "Nog geen schepen in de laatste 90 dagen.");
    $("daily-table").replaceChildren();
    return;
  }
  const points = rows.map((r, i) => {
    const date = new Date(`${r.date}T00:00`);
    return {
      value: r.ships,
      tick: (rows.length - 1 - i) % 14 === 0
        ? date.toLocaleDateString("nl-NL", { day: "numeric", month: "short" })
        : null,
      label: date.toLocaleDateString("nl-NL", { weekday: "long", day: "numeric", month: "long" }),
    };
  });
  columnChart($("daily"), points, {
    name: "Aantal verschillende schepen per dag, laatste 90 dagen",
    format: (v, axis) => (axis ? fmt.number(v) : ships(v)),
  });
  tableView($("daily-table"), ["Dag", "Schepen"], rows.map((r) => [r.date, fmt.number(r.ships)]));
}

// --- drukste momenten ------------------------------------------------------------------

function renderHeatmap(data) {
  const values = data.cells.flat().filter((v) => v !== null);
  if (!values.length) {
    empty($("heatmap"), "Nog te weinig gegevens. Na een paar dagen draaien verschijnt hier het weekpatroon.");
    $("heat-scale").replaceChildren();
    $("heat-table").replaceChildren();
    $("top").replaceChildren();
    return;
  }
  const max = Math.max(...values);
  const step = (v) => Math.min(5, Math.max(1, Math.ceil((v / max) * 5)));

  const grid = document.createElement("div");
  grid.className = "heatmap";
  grid.setAttribute("role", "img");
  grid.setAttribute("aria-label", "Gemiddeld aantal schepen per weekdag en uur; de tabel eronder bevat dezelfde waarden");
  grid.append(document.createElement("span"));
  for (let h = 0; h < 24; h++) {
    const label = document.createElement("span");
    label.className = "hour";
    label.textContent = h % 6 === 0 ? String(h) : "";
    grid.append(label);
  }
  data.cells.forEach((row, d) => {
    const day = document.createElement("span");
    day.className = "day";
    day.textContent = DAYS[d];
    grid.append(day);
    row.forEach((value, h) => {
      const cell = document.createElement("span");
      cell.className = "cell";
      const label = `${DAY_NAMES[d]} ${String(h).padStart(2, "0")}:00`;
      if (value === null) {
        cell.dataset.empty = "";
      } else {
        cell.style.background = `var(--seq-${step(value)})`;
      }
      cell.addEventListener("pointermove", (event) =>
        showTip(event.clientX, event.clientY, value === null ? "geen gegevens" : shipsAvg(value), label));
      cell.addEventListener("pointerleave", hideTip);
      grid.append(cell);
    });
  });
  $("heatmap").replaceChildren(grid);

  const scale = [document.createTextNode("minder")];
  for (let i = 1; i <= 5; i++) {
    const swatch = document.createElement("span");
    swatch.style.background = `var(--seq-${i})`;
    scale.push(swatch);
  }
  scale.push(document.createTextNode(`meer (tot ${fmt.number(max, 1)})`));
  $("heat-scale").replaceChildren(...scale);

  const rows = [];
  data.cells.forEach((row, d) => row.forEach((v, h) => {
    if (v !== null) rows.push([DAY_NAMES[d], `${String(h).padStart(2, "0")}:00`, fmt.number(v, 1)]);
  }));
  tableView($("heat-table"), ["Dag", "Uur", "Gemiddeld aantal schepen"], rows);

  const top = data.top.map((slot) => {
    const li = document.createElement("li");
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = `${DAY_NAMES[slot.weekday]} ${String(slot.hour).padStart(2, "0")}:00`;
    const how = document.createElement("span");
    how.className = "how";
    how.textContent = `gemiddeld ${shipsAvg(slot.avg)}`;
    li.append(when, how);
    return li;
  });
  $("top").replaceChildren(...top);
}

// --- snelheid ------------------------------------------------------------------------------

async function loadSpeed(period) {
  for (const button of $("periods").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.period === period));
  }
  const rows = await fmt.getJson(`/api/stats/speed?period=${period}`);
  if (!rows.length) {
    empty($("speed"), "Nog geen varende schepen gemeten in deze periode.");
    $("speed-table").replaceChildren();
    return;
  }
  barChart($("speed"), rows.map((r) => ({
    label: groupLabel(r.type_group),
    value: r.avg_kn,
    detail: `${groupLabel(r.type_group)}, ${fmt.number(r.samples)} metingen`,
  })), {
    name: "Gemiddelde snelheid per scheepstype in knopen",
    format: (v) => fmt.knots(v),
  });
  tableView($("speed-table"), ["Type", "Gemiddelde snelheid", "Metingen"],
    rows.map((r) => [groupLabel(r.type_group), fmt.knots(r.avg_kn), fmt.number(r.samples)]));
}

// --- grootste schip ------------------------------------------------------------------------------

function toScale(ship) {
  const ns = "http://www.w3.org/2000/svg";
  const make = (name, attrs) => {
    const node = document.createElementNS(ns, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };
  const span = Math.max(400, Math.ceil(ship.length / 100) * 100);
  const beam = ship.beam ?? Math.round(ship.length / 7);
  const top = 6;
  const svg = make("svg", {
    viewBox: `-4 0 ${span + 12} ${top + beam + 30}`,
    role: "img",
    "aria-label": `Op schaal: ${ship.length} meter lang en ${beam} meter breed`,
  });
  const L = ship.length;
  const bow = Math.min(L * 0.12, 40);
  const color = `var(${CLASSES.find((c) => c.key === colorClass(ship.type_group)).color})`;
  const hull = make("path", {
    class: "hull-drawing",
    d: `M0,${top} L${L - bow},${top} Q${L},${top + beam / 2} ${L - bow},${top + beam} L0,${top + beam} Z`,
    fill: color,
    stroke: color,
  });
  svg.append(hull);
  const rulerY = top + beam + 8;
  svg.append(make("line", { class: "ruler", x1: 0, x2: span, y1: rulerY, y2: rulerY }));
  for (let m = 0; m <= span; m += 100) {
    svg.append(make("line", { class: "ruler", x1: m, x2: m, y1: rulerY, y2: rulerY + 4 }));
    const label = make("text", { x: m, y: rulerY + 16, "text-anchor": m === 0 ? "start" : "middle" });
    label.textContent = `${m} m`;
    svg.append(label);
  }
  const wrap = document.createElement("div");
  wrap.className = "to-scale";
  wrap.append(svg);
  return wrap;
}

function renderLargest(data) {
  const box = $("largest");
  const current = data.current;
  const left = document.createElement("div");
  if (current) {
    const value = document.createElement("div");
    value.className = "figure-value";
    value.append(`${fmt.number(current.length)} `);
    const unit = document.createElement("span");
    unit.className = "figure-unit";
    unit.textContent = "meter";
    value.append(unit);
    const name = document.createElement("p");
    name.className = "figure-sub";
    const strong = document.createElement("strong");
    strong.textContent = current.name ?? `MMSI ${current.mmsi}`;
    name.append(strong, `, ${groupLabel(current.type_group).toLowerCase()}`);
    const seen = document.createElement("p");
    seen.className = "figure-sub";
    const pitches = fmt.number(current.length / FOOTBALL_PITCH_M, 1);
    seen.textContent = `${current.length} × ${current.beam ?? "?"} m, voor het eerst gezien op ${fmt.date(current.seen)}. `
      + `Dat is ${pitches} voetbalvelden achter elkaar.`;
    left.append(value, name, seen, toScale(current));
  } else {
    empty(left, "Deze maand is nog geen schip met bekende afmetingen gezien.");
  }

  const right = document.createElement("div");
  const title = document.createElement("h3");
  title.className = "chart-title";
  title.textContent = "Eerdere maanden";
  right.append(title);
  if (data.previous.length) {
    const table = document.createElement("table");
    table.className = "data-table";
    const head = table.createTHead().insertRow();
    for (const h of ["Maand", "Schip", "Lengte"]) {
      const th = document.createElement("th");
      th.textContent = h;
      head.append(th);
    }
    const body = table.createTBody();
    for (const m of data.previous) {
      const tr = body.insertRow();
      tr.insertCell().textContent = fmt.monthName(m.month);
      tr.insertCell().textContent = m.name ?? `MMSI ${m.mmsi}`;
      tr.insertCell().textContent = `${m.length} m`;
    }
    right.append(table);
  } else {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "Nog geen eerdere maanden.";
    right.append(p);
  }
  box.replaceChildren(left, right);
}

// --- verste ontvangst -----------------------------------------------------------------------------

function renderRange(data) {
  const box = $("range");
  const left = document.createElement("div");
  const record = data.record;
  if (record) {
    const value = document.createElement("div");
    value.className = "figure-value";
    value.append(`${fmt.number(record.distance_km, 1)} `);
    const unit = document.createElement("span");
    unit.className = "figure-unit";
    unit.textContent = `km, ${fmt.number(record.distance_nm, 1)} zeemijl`;
    value.append(unit);
    const who = document.createElement("p");
    who.className = "figure-sub";
    const strong = document.createElement("strong");
    strong.textContent = record.name ?? `MMSI ${record.mmsi}`;
    who.append(strong, ` op ${fmt.date(record.ts)}, gemeten vanaf de antenne.`);
    const link = document.createElement("p");
    link.className = "figure-sub";
    const a = document.createElement("a");
    a.href = "/";
    a.textContent = "Bekijk de lijn naar dit punt op de kaart";
    link.append(a);
    left.append(value, who, link);
  } else {
    empty(left, "Nog geen record. Het eerste schip dat twee keer goed ontvangen is, zet de lat.");
  }

  const right = document.createElement("div");
  const title = document.createElement("h3");
  title.className = "chart-title";
  title.textContent = "Hoe het record groeide";
  right.append(title);
  if (data.history.length > 1) {
    const table = document.createElement("table");
    table.className = "data-table";
    const head = table.createTHead().insertRow();
    for (const h of ["Datum", "Schip", "Afstand"]) {
      const th = document.createElement("th");
      th.textContent = h;
      head.append(th);
    }
    const body = table.createTBody();
    for (const r of [...data.history].reverse()) {
      const tr = body.insertRow();
      tr.insertCell().textContent = fmt.dateTime(r.ts);
      tr.insertCell().textContent = r.name ?? `MMSI ${r.mmsi}`;
      tr.insertCell().textContent = `${fmt.number(r.distance_km, 1)} km`;
    }
    right.append(table);
  } else {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "Elke keer dat een schip verder weg wordt ontvangen, komt het hier bij.";
    right.append(p);
  }
  box.replaceChildren(left, right);
}

// --- opstarten ------------------------------------------------------------------------------------

let period = "30d";

async function loadAll() {
  const results = await Promise.allSettled([
    fmt.getJson("/api/stats/hourly?hours=48").then(renderHourly),
    fmt.getJson("/api/stats/daily?days=90").then(renderDaily),
    fmt.getJson("/api/stats/heatmap?weeks=8").then(renderHeatmap),
    loadSpeed(period),
    fmt.getJson("/api/stats/largest?months=12").then(renderLargest),
    fmt.getJson("/api/stats/range").then(renderRange),
  ]);
  const failed = results.filter((r) => r.status === "rejected").length;
  const time = new Date().toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
  $("updated").textContent = failed
    ? `Bijgewerkt om ${time}; ${failed} onderdelen konden niet laden. Draait de server nog?`
    : `Bijgewerkt om ${time}, elke vijf minuten opnieuw.`;
}

async function start() {
  try {
    const config = await fmt.getJson("/api/config");
    $("station-name").textContent = config.station.name;
    document.title = `${config.station.name} in cijfers`;
  } catch {
    /* standaardnaam blijft staan */
  }
  $("periods").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-period]");
    if (!button) return;
    period = button.dataset.period;
    loadSpeed(period).catch(() => empty($("speed"), "Snelheden konden niet laden."));
  });
  await loadAll();
  setInterval(loadAll, 300_000);
}

start();
