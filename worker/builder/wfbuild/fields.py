"""Distance-to-road field over the terrain (pure numpy): the basis for the generated maps and for placing scenery.

``DistanceField(geo)`` rasterises, at about 2 m per pixel over the terrain extent, the distance from each pixel
to the nearest road edge (negative on the road) and which route sample is nearest. Each route sample stamps a
disc into the grid, so the cost is samples x disc area rather than pixels x samples.
"""
from __future__ import annotations

import math

import numpy as np

from . import config as C


class DistanceField:
    def __init__(self, geo, cap: float = C.FIELD_CAP, px_min: float = C.FIELD_PX_MIN):
        x0, x1, y0, y1 = geo.extent
        size = max(x1 - x0, y1 - y0)
        self.px = max(px_min, size / 1024.0)
        self.x0, self.y0 = x0, y0
        self.nx = int(math.ceil((x1 - x0) / self.px)) + 1
        self.ny = int(math.ceil((y1 - y0) / self.px)) + 1
        self.cap = cap
        sel = np.concatenate([geo.svalid[r.index] for r in geo.routes])
        self.xy = geo.index.xy[sel]
        self.hw = geo.index.hw[sel]
        self.ri = geo.index.ri[sel]
        self.si = geo.index.si[sel]
        d = np.full((self.ny, self.nx), cap, dtype=np.float32)
        near = np.full((self.ny, self.nx), -1, dtype=np.int32)
        xs = self.x0 + np.arange(self.nx) * self.px
        ys = self.y0 + np.arange(self.ny) * self.px
        for k in range(len(self.xy)):
            cx, cy = self.xy[k]
            rad = cap + self.hw[k]
            i0 = max(0, int((cx - rad - self.x0) / self.px))
            i1 = min(self.nx, int((cx + rad - self.x0) / self.px) + 2)
            j0 = max(0, int((cy - rad - self.y0) / self.px))
            j1 = min(self.ny, int((cy + rad - self.y0) / self.px) + 2)
            if i0 >= i1 or j0 >= j1:
                continue
            dd = np.hypot(xs[None, i0:i1] - cx, ys[j0:j1, None] - cy) - self.hw[k]
            win = d[j0:j1, i0:i1]
            m = dd < win
            win[m] = dd[m]
            near[j0:j1, i0:i1][m] = k
        self.d = d
        self.near = near

    def _ij(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        fx = np.clip((xy[:, 0] - self.x0) / self.px, 0, self.nx - 1.001)
        fy = np.clip((xy[:, 1] - self.y0) / self.px, 0, self.ny - 1.001)
        i, j = np.floor(fx).astype(int), np.floor(fy).astype(int)
        return i, j, fx - i, fy - j

    def dist(self, xy) -> np.ndarray:
        """Bilinear distance to the nearest road edge (metres, negative on the road, capped)."""
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        i, j, u, v = self._ij(xy)
        d = self.d
        return ((d[j, i] * (1 - u) + d[j, i + 1] * u) * (1 - v) + (d[j + 1, i] * (1 - u) + d[j + 1, i + 1] * u) * v)

    def nearest(self, xy) -> np.ndarray:
        """Index (into this field's sample arrays) of the nearest route sample, -1 beyond the cap."""
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        i, j, u, v = self._ij(xy)
        return self.near[j + (v > 0.5), i + (u > 0.5)]

    def resample(self, n: int, frame: tuple[float, float, float]) -> np.ndarray:
        """The field on an n x n grid over a square frame (x0, y0, size), row 0 at y0 (pixel centres)."""
        x0, y0, size = frame
        c = (np.arange(n) + 0.5) / n * size
        gx, gy = np.meshgrid(x0 + c, y0 + c)
        return self.dist(np.stack([gx.ravel(), gy.ravel()], axis=1)).reshape(n, n)


def exact_edge_distance(geo, pts: np.ndarray) -> np.ndarray:
    """Brute-force distance from points to the nearest valid road sample's edge (for tests and spot checks)."""
    sel = np.concatenate([geo.svalid[r.index] for r in geo.routes])
    xy, hw = geo.index.xy[sel], geo.index.hw[sel]
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    out = np.empty(len(pts))
    for a in range(0, len(pts), 256):
        p = pts[a:a + 256]
        out[a:a + 256] = (np.hypot(p[:, None, 0] - xy[None, :, 0], p[:, None, 1] - xy[None, :, 1]) - hw[None, :]).min(1)
    return out
