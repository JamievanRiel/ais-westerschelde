// Scheepstypes, kleurklassen en navigatiestatussen, zoals de kaart ze toont.
//
// De kaart kleurt maar drie klassen (vracht, tanker, passagiers). Met vier of
// meer kleuren door elkaar zijn niet alle paren meer te onderscheiden voor
// kleurenblinde lezers; al het andere is grijs, het precieze type staat in de
// lijst en het detailpaneel.

export const CLASSES = [
  { key: "cargo", label: "Vracht", color: "--ship-cargo" },
  { key: "tanker", label: "Tanker", color: "--ship-tanker" },
  { key: "passenger", label: "Passagiers", color: "--ship-passenger" },
  { key: "other", label: "Overig", color: "--ship-other" },
];

// type_group van de backend → kleurklasse op de kaart.
export function colorClass(typeGroup) {
  if (typeGroup === "cargo" || typeGroup === "tanker") return typeGroup;
  if (typeGroup === "passenger" || typeGroup === "hsc") return "passenger";
  return "other";
}

const GROUP_LABELS = {
  cargo: "Vrachtschip",
  tanker: "Tanker",
  passenger: "Passagiersschip",
  hsc: "Snel vaartuig",
  towing: "Sleep- of duwboot",
  dredging: "Baggerschip",
  pilot: "Loodsboot",
  fishing: "Vissersschip",
  pleasure: "Plezier- of zeiljacht",
  other: "Overig schip",
  unknown: "Type onbekend",
};

export function groupLabel(typeGroup) {
  return GROUP_LABELS[typeGroup] ?? GROUP_LABELS.unknown;
}

const NAV_STATUS = {
  0: "Onderweg op de motor",
  1: "Voor anker",
  2: "Niet onder commando",
  3: "Beperkt manoeuvreerbaar",
  4: "Beperkt door diepgang",
  5: "Afgemeerd",
  6: "Aan de grond",
  7: "Aan het vissen",
  8: "Onderweg onder zeil",
  14: "Noodbaken (AIS-SART)",
};

export function navStatusLabel(code) {
  return NAV_STATUS[code] ?? null;
}

export function cssColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Klein rompje als SVG, voor de lijst en de legenda.
export function hullIcon(typeGroup, moving = true) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "hull");
  svg.setAttribute("viewBox", "0 0 9 16");
  svg.setAttribute("aria-hidden", "true");
  const cls = CLASSES.find((c) => c.key === colorClass(typeGroup));
  const shape = moving
    ? document.createElementNS(ns, "path")
    : document.createElementNS(ns, "circle");
  if (moving) {
    shape.setAttribute("d", "M4.5 0.5 L8.5 5 L8.3 15.5 L0.7 15.5 L0.5 5 Z");
  } else {
    shape.setAttribute("cx", "4.5");
    shape.setAttribute("cy", "8");
    shape.setAttribute("r", "3.5");
  }
  shape.setAttribute("fill", `var(${cls.color})`);
  shape.setAttribute("stroke", "var(--ship-halo)");
  shape.setAttribute("stroke-width", "1");
  svg.append(shape);
  return svg;
}
