"""Download the open LIDAR point-cloud tiles under a real circuit's roads (for measure_width.py).

    py tools/real_tracks_data/fetch_pointcloud.py cadwellpark

England: the Environment Agency National LIDAR Programme point cloud (LAZ in 5 km OS tiles, Open Government
Licence v3), found through the DEFRA survey catalogue API. Navarre: the Gobierno de Navarra's 2024 LIDAR flight
(LAZ in 1 km tiles, 5 points/m2 with RGBI from the joint camera, CC BY 4.0) from filescartografia.navarra.es.
Wales has no open point cloud (DataMapWales serves only the 2020-22 DTM/DSM rasters): nothing to fetch.

Only tiles within ``--corridor`` metres of a committed sample's centreline (main lap and every branch) are kept.
Files go to E:\\Source\\wreckfest\\.local\\realtracks\\<slug>\\pointcloud\\ (gitignored, never committed).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(r"E:\Source\wreckfest\.local")
EA_SEARCH = "https://environment.data.gov.uk/backend/catalog/api/tiles/collections/survey/search"
NAVARRA_LAZ = "https://filescartografia.navarra.es/5_LIDAR/5_6_2024_NAV_cam_EPSG25830/las_cam_{x}-{y}_2024_NAV_EPSG25830.laz"
UA = {"User-Agent": "WreckfestTrackGen/0.1 (private track importer)"}
REGION = {"navarra": "navarra", "anglesey": "wales"}  # everything else: England


def report_path(slug: str) -> Path:
    for p in (LOCAL / "realtracks" / slug / "report.json", LOCAL / slug / "report.json"):
        if p.exists():
            return p
    raise FileNotFoundError(f"no import report for {slug} (it holds the local frame)")


def roads_projected(slug: str) -> list[np.ndarray]:
    """Every route of the committed sample in the recipe's grid (BNG or UTM 30N), main lap first."""
    spec = json.loads((ROOT / "geom" / "tests" / "samples" / f"{slug}.json").read_text(encoding="utf-8"))
    fr = json.loads(report_path(slug).read_text(encoding="utf-8"))["frame"]
    imp = spec["sketch"]["imported"]
    lines = [imp["centreline"]] + [a["centreline"] for a in imp.get("altLayouts", [])]
    return [np.array([[q["x"], q["y"]] for q in c]) * fr["scale"] + [fr["centreE"], fr["centreN"]] for c in lines]


def _get(url: str, data: bytes | None = None, headers: dict | None = None, timeout: float = 600.0) -> bytes:
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def near(roads: list[np.ndarray], x0: float, y0: float, x1: float, y1: float, corridor: float) -> bool:
    p = np.vstack(roads)
    return bool(((p[:, 0] > x0 - corridor) & (p[:, 0] < x1 + corridor) & (p[:, 1] > y0 - corridor)
                 & (p[:, 1] < y1 + corridor)).any())


def os_tile_origin(tile: str) -> tuple[float, float]:
    """'TF2580' -> the 5 km tile's south-west corner in BNG metres."""
    letters = "ABCDEFGHJKLMNOPQRSTUVWXYZ"  # no I
    l1, l2 = letters.index(tile[0]), letters.index(tile[1])
    e = ((l1 % 5) - 2) * 500000 + (l2 % 5) * 100000
    n = (3 - l1 // 5) * 500000 + (4 - l2 // 5) * 100000
    return e + int(tile[2:4]) * 1000, n + int(tile[4:6]) * 1000


def fetch_england(slug: str, recipe: dict, roads: list[np.ndarray], out: Path, corridor: float, log) -> list[str]:
    s, w, n, e = recipe["bbox"]
    poly = {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}
    res = json.loads(_get(EA_SEARCH, json.dumps(poly).encode(), {"Content-Type": "application/geo+json"}))
    tiles: dict[str, dict] = {}
    for r in res["results"]:
        if r["product"]["id"] != "national_lidar_programme_point_cloud":
            continue
        t = r["tile"]["id"]
        if t not in tiles or r["year"]["id"] > tiles[t]["year"]["id"]:
            tiles[t] = r
    got = []
    for t, r in sorted(tiles.items()):
        x0, y0 = os_tile_origin(t)
        if not near(roads, x0, y0, x0 + 5000, y0 + 5000, corridor):
            continue
        have = list(out.glob(f"{t}_*.laz"))
        if have:
            got.append(have[0].name)
            continue
        log(f"{slug}: downloading {r['label']} ...")
        z = zipfile.ZipFile(io.BytesIO(_get(r["uri"])))
        for m in z.infolist():
            if m.filename.lower().endswith(".laz"):
                (out / Path(m.filename).name).write_bytes(z.read(m))
                got.append(Path(m.filename).name)
        (out / f"{t}.source.json").write_text(json.dumps({"tile": t, "label": r["label"], "uri": r["uri"],
                                                          "year": r["year"]["id"]}, indent=1), encoding="utf-8")
    return got


def fetch_navarra(slug: str, roads: list[np.ndarray], out: Path, corridor: float, log) -> list[str]:
    p = np.vstack(roads)
    got = []
    for xk in range(int((p[:, 0].min() - corridor) // 1000), int((p[:, 0].max() + corridor) // 1000) + 1):
        for yk in range(int((p[:, 1].min() - corridor) // 1000) + 1, int((p[:, 1].max() + corridor) // 1000) + 2):
            # tiles are named by their top-left corner in km: x .. x+1, y-1 .. y
            if not near(roads, xk * 1000, (yk - 1) * 1000, (xk + 1) * 1000, yk * 1000, corridor):
                continue
            name = f"las_cam_{xk}-{yk}_2024_NAV_EPSG25830.laz"
            if (out / name).exists():
                got.append(name)
                continue
            log(f"{slug}: downloading {name} ...")
            (out / name).write_bytes(_get(NAVARRA_LAZ.format(x=xk, y=yk)))
            got.append(name)
    return got


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--corridor", type=float, default=40.0, help="keep tiles within this distance of a road (m)")
    a = ap.parse_args(argv)
    recipe = json.loads((ROOT / "tools" / "real_tracks" / f"{a.slug}.json").read_text(encoding="utf-8"))
    out = LOCAL / "realtracks" / a.slug / "pointcloud"
    out.mkdir(parents=True, exist_ok=True)
    roads = roads_projected(a.slug)
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    region = REGION.get(a.slug, "england")
    if region == "wales":
        log("Wales: no open point cloud (DataMapWales has the 2020-22 DTM/DSM only); measure_width uses the rasters")
        return 0
    got = fetch_navarra(a.slug, roads, out, a.corridor, log) if region == "navarra" else \
        fetch_england(a.slug, recipe, roads, out, a.corridor, log)
    print(json.dumps({"slug": a.slug, "tiles": got}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
