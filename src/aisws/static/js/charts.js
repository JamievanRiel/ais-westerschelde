// Kleine SVG-grafieken: kolommen (ook boven en onder een nullijn), liggende balken,
// taartpunten rond het station en een tooltip.
// Waarden komen ook altijd in een tabel onder de grafiek, de tooltip is extra.

const NS = "http://www.w3.org/2000/svg";

function el(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

// --- tooltip -------------------------------------------------------------------------

let tip;

export function showTip(x, y, value, label) {
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "tooltip";
    tip.setAttribute("role", "status");
    document.body.append(tip);
  }
  const strong = document.createElement("strong");
  strong.textContent = value;
  tip.replaceChildren(strong, label);
  tip.hidden = false;
  const box = tip.getBoundingClientRect();
  const left = Math.min(x + 12, window.innerWidth - box.width - 8);
  const top = y - box.height - 12 < 8 ? y + 16 : y - box.height - 12;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${top}px`;
}

export function hideTip() {
  if (tip) tip.hidden = true;
}

// --- hulpjes --------------------------------------------------------------------------

// As tot een rond getal met hoogstens drie ronde stappen (1, 2 of 5 × 10ⁿ, minstens 1:
// aantallen schepen en kilometers hebben geen fijnere stap nodig). Zo 0-10-20-30 in
// plaats van 0-13-25.
function niceScale(max) {
  const raw = Math.max(1, max / 3);
  const exponent = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((s) => s * exponent).find((s) => s >= raw);
  return { max: Math.max(step, Math.ceil(max / step) * step), step };
}

// Balk met 4px afgeronde data-kant; de kant aan de basislijn blijft recht.
function columnPath(x, y, w, h) {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h} L${x},${y + r} Q${x},${y} ${x + r},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h} Z`;
}

// Kolom onder een nullijn: recht aan de lijn, afgerond aan de datakant (onder).
function columnDownPath(x, y, w, h) {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y} L${x + w},${y} L${x + w},${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} L${x + r},${y + h} Q${x},${y + h} ${x},${y + h - r} Z`;
}

function barPath(x, y, w, h) {
  const r = Math.min(4, h / 2, w);
  return `M${x},${y} L${x + w - r},${y} Q${x + w},${y} ${x + w},${y + r} L${x + w},${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} L${x},${y + h} Z`;
}

// Met pijltjestoetsen door de markeringen lopen; ``focus(i)`` toont dezelfde tooltip
// als hover. Bij ``wrap`` (rondom, zoals kompasrichtingen) loopt het door na het eind.
function keyboard(svg, count, focus, clear, { wrap = false } = {}) {
  let active = -1;
  const go = (index) => {
    active = wrap ? (index + count) % count : Math.max(0, Math.min(count - 1, index));
    focus(active);
  };
  svg.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || (wrap && event.key === "ArrowDown")) go(active + 1);
    else if (event.key === "ArrowLeft" || (wrap && event.key === "ArrowUp")) go(active < 0 ? count - 1 : active - 1);
    else if (event.key === "Home") go(0);
    else if (event.key === "End") go(count - 1);
    else return;
    event.preventDefault();
  });
  svg.addEventListener("blur", () => {
    hideTip();
    clear();
  });
}

const observers = new WeakMap();

/** Maakt een grafiekcontainer leeg én stopt het hertekenen bij een breedteverandering. */
export function clearChart(container) {
  observers.get(container)?.disconnect();
  observers.delete(container);
  container.replaceChildren();
}

// Tekent opnieuw bij elke breedteverandering. Een nieuwe aanroep voor dezelfde
// container vervangt de vorige observer, zodat verversen niets laat lekken.
function onResize(container, draw) {
  observers.get(container)?.disconnect();
  let width = 0;
  const observer = new ResizeObserver(([entry]) => {
    const next = Math.round(entry.contentRect.width);
    if (next !== width) {
      width = next;
      draw(width);
    }
  });
  observer.observe(container);
  observers.set(container, observer);
}

// --- kolomgrafiek ------------------------------------------------------------------

/**
 * points: [{ value, tick, label }]
 *   tick:  korte as-tekst of null
 *   label: volledige omschrijving voor de tooltip
 * options.format(value) → tekst voor de tooltip en het maximumlabel
 */
export function columnChart(container, points, { format, height = 180, name }) {
  container.classList.add("chart");

  function draw(width) {
    const margin = { top: 20, right: 4, bottom: 24, left: 34 };
    const plotW = Math.max(40, width - margin.left - margin.right);
    const plotH = height;
    const scale = niceScale(Math.max(0, ...points.map((p) => p.value)));
    const max = scale.max;
    const band = plotW / points.length;
    const barW = Math.max(1, Math.min(24, band - 2));
    const y = (v) => margin.top + plotH - (v / max) * plotH;

    const svg = el("svg", {
      viewBox: `0 0 ${width} ${plotH + margin.top + margin.bottom}`,
      role: "img",
      "aria-label": name,
      tabindex: "0",
    });

    for (let value = 0; value <= max; value += scale.step) {
      const gy = y(value);
      svg.append(el("line", { class: value === 0 ? "axis" : "grid", x1: margin.left, x2: width - margin.right, y1: gy, y2: gy }));
      const label = el("text", { x: margin.left - 6, y: gy + 4, "text-anchor": "end" });
      label.textContent = format(value, true);
      svg.append(label);
    }

    const maxIndex = points.reduce((best, p, i) => (p.value > points[best].value ? i : best), 0);
    const bars = [];
    points.forEach((p, i) => {
      const x = margin.left + i * band + (band - barW) / 2;
      const hit = el("rect", { class: "hit", x: margin.left + i * band, y: margin.top, width: band, height: plotH });
      const h = plotH - (y(p.value) - margin.top);
      const mark = el("path", { class: "mark", d: h > 0 ? columnPath(x, y(p.value), barW, h) : "" });
      hit.addEventListener("pointermove", (event) => showTip(event.clientX, event.clientY, format(p.value), p.label));
      hit.addEventListener("pointerleave", hideTip);
      svg.append(hit, mark);
      bars.push({ hit, mark, x: x + barW / 2 });
      if (p.tick) {
        const tick = el("text", { x: x + barW / 2, y: margin.top + plotH + 16, "text-anchor": "middle" });
        tick.textContent = p.tick;
        svg.append(tick);
      }
    });

    if (points.length && points[maxIndex].value > 0) {
      // Label boven de hoogste kolom, maar nooit buiten de grafiek.
      const cx = bars[maxIndex].x;
      let anchor = "middle";
      let lx = cx;
      if (cx > width - margin.right - 40) {
        anchor = "end";
        lx = cx + barW / 2;
      } else if (cx < margin.left + 40) {
        anchor = "start";
        lx = cx - barW / 2;
      }
      const top = el("text", {
        class: "value-label",
        x: lx,
        y: y(points[maxIndex].value) - 6,
        "text-anchor": anchor,
      });
      top.textContent = format(points[maxIndex].value);
      svg.append(top);
    }

    keyboard(svg, points.length, (active) => {
      const box = bars[active].hit.getBoundingClientRect();
      bars.forEach((b, i) => b.mark.classList.toggle("active", i === active));
      showTip(box.left + box.width / 2, box.top + 20, format(points[active].value), points[active].label);
    }, () => bars.forEach((b) => b.mark.classList.remove("active")));

    container.replaceChildren(svg);
  }

  onResize(container, draw);
}

// --- liggende balken -------------------------------------------------------------------

/** rows: [{ label, value, detail }] – waarde staat aan het eind van de balk. */
export function barChart(container, rows, { format, name }) {
  container.classList.add("chart");

  function draw(width) {
    const labelW = Math.min(150, width * 0.38);
    const valueW = 64;
    const rowH = 30;
    const barH = 14;
    const plotW = Math.max(40, width - labelW - valueW);
    const max = Math.max(...rows.map((r) => r.value), 0) || 1;
    const svg = el("svg", { viewBox: `0 0 ${width} ${rows.length * rowH}`, role: "img", "aria-label": name });

    rows.forEach((row, i) => {
      const top = i * rowH;
      const w = Math.max(2, (row.value / max) * plotW);
      const label = el("text", { x: 0, y: top + rowH / 2 + 4 });
      label.textContent = row.label;
      const hit = el("rect", { class: "hit", x: 0, y: top, width, height: rowH });
      const mark = el("path", { class: "mark", d: barPath(labelW, top + (rowH - barH) / 2, w, barH) });
      const value = el("text", { class: "value-label", x: labelW + w + 6, y: top + rowH / 2 + 4 });
      value.textContent = format(row.value);
      hit.addEventListener("pointermove", (event) => showTip(event.clientX, event.clientY, format(row.value), row.detail));
      hit.addEventListener("pointerleave", hideTip);
      svg.append(label, hit, mark, value);
    });
    container.replaceChildren(svg);
  }

  onResize(container, draw);
}

// --- kolommen boven en onder een nullijn -------------------------------------------------

/**
 * Twee richtingen van één maat: ``up`` boven de nullijn, ``down`` eronder, in dezelfde
 * kleur; de positie zegt welke richting het is, de labels boven en onder benoemen ze.
 * points: [{ up, down, tick, label }]
 * options.tip(point) → tekst voor de tooltip; options.format(value) → as-tekst
 */
export function divergingChart(container, points, { format, tip, labels, height = 200, name }) {
  container.classList.add("chart");

  function draw(width) {
    const margin = { top: 20, right: 4, bottom: 40, left: 34 };
    const plotW = Math.max(40, width - margin.left - margin.right);
    const half = height / 2;
    const zero = margin.top + half;
    const scale = niceScale(Math.max(0, ...points.flatMap((p) => [p.up, p.down])));
    const len = (v) => (v / scale.max) * half;
    const band = plotW / points.length;
    const barW = Math.max(1, Math.min(24, band - 2));

    const svg = el("svg", {
      viewBox: `0 0 ${width} ${height + margin.top + margin.bottom}`,
      role: "img",
      "aria-label": name,
      tabindex: "0",
    });

    const tickLabel = (value, gy) => {
      const label = el("text", { x: margin.left - 6, y: gy + 4, "text-anchor": "end" });
      label.textContent = format(value);
      svg.append(label);
    };
    for (let value = scale.step; value <= scale.max; value += scale.step) {
      for (const gy of [zero - len(value), zero + len(value)]) {
        svg.append(el("line", { class: "grid", x1: margin.left, x2: width - margin.right, y1: gy, y2: gy }));
        tickLabel(value, gy);
      }
    }
    svg.append(el("line", { class: "axis", x1: margin.left, x2: width - margin.right, y1: zero, y2: zero }));
    tickLabel(0, zero);

    const upLabel = el("text", { class: "series-label", x: margin.left, y: margin.top - 8 });
    upLabel.textContent = `↑ ${labels.up}`;
    const downLabel = el("text", { class: "series-label", x: margin.left, y: zero + half + 34 });
    downLabel.textContent = `↓ ${labels.down}`;
    svg.append(upLabel, downLabel);

    // 1 px lucht aan weerszijden van de nullijn, zodat op en af niet tegen elkaar plakken.
    const columns = points.map((p, i) => {
      const x = margin.left + i * band + (band - barW) / 2;
      const hit = el("rect", { class: "hit", x: margin.left + i * band, y: margin.top, width: band, height });
      const marks = el("g", { class: "marks" });
      if (p.up > 0) marks.append(el("path", { class: "mark", d: columnPath(x, zero - 1 - len(p.up), barW, len(p.up)) }));
      if (p.down > 0) marks.append(el("path", { class: "mark", d: columnDownPath(x, zero + 1, barW, len(p.down)) }));
      hit.addEventListener("pointermove", (event) => showTip(event.clientX, event.clientY, tip(p), p.label));
      hit.addEventListener("pointerleave", hideTip);
      svg.append(hit, marks);
      if (p.tick) {
        const tick = el("text", { x: x + barW / 2, y: zero + half + 16, "text-anchor": "middle" });
        tick.textContent = p.tick;
        svg.append(tick);
      }
      return { hit, marks };
    });

    keyboard(svg, points.length, (active) => {
      const box = columns[active].hit.getBoundingClientRect();
      columns.forEach((c, i) => c.marks.classList.toggle("active", i === active));
      showTip(box.left + box.width / 2, box.top + 20, tip(points[active]), points[active].label);
    }, () => columns.forEach((c) => c.marks.classList.remove("active")));

    container.replaceChildren(svg);
  }

  onResize(container, draw);
}

// --- taartpunten rond het station ---------------------------------------------------------

const COMPASS = ["N", "NNO", "NO", "ONO", "O", "OZO", "ZO", "ZZO", "Z", "ZZW", "ZW", "WZW", "W", "WNW", "NW", "NNW"];

/** Kompasrichting in 16 streken, zoals op een zeekaart: 95° → "O". */
export function compass(deg) {
  return COMPASS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16];
}

/**
 * Afstand per richting vanaf een middelpunt: 0° = noord, met de klok mee. De straal is
 * de afstand zelf (lineair), zodat het diagram leest als een kaartje rond het station.
 * Geen getallen bij de punten: de verste richting hoort in een zin bij het diagram.
 * sectors: [{ from, to, value | null, label }]
 * options.format(value, axis) → tekst voor tooltip en ringen
 */
export function polarChart(container, sectors, { format, name, empty = "niets ontvangen" }) {
  container.classList.add("chart", "polar");

  function draw(width) {
    const size = Math.min(width, 420);
    const pad = 22;
    const radius = size / 2 - pad;
    const cx = width / 2;
    const cy = size / 2;
    const scale = niceScale(Math.max(0, ...sectors.map((s) => s.value ?? 0)));
    const r = (v) => (v / scale.max) * radius;
    const at = (deg, dist) => {
      const a = (deg * Math.PI) / 180;
      return [cx + dist * Math.sin(a), cy - dist * Math.cos(a)];
    };
    const wedge = (from, to, dist) => {
      const [x0, y0] = at(from, dist);
      const [x1, y1] = at(to, dist);
      return `M${cx},${cy} L${x0},${y0} A${dist},${dist} 0 0 1 ${x1},${y1} Z`;
    };

    const svg = el("svg", { viewBox: `0 0 ${width} ${size}`, role: "img", "aria-label": name, tabindex: "0" });

    // Ringwaarden op de diagonaal naar het noordoosten, weg van de kompasletters.
    for (let value = scale.step; value <= scale.max; value += scale.step) {
      svg.append(el("circle", { class: "grid ring", cx, cy, r: r(value) }));
      const [x, y] = at(45, r(value));
      const label = el("text", { x: x + 3, y: y - 3 });
      label.textContent = format(value, true);
      svg.append(label);
    }
    for (const [deg, text] of [[0, "N"], [90, "O"], [180, "Z"], [270, "W"]]) {
      const [x, y] = at(deg, radius + 12);
      const label = el("text", { class: "compass", x, y: y + 4, "text-anchor": "middle" });
      label.textContent = text;
      svg.append(label);
    }

    const wedges = sectors.map((s) => {
      const hit = el("path", { class: "hit", d: wedge(s.from, s.to, radius) });
      const mark = el("path", { class: "mark", d: s.value ? wedge(s.from, s.to, r(s.value)) : "" });
      const value = s.value ? format(s.value) : empty;
      hit.addEventListener("pointermove", (event) => showTip(event.clientX, event.clientY, value, s.label));
      hit.addEventListener("pointerleave", hideTip);
      svg.append(hit, mark);
      return { hit, mark, value };
    });
    svg.append(el("circle", { class: "station", cx, cy, r: 3.5 }));

    keyboard(svg, sectors.length, (active) => {
      const s = sectors[active];
      const [x, y] = at((s.from + s.to) / 2, Math.max(r(s.value ?? 0), radius * 0.3));
      const box = svg.getBoundingClientRect();
      const k = box.width / width;
      wedges.forEach((w, i) => w.mark.classList.toggle("active", i === active));
      showTip(box.left + x * k, box.top + y * k, wedges[active].value, s.label);
    }, () => wedges.forEach((w) => w.mark.classList.remove("active")), { wrap: true });

    container.replaceChildren(svg);
  }

  onResize(container, draw);
}

// --- tabel als alternatief voor elke grafiek ------------------------------------------

export function tableView(container, headers, rows, summaryText = "Toon als tabel") {
  const details = document.createElement("details");
  details.className = "table-view";
  const summary = document.createElement("summary");
  summary.textContent = summaryText;
  const scroll = document.createElement("div");
  scroll.className = "scroll";
  const table = document.createElement("table");
  const head = table.createTHead().insertRow();
  for (const h of headers) {
    const th = document.createElement("th");
    th.textContent = h;
    head.append(th);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const cell of row) tr.insertCell().textContent = cell;
  }
  scroll.append(table);
  details.append(summary, scroll);
  container.replaceChildren(details);
}
