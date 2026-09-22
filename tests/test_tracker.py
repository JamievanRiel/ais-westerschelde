import pytest

from aisws.config import GateConfig, TrackerConfig
from aisws.messages import PositionReport, StaticData
from aisws.tracker import (
    GateCrossing,
    HourSample,
    RangeRecord,
    SectorSample,
    TrackPoint,
    Tracker,
    VesselUpdate,
)

STATION = (51.44, 3.58)
MMSI = 244000001


def pos(lat=51.40, lon=3.60, sog=10.0, cog=90.0, heading=90, nav_status=0, mmsi=MMSI):
    return PositionReport(1, mmsi, lat, lon, sog, cog, heading, nav_status, "A")


def tracker(static_lookup=None, gate=None, **overrides):
    return Tracker(TrackerConfig(**overrides), *STATION, static_lookup=static_lookup, gate=gate)


def of_type(events, cls):
    return [event for event in events if isinstance(event, cls)]


def track_times(t, reports):
    """Voert (tijd, bericht)-paren in; geeft de tijden van opgeslagen spoorpunten."""
    return [
        point.ts
        for now, report in reports
        for point in of_type(t.handle(report, now), TrackPoint)
    ]


# --- acceptatie en plausibiliteit -------------------------------------------


def test_first_position_is_accepted_and_goes_live():
    t = tracker()
    events = t.handle(pos(), now=1000)
    assert of_type(events, VesselUpdate) == [VesselUpdate(MMSI, 1000, "A", {})]
    assert of_type(events, TrackPoint) == [TrackPoint(MMSI, 1000, 51.40, 3.60, 10.0, 90.0, 90, 0)]
    [live] = t.live_vessels()
    assert (live["mmsi"], live["lat"], live["lon"], live["last_seen"]) == (MMSI, 51.40, 3.60, 1000)
    assert t.drain_changes() == ({MMSI}, set())
    assert t.drain_changes() == (set(), set())


def test_position_beyond_max_range_is_rejected():
    t = tracker()
    assert t.handle(pos(lat=55.5, lon=3.58), now=0) == []  # ~451 km
    assert t.implausible == 1
    assert t.live_vessels() == []


def test_max_range_is_configurable():
    t = tracker(max_range_km=50)
    assert t.handle(pos(lat=51.80, lon=2.80), now=0) == []  # ~67 km


def test_impossible_jump_is_rejected_and_ship_stays_put():
    t = tracker()
    t.handle(pos(lat=51.40), now=0)
    # 0,09° noord = 10,0 km in 300 s = 64,8 kn > 60 kn
    assert t.handle(pos(lat=51.49), now=300) == []
    assert t.implausible == 1
    assert t.live_vessels()[0]["lat"] == 51.40


def test_fast_but_possible_move_is_accepted():
    t = tracker()
    t.handle(pos(lat=51.40), now=0)
    # 10,0 km in 330 s = 58,9 kn
    assert t.handle(pos(lat=51.49), now=330) != []
    assert t.live_vessels()[0]["lat"] == 51.49


def test_jump_after_the_previous_position_went_stale_is_accepted():
    t = tracker(stale_after_s=600)
    t.handle(pos(lat=51.40), now=0)
    t.handle(pos(lat=51.60), now=601)  # 22 km in 601 s = 72 kn, maar vorige is oud
    assert t.live_vessels()[0]["lat"] == 51.60


def test_small_jitter_within_the_same_second_is_accepted():
    t = tracker()
    t.handle(pos(lat=51.40), now=0)
    t.handle(pos(lat=51.4005), now=0)  # 56 m
    assert t.implausible == 0


def test_after_three_rejections_the_new_position_becomes_the_base():
    t = tracker()
    t.handle(pos(lat=51.40), now=0)  # later blijkt dit de glitch
    for now in (10, 20, 30):
        assert t.handle(pos(lat=51.30), now=now) == []
    assert t.handle(pos(lat=51.30), now=40) != []
    assert t.implausible == 3
    assert t.live_vessels()[0]["lat"] == 51.30


def test_accepted_position_resets_the_rejection_count():
    t = tracker()
    t.handle(pos(lat=51.40), now=0)
    t.handle(pos(lat=51.30), now=10)
    t.handle(pos(lat=51.30), now=20)
    t.handle(pos(lat=51.40), now=30)  # weer consistent
    for now in (40, 50, 60):
        assert t.handle(pos(lat=51.30), now=now) == []


# --- uitdunnen van sporen ----------------------------------------------------


def test_moving_ship_is_stored_every_30_seconds():
    t = tracker()
    reports = [(now, pos(sog=10.0)) for now in range(0, 91, 10)]
    assert track_times(t, reports) == [0, 30, 60, 90]


def test_stationary_ship_is_stored_every_5_minutes():
    t = tracker()
    reports = [(now, pos(sog=0.1, nav_status=1)) for now in range(0, 601, 60)]
    assert track_times(t, reports) == [0, 300, 600]


def test_course_change_adds_a_track_point():
    t = tracker()
    reports = [(0, pos(cog=90.0)), (10, pos(cog=95.0)), (20, pos(cog=101.0))]
    assert track_times(t, reports) == [0, 20]


def test_course_change_across_north_is_measured_the_short_way():
    t = tracker()
    reports = [(0, pos(cog=355.0)), (10, pos(cog=3.0)), (20, pos(cog=6.0))]
    assert track_times(t, reports) == [0, 20]


def test_course_change_of_a_stationary_ship_is_ignored():
    t = tracker()
    reports = [(0, pos(sog=0.1, cog=10.0)), (10, pos(sog=0.1, cog=200.0))]
    assert track_times(t, reports) == [0]


def test_nav_status_change_adds_a_track_point():
    t = tracker()
    reports = [(0, pos(nav_status=0)), (10, pos(nav_status=0)), (20, pos(nav_status=1))]
    assert track_times(t, reports) == [0, 20]


# --- uur-aggregaten en snelheid -----------------------------------------------


def test_every_position_counts_in_the_hour_it_was_received():
    t = tracker()
    [sample] = of_type(t.handle(pos(), now=7200 + 1234), HourSample)
    assert (sample.hour_ts, sample.mmsi) == (7200, MMSI)
    assert sample.distance_m == pytest.approx(4659, abs=20)


def test_speed_is_sampled_at_most_every_30_seconds():
    t = tracker()
    speeds = [
        of_type(t.handle(pos(sog=12.0), now=now), HourSample)[0].speed
        for now in (0, 10, 20, 30)
    ]
    assert speeds == [12.0, None, None, 12.0]


@pytest.mark.parametrize("sog", [None, 0.0, 0.3, 0.9])
def test_speed_below_one_knot_is_not_sampled(sog):
    t = tracker()
    [sample] = of_type(t.handle(pos(sog=sog), now=0), HourSample)
    assert sample.speed is None


# --- afstandsrecord ----------------------------------------------------------


def test_first_position_of_a_ship_cannot_set_a_record():
    t = tracker()
    assert of_type(t.handle(pos(lat=51.80, lon=2.80), now=0), RangeRecord) == []
    assert t.record is None


def test_confirmed_position_sets_the_record():
    t = tracker()
    t.handle(pos(lat=51.80, lon=2.80), now=0)
    [record] = of_type(t.handle(pos(lat=51.80, lon=2.80), now=30), RangeRecord)
    assert (record.ts, record.mmsi, record.lat, record.lon) == (30, MMSI, 51.80, 2.80)
    assert record.distance_m == pytest.approx(67_072, abs=300)
    assert t.record == record


def test_only_a_farther_position_breaks_the_record():
    t = tracker()
    t.handle(pos(lat=51.80, lon=2.80), now=0)
    t.handle(pos(lat=51.80, lon=2.80), now=30)
    t.handle(pos(lat=51.60, lon=3.00, mmsi=2), now=40)
    assert of_type(t.handle(pos(lat=51.60, lon=3.00, mmsi=2), now=70), RangeRecord) == []
    t.handle(pos(lat=52.00, lon=2.60, mmsi=3), now=80)
    assert len(of_type(t.handle(pos(lat=52.00, lon=2.60, mmsi=3), now=110), RangeRecord)) == 1


def test_position_accepted_without_speed_check_cannot_set_a_record():
    t = tracker(stale_after_s=600)
    t.handle(pos(lat=51.80, lon=2.80), now=0)
    assert of_type(t.handle(pos(lat=51.80, lon=2.80), now=700), RangeRecord) == []


# --- statische gegevens -------------------------------------------------------


def test_static_data_is_merged_and_shown_live():
    t = tracker()
    t.handle(pos(), now=0)
    t.drain_changes()
    events = t.handle(
        StaticData(5, MMSI, "A", name="SCHELDE", ship_type=70, length=180, beam=28), now=5
    )
    assert events == [
        VesselUpdate(MMSI, 5, "A", {"name": "SCHELDE", "ship_type": 70, "length": 180, "beam": 28})
    ]
    t.handle(StaticData(24, MMSI, "B", callsign="PA1B"), now=6)
    live = t.live_vessels()[0]
    assert (live["name"], live["type_group"], live["length"]) == ("SCHELDE", "cargo", 180)
    assert t.drain_changes() == ({MMSI}, set())


def test_static_data_before_any_position_is_not_live():
    t = tracker()
    t.handle(StaticData(5, MMSI, "A", name="SCHELDE"), now=0)
    assert t.live_vessels() == []
    t.handle(pos(), now=1)
    assert t.live_vessels()[0]["name"] == "SCHELDE"


def test_unknown_ship_gets_static_data_from_the_lookup():
    calls = []

    def lookup(mmsi):
        calls.append(mmsi)
        return {"name": "WESTERSCHELDE", "ship_type": 80, "length": 250}

    t = tracker(static_lookup=lookup)
    t.handle(pos(), now=0)
    t.handle(pos(), now=10)
    live = t.live_vessels()[0]
    assert (live["name"], live["type_group"], live["length"]) == ("WESTERSCHELDE", "tanker", 250)
    assert calls == [MMSI]


# --- verouderen en herstellen -------------------------------------------------


def test_sweep_removes_ships_without_recent_position():
    t = tracker(stale_after_s=600)
    t.handle(pos(), now=0)
    t.handle(pos(mmsi=2), now=500)
    t.drain_changes()
    t.sweep(now=601)
    assert [v["mmsi"] for v in t.live_vessels()] == [2]
    assert t.drain_changes() == (set(), {MMSI})
    t.sweep(now=602)
    assert t.drain_changes() == (set(), set())


def test_ship_that_returns_after_a_day_is_looked_up_again():
    calls = []
    t = tracker(static_lookup=lambda mmsi: calls.append(mmsi) or None)
    t.handle(pos(), now=0)
    t.sweep(now=90_000)
    t.handle(pos(), now=90_010)
    assert calls == [MMSI, MMSI]


def test_restored_ships_are_live_and_speed_checked():
    t = tracker()
    t.restore(
        vessels=[{"mmsi": MMSI, "ais_class": "A", "name": "HERSTELD", "ship_type": 70,
                  "lat": 51.40, "lon": 3.60, "sog": 10.0, "cog": 90.0, "heading": 90,
                  "nav_status": 0, "position_ts": 1000, "first_seen": 10}],
        record=RangeRecord(900, 7, 51.8, 2.8, 67_000.0),
    )
    assert t.live_vessels()[0]["name"] == "HERSTELD"
    assert t.record.distance_m == 67_000.0
    assert t.handle(pos(lat=51.49), now=1300) == []  # 64,8 kn t.o.v. herstelde positie


# --- bereik per richting ------------------------------------------------------


def test_sector_sample_after_a_speed_checked_position():
    t = tracker()
    assert of_type(t.handle(pos(lat=51.40, lon=3.60), now=86_400 + 10), SectorSample) == []
    [sample] = of_type(t.handle(pos(lat=51.40, lon=3.601), now=86_400 + 40), SectorSample)
    # Vanaf het station (51.44, 3.58) ligt dit punt op ~162°: sector 16 (160–170°).
    assert (sample.day_ts, sample.sector, sample.mmsi) == (86_400, 16, MMSI)
    assert sample.distance_m == pytest.approx(4_700, abs=100)


def test_no_sector_sample_after_a_stale_position():
    t = tracker()
    t.handle(pos(), now=0)
    assert of_type(t.handle(pos(), now=700), SectorSample) == []


# --- doorvaartlijn --------------------------------------------------------------

GATE = GateConfig(name="Test", lat1=51.46, lon1=3.64, lat2=51.38, lon2=3.64)


def crossings(t, *points, sog=10.0):
    """Voert posities (lat, lon) 30 s na elkaar in; geeft alle kruisingen."""
    return [
        crossing
        for i, (lat, lon) in enumerate(points)
        for crossing in of_type(t.handle(pos(lat=lat, lon=lon, sog=sog), now=30 * i), GateCrossing)
    ]


def test_crossing_west_to_east_goes_up_the_river():
    assert crossings(tracker(gate=GATE), (51.42, 3.638), (51.42, 3.642)) == [
        GateCrossing(30, MMSI, upstream=True)
    ]


def test_crossing_east_to_west_goes_down_the_river():
    assert crossings(tracker(gate=GATE), (51.42, 3.642), (51.42, 3.638)) == [
        GateCrossing(30, MMSI, upstream=False)
    ]


def test_position_exactly_on_the_line_counts_once():
    t = tracker(gate=GATE)
    assert len(crossings(t, (51.42, 3.638), (51.42, 3.640), (51.42, 3.642))) == 1


def test_no_crossing_beyond_the_end_of_the_line():
    assert crossings(tracker(gate=GATE), (51.36, 3.638), (51.36, 3.642)) == []


def test_slow_or_unknown_speed_does_not_cross():
    # Ankerliggers met GPS-ruis mogen de telling niet opdrijven.
    assert crossings(tracker(gate=GATE), (51.42, 3.6399), (51.42, 3.6401), sog=0.3) == []
    assert crossings(tracker(gate=GATE), (51.42, 3.638), (51.42, 3.642), sog=None) == []


def test_no_crossing_after_a_stale_position():
    t = tracker(gate=GATE)
    t.handle(pos(lat=51.42, lon=3.638), now=0)
    assert of_type(t.handle(pos(lat=51.42, lon=3.642), now=700), GateCrossing) == []


def test_without_a_gate_nothing_is_counted():
    assert crossings(tracker(), (51.42, 3.638), (51.42, 3.642)) == []


def test_touching_the_line_and_turning_back_counts_nothing():
    # AIS-posities zijn veelvouden van 1/600000°; 3,640° is er exact een van.
    assert crossings(tracker(gate=GATE), (51.42, 3.642), (51.42, 3.640), (51.42, 3.642)) == []
    assert crossings(tracker(gate=GATE), (51.42, 3.638), (51.42, 3.640), (51.42, 3.638)) == []


def test_drifting_across_slowly_is_not_counted_later():
    t = tracker(gate=GATE)
    assert crossings(t, (51.42, 3.6399), (51.42, 3.6401), sog=0.3) == []
    assert of_type(t.handle(pos(lat=51.42, lon=3.6405, sog=10.0), now=60), GateCrossing) == []


def test_crossing_right_after_a_restart_is_counted():
    t = tracker(gate=GATE)
    t.restore(
        vessels=[{"mmsi": MMSI, "ais_class": "A", "lat": 51.42, "lon": 3.638, "sog": 10.0, "cog": 90.0,
                  "heading": 90, "nav_status": 0, "position_ts": 1000, "first_seen": 10}],
        record=None,
    )
    assert of_type(t.handle(pos(lat=51.42, lon=3.642), now=1030), GateCrossing) == [
        GateCrossing(1030, MMSI, upstream=True)
    ]
