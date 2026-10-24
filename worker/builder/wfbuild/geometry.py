"""Track geometry from a TrackModel: road, junctions, kerbs, shoulders, walls, terrain, bridges, jumps.

Pure numpy (no bpy), deterministic. Coordinates are the TrackModel's: metres, Z up, x east, y north.
Meshes are returned as :class:`Mesh` (vertices, polygons, per-face material keys and per-loop UVs);
``bscene`` turns them into Blender objects.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import config as C


def smoothstep(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def wrap_uv(uv: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Shift a face's UVs by whole repeats so the smallest u and v lie in [0, 1).

    Wreckfest stores UVs as int16 / 2048, so they must stay within +-16 (stock UV1 spans about -16..14).
    Shifting by integers is invisible for tiling textures; neighbouring faces only differ where a face
    crosses a whole repeat, so few vertices are split."""
    if not uv:
        return uv
    du = math.floor(min(p[0] for p in uv))
    dv = math.floor(min(p[1] for p in uv))
    return [(p[0] - du, p[1] - dv) for p in uv]


@dataclass
class Mesh:
    name: str
    custom_data: str = "col = true"
    verts: list[tuple[float, float, float]] = field(default_factory=list)
    faces: list[tuple[int, ...]] = field(default_factory=list)
    mats: list[str] = field(default_factory=list)
    uvs: list[list[tuple[float, float]]] = field(default_factory=list)
    kind: str = ""  # free-form tag for the manifest/tests (road, kerb, wall, terrain, ...)
    origin: tuple[float, float, float] | None = None  # object location; vertices are stored in world space
    uvs2: list[list[tuple[float, float]]] | None = None  # UV2 (0..1 over the track's map frame) for #blend maps

    def vert(self, p) -> int:
        self.verts.append((float(p[0]), float(p[1]), float(p[2])))
        return len(self.verts) - 1

    def face(self, idx, mat: str, uv=None) -> None:
        idx = [int(i) for i in idx]
        if uv is None:
            uv = [(self.verts[i][0] / C.TEX_TILE["ground"], self.verts[i][1] / C.TEX_TILE["ground"]) for i in idx]
        keep = [k for k in range(len(idx)) if idx[k] != idx[k - 1]] if len(idx) > 1 else []
        idx = [idx[k] for k in keep]
        if len(set(idx)) < 3 or len(set(idx)) != len(idx):
            return
        self.faces.append(tuple(idx))
        self.mats.append(mat)
        self.uvs.append(wrap_uv([(float(uv[k][0]), float(uv[k][1])) for k in keep]))

    def set_map_uv(self, frame: tuple[float, float, float]) -> None:
        """UV2 from world x, y: (x - x0) / size, (y - y0) / size, the frame of the generated blend/colour maps."""
        x0, y0, size = frame
        self.uvs2 = [[((self.verts[i][0] - x0) / size, (self.verts[i][1] - y0) / size) for i in f] for f in self.faces]

    def oriented_face(self, idx, mat: str, outward, uv=None) -> None:
        """Add a polygon, flipping it if its normal points against ``outward``."""
        pts = np.array([self.verts[i] for i in idx])
        n = _newell(pts)
        if float(np.dot(n, outward)) < 0:
            idx = idx[::-1]
            uv = uv[::-1] if uv is not None else None
        self.face(idx, mat, uv)

    @property
    def empty(self) -> bool:
        return not self.faces


class TriIndex:
    """2D point-in-triangle queries over a mesh's faces (fan-triangulated; grid hash)."""

    def __init__(self, tris: np.ndarray, cell: float = 6.0):
        self.t = np.asarray(tris, dtype=np.float64).reshape(-1, 3, 3)[:, :, :2]
        self.cell = cell
        self.grid: dict[tuple[int, int], list[int]] = {}
        if not len(self.t):
            return
        lo = np.floor(self.t.min(1) / cell).astype(np.int64)
        hi = np.floor(self.t.max(1) / cell).astype(np.int64)
        for k in range(len(self.t)):
            for gx in range(lo[k, 0], hi[k, 0] + 1):
                for gy in range(lo[k, 1], hi[k, 1] + 1):
                    self.grid.setdefault((gx, gy), []).append(k)

    @staticmethod
    def of(*meshes: "Mesh") -> "TriIndex":
        out = []
        for m in meshes:
            v = np.array(m.verts, dtype=np.float64).reshape(-1, 3)
            for f in m.faces:
                for k in range(1, len(f) - 1):
                    out.append(v[[f[0], f[k], f[k + 1]]])
        return TriIndex(np.array(out))

    def covered(self, p, tol: float = 0.02) -> bool:
        """Is 2D point p on a triangle (with ``tol`` slack, relative, for points on a shared edge)?"""
        ks = self.grid.get((int(np.floor(p[0] / self.cell)), int(np.floor(p[1] / self.cell))))
        if not ks:
            return False
        t = self.t[ks]
        a, b, c = t[:, 0], t[:, 1], t[:, 2]

        def cross(o, u, v):
            return (u[:, 0] - o[:, 0]) * (v[..., 1] - o[:, 1]) - (u[:, 1] - o[:, 1]) * (v[..., 0] - o[:, 0])

        d1, d2, d3 = cross(a, b, p), cross(b, c, p), cross(c, a, p)
        e = tol * np.sqrt(np.abs(cross(a, b, c)) + 1e-12)
        return bool(np.any(((d1 < e) & (d2 < e) & (d3 < e)) | ((d1 > -e) & (d2 > -e) & (d3 > -e))))


def _newell(pts: np.ndarray) -> np.ndarray:
    n = np.zeros(3)
    for a, b in zip(pts, np.roll(pts, -1, axis=0)):
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return n


class Route:
    def __init__(self, index: int, d: dict):
        self.index = index
        self.id = d["id"]
        self.ai_index = int(d["aiIndex"])
        self.closed = bool(d["closed"])
        smp = d["samples"]
        self.p = np.array([[q["x"], q["y"], q["z"]] for q in smp], dtype=np.float64)
        self.s = np.array([q["s"] for q in smp], dtype=np.float64)
        self.h = np.array([q["heading"] for q in smp], dtype=np.float64)
        self.w = np.array([q["width"] for q in smp], dtype=np.float64)
        self.left = np.array(d["leftEdge"], dtype=np.float64)
        self.right = np.array(d["rightEdge"], dtype=np.float64)
        self.n = len(smp)
        self.length = float(d["lengthM"])
        self.width = float(d["widthM"])
        self.nl = np.stack([-np.sin(self.h), np.cos(self.h)], axis=1)
        # camber: rise of the surface per metre towards the left edge (imported tracks; 0 for drawn ones)
        self.slope = (self.left[:, 2] - self.right[:, 2]) / np.maximum(self.w, 1e-6)
        self.tangent = np.stack([np.cos(self.h), np.sin(self.h)], axis=1)
        self.segs = self.n if self.closed else self.n - 1
        # longitudinal grade (rise per metre along s), central differences
        if self.n > 2:
            if self.closed:
                dzs = np.roll(self.p[:, 2], -1) - np.roll(self.p[:, 2], 1)
                dss = (np.roll(self.s, -1) - np.roll(self.s, 1)) % max(self.length, 1e-9)
            else:
                dzs = np.gradient(self.p[:, 2])
                dss = np.gradient(self.s)
            self.grade = dzs / np.maximum(dss, 1e-6)
        else:
            self.grade = np.zeros(self.n)
        self.m = max(1, round(float(np.median(self.w)) / C.ROAD_CELL_M))
        self.start_join = d.get("startJoin")
        self.end_join = d.get("endJoin")
        dh = np.diff(np.unwrap(np.concatenate([self.h, self.h[:1]]) if self.closed else self.h))
        ds = np.maximum(self.seg_len(), 1e-6)
        k = dh / ds[: len(dh)]
        if not self.closed:
            k = np.concatenate([k, k[-1:]])
        kern = np.ones(5) / 5.0
        if self.closed:
            k = np.convolve(np.concatenate([k[-2:], k, k[:2]]), kern, mode="valid")
        else:
            k = np.convolve(np.pad(k, 2, mode="edge"), kern, mode="valid")
        self.curvature = k  # signed, 1/m, positive = turning left

    @property
    def is_main(self) -> bool:
        return self.ai_index == 0

    def nxt(self, i: int) -> int:
        return (i + 1) % self.n

    def seg_len(self) -> np.ndarray:
        q = np.roll(self.p, -1, axis=0) if self.closed else self.p[1:]
        a = self.p if self.closed else self.p[:-1]
        return np.hypot(q[:, 0] - a[:, 0], q[:, 1] - a[:, 1])

    def s_unwrapped(self, i: int) -> float:
        return float(self.s[i])

    def s_dist(self, a: np.ndarray | float, b: np.ndarray | float) -> np.ndarray:
        d = np.abs(np.asarray(a, dtype=np.float64) - b)
        if self.closed:
            d = np.minimum(d, self.length - d)
        return d

    def signed_s(self, s: np.ndarray, s0: float) -> np.ndarray:
        d = np.asarray(s, dtype=np.float64) - s0
        if self.closed:
            d = (d + self.length / 2.0) % self.length - self.length / 2.0
        return d

    def cross_section(self, i: int) -> np.ndarray:
        t = (np.arange(self.m + 1) / self.m)[:, None]
        return self.left[i] + (self.right[i] - self.left[i]) * t

    def index_at(self, s: float) -> int:
        d = self.s_dist(self.s, s)
        return int(np.argmin(d))

    def window(self, i0: int, half: int) -> list[int]:
        if self.closed:
            return [(i0 + k) % self.n for k in range(-half, half + 1)]
        return list(range(max(0, i0 - half), min(self.n, i0 + half + 1)))


def dist_to_polyline(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """XY distance from each point to an open polyline."""
    a = poly[:-1, :2]
    b = poly[1:, :2]
    ab = b - a
    L2 = np.maximum((ab * ab).sum(1), 1e-12)
    p = pts[:, None, :2]
    t = np.clip(((p - a) * ab).sum(2) / L2, 0.0, 1.0)
    q = a + t[..., None] * ab
    return np.sqrt(((p - q) ** 2).sum(2)).min(1)


def _project_polyline(p: np.ndarray, poly: np.ndarray, max_dist: float = 3.0) -> float | None:
    """The nearest point of an open polyline to p, as a fractional vertex index (None beyond ``max_dist``)."""
    a, b = poly[:-1], poly[1:]
    e = b - a
    t = np.clip(np.sum((p - a) * e, axis=1) / np.maximum(np.sum(e * e, axis=1), 1e-12), 0.0, 1.0)
    d = np.hypot(*(a + e * t[:, None] - p).T)
    k = int(np.argmin(d))
    return None if d[k] > max_dist else float(k + t[k])


def _ray_polyline(p0: np.ndarray, d: np.ndarray, poly: np.ndarray, max_len: float) -> float | None:
    """First hit of the ray p0 + t*d (t in [0, max_len]) on an open polyline, as a fractional vertex index."""
    best = None
    for k in range(len(poly) - 1):
        a, b = poly[k], poly[k + 1]
        e = b - a
        den = d[0] * e[1] - d[1] * e[0]
        if abs(den) < 1e-12:
            continue
        w = a - p0
        t = (w[0] * e[1] - w[1] * e[0]) / den
        u = (w[0] * d[1] - w[1] * d[0]) / den
        if 0.0 <= t <= max_len and -1e-9 <= u <= 1.0 + 1e-9 and (best is None or t < best[0]):
            best = (t, k + u)
    return None if best is None else best[1]


def ear_clip(xy: np.ndarray) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon (any winding). Returns CCW triangles as index triples."""
    n = len(xy)
    area = 0.5 * float(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1]))
    idx = list(range(n)) if area > 0 else list(range(n))[::-1]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= -1e-12 and cross(b, c, p) >= -1e-12 and cross(c, a, p) >= -1e-12

    tris: list[tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3 and guard < 10 * n:
        guard += 1
        for k in range(len(idx)):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            if cross(xy[a], xy[b], xy[c]) <= 1e-9:
                continue
            if any(inside(xy[o], xy[a], xy[b], xy[c]) for o in idx if o not in (a, b, c)):
                continue
            tris.append((a, b, c))
            idx.pop(k)
            break
        else:
            # Degenerate remainder: fan it.
            for k in range(1, len(idx) - 1):
                tris.append((idx[0], idx[k], idx[k + 1]))
            return tris
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


class SampleIndex:
    """All route samples, for "is this point on (near) another road?" and terrain queries."""

    def __init__(self, routes: list[Route]):
        self.routes = routes
        self.xy = np.concatenate([r.p[:, :2] for r in routes])
        self.z = np.concatenate([r.p[:, 2] for r in routes])
        self.hw = np.concatenate([r.w / 2.0 for r in routes])
        self.nl = np.concatenate([r.nl for r in routes])
        self.slope = np.concatenate([r.slope for r in routes])
        self.ri = np.concatenate([np.full(r.n, r.index) for r in routes])
        self.si = np.concatenate([np.arange(r.n) for r in routes])
        self.s = np.concatenate([r.s for r in routes])
        self.closed_len = np.concatenate([np.full(r.n, r.length if r.closed else np.inf) for r in routes])
        self.tangent = np.concatenate([r.tangent for r in routes])
        self.grade = np.concatenate([r.grade for r in routes])

    def near_other(self, pts: np.ndarray, own_ri: np.ndarray, own_s: np.ndarray, clearance,
                   zdiff: float = 3.0, mask: np.ndarray | None = None) -> np.ndarray:
        """True where a point lies within (half width + clearance) of a sample of *another* road: a
        different route, or the same route further than OTHER_ROUTE_S along it, within ``zdiff``.
        ``clearance`` is a number or one value per point."""
        out = np.zeros(len(pts), dtype=bool)
        clr = np.broadcast_to(np.asarray(clearance, dtype=np.float64), (len(pts),))
        sel = np.ones(len(self.xy), dtype=bool) if mask is None else mask
        xy, z, hw, ri, s, cl = (self.xy[sel], self.z[sel], self.hw[sel], self.ri[sel], self.s[sel],
                                self.closed_len[sel])
        for a in range(0, len(pts), 512):
            p = pts[a:a + 512]
            d = np.hypot(p[:, None, 0] - xy[None, :, 0], p[:, None, 1] - xy[None, :, 1])
            close = d < hw[None, :] + clr[a:a + 512, None]
            if not close.any():
                continue
            sd = np.abs(s[None, :] - own_s[a:a + 512, None])
            sd = np.where(np.isfinite(cl)[None, :], np.minimum(sd, cl[None, :] - sd), sd)
            other = (ri[None, :] != own_ri[a:a + 512, None]) | (sd > C.OTHER_ROUTE_S)
            zok = np.abs(z[None, :] - p[:, None, 2]) < zdiff
            out[a:a + 512] = (close & other & zok).any(1)
        return out


    def other_at(self, pts: np.ndarray, own_ri: int, own_s: np.ndarray, zdiff: float = 8.0,
                 mask: np.ndarray | None = None) -> np.ndarray:
        """Index (into this SampleIndex) of a sample of *another* road (a different route, or this one further
        than OTHER_ROUTE_S along it) whose road covers each point (within its half width), else -1. ``mask``:
        only these samples count as road."""
        out = np.full(len(pts), -1, dtype=np.int64)
        for a in range(0, len(pts), 512):
            p = pts[a:a + 512]
            d = np.hypot(p[:, None, 0] - self.xy[None, :, 0], p[:, None, 1] - self.xy[None, :, 1]) - self.hw[None, :]
            close = d < 0.0
            if mask is not None:
                close &= mask[None, :]
            if not close.any():
                continue
            sd = np.abs(self.s[None, :] - own_s[a:a + 512, None])
            sd = np.where(np.isfinite(self.closed_len)[None, :], np.minimum(sd, self.closed_len[None, :] - sd), sd)
            other = (self.ri[None, :] != own_ri) | (sd > C.OTHER_ROUTE_S)
            ok = close & other & (np.abs(self.z[None, :] - p[:, None, 2]) < zdiff)
            hit = ok.any(1)
            out[a:a + 512][hit] = np.argmin(np.where(ok, d, np.inf), axis=1)[hit]
        return out


class TerrainField:
    """Terrain height: the natural ground, pulled to just below the road near it.

    ``ground`` is trackgen_geom's seeded height field (the same one the road heights came from) or, for an
    imported track, its real heightfield. Near a road the terrain follows the nearest road surface (see
    ``__call__``; once the lowest road sample within ~10 m, which on a graded road sank the grass 0.5-1 m below
    the edge), then blends to the ground over TERRAIN_BLEND. The road surface is taken across its camber, so
    the ground meets the low edge of a cambered road and never pokes through it. Samples on bridge structures
    don't pull the terrain up. ``berm`` raises the outer edge of the terrain (off for real ground, which continues into the backdrop).
    """

    def __init__(self, index: SampleIndex, ground, influence: np.ndarray, extent: tuple[float, float, float, float],
                 berm: float = C.BERM_H):
        self.ground = ground
        sel = influence
        self.xy = index.xy[sel]
        self.z = index.z[sel]
        self.hw = index.hw[sel]
        self.nl = index.nl[sel]
        self.slope = index.slope[sel]
        self.tangent = index.tangent[sel]
        self.grade = index.grade[sel]
        self.ri = index.ri[sel]
        self.s = index.s[sel]
        self.closed_len = index.closed_len[sel]
        self.cambered = bool(np.any(np.abs(self.slope) > 1e-9))
        self.berm = berm
        self.extent = extent  # min_x, max_x, min_y, max_y of the terrain
        self.banks: list[tuple[np.ndarray, np.ndarray]] = []  # TrackGeometry._divider_banks
        self.dip = None  # (xy) -> metres the ground sinks there (an imported track's gravel traps; runoff.py)

    def road_height(self, p: np.ndarray, j: np.ndarray) -> np.ndarray:
        """Height of the road surface of sample ``j`` extended to the points ``p``: along its grade (within one
        sample spacing) and across its camber (clamped to the edges)."""
        dx, dy = p[:, 0] - self.xy[j, 0], p[:, 1] - self.xy[j, 1]
        along = np.clip(dx * self.tangent[j, 0] + dy * self.tangent[j, 1], -2.5, 2.5)
        z = self.z[j] + along * self.grade[j]
        if self.cambered:
            # the camber plane continues CAMBER_RUNOUT past the low edge (a terrain cell spanning the edge then
            # doesn't bridge above it); past the high edge the ground stays level with the edge
            lat = dx * self.nl[j, 0] + dy * self.nl[j, 1]
            s = self.slope[j]
            hi = self.hw[j] + np.where(s < 0, C.CAMBER_RUNOUT, 0.0)
            lo = -self.hw[j] - np.where(s > 0, C.CAMBER_RUNOUT, 0.0)
            z = z + np.clip(lat, lo, hi) * s
        return z

    def __call__(self, pts: np.ndarray) -> np.ndarray:
        """Near a road the terrain is the nearest road's surface (extended across its camber and along its
        grade), lowered by TERRAIN_SINK under the road and TERRAIN_FLUSH beside it, so grass meets the road
        edge a few centimetres down; where another road (a different route, or the same one further than
        OTHER_ROUTE_S along it) is about as near, the lower of the two. It then blends to the ground over
        TERRAIN_BLEND, never dipping below the straight line from the road edge to the ground at the end of
        the blend (no artificial ditch beside the road; banks that rise away from it stay)."""
        pts = np.asarray(pts, dtype=np.float64)
        out = np.empty(len(pts))
        g = self.ground(pts[:, :2])
        reach = C.TERRAIN_CORE + C.TERRAIN_BLEND
        near_band = 3.0 + C.TERRAIN_CELL * 0.75
        for a in range(0, len(pts), 1024):
            p = pts[a:a + 1024]
            ar = np.arange(len(p))
            d = np.hypot(p[:, None, 0] - self.xy[None, :, 0], p[:, None, 1] - self.xy[None, :, 1])
            e = d - self.hw[None, :]
            k = np.argmin(e, axis=1)
            e_min = e[ar, k]
            zr = self.road_height(p, k)
            # other roads within the band (crossings, close parallel sections) pull the terrain below them too
            band = e <= (e_min[:, None] + near_band)
            sd = np.abs(self.s[None, :] - self.s[k][:, None])
            cl = self.closed_len[k][:, None]
            sd = np.where(np.isfinite(cl), np.minimum(sd, cl - sd), sd)
            # another road, or this one doubling back (round a hairpin: far along it but close by)
            dxy = np.hypot(self.xy[None, :, 0] - self.xy[k][:, None, 0], self.xy[None, :, 1] - self.xy[k][:, None, 1])
            other = band & ((self.ri[None, :] != self.ri[k][:, None]) | (sd > 1.5 * dxy + 4.0))
            if other.any():
                # a lower road nearby pulls the ground down to its surface only close to its own edge (fully
                # within TERRAIN_PULL_FULL, fading out over TERRAIN_PULL_FADE), so between two roads at different
                # heights the ground slopes across the gap instead of dropping to the lower one right at the
                # higher one's edge: that drop showed as dark trenches and holes beside junction gores and close
                # parallel sections (Cadwell Park's start straight, Anglesey's Corkscrew split, Brands Hatch,
                # Donington; Chris 2026-10-24)
                rows, cols = np.nonzero(other)
                pull = 1.0 - smoothstep((e[rows, cols] - C.TERRAIN_PULL_FULL) / C.TERRAIN_PULL_FADE)
                zo = np.full(len(p), np.inf)
                np.minimum.at(zo, rows, zr[rows] + pull * (self.road_height(p[rows], cols) - zr[rows]))
                zr = np.minimum(zr, zo)
            # full sink under the road (more on steep grades: a 4 m terrain cell spanning a curving road can't
            # follow it exactly), easing to flush by 1.5 m out
            under = C.TERRAIN_SINK + 0.5 * np.abs(self.grade[k])
            sink = under + (C.TERRAIN_FLUSH - under) * smoothstep(e_min / 1.5)
            zr = zr - sink
            t = smoothstep((e_min - C.TERRAIN_CORE) / C.TERRAIN_BLEND)
            gz = g[a:a + 1024]
            zz = zr * (1.0 - t) + gz * t
            # Fill dips between the road and the ground further out (verge ditches in a real DTM, the coarse
            # heightfield): not below the line from the road edge level to the ground at the blend's end.
            blend = (e_min > 0.0) & (e_min < reach)
            if blend.any():
                kb = k[blend]
                lat = ((p[blend, 0] - self.xy[kb, 0]) * self.nl[kb, 0] + (p[blend, 1] - self.xy[kb, 1]) * self.nl[kb, 1])
                outward = self.nl[kb] * np.sign(lat)[:, None]
                far_pt = p[blend, :2] + outward * (reach - e_min[blend])[:, None]
                g_far = self.ground(far_pt)
                f = np.clip(e_min[blend] / reach, 0.0, 1.0)
                floor = zr[blend] * (1.0 - f) + np.minimum(g_far, zr[blend]) * f
                zz[blend] = np.maximum(zz[blend], floor)
            far = e_min > reach
            zz[far] = gz[far]
            out[a:a + 1024] = zz
        for bp, bhw in self.banks:  # grass banks between close sections (drawn tracks)
            for a in range(0, len(pts), 1024):
                p = pts[a:a + 1024, :2]
                d = np.hypot(p[:, None, 0] - bp[None, :, 0], p[:, None, 1] - bp[None, :, 1])
                k = np.argmin(d, axis=1)
                f = 1.0 - d[np.arange(len(p)), k] / np.maximum(bhw[k], 1e-6)
                out[a:a + 1024] += C.DIVIDER_BANK_H * smoothstep(f)
        if self.dip is not None:
            out -= self.dip(pts[:, :2])
        x0, x1, y0, y1 = self.extent
        edge = np.minimum.reduce([pts[:, 0] - x0, x1 - pts[:, 0], pts[:, 1] - y0, y1 - pts[:, 1]])
        if self.berm:
            out += self.berm * smoothstep((C.BERM_W - edge) / C.BERM_W)
        return out


def make_ground(model: dict, routes: list[Route], warnings: list[str], spec: dict | None = None):
    """The natural ground height function (x, y) -> z: an imported track's real heightfield, otherwise
    trackgen_geom's seeded field when importable."""
    imported = ((spec or {}).get("sketch") or {}).get("imported")
    if imported:
        zmean = float(np.mean(np.concatenate([r.p[:, 2] for r in routes])))
        if imported.get("terrain"):
            try:
                from trackgen_geom.heightfield import Heightfield
                hf = Heightfield(imported["terrain"])

                def ground(xy: np.ndarray) -> np.ndarray:
                    return hf.height(np.asarray(xy, dtype=np.float64))

                ground.extent = hf.extent  # the real ground's coverage (the backdrop continues it)
                return ground
            except ImportError as e:  # pragma: no cover - the worker and geom images both have trackgen_geom
                warnings.append(f"ground-fallback: trackgen_geom unavailable ({e}); level ground at the mean road height")
        else:
            warnings.append("ground-flat: the imported track has no terrain; level ground at the mean road height")
        return lambda xy: np.full(len(xy), zmean)
    try:
        from trackgen_geom import elevation as el
        terrain = el.Terrain(model["slug"], model["elevation"])
        terrain.limit_grade([(r.p[:, :2], r.s, r.closed) for r in routes])

        def ground(xy: np.ndarray) -> np.ndarray:
            return terrain.height(np.asarray(xy, dtype=np.float64))

        main = next(r for r in routes if r.is_main)
        resid = np.abs(ground(main.p[:, :2]) - main.p[:, 2])
        if np.median(resid) > 0.05:
            warnings.append(f"ground-mismatch: the terrain field differs from the road heights "
                            f"(median {np.median(resid):.2f} m); the terrain follows the road anyway")
        return ground
    except (ImportError, KeyError, ValueError) as e:  # fallback when trackgen_geom isn't usable
        warnings.append(f"ground-fallback: trackgen_geom unavailable ({e}); flat ground at the mean road height")
        zmean = float(np.mean(np.concatenate([r.p[:, 2] for r in routes])))
        return lambda xy: np.full(len(xy), zmean)


@dataclass
class Bridge:
    id: str
    over: Route
    under: Route
    s_over: float
    s_under: float
    angle_deg: float
    deck_half: float
    structure: np.ndarray  # over-route sample mask: elevated structure (deck + elevated ramp)


@dataclass
class Cut:
    """Part of a road removed where it meets another road (a junction mouth or a flat crossing)."""
    route: Route
    sample: int          # the cross-section left at the edge of the hole
    target: Route        # the road it is stitched to
    target_sample: int   # around here on the target
    kind: str = "mouth"  # mouth (alt-route junction) | crossing (flat crossing hole)


class TrackGeometry:
    def __init__(self, model: dict, spec: dict):
        self.model = model
        self.spec = spec
        self.options = spec.get("options", {})
        self.warnings: list[str] = []
        self.routes = [Route(i, d) for i, d in enumerate(model["routes"])]
        self.by_id = {r.id: r for r in self.routes}
        self.main = next(r for r in self.routes if r.is_main)
        self.index = SampleIndex(self.routes)
        self.imported = (spec.get("sketch") or {}).get("imported")
        self.real_ground = bool(self.imported and self.imported.get("terrain"))
        self.ground = make_ground(model, self.routes, self.warnings, spec)
        walls = self.options.get("walls", "tyres")
        self.walls = walls if walls in ("concrete", "tyres") else "none"
        self.qkeep = {r.index: np.ones(r.segs, dtype=bool) for r in self.routes}
        self.svalid = {r.index: np.ones(r.n, dtype=bool) for r in self.routes}
        self.cuts: list[Cut] = []
        self.bridges: list[Bridge] = []
        self.structure = {r.index: np.zeros(r.n, dtype=bool) for r in self.routes}
        self.jump_zone = {r.index: np.zeros(r.n, dtype=bool) for r in self.routes}
        self.mouth_samples: dict[tuple[int, int], set[int]] = {}  # (route, side) -> samples under a stitch
        self.aprons: list[tuple[Route, list[tuple[Route, int, list[int]]]]] = []  # _apron: (link, facing edge runs)
        self.apron_routes: set[int] = set()
        self._junctions()
        self._crossings()
        self._jumps()
        b = model["bounds"]
        margin = b["terrainMarginM"]
        lim = C.LIMIT_M - 1.0
        self.extent = (max(b["minX"] - margin, -lim), min(b["maxX"] + margin, lim),
                       max(b["minY"] - margin, -lim), min(b["maxY"] + margin, lim))
        influence = ~np.concatenate([self.structure[r.index] for r in self.routes])
        self.terrain = TerrainField(self.index, self.ground, influence, self.extent,
                                    berm=0.0 if self.real_ground else C.BERM_H)
        if not self.real_ground:  # an imported track's DTM already has whatever bank divides close sections
            self.terrain.banks = self._divider_banks()
        from . import pits as pits_mod  # an imported track's real pit lane, wall, buildings and gantries
        self.pits = pits_mod.Pits(self) if pits_mod.present(self) else None
        # an imported track's measured run-off (gravel traps, paved aprons, barriers; runoff.py)
        from .runoff import RunoffPlan
        self.runoff = RunoffPlan(self)
        if self.runoff.active:
            self.runoff.finalise()
            self.terrain.dip = self.runoff.dip

    def _divider_banks(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """Grass banks under the dividers between close sections of a drawn track (where the gap leaves room):
        (points along the middle of the gap, bank half widths)."""
        out = []
        for (ri, side), g in self.road_gaps().items():
            r = self.routes[ri]
            sel = np.isfinite(g["gap"]) & g["owner"] & (g["gap"] >= C.DIVIDER_BANK_MIN_GAP)
            if not sel.any():
                continue
            edge = r.left if side > 0 else r.right
            p = edge[sel, :2] + r.nl[sel] * side * (g["gap"][sel] / 2.0)[:, None]
            out.append((p, np.minimum(g["gap"][sel] / 2.0 - 2.5, C.DIVIDER_BANK_HALF)))
        return out

    # ----------------------------------------------------------------- topology: junctions, crossings
    def _mouth(self, alt: Route, target: Route, join_sample: int, from_start: bool) -> int | None:
        half = int((4 * max(alt.width, target.width) + 40) / 2)
        win = target.window(join_sample, half)
        poly = target.p[win]
        hw = float(np.max(target.w[win])) / 2.0
        ks = range(alt.n // 2) if from_start else range(alt.n - 1, alt.n // 2, -1)
        for k in ks:
            d = dist_to_polyline(alt.cross_section(k), poly)
            if np.all(d > hw + C.MOUTH_CLEAR):
                return k
        return None

    def _junctions(self) -> None:
        for alt in self.routes:
            if alt.is_main:
                continue
            k0, k1 = 0, alt.n - 1
            ends = []
            for at_start, j in ((True, alt.start_join), (False, alt.end_join)):
                if j:
                    t = self.by_id[j["route"]]
                    ends.append((at_start, t, int(j["sampleIndex"]), self._mouth(alt, t, int(j["sampleIndex"]), at_start)))
            # an imported U-turn link between two adjacent sections (Cadwell Park's Club Hairpin): no mouth clears
            # the other road, so the turn is paved as an apron between the two sections instead (_apron)
            if self.imported and len(ends) == 2 and any(e[3] is None for e in ends) and self._apron(alt):
                continue
            for at_start, t, js, k in ends:
                if k is None:
                    self.warnings.append(f"junction-unresolved: {alt.id} {'start' if at_start else 'end'} overlaps {t.id}")
                    continue
                if at_start:
                    k0 = k
                else:
                    k1 = k
                self.cuts.append(Cut(alt, k, t, js))
            q = self.qkeep[alt.index]
            q[:] = False
            q[k0:k1] = True
            sv = self.svalid[alt.index]
            sv[:] = False
            sv[k0:k1 + 1] = True

    def _apron(self, alt: Route) -> bool:
        """Pave an imported U-turn link that lies across two adjacent sections of road (Cadwell Park's Club
        Hairpin from the run to the Mountain back onto the start straight, a few metres beside it): no cross-section
        of the branch clears both roads, so instead of its own road the gap between the two sections is paved where
        the branch crosses it (one polygon of the two facing edges' vertices, shared with their road quads) and the
        branch keeps only its AI route and checkpoints. Returns False when the link doesn't have that shape."""
        ta, ia = self.by_id[alt.start_join["route"]], int(alt.start_join["sampleIndex"])
        tb, ib = self.by_id[alt.end_join["route"]], int(alt.end_join["sampleIndex"])
        hw_b = alt.width / 2.0
        others = np.vstack([r.p[:, :2] for r in self.routes if r is not alt])
        hw_others = np.concatenate([r.w for r in self.routes if r is not alt]) / 2.0
        # the part of the turn off both roads (outside each nearest road sample's half width): it crosses the gap
        d = np.array([float(np.min(np.hypot(*(others - q).T) - hw_others)) for q in alt.p[:, :2]])
        gap = d > -0.5
        if not gap.any():
            gap = d >= d.max() - 0.25
        core = alt.p[gap, :2]
        chains = []
        for t, i, far in ((ta, ia, tb.p[ib, :2]), (tb, ib, ta.p[ia, :2])):
            side = 1 if float(np.dot(t.nl[i], far - t.p[i, :2])) > 0 else -1
            edge = t.left if side > 0 else t.right
            win = t.window(i, 60)
            near = [n for n, k in enumerate(win) if float(np.min(np.hypot(*(core - edge[k, :2]).T))) < hw_b + 0.5]
            if not near:
                return False
            chains.append((t, side, win[min(near):max(near) + 1]))
        self.aprons.append((alt, chains))
        self.apron_routes.add(alt.index)
        self.qkeep[alt.index][:] = False
        self.svalid[alt.index][:] = False
        for t, side, run in chains:
            blocked = self.mouth_samples.setdefault((t.index, side), set())
            for k in run:
                blocked.update(t.window(k, 1))
        return True

    def _crossings(self) -> None:
        for c in self.model.get("crossings", []):
            if not c.get("detected", True) or len(c.get("branches", [])) != 2:
                continue
            b = c["branches"]
            if c["type"] == "bridge":
                ob = c.get("overBranch") or 0
                over, under = self.by_id[b[ob]["route"]], self.by_id[b[1 - ob]["route"]]
                self._bridge(c, over, float(b[ob]["s"]), under, float(b[1 - ob]["s"]))
                continue
            # Flat (or unannotated) crossing: keep the branch with the lower |aiIndex| (main first),
            # cut a hole in the other and stitch both sides of the hole to the kept road's edges.
            r0, r1 = self.by_id[b[0]["route"]], self.by_id[b[1]["route"]]
            s0, s1 = float(b[0]["s"]), float(b[1]["s"])
            if abs(r1.ai_index) < abs(r0.ai_index):
                r0, r1, s0, s1 = r1, r0, s1, s0
            i0, i1 = r0.index_at(s0), r1.index_at(s1)
            half = int((4 * max(r0.width, r1.width) + 40) / 2)
            poly = r0.p[r0.window(i0, half)]
            hw = float(np.max(r0.w)) / 2.0
            def inside(k: int, r1=r1, poly=poly, hw=hw) -> bool:
                return bool(np.any(dist_to_polyline(r1.cross_section(k), poly) <= hw + C.MOUTH_CLEAR))

            ka = i1
            while inside(ka) and (r1.closed or ka > 0) and r1.s_dist(r1.s[ka], s1) < 60:
                ka = (ka - 1) % r1.n
            kb = i1
            while inside(kb) and (r1.closed or kb < r1.n - 1) and r1.s_dist(r1.s[kb], s1) < 60:
                kb = (kb + 1) % r1.n
            k = ka
            while k != kb:
                self.qkeep[r1.index][k] = False
                k = (k + 1) % r1.n if r1.closed else k + 1
                if k != kb:
                    self.svalid[r1.index][k] = False
            self.cuts.append(Cut(r1, ka, r0, i0, "crossing"))
            self.cuts.append(Cut(r1, kb, r0, i0, "crossing"))
            if c["type"] != "flat":
                self.warnings.append(f"crossing-unannotated: {c['id']} built as a flat crossing")

    def _bridge(self, c: dict, over: Route, s_over: float, under: Route, s_under: float) -> None:
        sin_a = max(math.sin(math.radians(float(c.get("angleDeg", 90.0)))), 0.3)
        hw_u = under.width / 2.0
        d = over.signed_s(over.s, s_over)
        reach = (hw_u + C.BRIDGE_STRUCT_EXTRA) / sin_a
        lift = over.p[:, 2] - self.ground(over.p[:, :2])
        deck_half = float(c.get("deckHalfLengthM", hw_u / sin_a))
        st = (np.abs(d) <= deck_half) | ((np.abs(d) <= reach) & (lift > 0.5))
        self.structure[over.index] |= st
        self.bridges.append(Bridge(c["id"], over, under, s_over, s_under, float(c.get("angleDeg", 90.0)),
                                   deck_half, st))

    def _jumps(self) -> None:
        for j in self.model.get("jumps", []):
            r = self.by_id[j["route"]]
            d = r.signed_s(r.s, float(j["s"]))
            lo = float(j["startS"]) - float(j["s"])
            hi = float(j["endS"]) - float(j["s"])
            self.jump_zone[r.index] |= (d >= lo - 2.0) & (d <= hi + 2.0)

    # ----------------------------------------------------------------------------------- materials
    def road_material(self, r: Route, i: int) -> str:
        roads = C.SURFACE_ROADS.get(self.options.get("surface", "tarmac"), C.SURFACE_ROADS["tarmac"])
        if len(roads) == 1:
            return roads[0]
        if not r.is_main:
            return roads[1]
        cps = sorted(float(c["s"]) for c in self.model.get("checkpoints", [])
                     if c["route"] == r.id and not c.get("isFinish"))
        sector = sum(1 for s in cps if s <= r.s[i])
        return roads[sector % 2]

    # ----------------------------------------------------------------------------------------- road
    def build_road(self) -> Mesh:
        if self.__dict__.get("_road_mesh") is not None:
            return self._road_mesh
        mesh = Mesh("track_surface", "col = true", kind="road")
        vids: dict[tuple[int, int, int], int] = {}

        def vid(r: Route, i: int, j: int) -> int:
            key = (r.index, i, j)
            if key not in vids:
                t = j / r.m
                vids[key] = mesh.vert(r.left[i] + (r.right[i] - r.left[i]) * t)
            return vids[key]

        tile = C.TEX_TILE["road"]
        for r in self.routes:
            q = self.qkeep[r.index]
            seg = r.seg_len()
            for i in range(r.segs):
                if not q[i]:
                    continue
                i2 = r.nxt(i)
                mat = self.road_material(r, i)
                v0, v1 = r.s[i] / tile, (r.s[i] + seg[i]) / tile
                wq = 0.5 * (r.w[i] + r.w[i2])  # the road's width here (real tracks: measured, varies along)
                for j in range(r.m):
                    u0 = j * wq / r.m / tile
                    u1 = (j + 1) * wq / r.m / tile
                    mesh.face((vid(r, i, j), vid(r, i, j + 1), vid(r, i2, j + 1), vid(r, i2, j)), mat,
                              [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
        self.road_quads = len(mesh.faces)
        for cut in self.cuts:
            self._stitch(mesh, vid, cut)
        for alt, chains in self.aprons:
            self._pave_apron(mesh, vid, alt, chains)
        self.road_vids = vids
        self._road_mesh = mesh
        return mesh

    def _pave_apron(self, mesh: Mesh, vid, alt: Route, chains) -> None:
        """The apron of a U-turn link (_apron): one polygon from the first section's facing edge vertices (along
        its s) and the second's (along its s, which runs the other way), triangulated."""
        ids = []
        for t, side, run in chains:
            ids += [vid(t, k, 0 if side > 0 else t.m) for k in run]
        if len(set(ids)) != len(ids) or len(ids) < 3:
            self.warnings.append(f"apron-degenerate: {alt.id}")
            return
        xy = np.array([mesh.verts[i][:2] for i in ids])
        tris = ear_clip(xy)
        if len(tris) != len(ids) - 2:
            self.warnings.append(f"apron-not-simple: {alt.id}")
        mat = self.road_material(alt, 0)
        tile = C.TEX_TILE["road"]
        for a, b, c in tris:
            f = (ids[a], ids[b], ids[c])
            mesh.face(f, mat, [(mesh.verts[i][0] / tile, mesh.verts[i][1] / tile) for i in f])
        self.stitched = getattr(self, "stitched", [])
        for t, side, run in chains:  # one entry per joined section, as a stitched junction has
            self.stitched.append({"route": alt.id, "sample": 0, "target": t.id, "kind": "apron",
                                  "edgeVertices": [list(mesh.verts[vid(t, k, 0 if side > 0 else t.m)]) for k in run]})

    def _stitch(self, mesh: Mesh, vid, cut: Cut) -> None:
        """Fill the gap between a cross-section of one road and the edge of the road it meets."""
        r, t = cut.route, cut.target
        cross = r.cross_section(cut.sample)
        cross_ids = [vid(r, cut.sample, j) for j in range(r.m + 1)]
        half = int((4 * max(r.width, t.width) + 40) / 2)
        win = t.window(cut.target_sample, half)
        wpts = t.p[win, :2]
        c = cross[:, :2].mean(0)
        jc = int(np.argmin(np.hypot(*(wpts - c).T)))
        side = float(np.dot(c - wpts[jc], t.nl[win[jc]]))
        col = 0 if side > 0 else t.m
        edge = (t.left if side > 0 else t.right)[win, :2]
        # The mouth's sides follow this road's own edges back from the cut to where they enter the target road
        # (a junction mouth: the fork keeps the real outline of both roads), then on to the target's edge; the
        # mouth spans the target-edge vertices between the two hits. Straight chords from the cut (as for a
        # flat crossing's hole) filled a large triangular apron outside a curving branch's edge (Donington's
        # National split; Chris 2026-10-24).
        away = r.tangent[cut.sample] * (1.0 if np.dot(r.tangent[cut.sample], c - wpts[jc]) >= 0 else -1.0)
        step_k = -1 if cut.sample < r.n // 2 else 1  # towards the junction
        hw_t = float(np.max(t.w[win])) / 2.0
        chains: list[list[int]] = [[], []]
        ends = []
        for n_c, (p0, ecol) in enumerate(((cross[0, :2], r.left), (cross[-1, :2], r.right))):
            start, d = p0, -away
            if cut.kind == "mouth":
                k = cut.sample + step_k
                last = p0
                while 0 <= k < r.n and len(chains[n_c]) < 60:
                    q = ecol[k, :2]
                    if float(np.min(dist_to_polyline(q[None, :], wpts))) <= hw_t:
                        break
                    # on a third road (another junction right beside this one, Anglesey's Bus Stop): that one's
                    # mouth paves it, so this side keeps the plain chord
                    mask = self.index.ri != t.index
                    if self.index.near_other(ecol[k:k + 1], np.array([r.index]), r.s[k:k + 1], 0.0, mask=mask)[0]:
                        chains[n_c] = []
                        break
                    chains[n_c].append(k)
                    last = q
                    k += step_k
                if 0 <= k < r.n and chains[n_c]:
                    dv = ecol[k, :2] - last
                    if float(np.hypot(*dv)) > 1e-6:
                        start, d = last, dv / float(np.hypot(*dv))
            hit = _ray_polyline(start, d, edge, 40.0)
            if hit is None and chains[n_c]:  # a shallow merge: the edges run side by side; the nearest edge point
                hit = _project_polyline(start, edge)
            if hit is None and chains[n_c]:  # back to the plain chord from the cut
                chains[n_c] = []
                hit = _ray_polyline(p0, -away, edge, 40.0)
            ends.append(hit if hit is not None else float(np.argmin(np.hypot(*(wpts - p0).T))))
        ja, jb = ends
        lo, hi = math.floor(min(ja, jb)), math.ceil(max(ja, jb))
        lo, hi = max(0, lo), min(len(win) - 1, hi)
        if hi - lo < 1:
            lo, hi = max(0, lo - 1), min(len(win) - 1, hi + 1)
        # two junctions close together on the same edge (Anglesey's Bus Stop: the Coastal joins one node before
        # the National leaves): each edge segment belongs to one stitch only, so no edge gets three faces
        used = self.__dict__.setdefault("_stitch_segs", {}).setdefault((t.index, 1 if side > 0 else -1), set())
        while hi - lo > 1 and (win[lo], win[lo + 1]) in used:
            lo += 1
        while hi - lo > 1 and (win[hi - 1], win[hi]) in used:
            hi -= 1
        if any((win[k], win[k + 1]) in used for k in range(lo, hi)):
            self.warnings.append(f"stitch-overlap: {r.id}@{cut.sample} -> {t.id}")
            return
        used.update((win[k], win[k + 1]) for k in range(lo, hi))
        edge_ids = [vid(t, win[k], col) for k in range(lo, hi + 1)]
        blocked = self.mouth_samples.setdefault((t.index, 1 if side > 0 else -1), set())
        blocked.update(win[k] for k in range(max(0, lo - 1), min(len(win), hi + 2)))
        # around the polygon: the target edge (lo -> hi), the side reaching hi (from the target edge to the cut),
        # the cut's cross-section, then the other side back towards lo
        side_a = [vid(r, k, 0) for k in chains[0]]      # from the cut corner (left) towards the target
        side_b = [vid(r, k, r.m) for k in chains[1]]    # from the cut corner (right) towards the target
        if jb >= ja:
            tail = side_b[::-1] + cross_ids[::-1] + side_a
        else:
            tail = side_a[::-1] + cross_ids + side_b
        poly_ids = edge_ids + tail
        if len(set(poly_ids)) != len(poly_ids):
            self.warnings.append(f"stitch-degenerate: {r.id}@{cut.sample} -> {t.id}")
            return
        xy = np.array([mesh.verts[i][:2] for i in poly_ids])
        mat = self.road_material(r, cut.sample)
        tile = C.TEX_TILE["road"]
        for a, b, cc in ear_clip(xy):
            ids = (poly_ids[a], poly_ids[b], poly_ids[cc])
            mesh.face(ids, mat, [(mesh.verts[i][0] / tile, mesh.verts[i][1] / tile) for i in ids])
        self.__dict__.setdefault("stitch_polys", []).append(xy)
        self.stitched = getattr(self, "stitched", [])
        self.stitched.append({"route": r.id, "sample": cut.sample, "target": t.id,
                              "edgeVertices": [list(mesh.verts[i]) for i in edge_ids]})

    # ------------------------------------------------------------------------ kerbs and shoulders
    def _side_points(self, r: Route, side: int, offset: np.ndarray | float) -> np.ndarray:
        """Points ``offset`` metres outside the road edge (side +1 left, -1 right)."""
        edge = r.left if side > 0 else r.right
        out = r.nl * side
        p = edge.copy()
        p[:, :2] += out * np.asarray(offset, dtype=np.float64).reshape(-1, 1)
        return p

    def _edge_ok(self, r: Route, pts: np.ndarray, clearance, side: int | None = None) -> np.ndarray:
        """Samples whose roadside point (``pts``, one per sample) is clear of every other road, on valid road
        away from bridges. With ``side``, another road's samples whose surface stays inside this road on that
        side don't count (``_inside_own``): a branch leaving across the other edge of a junction must not take
        this edge's kerb and verge (Oulton Park, Chris 2026-10-24: a dark wedge of bare terrain and a kerb cut
        short opposite the Island and Fosters junctions)."""
        own_ri = np.full(len(pts), r.index)
        mask = None if side is None else ~self._inside_own(r, side)
        bad = self.index.near_other(pts, own_ri, r.s, clearance, mask=mask)
        return ~bad & self.svalid[r.index] & ~self.structure[r.index]

    def _free_width(self, r: Route, side: int, idx: np.ndarray, full: np.ndarray, road: "TriIndex | None" = None,
                    step: float = 0.25) -> np.ndarray:
        """For samples ``idx`` of r: how far out from the road edge on ``side`` (up to ``full`` per sample) the
        ground is clear of another road's surface (as _edge_ok, without clearance) and, with ``road`` (the built
        road mesh), of any road face (junction stitches included) met after leaving this road."""
        edge = (r.left if side > 0 else r.right)[idx]
        free = np.asarray(full, dtype=np.float64)[idx].copy()
        todo = np.ones(len(idx), dtype=bool)
        mask = ~self._inside_own(r, side)
        cleared = np.zeros(len(idx), dtype=bool)
        t = 0.0
        while todo.any() and t <= float(free.max()):
            k = np.nonzero(todo & (t <= free))[0]
            if not len(k):
                break
            pts = edge[k].copy()
            pts[:, :2] += r.nl[idx[k]] * side * t
            hit = self.index.near_other(pts, np.full(len(k), r.index), r.s[idx[k]], 0.0, mask=mask)
            if road is not None:  # road faces once clear of this edge (a stitch's sliver along it doesn't count)
                on = np.array([road.covered(p) for p in pts[:, :2]], dtype=bool)
                hit |= on & cleared[k]
                cleared[k] |= ~on
            free[k[hit]] = t
            todo[k[hit]] = False
            t += step
        return free

    def _built_samples(self, r: Route) -> np.ndarray:
        """Samples of r next to at least one built road segment (``qkeep``)."""
        q = self.qkeep[r.index]
        b = np.zeros(r.n, dtype=bool)
        b[:len(q)] |= q
        b[1:len(q) + 1] |= q[:r.n - 1]
        if r.closed and len(q):
            b[0] |= q[-1]
        return b

    def _inside_own(self, r: Route, side: int) -> np.ndarray:
        """Index samples of other routes (or of this one further along) whose road surface lies within route r's
        road on ``side``: beside a centreline sample of r (within 1.5 m along it, 3 m in height) and either not
        built there (the stitched overlap of a branch, ``qkeep``) or not reaching past r's edge on that side
        (lateral offset towards the side + their half width <= r's half width + 0.6 m). Such a road can't take
        r's verge on that side."""
        cache = self.__dict__.setdefault("_inside_own_cache", {})
        if (r.index, side) in cache:
            return cache[(r.index, side)]
        ix = self.index
        out = np.zeros(len(ix.xy), dtype=bool)
        cand = np.arange(len(ix.xy))
        built = np.concatenate([self._built_samples(q) for q in self.routes])
        c = r.p[:, :2]
        hw = r.w / 2.0
        for a in range(0, len(cand), 512):
            k = cand[a:a + 512]
            d = np.hypot(ix.xy[k, None, 0] - c[None, :, 0], ix.xy[k, None, 1] - c[None, :, 1])
            j = np.argmin(d, axis=1)
            rel = ix.xy[k] - c[j]
            along = np.abs(np.sum(rel * r.tangent[j, :2], axis=1))
            lat = np.sum(rel * r.nl[j], axis=1) * side
            dz = np.abs(ix.z[k] - r.p[j, 2])
            # this route's own samples nearby are its own road, not another (near_other skips them anyway)
            sd = np.abs(ix.s[k] - r.s[j])
            if r.closed:
                sd = np.minimum(sd, r.length - sd)
            other = (ix.ri[k] != r.index) | (sd > C.OTHER_ROUTE_S)
            out[k] = other & (along <= 1.5) & (dz < 3.0) & ((lat + ix.hw[k] <= hw[j] + 0.6) | ~built[k])
        cache[(r.index, side)] = out
        return out

    # ------------------------------------------------------------- corners, kerbs and track limits
    def corner_runs(self, r: Route, max_radius: float = C.KERB_CORNER_RADIUS,
                    min_turn_deg: float = C.CORNER_MIN_TURN_DEG) -> list[dict]:
        """Corners of route r: runs of samples whose curvature (smoothed over ~CORNER_SMOOTH_M) is tighter than
        ``max_radius`` one way and that turn at least ``min_turn_deg`` in all, with the entry, apex (sharpest
        sample) and exit samples, the inside side (+1 left) and the apex radius. Kinks on straights don't count."""
        seg = float(np.median(r.seg_len())) if r.n > 1 else 2.0
        w = max(1, int(round(C.CORNER_SMOOTH_M / max(seg, 1e-6) / 2)))
        ker = np.ones(2 * w + 1) / (2 * w + 1)
        k = r.curvature
        ks = (np.convolve(np.concatenate([k[-w:], k, k[:w]]), ker, mode="valid") if r.closed
              else np.convolve(np.pad(k, w, mode="edge"), ker, mode="valid"))
        segs = r.seg_len()
        out = []
        for sign in (1, -1):
            for run in self._runs(ks * sign > 1.0 / max_radius, r.closed):
                idx = np.array(run)
                turn = abs(float(np.sum(k[idx] * segs[np.minimum(idx, len(segs) - 1)])))
                if len(run) < 3 or math.degrees(turn) < min_turn_deg:
                    continue
                apex = int(idx[np.argmax(np.abs(ks[idx]))])
                out.append({"inside": sign, "i_in": int(run[0]), "i_apex": apex, "i_out": int(run[-1]),
                            "radius": 1.0 / max(abs(float(ks[apex])), 1e-9), "turnDeg": math.degrees(turn)})
        return sorted(out, key=lambda c: float(r.s[c["i_apex"]]))

    def _named(self, s: float) -> str:
        """The imported track's corner label nearest arc length s on the main route (within 150 m), else ''."""
        best, name = 150.0, ""
        for c in (self.imported or {}).get("corners") or []:
            d = float(self.main.s_dist(float(c["sM"]), s))
            if d < best:
                best, name = d, c["name"]
        return name

    def kerb_plan(self) -> dict[tuple[int, int], tuple[np.ndarray, np.ndarray]]:
        """Kerb and green track-limit strip widths per (route, side), as on real circuits: per corner an apex kerb
        on the inside, an exit kerb on the outside from past the apex to beyond the exit, and on fast corners
        (apex radius >= GREEN_MIN_RADIUS) a green strip beyond the exit kerb; nothing on straights. An imported
        track's ``kerbs`` overrides (real data: OSM, LIDAR, track guides) replace the geometric choice at the
        corner nearest their sM. ``self.kerb_report`` records each corner's kerbs and their source."""
        if getattr(self, "_kerb_plan", None) is not None:
            return self._kerb_plan
        plan: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        self.kerb_report: list[dict] = []
        overrides = list((self.imported or {}).get("kerbs") or [])
        for r in self.routes:
            want = {side: (np.zeros(r.n, dtype=bool), np.zeros(r.n, dtype=bool)) for side in (1, -1)}
            corners = (self.corner_runs(r) if self.options.get("kerbs", False) and r.index not in self.apron_routes
                       else [])  # an apron link has no road edge of its own (_apron_kerb)
            if r.is_main and corners:
                for o in overrides:  # each override applies to the corner whose apex is nearest its sM
                    near = min(corners, key=lambda c: float(r.s_dist(r.s[c["i_apex"]], float(o["sM"]))))
                    if float(r.s_dist(r.s[near["i_apex"]], float(o["sM"]))) < 150.0:
                        near.setdefault("overrides", []).append(o)
            L = r.length
            for c in corners:
                s_in, s_ap, s_out = (float(r.s[c[k]]) for k in ("i_in", "i_apex", "i_out"))
                before = (s_ap - s_in) % L if r.closed else s_ap - s_in
                after = (s_out - s_ap) % L if r.closed else s_out - s_ap
                R = c["radius"]
                kinds = {"apex": True, "exit": True, "entry": False, "green": R >= C.GREEN_MIN_RADIUS}
                source = "geometry"
                for o in c.get("overrides", []):
                    kinds.update({k: bool(o[k]) for k in ("entry", "apex", "exit", "green") if k in o})
                    source = o["source"]
                d = r.signed_s(r.s, s_ap)
                ins, outs = c["inside"], -c["inside"]
                a = float(np.clip(0.3 * (before + after) + 6.0, 8.0, 30.0))
                spans = []
                if kinds["apex"]:
                    spans.append((ins, -a, a, False))
                if kinds["exit"]:
                    spans.append((outs, 0.25 * after, after + float(np.clip(0.2 * R, 8.0, 30.0)), kinds["green"]))
                if kinds["entry"]:
                    spans.append((outs, -before - 15.0, -0.7 * before, False))
                for side, lo, hi, green in spans:
                    m = (d >= lo) & (d <= hi)
                    want[side][0][m] = True
                    if green:
                        want[side][1][m] = True
                self.kerb_report.append({
                    "route": r.id, "corner": self._named(s_ap) if r.is_main else "", "sApex": round(s_ap, 1),
                    "inside": "left" if ins > 0 else "right", "radiusM": round(R, 1),
                    "kerbs": [k for k in ("entry", "apex", "exit", "green") if kinds[k]], "source": source})
            for side in (1, -1):
                kerb, green = want[side]
                kerb &= ~self.jump_zone[r.index]
                outer = self._side_points(r, side, C.KERB_W + C.GREEN_W + C.SHOULDER_W)
                kerb &= self._edge_ok(r, outer, 0.3, side)
                kw = C.KERB_W * self._taper(r, kerb)
                gw = C.GREEN_W * self._taper(r, green & kerb)
                plan[(r.index, side)] = (kw, gw)
        self._kerb_plan = plan
        return plan

    def _taper(self, r: Route, mask: np.ndarray) -> np.ndarray:
        """1 inside the mask, tapering to 0 over KERB_TAPER samples at each end of a run; 0 outside."""
        idx = np.nonzero(~mask)[0]
        if len(idx) == 0:
            return np.ones(r.n)
        dist = np.zeros(r.n, dtype=np.float64)
        for i in np.nonzero(mask)[0]:
            dd = np.abs(idx - i)
            if r.closed:
                dd = np.minimum(dd, r.n - dd)
            dist[i] = dd.min()
        return np.clip(dist / C.KERB_TAPER, 0.0, 1.0) * mask

    def kerb_widths(self, r: Route, side: int) -> np.ndarray:
        return self.kerb_plan()[(r.index, side)][0]

    def build_kerbs_and_shoulders(self) -> tuple[Mesh, Mesh]:
        return self.build_roadside()[:2]

    def build_roadside(self) -> tuple[Mesh, Mesh, Mesh]:
        """Kerbs (stock red/white prop_curb1, #curb, rising KERB_RISE to their outer side), green track-limit
        strips beyond exit kerbs (our own green paint, from the kerb down to just above the grass) and the
        verge shoulders. Returns (kerbs, shoulders, limits)."""
        if getattr(self, "_roadside", None) is not None:
            return self._roadside
        road = TriIndex.of(self.build_road())  # junction stitches and mouths are set while the road is built
        kerbs = Mesh("track_kerbs", "col = true", kind="kerb")
        limits = Mesh("track_limits", "col = true", kind="limits")
        shoulders = Mesh("track_shoulders", "col = true", kind="shoulder")
        none = self.walls == "none"
        sw = C.RUNOFF_W if none else C.SHOULDER_W
        drop = C.RUNOFF_DROP if none else C.SHOULDER_DROP
        # Same textures as the terrain (and the same world-space UV1), so the verge blends into it seamlessly;
        # only the surface (gravel) differs.
        smat = "shoulder"
        self.kerb_runs: dict[str, np.ndarray] = {}
        self.edge_ok: dict[tuple[int, int], np.ndarray] = {}  # (route, side) -> samples with a plain edge
        plan = self.kerb_plan()
        P = C.KERB_PERIOD
        up = np.array([0.0, 0.0, 1.0])
        for r in self.routes:
            q = self.qkeep[r.index]
            seg = r.seg_len()
            for side in (1, -1):
                kw, gw = plan[(r.index, side)]
                smats = None
                if self.runoff.active:  # a paved apron or a gravel trap right at the edge: the shoulder is it too
                    a = self.runoff.side[(r.index, side)]
                    smats = np.where(a["surface"] == 2, "terrain_apron",
                                     np.where((a["surface"] == 1) & (a["g0"] <= kw + gw + C.SHOULDER_W), "terrain_trap", smat))
                self.kerb_runs[f"{r.id}:{'l' if side > 0 else 'r'}"] = kw
                edge = r.left if side > 0 else r.right
                inner = self._side_points(r, side, kw)
                inner[:, 2] = edge[:, 2] + C.KERB_RISE * (kw / C.KERB_W)
                green = self._side_points(r, side, kw + gw)
                gz = self.terrain(green) + C.GREEN_LIFT
                green[:, 2] = np.where(gw > 0, np.minimum(inner[:, 2], gz), inner[:, 2])
                outer = self._side_points(r, side, kw + gw + sw)
                outer[:, 2] = np.minimum(green[:, 2] - drop, self.terrain(outer) - 0.3)
                ok = self._edge_ok(r, outer, 0.3, side) & self._edge_ok(r, inner, 0.3, side)
                for i in self.mouth_samples.get((r.index, side), ()):
                    ok[i] = False
                self.edge_ok[(r.index, side)] = ok & (kw <= 0)
                # Where another road takes the full strip (a junction's gore, two roads converging), a narrower
                # verge reaches half way to it (that road's own verge takes the other half), so no bare terrain
                # shows between road and grass (Oulton Park, Chris 2026-10-24); none where the roads touch.
                valid = self.svalid[r.index] & ~self.structure[r.index]
                # (no kerb or green strip there: they need the full strip; the verge starts at the road edge)
                narrow = np.nonzero(~ok & valid)[0]
                width = np.where(ok, kw + gw + sw, 0.0)
                if len(narrow):
                    free = self._free_width(r, side, narrow, np.full(r.n, 2.0 * sw), road)
                    w = np.clip(free / 2.0 - 0.05, 0.0, sw)
                    w[w < 0.3] = 0.0
                    width[narrow] = w
                    green[narrow] = edge[narrow]
                    o = self._side_points(r, side, width)[narrow]
                    o[:, 2] = np.minimum(green[narrow, 2] - drop * w / sw, self.terrain(o) - 0.3)
                    outer[narrow] = o
                kv: dict[int, tuple[int, int]] = {}
                gv: dict[int, tuple[int, int]] = {}
                sv: dict[int, tuple[int, int]] = {}
                for i in range(r.segs):
                    i2 = r.nxt(i)
                    if not (q[i] and valid[i] and valid[i2] and max(width[i], width[i2]) > 0):
                        continue
                    s0, s1 = r.s[i], r.s[i] + seg[i]
                    if ok[i] and ok[i2] and (kw[i] > 0 or kw[i2] > 0):
                        for k in (i, i2):
                            if k not in kv:
                                e = kerbs.vert(edge[k])
                                kv[k] = (e, kerbs.vert(inner[k]) if kw[k] > 0 else e)
                        # red/white blocks along the road (the texture's long axis), its short axis across the kerb
                        ids = (kv[i][0], kv[i2][0], kv[i2][1], kv[i][1])
                        uv = [(s0 / P, 0.0), (s1 / P, 0.0), (s1 / P, kw[i2] / C.KERB_W), (s0 / P, kw[i] / C.KERB_W)]
                        kerbs.oriented_face(ids, "kerb", up, uv)
                    if ok[i] and ok[i2] and (gw[i] > 0 or gw[i2] > 0):
                        for k in (i, i2):
                            if k not in gv:
                                a = limits.vert(inner[k])
                                gv[k] = (a, limits.vert(green[k]) if gw[k] > 0 else a)
                        ids = (gv[i][0], gv[i2][0], gv[i2][1], gv[i][1])
                        t = C.TEX_TILE["limits"]
                        uv = [(0.0, s0 / t), (0.0, s1 / t), (gw[i2] / t, s1 / t), (gw[i] / t, s0 / t)]
                        limits.oriented_face(ids, "limits", up, uv)
                    for k in (i, i2):
                        if k not in sv:
                            g = shoulders.vert(green[k])
                            sv[k] = (g, shoulders.vert(outer[k]) if width[k] > 0 else g)
                    ids = (sv[i][0], sv[i2][0], sv[i2][1], sv[i][1])
                    shoulders.oriented_face(ids, smats[i] if smats is not None else smat, up)  # world-space UV1
        if self.options.get("kerbs", False):
            for alt, chains in self.aprons:
                self._apron_kerb(alt, chains, kerbs, limits)
        self._roadside = (kerbs, shoulders, limits)
        return self._roadside

    def _apron_kerb(self, alt: Route, chains, kerbs: Mesh, limits: Mesh) -> None:
        """Apex kerb and green strip of an apron U-turn (_apron): along the apron's inside edge, from the first
        section's edge where the turn starts across the gap to the second's where it ends, facing back between
        the two sections (the inside of the hairpin)."""
        (ta, sa, run_a), (tb, sb, run_b) = chains
        ea, eb = (ta.left if sa > 0 else ta.right), (tb.left if sb > 0 else tb.right)
        p, q = ea[run_a[0]].copy(), eb[run_b[-1]].copy()
        out = -(ta.tangent[run_a[0]] + tb.tangent[run_b[-1]] * -1.0)  # back along both sections, away from the turn
        n = float(np.hypot(*out))
        if n < 1e-6 or float(np.hypot(*(q - p)[:2])) < 1.0:
            return
        out = np.append(out / n, 0.0)
        up = np.array([0.0, 0.0, 1.0])
        k_in = [p, q]
        k_out = [v + out * C.KERB_W + np.array([0.0, 0.0, C.KERB_RISE]) for v in k_in]
        g_out = [v + out * C.GREEN_W for v in k_out]
        for g in g_out:
            g[2] = min(g[2], float(self.terrain(g[None, :])[0]) + C.GREEN_LIFT)
        L = float(np.hypot(*(q - p)[:2]))
        ids = [kerbs.vert(v) for v in (k_in[0], k_in[1], k_out[1], k_out[0])]
        kerbs.oriented_face(tuple(ids), "kerb", up, [(0.0, 0.0), (L / C.KERB_PERIOD, 0.0), (L / C.KERB_PERIOD, 1.0), (0.0, 1.0)])
        t = C.TEX_TILE["limits"]
        ids = [limits.vert(v) for v in (k_out[0], k_out[1], g_out[1], g_out[0])]
        limits.oriented_face(tuple(ids), "limits", up, [(0.0, 0.0), (0.0, L / t), (C.GREEN_W / t, L / t), (C.GREEN_W / t, 0.0)])
        self.kerb_plan()
        self.kerb_report.append({"route": alt.id, "corner": "", "sApex": round(alt.length / 2.0, 1), "inside": "apron",
                                 "radiusM": round(1.0 / max(float(np.max(np.abs(alt.curvature))), 1e-6), 1), "kerbs": ["apex", "green"],
                                 "source": "geometry: apex kerb and green strip on the inside edge of the apron U-turn"})

    def build_edge_decals(self) -> Mesh:
        """Stock-style broken asphalt edge: a decal strip (#decal, no collision) over each plain road edge
        (no kerb, no junction mouth, not on a bridge), bent over the verge, so the road doesn't end in a hard
        line against the verge. The texture's own broken edge sits EDGE_DECAL_OUT outside the road edge."""
        from . import envs
        d = envs.env(self.options.get("environment"))["edgeDecal"]
        mesh = Mesh("track_edges", "", kind="decal")
        none = self.walls == "none"
        sw = C.RUNOFF_W if none else C.SHOULDER_W
        drop = C.RUNOFF_DROP if none else C.SHOULDER_DROP
        width, at = float(d["width"]), float(d["edgeAt"])
        t_edge = at + C.EDGE_DECAL_OUT / width          # across-coordinate (0 = outer side) of the road edge
        offs = [(at - t) * width + C.EDGE_DECAL_OUT for t in (0.0, t_edge, 1.0)]  # outward offsets of 3 columns
        ts = (0.0, t_edge, 1.0)
        L = float(d["lengthM"])
        for r in self.routes:
            q = self.qkeep[r.index]
            for side in (1, -1):
                ok = self.edge_ok.get((r.index, side))
                if ok is None:
                    continue
                edge = r.left if side > 0 else r.right
                other = r.right if side > 0 else r.left
                cols = []
                for off in offs:
                    if off <= 0:  # on the road: along the cross-section towards the other edge
                        f = np.minimum(-off / np.maximum(r.w, 1e-6), 1.0)[:, None]
                        p = edge + (other - edge) * f
                    else:         # on the verge, following its slope
                        p = self._side_points(r, side, off)
                        p[:, 2] = edge[:, 2] - drop * min(off / sw, 1.0)
                    p = p.copy()
                    p[:, 2] += C.EDGE_DECAL_LIFT
                    cols.append(p)
                vid: dict[tuple[int, int], int] = {}
                seg = r.seg_len()
                for i in range(r.segs):
                    i2 = r.nxt(i)
                    # asphalt edges only: gravel and sand roads fade into the verge through their blend map
                    if not (q[i] and ok[i] and ok[i2]) or self.road_material(r, i) != "road_tarmac":
                        continue
                    for k in (i, i2):
                        for c in range(3):
                            if (k, c) not in vid:
                                vid[k, c] = mesh.vert(cols[c][k])
                    v0, v1 = r.s[i] / L, (r.s[i] + float(seg[i])) / L
                    for c in range(2):
                        ids = (vid[i, c], vid[i2, c], vid[i2, c + 1], vid[i, c + 1])
                        a, b = ts[c], ts[c + 1]
                        if d["across"] == "u":
                            uv = [(a, v0), (a, v1), (b, v1), (b, v0)]
                        else:
                            uv = [(v0, a), (v1, a), (v1, b), (v0, b)]
                        mesh.oriented_face(ids, "edge_decal", np.array([0.0, 0.0, 1.0]), uv)
        return mesh

    # --------------------------------------------------------------------------- surface transitions
    def surface_switches(self) -> list[dict]:
        """Where a mixed track's main route changes between asphalt and gravel (checkpoint sector bounds):
        route, sample index, s, centre point, unit tangent, half width and ``asphalt`` = +1 when the asphalt lies
        ahead (higher s), -1 when behind. Empty unless options.surface is mixed."""
        roads = C.SURFACE_ROADS.get(self.options.get("surface", "tarmac"), C.SURFACE_ROADS["tarmac"])
        if len(roads) < 2:
            return []
        r = self.main
        out = []
        for i in range(1, r.segs):
            a, b = self.road_material(r, i - 1), self.road_material(r, i)
            if a == b or not (self.qkeep[r.index][i - 1] and self.qkeep[r.index][i]):
                continue
            out.append({"route": r, "index": i, "s": float(r.s[i]), "point": r.p[i].copy(),
                        "tangent": r.tangent[i].copy(), "hw": float(r.w[i]) / 2.0,
                        "asphalt": 1 if b == "road_tarmac" else -1})
        if r.closed and self.road_material(r, r.segs - 1) != self.road_material(r, 0):
            b = self.road_material(r, 0)
            out.append({"route": r, "index": 0, "s": 0.0, "point": r.p[0].copy(), "tangent": r.tangent[0].copy(),
                        "hw": float(r.w[0]) / 2.0, "asphalt": 1 if b == "road_tarmac" else -1})
        return out

    def _section_at(self, r: Route, s: float) -> tuple[np.ndarray, np.ndarray]:
        """Left and right road edge points at arc length s (linear between samples)."""
        if r.closed:
            s = s % r.length
            ss = np.append(r.s, r.length)
            L, R = np.vstack([r.left, r.left[:1]]), np.vstack([r.right, r.right[:1]])
        else:
            s = min(max(s, float(r.s[0])), float(r.s[-1]))
            ss, L, R = r.s, r.left, r.right
        k = int(np.clip(np.searchsorted(ss, s) - 1, 0, len(ss) - 2))
        t = (s - ss[k]) / max(ss[k + 1] - ss[k], 1e-9)
        return L[k] + (L[k + 1] - L[k]) * t, R[k] + (R[k + 1] - R[k]) * t

    def build_transition_decals(self) -> Mesh:
        """Stock asphalt-to-sand transition decal (#decal, no collision) across the road at every surface switch
        of a mixed track: broken asphalt that ends at the switch, over the asphalt-side blend ramp (maps.py)."""
        mesh = Mesh("track_transitions", "", kind="decal")
        on_asphalt, on_gravel = C.TRANSITION_DECAL
        span = on_asphalt + on_gravel
        across = 2.0 * span  # the texture is 2:1, so one repeat across is twice the decal length
        for sw in self.surface_switches():
            r, sgn = sw["route"], sw["asphalt"]
            n = max(2, int(math.ceil(span / 1.0)))
            rows = []
            for k in range(n + 1):
                into = -on_gravel + span * k / n           # metres into the asphalt (negative: gravel side)
                left, right = self._section_at(r, sw["s"] + sgn * into)
                cols = []
                for j in range(r.m + 1):
                    p = left + (right - left) * (j / r.m)
                    cols.append(mesh.vert((p[0], p[1], p[2] + C.EDGE_DECAL_LIFT + 0.01)))
                rows.append((cols, (into + on_gravel) / span))
            w = float(np.linalg.norm((right - left)[:2]))
            for k in range(n):
                (c0, v0), (c1, v1) = rows[k], rows[k + 1]
                for j in range(r.m):
                    u0, u1 = j * w / r.m / across, (j + 1) * w / r.m / across
                    mesh.oriented_face((c0[j], c0[j + 1], c1[j + 1], c1[j]), "transition_decal",
                                       np.array([0.0, 0.0, 1.0]), [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
        return mesh

    # ---------------------------------------------------------------------------------------- walls
    def _extrude(self, mesh: Mesh, line: np.ndarray, out2: np.ndarray, base: np.ndarray, top: np.ndarray,
                 profile: list[tuple[float, float]], mat: str, closed: bool, s: np.ndarray) -> None:
        """Sweep a wall profile along ``line``. Profile points are (outward offset, height fraction
        between base and top), ordered from the inner foot over the top to the outer foot."""
        n = len(line)
        rings = []
        for i in range(n):
            ring = []
            for off, f in profile:
                x, y = line[i, :2] + out2[i] * off
                ring.append(mesh.vert((x, y, base[i] + (top[i] - base[i]) * f)))
            rings.append(ring)
        tile = C.TEX_TILE["wall"]
        segs = n if closed else n - 1
        for i in range(segs):
            i2 = (i + 1) % n
            v0, v1 = s[i] / tile, (s[i] + float(np.hypot(*(line[i2, :2] - line[i, :2])))) / tile
            for k in range(len(profile) - 1):
                off = 0.5 * (profile[k][0] + profile[k + 1][0])
                fz = 0.5 * (profile[k][1] + profile[k + 1][1])
                outward = np.array([out2[i][0] * np.sign(off or 1.0), out2[i][1] * np.sign(off or 1.0),
                                    1.0 if fz > 0.99 else 0.0])
                ids = (rings[i][k], rings[i2][k], rings[i2][k + 1], rings[i][k + 1])
                uv = [(v0, k / 2.0), (v1, k / 2.0), (v1, (k + 1) / 2.0), (v0, (k + 1) / 2.0)]
                mesh.oriented_face(ids, mat, outward, uv)
        if not closed:
            for i in (0, n - 1):
                t = line[1 if i == 0 else n - 2, :2]
                tang = line[i, :2] - t
                tang = tang / max(np.linalg.norm(tang), 1e-9)
                mesh.oriented_face(tuple(rings[i]), mat, np.array([tang[0], tang[1], 0.0]))

    def _runs(self, ok: np.ndarray, closed: bool) -> list[list[int]]:
        n = len(ok)
        if closed and ok.all():
            return [list(range(n))]
        runs, cur = [], []
        start = 0
        if closed:
            bad = np.nonzero(~ok)[0]
            start = int(bad[0]) + 1 if len(bad) else 0
        for k in range(n):
            i = (start + k) % n if closed else k
            if ok[i]:
                cur.append(i)
            else:
                if len(cur) >= 2:
                    runs.append(cur)
                cur = []
        if len(cur) >= 2:
            runs.append(cur)
        return runs

    def _near_s(self, r: Route, mask: np.ndarray) -> np.ndarray:
        """Distance along the route from each sample to the nearest sample in ``mask`` (inf when none)."""
        idx = np.nonzero(mask)[0]
        if not len(idx):
            return np.full(r.n, np.inf)
        out = np.empty(r.n)
        for a in range(0, r.n, 256):
            out[a:a + 256] = r.s_dist(r.s[a:a + 256, None], r.s[idx][None, :]).min(1)
        return out

    def road_gaps(self) -> dict[tuple[int, int], dict]:
        """Per (route, side): ``gap``, the distance from this road edge to another road (a different route, or
        this one further than OTHER_ROUTE_S along it) straight out on that side, up to DIVIDER_GAP (inf beyond),
        and ``owner``, whether this side builds the divider between them (of the two facing edges, the one with
        the lower (route, s)). Samples near a junction mouth, a flat crossing or a bridge don't count (those
        roads meet on purpose)."""
        if getattr(self, "_gaps", None) is not None:
            return self._gaps
        out: dict[tuple[int, int], dict] = {}
        probes = np.arange(0.5, C.DIVIDER_GAP + 0.01, 1.0)
        # an apron link (_apron) is no road of its own: probes see the sections it joins, and the apron between
        # them needs no divider
        road = ~np.isin(self.index.ri, list(self.apron_routes))
        for r in self.routes:
            skip = ~self.svalid[r.index] | self.structure[r.index]
            meet = [c.sample for c in self.cuts if c.route is r]
            # Imported tracks: where a branch's mouth meets this road, only the mouth itself (plus a few metres) is
            # left open; probes that see the branch beyond it are its merge (_merging), and a wall line never
            # crosses another road (_edge_ok). Leaving DIVIDER_JUNCTION_CLEAR open on both sides lost the divider
            # between two other sections of road beside a short link (Cadwell Park's start straight and the run
            # to the Mountain beside the Woodlands and Club links; Chris 2026-10-24: cars could cut across).
            # Drawn tracks keep the wide clearance.
            near_mouth = []
            for c in self.cuts:
                if c.target is r:
                    if self.imported and c.kind == "mouth":
                        near_mouth.append((float(r.s[c.target_sample]), 0.75 * c.route.width + C.DIVIDER_APRON_CLEAR))
                    else:
                        meet.append(c.target_sample)
            joined = {(c.route.index, c.target.index) for c in self.cuts if c.kind == "mouth"}
            meet += [int(j["sampleIndex"]) for a in self.routes for j in (a.start_join, a.end_join)
                     if j and j["route"] == r.id and a.index not in self.apron_routes
                     and not (self.imported and (a.index, r.index) in joined)]
            for s_m, clear in near_mouth:
                skip |= r.s_dist(r.s, s_m) < clear
            # an apron link (a U-turn paved across the gap between two sections) opens the divider only where it
            # crosses: its paved run plus a few metres
            for _, chains in self.aprons:
                for t, _, run in chains:
                    if t is r and run:
                        lo, hi = float(r.s[run[0]]), float(r.s[run[-1]])
                        mid, half = 0.5 * (lo + hi), 0.5 * abs(hi - lo)
                        skip |= r.s_dist(r.s, mid) < half + C.DIVIDER_APRON_CLEAR
            for b in self.bridges:
                if b.under is r:
                    meet.append(r.index_at(b.s_under))
                if b.over is r:
                    meet.append(r.index_at(b.s_over))
            for i in meet:
                skip |= r.s_dist(r.s, r.s[i]) < C.DIVIDER_JUNCTION_CLEAR
            for side in (1, -1):
                edge = r.left if side > 0 else r.right
                gap = np.full(r.n, np.inf)
                owner = np.zeros(r.n, dtype=bool)
                hit_ri = np.full(r.n, -1, dtype=np.int32)   # the other road: its route ...
                hit_s = np.full(r.n, np.nan)                # ... and metres along it
                todo = ~skip
                for t in probes:
                    if not todo.any():
                        break
                    idx = np.nonzero(todo)[0]
                    pts = edge[idx].copy()
                    pts[:, :2] += r.nl[idx] * side * t
                    j = self.index.other_at(pts, r.index, r.s[idx], mask=road)
                    if self.imported:  # a real branch merging into its road: one road, no divider
                        merge = (j >= 0) & self._merging(r, idx, j, t)
                        todo[idx[merge]] = False
                        j = np.where(merge, -1, j)
                    hit = j >= 0
                    if hit.any():
                        ii, jj = idx[hit], j[hit]
                        gap[ii] = t
                        owner[ii] = (self.index.ri[jj] > r.index) | ((self.index.ri[jj] == r.index)
                                                                     & (self.index.s[jj] > r.s[ii]))
                        hit_ri[ii], hit_s[ii] = self.index.ri[jj], self.index.s[jj]
                        todo[ii] = False
                out[(r.index, side)] = {"gap": gap, "owner": owner, "hit_ri": hit_ri, "hit_s": hit_s}
        self._gaps = out
        return out

    def separate_part(self, r: Route, g: dict) -> np.ndarray:
        """Per sample of r, whether the road its ``road_gaps`` probe hit is a separate part of the same lap: the
        same route, at least SEPARATE_PART_M away along it (not the other leg of a chicane, esses or hairpin, not a
        branch or link merging into it)."""
        same = (g["hit_ri"] == r.index) & np.isfinite(g["hit_s"])
        d = np.abs(np.where(same, g["hit_s"], 0.0) - r.s)
        if r.closed:
            d = np.minimum(d, r.length - d)
        return same & (d >= C.SEPARATE_PART_M)

    def _merging(self, r: Route, own: np.ndarray, hits: np.ndarray, gap: float) -> np.ndarray:
        """Per probe of route r (own sample indices) that hit road at SampleIndex ``hits`` ``gap`` metres out:
        whether that road is a branch joined to r (or r a branch joined to it) and the probe is within
        DIVIDER_MERGE_M of that junction along the branch, or too close for a divider anyway, where an imported
        branch still merges into (or splits from) its road at a shallow angle (Anglesey's Club Hairpin onto the
        start straight)."""
        near = gap < 2.0 * C.DIVIDER_MIN + 1.0
        out = np.zeros(len(hits), dtype=bool)
        for n, (i, j) in enumerate(zip(own, hits)):
            h = self.routes[int(self.index.ri[j])]
            for br, other, s_br in ((h, r, float(self.index.s[j])), (r, h, float(r.s[i]))):
                if br.is_main:
                    continue
                if br.start_join and br.start_join["route"] == other.id and (near or s_br <= C.DIVIDER_MERGE_M):
                    out[n] = True
                if br.end_join and br.end_join["route"] == other.id and (near or br.length - s_br <= C.DIVIDER_MERGE_M):
                    out[n] = True
        return out

    def corner_apexes(self) -> list[tuple[int, int, float]]:
        """Main-route corner apexes (index, outside side, |curvature|), sharpest first, at least 60 m apart."""
        r = self.main
        k = np.abs(r.curvature)
        w = 12
        cand = []
        for i in range(r.n):
            win = [(i + o) % r.n for o in range(-w, w + 1)] if r.closed else range(max(0, i - w), min(r.n, i + w + 1))
            if k[i] > 1.0 / 150.0 and k[i] >= max(k[j] for j in win):
                cand.append(i)
        cand.sort(key=lambda i: -k[i])
        out: list[tuple[int, int, float]] = []
        for i in cand:
            if all(r.s_dist(r.s[i], r.s[j]) > 60.0 for j, _, _ in out):
                out.append((i, -1 if r.curvature[i] > 0 else 1, float(k[i])))
        return out

    def crowd_spots(self) -> list[tuple[Route, int, int, float]]:
        """Where spectators stand (route, sample, side, length along the road), in priority order: jump landings
        and beside bridges, an imported track's named viewing spots (both sides, the longer bank outside), then
        the outside of the sharpest corners. The wall plan puts a barrier in front of each; dressing places the
        crowds there."""
        spots: list[tuple[Route, int, int, float]] = []
        for j in self.model.get("jumps", []):
            r = self.by_id[j["route"]]
            i = r.index_at((float(j["endS"]) + 25.0) % r.length if r.closed else min(float(j["endS"]) + 25.0, r.length))
            spots += [(r, i, 1, 30.0), (r, i, -1, 30.0)]
        for b in self.bridges:
            u = b.under
            for off in (-45.0, 45.0):
                k = u.index_at((b.s_under + off) % u.length if u.closed else min(max(b.s_under + off, 0.0), u.length))
                spots.append((u, k, 1 if off > 0 else -1, 24.0))
        named = (self.imported or {}).get("viewingSpots") or []
        for spot in named:
            i = self.main.index_at(float(spot["sM"]) % self.main.length)
            out = -1 if self.main.curvature[i] > 0 else 1
            spots += [(self.main, i, out, 50.0), (self.main, i, -out, 34.0)]
        n_max = (int(min(14, max(4, round(self.main.length / 260.0)))) + len(self.bridges) * 2
                 + len(self.model.get("jumps", [])) * 2 + 2 * len(named))
        corners = self.corner_apexes()
        for i, side, k in corners[:max(0, n_max - len(spots))]:
            spots.append((self.main, i, side, 30.0 + min(20.0, k * 2000.0)))
        return spots[:n_max]

    def wall_plan(self) -> dict[tuple[int, int], dict]:
        """Where a barrier (collision wall + stock barrier props) runs, per (route, side). Not round the whole lap:
        open grass run-off first, and a barrier only where one is needed, each at its own distance from the road
        edge (the nearest wins, moving between them at WALL_RAMP_SLOPE metres out per metre along):

        - **dividers** between close sections of track (another road within DIVIDER_GAP straight out): along the
          middle of the gap, built by one of the two facing edges (``road_gaps``);
        - the **grid sector** (pit wall, grandstand) and **under bridges**: WALL_OFFSET;
        - in front of **crowds** (``crowd_spots``) and the paddock: CROWD_WALL;
        - outside **fast corners** (apex radius FAST_CORNER_RADIUS): FAST_CORNER_WALL, behind the run-off;
        - where the **ground drops away** more than DROP_H within DROP_PROBE of the edge: WALL_OFFSET.

        ``ok`` marks the samples that carry a wall (not on a bridge, the line clear of other roads, no fold on
        tight insides); ``offset`` the wall line's distance from the edge, ``ai_margin`` how far the AI route
        (and with it the reset-free area) may reach beyond the edge on that side. Shared by the collision walls
        (``build_walls``), the barrier props (``dressing``) and the AI routes (``layout``)."""
        if getattr(self, "_wall_plan", None) is not None:
            return self._wall_plan
        plan: dict[tuple[int, int], dict] = {}
        slots = self.model.get("grid", {}).get("slots", [])
        gaps = self.road_gaps()
        spots = self.crowd_spots()
        for r in self.routes:
            near = np.zeros(r.n, dtype=bool)
            if r.is_main and slots:  # the grid, the start/finish and the paddock beside them (dressing.paddock)
                d = r.signed_s(r.s, 0.0)
                near = (d >= -70.0) & (d <= max(float(max(q["s"] for q in slots)) + 15.0, 130.0))
            for b in self.bridges:  # under a bridge the wall stays inside the pillars
                if b.under is r:
                    reach = (b.over.width / 2.0 + C.BRIDGE_STRUCT_EXTRA) / max(math.sin(math.radians(b.angle_deg)), 0.3)
                    near = near | (r.s_dist(r.s, b.s_under) <= reach + 20.0)
            fast = {1: np.zeros(r.n, dtype=bool), -1: np.zeros(r.n, dtype=bool)}
            measured = self.runoff.active
            for c in ([] if measured else  # measured run-off: the real barriers replace the generic ones
                      self.corner_runs(r, max_radius=C.FAST_CORNER_MAX_RADIUS, min_turn_deg=C.FAST_CORNER_MIN_TURN_DEG)):
                if c["radius"] < C.FAST_CORNER_RADIUS:
                    continue
                s_in, s_ap, s_out = (float(r.s[c[k]]) for k in ("i_in", "i_apex", "i_out"))
                d = r.signed_s(r.s, s_ap)
                before = r.s_dist(s_ap, s_in)
                after = r.s_dist(s_out, s_ap)
                fast[-c["inside"]] |= (d >= -0.5 * before) & (d <= after + 40.0)
            for side in (1, -1):
                edge = r.left if side > 0 else r.right
                g = gaps[(r.index, side)]
                reasons: list[tuple[np.ndarray, np.ndarray]] = []  # (mask, offset per sample)
                kinds: list[str] = []  # what each reason's barrier is (measured run-off: by type)
                if measured:
                    btype, boff, bfence = self.runoff.barrier(r.index, side)
                    hard = np.isin(btype, ("armco", "tyres", "concrete")) & np.isfinite(boff)
                    default = str(self.runoff.default_type)
                div = np.isfinite(g["gap"]) & g["owner"]
                if measured:
                    # a real circuit: a divider only between separate parts of the lap (Cadwell Park's start
                    # straight beside the run to the Mountain), never between a chicane's, an esses' or a hairpin's
                    # legs, nor beside a branch or link (Chris 2026-10-24: concrete walls in Donington's Fogarty Esses
                    # and between Anglesey's Coastal and International branches); other barriers come from the data
                    div &= self.separate_part(r, g)
                half = np.maximum(g["gap"] / 2.0, C.DIVIDER_MIN)
                # a divider runs DIVIDER_OVERRUN samples past the last probe that saw the other road, so a road
                # that turns away right there (Oulton Park's Island Hairpin link beside Brittens) is still divided
                # where it leaves (the line stays clear of other roads: _edge_ok below)
                grown, half_g = div.copy(), half.copy()
                for k in range(1, C.DIVIDER_OVERRUN + 1):
                    for sh in (k, -k):
                        src = np.roll(div, sh) if r.closed else np.concatenate(
                            [np.zeros(max(sh, 0), bool), div[max(-sh, 0):len(div) - max(sh, 0)], np.zeros(max(-sh, 0), bool)])
                        hv = np.roll(np.where(div, half, np.inf), sh) if r.closed else np.concatenate(
                            [np.full(max(sh, 0), np.inf), np.where(div, half, np.inf)[max(-sh, 0):len(div) - max(sh, 0)],
                             np.full(max(-sh, 0), np.inf)])
                        new = src & ~grown
                        half_g = np.where(new, hv, half_g)
                        grown |= src
                div = grown
                reasons.append((div, half_g))
                kinds.append("divider")
                reasons.append((near & self.svalid[r.index], np.full(r.n, C.WALL_OFFSET)))
                kinds.append("grid")
                crowd = np.zeros(r.n, dtype=bool)
                for sr, i, sside, length in spots:
                    if sr is r and sside == side:
                        crowd |= r.s_dist(r.s, r.s[i]) <= length / 2.0 + C.CROWD_WALL_EXTEND
                if measured:
                    # crowds stand behind the real barrier, or well back on the open grass behind the reset wall
                    # (dressing.crowd): no spectator barrier of our own (Chris 2026-10-24, Anglesey's Peel)
                    # the measured barriers, each at its own offset; where offsets step between zones the line
                    # angles across (MEASURED_WALL_SLOPE metres out per metre along)
                    reasons.append((hard, np.where(hard, boff, np.inf)))
                    kinds.append("measured")
                else:
                    reasons.append((crowd, np.full(r.n, C.CROWD_WALL)))
                    kinds.append("crowd")
                    reasons.append((fast[side], np.full(r.n, C.FAST_CORNER_WALL)))
                    kinds.append("fast")
                    probe = edge.copy()
                    probe[:, :2] += r.nl * side * C.DROP_PROBE
                    drop = (edge[:, 2] - self.terrain(probe)) > C.DROP_H
                    drop &= ~np.isfinite(g["gap"])  # a lower road beside this one is a divider's job
                    reasons.append((drop, np.full(r.n, C.WALL_OFFSET)))
                    kinds.append("drop")
                offset = np.full(r.n, np.inf)
                has = np.zeros(r.n, dtype=bool)
                which = np.full(r.n, -1, dtype=np.int32)
                masks: list[np.ndarray] = []
                for q, (mask, off) in enumerate(reasons):
                    mask = mask & self.svalid[r.index]
                    if not mask.any():
                        continue
                    slope = C.MEASURED_WALL_SLOPE if kinds[q] == "measured" else C.WALL_RAMP_SLOPE
                    cone = self._cone(r, mask, off, slope)
                    if kinds[q] == "measured":
                        # a measured barrier only where the data has one (plus short angled joins between zones)
                        reach = mask | self._near_mask(r, mask, C.MEASURED_JOIN_M) & self._between(r, mask)
                        cone = np.where(reach, cone, np.inf)
                        mask = reach
                    has |= mask
                    masks.append((q, mask))
                    which = np.where(cone < offset, q, which)
                    offset = np.minimum(offset, cone)
                if measured:
                    # never in a measured gravel trap or on an apron: the angled joins between zones and the
                    # spectator barriers stop at the run-off's outer edge (dividers and the pit wall excepted)
                    a = self.runoff.side[(r.index, side)]
                    end = np.maximum(np.where(a["surface"] == 1, a["g1"], 0.0), a["tarmac"])
                    kind_of = np.array([kinds[q] if q >= 0 else "" for q in which])
                    # (only where the divider or pit wall itself runs: its ramp out towards a barrier further
                    # out stays behind the trap, e.g. Knickerbrook's after the divider by Hislop's straight)
                    own = np.zeros(r.n, dtype=bool)
                    for q, mask in masks:
                        own |= (which == q) & mask
                    push = (end > 0) & ~(np.isin(kind_of, ("divider", "grid")) & own)
                    offset = np.where(push, np.maximum(offset, end + C.TRAP_BARRIER_GAP), offset)
                offset = np.where(np.isfinite(offset), offset, C.CROWD_WALL)
                line = self._side_points(r, side, offset)
                ok = has & self._edge_ok(r, line, 0.5)
                # Drop folds: on the inside of a tight corner the offset line can run backwards.
                nxt = np.roll(line[:, :2], -1, axis=0) if r.closed else np.vstack([line[1:, :2], line[-1:, :2]])
                prv = np.roll(line[:, :2], 1, axis=0) if r.closed else np.vstack([line[:1, :2], line[:-1, :2]])
                tan = nxt - prv
                ok &= (tan * r.tangent).sum(1) > 0.2 * np.maximum(np.linalg.norm(tan, axis=1), 1e-9)
                line[:, 2] = self.terrain(line)
                # the AI route covers the grass run-off, up to the wall and half way to another road
                runoff = C.AI_ROUTE_MARGIN["none" if self.walls == "none" else "walls"]
                margin = np.full(r.n, runoff)
                margin = np.where(ok, np.minimum(margin, offset - 0.8), margin)
                margin = np.minimum(margin, g["gap"] / 2.0 - 0.5)
                # the barrier type along the line: drawn tracks use options.walls everywhere; measured run-off
                # follows the data (dividers and spectator barriers take the nearest measured type, else the
                # circuit's default; the grid sector's pit wall is concrete)
                if measured:
                    kind_of = np.array([kinds[q] if q >= 0 else "" for q in which])
                    types = np.where(self._near_mask(r, hard, 60.0), self._fill_nearest(r, hard, btype), default)
                    types = np.where(kind_of == "grid", "concrete", types)
                    fence = np.where(hard | (kind_of == "measured"), self._fill_nearest(r, hard, bfence), False).astype(bool)
                    ok &= types != "none"
                else:
                    types = np.full(r.n, self.walls, dtype=object)
                    fence = np.zeros(r.n, dtype=bool)
                plan[(r.index, side)] = {"route": r, "side": side, "offset": offset, "line": line,
                                         "type": np.asarray(types, dtype=object), "fence": fence & ok,
                                         "out": r.nl * side, "ok": ok, "divider": div & ok,
                                         "ai_margin": np.maximum(self._cone(r, np.ones(r.n, bool), margin, 0.5), 0.5)}
        if self.pits is not None:  # the pit wall is the containment on its stretch (pits.Pits.wall_hook)
            self.pits.wall_hook(plan)
        self._wall_plan = plan
        return plan

    def _on_stitch(self, pts: np.ndarray) -> np.ndarray:
        """Points (2D) inside a junction mouth's paving (``_stitch`` polygons), by ray casting."""
        out = np.zeros(len(pts), dtype=bool)
        for poly in self.__dict__.get("stitch_polys", []):
            lo, hi = poly.min(0), poly.max(0)
            sel = np.flatnonzero((pts[:, 0] >= lo[0]) & (pts[:, 0] <= hi[0]) & (pts[:, 1] >= lo[1]) & (pts[:, 1] <= hi[1]))
            if not len(sel):
                continue
            x, y = pts[sel, 0], pts[sel, 1]
            ins = np.zeros(len(sel), dtype=bool)
            for (x1, y1), (x2, y2) in zip(poly, np.roll(poly, -1, axis=0)):
                dy = (y2 - y1) if y2 != y1 else 1e-12
                ins ^= ((y1 > y) != (y2 > y)) & (x < (x2 - x1) * (y - y1) / dy + x1)
            out[sel] |= ins
        return out

    def ground_dips(self, offsets=(1.0, 2.0, 3.0), floor: float = C.GROUND_DIP_MAX) -> list[dict]:
        """Artificial dips in the ground beside the road edges: points ``offsets`` metres out from every built edge
        sample (not on another road, a bridge or a jump) where the terrain lies more than ``floor`` below the edge
        and below the natural ground there (a real bank or ditch beside the road doesn't count, nor does ground
        falling away at the cambered edge's runout). Junction gores and close parallel sections showed these as
        dark trenches and holes when the terrain between two roads took the lower one's height up to the higher
        one's edge (Cadwell Park, Anglesey, Donington, Brands Hatch; Chris 2026-10-24). Returns one entry per
        dipping sample: route, side, s, depth (m below the allowed floor)."""
        out = []
        # only built road counts as road: a branch's samples beyond its junction cut aren't paved unless the mouth
        # stitch covers them (Brands Hatch's Indy split: its edge ran 2 m outside the main road for 40 m, unpaved,
        # with the ground sunk under it)
        built = np.concatenate([self.svalid[q.index] | (q.index in self.apron_routes) for q in self.routes])
        if getattr(self, "road_vids", None) is None:
            self.build_road()  # the junction mouths' paving
        paved = True
        for r in self.routes:
            if r.index in self.apron_routes:
                continue
            ok = self.svalid[r.index] & ~self.structure[r.index] & ~self.jump_zone[r.index]
            for side in (1, -1):
                edge = r.left if side > 0 else r.right
                worst = np.zeros(r.n)
                for t in offsets:
                    pts = edge.copy()
                    pts[:, :2] += r.nl * side * t
                    on_road = self.index.near_other(pts, np.full(r.n, r.index), r.s, 0.0, mask=built)
                    if paved is not None:  # or on a junction mouth's paving
                        on_road |= self._on_stitch(pts[:, :2])
                    drop = edge[:, 2] - self.terrain(pts)
                    natural = np.maximum(edge[:, 2] - self.ground(pts[:, :2]), 0.0)
                    runout = np.where(r.slope * side < 0, np.abs(r.slope) * min(t, C.CAMBER_RUNOUT), 0.0)
                    excess = drop - floor - natural - runout
                    worst = np.maximum(worst, np.where(ok & ~on_road, excess, 0.0))
                for i in np.flatnonzero(worst > 0.0):
                    out.append({"route": r.id, "side": "l" if side > 0 else "r", "s": round(float(r.s[i]), 1),
                                "depthM": round(float(worst[i]), 2)})
        return out

    def _cone(self, r: Route, mask: np.ndarray, value: np.ndarray, slope: float) -> np.ndarray:
        """min over masked samples j of value[j] + slope * |s_i - s_j| (inf without any): a value that changes by
        at most ``slope`` per metre along the road."""
        idx = np.nonzero(mask)[0]
        if not len(idx):
            return np.full(r.n, np.inf)
        v = np.asarray(value, dtype=np.float64)[idx]
        out = np.empty(r.n)
        for a in range(0, r.n, 256):
            out[a:a + 256] = (v[None, :] + slope * r.s_dist(r.s[a:a + 256, None], r.s[idx][None, :])).min(1)
        return out

    def _near_mask(self, r: Route, mask: np.ndarray, reach: float) -> np.ndarray:
        """Samples within ``reach`` metres (along the route) of a sample in ``mask``."""
        return self._near_s(r, mask) <= reach

    def _between(self, r: Route, mask: np.ndarray) -> np.ndarray:
        """Samples with a masked sample on both sides within MEASURED_JOIN_M (gaps between two barrier zones)."""
        idx = np.nonzero(mask)[0]
        out = np.zeros(r.n, dtype=bool)
        if not len(idx):
            return out
        for a in range(0, r.n, 256):
            d = r.signed_s(r.s[idx][None, :], 0.0) - r.signed_s(r.s[a:a + 256, None], 0.0)
            if r.closed:
                d = (d + r.length / 2.0) % r.length - r.length / 2.0
            ahead = ((d > 0) & (d <= C.MEASURED_JOIN_M)).any(1)
            behind = ((d < 0) & (d >= -C.MEASURED_JOIN_M)).any(1)
            out[a:a + 256] = ahead & behind
        return out

    def _fill_nearest(self, r: Route, mask: np.ndarray, values: np.ndarray) -> np.ndarray:
        """``values`` where ``mask``, elsewhere the value of the nearest masked sample along the route."""
        values = np.asarray(values, dtype=object)
        idx = np.nonzero(mask)[0]
        if not len(idx):
            return values.copy()
        out = values.copy()
        for a in range(0, r.n, 256):
            d = r.s_dist(r.s[a:a + 256, None], r.s[idx][None, :])
            out[a:a + 256] = values[idx[np.argmin(d, axis=1)]]
        return np.where(mask, values, out)

    def _typed_runs(self, p: dict) -> list[tuple[str, list[int], bool]]:
        """A wall plan side's continuous runs, split where the barrier type changes: (type, samples, closed).
        Consecutive runs of different types share their end sample, so the line stays unbroken."""
        r = p["route"]
        out = []
        for run in self._runs(p["ok"], r.closed):
            closed = r.closed and len(run) == r.n
            t = [str(p["type"][i]) for i in run]
            if len(set(t)) == 1:
                out.append((t[0], run, closed))
                continue
            if closed:  # start the run where the type changes
                k0 = next(k for k in range(len(run)) if t[k] != t[k - 1])
                run, t = run[k0:] + run[:k0], t[k0:] + t[:k0]
            a = 0
            for k in range(1, len(run) + 1):
                if k == len(run) or t[k] != t[a]:
                    seg = run[a:min(k + 1, len(run))] if k < len(run) else run[a:]
                    if closed and k == len(run):
                        seg = seg + [run[0]]
                    if len(seg) >= 2:
                        out.append((t[a], seg, False))
                    a = k
        return out

    def wall_runs(self) -> list[dict]:
        """The wall line as continuous runs of one barrier type: route id, side, type, points (N, 3; z =
        terrain), outward unit normals, s, offsets and debris-fence flags. Empty when options.walls is none."""
        if self.walls == "none":
            return []
        out = []
        for (_ri, side), p in sorted(self.wall_plan().items(), key=lambda kv: kv[0]):
            r = p["route"]
            for typ, run, closed in self._typed_runs(p):
                idx = np.array(run)
                out.append({"route": r.id, "side": side, "type": typ, "closed": closed,
                            "points": p["line"][idx], "out": p["out"][idx], "s": r.s[idx],
                            "offset": p["offset"][idx], "fence": p["fence"][idx]})
        return out

    def build_walls(self) -> list[Mesh]:
        """Invisible collision walls (CustomData ``vis = false | col = true``, like stock bases' wall_col meshes)
        along the wall plan, with the barrier type's surface (concrete, tyres, Armco); the visible barrier is a row
        of stock props on the same line (dressing.py) or, for an imported track's measured barriers, our own
        meshes with stock textures (build_barriers)."""
        if self.walls == "none":
            return []
        meshes = []
        for (ri, side), p in sorted(self.wall_plan().items(), key=lambda kv: kv[0]):
            r = p["route"]
            mesh = Mesh(f"walls_{r.id}_{'l' if side > 0 else 'r'}", C.WALL_CUSTOM_DATA, kind="wall")
            line = p["line"]
            tz = line[:, 2]
            base = tz - C.WALL_SINK
            for typ, run, closed in self._typed_runs(p):
                idx = np.array(run)
                h = C.WALL_HALF[typ]
                profile = [(-h, 0.0), (-h, 1.0), (h, 1.0), (h, 0.0)]
                top = np.maximum(tz, (r.left[:, 2] if side > 0 else r.right[:, 2])) + C.WALL_HEIGHT[typ]
                self._extrude(mesh, line[idx], p["out"][idx], base[idx], top[idx], profile, C.WALL_MATERIAL[typ],
                              closed, r.s[idx])
            if not mesh.empty:
                meshes.append(mesh)
        return meshes

    # ------------------------------------------------------------------------- visible measured barriers
    def _sweep(self, mesh: Mesh, line: np.ndarray, out2: np.ndarray, z0: np.ndarray, s: np.ndarray,
               strips: list[tuple[tuple[float, float], tuple[float, float], str, tuple[float, float]]], mat: str,
               tile_u: float) -> None:
        """Strips along ``line``: each ((off_a, h_a), (off_b, h_b), facing, (v_a, v_b)) runs between two profile
        points (offset outwards from the line, height above z0) and faces ``road`` (-out), ``back`` (+out) or
        ``up``; u runs along the line every ``tile_u`` metres."""
        n = len(line)
        if n < 2:
            return
        for (oa, ha), (ob, hb), facing, (va, vb) in strips:
            A = [mesh.vert((line[i, 0] + out2[i, 0] * oa, line[i, 1] + out2[i, 1] * oa, z0[i] + ha)) for i in range(n)]
            B = [mesh.vert((line[i, 0] + out2[i, 0] * ob, line[i, 1] + out2[i, 1] * ob, z0[i] + hb)) for i in range(n)]
            for i in range(n - 1):
                u0, u1 = s[i] / tile_u, s[i + 1] / tile_u
                o = out2[i]
                outward = (np.array([-o[0], -o[1], 0.0]) if facing == "road" else
                           np.array([o[0], o[1], 0.0]) if facing == "back" else np.array([0.0, 0.0, 1.0]))
                mesh.oriented_face((A[i], A[i + 1], B[i + 1], B[i]), mat, outward,
                                   [(u0, va), (u1, va), (u1, vb), (u0, vb)])

    def _box_at(self, mesh: Mesh, c: np.ndarray, t: np.ndarray, o: np.ndarray, size: tuple[float, float],
                z0: float, z1: float, mat: str) -> None:
        """A small upright box (post) centred at c: ``size`` along t and o; four sides and the top."""
        a, b = size[0] / 2.0, size[1] / 2.0
        corners = [c - t * a - o * b, c + t * a - o * b, c + t * a + o * b, c - t * a + o * b]
        lo = [mesh.vert((q[0], q[1], z0)) for q in corners]
        hi = [mesh.vert((q[0], q[1], z1)) for q in corners]
        for k in range(4):
            k2 = (k + 1) % 4
            mid = (corners[k] + corners[k2]) / 2.0 - c
            mesh.oriented_face((lo[k], lo[k2], hi[k2], hi[k]), mat, np.array([mid[0], mid[1], 0.0]),
                               [(0.0, 0.0), (0.25, 0.0), (0.25, 1.0), (0.0, 1.0)])
        mesh.oriented_face(tuple(hi), mat, np.array([0.0, 0.0, 1.0]), [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)])

    @staticmethod
    def _smooth_barrier_line(xy: np.ndarray, z: np.ndarray, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """A visible barrier's line runs smoothly: plan position and height averaged over about
        BARRIER_LINE_SMOOTH_M (ends kept), the height within BARRIER_Z_FOLLOW of the ground below it, so the beams
        don't kink over every terrain step and zone join (Chris 2026-10-24, Anglesey's Corkscrew)."""
        n = len(xy)
        if n < 5:
            return xy, z
        step = max(float(np.median(np.diff(s))), 0.25)
        k = int(max(1, round(C.BARRIER_LINE_SMOOTH_M / step / 2)))
        k = min(k, (n - 1) // 2)
        ker = np.ones(2 * k + 1) / (2 * k + 1)

        def sm(v: np.ndarray) -> np.ndarray:
            p = np.pad(v, (k, k), mode="reflect", reflect_type="odd")  # straight on past the ends
            return np.convolve(p, ker, "valid")

        out = np.column_stack([sm(xy[:, 0]), sm(xy[:, 1])])
        out[0], out[-1] = xy[0], xy[-1]
        zs = np.clip(sm(z), z + C.BARRIER_Z_FOLLOW[0], z + C.BARRIER_Z_FOLLOW[1])
        return out, zs

    def build_barriers(self) -> list[Mesh]:
        """The visible measured barriers of an imported track (runoff.py), as stock bases build theirs: meshes on
        the wall line with stock textures, no collision of their own (the collision wall under them has the
        type's surface). Armco: two galvanised W-beams (prop_guardrail01) on posts every 2 m; tyres: a tyre wall
        faced with plain black conveyor belting (prop_tirerubberbarrier); concrete: a grey wall (a_concrete2);
        a debris fence (chain-link, prop_chainlinkfence04_small) on top where the zone has one. Split into
        objects of at most BARRIER_CHUNK samples so no draw batch nears the vertex limit."""
        if not self.runoff.active or self.walls == "none":
            return []
        meshes: list[Mesh] = []
        counter = 0
        for run in self.wall_runs():
            typ = run["type"]
            if typ not in C.BARRIER_MESH:
                continue
            P, O, S = run["points"], run["out"], run["s"]
            if run["closed"]:
                P, O, S = np.vstack([P, P[:1]]), np.vstack([O, O[:1]]), np.append(S, S[-1] + float(np.hypot(*(P[0, :2] - P[-1, :2]))))
            S = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(P[:, :2], axis=0).T))])
            F = np.asarray(run["fence"], dtype=bool)
            if run["closed"]:
                F = np.append(F, F[:1])
            h = C.WALL_HALF[typ]
            front, z0 = self._smooth_barrier_line(P[:, :2] - O * h, P[:, 2], S)  # the collision wall's road face
            spec = C.BARRIER_MESH[typ]
            for a in range(0, len(P) - 1, C.BARRIER_CHUNK):
                b = min(a + C.BARRIER_CHUNK + 1, len(P))
                m = Mesh(f"barrier_{typ}_{counter:03d}", "", kind="barrier")
                counter += 1
                L, Oa, Za, Sa = front[a:b], O[a:b], z0[a:b], S[a:b]
                if typ == "armco":
                    for lo_, hi_ in C.ARMCO_BEAMS:
                        self._sweep(m, L, Oa, Za, Sa, [((0.0, lo_), (0.0, hi_), "road", (0.0, 1.0)),
                                                       ((0.0, hi_), (0.12, hi_), "up", (0.95, 1.0)),
                                                       ((0.12, lo_), (0.12, hi_), "back", (0.0, 1.0))],
                                    spec["mat"], spec["tileU"])
                    # posts every ARMCO_POST_M behind the beams
                    seg = np.hypot(*np.diff(L, axis=0).T)
                    cum = np.concatenate([[0.0], np.cumsum(seg)])
                    for d in np.arange(0.5, cum[-1], C.ARMCO_POST_M):
                        k = int(np.clip(np.searchsorted(cum, d) - 1, 0, len(L) - 2))
                        f = (d - cum[k]) / max(seg[k], 1e-9)
                        c = L[k] + (L[k + 1] - L[k]) * f
                        t = (L[k + 1] - L[k]) / max(seg[k], 1e-9)
                        zc = Za[k] + (Za[k + 1] - Za[k]) * f
                        self._box_at(m, c + Oa[k] * 0.2, t, Oa[k], (0.12, 0.12), zc - 0.3, zc + C.ARMCO_BEAMS[-1][1] - 0.02,
                                     spec["mat"])
                else:
                    H, D = spec["height"], spec["depth"]
                    self._sweep(m, L, Oa, Za, Sa, [((0.0, -0.3), (0.0, H), "road", (0.0, 1.0)),
                                                   ((0.0, H), (D, H), "up", (0.0, 0.25)),
                                                   ((D, H), (D, -0.3), "back", (1.0, 0.0))],
                                spec["mat"], spec["tileU"])
                # debris fence on the barrier where the zone has one
                Fa = F[a:b]
                for fr in self._runs(Fa, False):
                    idx = np.array(fr)
                    top = C.ARMCO_BEAMS[-1][1] if typ == "armco" else spec["height"]
                    self._sweep(m, L[idx] + Oa[idx] * 0.15, Oa[idx], Za[idx], Sa[idx],
                                [((0.0, top), (0.0, top + C.FENCE_HEIGHT), "road", C.FENCE_V),
                                 ((0.0, top), (0.0, top + C.FENCE_HEIGHT), "back", C.FENCE_V)],
                                "barrier_fence", C.FENCE_TILE_U)
                if not m.empty:
                    meshes.append(m)
        return meshes

    # -------------------------------------------------------------------------------------- bridges
    def build_bridges(self) -> list[Mesh]:
        out = []
        for b in self.bridges:
            r = b.over
            deck = Mesh(f"bridge_{b.id}_deck", "col = true", kind="bridge")
            barriers = Mesh(f"bridge_{b.id}_barriers", "col = true", kind="bridge")
            pillars = Mesh(f"bridge_{b.id}_pillars", "col = true", kind="bridge")
            for run in self._runs(b.structure, r.closed):
                idx = np.array(run)
                L, R = r.left[idx], r.right[idx]
                Lb, Rb = L.copy(), R.copy()
                Lb[:, 2] -= C.BRIDGE_SLAB
                Rb[:, 2] -= C.BRIDGE_SLAB
                ids = [(deck.vert(L[k]), deck.vert(Lb[k]), deck.vert(Rb[k]), deck.vert(R[k])) for k in range(len(idx))]
                for k in range(len(idx) - 1):
                    a, c = ids[k], ids[k + 1]
                    nl = np.append(r.nl[idx[k]], 0.0)
                    deck.oriented_face((a[0], c[0], c[1], a[1]), "bridge_concrete", nl)
                    deck.oriented_face((a[3], c[3], c[2], a[2]), "bridge_concrete", -nl)
                    deck.oriented_face((a[1], c[1], c[2], a[2]), "bridge_concrete", np.array([0, 0, -1.0]))
                t0 = np.append(r.tangent[idx[0]], 0.0)
                deck.oriented_face(ids[0], "bridge_concrete", -t0)
                deck.oriented_face(ids[-1], "bridge_concrete", np.append(r.tangent[idx[-1]], 0.0))
                for side in (1, -1):
                    line = self._side_points(r, side, 0.3)[idx]
                    edge_z = (r.left if side > 0 else r.right)[idx, 2]
                    self._extrude(barriers, line, (r.nl * side)[idx], edge_z - C.BRIDGE_SLAB,
                                  edge_z + C.BRIDGE_BARRIER_H, [(-0.3, 0.0), (-0.15, 1.0), (0.15, 1.0), (0.3, 0.0)],
                                  "bridge_concrete", False, r.s[idx])
            # Pillars under both deck edges, outside the under road's walls, then every few metres
            # outwards while the deck is high enough.
            sin_a = max(math.sin(math.radians(b.angle_deg)), 0.3)
            d0 = (b.under.width / 2.0 + C.WALL_OFFSET + 1.5) / sin_a
            dist = r.signed_s(r.s, b.s_over)
            for sgn in (-1.0, 1.0):
                d = d0
                while True:
                    i = int(np.argmin(np.abs(dist - sgn * d)))
                    if not b.structure[i] or abs(dist[i] - sgn * d) > 3.0:
                        break
                    under_z = r.p[i, 2] - C.BRIDGE_SLAB
                    for side in (1, -1):
                        c = r.p[i].copy()
                        c[:2] += r.nl[i] * side * (r.w[i] / 2.0 - 1.0)
                        gz = float(self.terrain(c[None, :])[0])
                        if under_z - gz < 1.0:
                            continue
                        near = self.index.near_other(np.array([[c[0], c[1], gz]]), np.array([r.index]),
                                                     np.array([r.s[i]]), 1.0, zdiff=4.0)
                        if near[0]:
                            continue
                        self._box(pillars, c[:2], r.h[i], C.BRIDGE_PILLAR, gz - 0.5, under_z, "bridge_concrete")
                    d += C.BRIDGE_PILLAR_STEP
            for m in (deck, barriers, pillars):
                if not m.empty:
                    out.append(m)
        return out

    @staticmethod
    def _box(mesh: Mesh, c, heading: float, size: float, z0: float, z1: float, mat: str) -> None:
        ca, sa, h = math.cos(heading), math.sin(heading), size / 2.0
        corners = [(-h, -h), (h, -h), (h, h), (-h, h)]
        bot = [mesh.vert((c[0] + x * ca - y * sa, c[1] + x * sa + y * ca, z0)) for x, y in corners]
        top = [mesh.vert((c[0] + x * ca - y * sa, c[1] + x * sa + y * ca, z1)) for x, y in corners]
        for k in range(4):
            k2 = (k + 1) % 4
            mid = np.array([(corners[k][0] + corners[k2][0]) / 2, (corners[k][1] + corners[k2][1]) / 2])
            out = np.array([mid[0] * ca - mid[1] * sa, mid[0] * sa + mid[1] * ca, 0.0])
            mesh.oriented_face((bot[k], bot[k2], top[k2], top[k]), mat, out)
        mesh.oriented_face(tuple(top), mat, np.array([0, 0, 1.0]))

    # ---------------------------------------------------------------------------------------- jumps
    def build_layout_jumps(self, layout: str) -> list[Mesh]:
        """Jumps that are not part of the shared road surface (inBase false), for one layout only."""
        out = []
        for j in self.model.get("jumps", []):
            if j.get("inBase", True) or layout not in j.get("layouts", []):
                continue
            r = self.by_id[j["route"]]
            prof = np.array(j["profile"], dtype=np.float64)
            d = r.signed_s(r.s, float(j["s"]))
            sel = np.nonzero((d >= prof[0, 0]) & (d <= prof[-1, 0]))[0]
            if len(sel) < 2:
                continue
            sel = sel[np.argsort(d[sel])]
            dz = np.interp(d[sel], prof[:, 0], prof[:, 1]) + C.JUMP_LIFT
            mesh = Mesh(f"jump_{j['id']}", "col = true", kind="jump")
            tops, bots = [], []
            for k, i in enumerate(sel):
                cs = r.cross_section(i)
                tops.append([mesh.vert((p[0], p[1], p[2] + dz[k])) for p in cs])
                bots.append([mesh.vert((cs[0][0], cs[0][1], cs[0][2] - 0.1)),
                             mesh.vert((cs[-1][0], cs[-1][1], cs[-1][2] - 0.1))])
            tile = C.TEX_TILE["road"]
            mat = self.road_material(r, int(sel[0]))
            for k in range(len(sel) - 1):
                for jj in range(r.m):
                    ids = (tops[k][jj], tops[k][jj + 1], tops[k + 1][jj + 1], tops[k + 1][jj])
                    mesh.oriented_face(ids, mat, np.array([0, 0, 1.0]),
                                       [(jj / tile, d[sel[k]] / tile), ((jj + 1) / tile, d[sel[k]] / tile),
                                        ((jj + 1) / tile, d[sel[k + 1]] / tile), (jj / tile, d[sel[k + 1]] / tile)])
                for col, b, sgn in ((0, 0, 1), (r.m, 1, -1)):
                    out_dir = np.append(r.nl[sel[k]] * sgn, 0.0)
                    mesh.oriented_face((tops[k][col], tops[k + 1][col], bots[k + 1][b], bots[k][b]), mat, out_dir)
            out.append(mesh)
        return out

    # -------------------------------------------------------------------------------------- terrain
    def build_terrain(self) -> list[Mesh]:
        x0, x1, y0, y1 = self.extent
        cell = C.TERRAIN_CELL
        nx = max(1, math.ceil((x1 - x0) / cell))
        ny = max(1, math.ceil((y1 - y0) / cell))
        xs = x0 + np.arange(nx + 1) * (x1 - x0) / nx
        ys = y0 + np.arange(ny + 1) * (y1 - y0) / ny
        gx, gy = np.meshgrid(xs, ys, indexing="ij")
        pts = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
        z = self.terrain(pts).reshape(gx.shape)
        mat = "terrain"
        # measured run-off: faces whose centre lies in a gravel trap or on a paved apron get those materials
        face_mat = None
        if self.runoff.active:
            cxg, cyg = np.meshgrid((xs[:-1] + xs[1:]) / 2.0, (ys[:-1] + ys[1:]) / 2.0, indexing="ij")
            code = self.runoff.at(np.stack([cxg.ravel(), cyg.ravel()], axis=1)).reshape(cxg.shape)
            face_mat = np.array(["terrain", "terrain_trap", "terrain_apron"])[code]
        T = C.TERRAIN_TILE_CELLS
        tile = C.TEX_TILE["ground"]
        meshes = []
        for tx in range(0, nx, T):
            for ty in range(0, ny, T):
                mesh = Mesh(f"terrain_{tx // T:02d}_{ty // T:02d}", "col = true", kind="terrain")
                ids = {}
                for i in range(tx, min(tx + T, nx) + 1):
                    for j in range(ty, min(ty + T, ny) + 1):
                        ids[i, j] = mesh.vert((xs[i], ys[j], z[i, j]))
                for i in range(tx, min(tx + T, nx)):
                    for j in range(ty, min(ty + T, ny)):
                        q = (ids[i, j], ids[i + 1, j], ids[i + 1, j + 1], ids[i, j + 1])
                        mesh.face(q, mat if face_mat is None else str(face_mat[i, j]), [(xs[a] / tile, ys[b] / tile) for a, b in
                                           ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))])
                meshes.append(mesh)
        self.terrain_grid = (xs, ys, z)
        return meshes

    def build_base(self) -> list[Mesh]:
        meshes = [self.build_road()]
        kerbs, shoulders, limits = self.build_roadside()
        meshes += [m for m in (kerbs, limits, shoulders, self.build_edge_decals(), self.build_transition_decals())
                   if not m.empty]
        meshes += self.build_walls()
        meshes += self.build_barriers()
        meshes += self.build_bridges()
        if self.pits is not None:
            meshes += self.pits.meshes(self.road_material(self.main, 0))
        meshes += self.build_terrain()
        return meshes
