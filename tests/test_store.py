import sqlite3

import pytest

from aisws.store import Store
from aisws.tracker import HourSample, RangeRecord, TrackPoint, VesselUpdate

MMSI = 244000001


def point(ts, mmsi=MMSI, lat=51.40, lon=3.60):
    return TrackPoint(mmsi, ts, lat, lon, 10.0, 90.0, 90, 0)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "ais.db")
    yield s
    s.close()


def rows(store, sql):
    with store.reader() as conn:
        return [tuple(row) for row in conn.execute(sql)]


def test_database_uses_wal_and_survives_reopening(tmp_path):
    first = Store(tmp_path / "ais.db")
    first.add([point(100)])
    assert first.flush()
    first.close()
    second = Store(tmp_path / "ais.db")
    assert rows(second, "SELECT mmsi, ts FROM positions") == [(MMSI, 100)]
    assert rows(second, "PRAGMA journal_mode") == [("wal",)]
    second.close()


def test_track_points_are_written(store):
    store.add([point(100), point(130, lat=51.41)])
    store.flush()
    assert rows(store, "SELECT ts, lat, lon, sog, cog, heading, nav_status FROM positions ORDER BY ts") == [
        (100, 51.40, 3.60, 10.0, 90.0, 90, 0),
        (130, 51.41, 3.60, 10.0, 90.0, 90, 0),
    ]


def test_hour_samples_are_summed_within_and_across_flushes(store):
    store.add([HourSample(3600, MMSI, 12.0, 5000.0), HourSample(3600, MMSI, None, 7000.0)])
    store.flush()
    store.add([HourSample(3600, MMSI, 10.0, 6000.0), HourSample(7200, MMSI, None, 100.0)])
    store.flush()
    assert rows(store, "SELECT * FROM vessel_hours ORDER BY hour_ts") == [
        (3600, MMSI, 3, 2, 22.0, 7000.0),
        (7200, MMSI, 1, 0, 0.0, 100.0),
    ]


def test_vessel_rows_keep_first_seen_and_do_not_lose_static_data(store):
    store.add([VesselUpdate(MMSI, 100, "A", {"name": "SCHELDE", "ship_type": 70, "length": 180})])
    store.flush()
    store.add([VesselUpdate(MMSI, 50, "A", {}), VesselUpdate(MMSI, 200, "A", {"beam": 28})])
    store.add([VesselUpdate(MMSI, 150, "A", {"name": "SCHELDE II"})])
    store.flush()
    assert rows(store, "SELECT mmsi, name, ship_type, length_m, beam_m, first_seen, last_seen FROM vessels") == [
        (MMSI, "SCHELDE II", 70, 180, 28, 50, 200),
    ]


def test_range_records_are_written(store):
    store.add([RangeRecord(100, MMSI, 51.8, 2.8, 67_000.0)])
    store.flush()
    assert store.load_record() == RangeRecord(100, MMSI, 51.8, 2.8, 67_000.0)


def test_latest_record_is_loaded(store):
    store.add([RangeRecord(100, 1, 51.8, 2.8, 60_000.0), RangeRecord(200, 2, 52.0, 2.6, 90_000.0)])
    store.flush()
    assert store.load_record().mmsi == 2


def test_no_record_yet(store):
    assert store.load_record() is None


def test_failed_flush_keeps_the_data_for_the_next_attempt(tmp_path):
    store = Store(tmp_path / "ais.db", busy_timeout_s=0)
    blocker = sqlite3.connect(tmp_path / "ais.db", isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    store.add([point(100), HourSample(0, MMSI, 5.0, 10.0)])
    assert store.flush() is False
    assert store.flush_errors == 1
    store.add([point(130), HourSample(0, MMSI, 7.0, 20.0)])
    blocker.execute("ROLLBACK")
    blocker.close()
    assert store.flush() is True
    assert rows(store, "SELECT ts FROM positions ORDER BY ts") == [(100,), (130,)]
    assert rows(store, "SELECT reports, speed_samples, speed_sum, max_range_m FROM vessel_hours") == [
        (2, 2, 12.0, 20.0)
    ]
    store.close()


def test_oldest_track_points_are_dropped_above_the_limit(tmp_path):
    store = Store(tmp_path / "ais.db", max_pending_writes=3)
    store.add([point(ts) for ts in (1, 2, 3, 4, 5)])
    assert store.writes_dropped == 2
    store.flush()
    assert rows(store, "SELECT ts FROM positions ORDER BY ts") == [(3,), (4,), (5,)]
    store.close()


def test_cleanup_deletes_old_positions_only(store):
    store.add([point(100), point(200), point(300), HourSample(0, MMSI, None, 1.0)])
    store.flush()
    assert store.cleanup(before_ts=200) == 1
    assert rows(store, "SELECT ts FROM positions ORDER BY ts") == [(200,), (300,)]
    assert rows(store, "SELECT count(*) FROM vessel_hours") == [(1,)]


def test_load_live_returns_latest_recent_position_with_static_data(store):
    store.add([
        VesselUpdate(MMSI, 10, "A", {"name": "SCHELDE", "ship_type": 70, "length": 180}),
        point(100), point(400, lat=51.45),
        VesselUpdate(2, 10, "B", {}), point(50, mmsi=2),
    ])
    store.flush()
    [vessel] = store.load_live(since_ts=300)
    assert vessel == {
        "mmsi": MMSI, "ais_class": "A", "first_seen": 10, "last_seen": 10,
        "name": "SCHELDE", "callsign": None, "imo": None, "ship_type": 70,
        "length": 180, "beam": None, "draught": None, "destination": None,
        "lat": 51.45, "lon": 3.60, "sog": 10.0, "cog": 90.0, "heading": 90,
        "nav_status": 0, "position_ts": 400,
    }


def test_lookup_static_returns_known_fields(store):
    store.add([VesselUpdate(MMSI, 10, "A", {"name": "SCHELDE", "draught": 7.5})])
    store.flush()
    assert store.lookup_static(MMSI) == {"name": "SCHELDE", "draught": 7.5}
    assert store.lookup_static(999) is None


def test_reader_is_read_only(store):
    with store.reader() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM positions")


def test_flush_of_an_empty_buffer_is_a_no_op(store):
    assert store.flush() is True


def record_rows(store):
    return rows(store, "SELECT ts, mmsi, distance_m FROM range_records ORDER BY ts")


def test_same_ship_improving_its_record_updates_one_row(store):
    store.add([RangeRecord(100, 7, 51.8, 2.8, 60_000.0)])
    store.flush()
    store.add([RangeRecord(130, 7, 51.81, 2.79, 61_000.0), RangeRecord(160, 7, 51.82, 2.78, 62_000.0)])
    store.flush()
    assert record_rows(store) == [(160, 7, 62_000.0)]


def test_another_ship_breaking_the_record_adds_a_row(store):
    store.add([RangeRecord(100, 7, 51.8, 2.8, 60_000.0), RangeRecord(200, 8, 52.0, 2.6, 70_000.0)])
    store.flush()
    assert record_rows(store) == [(100, 7, 60_000.0), (200, 8, 70_000.0)]


def test_same_ship_on_a_later_voyage_adds_a_row(store):
    store.add([RangeRecord(100, 7, 51.8, 2.8, 60_000.0)])
    store.flush()
    store.add([RangeRecord(100 + 3601, 7, 51.9, 2.7, 65_000.0)])
    store.flush()
    assert record_rows(store) == [(100, 7, 60_000.0), (3701, 7, 65_000.0)]


def test_close_waits_for_a_flush_that_is_still_running(tmp_path):
    import threading
    import time

    store = Store(tmp_path / "ais.db")
    store.add([point(100)])
    real_write = store._write

    def slow_write(batch):
        time.sleep(0.2)  # schrijven duurt even, net als op een trage SD-kaart
        real_write(batch)

    store._write = slow_write
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("ok", store.flush()))
    worker.start()
    time.sleep(0.05)
    store.close()
    worker.join()
    assert result["ok"] is True
    assert store.flush_errors == 0
