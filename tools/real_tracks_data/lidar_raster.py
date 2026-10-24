"""Rasterise a real circuit's open LIDAR point cloud (or, in Wales, its DSM/DTM) over the terrain box, for
measure_runoff.py: ground height, surface height, canopy height, vegetation and object counts and the ground's
intensity (and, in Navarre, colour and NDVI).

    <venv>\\Scripts\\python tools/real_tracks_data/lidar_raster.py doningtonpark [--res 1.0] [--margin 450]

Needs numpy, scipy and laspy[lazrs] (the widths venv on E:). Reads the tiles fetch_pointcloud.py downloaded
(``--corridor 450`` so the terrain box is covered) one at a time in chunks, single threaded. Writes
E:\\Source\\wreckfest\\.local\\realtracks\\<slug>\\runoff\\raster.npz (raw-derived, gitignored, never committed):

- ``dtm``: mean ground (class 2) height per cell, holes filled from the nearest ground cell;
- ``dsm``: highest non-noise return; ``chm`` = dsm - dtm (canopy, buildings, barriers);
- ``veg``: fraction of returns classified vegetation (3-5) more than 2 m above the ground;
- ``low``: fraction of returns 0.3-2.5 m above the ground that aren't ground (tyre walls, guardrails, walls,
  fences, cars, bushes);
- ``inten``: mean intensity of the ground's last returns, each flight line divided by its own median over the
  box (EA flight lines differ in gain); ``ground_n`` the ground point count;
- Navarre: ``bright`` (RGB mean) and ``ndvi`` of the ground points (the 2024 flight's joint camera).
- Wales (no open point cloud): ``dtm`` and ``dsm`` from the DataMapWales 1 m COGs, ``chm`` their difference.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_pointcloud import LOCAL, REGION, roads_projected  # noqa: E402

CHUNK = 3_000_000
GROUND = (2, 11)  # ground; 11 = road surface (the Navarre 2024 flight classifies the tarmac separately)


def box(slug: str, margin: float, res: float) -> tuple[float, float, int, int]:
    p = np.vstack(roads_projected(slug))
    x0 = np.floor((p[:, 0].min() - margin) / res) * res
    y0 = np.floor((p[:, 1].min() - margin) / res) * res
    nx = int(np.ceil((p[:, 0].max() + margin - x0) / res))
    ny = int(np.ceil((p[:, 1].max() + margin - y0) / res))
    return x0, y0, nx, ny


def _cells(x, y, x0, y0, res, nx, ny):
    i = ((x - x0) / res).astype(np.int64)
    j = ((y - y0) / res).astype(np.int64)
    ok = (i >= 0) & (i < nx) & (j >= 0) & (j < ny)
    return j * nx + i, ok


def source_medians(tiles: list[Path], geo, log) -> dict[int, float]:
    """Median ground last-return intensity per flight line (every 7th point of each chunk)."""
    import laspy  # noqa: PLC0415

    x0, y0, res, nx, ny = geo
    vals: dict[int, list] = {}
    for t in tiles:
        with laspy.open(t) as f:
            for pts in f.chunk_iterator(CHUNK):
                sl = slice(None, None, 7)
                x, y = np.asarray(pts.x)[sl], np.asarray(pts.y)[sl]
                _, ok = _cells(x, y, x0, y0, res, nx, ny)
                cls = np.asarray(pts.classification)[sl]
                last = (np.asarray(pts.return_number) == np.asarray(pts.number_of_returns))[sl]
                m = ok & np.isin(cls, GROUND) & last
                src = np.asarray(pts.point_source_id)[sl][m]
                it = np.asarray(pts.intensity)[sl][m]
                for s in np.unique(src):
                    vals.setdefault(int(s), []).append(it[src == s])
    out = {s: float(np.median(np.concatenate(v))) for s, v in vals.items() if sum(len(a) for a in v) > 500}
    log(f"  {len(out)} flight lines, median intensity {min(out.values()):.0f}-{max(out.values()):.0f}")
    return out


def rasterise(tiles: list[Path], geo, med: dict[int, float], rgb: bool, log) -> dict[str, np.ndarray]:
    import laspy  # noqa: PLC0415

    x0, y0, res, nx, ny = geo
    N = nx * ny
    gsum = np.zeros(N)
    gn = np.zeros(N, np.int32)
    dsm = np.full(N, -np.inf, np.float32)
    isum = np.zeros(N, np.float32)
    acc = {k: np.zeros(N, np.float32) for k in ("bright", "ndvi")} if rgb else {}
    hist = np.zeros(32, np.int64)
    # pass 1: ground and surface
    for t in tiles:
        t0 = time.time()
        with laspy.open(t) as f:
            for pts in f.chunk_iterator(CHUNK):
                x, y, z = np.asarray(pts.x), np.asarray(pts.y), np.asarray(pts.z)
                c, ok = _cells(x, y, x0, y0, res, nx, ny)
                cls = np.asarray(pts.classification)
                hist += np.bincount(np.minimum(cls, 31), minlength=32)
                keep = ok & (cls != 7) & (cls != 18)
                np.maximum.at(dsm, c[keep], z[keep].astype(np.float32))
                last = np.asarray(pts.return_number) == np.asarray(pts.number_of_returns)
                g = ok & np.isin(cls, GROUND)
                np.add.at(gsum, c[g], z[g])
                np.add.at(gn, c[g], 1)
                gl = g & last
                src = np.asarray(pts.point_source_id)[gl]
                gain = np.array([med.get(int(s), np.nan) for s in np.unique(src)])
                lut = dict(zip(np.unique(src).tolist(), gain.tolist()))
                norm = np.asarray(pts.intensity)[gl] / np.array([lut[int(s)] for s in src]) if len(src) else np.zeros(0)
                fin = np.isfinite(norm)
                np.add.at(isum, c[gl][fin], norm[fin].astype(np.float32))
                if rgb:
                    r, gg, b = (np.asarray(pts[k])[g].astype(np.float64) for k in ("red", "green", "blue"))
                    nir = np.asarray(pts["nir"])[g].astype(np.float64)
                    np.add.at(acc["bright"], c[g], ((r + gg + b) / 3.0 / 256.0).astype(np.float32))
                    np.add.at(acc["ndvi"], c[g], ((nir - r) / np.maximum(nir + r, 1.0)).astype(np.float32))
        log(f"  {t.name}: ground pass {time.time() - t0:.0f} s")
    with np.errstate(invalid="ignore", divide="ignore"):
        dtm = np.where(gn > 0, gsum / np.maximum(gn, 1), np.nan)
    nlast = np.zeros(N, np.int32)
    # count of ground last returns (for the intensity mean)
    dtm2 = dtm.reshape(ny, nx)
    holes = np.isnan(dtm2)
    if holes.any():
        idx = ndimage.distance_transform_edt(holes, return_distances=False, return_indices=True)
        dtm2 = dtm2[tuple(idx)]
    dtm = dtm2.ravel()
    # pass 2: heights above the ground (vegetation, low objects) and the ground last-return count
    tot = np.zeros(N, np.int32)
    veg = np.zeros(N, np.int32)
    low = np.zeros(N, np.int32)
    for t in tiles:
        t0 = time.time()
        with laspy.open(t) as f:
            for pts in f.chunk_iterator(CHUNK):
                x, y, z = np.asarray(pts.x), np.asarray(pts.y), np.asarray(pts.z)
                c, ok = _cells(x, y, x0, y0, res, nx, ny)
                cls = np.asarray(pts.classification)
                keep = ok & (cls != 7) & (cls != 18)
                cc = c[keep]
                h = z[keep] - dtm[cc]
                k = cls[keep]
                np.add.at(tot, cc, 1)
                np.add.at(veg, cc[np.isin(k, (3, 4, 5)) & (h > 2.0)], 1)
                np.add.at(low, cc[~np.isin(k, GROUND) & (h > 0.3) & (h < 2.5)], 1)
                last = (np.asarray(pts.return_number) == np.asarray(pts.number_of_returns))[keep]
                src = np.asarray(pts.point_source_id)[keep]
                has = np.isin(src, list(med.keys()))
                np.add.at(nlast, cc[np.isin(k, GROUND) & last & has], 1)
        log(f"  {t.name}: object pass {time.time() - t0:.0f} s")
    dsm = np.where(np.isfinite(dsm), dsm, dtm).astype(np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = {"dtm": dtm.reshape(ny, nx).astype(np.float32), "dsm": dsm.reshape(ny, nx),
               "veg": np.where(tot > 0, veg / np.maximum(tot, 1), 0).reshape(ny, nx).astype(np.float32),
               "low": np.where(tot > 0, low / np.maximum(tot, 1), 0).reshape(ny, nx).astype(np.float32),
               "inten": np.where(nlast > 0, isum / np.maximum(nlast, 1), np.nan).reshape(ny, nx).astype(np.float32),
               "ground_n": gn.reshape(ny, nx).astype(np.int16), "tot": tot.reshape(ny, nx).astype(np.int16)}
        for k, v in acc.items():
            out[k] = np.where(gn > 0, v / np.maximum(gn, 1), np.nan).reshape(ny, nx).astype(np.float32)
    out["chm"] = np.clip(out["dsm"] - out["dtm"], 0, 60).astype(np.float32)
    log("  classes: " + ", ".join(f"{k}:{v}" for k, v in enumerate(hist) if v))
    return out


def wales(slug: str, geo, log) -> dict[str, np.ndarray]:
    """DataMapWales 1 m DTM and DSM (COG range reads, cog.py) on the raster grid."""
    from cog import RemoteCog  # noqa: PLC0415

    x0, y0, res, nx, ny = geo
    out = {}
    for key in ("dtm", "dsm"):
        cog = RemoteCog(WALES_COG.format(key), str(LOCAL / "realtracks" / slug / "cog_cache"))
        arr, xc, yc, r = cog.read_bbox(x0, y0, x0 + nx * res, y0 + ny * res)
        # north-up rows from (xc, yc) at 1 m -> our south-up grid of cell centres x0 + (i + 0.5) res
        xs = x0 + (np.arange(nx) + 0.5) * res
        ys = y0 + (np.arange(ny) + 0.5) * res
        ci = np.clip(np.round((xs - xc) / r).astype(int), 0, arr.shape[1] - 1)
        ri = np.clip(np.round((yc - ys) / r).astype(int), 0, arr.shape[0] - 1)
        out[key] = arr[np.ix_(ri, ci)].astype(np.float32)
        log(f"  {key}: {cog.requests} range requests, {np.isnan(out[key]).mean():.0%} no data (sea)")
    out["chm"] = np.clip(np.nan_to_num(out["dsm"] - out["dtm"]), 0, 60).astype(np.float32)
    return out


WALES_COG = "https://dmwproductionblob.blob.core.windows.net/cogs/lidar/wales_{}_32bit_cog.tif"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--res", type=float, default=1.0)
    ap.add_argument("--margin", type=float, default=450.0)
    a = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    x0, y0, nx, ny = box(a.slug, a.margin, a.res)
    geo = (x0, y0, a.res, nx, ny)
    log(f"{a.slug}: {nx} x {ny} cells of {a.res} m from ({x0:.0f}, {y0:.0f})")
    region = REGION.get(a.slug, "england")
    if region == "wales":
        out = wales(a.slug, geo, log)
    else:
        tiles = sorted((LOCAL / "realtracks" / a.slug / "pointcloud").glob("*.laz"))
        med = source_medians(tiles, geo, log)
        out = rasterise(tiles, geo, med, region == "navarra", log)
    d = LOCAL / "realtracks" / a.slug / "runoff"
    d.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(d / "raster.npz", x0=x0, y0=y0, res=a.res, **out)
    (d / "raster.json").write_text(json.dumps({"x0": x0, "y0": y0, "res": a.res, "nx": nx, "ny": ny,
                                               "layers": sorted(out)}, indent=1), encoding="utf-8")
    log(f"wrote {d / 'raster.npz'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
