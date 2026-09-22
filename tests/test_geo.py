import pytest

from aisws.geo import bearing_deg, haversine_m


def test_one_degree_of_latitude():
    assert haversine_m(0, 0, 1, 0) == pytest.approx(111_195, abs=10)


def test_vlissingen_to_antwerp():
    # Vlissingen (51.4426, 3.5736) → Antwerpen (51.2194, 4.4025): ~62,7 km hemelsbreed.
    assert haversine_m(51.4426, 3.5736, 51.2194, 4.4025) == pytest.approx(62_700, abs=500)


def test_same_point_is_zero():
    assert haversine_m(51.4, 3.6, 51.4, 3.6) == 0


@pytest.mark.parametrize(
    "lat, lon, expected",
    [(52.0, 3.6, 0.0), (51.4, 4.0, 90.0), (51.0, 3.6, 180.0), (51.4, 3.0, 270.0)],
)
def test_bearing_to_the_four_quarters(lat, lon, expected):
    # Oost en west wijken op deze breedte minder dan een halve graad af van 90/270.
    assert bearing_deg(51.4, 3.6, lat, lon) == pytest.approx(expected, abs=0.5)


def test_bearing_vlissingen_to_antwerp():
    # Antwerpen ligt vanaf Vlissingen iets zuid van oost: 113,0° (nagerekend via ECEF-vectoren).
    assert bearing_deg(51.4426, 3.5736, 51.2194, 4.4025) == pytest.approx(113.0, abs=0.2)


def test_bearing_is_never_360():
    assert 0 <= bearing_deg(51.4, 3.6, 51.5, 3.6 - 1e-12) < 360
