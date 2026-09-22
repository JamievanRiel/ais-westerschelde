"""Query-functies voor de statistiekenpagina.

Tijden staan in de database als UTC. Lokale dag- en maandgrenzen worden hier
met ``zoneinfo`` omgerekend naar UTC-bereiken, zodat zomer- en wintertijd
kloppen zonder de tijdzone van het proces aan te passen.
"""

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aisws.geo import METERS_PER_NM
from aisws.shiptypes import type_group
from aisws.tracker import DAY_S, SECTOR_DEG

HOUR = 3600
PERIODS = {"7d": 7 * 86400, "30d": 30 * 86400, "all": None}
PASSAGES_BY_TYPE_DAYS = 30
MIN_LENGTH_M, MAX_LENGTH_M = 1, 460


def _local_midnight(day: date, tz: ZoneInfo) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=tz).timestamp())


def _local_date(ts: int, tz: ZoneInfo) -> date:
    return datetime.fromtimestamp(ts, tz).date()


def hourly(conn: sqlite3.Connection, tz: ZoneInfo, now: int, hours: int = 48) -> list[dict[str, Any]]:
    """Aantal schepen per uur, inclusief lege uren."""
    last = now - now % HOUR
    first = last - (hours - 1) * HOUR
    counts = dict(conn.execute(
        "SELECT hour_ts, count(*) FROM vessel_hours WHERE hour_ts BETWEEN ? AND ? GROUP BY hour_ts",
        (first, last),
    ).fetchall())
    return [
        {
            "hour": ts,
            "local": datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%dT%H:%M"),
            "ships": counts.get(ts, 0),
        }
        for ts in range(first, last + 1, HOUR)
    ]


def daily(conn: sqlite3.Connection, tz: ZoneInfo, now: int, days: int = 90) -> list[dict[str, Any]]:
    """Aantal verschillende schepen per lokale dag, inclusief lege dagen."""
    today = _local_date(now, tz)
    result = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        start = _local_midnight(day, tz)
        end = _local_midnight(day + timedelta(days=1), tz)
        (ships,) = conn.execute(
            "SELECT count(DISTINCT mmsi) FROM vessel_hours WHERE hour_ts >= ? AND hour_ts < ?",
            (start, end),
        ).fetchone()
        result.append({"date": day.isoformat(), "ships": ships})
    return result


def speed(conn: sqlite3.Connection, now: int, period: str = "30d") -> list[dict[str, Any]]:
    """Tijdgewogen gemiddelde snelheid (varend, ≥ 1 kn) per scheepstypegroep."""
    if period not in PERIODS:
        raise ValueError(f"onbekende periode {period!r}")
    window = PERIODS[period]
    since = 0 if window is None else now - window
    rows = conn.execute(
        """SELECT v.ship_type, sum(h.speed_sum), sum(h.speed_samples)
           FROM vessel_hours h LEFT JOIN vessels v ON v.mmsi = h.mmsi
           WHERE h.hour_ts >= ? AND h.speed_samples > 0
           GROUP BY v.ship_type""",
        (since,),
    ).fetchall()
    groups: dict[str, list[float]] = {}
    for ship_type, total, samples in rows:
        group = groups.setdefault(type_group(ship_type), [0.0, 0])
        group[0] += total
        group[1] += samples
    result = [
        {"type_group": name, "avg_kn": round(total / samples, 1), "samples": samples}
        for name, (total, samples) in groups.items()
    ]
    return sorted(result, key=lambda row: (-row["avg_kn"], row["type_group"]))


def heatmap(conn: sqlite3.Connection, tz: ZoneInfo, now: int, weeks: int = 8) -> dict[str, Any]:
    """Gemiddeld aantal schepen per (weekdag, lokaal uur); maandag = 0.

    Er wordt alleen gemiddeld over uren met data: uitval van het station telt
    niet als een rustig uur.
    """
    # Het lopende uur telt niet mee: dat is nog niet compleet.
    rows = conn.execute(
        "SELECT hour_ts, count(*) FROM vessel_hours WHERE hour_ts >= ? AND hour_ts < ? GROUP BY hour_ts",
        (now - weeks * 7 * 86400, now - now % HOUR),
    ).fetchall()
    sums = [[0] * 24 for _ in range(7)]
    counts = [[0] * 24 for _ in range(7)]
    for hour_ts, ships in rows:
        local = datetime.fromtimestamp(hour_ts, tz)
        sums[local.weekday()][local.hour] += ships
        counts[local.weekday()][local.hour] += 1
    cells = [
        [round(sums[d][h] / counts[d][h], 1) if counts[d][h] else None for h in range(24)]
        for d in range(7)
    ]
    slots = [
        {"weekday": d, "hour": h, "avg": cells[d][h]}
        for d in range(7) for h in range(24) if cells[d][h] is not None
    ]
    top = sorted(slots, key=lambda slot: (-slot["avg"], slot["weekday"], slot["hour"]))[:3]
    return {"cells": cells, "top": top}


def _month_bounds(year: int, month: int, tz: ZoneInfo) -> tuple[int, int]:
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return _local_midnight(date(year, month, 1), tz), _local_midnight(date(next_year, next_month, 1), tz)


def _largest_in(conn: sqlite3.Connection, start: int, end: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT v.mmsi, v.name, v.length_m, v.beam_m, v.ship_type, min(h.hour_ts) AS seen
           FROM vessel_hours h JOIN vessels v ON v.mmsi = h.mmsi
           WHERE h.hour_ts >= ? AND h.hour_ts < ? AND v.length_m BETWEEN ? AND ?
           GROUP BY v.mmsi
           ORDER BY v.length_m DESC, v.beam_m DESC, seen
           LIMIT 1""",
        (start, end, MIN_LENGTH_M, MAX_LENGTH_M),
    ).fetchone()
    if row is None:
        return None
    mmsi, name, length, beam, ship_type, seen = row
    return {
        "mmsi": mmsi, "name": name, "length": length, "beam": beam,
        "ship_type": ship_type, "type_group": type_group(ship_type), "seen": seen,
    }


def largest(conn: sqlite3.Connection, tz: ZoneInfo, now: int, months: int = 12) -> dict[str, Any]:
    """Grootste schip van deze lokale maand en de winnaars van de ``months`` maanden ervoor."""
    today = _local_date(now, tz)
    year, month = today.year, today.month
    current = None
    previous = []
    for index in range(months + 1):
        start, end = _month_bounds(year, month, tz)
        winner = _largest_in(conn, start, end)
        if winner is not None:
            winner = {"month": f"{year:04d}-{month:02d}", **winner}
            if index == 0:
                current = winner
            else:
                previous.append(winner)
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return {"current": current, "previous": previous}


def range_records(conn: sqlite3.Connection) -> dict[str, Any]:
    """Het record voor de verste ontvangst en alle eerdere records."""
    rows = conn.execute(
        """SELECT r.ts, r.mmsi, r.lat, r.lon, r.distance_m, v.name, v.ship_type
           FROM range_records r LEFT JOIN vessels v ON v.mmsi = r.mmsi
           ORDER BY r.ts"""
    ).fetchall()
    history = [
        {
            "ts": ts, "mmsi": mmsi, "lat": lat, "lon": lon,
            "distance_km": round(distance / 1000, 2),
            "distance_nm": round(distance / METERS_PER_NM, 2),
            "name": name, "type_group": type_group(ship_type),
        }
        for ts, mmsi, lat, lon, distance, name, ship_type in rows
    ]
    # Kiezen op de onafgeronde afstand: afgerond zijn twee records binnen 10 m gelijk.
    distances = [row[4] for row in rows]
    record = history[distances.index(max(distances))] if rows else None
    return {"record": record, "history": history}


def coverage(conn: sqlite3.Connection, now: int, period: str = "30d") -> list[dict[str, Any]]:
    """Verste gecontroleerde ontvangst per richting van 10° vanaf het station.

    Altijd 36 sectoren, met ``None`` waar niets ontvangen is. Een periode telt
    in hele UTC-dagen, zo zijn de sectoren opgeslagen.
    """
    if period not in PERIODS:
        raise ValueError(f"onbekende periode {period!r}")
    window = PERIODS[period]
    since = 0 if window is None else (now - window) - (now - window) % DAY_S
    # Bij max() geeft SQLite de overige kolommen van de rij met het maximum.
    rows = conn.execute(
        """SELECT s.sector, max(s.max_range_m), s.mmsi, v.name
           FROM range_sectors s LEFT JOIN vessels v ON v.mmsi = s.mmsi
           WHERE s.day_ts >= ?
           GROUP BY s.sector""",
        (since,),
    ).fetchall()
    found = {sector: (distance, mmsi, name) for sector, distance, mmsi, name in rows}
    result = []
    for sector in range(360 // SECTOR_DEG):
        distance, mmsi, name = found.get(sector, (None, None, None))
        result.append({
            "sector": sector,
            "from_deg": sector * SECTOR_DEG,
            "max_km": None if distance is None else round(distance / 1000, 2),
            "mmsi": mmsi,
            "name": name,
        })
    return result


def passages(conn: sqlite3.Connection, tz: ZoneInfo, now: int, days: int = 90) -> dict[str, Any]:
    """Kruisingen van de doorvaartlijn: per lokale dag, vandaag, en per type over 30 dagen."""
    today = _local_date(now, tz)
    start = _local_midnight(today - timedelta(days=days - 1), tz)
    per_day: dict[date, dict[str, int]] = {}
    for ts, upstream in conn.execute("SELECT ts, upstream FROM gate_crossings WHERE ts >= ?", (start,)):
        counts = per_day.setdefault(_local_date(ts, tz), {"up": 0, "down": 0})
        counts["up" if upstream else "down"] += 1

    since = _local_midnight(today - timedelta(days=PASSAGES_BY_TYPE_DAYS - 1), tz)
    groups: dict[str, dict[str, int]] = {}
    for ship_type, upstream, count in conn.execute(
        """SELECT v.ship_type, c.upstream, count(*)
           FROM gate_crossings c LEFT JOIN vessels v ON v.mmsi = c.mmsi
           WHERE c.ts >= ?
           GROUP BY v.ship_type, c.upstream""",
        (since,),
    ):
        counts = groups.setdefault(type_group(ship_type), {"up": 0, "down": 0})
        counts["up" if upstream else "down"] += count

    empty = {"up": 0, "down": 0}
    return {
        "days": [
            {"date": day.isoformat(), **per_day.get(day, empty)}
            for day in (today - timedelta(days=offset) for offset in range(days - 1, -1, -1))
        ],
        "today": dict(per_day.get(today, empty)),
        "by_type": sorted(
            ({"type_group": name, **counts} for name, counts in groups.items()),
            key=lambda row: (-(row["up"] + row["down"]), row["type_group"]),
        ),
    }
