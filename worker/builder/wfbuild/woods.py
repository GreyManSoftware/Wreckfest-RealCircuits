"""Woodland cover from polygons (an imported track's OSM woods), as a smooth field over the map frame (pure numpy).

The scenery planner plants its trees where the cover is high and the generated maps put forest floor and canopy
shade there, so trees and ground agree (as ``Noise2D`` does for the seeded clusters of drawn tracks).
"""
from __future__ import annotations

import math

import numpy as np


class WoodMask:
    """Polygons rasterised on a ``cell`` grid over the square frame (x0, y0, size), then softened with a box blur
    of half-width ``soft`` metres (two passes): ``at`` is ~0 well outside a wood, ~0.5 on its edge and ~1 more
    than ``soft`` metres inside it."""

    def __init__(self, polygons: list[np.ndarray], frame: tuple[float, float, float], cell: float = 2.0,
                 soft: float = 10.0, weights: list[float] | None = None):
        self.frame = frame
        x0, y0, size = frame
        n = max(2, int(math.ceil(size / cell)))
        self.res, self.cell = n, size / n
        c = (np.arange(n) + 0.5) * self.cell
        cov = np.zeros((n, n))  # rows = y
        for pk, poly in enumerate(polygons):
            wv = 1.0 if weights is None else float(weights[pk])  # measured cover per polygon (else 1)
            p = np.asarray(poly, dtype=np.float64)[:, :2]
            if len(p) < 3:
                continue
            q = np.roll(p, -1, axis=0)
            ys = y0 + c
            lo, hi = np.searchsorted(ys, p[:, 1].min()), np.searchsorted(ys, p[:, 1].max())
            for j in range(max(lo, 0), min(hi, n)):
                y = ys[j]
                cross = (p[:, 1] > y) != (q[:, 1] > y)
                if not cross.any():
                    continue
                a, b = p[cross], q[cross]
                xs = np.sort(a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1]))
                for xa, xb in zip(xs[0::2], xs[1::2], strict=False):
                    i0 = max(0, int(math.ceil((xa - x0) / self.cell - 0.5)))
                    i1 = min(n - 1, int(math.floor((xb - x0) / self.cell - 0.5)))
                    if i1 >= i0:
                        cov[j, i0:i1 + 1] = wv
        r = max(1, int(round(soft / self.cell)))
        g = cov
        for _ in range(2):
            g = _box(_box(g, r, 0), r, 1)
        self.g = np.clip(g, 0.0, 1.0)  # a straight edge (half the window covered) reads 0.5
        self.cover = cov

    def at(self, xy) -> np.ndarray:
        x0, y0, _ = self.frame
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        fx = np.clip((xy[:, 0] - x0) / self.cell - 0.5, 0, self.res - 1.001)
        fy = np.clip((xy[:, 1] - y0) / self.cell - 0.5, 0, self.res - 1.001)
        i, j = np.floor(fx).astype(int), np.floor(fy).astype(int)
        u, v = fx - i, fy - j
        g = self.g
        return (g[j, i] * (1 - u) + g[j, i + 1] * u) * (1 - v) + (g[j + 1, i] * (1 - u) + g[j + 1, i + 1] * u) * v

    def grid(self, n: int) -> np.ndarray:
        x0, y0, size = self.frame
        c = (np.arange(n) + 0.5) / n * size
        gx, gy = np.meshgrid(x0 + c, y0 + c)
        return self.at(np.stack([gx.ravel(), gy.ravel()], axis=1)).reshape(n, n)


def _box(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """Mean over a window of 2r + 1 cells along ``axis`` (edges padded with the edge value)."""
    pad = [(0, 0), (0, 0)]
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis)
    hi = np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
    lo = np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)
    return (hi - lo) / (2 * r + 1)
