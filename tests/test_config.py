import pytest

from aisws.config import ConfigError, load_config, parse_config

STATION = {"station": {"lat": 51.44, "lon": 3.58}}


def test_minimal_config_uses_defaults():
    config = parse_config(STATION)
    assert (config.station.lat, config.station.lon) == (51.44, 3.58)
    assert config.station.timezone == "Europe/Amsterdam"
    assert config.ingest.udp_port == 10110
    assert config.tracker.track_interval_moving_s == 30
    assert config.storage.positions_retention_days == 30
    assert config.web.port == 8000


def test_values_override_defaults():
    config = parse_config({**STATION, "web": {"port": 9000}, "tracker": {"max_range_km": 250}})
    assert config.web.port == 9000
    assert config.tracker.max_range_km == 250.0


def test_integers_are_accepted_for_float_settings():
    config = parse_config({"station": {"lat": 51, "lon": 3}})
    assert isinstance(config.station.lat, float)


@pytest.mark.parametrize(
    "data, fragment",
    [
        ({}, "station.lat"),
        ({"station": {"lat": 51.4}}, "station.lon"),
        ({"station": {"lat": 0.0, "lon": 0.0}}, "positie"),
        ({"station": {"lat": 91.0, "lon": 3.5}}, "station.lat"),
        ({"station": {"lat": 51.4, "lon": 181.0}}, "station.lon"),
        ({**STATION, "web": {"port": "8000"}}, "web.port"),
        ({**STATION, "web": {"port": True}}, "web.port"),
        ({**STATION, "web": {"poort": 8000}}, "web.poort"),
        ({**STATION, "webb": {}}, "webb"),
        ({"station": {"lat": 51.4, "lon": 3.5, "timezone": "Europe/Vlissingen"}}, "timezone"),
    ],
)
def test_invalid_config_is_rejected_with_a_clear_message(data, fragment):
    with pytest.raises(ConfigError, match=fragment):
        parse_config(data)


def test_load_config_reads_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[station]\nname = "Test"\nlat = 51.44\nlon = 3.58\n')
    assert load_config(path).station.name == "Test"


def test_load_config_reports_toml_syntax_errors(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[station\n")
    with pytest.raises(ConfigError, match="config.toml"):
        load_config(path)


def test_example_config_is_complete_apart_from_the_station_position():
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    with pytest.raises(ConfigError, match="positie"):
        load_config(example)
