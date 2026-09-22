import pytest

from aisws.geo import haversine_m


def test_one_degree_of_latitude():
    assert haversine_m(0, 0, 1, 0) == pytest.approx(111_195, abs=10)


def test_vlissingen_to_antwerp():
    # Vlissingen (51.4426, 3.5736) → Antwerpen (51.2194, 4.4025): ~62,7 km hemelsbreed.
    assert haversine_m(51.4426, 3.5736, 51.2194, 4.4025) == pytest.approx(62_700, abs=500)


def test_same_point_is_zero():
    assert haversine_m(51.4, 3.6, 51.4, 3.6) == 0
