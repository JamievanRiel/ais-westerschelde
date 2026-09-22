import os
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from aisws.backup import BackupJob
from aisws.store import SCHEMA_VERSION, BackupError, Store
from aisws.tracker import GateCrossing, HourSample, RangeRecord, SectorSample, TrackPoint, VesselUpdate

DAY = date(2026, 10, 1)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "ais.db")
    s.add([
        VesselUpdate(7, 100, "A", {"name": "BEWAARD", "length": 200}),
        TrackPoint(7, 100, 51.40, 3.60, 10.0, 90.0, 90, 0),
        HourSample(0, 7, 10.0, 5_000.0),
        RangeRecord(100, 7, 51.40, 3.60, 5_000.0),
        SectorSample(0, 16, 7, 5_000.0),
        GateCrossing(100, 7, True),
    ])
    s.flush()
    yield s
    s.close()


@pytest.fixture
def stick(tmp_path):
    path = tmp_path / "stick"
    path.mkdir()
    return path


def table(path, sql):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_backup_holds_everything_except_the_tracks(store, stick):
    target = store.backup(stick, DAY, keep=7)
    assert target == stick / "ais-2026-10-01.db"
    assert table(target, "SELECT name FROM vessels") == [("BEWAARD",)]
    assert table(target, "SELECT count(*) FROM vessel_hours") == [(1,)]
    assert table(target, "SELECT count(*) FROM range_records") == [(1,)]
    assert table(target, "SELECT sector, mmsi FROM range_sectors") == [(16, 7)]
    assert table(target, "SELECT mmsi, upstream FROM gate_crossings") == [(7, 1)]
    assert table(target, "SELECT count(*) FROM positions") == [(0,)]
    assert table(target, "PRAGMA user_version") == [(SCHEMA_VERSION,)]
    assert sorted(os.listdir(stick)) == ["ais-2026-10-01.db"]


def test_backup_is_a_database_aisws_can_open(store, stick):
    restored = Store(store.backup(stick, DAY, keep=7))
    assert restored.lookup_static(7)["name"] == "BEWAARD"
    restored.close()


def test_only_the_newest_backups_are_kept(store, stick):
    for day in ("2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30"):
        (stick / f"ais-{day}.db").write_bytes(b"")
    (stick / "notities.txt").write_text("blijft staan")
    store.backup(stick, DAY, keep=3)
    assert sorted(os.listdir(stick)) == [
        "ais-2026-09-29.db", "ais-2026-09-30.db", "ais-2026-10-01.db", "notities.txt",
    ]


def test_backup_of_the_same_day_is_replaced(store, stick):
    (stick / "ais-2026-10-01.db").write_bytes(b"oud")
    (stick / ".ais-2026-10-01.db.tmp").write_bytes(b"half")
    store.backup(stick, DAY, keep=7)
    assert table(stick / "ais-2026-10-01.db", "SELECT name FROM vessels") == [("BEWAARD",)]
    assert sorted(os.listdir(stick)) == ["ais-2026-10-01.db"]


def test_missing_directory_is_an_error_and_is_not_created(store, tmp_path):
    missing = tmp_path / "niet-gekoppeld"
    with pytest.raises(BackupError, match="stick"):
        store.backup(missing, DAY, keep=7)
    assert not missing.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root mag overal schrijven")
def test_unwritable_directory_says_so(store, stick):
    # Zo ziet een ontkoppelde stick eruit: het lege koppelpunt is van root.
    stick.chmod(0o555)
    try:
        with pytest.raises(BackupError, match="niet schrijfbaar"):
            store.backup(stick, DAY, keep=7)
    finally:
        stick.chmod(0o755)
    assert os.listdir(stick) == []


def test_no_half_written_file_is_left_after_a_failure(store, stick, monkeypatch):
    def broken_replace(*args):
        raise OSError("schijf vol")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(BackupError, match="schijf vol"):
        store.backup(stick, DAY, keep=7)
    assert os.listdir(stick) == []


# --- planning ----------------------------------------------------------------------

TZ = ZoneInfo("Europe/Amsterdam")
NIGHT = datetime(2026, 10, 1, 0, 5, tzinfo=TZ).timestamp()  # 1 okt 00:05 lokaal


def test_backup_is_due_once_per_local_day(store, stick):
    job = BackupJob(store, str(stick), keep=7, tz=TZ)
    assert job.status() == {"file": None, "ts": None, "bytes": None, "error": None}
    assert job.due(NIGHT)
    job.run(NIGHT)
    status = job.status()
    assert (status["file"], status["ts"], status["error"]) == ("ais-2026-10-01.db", int(NIGHT), None)
    assert status["bytes"] > 0
    assert not job.due(NIGHT + 60)
    assert not job.due(NIGHT + 23 * 3600)        # 1 okt 23:05
    assert job.due(NIGHT + 24 * 3600)            # 2 okt 00:05


def test_failed_backup_is_retried_an_hour_later(store, tmp_path):
    job = BackupJob(store, str(tmp_path / "niet-gekoppeld"), keep=7, tz=TZ)
    job.run(NIGHT)
    assert "stick" in job.status()["error"]
    assert job.status()["file"] is None
    assert not job.due(NIGHT + 3599)
    assert job.due(NIGHT + 3600)


def test_existing_backup_of_today_is_not_redone_after_a_restart(store, stick):
    BackupJob(store, str(stick), keep=7, tz=TZ).run(NIGHT)
    assert not BackupJob(store, str(stick), keep=7, tz=TZ).due(NIGHT + 600)


def test_status_is_read_from_the_directory_after_a_restart(store, stick):
    BackupJob(store, str(stick), keep=7, tz=TZ).run(NIGHT)
    target = stick / "ais-2026-10-01.db"
    restarted = BackupJob(store, str(stick), keep=7, tz=TZ)
    restarted.tick(NIGHT + 600)
    status = restarted.status()
    assert (status["file"], status["bytes"], status["error"]) == (target.name, target.stat().st_size, None)
    assert status["ts"] == int(target.stat().st_mtime)


def test_attempts_are_at_least_an_hour_apart(store, stick):
    job = BackupJob(store, str(stick), keep=7, tz=TZ)
    job.run(NIGHT)
    (stick / "ais-2026-10-01.db").unlink()
    assert not job.due(NIGHT + 60)
    assert job.due(NIGHT + 3600)


def test_new_backup_is_never_pruned_itself(store, stick):
    # Bestanden uit de toekomst (klok die verkeerd stond) mogen de back-up van vandaag niet wegdrukken.
    for day in ("2027-01-01", "2027-01-02", "2027-01-03"):
        (stick / f"ais-{day}.db").write_bytes(b"")
    store.backup(stick, DAY, keep=3)
    assert sorted(os.listdir(stick)) == ["ais-2026-10-01.db", "ais-2027-01-02.db", "ais-2027-01-03.db"]
