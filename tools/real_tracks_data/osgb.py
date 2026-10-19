"""WGS84 lat/lon -> OSGB36 British National Grid (EPSG:27700), stdlib + numpy.

Uses the OS 7-parameter Helmert (WGS84 -> OSGB36) and the Airy 1830 transverse
Mercator. Absolute accuracy is about 2-5 m against OSTN15 (the official grid
transform), which is within the positional noise of OSM itself; both the OSM
centreline and the LIDAR are then in the same metric frame, and the residual is
handled by registering the centreline to the DTM (see process_anglesey.py).
"""
from __future__ import annotations

import numpy as np

# WGS84
_A84, _B84 = 6378137.0, 6356752.314245
# Airy 1830
_AA, _BA = 6377563.396, 6356256.909
# Helmert WGS84 -> OSGB36 (OS guide, "A guide to coordinate systems in Great Britain")
_TX, _TY, _TZ = -446.448, 125.157, -542.060
_S = 20.4894e-6
_RX, _RY, _RZ = np.radians(np.array([-0.1502, -0.2470, -0.8421]) / 3600.0)
# National Grid TM
_F0 = 0.9996012717
_LAT0, _LON0 = np.radians(49.0), np.radians(-2.0)
_E0, _N0 = 400000.0, -100000.0


def _to_cart(lat, lon, a, b, h=0.0):
    e2 = 1 - (b * b) / (a * a)
    nu = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
    x = (nu + h) * np.cos(lat) * np.cos(lon)
    y = (nu + h) * np.cos(lat) * np.sin(lon)
    z = ((1 - e2) * nu + h) * np.sin(lat)
    return x, y, z


def _to_geo(x, y, z, a, b):
    e2 = 1 - (b * b) / (a * a)
    p = np.sqrt(x * x + y * y)
    lat = np.arctan2(z, p * (1 - e2))
    for _ in range(10):
        nu = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
        lat = np.arctan2(z + e2 * nu * np.sin(lat), p)
    return lat, np.arctan2(y, x)


def _tm(lat, lon):
    a, b, F0 = _AA, _BA, _F0
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)
    nu = a * F0 / np.sqrt(1 - e2 * np.sin(lat) ** 2)
    rho = a * F0 * (1 - e2) / (1 - e2 * np.sin(lat) ** 2) ** 1.5
    eta2 = nu / rho - 1
    dl, sl = lat - _LAT0, lat + _LAT0
    M = b * F0 * ((1 + n + 1.25 * n ** 2 + 1.25 * n ** 3) * dl
                  - (3 * n + 3 * n ** 2 + 21 / 8 * n ** 3) * np.sin(dl) * np.cos(sl)
                  + (15 / 8 * n ** 2 + 15 / 8 * n ** 3) * np.sin(2 * dl) * np.cos(2 * sl)
                  - 35 / 24 * n ** 3 * np.sin(3 * dl) * np.cos(3 * sl))
    c, s, t = np.cos(lat), np.sin(lat), np.tan(lat)
    I = M + _N0
    II = nu / 2 * s * c
    III = nu / 24 * s * c ** 3 * (5 - t ** 2 + 9 * eta2)
    IIIA = nu / 720 * s * c ** 5 * (61 - 58 * t ** 2 + t ** 4)
    IV = nu * c
    V = nu / 6 * c ** 3 * (nu / rho - t ** 2)
    VI = nu / 120 * c ** 5 * (5 - 18 * t ** 2 + t ** 4 + 14 * eta2 - 58 * t ** 2 * eta2)
    d = lon - _LON0
    N = I + II * d ** 2 + III * d ** 4 + IIIA * d ** 6
    E = _E0 + IV * d + V * d ** 3 + VI * d ** 5
    return E, N


def wgs84_to_bng(lat_deg, lon_deg):
    lat, lon = np.radians(np.asarray(lat_deg, float)), np.radians(np.asarray(lon_deg, float))
    x, y, z = _to_cart(lat, lon, _A84, _B84)
    x2 = _TX + (1 + _S) * x - _RZ * y + _RY * z
    y2 = _TY + _RZ * x + (1 + _S) * y - _RX * z
    z2 = _TZ - _RY * x + _RX * y + (1 + _S) * z
    lat2, lon2 = _to_geo(x2, y2, z2, _AA, _BA)
    return _tm(lat2, lon2)


if __name__ == "__main__":
    # OS worked example-ish check: Caernarfon castle approx (53.1393, -4.2768) -> ~ (247770, 362580)
    print(wgs84_to_bng(53.1393, -4.2768))
    print(wgs84_to_bng(53.1886, -4.4966))
