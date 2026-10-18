"""A small 2D polyline toolkit (numpy only).

Points are float64 arrays of shape ``(n, 2)``. A closed polyline does not repeat its first point;
its last segment joins the last point back to the first. Arc length ``s`` starts at point 0.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

MAX_POINTS = 100_000  # defensive cap on any resampling (a 5 km lap at 2 m is 2,500 points)


class GeometryError(ValueError):
    """The sketch can't be turned into geometry (e.g. a degenerate stroke). ``code`` identifies the
    problem for API clients."""

    def __init__(self, message: str, code: str = "geometry-error") -> None:
        super().__init__(message)
        self.code = code


def as_points(points: object) -> np.ndarray:
    return np.asarray(points, dtype=np.float64).reshape(-1, 2)


def dedupe(p: np.ndarray, closed: bool, eps: float = 1e-9) -> np.ndarray:
    """Drop consecutive duplicate points (and, if closed, a repeated first point at the end)."""
    if len(p) == 0:
        return p
    step = np.hypot(*np.diff(p, axis=0).T)
    q = p[np.concatenate([[True], step > eps])]
    if closed:
        while len(q) > 1 and math.hypot(*(q[-1] - q[0])) <= eps:
            q = q[:-1]
    return q


def segments(p: np.ndarray, closed: bool) -> tuple[np.ndarray, np.ndarray]:
    if closed:
        return p, np.roll(p, -1, axis=0)
    return p[:-1], p[1:]


def seg_lengths(p: np.ndarray, closed: bool) -> np.ndarray:
    a, b = segments(p, closed)
    d = b - a
    return np.hypot(d[:, 0], d[:, 1])


def cumulative(p: np.ndarray, closed: bool) -> np.ndarray:
    """Arc length at each point; for a closed polyline one extra entry holds the total length."""
    return np.concatenate([[0.0], np.cumsum(seg_lengths(p, closed))])


def length(p: np.ndarray, closed: bool) -> float:
    return float(seg_lengths(p, closed).sum())


def _interp(ext: np.ndarray, cum: np.ndarray, t: np.ndarray) -> np.ndarray:
    return np.column_stack([np.interp(t, cum, ext[:, 0]), np.interp(t, cum, ext[:, 1])])


def resample_closed(p: np.ndarray, n: int, start_s: float = 0.0) -> np.ndarray:
    """``n`` evenly spaced points along a closed polyline, the first at arc length ``start_s``."""
    n = min(n, MAX_POINTS)
    cum = cumulative(p, True)
    total = cum[-1]
    t = np.mod(start_s + np.arange(n) * (total / n), total)
    return _interp(np.vstack([p, p[:1]]), cum, t)


def resample_open(p: np.ndarray, n_seg: int) -> np.ndarray:
    """``n_seg + 1`` evenly spaced points along an open polyline; the end points are kept exactly."""
    n_seg = min(n_seg, MAX_POINTS)
    cum = cumulative(p, False)
    out = _interp(p, cum, np.linspace(0.0, cum[-1], n_seg + 1))
    out[0], out[-1] = p[0], p[-1]
    return out


def gaussian_smooth(p: np.ndarray, closed: bool, sigma: float, spacing: float) -> np.ndarray:
    """Resample at about ``spacing`` and convolve with a Gaussian of ``sigma`` (same units).

    Closed polylines wrap around; open ones are padded by point reflection, which keeps the end
    points and the end tangents' trend. A low-pass filter that removes hand-drawn jitter.
    """
    total = length(p, closed)
    n = min(max(8, round(total / spacing)), MAX_POINTS)
    q = resample_closed(p, n) if closed else resample_open(p, n)
    h = total / n
    k = min(int(math.ceil(3.0 * sigma / h)), len(q) - 1)
    if sigma <= 0 or k < 1:
        return q
    x = np.arange(-k, k + 1) * h
    w = np.exp(-0.5 * (x / sigma) ** 2)
    w /= w.sum()
    if closed:
        ext = np.vstack([q[-k:], q, q[:k]])
    else:
        ext = np.vstack([2.0 * q[0] - q[k:0:-1], q, 2.0 * q[-1] - q[-2 : -k - 2 : -1]])
    out = np.column_stack([np.convolve(ext[:, 0], w, "valid"), np.convolve(ext[:, 1], w, "valid")])
    if not closed:
        out[0], out[-1] = q[0], q[-1]
    return out


def subdivide_closed(c: np.ndarray, levels: int) -> np.ndarray:
    """Cubic B-spline subdivision (Lane-Riesenfeld) of a closed control polygon."""
    for _ in range(levels):
        prev, nxt = np.roll(c, 1, axis=0), np.roll(c, -1, axis=0)
        out = np.empty((2 * len(c), 2))
        out[0::2] = (prev + 6.0 * c + nxt) / 8.0
        out[1::2] = (c + nxt) / 2.0
        c = out
    return c


def subdivide_open(c: np.ndarray, levels: int) -> np.ndarray:
    """Cubic B-spline subdivision of an open control polygon with fixed end points."""
    for _ in range(levels):
        v = c.copy()
        v[1:-1] = (c[:-2] + 6.0 * c[1:-1] + c[2:]) / 8.0
        out = np.empty((2 * len(c) - 1, 2))
        out[0::2] = v
        out[1::2] = (c[:-1] + c[1:]) / 2.0
        c = out
    return c


def tangents(p: np.ndarray, closed: bool) -> np.ndarray:
    if closed:
        d = np.roll(p, -1, axis=0) - np.roll(p, 1, axis=0)
    else:
        d = np.empty_like(p)
        d[1:-1] = p[2:] - p[:-2]
        d[0] = p[1] - p[0]
        d[-1] = p[-1] - p[-2]
    n = np.hypot(d[:, 0], d[:, 1])
    return d / np.where(n > 0, n, 1.0)[:, None]


def headings(p: np.ndarray, closed: bool) -> np.ndarray:
    """Tangent direction at each point, radians counter-clockwise from +x."""
    t = tangents(p, closed)
    return np.arctan2(t[:, 1], t[:, 0])


def signed_curvature(p: np.ndarray, closed: bool, w: int) -> np.ndarray:
    """Curvature from the circle through points i-w, i, i+w (positive = turning left). Open
    polylines get 0 within ``w`` points of their ends."""
    n = len(p)
    k = np.zeros(n)
    if closed:
        if n < 3:
            return k
        w = max(1, min(w, (n - 1) // 2))
        a, b, c = np.roll(p, w, axis=0), p, np.roll(p, -w, axis=0)
        sl = slice(0, n)
    else:
        if n <= 2 * w:
            return k
        a, b, c = p[: n - 2 * w], p[w : n - w], p[2 * w :]
        sl = slice(w, n - w)
    ab, ac, bc = b - a, c - a, c - b
    cross = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
    denom = np.hypot(*ab.T) * np.hypot(*bc.T) * np.hypot(*ac.T)
    k[sl] = np.where(denom > 0, 2.0 * cross / np.where(denom > 0, denom, 1.0), 0.0)
    return k


def radii(p: np.ndarray, closed: bool, w: int) -> np.ndarray:
    k = np.abs(signed_curvature(p, closed, w))
    with np.errstate(divide="ignore"):
        return np.where(k > 0, 1.0 / np.where(k > 0, k, 1.0), np.inf)


@dataclass(frozen=True)
class Projection:
    s: float
    point: np.ndarray
    distance: float


def project(p: np.ndarray, closed: bool, q: np.ndarray) -> Projection:
    """Nearest point on the polyline to ``q`` (ties go to the lowest segment index)."""
    a, b = segments(p, closed)
    d = b - a
    l2 = (d * d).sum(axis=1)
    t = np.clip(((q - a) * d).sum(axis=1) / np.where(l2 > 0, l2, 1.0), 0.0, 1.0)
    foot = a + t[:, None] * d
    dist = np.hypot(*(foot - q).T)
    i = int(np.argmin(dist))
    cum = cumulative(p, closed)
    s = float(cum[i] + t[i] * math.sqrt(l2[i]))
    if closed and s >= cum[-1]:
        s -= float(cum[-1])
    return Projection(s, foot[i].copy(), float(dist[i]))


def interp_at(cum: np.ndarray, values: np.ndarray, closed: bool, s: np.ndarray | float) -> np.ndarray:
    """Linear interpolation of per-point ``values`` (1D or 2D) at arc lengths ``s``."""
    s = np.asarray(s, dtype=np.float64)
    if closed:
        s = np.mod(s, cum[-1])
        values = np.concatenate([values, values[:1]])
    else:
        s = np.clip(s, 0.0, cum[-1])
    if values.ndim == 1:
        return np.interp(s, cum, values)
    return np.stack([np.interp(s, cum, values[:, j]) for j in range(values.shape[1])], axis=-1)


@dataclass(frozen=True)
class Intersection:
    route_a: int
    seg_a: int
    t_a: float
    route_b: int
    seg_b: int
    t_b: float
    point: tuple[float, float]


def intersections(polys: list[tuple[np.ndarray, bool]], cell: float, min_gap: int) -> list[Intersection]:
    """Proper segment intersections within and between polylines, found with a uniform spatial hash.

    Segments of the same polyline closer than ``min_gap`` in index (cyclically if closed) are skipped.
    Each segment is half-open, so an intersection exactly at a shared vertex is reported once.
    """
    segs: list[tuple[list[tuple[float, float]], list[tuple[float, float]], bool]] = []
    buckets: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for r, (p, closed) in enumerate(polys):
        a, b = segments(p, closed)
        segs.append((a.tolist(), b.tolist(), closed))
        lo = np.floor(np.minimum(a, b) / cell).astype(int).tolist()
        hi = np.floor(np.maximum(a, b) / cell).astype(int).tolist()
        for i, ((x0, y0), (x1, y1)) in enumerate(zip(lo, hi, strict=True)):
            for cx in range(x0, x1 + 1):
                for cy in range(y0, y1 + 1):
                    buckets[(cx, cy)].append((r, i))

    seen: set[tuple[int, int, int, int]] = set()
    hits: list[Intersection] = []
    for key in sorted(buckets):
        items = buckets[key]
        for x in range(len(items)):
            for y in range(x + 1, len(items)):
                (r1, i1), (r2, i2) = sorted((items[x], items[y]))
                pair = (r1, i1, r2, i2)
                if pair in seen:
                    continue
                seen.add(pair)
                if r1 == r2:
                    n = len(segs[r1][0])
                    gap = abs(i1 - i2)
                    if segs[r1][2]:
                        gap = min(gap, n - gap)
                    if gap < min_gap:
                        continue
                (px, py), (bx, by) = segs[r1][0][i1], segs[r1][1][i1]
                (qx, qy), (dx, dy) = segs[r2][0][i2], segs[r2][1][i2]
                rx, ry, sx, sy = bx - px, by - py, dx - qx, dy - qy
                denom = rx * sy - ry * sx
                if abs(denom) < 1e-12:
                    continue
                wx, wy = qx - px, qy - py
                t = (wx * sy - wy * sx) / denom
                u = (wx * ry - wy * rx) / denom
                if 0.0 <= t < 1.0 and 0.0 <= u < 1.0:
                    hits.append(Intersection(r1, i1, t, r2, i2, u, (px + t * rx, py + t * ry)))
    hits.sort(key=lambda h: (h.route_a, h.seg_a, h.t_a, h.route_b, h.seg_b, h.t_b))
    return hits
