// Live kaart: WebSocket-updates, scheepslijst, detailpaneel en sporen.

import {
  Map as MapLibre,
  NavigationControl,
  ScaleControl,
} from "https://cdn.jsdelivr.net/npm/maplibre-gl@6.10.0/dist/maplibre-gl.mjs";
import { CLASSES, colorClass, cssColor, groupLabel, hullIcon, navStatusLabel } from "./shiptypes.js";
import * as fmt from "./format.js";

const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const SEAMARKS = "https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png";
const FALLBACK_CENTER = [3.8, 51.4];
const MOVING_KN = 0.5;

// Kaartkleuren per modus: land in buff, water in kaartblauw (dag),
// of het donkere ECDIS-nachtpalet.
const CHART = {
  light: {
    land: "#eee5c9", park: "#e5e2c4", built: "#e9dfc0", building: "#dfd3ae",
    water: "#d4e6f2", waterLine: "#b9d3e6", road: "#f8f3e4", casing: "#d8cca7",
    rail: "#d3c8a6", boundary: "#a89f86", ink: "#1b2a36", ink2: "#4b5b68",
    waterText: "#34587a", seamarkOpacity: 0.9, seamarkBrightness: 1, labelHalo: "rgba(251,252,253,0.85)",
  },
  dark: {
    land: "#1e1d18", park: "#22231b", built: "#23221b", building: "#2b2922",
    water: "#10202c", waterLine: "#18324a", road: "#35332b", casing: "#29281f",
    rail: "#3a372e", boundary: "#5b574a", ink: "#c5d0d8", ink2: "#8f9ca6",
    waterText: "#7fa3c2", seamarkOpacity: 0.75, seamarkBrightness: 0.45, labelHalo: "rgba(12,21,29,0.85)",
  },
};

const state = {
  station: null,
  gate: null,
  vessels: new Map(),
  selected: null,
  detail: null,
  trackHours: 6,
  track: [],
  record: null,
  filter: "",
};

const $ = (id) => document.getElementById(id);
let map;
let styleCache = null;

// --- modus en stijl ---------------------------------------------------------

function mode() {
  const forced = document.documentElement.dataset.theme;
  if (forced === "light" || forced === "dark") return forced;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function nauticalStyle(base, p) {
  const style = structuredClone(base);
  const set = (layer, key, value) => {
    if (layer.paint && key in layer.paint) layer.paint[key] = value;
  };
  for (const layer of style.layers) {
    const id = layer.id;
    if (id === "background") set(layer, "background-color", p.land);
    else if (id === "water") set(layer, "fill-color", p.water);
    else if (id === "waterway") set(layer, "line-color", p.waterLine);
    else if (id === "park" || id === "landcover_wood") set(layer, "fill-color", p.park);
    else if (id === "landuse_residential") set(layer, "fill-color", p.built);
    else if (id === "building") {
      set(layer, "fill-color", p.building);
      set(layer, "fill-outline-color", p.building);
    } else if (id.startsWith("road_")) {
      set(layer, "fill-color", p.land);
      set(layer, "line-color", p.land);
    } else if (id.startsWith("railway")) set(layer, "line-color", p.rail);
    else if (id.startsWith("highway") || id.startsWith("tunnel") || id.startsWith("aeroway")) {
      const casing = id.includes("casing") || id.includes("subtle");
      set(layer, "line-color", casing ? p.casing : p.road);
      set(layer, "fill-color", p.road);
    } else if (id.startsWith("boundary")) set(layer, "line-color", p.boundary);
    else if (id.startsWith("water_name") || id === "waterway_line_label") {
      set(layer, "text-color", p.waterText);
      set(layer, "text-halo-color", p.water);
    } else if (layer.type === "symbol") {
      set(layer, "text-color", id.startsWith("label_") ? p.ink : p.ink2);
      set(layer, "text-halo-color", p.land);
    }
  }
  return style;
}

function fallbackStyle(p) {
  return {
    version: 8,
    glyphs: GLYPHS,
    sources: {},
    layers: [{ id: "background", type: "background", paint: { "background-color": p.water } }],
  };
}

async function buildStyle() {
  const p = CHART[mode()];
  try {
    styleCache ??= await fmt.getJson(STYLE_URL);
    return nauticalStyle(styleCache, p);
  } catch {
    showMapError("De ondergrond kon niet laden. Heeft de Pi internet? De schepen blijven zichtbaar.");
    return fallbackStyle(p);
  }
}

function showMapError(text) {
  if (document.querySelector(".map-error")) return;
  const box = document.createElement("p");
  box.className = "map-error";
  box.setAttribute("role", "status");
  box.textContent = text;
  $("map").append(box);
}

// Rompje als SDF-afbeelding, zodat MapLibre het per scheepsklasse kan kleuren
// en er een donkere rand omheen kan tekenen.
function hullImage() {
  const w = 24;
  const h = 48;
  const poly = [[12, 3], [17, 15], [16.6, 45], [7.4, 45], [7, 15]];
  const data = new Uint8Array(w * h * 4);
  const inside = (x, y) => {
    let hit = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i];
      const [xj, yj] = poly[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
    }
    return hit;
  };
  const edgeDistance = (x, y) => {
    let best = Infinity;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [ax, ay] = poly[j];
      const [bx, by] = poly[i];
      const dx = bx - ax;
      const dy = by - ay;
      const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)));
      best = Math.min(best, Math.hypot(x - (ax + t * dx), y - (ay + t * dy)));
    }
    return best;
  };
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const px = x + 0.5;
      const py = y + 0.5;
      const d = edgeDistance(px, py) * (inside(px, py) ? 1 : -1);
      data[(y * w + x) * 4 + 3] = Math.max(0, Math.min(255, Math.round(191 + d * 32)));
    }
  }
  return { width: w, height: h, data };
}

function classColorExpression() {
  const pairs = CLASSES.flatMap((c) => [c.key, cssColor(c.color)]);
  return ["match", ["get", "cls"], ...pairs.slice(0, -2), cssColor("--ship-other")];
}

function addOverlays() {
  const p = CHART[mode()];
  const magenta = cssColor("--magenta");
  const halo = cssColor("--ship-halo");
  const shipColor = classColorExpression();
  const beforeLabels = map.getLayer("label_other") ? "label_other" : undefined;

  map.addSource("seamarks", {
    type: "raster",
    tiles: [SEAMARKS],
    tileSize: 256,
    attribution: '<a href="https://www.openseamap.org">OpenSeaMap</a>',
  });
  map.addLayer(
    // Boeien en betonning pas vanaf zoom 12: daaronder overstemmen ze de schepen.
    { id: "seamarks", type: "raster", source: "seamarks", minzoom: 12,
      paint: { "raster-opacity": p.seamarkOpacity, "raster-brightness-max": p.seamarkBrightness } },
    beforeLabels,
  );

  map.addSource("gate", { type: "geojson", data: gateGeoJson() });
  map.addSource("record", { type: "geojson", data: recordGeoJson() });
  map.addSource("track", { type: "geojson", data: trackGeoJson() });
  map.addSource("ships", { type: "geojson", data: shipsGeoJson() });

  // De doorvaartlijn: gestippeld in drukinkt, rustiger dan de magenta van het station.
  map.addLayer({
    id: "gate-line",
    type: "line",
    source: "gate",
    layout: { "line-cap": "round" },
    paint: { "line-color": p.ink2, "line-width": 1.5, "line-dasharray": [0.5, 2.5] },
  });
  map.addLayer({
    id: "gate-label",
    type: "symbol",
    source: "gate",
    minzoom: 10,
    layout: {
      "symbol-placement": "line",
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Regular"],
      "text-size": 10,
      "text-offset": [0, -0.8],
    },
    paint: { "text-color": p.ink2, "text-halo-color": p.labelHalo, "text-halo-width": 1.5 },
  });
  map.addLayer({
    id: "record-line",
    type: "line",
    source: "record",
    filter: ["==", ["geometry-type"], "LineString"],
    paint: { "line-color": magenta, "line-width": 1.5, "line-dasharray": [3, 2] },
  });
  map.addLayer({
    id: "track-line",
    type: "line",
    source: "track",
    layout: { "line-join": "round", "line-cap": "round" },
    paint: { "line-color": magenta, "line-width": 2 },
  });
  map.addLayer({
    id: "station",
    type: "circle",
    source: "record",
    filter: ["==", ["get", "kind"], "station"],
    paint: {
      "circle-radius": 6,
      "circle-color": magenta,
      "circle-stroke-color": p.labelHalo,
      "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: "ships-still",
    type: "circle",
    source: "ships",
    filter: ["!", ["get", "moving"]],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 2.5, 13, ["*", 5, ["get", "size"]]],
      "circle-color": shipColor,
      "circle-stroke-color": halo,
      "circle-stroke-width": 1,
    },
  });
  map.addLayer({
    id: "ships-moving",
    type: "symbol",
    source: "ships",
    filter: ["get", "moving"],
    layout: {
      "icon-image": "hull",
      "icon-rotate": ["get", "rotation"],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "icon-size": ["interpolate", ["linear"], ["zoom"],
        8, ["*", 0.6, ["get", "size"]],
        14, ["*", 1.5, ["get", "size"]]],
    },
    paint: { "icon-color": shipColor, "icon-halo-color": halo, "icon-halo-width": 1.2 },
  });
  map.addLayer({
    id: "selected",
    type: "circle",
    source: "ships",
    filter: ["==", ["get", "mmsi"], state.selected ?? -1],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 11, 14, 22],
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-color": magenta,
      "circle-stroke-width": 2,
    },
  });
  map.addLayer({
    id: "ship-labels",
    type: "symbol",
    source: "ships",
    minzoom: 11.5,
    layout: {
      "text-field": ["coalesce", ["get", "name"], ["to-string", ["get", "mmsi"]]],
      "text-font": ["Noto Sans Regular"],
      "text-size": 11,
      "text-offset": [0, 1.4],
      "text-anchor": "top",
      "text-optional": true,
    },
    paint: { "text-color": p.ink, "text-halo-color": p.labelHalo, "text-halo-width": 1.5 },
  });
  map.addLayer({
    id: "record-label",
    type: "symbol",
    source: "record",
    filter: ["==", ["get", "kind"], "record"],
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Bold"],
      "text-size": 11,
      "text-offset": [0, -1.2],
      "text-anchor": "bottom",
    },
    paint: { "text-color": p.ink, "text-halo-color": p.labelHalo, "text-halo-width": 1.5 },
  });
}

async function applyStyle() {
  map.setStyle(await buildStyle(), { diff: false });
}

// --- GeoJSON uit de toestand -------------------------------------------------

function rotationOf(v) {
  return v.heading ?? v.cog ?? 0;
}

function isMoving(v) {
  return v.sog !== null && v.sog >= MOVING_KN && (v.heading !== null || v.cog !== null);
}

function sizeOf(v) {
  if (!v.length) return 0.85;
  return 0.7 + 0.8 * Math.max(0, Math.min(1, (v.length - 20) / 380));
}

function shipsGeoJson() {
  const features = [];
  for (const v of state.vessels.values()) {
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [v.lon, v.lat] },
      properties: {
        mmsi: v.mmsi,
        name: v.name,
        cls: colorClass(v.type_group),
        moving: isMoving(v),
        rotation: rotationOf(v),
        size: sizeOf(v),
      },
    });
  }
  return { type: "FeatureCollection", features };
}

function trackGeoJson() {
  const coords = state.track.map(([, lat, lon]) => [lon, lat]);
  const live = state.vessels.get(state.selected);
  if (live) coords.push([live.lon, live.lat]);
  const features = coords.length > 1
    ? [{ type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: {} }]
    : [];
  return { type: "FeatureCollection", features };
}

function gateGeoJson() {
  const g = state.gate;
  const features = g
    ? [{
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[g.lon1, g.lat1], [g.lon2, g.lat2]] },
      properties: { label: `doorvaartlijn ${g.name}` },
    }]
    : [];
  return { type: "FeatureCollection", features };
}

function recordGeoJson() {
  const features = [];
  const s = state.station;
  if (s) {
    features.push({ type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] },
      properties: { kind: "station" } });
  }
  const r = state.record;
  if (s && r) {
    features.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[s.lon, s.lat], [r.lon, r.lat]] },
      properties: { kind: "line" },
    });
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [r.lon, r.lat] },
      properties: { kind: "record", label: `Verste ontvangst ${fmt.number(r.distance_km, 1)} km` },
    });
  }
  return { type: "FeatureCollection", features };
}

function refreshSource(id, data) {
  const source = map?.getSource(id);
  if (source) source.setData(data);
}

// --- lijst en tellers ----------------------------------------------------------

let listTimer = null;

function scheduleRender() {
  refreshSource("ships", shipsGeoJson());
  if (state.selected !== null) {
    refreshSource("track", trackGeoJson());
    renderLive();
  }
  $("count").textContent = fmt.number(state.vessels.size);
  if (listTimer === null) {
    listTimer = setTimeout(() => {
      listTimer = null;
      renderList();
    }, 500);
  }
}

function renderLegend() {
  const counts = {};
  for (const v of state.vessels.values()) {
    const cls = colorClass(v.type_group);
    counts[cls] = (counts[cls] ?? 0) + 1;
  }
  const items = CLASSES.map((c) => {
    const li = document.createElement("li");
    const groupForIcon = c.key === "other" ? "unknown" : c.key;
    li.append(hullIcon(groupForIcon), `${c.label} (${counts[c.key] ?? 0})`);
    return li;
  });
  $("legend").replaceChildren(...items);
}

function matchesFilter(v) {
  if (!state.filter) return true;
  const q = state.filter.toLowerCase();
  return (v.name ?? "").toLowerCase().includes(q) || String(v.mmsi).includes(q);
}

function renderList() {
  renderLegend();
  const vessels = [...state.vessels.values()].filter(matchesFilter).sort(
    (a, b) => (b.length ?? -1) - (a.length ?? -1) || (a.name ?? "~").localeCompare(b.name ?? "~"),
  );
  const rows = vessels.map((v) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ship-row";
    button.dataset.mmsi = v.mmsi;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = v.name ?? `MMSI ${v.mmsi}`;
    const speed = document.createElement("span");
    speed.className = "speed num";
    speed.textContent = isMoving(v) ? fmt.knots(v.sog) : navStatusLabel(v.nav_status) ?? "stil";
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = v.length ? `${groupLabel(v.type_group)}, ${v.length} m` : groupLabel(v.type_group);
    button.append(hullIcon(v.type_group, isMoving(v)), name, speed, meta);
    button.addEventListener("click", () => select(v.mmsi, { fly: true }));
    li.append(button);
    return li;
  });
  $("ships").replaceChildren(...rows);

  const empty = $("empty");
  if (rows.length) {
    empty.hidden = true;
  } else {
    empty.hidden = false;
    empty.textContent = state.filter
      ? `Geen schip gevonden voor “${state.filter}”.`
      : "Nog geen schepen ontvangen. Zodra AIS-catcher berichten doorstuurt, verschijnen ze hier.";
  }
}

// --- detailpaneel ------------------------------------------------------------------

function fact(dl, label, value) {
  if (value === null || value === undefined || value === "" || value === "–") return;
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.append(dt, dd);
}

function renderDetail() {
  const view = $("detail-view");
  const d = state.detail;
  const live = state.vessels.get(state.selected);
  if (!d && !live) return;
  const info = { ...(d ?? {}), ...(live ?? {}) };
  const typeGroup = info.type_group ?? "unknown";

  const back = document.createElement("button");
  back.type = "button";
  back.className = "back";
  back.textContent = "← Alle schepen";
  back.addEventListener("click", deselect);

  const title = document.createElement("h2");
  title.textContent = info.name ?? `MMSI ${info.mmsi}`;
  const kind = document.createElement("p");
  kind.className = "kind";
  kind.append(hullIcon(typeGroup, live ? isMoving(live) : true), groupLabel(typeGroup));

  const facts = document.createElement("dl");
  facts.className = "facts";
  facts.id = "facts";

  const hours = document.createElement("div");
  hours.className = "segmented";
  hours.setAttribute("role", "group");
  hours.setAttribute("aria-label", "Lengte van het spoor");
  for (const h of [6, 24]) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `${h} uur`;
    b.setAttribute("aria-pressed", String(state.trackHours === h));
    b.addEventListener("click", () => {
      state.trackHours = h;
      renderDetail();
      loadTrack();
    });
    hours.append(b);
  }
  const note = document.createElement("p");
  note.className = "track-note";
  note.id = "track-note";

  view.replaceChildren(back, title, kind, facts, hours, note);
  renderLive();
}

function renderLive() {
  const facts = $("facts");
  if (!facts) return;
  const d = state.detail ?? {};
  const live = state.vessels.get(state.selected);
  facts.replaceChildren();
  if (live) {
    fact(facts, "Snelheid", fmt.knots(live.sog));
    fact(facts, "Koers", fmt.degrees(live.cog));
    fact(facts, "Status", navStatusLabel(live.nav_status));
    if (state.station) {
      const km = fmt.distanceM(state.station.lat, state.station.lon, live.lat, live.lon) / 1000;
      fact(facts, "Afstand tot station", `${fmt.number(km, 1)} km`);
    }
  }
  fact(facts, "Bestemming", d.destination);
  const length = live?.length ?? d.length;
  fact(facts, "Afmetingen", length ? `${length} × ${d.beam ?? "?"} m` : null);
  fact(facts, "Diepgang", d.draught ? fmt.meters(d.draught, 1) : null);
  fact(facts, "MMSI", String(state.selected));
  fact(facts, "IMO", d.imo ? String(d.imo) : null);
  fact(facts, "Roepnaam", d.callsign);
  fact(facts, "Klasse", d.ais_class ? `AIS klasse ${d.ais_class}` : null);
  fact(facts, "Laatst gezien", live ? fmt.ago(live.last_seen) : d.last_seen ? fmt.ago(d.last_seen) : null);
  fact(facts, "Eerst gezien", d.first_seen ? fmt.date(d.first_seen) : null);

  const note = $("track-note");
  if (note) {
    note.textContent = state.track.length
      ? `Spoor van de laatste ${state.trackHours} uur, ${state.track.length} punten.`
      : `Nog geen opgeslagen spoor in de laatste ${state.trackHours} uur.`;
  }
}

async function loadTrack() {
  const mmsi = state.selected;
  if (mmsi === null) return;
  try {
    const track = await fmt.getJson(`/api/vessels/${mmsi}/track?hours=${state.trackHours}`);
    if (state.selected !== mmsi) return;
    state.track = track;
    refreshSource("track", trackGeoJson());
    renderLive();
  } catch {
    /* volgende poging bij de volgende verversing */
  }
}

async function select(mmsi, { fly = false } = {}) {
  state.selected = mmsi;
  state.detail = null;
  state.track = [];
  if (map?.getLayer("selected")) map.setFilter("selected", ["==", ["get", "mmsi"], mmsi]);
  refreshSource("track", trackGeoJson());
  $("list-view").hidden = true;
  $("detail-view").hidden = false;
  $("panel-body").scrollTop = 0;
  renderDetail();

  const live = state.vessels.get(mmsi);
  if (fly && live && map) {
    const inView = map.getBounds().contains([live.lon, live.lat]);
    map.easeTo({ center: [live.lon, live.lat], zoom: Math.max(map.getZoom(), 12), duration: inView ? 300 : 800 });
  }
  try {
    const detail = await fmt.getJson(`/api/vessels/${mmsi}`);
    if (state.selected === mmsi) {
      state.detail = detail;
      renderDetail();
    }
  } catch {
    /* live gegevens blijven zichtbaar */
  }
  loadTrack();
  $("detail-view").querySelector(".back")?.focus({ preventScroll: true });
}

function deselect() {
  const previous = state.selected;
  state.selected = null;
  state.detail = null;
  state.track = [];
  if (map?.getLayer("selected")) map.setFilter("selected", ["==", ["get", "mmsi"], -1]);
  refreshSource("track", trackGeoJson());
  $("detail-view").hidden = true;
  $("list-view").hidden = false;
  renderList();
  document.querySelector(`.ship-row[data-mmsi="${previous}"]`)?.focus({ preventScroll: false });
}

// --- verbinding met de server ------------------------------------------------------

function setConnection(stateName, text) {
  const conn = $("conn");
  conn.dataset.state = stateName;
  conn.textContent = text;
}

function connect(attempt = 0) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/ws`);
  ws.addEventListener("open", () => {
    attempt = 0;
    setConnection("open", "Live verbonden");
  });
  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "snapshot") {
      state.vessels = new Map(message.vessels.map((v) => [v.mmsi, v]));
    } else if (message.type === "update") {
      for (const v of message.vessels) state.vessels.set(v.mmsi, v);
      for (const mmsi of message.removed) state.vessels.delete(mmsi);
    }
    scheduleRender();
  });
  ws.addEventListener("close", () => {
    const delay = Math.min(30, 2 ** attempt);
    setConnection("closed", `Verbinding verbroken, opnieuw proberen over ${delay} s`);
    setTimeout(() => connect(attempt + 1), delay * 1000);
  });
}

async function pollHealth() {
  try {
    const health = await fmt.getJson("/api/health");
    $("rate").textContent = fmt.number(health.messages_last_min);
  } catch {
    $("rate").textContent = "–";
  }
}

async function loadRecord() {
  try {
    const ranges = await fmt.getJson("/api/stats/range");
    state.record = ranges.record;
    refreshSource("record", recordGeoJson());
  } catch {
    /* geen record te tonen */
  }
}

// --- opstarten -----------------------------------------------------------------------

function hoverTooltip() {
  const tip = document.createElement("div");
  tip.className = "tooltip";
  tip.hidden = true;
  document.body.append(tip);
  for (const layer of ["ships-moving", "ships-still"]) {
    map.on("mousemove", layer, (event) => {
      const props = event.features[0].properties;
      const v = state.vessels.get(props.mmsi);
      if (!v) return;
      map.getCanvas().style.cursor = "pointer";
      const name = document.createElement("strong");
      name.textContent = v.name ?? `MMSI ${v.mmsi}`;
      tip.replaceChildren(name, `${groupLabel(v.type_group)}, ${isMoving(v) ? fmt.knots(v.sog) : "stil"}`);
      tip.style.left = `${event.originalEvent.clientX + 14}px`;
      tip.style.top = `${event.originalEvent.clientY + 14}px`;
      tip.hidden = false;
    });
    map.on("mouseleave", layer, () => {
      map.getCanvas().style.cursor = "";
      tip.hidden = true;
    });
    map.on("click", layer, (event) => select(event.features[0].properties.mmsi));
  }
}

async function start() {
  try {
    const config = await fmt.getJson("/api/config");
    state.station = config.station;
    state.gate = config.gate ?? null;
    $("station-name").textContent = config.station.name;
    document.title = `${config.station.name} live`;
  } catch {
    /* standaardnaam blijft staan */
  }

  map = new MapLibre({
    container: "map",
    style: await buildStyle(),
    center: state.station ? [state.station.lon, state.station.lat] : FALLBACK_CENTER,
    zoom: 10.5,
    attributionControl: { compact: true },
  });
  map.addControl(new NavigationControl({ showCompass: true, visualizePitch: false }), "top-right");
  map.addControl(new ScaleControl({ unit: "nautical" }), "bottom-left");
  map.on("style.load", () => {
    if (!map.hasImage("hull")) map.addImage("hull", hullImage(), { sdf: true, pixelRatio: 2 });
    addOverlays();
  });
  hoverTooltip();

  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyStyle);

  $("filter").addEventListener("input", (event) => {
    state.filter = event.target.value.trim();
    renderList();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.selected !== null) deselect();
  });

  renderList();
  connect();
  pollHealth();
  loadRecord();
  setInterval(pollHealth, 15_000);
  setInterval(loadRecord, 300_000);
  setInterval(() => {
    if (state.selected !== null) renderLive();
  }, 1000);
  setInterval(loadTrack, 30_000);
}

start();
