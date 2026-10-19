"""Import a real circuit into a TrackSpec with an exact centreline, heights, camber and terrain.

    py tools/import_real_track.py tools/real_tracks/cadwell.json --out geom/tests/samples/realcadwell.json

Data (downloaded once and cached under E:\\Source\\wreckfest\\.local\\<slug>\\, never committed):

- **Layout:** OpenStreetMap ``highway=raceway`` ways listed in the recipe's ``lap`` (in driving order), plus
  ``landuse=forest`` / ``natural=wood`` polygons, from the Overpass API. (c) OpenStreetMap contributors, ODbL.
- **Heights:** the Environment Agency LIDAR Composite DTM 1 m (bare earth, Open Government Licence v3) through
  its WCS, in British National Grid (EPSG:27700).

Pipeline: OSM WGS84 -> OSGB36 (7-parameter Helmert) -> British National Grid metres -> local frame (x east,
y north, metres, divided by the grid's local scale factor). The OSM line is then registered to the DTM by
the shift that makes the road surface smoothest (OSM traces and the Helmert transform are each a few metres
out), smoothed and resampled every 2 m. Heights are the DTM along the centreline (median of a small
cross-track patch, then smoothed along the road), the camber is a least-squares cross-track slope over the
inner 80 % of the width, and the terrain is the DTM averaged onto a coarser grid.

Only numpy and the standard library are needed (the DTM is requested as an ESRI ASCII grid).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

USER_AGENT = "WreckfestTrackGen/0.1 (private track importer)"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
EA_WCS = ("https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs")
EA_COVERAGE = "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
DEFAULT_CACHE_ROOT = Path(r"E:\Source\wreckfest\.local")  # downloads go to <root>/<slug>/ (gitignored)

ATTRIBUTION = ("Layout and woodland (c) OpenStreetMap contributors, ODbL 1.0. Heights: Environment Agency "
               "LIDAR Composite DTM 1 m, (c) Environment Agency copyright and/or database right 2022, "
               "Open Government Licence v3.0.")

# ------------------------------------------------------------------------------------------------ geodesy
WGS84 = (6378137.000, 6356752.3141)
AIRY1830 = (6377563.396, 6356256.909)
# WGS84 -> OSGB36 (Ordnance Survey "A guide to coordinate systems in Great Britain", ~2-5 m accuracy).
HELMERT = dict(tx=-446.448, ty=125.157, tz=-542.060, s=20.4894e-6, rx=-0.1502, ry=-0.2470, rz=-0.8421)
NG = dict(F0=0.9996012717, lat0=math.radians(49.0), lon0=math.radians(-2.0), E0=400000.0, N0=-100000.0)


def _to_cartesian(lat: float, lon: float, h: float, ell: tuple[float, float]) -> np.ndarray:
    a, b = ell
    e2 = 1 - (b * b) / (a * a)
    nu = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return np.array([(nu + h) * math.cos(lat) * math.cos(lon), (nu + h) * math.cos(lat) * math.sin(lon),
                     ((1 - e2) * nu + h) * math.sin(lat)])


def _to_latlon(p: np.ndarray, ell: tuple[float, float]) -> tuple[float, float]:
    a, b = ell
    e2 = 1 - (b * b) / (a * a)
    lon = math.atan2(p[1], p[0])
    r = math.hypot(p[0], p[1])
    lat = math.atan2(p[2], r * (1 - e2))
    for _ in range(10):
        nu = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        lat = math.atan2(p[2] + e2 * nu * math.sin(lat), r)
    return lat, lon


def wgs84_to_osgb36(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    """WGS84 degrees -> OSGB36 radians (Helmert)."""
    p = _to_cartesian(math.radians(lat_deg), math.radians(lon_deg), 0.0, WGS84)
    sec = math.radians(1.0 / 3600.0)
    rx, ry, rz = HELMERT["rx"] * sec, HELMERT["ry"] * sec, HELMERT["rz"] * sec
    s = 1 + HELMERT["s"]
    rot = np.array([[1, -rz, ry], [rz, 1, -rx], [-ry, rx, 1]])
    q = np.array([HELMERT["tx"], HELMERT["ty"], HELMERT["tz"]]) + s * rot @ p
    return _to_latlon(q, AIRY1830)


def tm_project(lat: float, lon: float) -> tuple[float, float]:
    """OSGB36 latitude/longitude (radians) -> British National Grid easting/northing (OS formulae)."""
    a, b = AIRY1830
    F0, lat0, lon0, E0, N0 = NG["F0"], NG["lat0"], NG["lon0"], NG["E0"], NG["N0"]
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)
    sl, cl = math.sin(lat), math.cos(lat)
    nu = a * F0 / math.sqrt(1 - e2 * sl * sl)
    rho = a * F0 * (1 - e2) / (1 - e2 * sl * sl) ** 1.5
    eta2 = nu / rho - 1
    dl, sl0 = lat - lat0, lat + lat0
    M = b * F0 * ((1 + n + 1.25 * n**2 + 1.25 * n**3) * dl
                  - (3 * n + 3 * n**2 + 21 / 8 * n**3) * math.sin(dl) * math.cos(sl0)
                  + (15 / 8 * n**2 + 15 / 8 * n**3) * math.sin(2 * dl) * math.cos(2 * sl0)
                  - 35 / 24 * n**3 * math.sin(3 * dl) * math.cos(3 * sl0))
    t = math.tan(lat)
    I = M + N0
    II = nu / 2 * sl * cl
    III = nu / 24 * sl * cl**3 * (5 - t * t + 9 * eta2)
    IIIA = nu / 720 * sl * cl**5 * (61 - 58 * t * t + t**4)
    IV = nu * cl
    V = nu / 6 * cl**3 * (nu / rho - t * t)
    VI = nu / 120 * cl**5 * (5 - 18 * t * t + t**4 + 14 * eta2 - 58 * t * t * eta2)
    d = lon - lon0
    N = I + II * d**2 + III * d**4 + IIIA * d**6
    E = E0 + IV * d + V * d**3 + VI * d**5
    return E, N


def grid_scale_factor(lat: float, lon: float) -> float:
    """Point scale factor of the National Grid at OSGB36 (lat, lon) radians."""
    a, b = AIRY1830
    e2 = 1 - (b * b) / (a * a)
    sl = math.sin(lat)
    nu = a * NG["F0"] / math.sqrt(1 - e2 * sl * sl)
    rho = a * NG["F0"] * (1 - e2) / (1 - e2 * sl * sl) ** 1.5
    d = lon - NG["lon0"]
    cl = math.cos(lat)
    return NG["F0"] * (1 + (d * cl) ** 2 / 2 * (nu / rho) + (d * cl) ** 4 / 24 * (5 - 4 * math.tan(lat) ** 2))


def to_bng(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    return tm_project(*wgs84_to_osgb36(lat_deg, lon_deg))


# ------------------------------------------------------------------------------------------------ fetching
def _get(url: str, data: bytes | None = None, timeout: float = 120.0) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_osm(bbox: list[float], cache: Path) -> dict:
    path = cache / "osm.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    s, w, n, e = bbox
    b = f"({s},{w},{n},{e})"
    q = (f'[out:json][timeout:90];(way["highway"="raceway"]{b};way["landuse"="forest"]{b};'
         f'way["natural"="wood"]{b};relation["natural"="wood"]{b};relation["landuse"="forest"]{b};);'
         "out body geom;")
    last = None
    for url in OVERPASS:
        try:
            raw = _get(url, urllib.parse.urlencode({"data": q}).encode())
            d = json.loads(raw)
            cache.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            return d
        except Exception as exc:  # try the next mirror
            last = exc
    raise RuntimeError(f"Overpass failed: {last}")


def parse_geoserver_text(text: str) -> tuple[float, float, np.ndarray]:
    """GeoServer's WCS ``text/plain`` coverage: a header with the "Grid to world" affine (cell centre of
    column 0 / row 0 in ``elt_0_2`` / ``elt_1_2``, row step ``elt_1_1`` = -1 for north-up), then "Band 0:" and
    one line of values per row. Returns (x of column 0 centre, y of row 0 centre, z[row, col])."""
    head, _, body = text.partition("Band 0:")
    m = {k: float(v) for k, v in re.findall(r'PARAMETER\["(elt_\d_\d)", ([-0-9.eE]+)\]', head)}
    if m.get("elt_1_1", -1.0) != -1.0 or m.get("elt_0_0", 1.0) != 1.0:
        raise RuntimeError(f"unexpected DTM grid affine {m}")
    rows = [r.split() for r in body.strip().splitlines() if r.strip()]
    z = np.array(rows, dtype=np.float64)
    z[(z < -100) | (z > 5000)] = np.nan  # no-data
    return m["elt_0_2"], m["elt_1_2"], z


class Dtm:
    """A north-up 1 m grid in BNG: ``z[row, col]`` with row 0 at the north edge. Cell centres at
    (x0 + (col + 0.5) * cell, y1 - (row + 0.5) * cell)."""

    def __init__(self, x0: float, y1: float, cell: float, z: np.ndarray):
        self.x0, self.y1, self.cell, self.z = x0, y1, cell, z

    def sample(self, e: np.ndarray, n: np.ndarray) -> np.ndarray:
        """Bilinear sample at BNG coordinates (NaN outside)."""
        c = (np.asarray(e) - self.x0) / self.cell - 0.5
        r = (self.y1 - np.asarray(n)) / self.cell - 0.5
        c0 = np.floor(c).astype(int)
        r0 = np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        h, w = self.z.shape
        ok = (c0 >= 0) & (r0 >= 0) & (c0 + 1 < w) & (r0 + 1 < h)
        c0c, r0c = np.clip(c0, 0, w - 2), np.clip(r0, 0, h - 2)
        z = self.z
        v = (z[r0c, c0c] * (1 - fc) * (1 - fr) + z[r0c, c0c + 1] * fc * (1 - fr)
             + z[r0c + 1, c0c] * (1 - fc) * fr + z[r0c + 1, c0c + 1] * fc * fr)
        return np.where(ok, v, np.nan)


def fetch_dtm(e0: float, n0: float, e1: float, n1: float, cache: Path, tile: int = 1000) -> Dtm:
    """The DTM over [e0, e1] x [n0, n1] (BNG metres), fetched in tiles of ``tile`` m and cached."""
    e0, n0 = math.floor(e0 / tile) * tile, math.floor(n0 / tile) * tile
    e1, n1 = math.ceil(e1 / tile) * tile, math.ceil(n1 / tile) * tile
    nx, ny = int((e1 - e0) / tile), int((n1 - n0) / tile)
    full = np.full((ny * tile, nx * tile), np.nan)
    tdir = cache / "dtm"
    tdir.mkdir(parents=True, exist_ok=True)
    for ty in range(ny):
        for tx in range(nx):
            te, tn = e0 + tx * tile, n0 + ty * tile
            path = tdir / f"dtm1m_{te:.0f}_{tn:.0f}_{tile}.npy"
            if not path.exists():
                params = [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"),
                          ("CoverageId", EA_COVERAGE), ("format", "text/plain"),
                          ("subset", f"E({te},{te + tile})"), ("subset", f"N({tn},{tn + tile})")]
                url = EA_WCS + "?" + urllib.parse.urlencode(params)
                for attempt in range(3):
                    try:
                        raw = _get(url, timeout=300)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(5)
                text = raw.decode("ascii", "replace")
                if "Band 0:" not in text:
                    raise RuntimeError(f"DTM tile {te},{tn}: unexpected response {text[:300]!r}")
                xc, yc, z = parse_geoserver_text(text)
                if abs(xc - (te + 0.5)) > 1e-6 or abs(yc - (tn + tile - 0.5)) > 1e-6 or z.shape != (tile, tile):
                    raise RuntimeError(f"DTM tile {te},{tn}: got origin {xc},{yc} shape {z.shape}")
                np.save(path, z.astype(np.float32))
            z = np.load(path).astype(np.float64)
            r0 = int(round(n1 - (tn + tile)))
            c0 = int(round(te - e0))
            full[r0:r0 + tile, c0:c0 + tile] = z
    return Dtm(e0, n1, 1.0, full)


# ------------------------------------------------------------------------------------------------ layout
def stitch(osm: dict, lap: list[int]) -> tuple[list[tuple[float, float]], list[tuple[int, int, str]]]:
    """The lap's way geometries joined end to end (each way must start where the previous one ends).
    Returns the lat/lon points (closed loop, first point not repeated) and (way id, first point index, name)."""
    ways = {e["id"]: e for e in osm["elements"] if e["type"] == "way"}
    pts: list[tuple[float, float]] = []
    marks: list[tuple[int, int, str]] = []
    prev_end = None
    for wid in lap:
        w = ways[wid]
        nodes, geom = w["nodes"], w["geometry"]
        if prev_end is not None and nodes[0] != prev_end:
            raise ValueError(f"way {wid} doesn't start where the previous way ends ({nodes[0]} != {prev_end})")
        marks.append((wid, len(pts), w.get("tags", {}).get("name", "")))
        seq = [(g["lat"], g["lon"]) for g in geom]
        pts.extend(seq if not pts else seq[1:])
        prev_end = nodes[-1]
    if prev_end != ways[lap[0]]["nodes"][0]:
        raise ValueError("the lap doesn't close")
    return pts[:-1], marks


def woods(osm: dict) -> list[list[tuple[float, float]]]:
    out = []
    for e in osm["elements"]:
        t = e.get("tags", {})
        if not (t.get("landuse") == "forest" or t.get("natural") == "wood"):
            continue
        if e["type"] == "way" and e.get("geometry"):
            out.append([(g["lat"], g["lon"]) for g in e["geometry"]])
        elif e["type"] == "relation":
            for m in e.get("members", []):
                if m.get("role") == "outer" and m.get("geometry"):
                    out.append([(g["lat"], g["lon"]) for g in m["geometry"]])
    return out


def resample_closed(p: np.ndarray, step: float) -> np.ndarray:
    q = np.vstack([p, p[:1]])
    seg = np.hypot(*np.diff(q, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    n = max(8, int(round(cum[-1] / step)))
    t = np.arange(n) * (cum[-1] / n)
    return np.column_stack([np.interp(t, cum, q[:, 0]), np.interp(t, cum, q[:, 1])])


def gauss_closed(v: np.ndarray, sigma_samples: float) -> np.ndarray:
    """Circular Gaussian smoothing along axis 0."""
    if sigma_samples <= 0:
        return v.copy()
    r = int(math.ceil(4 * sigma_samples))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma_samples) ** 2)
    k /= k.sum()
    v2 = v.reshape(len(v), -1)
    out = np.empty_like(v2, dtype=np.float64)
    n = len(v)
    for c in range(v2.shape[1]):
        pad = np.concatenate([v2[-r:, c], v2[:, c], v2[:r, c]]) if r < n else np.resize(v2[:, c], n + 2 * r)
        out[:, c] = np.convolve(pad, k, mode="valid")
    return out.reshape(v.shape)


def normals(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unit tangents and left normals of a closed polyline (central differences)."""
    d = np.roll(p, -1, axis=0) - np.roll(p, 1, axis=0)
    t = d / np.maximum(np.hypot(*d.T), 1e-9)[:, None]
    return t, np.column_stack([-t[:, 1], t[:, 0]])


def signed_area(p: np.ndarray) -> float:
    return 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]))


def radii(p: np.ndarray, k: int = 3) -> np.ndarray:
    a, b, c = np.roll(p, k, axis=0), p, np.roll(p, -k, axis=0)
    ab, bc, ca = np.hypot(*(b - a).T), np.hypot(*(c - b).T), np.hypot(*(a - c).T)
    cross = np.abs((b - a)[:, 0] * (c - a)[:, 1] - (b - a)[:, 1] * (c - a)[:, 0])
    return np.where(cross > 1e-9, ab * bc * ca / (2 * np.maximum(cross, 1e-12)), 1e6)


# ------------------------------------------------------------------------------------------------ fitting
def cross_fit(dtm: Dtm, p: np.ndarray, nl: np.ndarray, offsets: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Least-squares line z = a + b * offset across the road at each point (``offsets`` along the left
    normal). Returns intercepts a (height at the point), slopes b (rise per metre to the left) and the RMS
    residual (how far the cross-section is from a plane: banks, ditches and kerbs make it large)."""
    q = p[:, None, :] + offsets[None, :, None] * nl[:, None, :]
    z = dtm.sample(q[..., 0].ravel(), q[..., 1].ravel()).reshape(len(p), len(offsets))
    o = offsets - offsets.mean()
    zm = np.nanmean(z, axis=1)
    b = np.nansum((z - zm[:, None]) * o[None, :], axis=1) / float(np.sum(o * o))
    a = zm - b * offsets.mean()
    res = np.sqrt(np.nanmean((z - (a[:, None] + b[:, None] * offsets[None, :])) ** 2, axis=1))
    return a, b, res


def register(dtm: Dtm, p: np.ndarray, nl: np.ndarray, width: float, step: float,
             max_shift: float = 6.0, shift_step: float = 0.5, smooth_m: float = 12.0) -> np.ndarray:
    """Lateral correction (metres along the left normal) per sample that puts the line on the flattest
    road-wide strip of the DTM. The cost of a shift is the RMS residual of a plane fitted across the whole
    road width (so the verges on both sides count and the line is centred), averaged over +-``smooth_m``; a
    dynamic programme keeps the shift smooth along the road (and near zero where the ground is flat
    everywhere, e.g. beside a paddock)."""
    shifts = np.arange(-max_shift, max_shift + 1e-9, shift_step)
    half = 0.5 * width
    offs = np.arange(-half, half + 1e-9, 0.5)
    cost = np.empty((len(p), len(shifts)))
    for k, d in enumerate(shifts):
        _, _, res = cross_fit(dtm, p + d * nl, nl, offs)
        cost[:, k] = res
    cost = gauss_closed(np.nan_to_num(cost, nan=1.0), smooth_m / step)
    cost = cost + 0.004 * np.abs(shifts)[None, :]  # prefer the OSM line when the ground is flat anyway
    # Dynamic programme along the (opened) loop: a cost for changing the shift between samples.
    move = 0.02 * ((shifts[:, None] - shifts[None, :]) / shift_step) ** 2
    acc = cost[0].copy()
    back = np.zeros((len(p), len(shifts)), dtype=int)
    for i in range(1, len(p)):
        tot = acc[:, None] + move
        back[i] = np.argmin(tot, axis=0)
        acc = tot[back[i], np.arange(len(shifts))] + cost[i]
    path = np.empty(len(p), dtype=int)
    path[-1] = int(np.argmin(acc))
    for i in range(len(p) - 1, 0, -1):
        path[i - 1] = back[i, path[i]]
    return gauss_closed(shifts[path].astype(np.float64), 5.0 / step)


def wrap_s(d: np.ndarray, length: float) -> np.ndarray:
    return (d + length / 2.0) % length - length / 2.0


def ease_corners(p: np.ndarray, min_radius: float, max_iter: int = 400) -> tuple[np.ndarray, float]:
    """Laplacian-relax the samples (and two either side) where the radius is under ``min_radius``, as the
    geometry core does for sketches, so the real line passes rule min-corner-radius. Returns the new points and
    the largest distance any sample moved."""
    q = p.copy()
    for _ in range(max_iter):
        tight = np.flatnonzero(radii(q, 3) < min_radius)
        if not len(tight):
            break
        mask = np.zeros(len(q), dtype=bool)
        for k in range(-4, 5):
            mask[(tight + k) % len(q)] = True
        lap = (np.roll(q, 1, axis=0) + np.roll(q, -1, axis=0)) / 2.0 - q
        q[mask] += 0.5 * lap[mask]
    return q, float(np.max(np.hypot(*(q - p).T)))


def closed_length(p: np.ndarray) -> float:
    return float(np.sum(np.hypot(*np.diff(np.vstack([p, p[:1]]), axis=0).T)))


def arc(p: np.ndarray) -> np.ndarray:
    seg = np.hypot(*np.diff(p, axis=0).T)
    return np.concatenate([[0.0], np.cumsum(seg)])


def project_s(p: np.ndarray, q: np.ndarray) -> float:
    """Arc length along the closed line ``p`` of the point nearest to ``q`` (at a vertex)."""
    s = arc(np.vstack([p, p[:1]]))
    return float(s[int(np.argmin(np.hypot(*(p - q).T)))])


def point_in_polygon(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    x0, y0 = poly[:, 0][None, :], poly[:, 1][None, :]
    x1, y1 = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    cond = (y0 > y) != (y1 > y)
    xc = x0 + (y - y0) * (x1 - x0) / np.where(np.abs(y1 - y0) < 1e-12, 1e-12, y1 - y0)
    return (np.sum(cond & (x < xc), axis=1) % 2) == 1


# ------------------------------------------------------------------------------------------------ import
def build(recipe: dict, cache: Path, step: float = 2.0, cell: float = 6.0, log=print) -> tuple[dict, dict]:
    """The TrackSpec and a report dict (lengths, heights, grades, camber, data checks)."""
    osm = fetch_osm(recipe["bbox"], cache)
    ll, marks = stitch(osm, recipe["lap"])
    bng = np.array([to_bng(a, b) for a, b in ll])
    lat_c, lon_c = np.mean([q[0] for q in ll]), np.mean([q[1] for q in ll])
    k_scale = grid_scale_factor(*wgs84_to_osgb36(lat_c, lon_c))
    clockwise = signed_area(bng) < 0
    if recipe.get("clockwise") is not None and clockwise != bool(recipe["clockwise"]):
        raise ValueError(f"the OSM ways run {'clockwise' if clockwise else 'anticlockwise'}, the recipe says otherwise")
    margin = float(recipe.get("terrainMarginM", 400.0))
    lo, hi = bng.min(0) - margin, bng.max(0) + margin
    log(f"Fetching the DTM over {hi[0] - lo[0]:.0f} x {hi[1] - lo[1]:.0f} m ...")
    dtm = fetch_dtm(lo[0], lo[1], hi[0], hi[1], cache)
    width = float(recipe["widthM"])

    # 1. Smooth the OSM polyline (it has corners at its nodes) and register it to the road in the DTM.
    p = resample_closed(gauss_closed(resample_closed(bng, 1.0), 5.0), step)
    _, nl = normals(p)
    shift = register(dtm, p, nl, width, step)
    p = resample_closed(gauss_closed(p + shift[:, None] * nl, 1.5), step)
    need = (width / 2.0 + 6.0) * 1.03  # rule min-corner-radius (widthM / 2 + minInnerEdgeRadiusM), +3 %
    p, eased = ease_corners(p, need)
    p = resample_closed(p, step)

    # 2. Start/finish: the recipe's point; sample 0 sits on it.
    sf = recipe["startFinish"]
    ways = {e["id"]: e for e in osm["elements"] if e["type"] == "way"}
    if "alongWay" in sf:
        g = np.array([to_bng(q["lat"], q["lon"]) for q in ways[sf["alongWay"]]["geometry"]])
        s_way = arc(g)
        t = float(sf.get("fraction", 0.5)) * s_way[-1]
        sf_pt = np.array([np.interp(t, s_way, g[:, 0]), np.interp(t, s_way, g[:, 1])])
    else:
        sf_pt = np.array(to_bng(sf["lat"], sf["lon"]))
    s0 = project_s(p, sf_pt)
    q = np.vstack([p, p[:1]])
    sq = arc(q)
    n = len(p)
    t = (s0 + np.arange(n) * sq[-1] / n) % sq[-1]
    p = np.column_stack([np.interp(t, sq, q[:, 0]), np.interp(t, sq, q[:, 1])])
    _, nl = normals(p)

    # 3. Heights and camber: a plane across the inner 80 % of the road at every sample.
    offs = np.arange(-0.4 * width, 0.4 * width + 1e-9, 0.5)
    a, b, res = cross_fit(dtm, p, nl, offs)
    if np.isnan(a).any():
        raise RuntimeError("the DTM has gaps under the road")
    z_raw_centre = dtm.sample(p[:, 0], p[:, 1])
    z = gauss_closed(a, 4.0 / step)
    slope = gauss_closed(b, 10.0 / step)
    bank = np.degrees(np.arctan(slope))
    datum = math.floor(float(z.min()))

    # 4. Local frame: x east, y north, metres on the ground, centred on the line's bounding box.
    centre = (p.min(0) + p.max(0)) / 2.0
    loc = (p - centre) / k_scale
    L = closed_length(loc)
    s = np.arange(n) * L / n
    grade = (np.roll(z, -5) - np.roll(z, 5)) / (10.0 * step / k_scale)  # over 20 m, centred
    g10 = (np.roll(z, -5) - z) / (5 * step / k_scale)                     # over 10 m, forward

    # 5. Terrain: block means of the DTM onto a coarser grid (nodes), in the local frame.
    e0, n0 = lo
    nx, ny = int((hi[0] - lo[0]) // cell), int((hi[1] - lo[1]) // cell)
    ex = e0 + np.arange(nx + 1) * cell
    ny_ = n0 + np.arange(ny + 1) * cell
    half = int(cell // 2)
    zz = np.empty((ny + 1, nx + 1))
    for j, yv in enumerate(ny_):
        r0 = int(round(dtm.y1 - yv)) - half
        rows = dtm.z[max(r0, 0):max(r0, 0) + 2 * half + 1]
        for i, xv in enumerate(ex):
            c0 = int(round(xv - dtm.x0)) - half
            blk = rows[:, max(c0, 0):max(c0, 0) + 2 * half + 1]
            zz[j, i] = np.nanmean(blk) if blk.size else np.nan
    zz = np.where(np.isnan(zz), np.nanmean(zz), zz)
    origin = (np.array([e0, n0]) - centre) / k_scale

    # 6. Woods (OSM) within the terrain grid, names and viewing spots along the line.
    box_lo, box_hi = (lo - centre) / k_scale, (hi - centre) / k_scale
    wood_polys = []
    for poly in woods(osm):
        w = (np.array([to_bng(la, lo_) for la, lo_ in poly]) - centre) / k_scale
        if len(w) > 2 and np.allclose(w[0], w[-1]):
            w = w[:-1]
        if len(w) < 3 or (w.max(0) < box_lo).any() or (w.min(0) > box_hi).any():
            continue
        wood_polys.append(w)
    # one label per name, at the middle of its (consecutive) ways
    groups: list[tuple[str, list[int]]] = []
    for wid, _, name in marks:
        label = recipe.get("lapNames", {}).get(str(wid), name) or str(wid)
        if groups and groups[-1][0] == label:
            groups[-1][1].append(wid)
        else:
            groups.append((label, [wid]))
    corners = []
    for label, wids in groups:
        g = np.vstack([np.array([to_bng(q_["lat"], q_["lon"]) for q_ in ways[w]["geometry"]]) for w in wids])
        sg = arc(g)
        mid = np.array([np.interp(sg[-1] / 2, sg, g[:, 0]), np.interp(sg[-1] / 2, sg, g[:, 1])])
        corners.append({"name": label, "sM": round(project_s(p, mid) / k_scale, 1)})
    corners.sort(key=lambda c: c["sM"])
    spots = [c for c in corners if c["name"] in recipe.get("viewingSpots", [])]

    # 7. The spec.
    canvas_margin = 100.0
    span = loc.max(0) - loc.min(0)
    W, H = float(math.ceil(span[0] + 2 * canvas_margin)), float(math.ceil(span[1] + 2 * canvas_margin))
    imported = {
        "source": ATTRIBUTION,
        "datumM": datum,
        "centreline": [{"x": round(float(x), 2), "y": round(float(y), 2), "z": round(float(zv - datum), 2),
                        "bankDeg": round(float(bk), 2)} for (x, y), zv, bk in zip(loc, z, bank)],
        "terrain": {"originX": round(float(origin[0]), 2), "originY": round(float(origin[1]), 2),
                    "cellM": round(cell / k_scale, 4), "nx": nx, "ny": ny,
                    "z": [round(float(v - datum), 2) for v in zz.ravel()]},
        "woodland": [{"points": [{"x": round(float(x), 1), "y": round(float(y), 1)} for x, y in w]} for w in wood_polys],
        "corners": corners,
        "viewingSpots": spots,
    }
    o = recipe["options"]
    spec = {
        "meta": {"name": recipe["name"], "slug": recipe["slug"], "description": recipe["trackDescription"][:500]},
        "sketch": {"canvas": {"width": W, "height": H}, "imported": imported},
        "options": {"lapLengthKm": round(L / 1000.0, 3), "widthM": width, "surface": o["surface"],
                    "kerbs": o["kerbs"], "walls": o["walls"], "elevation": "imported",
                    "environment": o["environment"], "gridSize": o["gridSize"], "weathers": o["weathers"],
                    "reverseLayout": o["reverseLayout"], "defaultLaps": o["defaultLaps"]},
    }

    # 8. Report.
    climb = float(np.clip(np.diff(np.concatenate([z, z[:1]])), 0, None).sum())
    rms = float(np.sqrt(np.mean((z - z_raw_centre) ** 2)))
    report = {
        "lapLengthM": round(L, 1), "publishedLapLengthM": recipe.get("publishedLapLengthM"),
        "osmPolylineLengthM": round(closed_length(bng) / k_scale, 1), "clockwise": clockwise,
        "frame": {"centreE": float(centre[0]), "centreN": float(centre[1]), "scale": k_scale},
        "gridScaleFactor": round(k_scale, 6), "registrationShiftM": [round(float(shift.min()), 2), round(float(shift.max()), 2),
                                                                      round(float(np.abs(shift).mean()), 2)],
        "cornerEasingMaxM": round(eased, 2), "minRadiusM": round(float(radii(loc, 3).min()), 2),
        "crossResidualM": {"mean": round(float(res.mean()), 3), "p95": round(float(np.percentile(res, 95)), 3)},
        "heightMinM": round(float(z.min()), 2), "heightMaxM": round(float(z.max()), 2),
        "heightRangeM": round(float(z.max() - z.min()), 2), "totalClimbM": round(climb, 1),
        "rmsVsDtmCentreM": round(rms, 3),
        "maxUpGradePct20m": round(float(grade.max()) * 100, 1), "maxDownGradePct20m": round(float(grade.min()) * 100, 1),
        "maxUpGradePct10m": round(float(g10.max()) * 100, 1), "maxDownGradePct10m": round(float(g10.min()) * 100, 1),
        "maxUpAtS": round(float(s[int(np.argmax(grade))]), 1), "maxDownAtS": round(float(s[int(np.argmin(grade))]), 1),
        "bankMaxDeg": round(float(np.abs(bank).max()), 2), "bankP95Deg": round(float(np.percentile(np.abs(bank), 95)), 2),
        "bankMeanDeg": round(float(np.abs(bank).mean()), 2), "datumM": datum,
        "terrainGrid": [nx + 1, ny + 1], "woods": len(wood_polys), "corners": corners,
    }
    for c in corners:
        i = int(round(c["sM"] / (L / n))) % n
        c_rep = {"zM": round(float(z[i]), 1), "gradePct": round(float(grade[i]) * 100, 1), "bankDeg": round(float(bank[i]), 1)}
        report.setdefault("atCorners", {})[c["name"]] = c_rep
    m0 = next((c for c in corners if c["name"] == "The Mountain"), None)
    if m0:
        # the climb after the Mountain way's midpoint region: from the dip before it to the crest after it
        # the steepest climb within 150 m of the named way's middle, back to the dip before it (where the
        # road stops falling) and on to the crest after it (where it stops climbing)
        i0 = int(round(m0["sM"] / (L / n)))
        win = [(i0 + d) % n for d in range(-75, 76)]
        zw, gw = z[win], grade[win]
        k = int(np.argmax(gw))
        lo_i = k
        while lo_i > 0 and gw[lo_i - 1] > 0.0:
            lo_i -= 1
        hi_i = k
        while hi_i < len(win) - 1 and gw[hi_i + 1] > 0.0:
            hi_i += 1
        report["mountain"] = {
            "dipS": round(float(s[win[lo_i]]), 1), "crestS": round(float(s[win[hi_i]]), 1),
            "riseM": round(float(zw[hi_i] - zw[lo_i]), 1), "runM": round(float((hi_i - lo_i) * L / n), 1),
            "meanGradePct": round(float((zw[hi_i] - zw[lo_i]) / max((hi_i - lo_i) * L / n, 1e-6) * 100), 1),
            "peakGradePct20m": round(float(grade[win][lo_i:hi_i + 1].max() * 100), 1),
            "gradeChangeOverCrestPct": round(float((grade[win[max(hi_i - 8, 0)]] - grade[win[min(hi_i + 8, len(win) - 1)]]) * 100), 1),
        }
    return spec, report


def verify(spec: dict, report: dict, recipe: dict, cache: Path) -> dict:
    """Build the TrackModel (needs trackgen_geom, e.g. geom's venv) and compare it with the source data:
    lap length against the published one, road heights (centreline and both cambered edges) against the DTM,
    camber, and the model's rule verdict."""
    from trackgen_geom import build_track_model  # noqa: PLC0415 - optional dependency

    result = build_track_model(spec)
    if result.model is None:
        return {"built": False, "errors": [e.message for e in result.errors]}
    fr = report["frame"]
    datum = spec["sketch"]["imported"]["datumM"]
    margin = float(recipe.get("terrainMarginM", 400.0))
    main = result.model.route("main")
    c = np.array([[q.x, q.y, q.z] for q in main.samples])
    left, right = np.array(main.left_edge), np.array(main.right_edge)
    lo = (np.vstack([c[:, :2]]).min(0) * fr["scale"] + [fr["centreE"], fr["centreN"]]) - margin
    hi = (np.vstack([c[:, :2]]).max(0) * fr["scale"] + [fr["centreE"], fr["centreN"]]) + margin
    dtm = fetch_dtm(lo[0], lo[1], hi[0], hi[1], cache)

    def dz(p: np.ndarray, inset: float = 0.0) -> np.ndarray:
        e = p[:, 0] * fr["scale"] + fr["centreE"]
        n = p[:, 1] * fr["scale"] + fr["centreN"]
        return p[:, 2] + datum - dtm.sample(e, n)

    # the edges are compared 10 % inside the road (the DTM's verge kerbs and ditches start at the edge)
    li = c + (left - c) * 0.8
    ri = c + (right - c) * 0.8
    pub = recipe.get("publishedLapLengthM")
    rms = lambda v: round(float(np.sqrt(np.nanmean(v ** 2))), 3)  # noqa: E731
    return {
        "built": True, "valid": result.valid,
        "violations": [f"{v.rule_id}: {v.message}" for v in result.rule_result.violations],
        "warnings": [w.code for w in result.warnings],
        "lapLengthM": round(result.model.lap_length_m, 1),
        "lapVsPublishedPct": round(100 * (result.model.lap_length_m - pub) / pub, 2) if pub else None,
        "rmsCentreVsDtmM": rms(dz(c)), "rmsInnerEdgesVsDtmM": rms(np.concatenate([dz(li), dz(ri)])),
        "maxAbsCentreVsDtmM": round(float(np.nanmax(np.abs(dz(c)))), 3),
        "maxBankDeg": result.facts["maxBankDeg"], "maxGradePct10m": result.facts["maxGradePct"],
        "gridMaxGradePct": result.facts["gridMaxGradePct"], "minCornerRadiusM": result.facts["minCornerRadiusM"],
        "heightRangeM": result.facts["heightRangeM"], "maxAbsCoordinateM": result.facts["maxAbsCoordinateM"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("recipe", help="recipe JSON (tools/real_tracks/*.json)")
    ap.add_argument("--out", required=True, help="spec JSON to write")
    ap.add_argument("--report", help="also write the report JSON here")
    ap.add_argument("--cache", help="download cache (default E:\\Source\\wreckfest\\.local\\<slug>)")
    ap.add_argument("--verify", action="store_true", help="build the TrackModel and compare it with the DTM "
                    "(needs trackgen_geom: run with geom's venv)")
    args = ap.parse_args(argv)
    recipe = json.loads(Path(args.recipe).read_text(encoding="utf-8"))
    cache = Path(args.cache) if args.cache else DEFAULT_CACHE_ROOT / recipe["slug"]
    cache.mkdir(parents=True, exist_ok=True)
    spec, report = build(recipe, cache, log=lambda m: print(m, file=sys.stderr))
    if args.verify:
        report["verify"] = verify(spec, report, recipe, cache)
    Path(args.out).write_text(json.dumps(spec, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    text = json.dumps(report, indent=1)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
