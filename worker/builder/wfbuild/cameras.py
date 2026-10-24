"""The layout's start camera: the view the game shows while a race loads in, before the menu (pure numpy).

Wreckfest 1 takes it from the layout's track data (``.tcat``, wfbuild/tcat.py): the camera flagged "start
camera". Without one the game looks from a default spot, which is under the ground on our tracks (Chris,
2026-10-23: every generated track; the scene origin lies 16 m under Oulton Park's terrain). 103 of the 112 stock
files with decodable cameras have exactly one. Measured on them (camera vs the compiled layout,
2026-10-23): a fixed camera 2-10 m above the ground (median ~4.5 m; a few overviews at 45-140 m), 4-20 m
beside the road, 10-50 m from the pole, looking back along the grid (its yaw within ~15 degrees of the bearing
to the pole), pitched 5-25 degrees down, field of view ~58.

``start_camera`` puts ours ahead of the pole along the layout's own lap (its ``ai_path``), beside the road and
past the run-off, ``HEIGHT_M`` above the terrain, looking at the front of the grid. A spot is used only when it
is off every road, on dry land inside the terrain, clear of every prop (and of the bridge and jump no-go zones),
and sees the target over the terrain and past the tall props; otherwise the next one is tried, ending with a
higher fallback. Everything is in TrackModel coordinates (the builder moves it with the rest of the layout).
"""
from __future__ import annotations

import re

import numpy as np

from . import envs

HEIGHT_M = 6.0                       # above the terrain (stock start cameras: median ~4.5 m)
MIN_ABOVE_POLE_M = 3.0               # and at least this far above the pole's road
ALONG_M = (35.0, 45.0, 28.0, 55.0, 65.0, 80.0)   # ahead of the pole along the lap
BEYOND_EDGE_M = (9.0, 13.0, 6.0, 18.0, 24.0)     # beyond the road edge
PROP_CLEAR_M = 2.0                   # clearance around a prop's footprint
TALL = ("trees", "buildings", "grandstand", "paddock", "lights", "cameras", "gantry", "foliage",
        "pitBuildings", "pitPaddock", "bridges", "pitWall", "realGrandstand")  # pitWall etc.: real pits (pits.py)
FALLBACK_HEIGHT_M = 15.0
FOV_DEG = 58.0
TARGET_LIFT_M = 1.0                  # look at the cars, not the tarmac


def _grid(lay) -> list:
    sp = [e for e in lay.empties if re.fullmatch(r"#startpoint\d+", e.name)]
    return sorted(sp, key=lambda e: int(e.name[len("#startpoint"):]))


def _path(lay) -> np.ndarray:
    c = next(c for c in lay.curves if c.name == "ai_path")
    return np.array(c.points, dtype=np.float64)


def _walk(path: np.ndarray, i0: int, dist: float) -> tuple[np.ndarray, np.ndarray]:
    """Point and unit tangent ``dist`` metres along the (closed) path from vertex i0."""
    n = len(path)
    left, i = dist, i0
    for _ in range(n):
        a, b = path[i % n], path[(i + 1) % n]
        seg = float(np.hypot(*(b[:2] - a[:2])))
        if seg > 1e-9 and left <= seg:
            t = (b[:2] - a[:2]) / seg
            return a + (b - a) * (left / seg), t
        left -= seg
        i += 1
    a, b = path[-2], path[-1]
    t = (b[:2] - a[:2]) / max(float(np.hypot(*(b[:2] - a[:2]))), 1e-9)
    return path[-1].copy(), t


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(float(ab @ ab), 1e-9), 0.0, 1.0)
    return np.hypot(*(p - (a + t[:, None] * ab)).T)


class _Props:
    def __init__(self, placements):
        self.xy = np.array([(p.x, p.y) for p in placements], dtype=np.float64).reshape(-1, 2)
        info = [envs.asset_info(p.asset) for p in placements]
        self.r = np.array([float(i["r"]) * p.scale for i, p in zip(info, placements)], dtype=np.float64)
        self.top = np.array([p.z + float(i.get("h", 2.0)) * p.scale for i, p in zip(info, placements)],
                            dtype=np.float64)
        self.tall = np.array([p.category in TALL for p in placements], dtype=bool)

    def clear(self, q: np.ndarray, eye_z: float) -> bool:
        if not len(self.xy):
            return True
        d = np.hypot(*(self.xy - q).T) - self.r
        return not bool(np.any((d < PROP_CLEAR_M) & (self.top > eye_z - 4.0)))

    def blocks(self, eye: np.ndarray, target: np.ndarray) -> bool:
        """A tall prop's footprint on the sight line where the line is below the prop's top."""
        sel = np.nonzero(self.tall)[0]
        if not len(sel):
            return False
        xy, r, top = self.xy[sel], self.r[sel], self.top[sel]
        d = _seg_dist(xy, eye[:2], target[:2])
        L = max(float(np.hypot(*(target[:2] - eye[:2]))), 1e-9)
        t = np.clip(((xy - eye[:2]) @ (target[:2] - eye[:2])) / (L * L), 0.0, 1.0)
        z = eye[2] + t * (target[2] - eye[2])
        return bool(np.any((d < r + 0.5) & (top > z) & (t > 0.02) & (t < 0.98)))


def start_camera(geo, lay, field, planner, placements) -> tuple[np.ndarray, np.ndarray, str]:
    """(eye, target, note) of the layout's start camera, TrackModel coordinates (x, y, z up)."""
    grid = _grid(lay)
    path = _path(lay)
    pole = np.array(grid[0].location, dtype=np.float64)
    back = np.array(grid[-1].location, dtype=np.float64)
    target = pole + (back - pole) * 0.1
    target[2] += TARGET_LIFT_M
    i0 = int(np.argmin(np.hypot(*(path[:, :2] - pole[:2]).T)))
    hw = float(np.median(geo.index.hw))
    props = _Props(placements)
    x0, x1, y0, y1 = geo.extent

    def terrain(xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        return geo.terrain(np.column_stack([xy, np.zeros(len(xy))]))

    def sees(eye: np.ndarray) -> bool:
        t = np.linspace(0.04, 0.96, 40)
        line = eye[None, :] + t[:, None] * (target - eye)[None, :]
        if np.any(terrain(line[:, :2]) + 0.3 > line[:, 2]):
            return False
        return not props.blocks(eye, target)

    for along in ALONG_M:
        c, tg = _walk(path, i0, along)
        nl = np.array([-tg[1], tg[0]])
        for beyond in BEYOND_EDGE_M:
            for side in (1.0, -1.0):
                q = c[:2] + nl * side * (hw + beyond)
                if not (x0 + 20 <= q[0] <= x1 - 20 and y0 + 20 <= q[1] <= y1 - 20):
                    continue
                if float(field.dist(q)[0]) < beyond - 1.5:  # another road (or this one) nearer than intended
                    continue
                if not planner.dry(float(q[0]), float(q[1])):
                    continue
                if planner.in_nogo(q, 1.0, True)[0]:  # bridges and jumps (the grid zone only keeps props out)
                    continue
                z = max(float(terrain(q)[0]) + HEIGHT_M, pole[2] + MIN_ABOVE_POLE_M)
                eye = np.array([q[0], q[1], z])
                if props.clear(q, z) and sees(eye):
                    return eye, target, f"{along:.0f} m ahead of the pole, {beyond:.0f} m beyond the edge"
    # fallback: straight above the road ahead of the pole, high enough to see over the props
    c, _ = _walk(path, i0, ALONG_M[0])
    eye = np.array([c[0], c[1], max(float(terrain(c[:2])[0]), c[2]) + FALLBACK_HEIGHT_M])
    return eye, target, "fallback above the road"

