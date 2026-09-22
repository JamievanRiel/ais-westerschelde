"""Eind-tot-eind: voorbeeldbestand via UDP naar de echte app, dan de API bekijken."""

import time
from pathlib import Path

from fastapi.testclient import TestClient

from aisws.cli import replay
from aisws.config import parse_config
from aisws.web import create_app

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample.nmea"


def test_sample_replay_fills_the_map_and_statistics(tmp_path):
    config = parse_config({
        "station": {"lat": 51.44, "lon": 3.58},
        "ingest": {"udp_port": 0},
        "storage": {"db_path": str(tmp_path / "ais.db"), "flush_interval_s": 0.05},
    })
    with TestClient(create_app(config)) as client:
        lines = SAMPLE.read_text().splitlines()
        sent = replay(lines, "127.0.0.1", client.app.state.udp_port, rate=5000)

        def done():
            return client.get("/api/health").json()["counters"]["lines"] == sent

        deadline = time.monotonic() + 5
        while not done():
            assert time.monotonic() < deadline, "niet alle regels verwerkt"

        health = client.get("/api/health").json()
        assert health["counters"]["checksum_error"] == 0
        assert health["counters"]["parse_error"] == 0
        assert health["counters"]["multipart_dropped"] == 0

        vessels = client.get("/api/vessels").json()
        assert len(vessels) == 22
        assert all(v["name"] for v in vessels)
        assert {v["type_group"] for v in vessels} >= {"cargo", "tanker", "pilot", "pleasure", "towing"}
