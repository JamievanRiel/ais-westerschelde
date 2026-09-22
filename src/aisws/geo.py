"""Afstanden op het aardoppervlak."""

from math import asin, atan2, cos, degrees, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_008.8
METERS_PER_NM = 1852.0
MPS_PER_KNOT = METERS_PER_NM / 3600


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi, dlambda = phi2 - phi1, radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Beginkoers van de grootcirkel van punt 1 naar punt 2: 0 = noord, met de klok mee."""
    phi1, phi2 = radians(lat1), radians(lat2)
    dlambda = radians(lon2 - lon1)
    y = sin(dlambda) * cos(phi2)
    x = cos(phi1) * sin(phi2) - sin(phi1) * cos(phi2) * cos(dlambda)
    bearing = degrees(atan2(y, x)) % 360
    return 0.0 if bearing >= 360 else bearing
