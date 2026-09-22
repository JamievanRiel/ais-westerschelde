"""SQLite-opslag: schema, gebufferd schrijven, opschonen en laden bij opstart.

De ingest legt events in een buffer (``add``); ``flush`` schrijft alles in één
transactie weg. ``add`` draait in de event loop en ``flush`` in een thread,
daarom wordt de buffer onder een lock omgewisseld. Een tweede lock bewaakt de
schrijfverbinding: ``close`` wacht tot een lopende flush klaar is, want een
SQLite-verbinding sluiten terwijl een andere thread hem gebruikt kan crashen.
"""

import os
import re
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aisws.tracker import (
    STATIC_FIELDS,
    Event,
    GateCrossing,
    HourSample,
    RangeRecord,
    SectorSample,
    TrackPoint,
    VesselUpdate,
)

SCHEMA_VERSION = 2
RECORD_MERGE_S = 3600
SCHEMA = """
CREATE TABLE vessels (
  mmsi INTEGER PRIMARY KEY,
  name TEXT, callsign TEXT, imo INTEGER, ship_type INTEGER,
  length_m INTEGER, beam_m INTEGER, draught_m REAL, destination TEXT,
  ais_class TEXT, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL
);
CREATE TABLE positions (
  mmsi INTEGER NOT NULL, ts INTEGER NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL,
  sog REAL, cog REAL, heading INTEGER, nav_status INTEGER
);
CREATE INDEX positions_mmsi_ts ON positions (mmsi, ts);
CREATE INDEX positions_ts ON positions (ts);
CREATE TABLE vessel_hours (
  hour_ts INTEGER NOT NULL, mmsi INTEGER NOT NULL,
  reports INTEGER NOT NULL, speed_samples INTEGER NOT NULL,
  speed_sum REAL NOT NULL, max_range_m REAL NOT NULL,
  PRIMARY KEY (hour_ts, mmsi)
) WITHOUT ROWID;
CREATE TABLE range_records (
  ts INTEGER NOT NULL, mmsi INTEGER NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL, distance_m REAL NOT NULL
);
"""
# Per versie wat erbij kwam; een oudere database krijgt bij het openen de ontbrekende stappen.
MIGRATIONS = {
    2: """
CREATE TABLE range_sectors (
  day_ts INTEGER NOT NULL, sector INTEGER NOT NULL,
  max_range_m REAL NOT NULL, mmsi INTEGER NOT NULL,
  PRIMARY KEY (day_ts, sector)
) WITHOUT ROWID;
CREATE TABLE gate_crossings (
  ts INTEGER NOT NULL, mmsi INTEGER NOT NULL, upstream INTEGER NOT NULL
);
CREATE INDEX gate_crossings_ts ON gate_crossings (ts);
""",
}
FULL_SCHEMA = SCHEMA + "".join(MIGRATIONS[v] for v in sorted(MIGRATIONS))

# Wat een back-up bevat: alles wat niet terug te halen is. De tracksporen (positions)
# zijn groot en na 30 dagen toch weg, die blijven eruit.
BACKUP_TABLES = ("vessels", "vessel_hours", "range_records", "range_sectors", "gate_crossings")
BACKUP_NAME = re.compile(r"^ais-\d{4}-\d{2}-\d{2}\.db$")


def backup_name(day: date) -> str:
    return f"ais-{day.isoformat()}.db"


class SchemaError(RuntimeError):
    pass


class BackupError(RuntimeError):
    pass


# Statisch veld in de tracker → kolom in de database.
COLUMNS = {key: key for key in STATIC_FIELDS} | {
    "length": "length_m",
    "beam": "beam_m",
    "draught": "draught_m",
}


@dataclass
class _Vessel:
    ais_class: str
    first_seen: int
    last_seen: int
    static: dict[str, Any] = field(default_factory=dict)

    def merge(self, newer: "_Vessel") -> None:
        self.ais_class = newer.ais_class
        self.first_seen = min(self.first_seen, newer.first_seen)
        self.last_seen = max(self.last_seen, newer.last_seen)
        self.static.update(newer.static)


@dataclass
class _Batch:
    positions: list[TrackPoint] = field(default_factory=list)
    hours: dict[tuple[int, int], list[float]] = field(default_factory=dict)
    vessels: dict[int, _Vessel] = field(default_factory=dict)
    records: list[RangeRecord] = field(default_factory=list)
    sectors: dict[tuple[int, int], tuple[float, int]] = field(default_factory=dict)
    crossings: list[GateCrossing] = field(default_factory=list)

    def add(self, event: Event) -> None:
        if isinstance(event, TrackPoint):
            self.positions.append(event)
        elif isinstance(event, HourSample):
            self._add_hour((event.hour_ts, event.mmsi), [
                1, 0 if event.speed is None else 1, event.speed or 0.0, event.distance_m,
            ])
        elif isinstance(event, VesselUpdate):
            self._add_vessel(event.mmsi, _Vessel(event.ais_class, event.ts, event.ts, dict(event.static)))
        elif isinstance(event, RangeRecord):
            self.records.append(event)
        elif isinstance(event, SectorSample):
            self._add_sector((event.day_ts, event.sector), (event.distance_m, event.mmsi))
        elif isinstance(event, GateCrossing):
            self.crossings.append(event)

    def _add_sector(self, key: tuple[int, int], value: tuple[float, int]) -> None:
        current = self.sectors.get(key)
        if current is None or value[0] > current[0]:
            self.sectors[key] = value

    def _add_hour(self, key: tuple[int, int], values: list[float]) -> None:
        current = self.hours.get(key)
        if current is None:
            self.hours[key] = values
        else:
            current[0] += values[0]
            current[1] += values[1]
            current[2] += values[2]
            current[3] = max(current[3], values[3])

    def _add_vessel(self, mmsi: int, vessel: _Vessel) -> None:
        if mmsi in self.vessels:
            self.vessels[mmsi].merge(vessel)
        else:
            self.vessels[mmsi] = vessel

    def absorb_newer(self, newer: "_Batch") -> None:
        self.positions.extend(newer.positions)
        for key, values in newer.hours.items():
            self._add_hour(key, values)
        for mmsi, vessel in newer.vessels.items():
            self._add_vessel(mmsi, vessel)
        self.records.extend(newer.records)
        for key, value in newer.sectors.items():
            self._add_sector(key, value)
        self.crossings.extend(newer.crossings)

    def is_empty(self) -> bool:
        return not (self.positions or self.hours or self.vessels or self.records
                    or self.sectors or self.crossings)


class Store:
    def __init__(
        self, path: str | Path, max_pending_writes: int = 10_000, busy_timeout_s: float = 5.0
    ) -> None:
        self.path = Path(path)
        self.max_pending_writes = max_pending_writes
        self.flush_errors = 0
        self.writes_dropped = 0
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._batch = _Batch()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path, timeout=busy_timeout_s, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate()
        # Aparte verbinding voor lookups vanuit de event loop, los van de flush-thread.
        self._read_conn = self._open_reader(check_same_thread=False)

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            self._conn.close()
            raise SchemaError(
                f"{self.path} is gemaakt door een nieuwere versie van aisws (schema {version}, "
                f"deze versie kent {SCHEMA_VERSION}); werk aisws bij"
            )
        if version == 0:
            steps = FULL_SCHEMA
        else:
            steps = "".join(MIGRATIONS[v] for v in range(version + 1, SCHEMA_VERSION + 1))
        if steps:
            self._conn.executescript(f"BEGIN; {steps} PRAGMA user_version={SCHEMA_VERSION}; COMMIT;")

    def close(self) -> None:
        with self._write_lock:
            self._read_conn.close()
            self._conn.close()

    def _open_reader(self, check_same_thread: bool = True) -> sqlite3.Connection:
        conn = sqlite3.connect(
            f"file:{self.path}?mode=ro", uri=True, timeout=5.0, check_same_thread=check_same_thread
        )
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        """Nieuwe alleen-lezen verbinding, veilig om in een willekeurige thread te gebruiken."""
        with closing(self._open_reader()) as conn:
            yield conn

    # --- schrijven ------------------------------------------------------------

    def add(self, events: Iterable[Event]) -> None:
        with self._lock:
            for event in events:
                self._batch.add(event)
            self._enforce_limit()

    def _enforce_limit(self) -> None:
        excess = len(self._batch.positions) - self.max_pending_writes
        if excess > 0:
            del self._batch.positions[:excess]
            self.writes_dropped += excess

    def flush(self) -> bool:
        """Schrijft de buffer weg. Bij een fout blijft alles bewaard voor de volgende keer.

        De schrijflock omvat ook het omwisselen van de buffer, zodat ``close`` of
        een tweede flush nooit tussen omwisselen en wegschrijven kan komen.
        """
        with self._write_lock:
            with self._lock:
                batch, self._batch = self._batch, _Batch()
            if batch.is_empty():
                return True
            try:
                self._write(batch)
            except sqlite3.Error:
                self.flush_errors += 1
                with self._lock:
                    batch.absorb_newer(self._batch)
                    self._batch = batch
                    self._enforce_limit()
                return False
            return True

    def _write(self, batch: _Batch) -> None:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(p.mmsi, p.ts, p.lat, p.lon, p.sog, p.cog, p.heading, p.nav_status)
                 for p in batch.positions],
            )
            conn.executemany(
                """INSERT INTO vessel_hours VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (hour_ts, mmsi) DO UPDATE SET
                     reports = reports + excluded.reports,
                     speed_samples = speed_samples + excluded.speed_samples,
                     speed_sum = speed_sum + excluded.speed_sum,
                     max_range_m = max(max_range_m, excluded.max_range_m)""",
                [(hour, mmsi, *values) for (hour, mmsi), values in batch.hours.items()],
            )
            columns = list(COLUMNS.values())
            conn.executemany(
                f"""INSERT INTO vessels (mmsi, ais_class, first_seen, last_seen, {", ".join(columns)})
                    VALUES (?, ?, ?, ?, {", ".join("?" * len(columns))})
                    ON CONFLICT (mmsi) DO UPDATE SET
                      ais_class = excluded.ais_class,
                      first_seen = min(first_seen, excluded.first_seen),
                      last_seen = max(last_seen, excluded.last_seen),
                      {", ".join(f"{c} = coalesce(excluded.{c}, {c})" for c in columns)}""",
                [(mmsi, v.ais_class, v.first_seen, v.last_seen, *(v.static.get(k) for k in COLUMNS))
                 for mmsi, v in batch.vessels.items()],
            )
            for record in batch.records:
                self._write_record(record)
            conn.executemany(
                """INSERT INTO range_sectors VALUES (?, ?, ?, ?)
                   ON CONFLICT (day_ts, sector) DO UPDATE SET
                     max_range_m = excluded.max_range_m, mmsi = excluded.mmsi
                   WHERE excluded.max_range_m > range_sectors.max_range_m""",
                [(day, sector, distance, mmsi) for (day, sector), (distance, mmsi) in batch.sectors.items()],
            )
            conn.executemany(
                "INSERT INTO gate_crossings VALUES (?, ?, ?)",
                [(c.ts, c.mmsi, int(c.upstream)) for c in batch.crossings],
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def _write_record(self, record: RangeRecord) -> None:
        """Een schip dat zijn eigen record steeds verder verbetert (het vaart van
        het station weg) werkt één rij bij in plaats van er elke keer een toe te voegen."""
        last = self._conn.execute(
            "SELECT rowid, ts, mmsi FROM range_records ORDER BY ts DESC, rowid DESC LIMIT 1"
        ).fetchone()
        values = (record.ts, record.mmsi, record.lat, record.lon, record.distance_m)
        if last and last["mmsi"] == record.mmsi and record.ts - last["ts"] <= RECORD_MERGE_S:
            self._conn.execute(
                "UPDATE range_records SET ts = ?, mmsi = ?, lat = ?, lon = ?, distance_m = ? "
                "WHERE rowid = ?",
                (*values, last["rowid"]),
            )
        else:
            self._conn.execute("INSERT INTO range_records VALUES (?, ?, ?, ?, ?)", values)

    def cleanup(self, before_ts: int) -> int:
        with self._write_lock:
            cursor = self._conn.execute("DELETE FROM positions WHERE ts < ?", (before_ts,))
            return cursor.rowcount

    # --- lezen bij opstart ---------------------------------------------------

    def load_record(self) -> RangeRecord | None:
        row = self._read_conn.execute(
            "SELECT ts, mmsi, lat, lon, distance_m FROM range_records "
            "ORDER BY distance_m DESC LIMIT 1"
        ).fetchone()
        return RangeRecord(*row) if row else None

    def load_live(self, since_ts: int) -> list[dict[str, Any]]:
        """Laatste positie van elk schip met een positie sinds ``since_ts``, plus statische data."""
        static = ", ".join(f"v.{column} AS {key}" for key, column in COLUMNS.items())
        rows = self._read_conn.execute(
            f"""SELECT p.mmsi, v.ais_class, v.first_seen, v.last_seen, {static},
                       p.lat, p.lon, p.sog, p.cog, p.heading, p.nav_status, p.ts AS position_ts
                FROM positions p
                JOIN (SELECT mmsi, max(ts) AS ts FROM positions WHERE ts >= ? GROUP BY mmsi) latest
                  ON latest.mmsi = p.mmsi AND latest.ts = p.ts
                JOIN vessels v ON v.mmsi = p.mmsi""",
            (since_ts,),
        ).fetchall()
        return [dict(row) for row in rows]

    def lookup_static(self, mmsi: int) -> dict[str, Any] | None:
        row = self._read_conn.execute(
            f"SELECT {', '.join(COLUMNS.values())} FROM vessels WHERE mmsi = ?", (mmsi,)
        ).fetchone()
        if row is None:
            return None
        return {key: row[column] for key, column in COLUMNS.items() if row[column] is not None}

    # --- back-up ----------------------------------------------------------------

    def backup(self, directory: str | Path, day: date, keep: int) -> Path:
        """Schrijft ``directory/ais-<dag>.db`` en bewaart de nieuwste ``keep`` back-ups.

        De map wordt niet aangemaakt: een ontkoppelde USB-stick moet een fout
        geven, niet stilletjes een back-up op de SD-kaart. Het bestand komt eerst
        onder een tijdelijke naam en wordt pas daarna hernoemd.
        """
        directory = Path(directory)
        if not directory.is_dir():
            raise BackupError(f"{directory} bestaat niet; is de stick gekoppeld?")
        if not os.access(directory, os.W_OK):
            raise BackupError(f"{directory} is niet schrijfbaar; is de stick gekoppeld en van aisws?")
        target = directory / backup_name(day)
        tmp = directory / f".{target.name}.tmp"
        try:
            tmp.unlink(missing_ok=True)
            with closing(sqlite3.connect(f"file:{quote(str(tmp))}", uri=True, isolation_level=None)) as conn:
                conn.executescript(f"BEGIN; {FULL_SCHEMA} PRAGMA user_version={SCHEMA_VERSION}; COMMIT;")
                conn.execute("ATTACH DATABASE ? AS src", (f"file:{quote(str(self.path))}?mode=ro",))
                # Eén transactie: alle tabellen komen uit dezelfde momentopname.
                conn.execute("BEGIN")
                for name in BACKUP_TABLES:
                    conn.execute(f"INSERT INTO main.{name} SELECT * FROM src.{name}")
                conn.execute("COMMIT")
                conn.execute("DETACH DATABASE src")
            os.replace(tmp, target)
            for old in sorted(p for p in directory.iterdir() if BACKUP_NAME.match(p.name))[:-keep]:
                old.unlink()
        except (OSError, sqlite3.Error) as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise BackupError(f"back-up naar {directory} mislukt: {exc}") from exc
        return target

    def size_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for path in (self.path, self.path.with_name(self.path.name + "-wal"))
            if path.exists()
        )
