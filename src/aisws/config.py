"""Instellingen uit een TOML-bestand, met standaardwaarden en validatie."""

import tomllib
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class StationConfig:
    lat: float
    lon: float
    name: str = "Westerschelde"
    timezone: str = "Europe/Amsterdam"


@dataclass(frozen=True)
class IngestConfig:
    udp_host: str = "127.0.0.1"
    udp_port: int = 10110
    multipart_timeout_s: float = 2.0


@dataclass(frozen=True)
class TrackerConfig:
    stale_after_s: float = 600.0
    stationary_sog_kn: float = 0.5
    moving_sog_kn: float = 1.0
    track_interval_moving_s: float = 30.0
    track_interval_stationary_s: float = 300.0
    track_cog_change_deg: float = 10.0
    speed_sample_interval_s: float = 30.0
    max_range_km: float = 400.0
    max_implied_speed_kn: float = 60.0


@dataclass(frozen=True)
class StorageConfig:
    db_path: str = "/var/lib/aisws/ais.db"
    flush_interval_s: float = 5.0
    positions_retention_days: int = 30
    cleanup_hour_local: int = 4
    max_pending_writes: int = 10000


@dataclass(frozen=True)
class WebConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    ws_update_interval_s: float = 1.0


@dataclass(frozen=True)
class Config:
    station: StationConfig
    ingest: IngestConfig = field(default_factory=IngestConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    web: WebConfig = field(default_factory=WebConfig)


def _convert(name: str, expected: type, value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ConfigError(f"{name}: ongeldige waarde {value!r}")
    if expected is float and isinstance(value, int):
        return float(value)
    if not isinstance(value, expected):
        raise ConfigError(f"{name}: verwacht {expected.__name__}, kreeg {value!r}")
    return value


def _section(cls: type, name: str, data: Any) -> Any:
    if not isinstance(data, dict):
        raise ConfigError(f"{name}: moet een tabel zijn")
    known = {f.name: f for f in fields(cls)}
    for key in data:
        if key not in known:
            raise ConfigError(f"{name}.{key}: onbekende instelling")
    values = {}
    for key, spec in known.items():
        if key in data:
            values[key] = _convert(f"{name}.{key}", spec.type, data[key])
        elif spec.default is MISSING:
            raise ConfigError(f"{name}.{key}: verplicht")
    return cls(**values)


def parse_config(data: dict[str, Any]) -> Config:
    sections = {f.name: f for f in fields(Config)}
    for key in data:
        if key not in sections:
            raise ConfigError(f"{key}: onbekende sectie")
    values = {
        key: _section(spec.type, key, data.get(key, {}))
        for key, spec in sections.items()
        if key in data or key == "station"
    }
    config = Config(**values)
    _validate(config)
    return config


def _validate(config: Config) -> None:
    station = config.station
    if not -90 <= station.lat <= 90:
        raise ConfigError("station.lat: moet tussen -90 en 90 liggen")
    if not -180 <= station.lon <= 180:
        raise ConfigError("station.lon: moet tussen -180 en 180 liggen")
    if station.lat == 0 and station.lon == 0:
        raise ConfigError("station: vul de positie van je antenne in (lat/lon staan op 0,0)")
    try:
        ZoneInfo(station.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"station.timezone: onbekende tijdzone {station.timezone!r}") from None


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    return parse_config(data)
