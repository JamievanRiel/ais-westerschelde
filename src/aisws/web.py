"""FastAPI-app: UDP-ingest, periodieke taken, REST-API, WebSocket en de frontend."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from aisws import stats
from aisws.assembler import Assembler
from aisws.backup import BackupJob
from aisws.config import Config
from aisws.hub import Hub
from aisws.ingest import Pipeline, SilenceMonitor, UdpProtocol
from aisws.shiptypes import type_group
from aisws.store import COLUMNS, Store
from aisws.tracker import Tracker

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


async def _every(interval_s: float, job: Callable[[], Awaitable[None]]) -> None:
    while True:
        await asyncio.sleep(interval_s)
        try:
            await job()
        except Exception:
            log.exception("periodieke taak mislukt")


def create_app(config: Config, clock: Callable[[], float] = time.time) -> FastAPI:
    tz = ZoneInfo(config.station.timezone)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(config.storage.db_path, max_pending_writes=config.storage.max_pending_writes)
        tracker = Tracker(
            config.tracker, config.station.lat, config.station.lon,
            static_lookup=store.lookup_static, gate=config.gate,
        )
        started = clock()
        tracker.restore(store.load_live(since_ts=int(started - config.tracker.stale_after_s)),
                        store.load_record())
        assembler = Assembler(config.ingest.multipart_timeout_s)
        pipeline = Pipeline(tracker, store, assembler)
        hub = Hub(tracker)
        silence = SilenceMonitor()
        last_cleanup: list[Any] = [None]
        backup = (
            BackupJob(store, config.storage.backup_dir, config.storage.backup_keep, tz)
            if config.storage.backup_dir else None
        )

        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: UdpProtocol(pipeline, clock),
            local_addr=(config.ingest.udp_host, config.ingest.udp_port),
        )
        app.state.udp_port = transport.get_extra_info("sockname")[1]
        app.state.store, app.state.tracker, app.state.hub = store, tracker, hub
        app.state.pipeline, app.state.started, app.state.backup = pipeline, started, backup
        log.info("luistert naar AIS-catcher op udp://%s:%d", config.ingest.udp_host, app.state.udp_port)

        async def live_job() -> None:
            now = clock()
            assembler.expire(now)
            await hub.tick(now)
            age = pipeline.last_message_age(now)
            if silence.check(now - started if age is None else age):
                log.warning("al 5 minuten geen AIS-berichten ontvangen; controleer dongle en antenne")

        async def storage_job() -> None:
            if not await asyncio.to_thread(store.flush):
                log.error("wegschrijven naar de database mislukt; volgende poging over %.0f s",
                          config.storage.flush_interval_s)
            local = datetime.fromtimestamp(clock(), tz)
            if local.hour == config.storage.cleanup_hour_local and last_cleanup[0] != local.date():
                last_cleanup[0] = local.date()
                cutoff = int(clock()) - config.storage.positions_retention_days * 86400
                deleted = await asyncio.to_thread(store.cleanup, cutoff)
                log.info("%d oude posities opgeruimd", deleted)
            if backup is not None:
                await asyncio.to_thread(backup.tick, clock())

        tasks = [
            asyncio.create_task(_every(config.web.ws_update_interval_s, live_job)),
            asyncio.create_task(_every(config.storage.flush_interval_s, storage_job)),
        ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            transport.close()
            store.flush()
            store.close()

    app = FastAPI(title="AIS Westerschelde", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def now() -> int:
        return int(clock())

    def read(query: Callable[..., Any], *args: Any) -> Any:
        with app.state.store.reader() as conn:
            return query(conn, *args)

    # --- pagina's -------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/stats", include_in_schema=False)
    async def stats_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "stats.html")

    # --- live ----------------------------------------------------------------

    def gate() -> dict[str, Any]:
        g = config.gate
        return {"name": g.name, "lat1": g.lat1, "lon1": g.lon1, "lat2": g.lat2, "lon2": g.lon2}

    def station() -> dict[str, float]:
        return {"lat": config.station.lat, "lon": config.station.lon}

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        return {"station": {"name": config.station.name, **station()}, "gate": gate()}

    @app.get("/api/vessels")
    async def vessels() -> list[dict[str, Any]]:
        return app.state.tracker.live_vessels()

    @app.get("/api/vessels/{mmsi}")
    async def vessel(mmsi: int) -> dict[str, Any]:
        def load(conn):
            return conn.execute("SELECT * FROM vessels WHERE mmsi = ?", (mmsi,)).fetchone()

        row = await asyncio.to_thread(read, load)
        state = app.state.tracker.vessel(mmsi)
        if row is None and state is None:
            raise HTTPException(404, "onbekend schip")
        detail: dict[str, Any] = {"mmsi": mmsi}
        for key, column in COLUMNS.items():
            value = getattr(state, key, None) if state is not None else None
            detail[key] = value if value is not None else (row[column] if row else None)
        detail["type_group"] = type_group(detail["ship_type"])
        detail["ais_class"] = state.ais_class if state is not None else row["ais_class"]
        detail["first_seen"] = row["first_seen"] if row else int(state.first_seen)
        detail["last_seen"] = int(state.last_seen) if state is not None else row["last_seen"]
        detail["live"] = state.as_live() if state is not None and state.live else None
        return detail

    @app.get("/api/vessels/{mmsi}/track")
    def track(mmsi: int, hours: int = Query(6, ge=1, le=720)) -> list[list[Any]]:
        def load(conn):
            return conn.execute(
                "SELECT ts, lat, lon, sog FROM positions WHERE mmsi = ? AND ts >= ? ORDER BY ts",
                (mmsi, now() - hours * 3600),
            ).fetchall()

        return [list(row) for row in read(load)]

    # --- statistieken -----------------------------------------------------------

    @app.get("/api/stats/hourly")
    def stats_hourly(hours: int = Query(48, ge=1, le=720)) -> list[dict[str, Any]]:
        return read(stats.hourly, tz, now(), hours)

    @app.get("/api/stats/daily")
    def stats_daily(days: int = Query(90, ge=1, le=3660)) -> list[dict[str, Any]]:
        return read(stats.daily, tz, now(), days)

    @app.get("/api/stats/speed")
    def stats_speed(period: Literal["7d", "30d", "all"] = "30d") -> list[dict[str, Any]]:
        return read(stats.speed, now(), period)

    @app.get("/api/stats/heatmap")
    def stats_heatmap(weeks: int = Query(8, ge=1, le=52)) -> dict[str, Any]:
        return read(stats.heatmap, tz, now(), weeks)

    @app.get("/api/stats/largest")
    def stats_largest(months: int = Query(12, ge=1, le=120)) -> dict[str, Any]:
        return read(stats.largest, tz, now(), months)

    @app.get("/api/stats/range")
    def stats_range() -> dict[str, Any]:
        result = read(stats.range_records)
        result["station"] = station()
        return result

    @app.get("/api/stats/coverage")
    def stats_coverage(period: Literal["7d", "30d", "all"] = "30d") -> dict[str, Any]:
        return {"sectors": read(stats.coverage, now(), period), "station": station()}

    @app.get("/api/stats/passages")
    def stats_passages(days: int = Query(90, ge=1, le=365)) -> dict[str, Any]:
        return {**read(stats.passages, tz, now(), days), "gate": gate()}

    # --- monitoring -------------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        state = app.state
        current = clock()
        age = state.pipeline.last_message_age(current)
        counters = dict(state.pipeline.counters)
        counters.update(
            multipart_dropped=state.pipeline.assembler.dropped,
            implausible=state.tracker.implausible,
            writes_dropped=state.store.writes_dropped,
            flush_errors=state.store.flush_errors,
        )
        return {
            "uptime_s": round(current - state.started),
            "last_message_age_s": None if age is None else round(age, 1),
            "messages_last_min": state.pipeline.messages_last_min(current),
            "vessels_live": len(state.tracker.live_vessels()),
            "counters": counters,
            "unsupported_types": {str(k): v for k, v in sorted(state.pipeline.unsupported_types.items())},
            "db_size_bytes": state.store.size_bytes(),
            "backup": state.backup.status() if state.backup is not None else None,
        }

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        await app.state.hub.serve(ws)

    return app
