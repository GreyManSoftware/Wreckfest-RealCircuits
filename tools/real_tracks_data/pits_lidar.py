"""Heights of things above the ground (nDSM = DSM - DTM, 1 m cells) around a circuit's pits, for pits.py: building
heights and footprints, bridges and gantries over the track.

- England and Navarre: the DSM is the highest return per cell from the cached open LIDAR point cloud
  (``.local\\realtracks\\<slug>\\pointcloud\\*.laz``: EA National LIDAR Programme, OGL v3; Gobierno de Navarra 2024,
  CC BY 4.0), classes 7 and 18 (noise) left out; the DTM is the importer's (EA 1 m composite, IDENA 2 m).
- Wales: the Welsh Government / NRW LIDAR composite DSM 1 m (OGL v3, DataMapWales COG, range requests cached).

The result is cached as ``.local\\realtracks\\<slug>\\ndsm-<e0>_<n0>_<e1>_<n1>.npz`` (grid metres, north-up).
Needs numpy and laspy[lazrs] (the widths venv).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from pits_common import LOCAL, Circuit, irt

WALES_DSM_COG = "https://dmwproductionblob.blob.core.windows.net/cogs/lidar/wales_dsm_32bit_cog.tif"


class Raster:
    """North-up grid: z[row, col], cell centres at (x0 + (col + .5) c, y1 - (row + .5) c), grid metres."""

    def __init__(self, x0: float, y1: float, cell: float, z: np.ndarray):
        self.x0, self.y1, self.cell, self.z = x0, y1, cell, z

    def sample(self, e, n) -> np.ndarray:
        c = np.floor((np.asarray(e) - self.x0) / self.cell).astype(int)
        r = np.floor((self.y1 - np.asarray(n)) / self.cell).astype(int)
        h, w = self.z.shape
        ok = (c >= 0) & (r >= 0) & (c < w) & (r < h)
        return np.where(ok, self.z[np.clip(r, 0, h - 1), np.clip(c, 0, w - 1)], np.nan)


def _dsm_from_laz(c: Circuit, e0: int, n0: int, e1: int, n1: int) -> np.ndarray:
    import laspy  # noqa: PLC0415

    w, h = e1 - e0, n1 - n0
    dsm = np.full(h * w, -np.inf)
    found = False
    for laz in sorted((LOCAL / "realtracks" / c.slug / "pointcloud").glob("*.laz")):
        with laspy.open(str(laz)) as f:
            hd = f.header
            if hd.maxs[0] < e0 or hd.mins[0] > e1 or hd.maxs[1] < n0 or hd.mins[1] > n1:
                continue
            found = True
            for pts in f.chunk_iterator(5_000_000):
                sc, of = hd.scales, hd.offsets
                x = np.asarray(pts.X, dtype=np.float64) * sc[0] + of[0]
                y = np.asarray(pts.Y, dtype=np.float64) * sc[1] + of[1]
                sel = (x >= e0) & (x < e1) & (y >= n0) & (y < n1)
                if not sel.any():
                    continue
                cls = np.asarray(pts.classification)[sel]
                keep = (cls != 7) & (cls != 18)
                z = np.asarray(pts.Z, dtype=np.float64) * sc[2] + of[2]
                xs, ys, zs = x[sel][keep], y[sel][keep], z[sel][keep]
                col = (xs - e0).astype(np.int64)
                row = (h - 1 - np.floor(ys - n0)).astype(np.int64)
                np.maximum.at(dsm, row * w + col, zs)
    if not found:
        raise FileNotFoundError(f"{c.slug}: no point-cloud tile covers {e0},{n0}-{e1},{n1}")
    dsm = dsm.reshape(h, w)
    dsm[~np.isfinite(dsm)] = np.nan
    # ~2 points/m2: fill empty cells from their neighbours (median of the 3 x 3 block, twice)
    for _ in range(2):
        holes = np.isnan(dsm)
        if not holes.any():
            break
        pad = np.pad(dsm, 1, constant_values=np.nan)
        stack = np.stack([pad[1 + dy:1 + dy + h, 1 + dx:1 + dx + w] for dy in (-1, 0, 1) for dx in (-1, 0, 1)])
        with np.errstate(all="ignore"):
            import warnings  # noqa: PLC0415
            warnings.simplefilter("ignore", RuntimeWarning)
            fill = np.nanmedian(stack, axis=0)
        dsm[holes] = fill[holes]
    return dsm


def _dsm_wales(c: Circuit, e0: int, n0: int, e1: int, n1: int) -> np.ndarray:
    import sys  # noqa: PLC0415
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cog import RemoteCog  # noqa: PLC0415

    cog = RemoteCog(WALES_DSM_COG, str(c.cache / "cog_cache_dsm"))
    z, xc, yc, res = cog.read_bbox(e0, n0, e1, n1)
    if abs(res - 1.0) > 1e-9:
        raise RuntimeError(f"unexpected DSM resolution {res}")
    x0, y1 = xc - 0.5, yc + 0.5
    r0, c0 = int(round(y1 - n1)), int(round(e0 - x0))
    return np.asarray(z, dtype=np.float64)[r0:r0 + (n1 - n0), c0:c0 + (e1 - e0)]


def ndsm(c: Circuit, e0: float, n0: float, e1: float, n1: float) -> tuple[Raster, Raster]:
    """(nDSM, DTM) over the box (grid metres, rounded out to whole metres), 1 m cells."""
    e0, n0, e1, n1 = math.floor(e0), math.floor(n0), math.ceil(e1), math.ceil(n1)
    path = LOCAL / "realtracks" / c.slug / f"ndsm-{e0}_{n0}_{e1}_{n1}.npz"
    if path.exists():
        d = np.load(path)
        return Raster(e0, n1, 1.0, d["ndsm"]), Raster(e0, n1, 1.0, d["dtm"])
    dsm = _dsm_wales(c, e0, n0, e1, n1) if c.recipe.get("dtm") == "wales" else _dsm_from_laz(c, e0, n0, e1, n1)
    dtm = irt.load_dtm(c.recipe, e0 - 4, n0 - 4, e1 + 4, n1 + 4, c.cache)
    gx, gy = np.meshgrid(e0 + 0.5 + np.arange(e1 - e0), n1 - 0.5 - np.arange(n1 - n0))
    ground = dtm.sample(gx.ravel(), gy.ravel()).reshape(gx.shape)
    nd = dsm - ground
    # a point cloud in ellipsoidal heights over an orthometric DTM (Navarre: ~50 m of geoid) reads as everything
    # floating: most cells are open ground, so their median offset is the datum difference
    off = float(np.nanmedian(nd))
    if abs(off) > 1.0:
        nd = nd - off
    np.savez_compressed(path, ndsm=nd.astype(np.float32), dtm=ground.astype(np.float32))
    return Raster(e0, n1, 1.0, nd), Raster(e0, n1, 1.0, ground)
