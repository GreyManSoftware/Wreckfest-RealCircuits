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
        self.tangent = np.stack([np.cos(self.h), np.sin(self.h)], axis=1)
        self.segs = self.n if self.closed else self.n - 1
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
        self.ri = np.concatenate([np.full(r.n, r.index) for r in routes])
        self.si = np.concatenate([np.arange(r.n) for r in routes])
        self.s = np.concatenate([r.s for r in routes])
        self.closed_len = np.concatenate([np.full(r.n, r.length if r.closed else np.inf) for r in routes])

    def near_other(self, pts: np.ndarray, own_ri: np.ndarray, own_s: np.ndarray, clearance: float,
                   zdiff: float = 3.0, mask: np.ndarray | None = None) -> np.ndarray:
        """True where a point lies within (half width + clearance) of a sample of *another* road: a
        different route, or the same route further than OTHER_ROUTE_S along it, within ``zdiff``."""
        out = np.zeros(len(pts), dtype=bool)
        sel = np.ones(len(self.xy), dtype=bool) if mask is None else mask
        xy, z, hw, ri, s, cl = (self.xy[sel], self.z[sel], self.hw[sel], self.ri[sel], self.s[sel],
                                self.closed_len[sel])
        for a in range(0, len(pts), 512):
            p = pts[a:a + 512]
            d = np.hypot(p[:, None, 0] - xy[None, :, 0], p[:, None, 1] - xy[None, :, 1])
            close = d < hw[None, :] + clearance
            if not close.any():
                continue
            sd = np.abs(s[None, :] - own_s[a:a + 512, None])
            sd = np.where(np.isfinite(cl)[None, :], np.minimum(sd, cl[None, :] - sd), sd)
            other = (ri[None, :] != own_ri[a:a + 512, None]) | (sd > C.OTHER_ROUTE_S)
            zok = np.abs(z[None, :] - p[:, None, 2]) < zdiff
            out[a:a + 512] = (close & other & zok).any(1)
        return out


class TerrainField:
    """Terrain height: the natural ground, pulled to just below the road near it.

    ``ground`` is trackgen_geom's seeded height field (the same one the road heights came from) when
    available. Near a road the terrain follows the lowest nearby road height minus TERRAIN_SINK, then
    blends to the ground over TERRAIN_BLEND. Samples on bridge structures don't pull the terrain up.
    """

    def __init__(self, index: SampleIndex, ground, influence: np.ndarray, extent: tuple[float, float, float, float]):
        self.ground = ground
        sel = influence
        self.xy = index.xy[sel]
        self.z = index.z[sel]
        self.hw = index.hw[sel]
        self.extent = extent  # min_x, max_x, min_y, max_y of the terrain

    def __call__(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float64)
        out = np.empty(len(pts))
        g = self.ground(pts[:, :2])
        reach = C.TERRAIN_CORE + C.TERRAIN_BLEND
        near_band = 3.0 + C.TERRAIN_CELL * 0.75
        for a in range(0, len(pts), 1024):
            p = pts[a:a + 1024]
            d = np.hypot(p[:, None, 0] - self.xy[None, :, 0], p[:, None, 1] - self.xy[None, :, 1])
            e = d - self.hw[None, :]
            k = np.argmin(e, axis=1)
            e_min = e[np.arange(len(p)), k]
            band = e <= (e_min[:, None] + near_band)
            zr = np.where(band, self.z[None, :], np.inf).min(1) - C.TERRAIN_SINK
            t = smoothstep((e_min - C.TERRAIN_CORE) / C.TERRAIN_BLEND)
            gz = g[a:a + 1024]
            zz = zr * (1.0 - t) + gz * t
            far = e_min > reach
            zz[far] = gz[far]
            out[a:a + 1024] = zz
        x0, x1, y0, y1 = self.extent
        edge = np.minimum.reduce([pts[:, 0] - x0, x1 - pts[:, 0], pts[:, 1] - y0, y1 - pts[:, 1]])
        out += C.BERM_H * smoothstep((C.BERM_W - edge) / C.BERM_W)
        return out


def make_ground(model: dict, routes: list[Route], warnings: list[str]):
    """The natural ground height function (x, y) -> z, from trackgen_geom when importable."""
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
        self.ground = make_ground(model, self.routes, self.warnings)
        walls = self.options.get("walls", "tyres")
        self.walls = walls if walls in ("concrete", "tyres") else "none"
        self.qkeep = {r.index: np.ones(r.segs, dtype=bool) for r in self.routes}
        self.svalid = {r.index: np.ones(r.n, dtype=bool) for r in self.routes}
        self.cuts: list[Cut] = []
        self.bridges: list[Bridge] = []
        self.structure = {r.index: np.zeros(r.n, dtype=bool) for r in self.routes}
        self.jump_zone = {r.index: np.zeros(r.n, dtype=bool) for r in self.routes}
        self.mouth_samples: dict[tuple[int, int], set[int]] = {}  # (route, side) -> samples under a stitch
        self._junctions()
        self._crossings()
        self._jumps()
        b = model["bounds"]
        margin = b["terrainMarginM"]
        lim = C.LIMIT_M - 1.0
        self.extent = (max(b["minX"] - margin, -lim), min(b["maxX"] + margin, lim),
                       max(b["minY"] - margin, -lim), min(b["maxY"] + margin, lim))
        influence = ~np.concatenate([self.structure[r.index] for r in self.routes])
        self.terrain = TerrainField(self.index, self.ground, influence, self.extent)

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
            if alt.start_join:
                t = self.by_id[alt.start_join["route"]]
                k = self._mouth(alt, t, int(alt.start_join["sampleIndex"]), True)
                if k is None:
                    self.warnings.append(f"junction-unresolved: {alt.id} start overlaps {t.id}")
                else:
                    k0 = k
                    self.cuts.append(Cut(alt, k, t, int(alt.start_join["sampleIndex"])))
            if alt.end_join:
                t = self.by_id[alt.end_join["route"]]
                k = self._mouth(alt, t, int(alt.end_join["sampleIndex"]), False)
                if k is None:
                    self.warnings.append(f"junction-unresolved: {alt.id} end overlaps {t.id}")
                else:
                    k1 = k
                    self.cuts.append(Cut(alt, k, t, int(alt.end_join["sampleIndex"])))
            q = self.qkeep[alt.index]
            q[:] = False
            q[k0:k1] = True
            sv = self.svalid[alt.index]
            sv[:] = False
            sv[k0:k1 + 1] = True

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
                for j in range(r.m):
                    u0 = j * r.width / r.m / tile
                    u1 = (j + 1) * r.width / r.m / tile
                    mesh.face((vid(r, i, j), vid(r, i, j + 1), vid(r, i2, j + 1), vid(r, i2, j)), mat,
                              [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
        self.road_quads = len(mesh.faces)
        for cut in self.cuts:
            self._stitch(mesh, vid, cut)
        self.road_vids = vids
        return mesh

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
        # Extend this road's two edge lines back to the target's edge: the mouth spans the target-edge
        # vertices between the two hits, so the corners between the roads are filled too.
        away = r.tangent[cut.sample] * (1.0 if np.dot(r.tangent[cut.sample], c - wpts[jc]) >= 0 else -1.0)
        ends = []
        for p0 in (cross[0, :2], cross[-1, :2]):
            hit = _ray_polyline(p0, -away, edge, 40.0)
            ends.append(hit if hit is not None else float(np.argmin(np.hypot(*(wpts - p0).T))))
        ja, jb = ends
        lo, hi = math.floor(min(ja, jb)), math.ceil(max(ja, jb))
        lo, hi = max(0, lo), min(len(win) - 1, hi)
        if hi - lo < 1:
            lo, hi = max(0, lo - 1), min(len(win) - 1, hi + 1)
        edge_ids = [vid(t, win[k], col) for k in range(lo, hi + 1)]
        blocked = self.mouth_samples.setdefault((t.index, 1 if side > 0 else -1), set())
        blocked.update(win[k] for k in range(max(0, lo - 1), min(len(win), hi + 2)))
        tail = cross_ids[::-1] if jb >= ja else cross_ids
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

    def _edge_ok(self, r: Route, pts: np.ndarray, clearance: float) -> np.ndarray:
        own_ri = np.full(len(pts), r.index)
        bad = self.index.near_other(pts, own_ri, r.s, clearance)
        return ~bad & self.svalid[r.index] & ~self.structure[r.index]

    def kerb_widths(self, r: Route, side: int) -> np.ndarray:
        if not self.options.get("kerbs", False):
            return np.zeros(r.n)
        want = np.abs(r.curvature) > C.KERB_MIN_CURVATURE
        want &= ~self.jump_zone[r.index]
        if want.any():
            k = np.ones(2 * C.KERB_DILATE + 1)
            if r.closed:
                pad = np.concatenate([want[-C.KERB_DILATE:], want, want[:C.KERB_DILATE]])
                want = np.convolve(pad.astype(float), k, mode="valid") > 0
            else:
                want = np.convolve(want.astype(float), k, mode="same") > 0
        outer = self._side_points(r, side, C.KERB_W + C.SHOULDER_W)
        want &= self._edge_ok(r, outer, 0.3)
        # Taper to zero over KERB_TAPER samples at each end of a kerbed run.
        dist = np.full(r.n, 10 ** 6, dtype=np.float64)
        idx = np.nonzero(~want)[0]
        if len(idx) == 0:
            return np.full(r.n, C.KERB_W)
        for i in range(r.n):
            dd = np.abs(idx - i)
            if r.closed:
                dd = np.minimum(dd, r.n - dd)
            dist[i] = dd.min()
        return C.KERB_W * np.clip(dist / C.KERB_TAPER, 0.0, 1.0) * want

    def build_kerbs_and_shoulders(self) -> tuple[Mesh, Mesh]:
        kerbs = Mesh("track_kerbs", "col = true", kind="kerb")
        shoulders = Mesh("track_shoulders", "col = true", kind="shoulder")
        none = self.walls == "none"
        sw = C.RUNOFF_W if none else C.SHOULDER_W
        drop = C.RUNOFF_DROP if none else C.SHOULDER_DROP
        # Same textures as the terrain (and the same world-space UV1), so the verge blends into it seamlessly;
        # only the surface (gravel) differs.
        smat = "shoulder"
        self.kerb_runs: dict[str, np.ndarray] = {}
        self.edge_ok: dict[tuple[int, int], np.ndarray] = {}  # (route, side) -> samples with a plain edge
        for r in self.routes:
            q = self.qkeep[r.index]
            seg = r.seg_len()
            for side in (1, -1):
                kw = self.kerb_widths(r, side)
                self.kerb_runs[f"{r.id}:{'l' if side > 0 else 'r'}"] = kw
                edge = r.left if side > 0 else r.right
                inner = self._side_points(r, side, kw)
                inner[:, 2] = edge[:, 2] + C.KERB_RISE * (kw / C.KERB_W)
                outer = self._side_points(r, side, kw + sw)
                outer[:, 2] = np.minimum(inner[:, 2] - drop, self.terrain(outer) - 0.3)
                ok = self._edge_ok(r, outer, 0.3) & self._edge_ok(r, inner, 0.3)
                for i in self.mouth_samples.get((r.index, side), ()):
                    ok[i] = False
                self.edge_ok[(r.index, side)] = ok & (kw <= 0)
                kv: dict[int, tuple[int, int]] = {}
                sv: dict[int, tuple[int, int]] = {}
                for i in range(r.segs):
                    i2 = r.nxt(i)
                    if not (q[i] and ok[i] and ok[i2]):
                        continue
                    v0, v1 = r.s[i], r.s[i] + seg[i]
                    if kw[i] > 0 or kw[i2] > 0:
                        for k in (i, i2):
                            if k not in kv:
                                e = kerbs.vert(edge[k])
                                kv[k] = (e, kerbs.vert(inner[k]) if kw[k] > 0 else e)
                        t = C.TEX_TILE["kerb"]
                        ids = (kv[i][0], kv[i2][0], kv[i2][1], kv[i][1])
                        uv = [(0, v0 / t), (0, v1 / t), (kw[i2] / t, v1 / t), (kw[i] / t, v0 / t)]
                        if side < 0:
                            ids, uv = ids[::-1], uv[::-1]
                        kerbs.face(ids, "kerb", uv)
                    for k in (i, i2):
                        if k not in sv:
                            sv[k] = (shoulders.vert(inner[k]), shoulders.vert(outer[k]))
                    ids = (sv[i][0], sv[i2][0], sv[i2][1], sv[i][1])
                    if side < 0:
                        ids = ids[::-1]
                    shoulders.face(ids, smat)  # world-space UV1, as the terrain
        return kerbs, shoulders

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
                        f = min(-off / max(r.width, 1e-6), 1.0)
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

    def build_walls(self) -> list[Mesh]:
        if self.walls == "none":
            return []
        mat = C.WALL_MATERIAL[self.walls]
        height = C.WALL_HEIGHT[self.walls]
        if self.walls == "concrete":
            profile = [(-0.35, 0.0), (-0.12, 1.0), (0.12, 1.0), (0.35, 0.0)]
        else:
            profile = [(-0.4, 0.0), (-0.45, 0.55), (-0.3, 1.0), (0.3, 1.0), (0.45, 0.55), (0.4, 0.0)]
        meshes = []
        for r in self.routes:
            for side in (1, -1):
                name = f"walls_{r.id}_{'l' if side > 0 else 'r'}"
                mesh = Mesh(name, "col = true", kind="wall")
                line = self._side_points(r, side, C.WALL_OFFSET)
                ok = self._edge_ok(r, line, C.WALL_OFFSET + 0.5)
                # Drop folds: on the inside of a tight corner the offset line can run backwards.
                nxt = np.roll(line[:, :2], -1, axis=0) if r.closed else np.vstack([line[1:, :2], line[-1:, :2]])
                prv = np.roll(line[:, :2], 1, axis=0) if r.closed else np.vstack([line[:1, :2], line[:-1, :2]])
                tan = nxt - prv
                ok &= (tan * r.tangent).sum(1) > 0.2 * np.maximum(np.linalg.norm(tan, axis=1), 1e-9)
                tz = self.terrain(line)
                base = tz - C.WALL_SINK
                top = np.maximum(tz, (r.left[:, 2] if side > 0 else r.right[:, 2])) + height
                out2 = r.nl * side
                for run in self._runs(ok, r.closed):
                    idx = np.array(run)
                    closed = r.closed and len(run) == r.n
                    self._extrude(mesh, line[idx], out2[idx], base[idx], top[idx], profile, mat, closed, r.s[idx])
                if not mesh.empty:
                    meshes.append(mesh)
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
                        mesh.face(q, mat, [(xs[a] / tile, ys[b] / tile) for a, b in
                                           ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))])
                meshes.append(mesh)
        self.terrain_grid = (xs, ys, z)
        return meshes

    def build_base(self) -> list[Mesh]:
        meshes = [self.build_road()]
        kerbs, shoulders = self.build_kerbs_and_shoulders()
        meshes += [m for m in (kerbs, shoulders, self.build_edge_decals()) if not m.empty]
        meshes += self.build_walls()
        meshes += self.build_bridges()
        meshes += self.build_terrain()
        return meshes
