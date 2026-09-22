"""Rooktest voor tools/make_demo_db.py: het script moet blijven werken met tracker en opslag."""

import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import make_demo_db

from aisws import stats

TZ = ZoneInfo("Europe/Amsterdam")
END = int(datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc).timestamp())


def test_demo_db_fills_every_statistic(tmp_path):
    path = tmp_path / "demo.db"
    heard, ships = make_demo_db.generate(path, days=2, end=END)
    assert heard > 10_000 and ships > 50

    conn = sqlite3.connect(path)
    try:
        assert all(h["ships"] > 0 for h in stats.hourly(conn, TZ, END, hours=24))
        groups = {s["type_group"] for s in stats.speed(conn, END, period="7d")}
        assert {"cargo", "tanker", "pilot", "pleasure"} <= groups
        assert stats.largest(conn, TZ, END)["current"]["length"] > 100
        assert stats.range_records(conn)["record"]["distance_km"] > 30
        names = [name for (name,) in conn.execute("SELECT name FROM vessels")]
        assert len(names) == len(set(names))
    finally:
        conn.close()
