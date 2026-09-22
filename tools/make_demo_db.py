"""Vult een database met weken verzonnen scheepvaart, voor de statistiekenpagina.

Het voorbeeldbestand (``make_sample.py``) is een kwartier verkeer: genoeg voor de
kaart, te weinig voor statistieken per dag of per week. Dit script laat
``--days`` dagen verzonnen verkeer door de echte tracker en opslag lopen, met een
dag-, week- en seizoenspatroon, dagen met overbereik voor het afstandsrecord en
af en toe een heel groot schip:

    python tools/make_demo_db.py demo.db
    # config.toml: [storage] db_path = "demo.db"
    aisws serve

De laatste dag loopt tot nu, dus direct daarna staan er ook schepen op de kaart.
Alle schepen, namen en MMSI's zijn verzonnen.
"""

import argparse
import heapq
import math
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from make_sample import FAIRWAY, _along, _dist_m

from aisws.config import TrackerConfig
from aisws.geo import haversine_m
from aisws.messages import PositionReport, StaticData
from aisws.store import Store
from aisws.tracker import Tracker

TZ = ZoneInfo("Europe/Amsterdam")
HOUR = 3600
DAY = 24 * HOUR

# Vanaf zee (Wandelaar) de vaargeul in; buiten het normale bereik van het station.
INBOUND = [(51.47, 2.55), (51.40, 2.85), *FAIRWAY]
OUTBOUND = INBOUND[::-1]
# Verkeersscheidingsstelsel op de Noordzee: alleen te ontvangen bij overbereik.
NORTH_SEA = [(51.25, 1.95), (51.45, 2.35), (51.62, 2.62), (51.85, 2.95), (52.05, 3.15)]
ANCHORAGE = (51.420, 3.505)
FERRY = [(51.4430, 3.5960), (51.4080, 3.5700)]  # Vlissingen – Breskens
LOCAL = [  # korte stukken voor plezier, visserij, sleep- en baggerwerk
    [(51.430, 3.630), (51.405, 3.700), (51.380, 3.760)],
    [(51.402, 3.540), (51.420, 3.470), (51.400, 3.390)],
    [(51.370, 3.850), (51.390, 3.950), (51.405, 4.030)],
    [(51.445, 3.520), (51.455, 3.420), (51.470, 3.330)],
]

FIRST = ["NORDIC", "ATLANTIC", "EASTERN", "NORTHERN", "BALTIC", "CAPE", "OCEAN", "SILVER",
         "GOLDEN", "ARCTIC", "IBERIAN", "CELTIC", "PACIFIC", "WESTERN", "COASTAL", "GRAND",
         "ROYAL", "SOUTHERN", "AMBER", "CORAL", "POLAR", "BRIGHT", "NOBLE", "SWIFT"]
SECOND = ["HORIZON", "TRADER", "SPIRIT", "EXPRESS", "HARMONY", "VOYAGER", "BRIDGE", "PIONEER",
          "STAR", "PRIDE", "WAVE", "CARRIER", "GLORY", "ENDEAVOUR", "FORTUNE", "BREEZE",
          "LEGACY", "VENTURE", "MERCHANT", "DAWN", "ISLAND", "PASSAGE", "CROWN", "COMPASS"]
DUTCH = ["ZEEUWSE", "WALCHERSE", "SCHELDE", "VLISSINGER", "BRESKENSE", "HANSWEERTER"]
BOATS = ["MEEUW", "WIND", "STROOM", "GOLF", "TIJ", "BRIES", "PLAAT", "KREEK", "DUIN", "ZWALUW"]
YACHT = ["BLAUWE", "WITTE", "VRIJE", "STILLE", "SNELLE", "LAATSTE", "ZILVEREN", "OUDE",
         "GOUDEN", "KLEINE", "WILDE", "LICHTE"]
YACHT_NOUN = ["ZWAAN", "MEEUW", "REIGER", "ZEEHOND", "DOLFIJN", "ZON", "WIND", "GOLF",
              "STER", "VOGEL", "BRIES", "SLOEP"]
MIDS = [636, 538, 477, 255, 219, 211, 244, 205, 357, 563, 248, 229]


class Fleet:
    """Een vaste pool van schepen per soort, zodat schepen terugkomen."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.pools: dict[str, list[dict]] = {}
        self.busy_until: dict[int, float] = {}
        used: set[int] = set()

        def mmsi(mids):
            while True:
                value = rng.choice(mids) * 1_000_000 + rng.randrange(100_000, 999_999)
                if value not in used:
                    used.add(value)
                    return value

        def names(first, second):
            """Alle combinaties in willekeurige volgorde: elk schip een eigen naam."""
            pool = [f"{a} {b}" for a in first for b in second]
            rng.shuffle(pool)
            return pool

        seagoing_names, local_names = names(FIRST, SECOND), names(DUTCH, BOATS)

        def seagoing(n, types, length, speed):
            ships = []
            for _ in range(n):
                size = rng.uniform(*length)
                ships.append({
                    "mmsi": mmsi(MIDS), "cls": "A", "type": rng.choice(types),
                    "name": seagoing_names.pop(),
                    "length": round(size), "beam": round(size / rng.uniform(6.2, 7.5)),
                    "draught": round(size / 24 + rng.uniform(-1, 1), 1),
                    "imo": rng.randrange(9_100_000, 9_999_999), "sog": rng.uniform(*speed),
                })
            return ships

        def local(n, types, length, speed, cls="A", pool=local_names):
            ships = []
            for _ in range(n):
                size = rng.uniform(*length)
                ships.append({
                    "mmsi": mmsi([244, 245, 205]), "cls": cls, "type": rng.choice(types),
                    "name": pool.pop(), "length": round(size),
                    "beam": max(2, round(size / rng.uniform(2.8, 4.2))), "draught": None, "imo": None,
                    "sog": rng.uniform(*speed),
                })
            return ships

        self.pools = {
            "cargo": seagoing(260, [70, 71, 79], (90, 300), (10.5, 15.5)),
            "giant": seagoing(14, [71], (366, 400), (13.0, 16.0)),
            "tanker": seagoing(90, [80, 84, 89], (100, 250), (9.5, 13.0)),
            "passenger": seagoing(12, [60, 69], (110, 135), (8.5, 11.0)),
            "anchored": seagoing(60, [70, 80], (120, 230), (0.0, 0.2)),
            "north_sea": seagoing(80, [70, 71, 80], (150, 350), (12.0, 16.0)),
            "pilot": local(4, [50], (18, 25), (17.0, 22.0)),
            "ferry": local(2, [40], (40, 42), (22.0, 25.0)),
            "tug": local(14, [31, 52], (22, 32), (6.5, 9.5)),
            "dredger": local(5, [33], (80, 120), (8.0, 10.5)),
            "fishing": local(10, [30], (18, 30), (4.5, 7.5)),
            "pleasure": local(120, [36, 37], (7, 14), (4.0, 7.5), cls="B",
                              pool=names(YACHT, YACHT_NOUN)),
        }
        for i, ship in enumerate(self.pools["pilot"], start=1):
            ship["name"] = f"LOODS {i}"
        for ship, name in zip(self.pools["ferry"], ["ZEEUWSE PIJL", "SCHELDE FLITS"]):
            ship["name"] = name

    def take(self, kind: str, start: float, end: float) -> dict | None:
        """Een schip dat van ``start`` tot ``end`` nergens anders vaart."""
        pool = self.pools[kind]
        for ship in self.rng.sample(pool, min(8, len(pool))):
            if self.busy_until.get(ship["mmsi"], 0) < start:
                self.busy_until[ship["mmsi"]] = end
                return ship
        return None


def static(ship: dict) -> StaticData:
    return StaticData(
        msg_type=5 if ship["cls"] == "A" else 24, mmsi=ship["mmsi"], ais_class=ship["cls"],
        name=ship["name"], ship_type=ship["type"], length=ship["length"], beam=ship["beam"],
        draught=ship["draught"], imo=ship["imo"],
    )


def report(ship: dict, lat, lon, sog, cog, nav_status=0) -> PositionReport:
    return PositionReport(
        msg_type=1 if ship["cls"] == "A" else 18, mmsi=ship["mmsi"], lat=lat, lon=lon,
        sog=round(sog, 1), cog=round(cog % 360, 1), heading=round(cog) % 360 if sog > 0.5 else None,
        nav_status=nav_status if ship["cls"] == "A" else None, ais_class=ship["cls"],
    )


def sail(ship, path, start, rng, interval_s, offset_m=0.0):
    """Posities langs ``path`` op kruissnelheid, met wat ruis in koers en snelheid."""
    total = sum(_dist_m(a, b) for a, b in zip(path, path[1:]))
    speed_mps = ship["sog"] * 1852 / 3600
    lateral = rng.uniform(-offset_m, offset_m)
    t, travelled = start + rng.uniform(0, interval_s), 0.0
    yield t, static(ship)
    while travelled < total:
        (lat, lon), cog = _along(path, travelled)
        side = math.radians(cog + 90)
        lat += lateral * math.cos(side) / 111_195
        lon += lateral * math.sin(side) / (111_195 * math.cos(math.radians(lat)))
        yield t, report(ship, lat, lon, ship["sog"] + rng.uniform(-0.4, 0.4), cog + rng.uniform(-2, 2))
        t += interval_s
        travelled += speed_mps * interval_s


def anchor(ship, where, start, end, rng):
    yield start, static(ship)
    t = start
    while t < end:
        lat = where[0] + rng.uniform(-0.0002, 0.0002)
        lon = where[1] + rng.uniform(-0.0003, 0.0003)
        yield t, report(ship, lat, lon, rng.uniform(0, 0.2), rng.uniform(0, 359), nav_status=1)
        t += 300


def rate_per_hour(kind: str, local: datetime) -> float:
    """Aankomsten per uur, naar soort, uur, weekdag en seizoen."""
    hour, weekend = local.hour + local.minute / 60, local.weekday() >= 5
    day = 0.5 - 0.5 * math.cos(2 * math.pi * (hour - 2) / 24)  # 0 om 02:00, 1 om 14:00
    summer = max(0.0, math.cos(2 * math.pi * (local.timetuple().tm_yday - 196) / 365))
    if kind == "cargo":
        return 2.2 * (0.75 + 0.45 * day) * (0.8 if weekend else 1.0)
    if kind == "tanker":
        return 0.8 * (0.85 + 0.3 * day)
    if kind == "giant":
        return 0.006
    if kind == "passenger":
        return 0.18 * day
    if kind == "pilot":
        return 1.2 * (0.7 + 0.5 * day)
    if kind == "tug":
        return 0.5 * (0.5 + day) * (0.6 if weekend else 1.0)
    if kind == "dredger":
        return 0.15 * (0.4 if weekend else 1.0)
    if kind == "fishing":
        return 0.25 * (0.3 + day)
    if kind == "pleasure":
        daylight = 1.0 if 9 <= hour < 19 else 0.05
        return (0.6 + 3.2 * summer) * daylight * (2.6 if weekend else 1.0)
    if kind == "anchored":
        return 0.22
    raise ValueError(kind)


def visits(fleet: Fleet, start: float, end: float, rng: random.Random, ducting_days: set[int]):
    """Alle bezoeken als losse generators van (tijd, bericht)."""
    kinds = ["cargo", "tanker", "giant", "passenger", "pilot", "tug", "dredger",
             "fishing", "pleasure", "anchored"]
    t = start
    while t < end:
        local = datetime.fromtimestamp(t, TZ)
        for kind in kinds:
            for _ in range(poisson(rng, rate_per_hour(kind, local) / 6)):
                begin = t + rng.uniform(0, 600)
                yield from visit(fleet, kind, begin, rng)
        if int((t - start) // DAY) in ducting_days:
            for _ in range(poisson(rng, 3.0 / 6)):
                ship = fleet.take("north_sea", t, t + 8 * HOUR)
                if ship:
                    path = NORTH_SEA if rng.random() < 0.5 else NORTH_SEA[::-1]
                    yield sail(ship, path, t + rng.uniform(0, 600), rng, 90, offset_m=1500)
        if local.minute == 0 and 6 <= local.hour <= 22:
            yield from ferry(fleet, t, rng)
        t += 600


# Hoe lang een schip bij een bezoek ruim bezet is (uren), zodat het niet op twee plekken vaart.
BUSY_H = {"cargo": 9, "tanker": 10, "giant": 8, "passenger": 11, "pilot": 2,
          "tug": 5, "dredger": 4, "fishing": 7, "pleasure": 8}


def visit(fleet, kind, begin, rng):
    if kind == "anchored":
        stay = rng.uniform(6, 36) * HOUR
        ship = fleet.take(kind, begin, begin + stay)
        if ship:
            spot = (ANCHORAGE[0] + rng.uniform(-0.012, 0.012), ANCHORAGE[1] + rng.uniform(-0.03, 0.03))
            yield anchor(ship, spot, begin, begin + stay, rng)
        return
    if kind in ("cargo", "tanker", "giant", "passenger", "pilot"):
        path = INBOUND if rng.random() < 0.5 else OUTBOUND
        if kind == "pilot":  # loodsboot: stukje de geul in en terug
            i = rng.randrange(0, 5)
            path = FAIRWAY[i:i + 3] + FAIRWAY[i:i + 2][::-1]
        ship = fleet.take(kind, begin, begin + BUSY_H[kind] * HOUR)
        if ship:
            yield sail(ship, path, begin, rng, 90 if kind != "pilot" else 60, offset_m=250)
        return
    route = rng.choice(LOCAL)
    path = route if rng.random() < 0.5 else route[::-1]
    if kind in ("dredger", "fishing", "pleasure"):
        path = path + path[::-1]  # heen en terug
    ship = fleet.take(kind, begin, begin + BUSY_H[kind] * HOUR)
    if ship:
        yield sail(ship, path, begin, rng, 60, offset_m=300)


def ferry(fleet, t, rng):
    for i, ship in enumerate(fleet.pools["ferry"]):
        path = FERRY if i == 0 else FERRY[::-1]
        yield sail(ship, path, t + rng.uniform(0, 60), rng, 30)


def poisson(rng: random.Random, mean: float) -> int:
    limit, k, p = math.exp(-mean), 0, rng.random()
    while p > limit:
        k += 1
        p *= rng.random()
    return k


def generate(path: Path, days: int = 90, lat: float = 51.443, lon: float = 3.570,
             seed: int = 2026, end: float | None = None) -> tuple[int, int]:
    """Schrijft ``days`` dagen tot ``end`` (standaard nu) naar ``path``.

    Geeft het aantal gehoorde posities en het aantal verschillende schepen terug.
    """
    rng = random.Random(seed)
    end = time.time() if end is None else end
    start = (end - days * DAY) // HOUR * HOUR
    fleet = Fleet(rng)
    # Normaal zo'n 40 km bereik; op een op de twaalf dagen overbereik tot ver op de Noordzee.
    reach_km = [rng.uniform(36, 46) for _ in range(days + 2)]
    ducting = {d for d in range(days + 1) if rng.random() < 1 / 12}
    for d in ducting:
        reach_km[d] = rng.uniform(70, 125)

    store = Store(path, max_pending_writes=10**9)
    tracker = Tracker(TrackerConfig(), lat, lon, static_lookup=store.lookup_static)
    heard, next_flush = 0, start + HOUR
    # Naam en afmetingen pas doorgeven als het station het schip ook hoort.
    waiting: dict[int, StaticData] = {}
    for t, message in heapq.merge(*visits(fleet, start, end, rng, ducting), key=lambda item: item[0]):
        if t > end:
            continue
        if isinstance(message, StaticData):
            waiting[message.mmsi] = message
            continue
        day = int((t - start) // DAY)
        if haversine_m(lat, lon, message.lat, message.lon) > reach_km[day] * 1000:
            continue
        heard += 1
        if message.mmsi in waiting:
            store.add(tracker.handle(waiting.pop(message.mmsi), t))
        store.add(tracker.handle(message, t))
        if t >= next_flush:
            tracker.sweep(t)
            store.flush()
            next_flush += HOUR
    store.flush()
    store.cleanup(int(end - 30 * DAY))
    store.close()
    return heard, len(fleet.busy_until)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("db", type=Path, help="nieuw databasebestand")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--lat", type=float, default=51.443, help="positie van het station")
    parser.add_argument("--lon", type=float, default=3.570)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if args.db.exists():
        sys.exit(f"{args.db} bestaat al; dit script voegt toe, dus begin met een nieuw bestand.")
    heard, ships = generate(args.db, args.days, args.lat, args.lon, args.seed)
    print(f"{args.db}: {args.days} {'dag' if args.days == 1 else 'dagen'}, {heard} posities, {ships} schepen")


if __name__ == "__main__":
    main()
