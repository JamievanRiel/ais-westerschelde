import pytest

import aisenc
from aisws.assembler import Assembler
from aisws.config import TrackerConfig
from aisws.ingest import Pipeline
from aisws.store import Store
from aisws.tracker import Tracker

MMSI = 244123456
POSITION = aisenc.sentences(aisenc.type1(MMSI, 51.40, 3.60, 10.0, 90.0, 90, 0))[0]


@pytest.fixture
def parts(tmp_path):
    store = Store(tmp_path / "ais.db")
    tracker = Tracker(TrackerConfig(), 51.44, 3.58, static_lookup=store.lookup_static)
    pipeline = Pipeline(tracker, store, Assembler())
    yield pipeline, tracker, store
    store.close()


def test_position_line_reaches_tracker_and_store(parts):
    pipeline, tracker, store = parts
    pipeline.feed_line(POSITION, now=1000)
    assert [v["mmsi"] for v in tracker.live_vessels()] == [MMSI]
    store.flush()
    with store.reader() as conn:
        assert [tuple(r) for r in conn.execute("SELECT mmsi, ts FROM positions")] == [(MMSI, 1000)]


def test_datagram_with_several_lines_is_split(parts):
    pipeline, tracker, _ = parts
    other = aisenc.sentences(aisenc.type18(244999999, 51.35, 3.70, 5.0, 180.0))[0]
    pipeline.feed_datagram(f"{POSITION}\r\n{other}\n\n".encode(), now=1000)
    assert sorted(v["mmsi"] for v in tracker.live_vessels()) == [MMSI, 244999999]
    assert pipeline.counters["lines"] == 2


def test_multipart_static_data_is_assembled(parts):
    pipeline, tracker, _ = parts
    static = aisenc.sentences(aisenc.type5(MMSI, name="SCHELDEBANK", ship_type=70, a=150, b=30))
    assert len(static) == 2
    pipeline.feed_datagram("\n".join([POSITION, *static]).encode(), now=1000)
    assert tracker.live_vessels()[0]["name"] == "SCHELDEBANK"


@pytest.mark.parametrize(
    "line, counter",
    [
        (POSITION[:-2] + f"{int(POSITION[-2:], 16) ^ 1:02X}", "checksum_error"),
        ("garbage", "parse_error"),
        ("!AIVDM,1,1,,A,x,0*5E", "parse_error"),  # ongeldig payloadteken
        (aisenc.sentences(aisenc.type1(MMSI, 51.4, 3.6)[:100])[0], "too_short"),
    ],
)
def test_bad_lines_are_counted_and_skipped(parts, line, counter):
    pipeline, tracker, _ = parts
    pipeline.feed_line(line, now=1000)
    assert pipeline.counters[counter] == 1
    assert tracker.live_vessels() == []


def test_unsupported_types_are_counted_per_type(parts):
    pipeline, _, _ = parts
    base_station = aisenc.sentences(aisenc.bits(6, 4) + "0" * 162)[0]
    pipeline.feed_line(base_station, now=1000)
    pipeline.feed_line(base_station, now=1001)
    assert pipeline.unsupported_types == {4: 2}


def test_own_vessel_sentences_are_ignored_silently(parts):
    pipeline, _, _ = parts
    pipeline.feed_line("!AIVDO,1,1,,,B52K>;h00Fc>jpUlNV@ikwpUoP06,0*0F", now=1000)
    assert pipeline.counters["parse_error"] == 0
    assert pipeline.counters["checksum_error"] == 0


def test_undecodable_bytes_do_not_stop_the_listener(parts):
    pipeline, tracker, _ = parts
    pipeline.feed_datagram(b"\xff\xfe\n" + POSITION.encode(), now=1000)
    assert pipeline.counters["parse_error"] == 1
    assert len(tracker.live_vessels()) == 1


def test_internal_errors_are_counted_not_raised(parts, monkeypatch):
    pipeline, tracker, _ = parts

    def broken(message, now):
        raise RuntimeError("stuk")

    monkeypatch.setattr(tracker, "handle", broken)
    pipeline.feed_datagram(POSITION.encode(), now=1000)
    assert pipeline.counters["internal_error"] == 1


def test_message_rate_and_age(parts):
    pipeline, _, _ = parts
    assert pipeline.last_message_age(now=1000) is None
    for now in (900, 950, 990, 995):
        pipeline.feed_line(POSITION, now=now)
    assert pipeline.messages_last_min(now=1000) == 3
    assert pipeline.last_message_age(now=1000) == 5


def test_silence_monitor_warns_once_per_silent_period():
    from aisws.ingest import SilenceMonitor

    monitor = SilenceMonitor(threshold_s=300)
    assert monitor.check(age_s=10) is False
    assert monitor.check(age_s=301) is True
    assert monitor.check(age_s=400) is False
    assert monitor.check(age_s=2) is False
    assert monitor.check(age_s=305) is True


def test_receive_times_stay_bounded_without_health_requests(parts):
    # Zonder open browser vraagt niemand /api/health op; de lijst met
    # ontvangsttijden mag dan toch niet eindeloos groeien.
    pipeline, _, _ = parts
    for i in range(2000):
        pipeline.feed_line("garbage", now=float(i))
    assert len(pipeline._received) <= 61
