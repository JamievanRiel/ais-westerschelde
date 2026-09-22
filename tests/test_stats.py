import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from aisws import stats
from aisws.store import Store

TZ = ZoneInfo("Europe/Amsterdam")


def utc(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / "ais.db")
    conn = sqlite3.connect(tmp_path / "ais.db", isolation_level=None)

    def hours(*rows):
        """(hour_ts, mmsi[, speed_samples, speed_sum])"""
        for row in rows:
            hour_ts, mmsi, samples, total = (*row, 0, 0.0)[:4]
            conn.execute(
                "INSERT INTO vessel_hours VALUES (?, ?, 1, ?, ?, 0)", (hour_ts, mmsi, samples, total)
            )

    def vessel(mmsi, name=None, ship_type=None, length=None, beam=None):
        conn.execute(
            "INSERT INTO vessels (mmsi, name, ship_type, length_m, beam_m, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, 0, 0)",
            (mmsi, name, ship_type, length, beam),
        )

    def record(ts, mmsi, lat, lon, distance_m):
        conn.execute("INSERT INTO range_records VALUES (?, ?, ?, ?, ?)", (ts, mmsi, lat, lon, distance_m))

    class Db:
        pass

    helpers = Db()
    helpers.hours, helpers.vessel, helpers.record = hours, vessel, record
    with store.reader() as reader:
        helpers.reader = reader
        yield helpers
    conn.close()
    store.close()


def test_hourly_counts_ships_per_hour_including_empty_hours(db):
    db.hours((utc(2026, 9, 21, 9), 1), (utc(2026, 9, 21, 11), 1), (utc(2026, 9, 21, 11), 2),
             (utc(2026, 9, 21, 12), 3))
    result = stats.hourly(db.reader, TZ, now=utc(2026, 9, 21, 12, 30), hours=3)
    assert result == [
        {"hour": utc(2026, 9, 21, 10), "local": "2026-09-21T12:00", "ships": 0},
        {"hour": utc(2026, 9, 21, 11), "local": "2026-09-21T13:00", "ships": 2},
        {"hour": utc(2026, 9, 21, 12), "local": "2026-09-21T14:00", "ships": 1},
    ]


def test_daily_counts_distinct_ships_per_local_day_across_the_dst_change(db):
    # 25 oktober 2026 duurt lokaal 25 uur: 24-10 22:00Z t/m 25-10 23:00Z.
    db.hours(
        (utc(2026, 10, 24, 21), 4),  # 24-10 23:00 CEST
        (utc(2026, 10, 24, 22), 1),  # 25-10 00:00 CEST
        (utc(2026, 10, 25, 5), 1),   # zelfde schip, zelfde dag
        (utc(2026, 10, 25, 22), 2),  # 25-10 23:00 CET
        (utc(2026, 10, 25, 23), 3),  # 26-10 00:00 CET
    )
    result = stats.daily(db.reader, TZ, now=utc(2026, 10, 26, 12), days=3)
    assert result == [
        {"date": "2026-10-24", "ships": 1},
        {"date": "2026-10-25", "ships": 2},
        {"date": "2026-10-26", "ships": 1},
    ]


def test_speed_is_the_sample_weighted_average_per_type_group(db):
    now = utc(2026, 9, 21, 12)
    db.vessel(1, ship_type=70)
    db.vessel(2, ship_type=79)
    db.vessel(3, ship_type=80)
    db.vessel(4)
    db.vessel(5, ship_type=37)
    db.hours(
        (now - 3600, 1, 2, 24.0),        # vracht: 12 kn × 2
        (now - 7200, 2, 1, 15.0),        # vracht: 15 kn × 1
        (now - 3600, 3, 4, 40.0),        # tanker: 10 kn × 4
        (now - 3600, 4, 1, 8.0),         # onbekend
        (now - 3600, 5, 0, 0.0),         # geen monsters
        (now - 10 * 86400, 3, 1, 20.0),  # buiten 7 dagen
    )
    assert stats.speed(db.reader, now=now, period="7d") == [
        {"type_group": "cargo", "avg_kn": 13.0, "samples": 3},
        {"type_group": "tanker", "avg_kn": 10.0, "samples": 4},
        {"type_group": "unknown", "avg_kn": 8.0, "samples": 1},
    ]
    tanker_all = [g for g in stats.speed(db.reader, now=now, period="all") if g["type_group"] == "tanker"]
    assert tanker_all == [{"type_group": "tanker", "avg_kn": 12.0, "samples": 5}]


def test_speed_rejects_unknown_periods(db):
    with pytest.raises(ValueError):
        stats.speed(db.reader, now=0, period="1y")


def test_heatmap_averages_only_over_hours_with_data(db):
    # Maandag 10:00 lokaal (08:00Z): week 1 drie schepen, week 2 één schip, week 3 uitval.
    db.hours(
        (utc(2026, 9, 7, 8), 1), (utc(2026, 9, 7, 8), 2), (utc(2026, 9, 7, 8), 3),
        (utc(2026, 9, 14, 8), 1),
        (utc(2026, 9, 19, 20), 1),  # zaterdag 22:00
        (utc(2026, 9, 20, 13), 1), (utc(2026, 9, 20, 13), 2),  # zondag 15:00
        (utc(2026, 8, 10, 8), 9),  # buiten 3 weken
    )
    result = stats.heatmap(db.reader, TZ, now=utc(2026, 9, 21, 12), weeks=3)
    assert result["cells"][0][10] == 2.0
    assert result["cells"][5][22] == 1.0
    assert result["cells"][6][15] == 2.0
    assert result["cells"][0][11] is None
    assert result["top"] == [
        {"weekday": 0, "hour": 10, "avg": 2.0},
        {"weekday": 6, "hour": 15, "avg": 2.0},
        {"weekday": 5, "hour": 22, "avg": 1.0},
    ]


def test_largest_ship_per_local_month(db):
    db.vessel(1, "MIDDEL", 70, 200, 32)
    db.vessel(2, "GROOT", 70, 400, 59)
    db.vessel(3, "EVEN GROOT BREDER", 80, 400, 61)
    db.vessel(4, "ONMOGELIJK", 70, 511, 63)
    db.vessel(5, "ONBEKEND")
    db.vessel(6, "AUGUSTUS", 70, 300, 48)
    db.hours(
        (utc(2026, 9, 2, 10), 1), (utc(2026, 9, 3, 10), 2), (utc(2026, 9, 5, 10), 3),
        (utc(2026, 9, 3, 10), 3), (utc(2026, 9, 3, 11), 4), (utc(2026, 9, 3, 11), 5),
        (utc(2026, 8, 31, 22), 6),  # lokaal 1 september 00:00
        (utc(2026, 8, 20, 10), 6),
    )
    result = stats.largest(db.reader, TZ, now=utc(2026, 9, 21, 12), months=2)
    assert result["current"] == {
        "month": "2026-09", "mmsi": 3, "name": "EVEN GROOT BREDER", "length": 400, "beam": 61,
        "ship_type": 80, "type_group": "tanker", "seen": utc(2026, 9, 3, 10),
    }
    assert [(m["month"], m["name"]) for m in result["previous"]] == [("2026-08", "AUGUSTUS")]
    assert result["previous"][0]["seen"] == utc(2026, 8, 20, 10)


def test_largest_without_data(db):
    assert stats.largest(db.reader, TZ, now=utc(2026, 9, 21, 12), months=2) == {
        "current": None, "previous": [],
    }


def test_range_record_and_history(db):
    db.vessel(7, "VER WEG", 70, 300, 48)
    db.record(100, 8, 51.6, 3.0, 40_000.0)
    db.record(200, 7, 51.8, 2.8, 67_072.0)
    result = stats.range_records(db.reader)
    assert result["record"] == {
        "ts": 200, "mmsi": 7, "lat": 51.8, "lon": 2.8, "distance_km": 67.07,
        "distance_nm": 36.22, "name": "VER WEG", "type_group": "cargo",
    }
    assert [r["mmsi"] for r in result["history"]] == [8, 7]


def test_range_record_is_chosen_before_rounding(db):
    # 111,7252 en 111,7346 km zijn afgerond allebei 111,73; het nieuwste record is het verste.
    db.record(100, 8, 51.3, 1.99, 111_725.2)
    db.record(200, 7, 51.3, 1.98, 111_734.6)
    assert stats.range_records(db.reader)["record"]["mmsi"] == 7


def test_no_range_record_yet(db):
    assert stats.range_records(db.reader) == {"record": None, "history": []}


def test_largest_looks_back_the_requested_number_of_previous_months(db):
    db.vessel(6, "AUGUSTUS", 70, 300, 48)
    db.hours((utc(2026, 8, 20, 10), 6))
    result = stats.largest(db.reader, TZ, now=utc(2026, 9, 21, 12), months=1)
    assert [m["month"] for m in result["previous"]] == ["2026-08"]


def test_heatmap_ignores_the_hour_that_is_still_running(db):
    now = utc(2026, 9, 21, 12, 30)  # maandag 14:30 lokaal
    db.hours((utc(2026, 9, 14, 12), 1), (utc(2026, 9, 14, 12), 2), (utc(2026, 9, 21, 12), 3))
    result = stats.heatmap(db.reader, TZ, now=now, weeks=2)
    assert result["cells"][0][14] == 2.0
