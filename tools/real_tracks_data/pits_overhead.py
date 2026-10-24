"""Find structures over a circuit's roads in the LIDAR (nDSM): bridges, footbridges and gantries span the road, so
across the road's width most cells stand high above it, where an overhanging tree covers only part of it.

    python tools/real_tracks_data/pits_overhead.py <slug>   # prints the spans along every route
"""
from __future__ import annotations

import sys

import numpy as np

from pits_common import Circuit
from pits_lidar import ndsm


def route_normals(p: np.ndarray, closed: bool) -> tuple[np.ndarray, np.ndarray]:
    q = np.vstack([p[-1:], p, p[:1]]) if closed else np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    t = q[2:] - q[:-2]
    t = t / np.linalg.norm(t, axis=1)[:, None]
    return t, np.column_stack([-t[:, 1], t[:, 0]])


def whole_box(c: Circuit, margin: float = 30.0) -> tuple[float, float, float, float]:
    pts = np.vstack(c.all_routes())
    g0, g1 = c.grid_of_local(pts.min(0) - margin), c.grid_of_local(pts.max(0) + margin)
    return g0[0], g0[1], g1[0], g1[1]


def spans(c: Circuit, min_h: float = 3.0, min_cover: float = 0.75) -> list[dict]:
    """Runs along each route where at least ``min_cover`` of the road's width stands over ``min_h`` m above the
    ground (the DTM there is the road): the structure's s range, its clearance (lowest underside seen: the 10th
    percentile of the high cells) and top (90th percentile)."""
    nd, _ = ndsm(c, *whole_box(c))
    out = []
    routes = [("main", c.main, c.main_w, True)] + [(k, c.alts[k], c.alt_w[k], False) for k in c.alts]
    for rid, p, w, closed in routes:
        t, nl = route_normals(p, closed)
        seg = np.hypot(*np.diff(np.vstack([p, p[:1]]) if closed else p, axis=0).T)
        s = np.concatenate([[0.0], np.cumsum(seg)])[:len(p)]
        fr = np.linspace(-0.45, 0.45, 19)
        cover = np.zeros(len(p))
        lo = np.full(len(p), np.nan)
        hi = np.full(len(p), np.nan)
        for i in range(len(p)):
            q = p[i] + nl[i] * (fr[:, None] * w[i])
            g = c.grid_of_local(q)
            h = nd.sample(g[:, 0], g[:, 1])
            high = h[np.isfinite(h) & (h > min_h)]
            cover[i] = len(high) / len(h)
            if len(high):
                lo[i], hi[i] = np.percentile(high, 10), np.percentile(high, 90)
        on = cover >= min_cover
        k = 0
        n = len(p)
        while k < n:
            if not on[k]:
                k += 1
                continue
            j = k
            while j + 1 < n and on[j + 1]:
                j += 1
            out.append({"route": rid, "s0": round(float(s[k]), 1), "s1": round(float(s[j]), 1),
                        "lengthM": round(float(s[j] - s[k]) + 2.0, 1),
                        "clearanceM": round(float(np.nanmin(lo[k:j + 1])), 1),
                        "topM": round(float(np.nanmax(hi[k:j + 1])), 1),
                        "xy": [round(float(v), 1) for v in p[(k + j) // 2]]})
            k = j + 1
    return out


def high_cells(c: Circuit, min_h: float = 4.0, route: str = "main") -> np.ndarray:
    """Every nDSM cell over the road (inside its edges) standing over ``min_h``: rows (s, lateral / half-width, h)."""
    nd, _ = ndsm(c, *whole_box(c))
    p = c.main if route == "main" else c.alts[route]
    w = c.main_w if route == "main" else c.alt_w[route]
    closed = route == "main"
    rows, cols = np.nonzero(np.nan_to_num(nd.z, nan=0.0) > min_h)
    g = np.column_stack([nd.x0 + cols + 0.5, nd.y1 - rows - 0.5])
    xy = c.local_of_grid(g)
    h = nd.z[rows, cols]
    t, nl = route_normals(p, closed)
    seg = np.hypot(*np.diff(np.vstack([p, p[:1]]) if closed else p, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])[:len(p)]
    out = []
    for a in range(0, len(xy), 4000):
        q = xy[a:a + 4000]
        d = np.hypot(q[:, None, 0] - p[None, :, 0], q[:, None, 1] - p[None, :, 1])
        i = d.argmin(1)
        lat = ((q - p[i]) * nl[i]).sum(1)
        along = ((q - p[i]) * t[i]).sum(1)
        inside = (np.abs(lat) <= w[i] / 2.0) & (np.abs(along) < 1.5)
        out.append(np.column_stack([s[i] + along, lat / (w[i] / 2.0), h[a:a + 4000]])[inside])
    return np.vstack(out) if out else np.zeros((0, 3))


def bands(cells: np.ndarray, bin_m: float = 2.0, min_spread: float = 1.0) -> list[dict]:
    """Bins along s whose high cells spread across most of the road (lateral range >= min_spread of the full
    -1..1): spanning structures; overhanging trees reach in from one side."""
    if not len(cells):
        return []
    b = np.floor(cells[:, 0] / bin_m).astype(int)
    out = []
    for k in np.unique(b):
        m = cells[b == k]
        spread = float(m[:, 1].max() - m[:, 1].min())
        if spread >= min_spread and len(m) >= 3:
            out.append({"s": round(float(k * bin_m + bin_m / 2), 1), "n": int(len(m)), "spread": round(spread, 2),
                        "hMin": round(float(m[:, 2].min()), 1), "hMed": round(float(np.median(m[:, 2])), 1),
                        "hMax": round(float(m[:, 2].max()), 1)})
    return out


if __name__ == "__main__":
    c = Circuit(sys.argv[1])
    for sp in spans(c):
        print(sp)
