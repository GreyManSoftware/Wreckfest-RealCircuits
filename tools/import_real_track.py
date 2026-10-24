"""Import a real circuit into a TrackSpec with an exact centreline, heights, camber and terrain.

    py tools/import_real_track.py tools/real_tracks/cadwellpark.json --out geom/tests/samples/cadwellpark.json

Data (downloaded once and cached under E:\\Source\\wreckfest\\.local\\<slug>\\, never committed):

- **Layout:** OpenStreetMap ``highway=raceway`` ways listed in the recipe's ``lap`` (in driving order), plus
  ``landuse=forest`` / ``natural=wood`` polygons, from the Overpass API. (c) OpenStreetMap contributors, ODbL.
- **Heights:** the Environment Agency LIDAR Composite DTM 1 m (bare earth, Open Government Licence v3) through
  its WCS, in British National Grid (EPSG:27700); or, with ``"dtm": "wales"``, the Welsh Government / NRW
  LIDAR composite DTM 1 m (OGL v3) read from its national Cloud Optimised GeoTIFF by range requests
  (``tools/real_tracks_data/cog.py``); or, with ``"dtm": "es-mdt05"`` (Spain), the IGN MDT05 5 m (PNOA LIDAR, CC BY
  4.0) from the IDEE WCS in ETRS89 / UTM 30N (EPSG:25830), the OSM line projected to UTM 30N instead (served as whole
  metres: check the report's ``dtmWholeMetreFraction``); or, with ``"dtm": "es-navarra"``, the Gobierno de Navarra's
  2 m DTM from the 2024 LIDAR (IDENA WCS, CC BY 4.0), also in UTM 30N.

Multi-layout recipes (``altLayouts``: each with its own ``lap`` of ways; ``[way, from_node, to_node]`` takes part
of a way) fit every lap the same way and keep, per alternative layout, the branch that leaves the main lap, with
its own heights and camber (``sketch.imported.altLayouts``). ``seaLevelODN`` floods no-data (sea) cells of the
terrain just below it and sets ``seaLevelM``.

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
# rule min-corner-radius for real tracks: widthM / 2 + this (real hairpins such as Cadwell's Club Hairpin are kept)
_RULE_CONSTANTS = json.loads(
    (Path(__file__).resolve().parents[1] / "schema" / "track-rules.json").read_text(encoding="utf-8"))["constants"]
IMPORTED_MIN_INNER_EDGE_RADIUS_M = float(_RULE_CONSTANTS["importedMinInnerEdgeRadiusM"])
# rule imported-max-bank: the fitted camber is clamped to it (Oulton Park's Shell Oils hairpin fits 10.9 degrees;
# the report keeps the unclamped maximum, bankFittedMaxDeg)
IMPORTED_MAX_BANK_DEG = float(_RULE_CONSTANTS["importedMaxBankDeg"])

# Centreline heights in mm: at 2 m spacing, cm rounding alone moves a step's grade by up to 0.5 % (Brands Hatch's
# grid, a steady ~3.7 % climb, read 4.01 % against the 4 % imported-grid-level rule).
Z_DECIMALS = 3

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


def tm_project(lat: float, lon: float, ell: tuple[float, float] = AIRY1830, ng: dict = NG) -> tuple[float, float]:
    """Latitude/longitude (radians) -> transverse Mercator easting/northing (OS formulae): by default OSGB36 ->
    British National Grid; with ``ell=GRS80, ng=utm_params(zone)`` ETRS89 -> UTM."""
    a, b = ell
    F0, lat0, lon0, E0, N0 = ng["F0"], ng["lat0"], ng["lon0"], ng["E0"], ng["N0"]
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


def grid_scale_factor(lat: float, lon: float, ell: tuple[float, float] = AIRY1830, ng: dict = NG) -> float:
    """Point scale factor of the National Grid (or another transverse Mercator grid) at (lat, lon) radians."""
    a, b = ell
    e2 = 1 - (b * b) / (a * a)
    sl = math.sin(lat)
    nu = a * ng["F0"] / math.sqrt(1 - e2 * sl * sl)
    rho = a * ng["F0"] * (1 - e2) / (1 - e2 * sl * sl) ** 1.5
    d = lon - ng["lon0"]
    cl = math.cos(lat)
    return ng["F0"] * (1 + (d * cl) ** 2 / 2 * (nu / rho) + (d * cl) ** 4 / 24 * (5 - 4 * math.tan(lat) ** 2))


def to_bng(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    return tm_project(*wgs84_to_osgb36(lat_deg, lon_deg))


GRS80 = (6378137.0, 6356752.314140)


def utm_params(zone: int) -> dict:
    return dict(F0=0.9996, lat0=0.0, lon0=math.radians(zone * 6 - 183), E0=500000.0, N0=0.0)


# The projected grid of the recipe's DTM: "bng" (British National Grid via OSGB36: the EA and Welsh DTMs) or "utm30"
# (ETRS89 / UTM zone 30N, EPSG:25830: Spain's IGN MDT; ETRS89 is taken as WGS84, under 1 m apart there). Set per
# recipe by set_projection (build, verify).
_GRID = {"name": "bng"}


def set_projection(recipe: dict) -> None:
    _GRID["name"] = "utm30" if recipe.get("dtm") in ("es-mdt05", "es-navarra") else "bng"


def to_grid(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    """WGS84 degrees -> the recipe's grid (metres)."""
    if _GRID["name"] == "utm30":
        return tm_project(math.radians(lat_deg), math.radians(lon_deg), GRS80, utm_params(30))
    return to_bng(lat_deg, lon_deg)


def grid_scale_at(lat_deg: float, lon_deg: float) -> float:
    """The recipe grid's point scale factor at WGS84 degrees (grid metres per ground metre)."""
    if _GRID["name"] == "utm30":
        return grid_scale_factor(math.radians(lat_deg), math.radians(lon_deg), GRS80, utm_params(30))
    return grid_scale_factor(*wgs84_to_osgb36(lat_deg, lon_deg))


# ------------------------------------------------------------------------------------------------ fetching
def _get(url: str, data: bytes | None = None, timeout: float = 120.0) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_osm(bbox: list[float], cache: Path) -> dict:
    s, w, n, e = bbox
    b = f"({s},{w},{n},{e})"
    q = (f'[out:json][timeout:90];(way["highway"="raceway"]{b};way["landuse"="forest"]{b};'
         f'way["natural"="wood"]{b};relation["natural"="wood"]{b};relation["landuse"="forest"]{b};);'
         "out body geom;")
    return _overpass_cached(q, cache / "osm.json", cache)


def fetch_osm_water(bbox: list[float], cache: Path) -> dict:
    """Open water (natural=water ways and multipolygons) over ``bbox``, one cached Overpass request."""
    s, w, n, e = bbox
    b = f"({s},{w},{n},{e})"
    q = f'[out:json][timeout:90];(way["natural"="water"]{b};relation["natural"="water"]{b};);out body geom;'
    return _overpass_cached(q, cache / "osm-water.json", cache)


def _overpass_cached(q: str, path: Path, cache: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
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


def with_extra_ways(osm: dict, ids: list[int], cache: Path) -> dict:
    """``osm`` plus the recipe's ``extraWays`` (by id, with geometry; cached in ``osm_extra.json``): ways that
    aren't ``highway=raceway`` but carry a layout, e.g. Bedford's service road between two of its loops."""
    if not ids:
        return osm
    path = cache / "osm_extra.json"
    extra = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"elements": []}
    have = {e["id"] for e in extra["elements"] if e["type"] == "way"}
    if not set(ids) <= have:
        q = f'[out:json][timeout:60];way(id:{",".join(str(i) for i in ids)});out body geom;'
        last = None
        for url in OVERPASS:
            try:
                raw = _get(url, urllib.parse.urlencode({"data": q}).encode())
                extra = json.loads(raw)
                path.write_bytes(raw)
                break
            except Exception as exc:  # try the next mirror
                last = exc
        else:
            raise RuntimeError(f"Overpass failed: {last}")
    known = {e["id"] for e in osm["elements"] if e["type"] == "way"}
    return {**osm, "elements": osm["elements"] + [e for e in extra["elements"] if e["type"] == "way" and e["id"] not in known]}


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


WALES_DTM_COG = "https://dmwproductionblob.blob.core.windows.net/cogs/lidar/wales_dtm_32bit_cog.tif"


def fetch_dtm_wales(e0: float, n0: float, e1: float, n1: float, cache: Path) -> Dtm:
    """Welsh Government / NRW LIDAR composite DTM 1 m (OGL v3) over [e0, e1] x [n0, n1], read from the national
    Cloud Optimised GeoTIFF with HTTP range requests (tools/real_tracks_data/cog.py; ranges cached). Sea and
    other no-data cells are NaN."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "real_tracks_data"))
    from cog import RemoteCog  # noqa: PLC0415 - optional, stdlib + numpy

    path = cache / "dtm" / f"wales_{e0:.0f}_{n0:.0f}_{e1:.0f}_{n1:.0f}.npz"
    if path.exists():
        d = np.load(path)
        return Dtm(float(d["x0"]), float(d["y1"]), 1.0, d["z"].astype(np.float64))
    cog = RemoteCog(WALES_DTM_COG, str(cache / "cog_cache"))
    z, xc, yc, res = cog.read_bbox(e0, n0, e1, n1)
    if abs(res - 1.0) > 1e-9:
        raise RuntimeError(f"unexpected DTM resolution {res}")
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, z=z.astype(np.float32), x0=xc - 0.5, y1=yc + 0.5)
    return Dtm(xc - 0.5, yc + 0.5, 1.0, z)


def horizon_profile(recipe: dict, eye: np.ndarray, eye_z: float, cache: Path, sea_level: float = 0.0) -> dict:
    """The distant skyline from ``eye`` (BNG) at height ``eye_z`` (m ODN): per azimuth step (clockwise from grid
    north) the highest elevation angle of the terrain between ``minDistanceM`` and ``maxDistanceM``, with earth
    curvature and refraction (k = 0.13), from the Wales DTM's 32 m overview (no-data = sea at ``sea_level``),
    and whether that skyline is the open sea."""
    h = recipe["horizon"]
    sys.path.insert(0, str(Path(__file__).resolve().parent / "real_tracks_data"))
    from cog import RemoteCog  # noqa: PLC0415

    r_max, r_min = float(h.get("maxDistanceM", 45000.0)), float(h.get("minDistanceM", 3000.0))
    step = float(h.get("stepDeg", 1.0))
    cog = RemoteCog(WALES_DTM_COG, str(cache / "cog_cache"))
    cog.use_level(int(h.get("overviewLevel", 5)))
    z, xc, yc, res = cog.read_bbox(eye[0] - r_max, eye[1] - r_max, eye[0] + r_max, eye[1] + r_max)
    z = np.where(np.isnan(z), sea_level, z)
    grid = Dtm(xc - res / 2.0, yc + res / 2.0, res, z)
    r = np.arange(r_min, r_max, res)
    drop = r * r / (2 * 6371000.0) * 0.87
    elev, sea = [], []
    for az in np.arange(0.0, 360.0, step):
        a = math.radians(az)
        zz = grid.sample(eye[0] + r * math.sin(a), eye[1] + r * math.cos(a))
        zz = np.where(np.isnan(zz), sea_level, zz)
        ang = np.degrees(np.arctan((zz - drop - eye_z) / r))
        i = int(np.argmax(ang))
        elev.append(round(float(ang[i]), 3))
        sea.append(bool(zz[i] < sea_level + 2.0))
    return {"stepDeg": step, "elevationDeg": elev, "sea": sea}


ES_WCS = "https://servicios.idee.es/wcs-inspire/mdt"
ES_COVERAGE = "Elevacion25830_5"  # IGN MDT05 (PNOA LIDAR), 5 m, ETRS89 / UTM 30N


def parse_arcgrid(text: str) -> tuple[float, float, float, np.ndarray]:
    """An ESRI ASCII grid (possibly inside a WCS multipart response): (x of the west edge, y of the north edge,
    cell size, z[row, col] with row 0 at the north edge; no-data as NaN)."""
    lines = text[text.index("ncols"):].splitlines()
    head = {}
    k = 0
    while k < len(lines) and lines[k].split() and lines[k].split()[0][:1].isalpha():
        key, val = lines[k].split()[:2]
        head[key.lower()] = float(val)
        k += 1
    nc, nr, cs = int(head["ncols"]), int(head["nrows"]), head["cellsize"]
    vals = []
    for ln in lines[k:]:
        if ln.startswith("--"):
            break
        vals.extend(ln.split())
    z = np.array(vals[:nc * nr], dtype=np.float64).reshape(nr, nc)
    if "nodata_value" in head:
        z[z == head["nodata_value"]] = np.nan
    z[(z < -100) | (z > 5000)] = np.nan
    x0 = head["xllcorner"] if "xllcorner" in head else head["xllcenter"] - cs / 2
    y0 = head["yllcorner"] if "yllcorner" in head else head["yllcenter"] - cs / 2
    return x0, y0 + nr * cs, cs, z


def fetch_dtm_es(e0: float, n0: float, e1: float, n1: float, cache: Path, tile: int = 1000) -> Dtm:
    """Spain's IGN MDT05 (5 m DTM from the PNOA LIDAR, CC BY 4.0) over [e0, e1] x [n0, n1] (ETRS89 / UTM 30N
    metres), from the IDEE INSPIRE WCS as ASCII grids in 1 km tiles, cached."""
    cs = 5.0
    e0, n0 = math.floor(e0 / tile) * tile, math.floor(n0 / tile) * tile
    e1, n1 = math.ceil(e1 / tile) * tile, math.ceil(n1 / tile) * tile
    nx, ny = int((e1 - e0) / tile), int((n1 - n0) / tile)
    m = int(tile / cs)
    full = np.full((ny * m, nx * m), np.nan)
    tdir = cache / "dtm"
    tdir.mkdir(parents=True, exist_ok=True)
    for ty in range(ny):
        for tx in range(nx):
            te, tn = e0 + tx * tile, n0 + ty * tile
            path = tdir / f"mdt05_{te:.0f}_{tn:.0f}_{tile}.npy"
            if not path.exists():
                params = [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"),
                          ("CoverageId", ES_COVERAGE), ("format", "application/asc"),
                          ("subset", f"x({te},{te + tile})"), ("subset", f"y({tn},{tn + tile})")]
                url = ES_WCS + "?" + urllib.parse.urlencode(params)
                for attempt in range(3):
                    try:
                        raw = _get(url, timeout=300)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(5)
                x0, y1, c, z = parse_arcgrid(raw.decode("ascii", "replace"))
                if abs(c - cs) > 1e-6 or abs(x0 - te) > 1e-6 or abs(y1 - (tn + tile)) > 1e-6 or z.shape != (m, m):
                    raise RuntimeError(f"MDT05 tile {te},{tn}: got origin {x0},{y1} cell {c} shape {z.shape}")
                np.save(path, z.astype(np.float32))
            z = np.load(path).astype(np.float64)
            r0 = int(round((n1 - (tn + tile)) / cs))
            c0 = int(round((te - e0) / cs))
            full[r0:r0 + m, c0:c0 + m] = z
    return Dtm(e0, n1, cs, full)


# Navarre's own LIDAR DTM (Gobierno de Navarra, IDENA): 2 m, from the 2024 flight (5 points/m2), centimetre heights,
# CC BY 4.0. Preferred to the IGN MDT05 there (2026-10-24 audit): the IDEE WCS serves the MDT05 as whole metres
# (16-bit integers, resampled), so its gentle slopes are 1 m terraces 40-50 m apart (Navarra's wavy straights), and
# its older flights predate the 2024 extension (Turns 11-14).
NAVARRA_WCS = "https://idena.navarra.es/ogc/wcs"
NAVARRA_COVERAGE = "IDENA.WCS__ELEVAC_Ras_MDT_2M"


def parse_geoserver_grid(text: str) -> tuple[float, float, float, np.ndarray]:
    """A GeoServer WCS ``text/plain`` coverage whose affine is the whole coverage's (IDENA): (x of the west edge, y
    of the north edge, cell size, z[row, col] with row 0 at the north edge), from its "Grid bounds" line."""
    head, _, body = text.partition("Band 0:")
    b = re.search(r"GeneralBounds\[\(([-0-9.eE]+), ([-0-9.eE]+)\), \(([-0-9.eE]+), ([-0-9.eE]+)\)\]", head)
    m = {k: float(v) for k, v in re.findall(r'PARAMETER\["(elt_\d_\d)", ([-0-9.eE]+)\]', head)}
    if b is None or m.get("elt_0_0") is None or m.get("elt_1_1") != -m["elt_0_0"]:
        raise RuntimeError("unexpected DTM grid header")
    z = np.array([r.split() for r in body.strip().splitlines() if r.strip()], dtype=np.float64)
    z[(z < -100) | (z > 5000)] = np.nan  # no-data (3.4e38)
    return float(b.group(1)), float(b.group(4)), m["elt_0_0"], z


def fetch_dtm_navarra(e0: float, n0: float, e1: float, n1: float, cache: Path, tile: int = 1000) -> Dtm:
    """Gobierno de Navarra's 2 m LIDAR DTM (2024) over [e0, e1] x [n0, n1] (ETRS89 / UTM 30N metres), from the IDENA
    WCS in 1 km tiles, cached. Its grid is offset from whole metres (cell edges at x .375, y .625)."""
    e0, n0 = math.floor(e0 / tile) * tile, math.floor(n0 / tile) * tile
    e1, n1 = math.ceil(e1 / tile) * tile, math.ceil(n1 / tile) * tile
    nx, ny = int((e1 - e0) / tile), int((n1 - n0) / tile)
    cs, m = 2.0, tile // 2
    full = np.full((ny * m, nx * m), np.nan)
    tdir = cache / "dtm"
    tdir.mkdir(parents=True, exist_ok=True)
    org = None
    for ty in range(ny):
        for tx in range(nx):
            te, tn = e0 + tx * tile, n0 + ty * tile
            path = tdir / f"idena2m_{te:.0f}_{tn:.0f}_{tile}.npz"
            if not path.exists():
                params = [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"),
                          ("CoverageId", NAVARRA_COVERAGE), ("format", "text/plain"),
                          ("subset", f"E({te},{te + tile})"), ("subset", f"N({tn},{tn + tile})")]
                url = NAVARRA_WCS + "?" + urllib.parse.urlencode(params)
                for attempt in range(3):
                    try:
                        raw = _get(url, timeout=300)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(5)
                x0, y1, c, z = parse_geoserver_grid(raw.decode("ascii", "replace"))
                if abs(c - cs) > 1e-9 or z.shape != (m, m) or abs(x0 - te) > cs or abs(y1 - tn - tile) > cs:
                    raise RuntimeError(f"IDENA tile {te},{tn}: got origin {x0},{y1} cell {c} shape {z.shape}")
                np.savez_compressed(path, z=z.astype(np.float32), dx=x0 - te, dy=y1 - tn - tile)
            d = np.load(path)
            off = (float(d["dx"]), float(d["dy"]))
            if org is None:
                org = off
            elif max(abs(off[0] - org[0]), abs(off[1] - org[1])) > 1e-6:
                raise RuntimeError(f"IDENA tile {te},{tn}: grid offset {off} != {org}")
            r0 = int(round((n1 - (tn + tile)) / cs))
            c0 = int(round((te - e0) / cs))
            full[r0:r0 + m, c0:c0 + m] = d["z"].astype(np.float64)
    return Dtm(e0 + org[0], n1 + org[1], cs, full)


def dtm_quantised_fraction(dtm: Dtm) -> float:
    """The fraction of the DTM's cells at a whole metre (to 1 mm). Near 0.01 for a LIDAR DTM in cm; the IDEE WCS's
    MDT05, served as whole metres and resampled, has 20-45 % (terraces 1 m high)."""
    v = dtm.z[np.isfinite(dtm.z)]
    return float(np.mean(np.abs(v - np.round(v)) < 1e-3)) if v.size else 0.0


def load_dtm(recipe: dict, e0: float, n0: float, e1: float, n1: float, cache: Path) -> Dtm:
    """The recipe's DTM (``"dtm": "ea"`` England, the default, ``"wales"``, ``"es-mdt05"`` Spain, IGN MDT05 in
    UTM 30N, or ``"es-navarra"``, Navarre's 2024 2 m LIDAR DTM in UTM 30N)."""
    if recipe.get("dtm") == "es-navarra":
        return fetch_dtm_navarra(e0, n0, e1, n1, cache)
    if recipe.get("dtm") == "es-mdt05":
        return fetch_dtm_es(e0, n0, e1, n1, cache)
    if recipe.get("dtm", "ea") == "wales":
        return fetch_dtm_wales(math.floor(e0), math.floor(n0), math.ceil(e1), math.ceil(n1), cache)
    return fetch_dtm(e0, n0, e1, n1, cache)


ATTRIBUTION_ES = ("Layout and woodland (c) OpenStreetMap contributors, ODbL 1.0. Heights: MDT05 (c) Instituto "
                  "Geografico Nacional de Espana (IGN), derived from PNOA LIDAR, CC BY 4.0 (scne.es).")
ATTRIBUTION_NAVARRA = ("Layout and woodland (c) OpenStreetMap contributors, ODbL 1.0. Heights: MDT 2 m 2024 from the "
                       "LiDAR de Navarra 2024, Servicio proporcionado por el Gobierno de Navarra (IDENA), CC BY 4.0.")
ATTRIBUTION_WALES = ("Layout (c) OpenStreetMap contributors, ODbL 1.0. Heights: Welsh Government / Natural Resources "
                     "Wales LIDAR composite DTM 1 m, contains Natural Resources Wales information (c) Natural "
                     "Resources Wales and database right, Open Government Licence v3.0.")


# ------------------------------------------------------------------------------------------------ layout
def way_part(w: dict, item) -> tuple[list[int], list[dict]]:
    """The nodes and geometry of one lap item: a way id (the whole way), ``[way, from_node, to_node]`` (that part,
    backwards when ``from_node`` comes after ``to_node``) or ``[way, from_node, to_node, "fwd"|"rev"]`` on a closed
    way (a loop such as Bedford's circuits): along or against the way's direction from ``from_node`` to
    ``to_node``, through the closing node when needed."""
    nodes, geom = w["nodes"], w["geometry"]
    if not isinstance(item, list):
        return nodes, geom
    if len(item) > 3:
        if nodes[0] != nodes[-1]:
            raise ValueError(f"way {w['id']}: a direction ({item[3]!r}) is only for closed ways")
        n = len(nodes) - 1  # the loop without its repeated closing node
        i, j = nodes.index(item[1]) % n, nodes.index(item[2]) % n
        d = {"fwd": 1, "rev": -1}[item[3]]
        k = (j - i) % n if d == 1 else (i - j) % n
        idx = [(i + d * t) % n for t in range(k + 1 if k else n + 1)]
        return [nodes[t] for t in idx], [geom[t] for t in idx]
    i, j = nodes.index(item[1]), nodes.index(item[2])
    if i <= j:
        return nodes[i:j + 1], geom[i:j + 1]
    return nodes[j:i + 1][::-1], geom[j:i + 1][::-1]


def stitch_nodes(osm: dict, lap: list, joins=()) -> tuple[list[tuple[float, float]], list[tuple[int, int, str]], list[int]]:
    """``stitch`` plus the OSM node id of every point. ``joins``: pairs of OSM nodes a few metres apart that are one
    junction on the ground but not joined in OSM (a recipe's ``nodeJoins``, e.g. Navarra's Short link starting 2 m
    off the lap); a way may start at its partner of the previous way's end, and its first point is moved there."""
    pair = {frozenset(map(int, j)) for j in joins}
    ways = {e["id"]: e for e in osm["elements"] if e["type"] == "way"}
    pts: list[tuple[float, float]] = []
    ids: list[int] = []
    marks: list[tuple[int, int, str]] = []
    prev_end = first = None
    for item in lap:
        wid = item[0] if isinstance(item, list) else item
        w = ways[wid]
        nodes, geom = way_part(w, item)
        if prev_end is not None and nodes[0] != prev_end and frozenset((nodes[0], prev_end)) in pair:
            nodes, geom = [prev_end] + list(nodes[1:]), [prev_geom] + list(geom[1:])
        if prev_end is not None and nodes[0] != prev_end:
            raise ValueError(f"way {wid} doesn't start where the previous way ends ({nodes[0]} != {prev_end})")
        first = nodes[0] if first is None else first
        # index of the way's first point (after the first way, the previous way's last point, which it shares;
        # it was the way's second point until 2026-10-20, which put a 2-node way's start at its far end)
        marks.append((wid, max(len(pts) - 1, 0), w.get("tags", {}).get("name", "")))
        seq = [(g["lat"], g["lon"]) for g in geom]
        pts.extend(seq if not pts else seq[1:])
        ids.extend(nodes if not ids else nodes[1:])
        prev_end, prev_geom = nodes[-1], geom[-1]
    if prev_end != first:
        raise ValueError("the lap doesn't close")
    return pts[:-1], marks, ids[:-1]


def stitch(osm: dict, lap: list) -> tuple[list[tuple[float, float]], list[tuple[int, int, str]]]:
    """The lap's way geometries joined end to end (each way must start where the previous one ends). An item
    ``[way, from_node, to_node]`` takes only that part of the way, driven backwards when ``from_node`` comes after
    ``to_node`` (a link mapped against the driving direction, e.g. Cadwell's Club Hairpin); on a closed way
    ``[way, from_node, to_node, "fwd"|"rev"]`` gives the direction (``way_part``). Returns the lat/lon points
    (closed loop, first point not repeated) and (way id, first point index, name)."""
    pts, marks, _ = stitch_nodes(osm, lap)
    return pts, marks


def water_polygons(osm: dict) -> list[tuple[str, list[tuple[float, float]]]]:
    """(name, outer ring lat/lon) of every natural=water way or multipolygon outer ring."""
    out = []
    for e in osm["elements"]:
        t = e.get("tags", {})
        if t.get("natural") != "water":
            continue
        if e["type"] == "way" and e.get("geometry"):
            out.append((t.get("name", ""), [(g["lat"], g["lon"]) for g in e["geometry"]]))
        elif e["type"] == "relation":
            for m in e.get("members", []):
                if m.get("role") == "outer" and m.get("geometry"):
                    out.append((t.get("name", ""), [(g["lat"], g["lon"]) for g in m["geometry"]]))
    return out


def lakes_from(water: list[tuple[str, np.ndarray]], dtm: "Dtm", road: np.ndarray, min_area: float,
               clear_m: float) -> list[dict]:
    """Lakes (BNG polygons) for the spec: at least ``min_area`` m2 and no road sample within ``clear_m`` of the
    polygon (inside it or close to its shore). The level is the median DTM inside (LIDAR returns the flat water
    surface there), else the 10th percentile along the shore where the DTM has no data on the water."""
    out = []
    for name, poly in water:
        if len(poly) > 2 and np.allclose(poly[0], poly[-1]):
            poly = poly[:-1]
        if len(poly) < 3 or abs(signed_area(poly)) < min_area:
            continue
        near = road[(road[:, 0] > poly[:, 0].min() - clear_m) & (road[:, 0] < poly[:, 0].max() + clear_m)
                    & (road[:, 1] > poly[:, 1].min() - clear_m) & (road[:, 1] < poly[:, 1].max() + clear_m)]
        if len(near) and (point_in_polygon(near, poly).any() or _min_dist_to_ring(near, poly) < clear_m):
            continue
        xs = np.arange(poly[:, 0].min(), poly[:, 0].max(), 2.0)
        ys = np.arange(poly[:, 1].min(), poly[:, 1].max(), 2.0)
        gx, gy = np.meshgrid(xs, ys)
        pts = np.column_stack([gx.ravel(), gy.ravel()])
        pts = pts[point_in_polygon(pts, poly)] if len(pts) else pts
        z = dtm.sample(pts[:, 0], pts[:, 1]) if len(pts) else np.array([np.nan])
        if np.isfinite(z).mean() >= 0.5:
            level = float(np.nanmedian(z))
        else:
            shore = dtm.sample(poly[:, 0], poly[:, 1])
            if not np.isfinite(shore).any():
                continue
            level = float(np.nanpercentile(shore, 10))
        out.append({"name": name, "poly": poly, "level": level, "areaM2": abs(signed_area(poly))})
    return out


def _min_dist_to_ring(pts: np.ndarray, ring: np.ndarray) -> float:
    a, b = ring, np.roll(ring, -1, axis=0)
    ab = b - a
    t = np.clip(np.einsum("pk,sk->ps", pts, ab) - np.einsum("sk,sk->s", a, ab)[None, :], 0.0, None)
    t = np.minimum(t / np.maximum(np.einsum("sk,sk->s", ab, ab), 1e-12)[None, :], 1.0)
    q = a[None, :, :] + t[:, :, None] * ab[None, :, :]
    return float(np.min(np.hypot(*(q - pts[:, None, :]).transpose(2, 0, 1))))


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
def gauss_open(v: np.ndarray, sigma_samples: float) -> np.ndarray:
    """Gaussian smoothing of an open sequence (ends padded with their own values)."""
    if sigma_samples <= 0:
        return v.copy()
    r = int(math.ceil(4 * sigma_samples))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma_samples) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(v, r, mode="edge"), k, mode="valid")


def resample_open(p: np.ndarray, step: float) -> np.ndarray:
    s = arc(p)
    n = max(3, int(round(s[-1] / step)) + 1)
    t = np.linspace(0.0, s[-1], n)
    return np.column_stack([np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])])


def normals_open(p: np.ndarray) -> np.ndarray:
    d = np.gradient(p, axis=0)
    t = d / np.maximum(np.hypot(*d.T), 1e-9)[:, None]
    return np.column_stack([-t[:, 1], t[:, 0]])


# Chicanes (a recipe's ``sharpWays``): the 5 m smoothing turns a bus-stop chicane's 10-15 m corners into a 30 m
# radius S that is taken flat out (Oulton Park's Hislop's and Brittens, Chris 2026-10-23: "missing"), and the
# registration can pull its line towards a disused straight beside it. Over these ways the line is only smoothed
# enough to round the OSM node corners, and the registration shift is held from the ends.
SHARP_SIGMA_M = 1.0
SHARP_FEATHER_M = 8.0


def sharp_segments(marks: list[tuple[int, int, str]], n_pts: int, sharp_ways) -> np.ndarray:
    """Per segment of a stitched closed lap (point i to i + 1): True on a ``sharpWays`` way."""
    sharp_ways = {int(w) for w in (sharp_ways or [])}
    seg = np.zeros(n_pts, dtype=bool)
    for k, (wid, i0, _) in enumerate(marks):
        i1 = marks[k + 1][1] if k + 1 < len(marks) else n_pts
        if wid in sharp_ways:
            seg[i0:i1] = True
    return seg


def _resample_weight(src: np.ndarray, w_seg: np.ndarray, step: float) -> tuple[np.ndarray, np.ndarray]:
    """resample_closed(src, step) and the per-segment weight carried to the new points."""
    q = np.vstack([src, src[:1]])
    cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(q, axis=0).T))])
    n = max(8, int(round(cum[-1] / step)))
    t = np.arange(n) * (cum[-1] / n)
    k = np.clip(np.searchsorted(cum, t, side="right") - 1, 0, len(w_seg) - 1)
    return np.column_stack([np.interp(t, cum, q[:, 0]), np.interp(t, cum, q[:, 1])]), w_seg[k].astype(np.float64)


def _hold_through(v: np.ndarray, zone: np.ndarray) -> np.ndarray:
    """``v`` with every run of ``zone`` (closed) replaced by a straight line between the values either side."""
    out, n = v.copy(), len(v)
    if not zone.any() or zone.all():
        return out
    start = int(np.flatnonzero(~zone)[0])
    i = 0
    while i < n:
        a = (start + i) % n
        if zone[a]:
            j = i
            while j < n and zone[(start + j) % n]:
                j += 1
            lo, hi = (start + i - 1) % n, (start + j) % n
            m = j - i + 1
            for k in range(i, j):
                f = (k - i + 1) / m
                out[(start + k) % n] = (1 - f) * v[lo] + f * v[hi]
            i = j
        else:
            i += 1
    return out


MIN_VERTICAL_RADIUS_M = 100.0  # crests and dips tighter than this (over 10 m) are eased (see ease_vertical)


def ease_vertical(z: np.ndarray, step: float, closed: bool = True, r_min: float = MIN_VERTICAL_RADIUS_M) -> np.ndarray:
    """Heights with crests and dips tighter than ``r_min`` (the 10 m grade changing by more than 10 m / r_min over
    10 m) eased by a local 6 m smoothing, feathered over 10 m. Real roads have vertical curves of a few hundred
    metres; tighter ones come from DTM steps (Navarra Short's hook ramp: a 93 m dip, which the game's road strips,
    a few metres per sector, can't follow within 5 cm)."""
    z = np.asarray(z, dtype=np.float64).copy()
    h = int(round(5.0 / step))
    sm = (lambda v, sig: gauss_closed(v, sig / step)) if closed else (lambda v, sig: gauss_open(v, sig / step))
    for _ in range(20):
        zp = np.concatenate([z[-2 * h:], z, z[:2 * h]]) if closed else np.pad(z, 2 * h, mode="edge")
        kv = (zp[4 * h:] - 2 * zp[2 * h:-2 * h] + zp[:-4 * h]) / (2 * h * step) ** 2
        bad = np.abs(kv) > 1.0 / r_min
        if not closed:
            bad[:2 * h] = False
            bad[-2 * h:] = False
        if not bad.any():
            break
        w = np.clip(sm(bad.astype(np.float64), 5.0) * 3.0, 0.0, 1.0)
        z = w * sm(z, 6.0) + (1.0 - w) * z
    return z


JOIN_BLEND_MIN_M = 30.0       # a branch end's height/camber mismatch with the main road fades out over at least this
JOIN_BLEND_MAX_GRADE = 0.015  # ... and long enough that it adds at most this to the grade


def join_blend(s: np.ndarray, z: np.ndarray, bank: np.ndarray, z_main: tuple[float, float],
               bank_main: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """An open branch's heights and camber (degrees) with its ends set to the main road's at the joins, the
    mismatch faded out linearly over JOIN_BLEND_MIN_M, or longer for a large height mismatch (at most
    JOIN_BLEND_MAX_GRADE added), never past the branch's middle."""
    z, bank = z.astype(np.float64).copy(), bank.astype(np.float64).copy()
    half = 0.5 * float(s[-1])
    for end, dist in ((0, s), (-1, s[-1] - s)):
        dz, db = float(z_main[end] - z[end]), float(bank_main[end] - bank[end])
        L = min(max(JOIN_BLEND_MIN_M, abs(dz) / JOIN_BLEND_MAX_GRADE), half)
        w = np.clip(1.0 - dist / L, 0.0, 1.0)
        z += dz * w
        bank += db * np.clip(1.0 - dist / min(JOIN_BLEND_MIN_M, half), 0.0, 1.0)
    return z, bank


def fit_loop(bng: np.ndarray, dtm: Dtm, width: float, step: float,
             max_shift: float = 6.0, sharp: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, float]:
    """Smooth the OSM polyline (it has corners at its nodes), register it to the road in the DTM and ease
    corners tighter than rule min-corner-radius. ``sharp`` (per segment of ``bng``, ``sharp_segments``): chicanes
    kept sharp (SHARP_SIGMA_M smoothing, feathered over SHARP_FEATHER_M; the registration shift held through
    them). Returns the points, the registration shifts and the largest easing move."""
    if sharp is None or not np.any(sharp):
        p = resample_closed(gauss_closed(resample_closed(bng, 1.0), 5.0), step)
        w = np.zeros(len(p))
    else:
        r1, w1 = _resample_weight(bng, np.asarray(sharp, dtype=bool), 1.0)
        # feather: the chicane's own corners fully sharp, blending into the smoothed line over SHARP_FEATHER_M
        w1 = np.clip(gauss_closed(w1, SHARP_FEATHER_M / 2.0) * 2.0, 0.0, 1.0)
        r1 = w1[:, None] * gauss_closed(r1, SHARP_SIGMA_M) + (1.0 - w1[:, None]) * gauss_closed(r1, 5.0)
        p, w = _resample_weight(r1, w1, step)
    _, nl = normals(p)
    shift = register(dtm, p, nl, width, step, max_shift=max_shift) if max_shift > 0 else np.zeros(len(p))
    shift = _hold_through(shift, w > 0.01)
    q = p + shift[:, None] * nl
    q = w[:, None] * q + (1.0 - w[:, None]) * gauss_closed(q, 1.5)
    p = resample_closed(q, step)
    need = (width / 2.0 + IMPORTED_MIN_INNER_EDGE_RADIUS_M) * 1.03  # rule min-corner-radius for real tracks, +3 %
    p, eased = ease_corners(p, need)
    return resample_closed(p, step), shift, eased


def node_bng(osm: dict, node: int) -> np.ndarray:
    for e in osm["elements"]:
        if e["type"] == "way" and node in e["nodes"]:
            g = e["geometry"][e["nodes"].index(node)]
            return np.array(to_grid(g["lat"], g["lon"]))
    raise ValueError(f"node {node} isn't on any fetched way")


def branch_of(alt: np.ndarray, main: np.ndarray, step: float, blend_m: float = 30.0,
              core: tuple[float, float] | None = None, off_m: float = 1.0,
              join_within_m=None) -> np.ndarray:
    """The part of a fitted alternative lap that leaves the main line, with its ends moved onto the nearest main
    points (the move fades out over ``blend_m``). ``core``: the fractions of the lap (from its first point)
    where it runs on ways the main lap doesn't use (``non_main_span``); the branch is that span grown at both
    ends while the line stays more than 1 m off the main one, plus 10 m. Without ``core``, the longest run of
    samples more than 1 m off (a lap fitted independently can differ from the main one by more than that
    elsewhere, so recipes get the core). Raises if the layouts share nothing."""
    d = np.min(np.hypot(alt[:, None, 0] - main[None, :, 0], alt[:, None, 1] - main[None, :, 1]), axis=1)
    off = d > off_m
    if off.all() or not off.any():
        raise ValueError("the alternative layout doesn't share part of the main lap")
    n = len(alt)
    if core is not None:
        k0 = int(math.floor(core[0] * n)) % n
        span = int(math.ceil(((core[1] - core[0]) % 1.0) * n))
        best = (0, span)
        while best[0] > -n // 4 and off[(k0 + best[0] - 1) % n]:
            best = (best[0] - 1, best[1])
        while best[1] < n // 2 and off[(k0 + best[1] + 1) % n]:
            best = (best[0], best[1] + 1)
    else:
        k0 = int(np.flatnonzero(~off)[0])  # start the scan on a shared sample so no run wraps
        best, cur = (0, 0), None
        for k in range(n + 1):
            i = (k0 + k) % n
            if k < n and off[i]:
                cur = (k, k) if cur is None else (cur[0], k)
            elif cur is not None:
                if cur[1] - cur[0] > best[1] - best[0]:
                    best = cur
                cur = None
    ext = int(round(10.0 / step))
    idx = [(k0 + k) % n for k in range(best[0] - ext, best[1] + ext + 1)]
    # a shallow merge: join where the line first comes within join_within_m of the main one; a number trims both
    # ends, [start, end] each end on its own (null: that end as usual)
    jw = join_within_m if isinstance(join_within_m, (list, tuple)) else [join_within_m, join_within_m]
    if jw[1]:
        while len(idx) > 2 * ext + 2 and d[idx[-1]] <= jw[1] and d[idx[-2]] <= jw[1]:
            idx.pop()
    if jw[0]:
        while len(idx) > 2 * ext + 2 and d[idx[0]] <= jw[0] and d[idx[1]] <= jw[0]:
            idx.pop(0)
    b = alt[idx].copy()
    s = arc(b)
    for end in (0, -1):
        j = int(np.argmin(np.hypot(*(main - b[end]).T)))
        move = main[j] - b[end]
        # the move fades out over blend_m, or five times its size when a trimmed end (joinWithinM) sits metres off
        bl = max(blend_m, 5.0 * float(np.hypot(*move)))
        w = np.clip(1.0 - (s if end == 0 else s[-1] - s) / bl, 0.0, 1.0)
        b += move[None, :] * w[:, None]
    return resample_open(b, step)


def start_finish_point(sf: dict, osm: dict) -> np.ndarray:
    """A recipe's start/finish line (BNG): ``{"node": id}`` (an OSM node on a fetched way), ``{"alongWay": id,
    "fraction": f}`` (that far along the way) or ``{"lat", "lon"}``."""
    if "alongWay" in sf:
        way = next(e for e in osm["elements"] if e["type"] == "way" and e["id"] == sf["alongWay"])
        g = np.array([to_grid(q["lat"], q["lon"]) for q in way["geometry"]])
        s_way = arc(g)
        t = float(sf.get("fraction", 0.5)) * s_way[-1]
        return np.array([np.interp(t, s_way, g[:, 0]), np.interp(t, s_way, g[:, 1])])
    if "node" in sf:
        return node_bng(osm, int(sf["node"]))
    return np.array(to_grid(sf["lat"], sf["lon"]))


SOURCE_KEYS = ("layoutSource", "directionSource", "startFinishSource")


def source_problems(recipe: dict) -> list[str]:
    """What a recipe lacks in sources (2026-10-20, MSV pack rules): the main layout and every ``altLayouts``
    entry say where their route (``layoutSource``), racing direction (``directionSource``) and start/finish
    line (``startFinishSource``) come from: official circuit maps first (the circuit's site, MSV track
    guides), then other reliable sources; OSM way direction alone doesn't count. A layout with its own line
    has ``startFinish``; one without shares the main line, and its ``startFinishSource`` says so."""
    out = []
    for where, d in [("main layout", recipe)] + [(f"layout {a.get('id')}", a) for a in recipe.get("altLayouts", [])]:
        for k in SOURCE_KEYS:
            v = d.get(k)
            if not isinstance(v, str) or len(v.strip()) < 10:
                out.append(f"{where}: no {k}")
    return out


def non_main_span(ll: list[tuple[float, float]], ids: list[int], main_ids: list[int]) -> tuple[float, float]:
    """The fractions of a stitched alternative lap (from its first point, by OSM arc length) of its run of OSM
    segments (node to node, in the driving direction) that the main lap doesn't drive: where the layout leaves
    the main line (``branch_of``'s core). Segments, not ways, so a layout may use part of a way the main lap
    uses another part of (Bedford's loops). The lap must have one such run."""
    main_edges = {(a, b) for a, b in zip(main_ids, main_ids[1:] + main_ids[:1])}
    n = len(ids)
    own = [(ids[k], ids[(k + 1) % n]) not in main_edges for k in range(n)]
    if not any(own):
        raise ValueError("the alternative layout uses only the main lap's segments")
    if all(own):
        raise ValueError("the alternative layout doesn't share part of the main lap")
    k0 = own.index(False)  # start the scan on a shared segment so no run wraps
    runs, cur = [], None
    for t in range(1, n + 1):
        k = (k0 + t) % n
        if own[k] and t < n:
            cur = (k, k) if cur is None else (cur[0], k)
        elif cur is not None:
            runs.append(cur)
            cur = None
    if len(runs) != 1:
        raise ValueError("the alternative layout leaves the main lap more than once")
    p = np.array([to_grid(a, b) for a, b in ll])
    s = arc(np.vstack([p, p[:1]]))
    # from the run's first point (where it leaves the main lap) to its last (where it rejoins), as the way-based
    # span with the way-start fix (4e47b37) gives for laps made of whole ways
    st, en = runs[0]
    return float(s[st] / s[-1]), float(s[min(en + 1, n)] / s[-1])


def corner_order_problem(names: list[str], expected: list[str] | None) -> str | None:
    """None when the corner labels (in driving order from the start line) follow the recipe's
    ``expectedCornerOrder`` (the published lap order, e.g. from track guides); otherwise what is wrong.
    Expected names without a label (not named in OSM) are skipped; at least half must be labelled. The lap
    is a loop, so the labels may start anywhere in the expected cycle."""
    if not expected:
        return None
    seen = [n for n in names if n in expected]
    if len(set(seen)) < max(2, len(expected) // 2):
        return f"only {sorted(set(seen))} of the expected corners are labelled"
    want = [n for n in expected if n in seen]
    k = want.index(seen[0])
    if seen != want[k:] + want[:k]:
        return f"labels run {seen}, expected {want} (reversed or out of order)"
    return None


def kerb_overrides(items: list[dict], corners: list[dict]) -> list[dict]:
    """The recipe's per-corner kerb data (``kerbs``: corner name, entry/apex/exit/green flags, source) placed at
    the named corner's sM, for ``sketch.imported.kerbs``. Real data only (OSM kerb features, a clear LIDAR
    ridge, a track guide); corners without an entry get the builder's geometric kerbs."""
    at = {c["name"]: c["sM"] for c in corners}
    out = []
    for k in items:
        if k["corner"] not in at:
            raise ValueError(f"kerbs: no labelled corner {k['corner']!r} (labels: {sorted(at)})")
        o = {"corner": k["corner"], "sM": at[k["corner"]], "source": k["source"]}
        o.update({f: bool(k[f]) for f in ("entry", "apex", "exit", "green") if f in k})
        out.append(o)
    return out


def default_slug(name: str) -> str:
    """The mod slug for a track name: the name in lower case with everything but letters and digits dropped
    ("Cadwell Park" -> "cadwellpark")."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def recipe_slug(recipe: dict) -> str:
    """The recipe's slug: ``slug`` when given, else the default for its ``name``. Real tracks are named after the
    circuit alone (2026-10-20, Chris: no "Real" prefix): the name may not start with "Real" and an explicit slug
    must be the default of the name or of its last words ("Circuito de Navarra" -> ``navarra``)."""
    name = recipe["name"]
    if re.match(r"Real(?=[A-Z\s])", name):
        raise ValueError(f"recipe name {name!r}: use the plain track name, no 'Real' prefix")
    slug = recipe.get("slug") or default_slug(name)
    words = name.split()
    if slug not in {default_slug(" ".join(words[k:])) for k in range(len(words))}:
        raise ValueError(f"recipe slug {slug!r} isn't the name's default {default_slug(name)!r} (or its last words')")
    return slug


# Profile artefact checks (2026-10-24 accuracy audit). A real road's straights are straight, their grade changes
# smoothly (vertical curves of hundreds of metres) and their camber keeps its sign; DTM artefacts (whole-metre
# terraces, tile seams, trees, bridges) and OSM tracing wobble break that. ``profile_series`` gives the per-sample
# series the audit plots and ``profile_artefacts`` the summary that the importer reports and
# geom/tests/test_import_real_track.py checks on every committed sample.
STRAIGHT_MAX_CURVATURE = 1.0 / 400.0  # a straight: |curvature| (smoothed over 10 m) under this ...
STRAIGHT_MIN_LENGTH_M = 100.0         # ... for at least this far; its first and last 20 m are left out


def _periodic_interp(s: np.ndarray, v: np.ndarray, t: np.ndarray, length: float, closed: bool) -> np.ndarray:
    if closed:
        return np.interp(t % length, np.concatenate([s, [length]]), np.concatenate([v, v[:1]]))
    return np.interp(np.clip(t, s[0], s[-1]), s, v)


def profile_series(xy: np.ndarray, z: np.ndarray, bank: np.ndarray, closed: bool = True) -> dict:
    """Per-sample series of a centreline (local metres, heights, camber in degrees): arc length ``s``, signed
    curvature ``k`` (1/m, left positive, smoothed over 4 m), grades over 10 and 50 m (centred, fractions), the
    vertical curvature ``kv`` (change of the 10 m grade per metre over 10 m), the grade ripple on straights (10 m
    grade minus its 15 m smoothing), the lateral wobble (offset from the line's 15 m smoothing, about its median per
    straight), the camber rate (degrees per metre, over 4 m) and the ``straight`` mask (interiors of straights)."""
    xy = np.asarray(xy, dtype=np.float64)
    z, bank = np.asarray(z, dtype=np.float64), np.asarray(bank, dtype=np.float64)
    n = len(xy)
    seg = np.hypot(*np.diff(np.vstack([xy, xy[:1]]) if closed else xy, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])[:n]
    length = float(seg.sum()) if closed else float(s[-1])
    ds = length / max(len(seg), 1)
    sm = (lambda v, sig: gauss_closed(v, sig / ds)) if closed else (lambda v, sig: gauss_open(v, sig / ds))
    if closed:
        tng = np.roll(xy, -1, axis=0) - np.roll(xy, 1, axis=0)
    else:
        tng = np.gradient(xy, axis=0)
    head = np.unwrap(np.arctan2(tng[:, 1], tng[:, 0]))
    if closed:
        dh = (np.roll(head, -1) - np.roll(head, 1) + math.pi) % (2 * math.pi) - math.pi
        k = dh / (2 * ds)
    else:
        k = np.gradient(head) / ds
    k = sm(k, 4.0)
    zi = lambda t: _periodic_interp(s, z, t, length, closed)  # noqa: E731
    g10 = (zi(s + 5.0) - zi(s - 5.0)) / 10.0
    g50 = (zi(s + 25.0) - zi(s - 25.0)) / 50.0
    gi = lambda t: _periodic_interp(s, g10, t, length, closed)  # noqa: E731
    kv = (gi(s + 5.0) - gi(s - 5.0)) / 10.0
    bi = lambda t: _periodic_interp(s, bank, t, length, closed)  # noqa: E731
    brate = (bi(s + 2.0) - bi(s - 2.0)) / 4.0
    # straights: low smoothed curvature over a long enough run, less 20 m at each end
    low = np.abs(sm(k, 10.0)) < STRAIGHT_MAX_CURVATURE
    straight = np.zeros(n, dtype=bool)
    # runs of low curvature as (first index, count), scanned from a curved sample so no run wraps
    runs = []
    if low.all():
        runs = [(0, n)]
    elif low.any():
        i0 = int(np.flatnonzero(~low)[0]) if closed else 0
        cur = None
        for t_ in range(n + 1):
            i = (i0 + t_) % n if closed else t_
            if t_ < n and low[i]:
                cur = (i, 1) if cur is None else (cur[0], cur[1] + 1)
            elif cur is not None:
                runs.append(cur)
                cur = None
    trim = int(round(20.0 / ds))
    wob = np.zeros(n)
    nl = normals(xy)[1] if closed else normals_open(xy)
    smooth_xy = np.column_stack([sm(xy[:, 0], 15.0), sm(xy[:, 1], 15.0)])
    off = np.einsum("ij,ij->i", xy - smooth_xy, nl)
    straights = []
    for a, cnt in runs:
        if cnt * ds < STRAIGHT_MIN_LENGTH_M or cnt <= 2 * trim:
            continue
        idx = (a + np.arange(trim, cnt - trim)) % n
        straight[idx] = True
        wob[idx] = off[idx] - np.median(off[idx])
        straights.append((float(s[idx[0]]), float(s[idx[-1]])))
    ripple = np.where(straight, g10 - sm(g10, 15.0), 0.0)
    return {"s": s, "k": k, "g10": g10, "g50": g50, "kv": kv, "ripple": ripple, "wobble": wob, "bankRate": brate,
            "bank": bank, "z": z, "straight": straight, "straights": straights, "ds": ds}


def profile_artefacts(xy: np.ndarray, z: np.ndarray, bank: np.ndarray, closed: bool = True) -> dict:
    """Summary of ``profile_series``: on straights the largest lateral wobble (m), grade ripple (%) and camber sign
    flips (|camber| over 0.75 degrees either side within 60 m); anywhere the tightest crest and dip (vertical radius,
    m, from the 10 m grade's change over 10 m), the largest 10 m grade and the fastest camber change (degrees/m)."""
    p = profile_series(xy, z, bank, closed)
    st, b = p["straight"], p["bank"]
    flips = 0
    if st.any():
        sgn = np.where(b > 0.75, 1, np.where(b < -0.75, -1, 0))
        win = int(round(60.0 / p["ds"]))
        idx = np.flatnonzero(st & (sgn != 0))
        for i, j in zip(idx, idx[1:]):
            if sgn[i] != sgn[j] and j - i <= win and st[i:j + 1].all():
                flips += 1
    kv = p["kv"]
    return {
        "straightsM": round(float(st.sum() * p["ds"]), 1),
        "straightWobbleMaxM": round(float(np.abs(p["wobble"]).max()), 3),
        "straightGradeRipplePct": round(float(np.abs(p["ripple"]).max()) * 100, 2),
        "straightGradeRippleRmsPct": round(float(np.sqrt(np.mean(p["ripple"][st] ** 2))) * 100 if st.any() else 0.0, 3),
        "straightWobbleRmsM": round(float(np.sqrt(np.mean(p["wobble"][st] ** 2))) if st.any() else 0.0, 3),
        "straightCamberFlips": int(flips),
        "minCrestRadiusM": round(float(1.0 / max(-kv.min(), 1e-9)), 0),
        "minDipRadiusM": round(float(1.0 / max(kv.max(), 1e-9)), 0),
        "maxAbsGradePct10m": round(float(np.abs(p["g10"]).max()) * 100, 2),
        "maxCamberRateDegPerM": round(float(np.abs(p["bankRate"]).max()), 3),
    }


def build(recipe: dict, cache: Path, step: float = 2.0, cell: float | None = None, log=print) -> tuple[dict, dict]:
    """The TrackSpec and a report dict (lengths, heights, grades, camber, data checks)."""
    set_projection(recipe)
    missing = source_problems(recipe)
    if missing:
        raise ValueError("recipe sources: " + "; ".join(missing))
    cell = float(cell or recipe.get("terrainCellM", 6.0))
    osm = with_extra_ways(fetch_osm(recipe["bbox"], cache), recipe.get("extraWays", []), cache)
    joins = recipe.get("nodeJoins", [])
    for a_, b_ in joins:  # near misses only
        if float(np.hypot(*(node_bng(osm, int(a_)) - node_bng(osm, int(b_))))) > 5.0:
            raise ValueError(f"nodeJoins {a_}-{b_}: more than 5 m apart")
    ll, marks, main_ids = stitch_nodes(osm, recipe["lap"], joins)
    bng = np.array([to_grid(a, b) for a, b in ll])
    lat_c, lon_c = np.mean([q[0] for q in ll]), np.mean([q[1] for q in ll])
    k_scale = grid_scale_at(lat_c, lon_c)
    clockwise = signed_area(bng) < 0
    if recipe.get("clockwise") is not None and clockwise != bool(recipe["clockwise"]):
        raise ValueError(f"the OSM ways run {'clockwise' if clockwise else 'anticlockwise'}, the recipe says otherwise")
    margin = float(recipe.get("terrainMarginM", 400.0))
    lo, hi = bng.min(0) - margin, bng.max(0) + margin
    log(f"Fetching the DTM over {hi[0] - lo[0]:.0f} x {hi[1] - lo[1]:.0f} m ...")
    dtm = load_dtm(recipe, lo[0], lo[1], hi[0], hi[1], cache)
    width = float(recipe["widthM"])

    # 1. Smooth the OSM polyline and register it to the road in the DTM.
    # registrationMaxShiftM 0: keep the OSM line (flat ground such as an airfield has no road signal in the DTM, so
    # the registration only drifts, differently per layout)
    max_shift = float(recipe.get("registrationMaxShiftM", 6.0))
    sharp_ways = recipe.get("sharpWays", [])
    p, shift, eased = fit_loop(bng, dtm, width, step, max_shift, sharp_segments(marks, len(bng), sharp_ways))

    # 2. Start/finish: the recipe's point; sample 0 sits on it.
    ways = {e["id"]: e for e in osm["elements"] if e["type"] == "way"}
    s0 = project_s(p, start_finish_point(recipe["startFinish"], osm))
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
    # height and camber smoothing (sigma, m): 4 and 10 m on a 1 m DTM, wider on a coarser one (the 5 m IGN MDT05
    # steps by a decimetre or so between cells, a 4 % "grade" over a 2 m sample)
    z_sig, b_sig = max(4.0, 2.0 * dtm.cell), max(10.0, 3.0 * dtm.cell)
    z = ease_vertical(gauss_closed(a, z_sig / step), step)
    slope = gauss_closed(b, b_sig / step)
    bank_fit = np.degrees(np.arctan(slope))
    bank = np.clip(bank_fit, -IMPORTED_MAX_BANK_DEG, IMPORTED_MAX_BANK_DEG)
    datum = math.floor(float(z.min()))

    # 4. Local frame: x east, y north, metres on the ground, centred on the line's bounding box.
    centre = (p.min(0) + p.max(0)) / 2.0
    loc = (p - centre) / k_scale
    L = closed_length(loc)
    s = np.arange(n) * L / n
    grade = (np.roll(z, -5) - np.roll(z, 5)) / (10.0 * step / k_scale)  # over 20 m, centred
    g10 = (np.roll(z, -5) - z) / (5 * step / k_scale)                     # over 10 m, forward

    # 4b. Alternative layouts sharing the base: fit each whole lap the same way, keep the part that leaves the
    # main line (its ends moved onto it), with its own heights and camber.
    alt_specs, alt_reports, alt_branches = [], [], []
    for alt in recipe.get("altLayouts", []):
        ll_a, marks_a, ids_a = stitch_nodes(osm, alt["lap"], joins)
        bng_a = np.array([to_grid(x, y) for x, y in ll_a])
        # a layout normally winds the same way as the main lap; one that doesn't says so (Snetterton's 100 is the
        # 300's infield loop, driven the same way but anticlockwise as a loop): "clockwise": false
        if (signed_area(bng_a) < 0) != bool(alt.get("clockwise", clockwise)):
            raise ValueError(f"layout {alt['id']} runs the other way round from the main lap (or its recipe's clockwise)")
        pa, shift_a, eased_a = fit_loop(bng_a, dtm, width, step, max_shift, sharp_segments(marks_a, len(bng_a), sharp_ways))
        # branchOffM: how far off the main line a layout's line counts as its own (default 1 m; unregistered OSM
        # lines on shared ways match exactly but are sampled out of phase, up to 1 m apart vertex to vertex)
        # joinWithinM (per layout): a branch that runs beside the main road for a while before it merges (Bedford
        # SEN's return beside the south straight, paved between) joins where it first comes within this distance
        br = branch_of(pa, p, step, core=non_main_span(ll_a, ids_a, main_ids), off_m=float(recipe.get("branchOffM", 1.0)),
                       join_within_m=alt.get("joinWithinM"))
        alt_branches.append(br)
        nl_b = normals_open(br)
        ab, bb, res_b = cross_fit(dtm, br, nl_b, offs)
        if np.isnan(ab).any():
            raise RuntimeError(f"the DTM has gaps under layout {alt['id']}")
        zb = gauss_open(ab, z_sig / step)
        bank_b = np.clip(np.degrees(np.arctan(gauss_open(bb, b_sig / step))), -IMPORTED_MAX_BANK_DEG, IMPORTED_MAX_BANK_DEG)
        loc_b = (br - centre) / k_scale
        # the branch's ends take the main road's height and camber where they join, the difference faded out
        # along the branch (geom only blends heights over 20 m): a shallow merge's end lies beside the main road
        # (Navarra Short's hook, 12 m off: 1 m lower, a 14 % grade spike and a camber step at the join)
        j0 = int(np.argmin(np.hypot(*(loc - loc_b[0]).T)))
        j1 = int(np.argmin(np.hypot(*(loc - loc_b[-1]).T)))
        zb, bank_b = join_blend(arc(loc_b), zb, bank_b, (z[j0], z[j1]), (bank[j0], bank[j1]))
        zb = ease_vertical(zb, step, closed=False)
        alt_specs.append({"id": alt["id"], "name": alt["name"],
                          "centreline": [{"x": round(float(x), 2), "y": round(float(y), 2), "z": round(float(zv - datum), Z_DECIMALS),
                                          "bankDeg": round(float(bk), 2)} for (x, y), zv, bk in zip(loc_b, zb, bank_b)]})
        if alt.get("startFinish"):  # the layout's own line, snapped onto the fitted road (main or branch)
            q_sf = start_finish_point(alt["startFinish"], osm)
            both = np.vstack([p, br])
            snap = both[int(np.argmin(np.hypot(*(both - q_sf).T)))]
            xy_sf = (snap - centre) / k_scale
            alt_specs[-1]["startFinish"] = {"x": round(float(xy_sf[0]), 2), "y": round(float(xy_sf[1]), 2)}
        i0 = int(np.argmin(np.hypot(*(loc - loc_b[0]).T)))
        i1 = int(np.argmin(np.hypot(*(loc - loc_b[-1]).T)))
        Lb = float(arc(loc_b)[-1])
        back = [(i1 + k) % n for k in range((i0 - i1) % n + 1)]  # main from the rejoin round to the branch start
        lap_z = np.concatenate([zb[1:-1], z[back]])
        lap_xy = np.vstack([loc_b[1:-1], loc[back]])
        seg = np.hypot(*np.diff(np.vstack([lap_xy, lap_xy[:1]]), axis=0).T)
        dzl = np.diff(np.concatenate([lap_z, lap_z[:1]]))
        g_lap = dzl / np.maximum(seg, 1e-6)
        g10_lap = (np.roll(lap_z, -5) - lap_z) / (5 * step / k_scale)
        alt_reports.append({
            "id": alt["id"], "name": alt["name"], "branchLengthM": round(Lb, 1),
            "lapLengthM": round(float(Lb + (s[i0] - s[i1]) % L), 1),
            "measuredLapLengthM": alt.get("measuredLapLengthM"), "publishedLapLengthM": alt.get("publishedLapLengthM"),
            "leavesMainAtS": round(float(s[i0]), 1), "rejoinsMainAtS": round(float(s[i1]), 1),
            "joinGapsM": [round(float(np.hypot(*(loc[i0] - loc_b[0]))), 2), round(float(np.hypot(*(loc[i1] - loc_b[-1]))), 2)],
            "heightMinM": round(float(lap_z.min()), 2), "heightMaxM": round(float(lap_z.max()), 2),
            "totalClimbM": round(float(np.clip(dzl, 0, None).sum()), 1),
            "maxUpGradePct10m": round(float(g10_lap.max()) * 100, 1), "maxDownGradePct10m": round(float(g10_lap.min()) * 100, 1),
            "branchBankMaxDeg": round(float(np.abs(bank_b).max()), 2),
            "branchMinRadiusM": round(float(radii_open(loc_b).min()), 2),
            "branchCrossResidualM": round(float(res_b.mean()), 3),
            "lapEasingMaxM": round(eased_a, 2), "registrationShiftM": [round(float(shift_a.min()), 2), round(float(shift_a.max()), 2)],
            "maxStepGradePct": round(float(np.abs(g_lap).max()) * 100, 1),
            "artefacts": profile_artefacts(loc_b, zb, bank_b, closed=False),
        })

    # 5. Terrain: block means of the DTM onto a coarser grid (nodes), in the local frame. Sea (no-data) cells
    # are seabed just below the recipe's sea level, else the mean ground.
    e0, n0 = lo
    nx, ny = int((hi[0] - lo[0]) // cell), int((hi[1] - lo[1]) // cell)
    ex = e0 + np.arange(nx + 1) * cell
    ny_ = n0 + np.arange(ny + 1) * cell
    half = max(int(cell / dtm.cell // 2), 0)  # block half-width in DTM cells
    zz = np.empty((ny + 1, nx + 1))
    with np.errstate(all="ignore"):
        import warnings  # noqa: PLC0415
        warnings.simplefilter("ignore", RuntimeWarning)
        for j, yv in enumerate(ny_):
            r0 = int(round((dtm.y1 - yv) / dtm.cell)) - half
            rows = dtm.z[max(r0, 0):max(r0, 0) + 2 * half + 1]
            for i, xv in enumerate(ex):
                c0 = int(round((xv - dtm.x0) / dtm.cell)) - half
                blk = rows[:, max(c0, 0):max(c0, 0) + 2 * half + 1]
                zz[j, i] = np.nanmean(blk) if blk.size else np.nan
    sea = recipe.get("seaLevelODN")
    sea_cells = int(np.isnan(zz).sum())
    zz = np.where(np.isnan(zz), (float(sea) - 3.0) if sea is not None else np.nanmean(zz), zz)
    origin = (np.array([e0, n0]) - centre) / k_scale

    # 5b. Lakes (recipe "lakes": {"minAreaM2", "depthM"}): OSM open water in the terrain box, away from the road;
    # the terrain nodes inside lie depthM below the water level (the LIDAR DTM is the water surface there).
    lake_list = []
    if recipe.get("lakes"):
        lk = recipe["lakes"]
        lat_m = 1.0 / 111_000.0
        lon_m = lat_m / max(math.cos(math.radians(lat_c)), 1e-6)
        s_, w_, n_, e_ = recipe["bbox"]
        wbox = [s_ - margin * lat_m, w_ - margin * lon_m, n_ + margin * lat_m, e_ + margin * lon_m]
        water = [(nm, np.array([to_grid(a_, b_) for a_, b_ in ring])) for nm, ring in water_polygons(fetch_osm_water(wbox, cache))]
        water = [(nm, w) for nm, w in water if (w.max(0) > lo).all() and (w.min(0) < hi).all()]
        roads = np.vstack([p] + [np.asarray(b_) for b_ in alt_branches])
        lake_list = lakes_from(water, dtm, roads, float(lk.get("minAreaM2", 2000.0)), width / 2.0 + 4.0)
        depth = float(lk.get("depthM", 1.0))
        gx_, gy_ = np.meshgrid(ex, ny_)
        nodes = np.column_stack([gx_.ravel(), gy_.ravel()])
        for lake in lake_list:
            ins = point_in_polygon(nodes, lake["poly"]).reshape(zz.shape)
            zz = np.where(ins, np.minimum(zz, lake["level"] - depth), zz)
            lake["carvedNodes"] = int(ins.sum())

    # 6. Woods (OSM) within the terrain grid, names and viewing spots along the line.
    box_lo, box_hi = (lo - centre) / k_scale, (hi - centre) / k_scale
    wood_polys = []
    for poly in woods(osm):
        w = (np.array([to_grid(la, lo_) for la, lo_ in poly]) - centre) / k_scale
        if len(w) > 2 and np.allclose(w[0], w[-1]):
            w = w[:-1]
        if len(w) < 3 or (w.max(0) < box_lo).any() or (w.min(0) > box_hi).any():
            continue
        wood_polys.append(w)
    # one label per name, at the middle of its (consecutive) ways; lapNames null drops a way's label
    names_map = recipe.get("lapNames", {})
    groups: list[tuple[str | None, list[int]]] = []
    for wid, _, name in marks:
        label = names_map[str(wid)] if str(wid) in names_map else (name or str(wid))
        if groups and groups[-1][0] == label:
            groups[-1][1].append(wid)
        else:
            groups.append((label, [wid]))
    corners = []
    for label, wids in groups:
        if label is None:
            continue
        g = np.vstack([np.array([to_grid(q_["lat"], q_["lon"]) for q_ in ways[w]["geometry"]]) for w in wids])
        sg = arc(g)
        mid = np.array([np.interp(sg[-1] / 2, sg, g[:, 0]), np.interp(sg[-1] / 2, sg, g[:, 1])])
        corners.append({"name": label, "sM": round(project_s(p, mid) / k_scale, 1)})
    for c in recipe.get("cornerLabels", []):  # names at OSM nodes (corners of a long way, e.g. a whole loop)
        corners.append({"name": c["name"], "sM": round(project_s(p, node_bng(osm, int(c["node"]))) / k_scale, 1)})
    corners.sort(key=lambda c: c["sM"])
    order_problem = corner_order_problem([c["name"] for c in corners], recipe.get("expectedCornerOrder"))
    if order_problem:
        raise ValueError(f"corner order along the driving direction: {order_problem}")
    spots = [c for c in corners if c["name"] in recipe.get("viewingSpots", [])]

    # 7. The spec.
    canvas_margin = 100.0
    span = loc.max(0) - loc.min(0)
    W, H = float(math.ceil(span[0] + 2 * canvas_margin)), float(math.ceil(span[1] + 2 * canvas_margin))
    imported = {
        "source": {"wales": ATTRIBUTION_WALES, "es-mdt05": ATTRIBUTION_ES, "es-navarra": ATTRIBUTION_NAVARRA}.get(recipe.get("dtm"), ATTRIBUTION),
        "datumM": datum,
        "centreline": [{"x": round(float(x), 2), "y": round(float(y), 2), "z": round(float(zv - datum), Z_DECIMALS),
                        "bankDeg": round(float(bk), 2)} for (x, y), zv, bk in zip(loc, z, bank)],
        "terrain": {"originX": round(float(origin[0]), 2), "originY": round(float(origin[1]), 2),
                    "cellM": round(cell / k_scale, 4), "nx": nx, "ny": ny,
                    "z": [round(float(v - datum), 2) for v in zz.ravel()]},
        "woodland": [{"points": [{"x": round(float(x), 1), "y": round(float(y), 1)} for x, y in w]} for w in wood_polys],
        **({"lakes": [{**({"name": lk_["name"][:60]} if lk_["name"] else {}), "levelM": round(lk_["level"] - datum, 2),
                       "points": [{"x": round(float(x), 1), "y": round(float(y), 1)}
                                  for x, y in (lk_["poly"][:: max(1, math.ceil(len(lk_["poly"]) / 2000))] - centre) / k_scale]}
                      for lk_ in lake_list]} if lake_list else {}),
        "corners": corners,
        "viewingSpots": spots,
    }
    if recipe.get("kerbs"):
        imported["kerbs"] = kerb_overrides(recipe["kerbs"], corners)
    if alt_specs:
        imported["mainLayoutName"] = recipe.get("mainLayoutName", "Main")
        imported["altLayouts"] = alt_specs
    if sea is not None:
        imported["seaLevelM"] = round(float(sea) - datum, 2)
    if recipe.get("horizon") and recipe.get("dtm") == "wales":
        log("Horizon profile from the DTM overview ...")
        imported["horizon"] = horizon_profile(recipe, p[0], float(z[0]) + 1.5, cache, float(sea or 0.0))
    o = recipe["options"]
    spec = {
        "meta": {"name": recipe["name"], "slug": recipe_slug(recipe), "description": recipe["trackDescription"][:500]},
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
        "measuredLapLengthM": recipe.get("measuredLapLengthM"),
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
        "bankMaxDeg": round(float(np.abs(bank).max()), 2), "bankFittedMaxDeg": round(float(np.abs(bank_fit).max()), 2), "bankP95Deg": round(float(np.percentile(np.abs(bank), 95)), 2),
        "bankMeanDeg": round(float(np.abs(bank).mean()), 2), "datumM": datum,
        "terrainGrid": [nx + 1, ny + 1], "terrainCellM": cell, "seaCells": sea_cells, "woods": len(wood_polys),
        "lakes": [{"name": lk_["name"], "areaM2": round(lk_["areaM2"]), "levelM": round(lk_["level"], 2),
                   "carvedNodes": lk_["carvedNodes"]} for lk_ in lake_list],
        "corners": corners,
        "artefacts": profile_artefacts(loc, z, bank),
        "dtmCellM": dtm.cell, "dtmWholeMetreFraction": round(dtm_quantised_fraction(dtm), 3),
    }
    if alt_reports:
        report["mainLayoutName"] = recipe.get("mainLayoutName", "Main")
        report["altLayouts"] = alt_reports
    for c in corners:
        i = int(round(c["sM"] / (L / n))) % n
        c_rep = {"zM": round(float(z[i]), 1), "gradePct": round(float(grade[i]) * 100, 1), "bankDeg": round(float(bank[i]), 1)}
        report.setdefault("atCorners", {})[c["name"]] = c_rep
    m0 = next((c for c in corners if c["name"] == "The Mountain"), None)
    if m0:
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


def radii_open(p: np.ndarray, k: int = 3) -> np.ndarray:
    r = radii(p, k)
    return r[k:-k] if len(r) > 2 * k + 1 else r


def verify(spec: dict, report: dict, recipe: dict, cache: Path) -> dict:
    """Build the TrackModel (needs trackgen_geom, e.g. geom's venv) and compare it with the source data:
    lap length against the published one, road heights (centreline and both cambered edges, on every route)
    against the DTM, camber, each layout's lap, and the model's rule verdict."""
    from trackgen_geom import build_track_model  # noqa: PLC0415 - optional dependency

    set_projection(recipe)

    result = build_track_model(spec)
    if result.model is None:
        return {"built": False, "errors": [e.message for e in result.errors]}
    fr = report["frame"]
    datum = spec["sketch"]["imported"]["datumM"]
    margin = float(recipe.get("terrainMarginM", 400.0))
    routes = result.model.routes
    allc = np.vstack([np.array([[q.x, q.y] for q in r.samples]) for r in routes])
    lo = allc.min(0) * fr["scale"] + [fr["centreE"], fr["centreN"]] - min(margin, 100.0)
    hi = allc.max(0) * fr["scale"] + [fr["centreE"], fr["centreN"]] + min(margin, 100.0)
    dtm = load_dtm(recipe, lo[0], lo[1], hi[0], hi[1], cache)

    def dz(p: np.ndarray) -> np.ndarray:
        e = p[:, 0] * fr["scale"] + fr["centreE"]
        n = p[:, 1] * fr["scale"] + fr["centreN"]
        return p[:, 2] + datum - dtm.sample(e, n)

    rms = lambda v: round(float(np.sqrt(np.nanmean(v ** 2))), 3)  # noqa: E731
    per_route = {}
    for r in routes:
        c = np.array([[q.x, q.y, q.z] for q in r.samples])
        left, right = np.array(r.left_edge), np.array(r.right_edge)
        # the edges are compared 10 % inside the road (the DTM's verge kerbs and ditches start at the edge)
        li, ri = c + (left - c) * 0.8, c + (right - c) * 0.8
        per_route[r.id] = {"rmsCentreVsDtmM": rms(dz(c)), "rmsInnerEdgesVsDtmM": rms(np.concatenate([dz(li), dz(ri)])),
                           "maxAbsCentreVsDtmM": round(float(np.nanmax(np.abs(dz(c)))), 3), "lengthM": round(r.length_m, 1)}
    pub = recipe.get("publishedLapLengthM")
    meas = recipe.get("measuredLapLengthM")
    layouts = []
    for lay in result.model.layouts:
        src = recipe if lay.id == "main" else next(a for a in recipe.get("altLayouts", []) if a["id"] == lay.id)
        want, publ = src.get("measuredLapLengthM"), src.get("publishedLapLengthM")
        g = lay.grid or result.model.grid
        layouts.append({"id": lay.id, "name": lay.name, "lapLengthM": round(lay.lap_length_m, 1), "measuredLapLengthM": want,
                        "vsMeasuredPct": round(100 * (lay.lap_length_m - want) / want, 2) if want else None,
                        "publishedLapLengthM": publ,
                        "vsPublishedPct": round(100 * (lay.lap_length_m - publ) / publ, 2) if publ else None,
                        "ownStartFinish": lay.start_finish is not None, "gridSlots": len(g.slots),
                        "sections": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in sec.items()}
                                     for sec in lay.sections]})
    main = per_route["main"]
    return {
        "built": True, "valid": result.valid,
        "violations": [f"{v.rule_id}: {v.message}" for v in result.rule_result.violations],
        "warnings": [w.code for w in result.warnings],
        "lapLengthM": round(result.model.lap_length_m, 1),
        "lapVsPublishedPct": round(100 * (result.model.lap_length_m - pub) / pub, 2) if pub else None,
        "rmsCentreVsDtmM": main["rmsCentreVsDtmM"], "rmsInnerEdgesVsDtmM": main["rmsInnerEdgesVsDtmM"],
        "maxAbsCentreVsDtmM": main["maxAbsCentreVsDtmM"], "routes": per_route, "layouts": layouts,
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
    cache = Path(args.cache) if args.cache else DEFAULT_CACHE_ROOT / recipe_slug(recipe)
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
