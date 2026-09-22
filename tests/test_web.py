import socket
import time

import pytest
from fastapi.testclient import TestClient

import aisenc
from aisws.config import parse_config
from aisws.web import create_app

MMSI = 244123456
POSITION = aisenc.sentences(aisenc.type1(MMSI, 51.40, 3.60, 10.0, 90.0, 90, 0))[0]
STATIC = aisenc.sentences(
    aisenc.type5(MMSI, name="SCHELDEBANK", ship_type=70, a=150, b=30, c=12, d=12, draught=7.5,
                 destination="ANTWERPEN")
)


def make_config(tmp_path, **storage):
    return parse_config({
        "station": {"name": "Test", "lat": 51.44, "lon": 3.58},
        "ingest": {"udp_port": 0},
        "storage": {"db_path": str(tmp_path / "ais.db"), "flush_interval_s": 0.05, **storage},
        "web": {"ws_update_interval_s": 0.05},
    })


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_config(tmp_path))) as client:
        yield client


def send(client, *lines):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto("\r\n".join(lines).encode(), ("127.0.0.1", client.app.state.udp_port))


def wait_for(fetch, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fetch()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("timeout")


def test_config_exposes_the_station_and_the_gate(client):
    assert client.get("/api/config").json() == {
        "station": {"name": "Test", "lat": 51.44, "lon": 3.58},
        "gate": {"name": "Vlissingen", "lat1": 51.458, "lon1": 3.640, "lat2": 51.380, "lon2": 3.640},
    }


def test_udp_position_shows_up_as_live_vessel(client):
    send(client, POSITION)
    [vessel] = wait_for(lambda: client.get("/api/vessels").json())
    assert (vessel["mmsi"], vessel["lat"], vessel["sog"]) == (MMSI, pytest.approx(51.40), 10.0)


def test_websocket_sends_snapshot_then_updates(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "snapshot", "vessels": []}
        send(client, POSITION)
        update = ws.receive_json()
        assert update["type"] == "update"
        assert [v["mmsi"] for v in update["vessels"]] == [MMSI]
        assert update["removed"] == []


def test_vessel_detail_combines_static_and_live_data(client):
    send(client, POSITION, *STATIC)
    detail = wait_for(lambda: client.get(f"/api/vessels/{MMSI}").json().get("name"))
    assert detail == "SCHELDEBANK"
    body = client.get(f"/api/vessels/{MMSI}").json()
    assert (body["type_group"], body["length"], body["beam"], body["draught"]) == ("cargo", 180, 24, 7.5)
    assert body["destination"] == "ANTWERPEN"
    assert body["live"]["mmsi"] == MMSI


def test_unknown_vessel_is_404(client):
    assert client.get("/api/vessels/123").status_code == 404


def test_track_contains_flushed_positions(client):
    send(client, POSITION)
    track = wait_for(lambda: client.get(f"/api/vessels/{MMSI}/track?hours=1").json())
    [[ts, lat, lon, sog]] = track
    assert (lat, lon, sog) == (pytest.approx(51.40), pytest.approx(3.60), 10.0)
    assert abs(ts - time.time()) < 5


def test_statistics_reflect_received_data(client):
    send(client, *STATIC, POSITION, POSITION)
    hourly = wait_for(lambda: client.get("/api/stats/hourly?hours=2").json()[-1]["ships"])
    assert hourly == 1
    assert client.get("/api/stats/daily?days=1").json()[0]["ships"] == 1
    assert client.get("/api/stats/speed?period=7d").json() == [
        {"type_group": "cargo", "avg_kn": 10.0, "samples": 1}
    ]
    heat = client.get("/api/stats/heatmap?weeks=1").json()
    assert len(heat["cells"]) == 7 and heat["top"] == []  # het lopende uur telt nog niet mee
    assert client.get("/api/stats/largest").json()["current"]["name"] == "SCHELDEBANK"
    ranges = client.get("/api/stats/range").json()
    assert ranges["record"]["mmsi"] == MMSI
    assert ranges["station"] == {"lat": 51.44, "lon": 3.58}


def test_invalid_statistics_parameters_are_rejected(client):
    assert client.get("/api/stats/speed?period=1y").status_code == 422
    assert client.get("/api/stats/hourly?hours=0").status_code == 422
    assert client.get("/api/stats/coverage?period=1y").status_code == 422
    assert client.get("/api/stats/passages?days=0").status_code == 422
    assert client.get("/api/stats/passages?days=366").status_code == 422


def test_ship_crossing_the_gate_is_counted_and_sets_the_coverage(tmp_path):
    # Een nepklok: twee posities 30 s na elkaar, anders keurt de tracker de sprong af.
    now = [time.time()]
    with TestClient(create_app(make_config(tmp_path), clock=lambda: now[0])) as client:
        send(client, aisenc.sentences(aisenc.type1(MMSI, 51.42, 3.638, 10.0, 90.0, 90, 0))[0])
        wait_for(lambda: client.get("/api/vessels").json())
        now[0] += 30
        send(client, aisenc.sentences(aisenc.type1(MMSI, 51.42, 3.642, 10.0, 90.0, 90, 0))[0])
        passages = wait_for(lambda: (p := client.get("/api/stats/passages?days=1").json())["today"]["up"] and p)
        assert passages["today"] == {"up": 1, "down": 0}
        assert passages["gate"]["name"] == "Vlissingen"
        coverage = client.get("/api/stats/coverage?period=7d").json()
        assert coverage["station"] == {"lat": 51.44, "lon": 3.58}
        [sector] = [s for s in coverage["sectors"] if s["max_km"] is not None]
        # (51.42, 3.642) ligt vanaf het station op ~117° en ~4,8 km.
        assert (sector["sector"], sector["max_km"]) == (11, pytest.approx(4.8, abs=0.1))


def test_health_reports_counters(client):
    bad = POSITION[:-2] + f"{int(POSITION[-2:], 16) ^ 1:02X}"
    send(client, POSITION, bad)
    health = wait_for(lambda: (h := client.get("/api/health").json())["counters"]["lines"] == 2 and h)
    assert health["counters"]["checksum_error"] == 1
    assert health["vessels_live"] == 1
    assert health["messages_last_min"] == 2
    assert health["last_message_age_s"] < 5
    assert health["db_size_bytes"] > 0


def test_backup_is_off_by_default(client):
    assert client.get("/api/health").json()["backup"] is None


def test_backup_is_made_right_after_startup(tmp_path):
    stick = tmp_path / "stick"
    stick.mkdir()
    with TestClient(create_app(make_config(tmp_path, backup_dir=str(stick)))) as client:
        backup = wait_for(lambda: client.get("/api/health").json()["backup"]["file"])
        assert (stick / backup).exists()
        assert client.get("/api/health").json()["backup"]["error"] is None


def test_failed_backup_shows_up_in_health(tmp_path):
    config = make_config(tmp_path, backup_dir=str(tmp_path / "niet-gekoppeld"))
    with TestClient(create_app(config)) as client:
        error = wait_for(lambda: client.get("/api/health").json()["backup"]["error"])
        assert "stick" in error


def test_restart_restores_live_vessels_and_record(tmp_path):
    config = make_config(tmp_path)
    with TestClient(create_app(config)) as first:
        send(first, *STATIC, POSITION, POSITION)
        wait_for(lambda: first.get("/api/vessels").json())
    with TestClient(create_app(config)) as second:
        [vessel] = second.get("/api/vessels").json()
        assert (vessel["mmsi"], vessel["name"]) == (MMSI, "SCHELDEBANK")
        assert second.get("/api/stats/range").json()["record"]["mmsi"] == MMSI


@pytest.mark.parametrize(
    "path, fragment",
    [
        ("/", 'src="/static/js/map.js"'),
        ("/stats", 'src="/static/js/stats.js"'),
        ("/static/css/style.css", "--magenta"),
        ("/static/js/shiptypes.js", "export const CLASSES"),
    ],
)
def test_frontend_files_are_served(client, path, fragment):
    response = client.get(path)
    assert response.status_code == 200
    assert fragment in response.text
