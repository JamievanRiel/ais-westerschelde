"""Live toestand per schip, uitdunnen van sporen, plausibiliteit en records.

Pure logica zonder I/O: ``handle`` krijgt een bericht plus ontvangsttijd en
geeft events terug die de opslag verwerkt. Wijzigingen voor de live kaart
worden apart verzameld en met ``drain_changes`` opgehaald.
"""

from collections.abc import Callable
from dataclasses import dataclass, fields
from math import cos, radians
from typing import Any

from aisws.config import GateConfig, TrackerConfig
from aisws.geo import MPS_PER_KNOT, bearing_deg, haversine_m
from aisws.messages import PositionReport, StaticData
from aisws.shiptypes import type_group

FORGET_AFTER_S = 86_400
DAY_S = 86_400
SECTOR_DEG = 10
JITTER_M = 100.0
MAX_REJECTIONS = 3

STATIC_FIELDS = ("name", "callsign", "imo", "ship_type", "length", "beam", "draught", "destination")


@dataclass(frozen=True)
class VesselUpdate:
    mmsi: int
    ts: int
    ais_class: str
    static: dict[str, Any]


@dataclass(frozen=True)
class TrackPoint:
    mmsi: int
    ts: int
    lat: float
    lon: float
    sog: float | None
    cog: float | None
    heading: int | None
    nav_status: int | None


@dataclass(frozen=True)
class HourSample:
    hour_ts: int
    mmsi: int
    speed: float | None
    distance_m: float


@dataclass(frozen=True)
class RangeRecord:
    ts: int
    mmsi: int
    lat: float
    lon: float
    distance_m: float


@dataclass(frozen=True)
class SectorSample:
    """Afstand van een gecontroleerde positie, per UTC-dag en richting vanaf het station."""

    day_ts: int
    sector: int  # 0..35, sector 0 = 0–10°
    mmsi: int
    distance_m: float


@dataclass(frozen=True)
class GateCrossing:
    ts: int
    mmsi: int
    upstream: bool  # True = de Schelde op


Event = VesselUpdate | TrackPoint | HourSample | RangeRecord | SectorSample | GateCrossing


@dataclass
class VesselState:
    mmsi: int
    ais_class: str
    first_seen: float
    last_seen: float
    lat: float | None = None
    lon: float | None = None
    sog: float | None = None
    cog: float | None = None
    heading: int | None = None
    nav_status: int | None = None
    position_ts: float | None = None
    name: str | None = None
    callsign: str | None = None
    imo: int | None = None
    ship_type: int | None = None
    length: int | None = None
    beam: int | None = None
    draught: float | None = None
    destination: str | None = None
    last_track_ts: float | None = None
    last_track_cog: float | None = None
    last_track_nav_status: int | None = None
    last_speed_sample_ts: float | None = None
    rejections: int = 0
    live: bool = False
    gate_side: int = 0  # kant van de doorvaartlijn: +1 links, -1 rechts, 0 onbekend

    def apply_static(self, values: dict[str, Any]) -> dict[str, Any]:
        changed = {}
        for key in STATIC_FIELDS:
            value = values.get(key)
            if value is not None:
                setattr(self, key, value)
                changed[key] = value
        return changed

    def as_live(self) -> dict[str, Any]:
        return {
            "mmsi": self.mmsi,
            "name": self.name,
            "type_group": type_group(self.ship_type),
            "lat": self.lat,
            "lon": self.lon,
            "sog": self.sog,
            "cog": self.cog,
            "heading": self.heading,
            "nav_status": self.nav_status,
            "length": self.length,
            "last_seen": int(self.position_ts or 0),
        }


def _angle_diff(a: float, b: float) -> float:
    diff = abs(a - b) % 360
    return min(diff, 360 - diff)


def _cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Kruisproduct (a − o) × (b − o): positief als b links ligt van de lijn o → a."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


class Tracker:
    def __init__(
        self,
        config: TrackerConfig,
        station_lat: float,
        station_lon: float,
        static_lookup: Callable[[int], dict[str, Any] | None] | None = None,
        gate: GateConfig | None = None,
    ) -> None:
        self.config = config
        self.station = (station_lat, station_lon)
        self.static_lookup = static_lookup
        self.gate = gate
        # Vlak met x = lon · cos(breedte), zodat de lijn over korte afstand recht blijft.
        self._scale = cos(radians((gate.lat1 + gate.lat2) / 2)) if gate else 1.0
        self._gate_points = (self._xy(gate.lat1, gate.lon1), self._xy(gate.lat2, gate.lon2)) if gate else None
        self.record: RangeRecord | None = None
        self.implausible = 0
        self._vessels: dict[int, VesselState] = {}
        self._changed: set[int] = set()
        self._removed: set[int] = set()

    # --- invoer -----------------------------------------------------------

    def handle(self, message: PositionReport | StaticData, now: float) -> list[Event]:
        if isinstance(message, PositionReport):
            return self._handle_position(message, now)
        return self._handle_static(message, now)

    def _state(self, mmsi: int, ais_class: str, now: float) -> VesselState:
        state = self._vessels.get(mmsi)
        if state is None:
            state = VesselState(mmsi, ais_class, first_seen=now, last_seen=now)
            if self.static_lookup is not None:
                state.apply_static(self.static_lookup(mmsi) or {})
            self._vessels[mmsi] = state
        return state

    def _handle_static(self, message: StaticData, now: float) -> list[Event]:
        state = self._state(message.mmsi, message.ais_class, now)
        state.ais_class = message.ais_class
        state.last_seen = now
        changed = state.apply_static({key: getattr(message, key) for key in STATIC_FIELDS})
        if state.live:
            self._changed.add(state.mmsi)
        return [VesselUpdate(message.mmsi, int(now), message.ais_class, changed)]

    def _handle_position(self, report: PositionReport, now: float) -> list[Event]:
        cfg = self.config
        distance = haversine_m(*self.station, report.lat, report.lon)
        if distance > cfg.max_range_km * 1000:
            self.implausible += 1
            return []

        state = self._state(report.mmsi, report.ais_class, now)
        previous = (state.lat, state.lon)
        side = self._gate_side(report.lat, report.lon)
        speed_checked = False
        if state.position_ts is not None and now - state.position_ts <= cfg.stale_after_s:
            moved = haversine_m(state.lat, state.lon, report.lat, report.lon)
            implied_kn = moved / max(now - state.position_ts, 1.0) / MPS_PER_KNOT
            if moved > JITTER_M and implied_kn > cfg.max_implied_speed_kn:
                if state.rejections < MAX_REJECTIONS:
                    state.rejections += 1
                    self.implausible += 1
                    return []
            else:
                speed_checked = True
        state.rejections = 0

        state.ais_class = report.ais_class
        state.lat, state.lon = report.lat, report.lon
        state.sog, state.cog, state.heading = report.sog, report.cog, report.heading
        state.nav_status = report.nav_status
        state.position_ts = state.last_seen = now
        state.live = True
        self._changed.add(state.mmsi)
        self._removed.discard(state.mmsi)

        ts = int(now)
        events: list[Event] = [VesselUpdate(state.mmsi, ts, state.ais_class, {})]
        if self._wants_track_point(state, now):
            state.last_track_ts = now
            state.last_track_cog = report.cog
            state.last_track_nav_status = report.nav_status
            events.append(
                TrackPoint(state.mmsi, ts, report.lat, report.lon, report.sog,
                           report.cog, report.heading, report.nav_status)
            )

        speed = None
        if report.sog is not None and report.sog >= cfg.moving_sog_kn and (
            state.last_speed_sample_ts is None
            or now - state.last_speed_sample_ts >= cfg.speed_sample_interval_s
        ):
            speed = report.sog
            state.last_speed_sample_ts = now
        events.append(HourSample(ts - ts % 3600, state.mmsi, speed, distance))

        if speed_checked:
            sector = int(bearing_deg(*self.station, report.lat, report.lon) // SECTOR_DEG) % (360 // SECTOR_DEG)
            events.append(SectorSample(ts - ts % DAY_S, sector, state.mmsi, distance))
            if (
                side and state.gate_side and side != state.gate_side
                and report.sog is not None and report.sog >= cfg.moving_sog_kn
                and self._through_gate(previous, (report.lat, report.lon))
            ):
                events.append(GateCrossing(ts, state.mmsi, upstream=side > 0))
            if self.record is None or distance > self.record.distance_m:
                self.record = RangeRecord(ts, state.mmsi, report.lat, report.lon, distance)
                events.append(self.record)
        if side:
            state.gate_side = side
        return events

    def _xy(self, lat: float, lon: float) -> tuple[float, float]:
        return lon * self._scale, lat

    def _gate_side(self, lat: float, lon: float) -> int:
        """+1 links van de lijn (gezien van punt 1 naar punt 2), -1 rechts, 0 erop of geen lijn.

        Van rechts naar links is de Schelde op. Een positie precies op de lijn
        (AIS rekent in 1/600000°, dus dat kan) verandert de onthouden kant niet:
        een schip dat de lijn raakt en terugdraait, telt niet.
        """
        if self._gate_points is None:
            return 0
        p1, p2 = self._gate_points
        c = _cross(p1, p2, self._xy(lat, lon))
        return (c > 0) - (c < 0)

    def _through_gate(self, before: tuple[float, float], after: tuple[float, float]) -> bool:
        """Snijdt het stuk van ``before`` naar ``after`` de lijn tussen haar eindpunten?"""
        p1, p2 = self._gate_points
        a, b = self._xy(*before), self._xy(*after)
        return _cross(a, b, p1) * _cross(a, b, p2) <= 0

    def _wants_track_point(self, state: VesselState, now: float) -> bool:
        cfg = self.config
        if state.last_track_ts is None or state.nav_status != state.last_track_nav_status:
            return True
        moving = state.sog is not None and state.sog >= cfg.stationary_sog_kn
        if (
            moving
            and state.cog is not None
            and state.last_track_cog is not None
            and _angle_diff(state.cog, state.last_track_cog) > cfg.track_cog_change_deg
        ):
            return True
        interval = cfg.track_interval_moving_s if moving else cfg.track_interval_stationary_s
        return now - state.last_track_ts >= interval

    # --- onderhoud ----------------------------------------------------------

    def sweep(self, now: float) -> None:
        """Haalt schepen zonder recente positie van de kaart; vergeet ze na een dag."""
        for mmsi, state in list(self._vessels.items()):
            if state.live and now - (state.position_ts or 0) > self.config.stale_after_s:
                state.live = False
                self._removed.add(mmsi)
                self._changed.discard(mmsi)
            if now - state.last_seen > FORGET_AFTER_S:
                del self._vessels[mmsi]

    def restore(self, vessels: list[dict[str, Any]], record: RangeRecord | None) -> None:
        """Zet de toestand van vóór een herstart terug (uit de database)."""
        known = {f.name for f in fields(VesselState)}
        for row in vessels:
            values = {key: value for key, value in row.items() if key in known}
            position_ts = values.get("position_ts")
            values.setdefault("first_seen", position_ts)
            values.setdefault("last_seen", position_ts)
            state = VesselState(**values)
            state.live = position_ts is not None
            if state.lat is not None and state.lon is not None:
                state.gate_side = self._gate_side(state.lat, state.lon)
            self._vessels[state.mmsi] = state
        self.record = record

    # --- uitvoer ------------------------------------------------------------

    def live_vessels(self) -> list[dict[str, Any]]:
        return [state.as_live() for state in self._vessels.values() if state.live]

    def vessel(self, mmsi: int) -> VesselState | None:
        return self._vessels.get(mmsi)

    def drain_changes(self) -> tuple[set[int], set[int]]:
        changed, removed = self._changed, self._removed
        self._changed, self._removed = set(), set()
        return changed, removed
