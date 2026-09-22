"""Genereert samples/sample.nmea: synthetisch AIS-verkeer op de Westerschelde.

Alle schepen, namen en MMSI's zijn verzonnen. Het bestand is bedoeld om zonder
antenne te ontwikkelen en te demonstreren:

    python tools/make_sample.py
    aisws replay samples/sample.nmea --rate 3.5

De kopregel van het bestand noemt het tempo waarop het in echte tijd afspeelt.
Veel sneller afspelen laat schepen onmogelijk hard varen, en dan keurt de
tracker hun posities terecht af.
"""

import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import aisenc

TICK_S = 10
DURATION_S = 15 * 60
STATIC_EVERY_S = 360

# Globale route door de vaargeul, van zee (Wielingen) naar Antwerpen.
FAIRWAY = [
    (51.385, 3.05), (51.405, 3.30), (51.425, 3.47), (51.428, 3.58), (51.400, 3.69),
    (51.375, 3.78), (51.365, 3.87), (51.405, 3.96), (51.415, 4.03), (51.395, 4.10),
    (51.400, 4.19), (51.375, 4.235), (51.345, 4.25), (51.300, 4.27),
]


def _dist_m(a, b):
    dlat = (b[0] - a[0]) * 111_195
    dlon = (b[1] - a[1]) * 111_195 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


def _bearing(a, b):
    dlat = b[0] - a[0]
    dlon = (b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.degrees(math.atan2(dlon, dlat)) % 360


def _along(path, distance):
    """Punt en koers op ``distance`` meter langs ``path``."""
    for a, b in zip(path, path[1:]):
        segment = _dist_m(a, b)
        if distance <= segment:
            f = distance / segment
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f), _bearing(a, b)
        distance -= segment
    return path[-1], _bearing(path[-2], path[-1])


@dataclass
class Ship:
    mmsi: int
    name: str
    ship_type: int
    dims: tuple[int, int, int, int]
    callsign: str = ""
    imo: int = 0
    draught: float = 0.0
    destination: str = ""
    cls: str = "A"
    path: list = field(default_factory=list)
    start_m: float = 0.0
    sog: float = 0.0
    anchor: tuple[float, float] | None = None
    nav_status: int = 0
    interval_s: int = TICK_S

    def position(self, t, rng):
        if self.anchor is not None:
            lat = self.anchor[0] + rng.uniform(-0.0001, 0.0001)
            lon = self.anchor[1] + rng.uniform(-0.00015, 0.00015)
            return lat, lon, round(rng.uniform(0, 0.2), 1), rng.uniform(0, 359.9)
        travelled = self.start_m + self.sog * 1852 / 3600 * t
        (lat, lon), cog = _along(self.path, travelled)
        return lat, lon, round(self.sog + rng.uniform(-0.3, 0.3), 1), cog

    def static_bits(self):
        a, b, c, d = self.dims
        if self.cls == "A":
            return [aisenc.type5(self.mmsi, self.name, self.callsign, self.imo, self.ship_type,
                                 a, b, c, d, self.draught, self.destination)]
        return [aisenc.type24a(self.mmsi, self.name),
                aisenc.type24b(self.mmsi, self.ship_type, self.callsign, a, b, c, d)]

    def position_bits(self, t, rng):
        lat, lon, sog, cog = self.position(t, rng)
        heading = round(cog + rng.uniform(-2, 2)) % 360
        if self.cls == "B":
            return aisenc.type18(self.mmsi, lat, lon, sog, cog, heading)
        return aisenc.type1(self.mmsi, lat, lon, sog, cog, heading, self.nav_status)


def fleet():
    inbound, outbound = FAIRWAY, FAIRWAY[::-1]
    ships = [
        Ship(636092101, "NORDIC HORIZON", 71, (330, 69, 25, 26), "D5AB1", 9800101, 14.2,
             "BEANR", path=inbound, start_m=8_000, sog=14.5),
        Ship(538009102, "ATLANTIC MERIDIAN", 70, (160, 40, 16, 16), "V7CD2", 9700102, 11.8,
             "BEANR", path=inbound, start_m=24_000, sog=12.8),
        Ship(477330103, "EASTERN PROMISE", 70, (320, 79, 29, 32), "VRAB3", 9900103, 15.5,
             "BEANR", path=inbound, start_m=42_000, sog=11.2),
        Ship(255806104, "LUSITANIA TRADER", 79, (120, 30, 10, 13), "CQAB4", 9500104, 7.9,
             "BEANR", path=inbound, start_m=60_000, sog=9.4),
        Ship(219025105, "SKAGEN TANK", 80, (150, 33, 12, 14), "OYAB5", 9600105, 9.6,
             "NLTNZ", path=inbound, start_m=15_000, sog=11.6),
        Ship(211440106, "ELBE CARRIER", 70, (110, 25, 8, 9), "DABC6", 9400106, 6.4,
             "GBFXT", path=outbound, start_m=5_000, sog=13.1),
        Ship(636092107, "MERIDIAN STAR", 71, (310, 90, 30, 31), "D5AB7", 9850107, 13.6,
             "CNSHA", path=outbound, start_m=28_000, sog=15.2),
        Ship(244690108, "ZEEUWSE STROOM", 79, (70, 16, 5, 6), "PBAB8", 9300108, 3.2,
             "NLVLI", path=outbound, start_m=47_000, sog=8.6),
        Ship(205310109, "SCHELDE PRIDE", 84, (190, 40, 17, 18), "ORAB9", 9750109, 10.1,
             "NOSVG", path=outbound, start_m=66_000, sog=12.0),
        Ship(538009110, "CAPE WESTKAPELLE", 80, (200, 44, 20, 20), "V7EF0", 9650110, 12.4,
             "BEANR", anchor=(51.4215, 3.4950), nav_status=1, interval_s=180),
        Ship(477330111, "HONG KONG BRIDGE", 70, (180, 44, 16, 16), "VRCD1", 9720111, 10.9,
             "BEANR", anchor=(51.4175, 3.5150), nav_status=1, interval_s=180),
        Ship(255806112, "MADEIRA SUN", 71, (140, 30, 12, 13), "CQCD2", 9550112, 8.1,
             "BEANR", anchor=(51.4235, 3.5250), nav_status=1, interval_s=180),
        Ship(244690113, "TERNEUZEN", 79, (90, 20, 6, 6), "PBCD3", 9350113, 4.8,
             "NLTNZ", anchor=(51.3480, 3.8200), nav_status=5, interval_s=180),
        Ship(244690114, "LOODS 12", 50, (15, 7, 3, 3), "PBLD1", 0, 1.8, "",
             path=inbound, start_m=21_000, sog=19.5),
        Ship(205310115, "SCHELDESTROOM", 40, (32, 10, 4, 4), "ORFE1", 0, 1.4, "BRESKENS",
             path=[(51.4380, 3.5700), (51.4010, 3.5550)], sog=24.0),
        Ship(244690116, "MULTRATUG 21", 52, (22, 10, 5, 5), "PBTG1", 0, 4.2, "",
             path=inbound, start_m=36_000, sog=7.5),
        Ship(205310117, "VLAANDEREN XX", 33, (98, 22, 11, 11), "ORDR1", 9200117, 6.8, "",
             path=outbound, start_m=40_000, sog=9.0),
        Ship(244690118, "ZEEUWSE VISSER", 30, (28, 8, 4, 4), "PBFS1", 0, 3.1, "",
             path=[(51.3950, 3.3000), (51.4050, 3.2000)], sog=5.5),
        Ship(244700119, "WINDEKIND", 37, (9, 3, 2, 2), "PD3419", cls="B",
             path=[(51.4300, 3.6300), (51.4050, 3.7000)], sog=5.8, interval_s=30),
        Ship(244700120, "ZILVERMEEUW", 36, (10, 3, 2, 2), "PD7788", cls="B",
             path=[(51.4020, 3.5400), (51.4200, 3.4700)], sog=6.4, interval_s=30),
        Ship(205700121, "TIJDGENOOT", 37, (12, 4, 2, 2), "OR4411", cls="B",
             path=[(51.3700, 3.8500), (51.3900, 3.9500)], sog=7.1, interval_s=30),
        # Ver op zee: zorgt voor een afstandsrecord in de demo.
        Ship(636092122, "NORTH SEA VOYAGER", 70, (250, 50, 20, 20), "D5GH2", 9870122, 12.9,
             "GBFXT", path=[(51.6800, 2.7000), (51.7600, 2.5500)], sog=13.5),
    ]
    return ships


def generate(seed: int = 2026) -> list[str]:
    rng = random.Random(seed)
    ships = fleet()
    # Vaste, willekeurige volgorde: zo liggen de meldingen van elk schip steeds
    # ongeveer één tick uit elkaar, ook bij afspelen met een vast tempo.
    rng.shuffle(ships)
    lines = []
    seq = 0
    for t in range(0, DURATION_S, TICK_S):
        batch = []
        for ship in ships:
            if t % STATIC_EVERY_S == ship.mmsi % 3 * TICK_S:
                for bits in ship.static_bits():
                    seq = seq % 9 + 1
                    batch.append(aisenc.sentences(bits, rng.choice("AB"), seq))
            if t % ship.interval_s == 0:
                batch.append(aisenc.sentences(ship.position_bits(t, rng), rng.choice("AB")))
        lines.extend(line for group in batch for line in group)
    return lines


def main() -> None:
    lines = generate()
    rate = len(lines) / DURATION_S
    out = Path(__file__).resolve().parents[1] / "samples" / "sample.nmea"
    header = [
        "# Synthetisch AIS-verkeer op de Westerschelde (verzonnen schepen), gemaakt met tools/make_sample.py",
        f"# {DURATION_S // 60} minuten, {len(lines)} regels: afspelen in echte tijd met --rate {rate:.1f},",
        f"# twee keer zo snel met --rate {2 * rate:.1f}. Veel sneller laat schepen onmogelijk hard varen.",
    ]
    out.write_text("\n".join(header + lines) + "\n", encoding="ascii")
    print(f"{out}: {len(lines)} regels, {rate:.1f} regels/s in echte tijd")


if __name__ == "__main__":
    main()
