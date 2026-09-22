// Getallen en tijden in het Nederlands.

const nl = "nl-NL";

export function number(value, digits = 0) {
  if (value === null || value === undefined) return "–";
  return value.toLocaleString(nl, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function knots(value) {
  return value === null || value === undefined ? "–" : `${number(value, 1)} kn`;
}

export function degrees(value) {
  return value === null || value === undefined ? "–" : `${Math.round(value)}°`;
}

export function meters(value, digits = 0) {
  return value === null || value === undefined ? "–" : `${number(value, digits)} m`;
}

export function ago(epochSeconds, now = Date.now() / 1000) {
  const s = Math.max(0, Math.round(now - epochSeconds));
  if (s < 60) return `${s} s geleden`;
  if (s < 3600) return `${Math.floor(s / 60)} min geleden`;
  if (s < 86400) return `${Math.floor(s / 3600)} uur geleden`;
  return `${Math.floor(s / 86400)} dagen geleden`;
}

export function date(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleDateString(nl, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export function dateTime(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleString(nl, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function monthName(yyyyMm) {
  const [y, m] = yyyyMm.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(nl, { month: "long", year: "numeric" });
}

// Afstand tussen twee punten in meters (haversine).
export function distanceM(lat1, lon1, lat2, lon2) {
  const r = 6371008.8;
  const rad = Math.PI / 180;
  const a =
    Math.sin(((lat2 - lat1) * rad) / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(((lon2 - lon1) * rad) / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
}

export async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}
