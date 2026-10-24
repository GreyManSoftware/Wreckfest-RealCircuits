"""Track cameras for a layout's track data (``.tcat``, wfbuild/tcat.py): a start camera and trackside cameras
(pure numpy).

Wreckfest 1 reads every camera from the layout's ``.tcat`` (BagEdit's bbTrackCamera v11; flag bits from its enum
table: 2 "start camera", 4 "sector activation"). Stock layouts carry, besides one fixed start camera, a set of
trackside cameras: rotating (they turn to follow the target) or moving, each active between two sectors of the
compiled main AI route, together covering the lap (crm01_1: 17, sandpit01_1: 15; 1162 cameras over 118 stock
files). The race intro ("WELCOME TO ..." while the race loads in) and replays cut between those; a layout with
none leaves the game at its default camera, the world origin: under the ground on every generated circuit
(Chris, 2026-10-23 and 2026-10-24, with a start camera alone already in the ``.tcat``).

Measured on stock (camera vs the stock base terrain with each model's matrix applied, 2026-10-24): start cameras
2-10 m above the ground near the grid (sandpit01_1 4.2 m, forest01_1 10.4 m, crm01_1 3.1 m), trackside cameras
2-15 m above the ground 5-40 m beside the road.

``start_camera`` puts the start camera ahead of the pole along the layout's own lap (its ``ai_path``), beside the
road and past the run-off, ``HEIGHT_M`` above the terrain, looking at the front of the grid. ``trackside_cameras``
splits the lap into ranges of the compiled main route's sectors (one per ~SPACING_M) and puts a rotating camera
beside the road near each range's middle. A spot is used only when it is off every road, on dry land inside the
terrain, clear of every prop (and of the bridge and jump no-go zones), and sees its target over the terrain and
past the tall props; otherwise the next one is tried, ending with a higher fallback. Everything is in TrackModel
coordinates (the builder moves it with the rest of the layout).
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
TALL = ("trees", "buildings", "grandstand", "paddock", "lights", "cameras", "gantry", "foliage")
FALLBACK_HEIGHT_M = 15.0
FOV_DEG = 58.0
TARGET_LIFT_M = 1.0                  # look at the cars, not the tarmac
SPACING_M = 160.0                    # trackside cameras: one per this much of the lap (stock ~150-250 m)
SIDE_HEIGHT_M = 5.0                  # trackside cameras above the terrain
SIDE_BEYOND_M = (10.0, 14.0, 7.0, 20.0, 28.0)
SIDE_SHIFT = (0.0, 0.15, -0.15, 0.3, -0.3)        # along the range, as a share of its length


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


class _Site:
    """Where a camera may stand: off the roads, on dry land inside the terrain, clear of props and no-go zones,
    with a clear view of its target."""

    def __init__(self, geo, field, planner, placements):
        self.geo, self.field, self.planner = geo, field, planner
        self.props = _Props(placements)

    def hw(self, xy) -> float:
        """Half width of the road sample nearest xy (real tracks vary in width)."""
        ix = self.geo.index
        return float(ix.hw[int(np.argmin(np.hypot(ix.xy[:, 0] - xy[0], ix.xy[:, 1] - xy[1])))])

    def terrain(self, xy) -> np.ndarray:
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        return self.geo.terrain(np.column_stack([xy, np.zeros(len(xy))]))

    def sees(self, eye: np.ndarray, target: np.ndarray) -> bool:
        t = np.linspace(0.04, 0.96, 40)
        line = eye[None, :] + t[:, None] * (target - eye)[None, :]
        if np.any(self.terrain(line[:, :2]) + 0.3 > line[:, 2]):
            return False
        return not self.props.blocks(eye, target)

    def eye(self, q: np.ndarray, beyond: float, height: float, min_z: float, target: np.ndarray) -> np.ndarray | None:
        x0, x1, y0, y1 = self.geo.extent
        if not (x0 + 20 <= q[0] <= x1 - 20 and y0 + 20 <= q[1] <= y1 - 20):
            return None
        if float(self.field.dist(q)[0]) < beyond - 1.5:  # another road (or this one) nearer than intended
            return None
        if not self.planner.dry(float(q[0]), float(q[1])):
            return None
        if self.planner.in_nogo(q, 1.0, True)[0]:  # bridges and jumps (the grid zone only keeps props out)
            return None
        z = max(float(self.terrain(q)[0]) + height, min_z)
        e = np.array([q[0], q[1], z])
        return e if self.props.clear(q, z) and self.sees(e, target) else None


def start_camera(geo, lay, field, planner, placements) -> tuple[np.ndarray, np.ndarray, str]:
    """(eye, target, note) of the layout's start camera, TrackModel coordinates (x, y, z up)."""
    grid = _grid(lay)
    path = _path(lay)
    pole = np.array(grid[0].location, dtype=np.float64)
    back = np.array(grid[-1].location, dtype=np.float64)
    target = pole + (back - pole) * 0.1
    target[2] += TARGET_LIFT_M
    i0 = int(np.argmin(np.hypot(*(path[:, :2] - pole[:2]).T)))
    site = _Site(geo, field, planner, placements)
    for along in ALONG_M:
        c, tg = _walk(path, i0, along)
        nl = np.array([-tg[1], tg[0]])
        for beyond in BEYOND_EDGE_M:
            for side in (1.0, -1.0):
                q = c[:2] + nl * side * (site.hw(c) + beyond)
                e = site.eye(q, beyond, HEIGHT_M, pole[2] + MIN_ABOVE_POLE_M, target)
                if e is not None:
                    return e, target, f"{along:.0f} m ahead of the pole, {beyond:.0f} m beyond the edge"
    # fallback: straight above the road ahead of the pole, high enough to see over the props
    c, _ = _walk(path, i0, ALONG_M[0])
    eye = np.array([c[0], c[1], max(float(site.terrain(c[:2])[0]), c[2]) + FALLBACK_HEIGHT_M])
    return eye, target, "fallback above the road"


def sector_centres(lay) -> np.ndarray:
    """Centres (x, y, z) of the layout's main AI route sectors in driving order: the faces of ``#ai_route_main``,
    which bgeometry compiles one sector per face in the same order (measured: every layout's pole lies in
    sector 6 of its compiled route; the compiled route has one more, closing sector)."""
    m = next(m for m in lay.meshes if m.name == "#ai_route_main")
    v = np.array(m.verts, dtype=np.float64)
    return np.array([v[list(f)].mean(0) for f in m.faces])


def ranges(n: int, centres: np.ndarray, spacing: float = SPACING_M) -> list[tuple[int, int]]:
    """Contiguous sector ranges (first, last, inclusive) covering sectors 0..n (the compiled closing sector n
    too), each about ``spacing`` metres of the lap."""
    d = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(centres[:, :2], axis=0).T))])
    k = max(1, int(round(d[-1] / spacing)))
    cuts = [int(np.searchsorted(d, d[-1] * j / k)) for j in range(k)] + [n + 1]
    out = []
    for a, b in zip(cuts, cuts[1:]):
        if b - 1 >= a:
            out.append((a, b - 1))
    return out


def trackside_cameras(geo, lay, field, planner, placements) -> list[tuple[np.ndarray, np.ndarray, tuple[int, int], str]]:
    """(eye, target, (first, last sector), note) per range of the lap (``ranges``), TrackModel coordinates: a
    camera SIDE_HEIGHT_M above the terrain, SIDE_BEYOND_M beyond the road edge on either side near the range's
    middle, looking at the road there."""
    cen = sector_centres(lay)
    n = len(cen)
    site = _Site(geo, field, planner, placements)
    out = []
    for a, b in ranges(n, cen):
        lo, hi = min(a, n - 1), min(b, n - 1)
        found = None
        for shift in SIDE_SHIFT:
            k = int(round(lo + (hi - lo) * (0.5 + shift)))
            k = min(max(k, 0), n - 1)
            c = cen[k]
            t = cen[min(k + 1, n - 1)] - cen[max(k - 1, 0)]
            t = t[:2] / max(float(np.hypot(*t[:2])), 1e-9)
            nl = np.array([-t[1], t[0]])
            target = c.copy()
            target[2] += TARGET_LIFT_M
            for beyond in SIDE_BEYOND_M:
                for side in (1.0, -1.0):
                    q = c[:2] + nl * side * (site.hw(c) + beyond)
                    e = site.eye(q, beyond, SIDE_HEIGHT_M, c[2] + 1.5, target)
                    if e is not None:
                        found = (e, target, (a, b), f"sector {k}, {beyond:.0f} m beyond the edge")
                        break
                if found:
                    break
            if found:
                break
        if found is None:  # above the road at the range's middle
            k = (lo + hi) // 2
            c = cen[k]
            target = cen[min(k + 3, n - 1)].copy()
            target[2] += TARGET_LIFT_M
            eye = np.array([c[0], c[1], max(float(site.terrain(c[:2])[0]), c[2]) + FALLBACK_HEIGHT_M])
            found = (eye, target, (a, b), "fallback above the road")
        out.append(found)
    return out
