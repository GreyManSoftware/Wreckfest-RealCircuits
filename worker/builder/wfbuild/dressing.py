"""Trackside dressing and scenery placement (pure numpy, no bpy): crowds, grandstands, start/finish gantry,
flags, paddock, ad fences, corner markers, lights, foliage, rocks and buildings, chosen per environment from
``environments.json`` and placed deterministically (seeded by the slug).

The result is a placement manifest (``Scenery.to_json``, written as ``scenery.json``; format in
``worker/docs/scenery-manifest.md``) in TrackModel coordinates. The Blender builder turns each placement into a
``#xref ob/<asset>.scne`` subscene; the web editor's 3D preview can read the same file.

    python -m wfbuild.dressing --spec spec.json --model model.json --out scenery.json

Conventions: metres, x east, y north, z up. ``yaw`` is radians counter-clockwise about +z; at yaw 0 an asset's
local +Y points north (+y). Each asset's road-facing axis is in the asset table (``face``): standing spectators
face the road with local +Y, grandstands, tents and ad fences with local -Y, and 'x' assets (gantry, trucks,
containers) run along the road with local Y along the driving direction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import config as C
from . import envs
from .fields import DistanceField
from .noise import Noise2D, seed_for

FORMAT = "trackgen-scenery/1"


def yaw_to(d) -> float:
    """Yaw that turns local +Y towards direction d (x, y)."""
    return math.atan2(-float(d[0]), float(d[1]))


@dataclass
class Placement:
    asset: str
    category: str
    x: float
    y: float
    z: float = 0.0
    yaw: float = 0.0
    scale: float = 1.0

    def to_json(self) -> dict:
        return {"asset": self.asset, "category": self.category,
                "position": [round(self.x, 3), round(self.y, 3), round(self.z, 3)],
                "yaw": round(self.yaw, 4), "scale": round(self.scale, 3)}


@dataclass
class Zone:
    """A circle of trampled ground (dirt in the blend map, no clutter grass): crowds, paddock, buildings."""
    kind: str
    x: float
    y: float
    r: float


@dataclass
class Scenery:
    slug: str
    environment: str
    placements: list[Placement] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    nogo: list[dict] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    def counts(self) -> dict:
        out: dict[str, int] = {}
        for p in self.placements:
            out[p.category] = out.get(p.category, 0) + 1
        return dict(sorted(out.items()))

    def to_json(self, transform: dict | None = None, backdrop: dict | None = None) -> dict:
        assets = sorted({p.asset for p in self.placements})
        return {
            "format": FORMAT,
            "slug": self.slug,
            "environment": self.environment,
            "coordinates": "TrackModel metres (x east, y north, z up); yaw radians CCW about +z, 0 = local +Y north",
            "transform": transform or {"rotateDeg": 0, "shift": [0.0, 0.0, 0.0],
                                       "note": "scene = rotate(model, rotateDeg about +z) + shift"},
            "assets": {a: {"path": f"data/art/objects/{a}.scne", **{k: v for k, v in envs.asset_info(a).items()}}
                       for a in assets},
            "counts": self.counts(),
            "total": len(self.placements),
            "placements": [p.to_json() for p in self.placements],
            "zones": [{"kind": z.kind, "position": [round(z.x, 2), round(z.y, 2)], "r": round(z.r, 2)} for z in self.zones],
            "noGo": self.nogo,
            "backdrop": backdrop or {},
            "info": self.info,
        }


class Occupancy:
    """Spatial hash of placed footprints (discs)."""

    def __init__(self, cell: float = 16.0):
        self.cell = cell
        self.cells: dict[tuple[int, int], list[tuple[float, float, float]]] = {}

    def free(self, x: float, y: float, r: float, slack: float = 0.95) -> bool:
        c = self.cell
        reach = int(math.ceil((r + 45.0) / c))
        ci, cj = int(math.floor(x / c)), int(math.floor(y / c))
        for i in range(ci - reach, ci + reach + 1):
            for j in range(cj - reach, cj + reach + 1):
                for (px, py, pr) in self.cells.get((i, j), ()):
                    if (px - x) ** 2 + (py - y) ** 2 < ((pr + r) * slack) ** 2:
                        return False
        return True

    def add(self, x: float, y: float, r: float) -> None:
        self.cells.setdefault((int(math.floor(x / self.cell)), int(math.floor(y / self.cell))), []).append(
            (x, y, min(r, 45.0)))


class Parcels:
    """Farm field parcels: a rotated grid of 120-200 m cells; a share of them are fields (blend map B, crop tint in
    the colour map, hedgerow trees along their borders). Shared by maps.py and the planner."""

    def __init__(self, seed: int):
        rng = np.random.default_rng(seed)
        self.a = rng.uniform(0, math.pi / 2)
        self.w = rng.uniform(120, 200)
        self.h = rng.uniform(90, 160)
        self.o = rng.uniform(0, 1000, 2)
        self.salt = int(rng.integers(1, 2 ** 31 - 1))

    def local(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        ca, sa = math.cos(self.a), math.sin(self.a)
        u = xy[:, 0] * ca + xy[:, 1] * sa + self.o[0]
        v = -xy[:, 0] * sa + xy[:, 1] * ca + self.o[1]
        return u / self.w, v / self.h

    def ids(self, xy: np.ndarray) -> np.ndarray:
        u, v = self.local(xy)
        i, j = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
        return (i * 73856093) ^ (j * 19349663) ^ self.salt

    def is_field(self, xy: np.ndarray) -> np.ndarray:
        return (self.ids(xy) % 100) < 55

    def tint(self, xy: np.ndarray) -> np.ndarray:
        return (self.ids(xy) // 100) % 3

    def border(self, xy: np.ndarray) -> np.ndarray:
        """Distance (metres) to the nearest parcel border."""
        u, v = self.local(xy)
        du = np.minimum(u - np.floor(u), np.ceil(u) - u) * self.w
        dv = np.minimum(v - np.floor(v), np.ceil(v) - v) * self.h
        return np.minimum(du, dv)


class Planner:
    def __init__(self, geo, field_: DistanceField | None = None):
        self.geo = geo
        self.slug = geo.model["slug"]
        self.field = field_ or DistanceField(geo)
        self.env_name = envs.env_name(geo.options.get("environment"))
        self.e = envs.env(self.env_name)
        self.common = envs.table()["common"]
        self.allowed = envs.allowed_assets(self.env_name)
        self.rng = np.random.default_rng(seed_for(self.slug, "scenery"))
        x0, x1, y0, y1 = geo.extent
        self.bounds = (x0, x1, y0, y1)
        self.frame = (x0, y0, max(x1 - x0, y1 - y0))
        self.walls = geo.walls != "none"
        # Nothing but the gantry, the barrier rows and corner markers may stand closer to a road edge than this
        # (behind the set-back barriers / beyond the run-off).
        self.edge_min = (C.WALL_SETBACK + 2.5) if self.walls else (C.RUNOFF_W + 2.0)
        # corner markers (dynamic tyre stacks, bales) stand just in front of the near barrier, or past the run-off
        self.marker_off = (C.WALL_OFFSET - 1.2) if self.walls else (C.RUNOFF_W + 1.0)
        self.fol = envs.foliage(self.env_name)
        self.occ = Occupancy()
        self.sc = Scenery(self.slug, self.env_name)
        self.tree_noise = Noise2D(seed_for(self.slug, "trees"), self.frame, 260.0, 3)
        cov = float(self.e.get("treeDensity", 0.1))
        self.tree_thr = float(np.quantile(self.tree_noise.g, 1.0 - min(max(cov, 0.0), 1.0))) if cov > 0 else 2.0
        self.parcels = Parcels(seed_for(self.slug, "parcels")) if self.e.get("fields") else None
        self.main = geo.main
        self._nogo()

    # ------------------------------------------------------------------------------------- no-go zones
    def _nogo(self) -> None:
        """Discs where nothing may stand: bridge decks and ramps, jump take-offs and landings, and (except the
        start/finish gantry, flags, staff and grandstand) the grid sector."""
        g = self.geo
        xs, rs, ex, kinds = [], [], [], []

        def add(p, r, exempt, kind):
            xs.append((float(p[0]), float(p[1])))
            rs.append(float(r))
            ex.append(exempt)
            kinds.append(kind)

        for b in g.bridges:
            r = b.over
            for i in np.nonzero(b.structure)[0][::2]:
                add(r.p[i], r.w[i] / 2.0 + 25.0, False, "bridge")
        for j in g.model.get("jumps", []):
            r = g.by_id[j["route"]]
            d = r.signed_s(r.s, float(j["s"]))
            lo, hi = float(j["startS"]) - float(j["s"]), float(j["endS"]) - float(j["s"]) + 40.0
            for i in np.nonzero((d >= lo) & (d <= hi))[0][::2]:
                add(r.p[i], r.w[i] / 2.0 + self.edge_min + 10.0, False, "jump")
        slots = g.model.get("grid", {}).get("slots", [])
        m = self.main
        if slots:
            s_hi = max(float(s["s"]) for s in slots) + 15.0
            s_lo = -25.0
            d = m.signed_s(m.s, 0.0)
            for i in np.nonzero((d >= s_lo) & (d <= s_hi))[0][::2]:
                add(m.p[i], m.w[i] / 2.0 + self.edge_min + 8.0, True, "grid")
        self.nogo_xy = np.array(xs).reshape(-1, 2)
        self.nogo_r = np.array(rs)
        self.nogo_exempt = np.array(ex, dtype=bool)
        # compact summary for the manifest (merged per kind as circles is too long; keep every 4th disc)
        self.sc.nogo = [{"kind": k, "position": [round(p[0], 1), round(p[1], 1)], "r": round(r, 1)}
                        for n, (p, r, k) in enumerate(zip(xs, rs, kinds)) if n % 4 == 0]

    def in_nogo(self, xy: np.ndarray, r: np.ndarray | float, exempt_ok: bool) -> np.ndarray:
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        if not len(self.nogo_xy):
            return np.zeros(len(xy), dtype=bool)
        rr = np.broadcast_to(np.asarray(r, dtype=np.float64), (len(xy),))
        out = np.zeros(len(xy), dtype=bool)
        sel = ~self.nogo_exempt if exempt_ok else np.ones(len(self.nogo_xy), dtype=bool)
        nx, nr = self.nogo_xy[sel], self.nogo_r[sel]
        if not len(nx):
            return out
        for a in range(0, len(xy), 512):
            p = xy[a:a + 512]
            d = np.hypot(p[:, None, 0] - nx[None, :, 0], p[:, None, 1] - nx[None, :, 1])
            out[a:a + 512] = (d < nr[None, :] + rr[a:a + 512, None]).any(1)
        return out

    def inside(self, x: float, y: float, margin: float) -> bool:
        x0, x1, y0, y1 = self.bounds
        return x0 + margin <= x <= x1 - margin and y0 + margin <= y <= y1 - margin

    # ---------------------------------------------------------------------------------------- placing
    def try_place(self, asset: str, category: str, x: float, y: float, yaw: float, min_edge: float,
                  exempt: bool = False, r: float | None = None, scale: float = 1.0, margin: float = 6.0,
                  occupy: bool = True) -> bool:
        if asset not in self.allowed:
            raise ValueError(f"{asset} is not allowed in {self.env_name}")
        info = envs.asset_info(asset)
        rr = float(info["r"] if r is None else r) * scale
        # Long, thin assets laid along the road (fences, grandstands) only reach towards it by half their depth.
        edge_r = rr
        if "width" in info and info.get("face") != "x":
            edge_r = float(info.get("depth", 1.0)) / 2.0 * scale
        if not self.inside(x, y, margin + rr):
            return False
        if float(self.field.dist([x, y])[0]) - edge_r < min_edge:
            return False
        if self.in_nogo([x, y], rr, exempt)[0]:
            return False
        if not self.occ.free(x, y, rr):
            return False
        if occupy:
            self.occ.add(x, y, rr)
        self.sc.placements.append(Placement(asset, category, float(x), float(y), 0.0, float(yaw), scale))
        return True

    def face_yaw(self, asset: str, toward_road, tangent) -> float:
        f = envs.asset_info(asset).get("face", "+y")
        if f == "+y":
            return yaw_to(toward_road)
        if f == "-y":
            return yaw_to((-toward_road[0], -toward_road[1]))
        return yaw_to(tangent)

    def side_point(self, r, i: int, side: int, off: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Point ``off`` metres outside the road edge on ``side``; returns (point, unit towards the road, tangent)."""
        n = r.nl[i] * side
        p = r.p[i, :2] + n * (r.w[i] / 2.0 + off)
        return p, -n, r.tangent[i]

    def pick(self, items: list[str]) -> str:
        return items[int(self.rng.integers(len(items)))]

    def walk(self, r, i0: int, length: float, step: float) -> list[int]:
        """Sample indices along route r from i0 - length/2 to i0 + length/2, about ``step`` metres apart."""
        d = r.signed_s(r.s, float(r.s[i0]))
        out, last = [], None
        for i in np.argsort(d):
            if abs(d[i]) > length / 2.0:
                continue
            if last is None or d[i] - last >= step - 1e-6:
                out.append(int(i))
                last = d[i]
        return out

    # -------------------------------------------------------------------------------------- features
    def corners(self) -> list[tuple[int, int, float]]:
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

    def straights(self) -> list[tuple[int, float]]:
        """Main-route straights (middle index, length), longest first."""
        r = self.main
        flat = np.abs(r.curvature) < 1.0 / 400.0
        if flat.all():
            return [(0, r.length)]
        n = r.n
        start = int(np.nonzero(~flat)[0][0])
        runs, cur = [], []
        for k in range(n + 1):
            i = (start + k) % n
            if k < n and flat[i]:
                cur.append(i)
            elif cur:
                runs.append(cur)
                cur = []
        seg = float(np.median(r.seg_len()))
        out = [(run[len(run) // 2], len(run) * seg) for run in runs if len(run) * seg >= 60.0]
        return sorted(out, key=lambda t: -t[1])

    # ----------------------------------------------------------------------------------- start/finish
    def start_finish(self) -> int:
        r = self.main
        i0 = 0  # the start/finish line is at s = 0 (layout.py)
        p, t = r.p[i0, :2], r.tangent[i0]
        hw = r.w[i0] / 2.0
        scale = max(1.0, (hw + C.WALL_OFFSET + 1.5) / 16.0)
        legs = np.array([p + r.nl[i0] * 16.35 * scale, p - r.nl[i0] * 16.35 * scale])
        if (self.field.dist(legs) > 1.5).all():
            # spans the road (the only asset allowed on it); exempt from the road clearance
            self.sc.placements.append(Placement("prop_start_startlights1", "gantry", float(p[0]), float(p[1]), 0.0,
                                                yaw_to(t), scale))
        room = []
        for side in (1, -1):
            q, _, _ = self.side_point(r, i0, side, 60.0)
            room.append((float(self.field.dist(q)[0]), side))
        room.sort(reverse=True)
        g_side = room[0][1]
        placed = False
        for asset in self.common["grandstand"]:
            info = envs.asset_info(asset)
            depth = float(info.get("depth", 8.0))
            for shift in (12.0, 30.0, -10.0, 50.0):
                i = r.index_at((r.s[i0] + shift) % r.length)
                q, tw, tg = self.side_point(r, i, g_side, self.edge_min + 4.0 + depth / 2.0)
                # its whole footprint must stay behind the clearance
                ends = np.array([q + tg * info["width"] / 2.0, q - tg * info["width"] / 2.0])
                if (self.field.dist(ends) < self.edge_min + 2.0).any():
                    continue
                if self.try_place(asset, "grandstand", q[0], q[1], self.face_yaw(asset, tw, tg),
                                  self.edge_min + 1.0, exempt=True, r=info["width"] / 2.0):
                    self.sc.zones.append(Zone("crowd", float(q[0]), float(q[1]), info["width"] / 2.0 + 4.0))
                    placed = True
                    break
            if placed:
                break
        for along in (-22.0, -14.0, -6.0, 6.0, 14.0, 22.0):
            i = r.index_at((r.s[i0] + along) % r.length)
            for side in (1, -1):
                q, tw, tg = self.side_point(r, i, side, self.edge_min + 1.5)
                a = self.common["flags"][0] if abs(along) < 15 else self.pick(self.common["flags"])
                self.try_place(a, "flags", q[0], q[1], self.face_yaw(a, tw, tg), self.edge_min, exempt=True)
        for k in range(4):
            i = r.index_at((r.s[i0] - 8.0 + 5.0 * k) % r.length)
            q, tw, tg = self.side_point(r, i, -g_side, self.edge_min + 1.2 + 0.8 * (k % 2))
            a = self.pick(self.common["staff"])
            self.try_place(a, "staff", q[0], q[1], self.face_yaw(a, tw, tg), self.edge_min - 1.0, exempt=True)
        return -g_side

    def paddock(self, side: int) -> None:
        r = self.main
        rows = [(36.0, ["prop_start_tent8", "prop_hightent_03", "prop_hightent_05", "prop_tent_star_01",
                        "prop_long_tent_equipment_01"]),
                (58.0, ["vehicle_t2_crewbus01", "vehicle_t2_cargotruck01", "prop_car_truck", "prop_trailer2"]),
                (82.0, ["prop_start_tent4", "prop_trailer2", "vehicle_t2_cargotruck01"])]
        for s_off in range(-60, 120, 24):
            i = r.index_at((r.s[0] + s_off) % r.length)
            for off, assets in rows:
                for sd in ((side,) if side else (1, -1)):
                    q, tw, tg = self.side_point(r, i, sd, off + float(self.rng.uniform(-3, 3)))
                    a = self.pick(assets)
                    if self.try_place(a, "paddock", q[0], q[1], self.face_yaw(a, tw, tg), 25.0):
                        self.sc.zones.append(Zone("paddock", float(q[0]), float(q[1]), envs.asset_info(a)["r"] + 6.0))
                    small = self.pick(self.common["paddockSmall"])
                    q2 = q + tg * 9.0
                    if small == "prop_portable_toilet":
                        for k in range(4):
                            qq = q2 + tg * (1.1 * k)
                            self.try_place(small, "paddock", qq[0], qq[1], self.face_yaw(small, tw, tg), 25.0)
                    else:
                        self.try_place(small, "paddock", q2[0], q2[1], self.face_yaw(small, tw, tg), 25.0)

    def lights(self) -> None:
        r = self.main
        for s_off in range(-200, 201, 70):
            i = r.index_at((r.s[0] + s_off) % r.length)
            for side in (1, -1):
                q, tw, tg = self.side_point(r, i, side, self.edge_min + 8.0)
                a = self.common["lights"][0]
                self.try_place(a, "lights", q[0], q[1], self.face_yaw(a, tw, tg), self.edge_min + 4.0)

    # ------------------------------------------------------------------------------------------ crowds
    def crowd(self, r, i: int, side: int, length: float) -> int:
        n0 = len(self.sc.placements)
        fence = self.e["crowdFence"]
        fw = float(envs.asset_info(fence).get("width", 4.0))
        off_f = self.edge_min + 5.0
        for k in self.walk(r, i, length + 6.0, fw):
            q, tw, tg = self.side_point(r, k, side, off_f)
            self.try_place(fence, "crowdFence", q[0], q[1], self.face_yaw(fence, tw, tg), off_f - 1.0, r=fw / 2.0)
        fills = (0.8, 0.6, 0.4)
        for row in range(3):
            for k in self.walk(r, i, length, 1.25):
                if self.rng.random() > fills[row]:
                    continue
                q, tw, tg = self.side_point(r, k, side, off_f + 1.6 + 1.4 * row + float(self.rng.uniform(-0.3, 0.3)))
                q = q + tg * float(self.rng.uniform(-0.35, 0.35))
                a = self.pick(self.common["spectators"])
                self.try_place(a, "spectators", q[0], q[1], yaw_to(tw) + float(self.rng.normal(0, 0.25)),
                               off_f + 0.5, r=0.55)
        if self.rng.random() < 0.35:
            q, tw, tg = self.side_point(r, i, side, off_f + 7.5)
            a = self.pick(self.common["cameras"])
            self.try_place(a, "cameras", q[0], q[1], self.face_yaw(a, tw, tg), off_f)
        q, _, _ = self.side_point(r, i, side, off_f + 3.0)
        if len(self.sc.placements) > n0:
            self.sc.zones.append(Zone("crowd", float(q[0]), float(q[1]), length / 2.0 + 4.0))
        return len(self.sc.placements) - n0

    def viewing_spots(self, corners) -> None:
        g = self.geo
        spots: list[tuple[object, int, int, float]] = []
        for i, side, k in corners:
            spots.append((self.main, i, side, 30.0 + min(20.0, k * 2000.0)))
        for j in g.model.get("jumps", []):
            r = g.by_id[j["route"]]
            i = r.index_at((float(j["endS"]) + 25.0) % r.length if r.closed else min(float(j["endS"]) + 25.0, r.length))
            spots.insert(0, (r, i, 1, 30.0))
            spots.insert(1, (r, i, -1, 30.0))
        for b in g.bridges:
            u = b.under
            i = u.index_at(b.s_under)
            for off in (-45.0, 45.0):
                k = u.index_at((b.s_under + off) % u.length if u.closed else min(max(b.s_under + off, 0.0), u.length))
                spots.insert(0, (u, k, 1 if off > 0 else -1, 24.0))
            del i
        n_max = int(min(14, max(4, round(self.main.length / 260.0)))) + len(g.bridges) * 2 + len(g.model.get("jumps", [])) * 2
        done = 0
        for r, i, side, length in spots:
            if done >= n_max:
                break
            if self.crowd(r, i, side, length) > 4:
                done += 1
        self.sc.info["viewingSpots"] = done

    # ------------------------------------------------------------------------------- road-side props
    def ad_fences(self, straights) -> None:
        r = self.main
        n = 0
        for k, (i, length) in enumerate(straights[:6]):
            side = 1 if k % 2 == 0 else -1
            run = min(length - 10.0, 80.0)
            for j in self.walk(r, i, run, 3.84):
                q, tw, tg = self.side_point(r, j, side, self.edge_min + 1.0)
                a = self.common["adFence"][(n // 6) % len(self.common["adFence"])]
                if self.try_place(a, "adFence", q[0], q[1], self.face_yaw(a, tw, tg), self.edge_min - 0.5, r=1.92):
                    n += 1
            if k < 2:
                q, tw, tg = self.side_point(r, i, -side, self.edge_min + 14.0)
                a = self.pick(self.common["bigBoard"])
                self.try_place(a, "bigBoard", q[0], q[1], self.face_yaw(a, tw, tg), self.edge_min + 8.0)

    def corner_markers(self, corners) -> None:
        r = self.main
        markers = self.e["cornerMarkers"]
        off = self.marker_off
        for i, side, _k in corners[:12]:
            for j in self.walk(r, i, 36.0, 2.6):
                q, tw, tg = self.side_point(r, j, side, off)
                a = self.pick(markers)
                self.try_place(a, "cornerMarkers", q[0], q[1], self.face_yaw(a, tw, tg), off - 1.0, r=0.6)
            for j in self.walk(r, i, 8.0, 2.6):
                q, tw, tg = self.side_point(r, j, -side, off)
                a = self.pick(markers)
                self.try_place(a, "cornerMarkers", q[0], q[1], self.face_yaw(a, tw, tg), off - 1.0, r=0.6)

    def barriers(self) -> None:
        """The visible containment: a row of the environment's stock barrier props (concrete blocks, muddy
        barriers, tyre walls; environments.json "barriers") along the wall line of the geometry's wall plan. Each
        prop's road-facing side sits on the invisible collision wall's face; local +Y points away from the road."""
        row = envs.barrier_row(self.env_name, self.geo.walls)
        if not row:
            return
        half = C.WALL_HALF.get(self.geo.walls, 0.35)
        for run in self.geo.wall_runs():
            P = np.asarray(run["points"], dtype=np.float64)[:, :2]
            if run["closed"]:
                P = np.vstack([P, P[:1]])
            seg = np.hypot(*np.diff(P, axis=0).T)
            cum = np.concatenate([[0.0], np.cumsum(seg)])
            L = float(cum[-1])
            at = lambda d: np.array([np.interp(d, cum, P[:, 0]), np.interp(d, cum, P[:, 1])])  # noqa: E731
            pos = 0.0
            while True:
                a = self.pick(row)
                info = envs.asset_info(a)
                w, dep = float(info.get("width", 3.0)), float(info.get("depth", 1.0))
                if pos + w * 0.5 > L:
                    break
                p0, p1 = at(pos), at(min(pos + w, L))
                t = p1 - p0
                t = t / max(float(np.linalg.norm(t)), 1e-9)
                out = np.array([-t[1], t[0]]) * run["side"]
                c = at(min(pos + w / 2.0, L)) + out * (dep / 2.0 - half)
                self.sc.placements.append(Placement(a, "barriers", float(c[0]), float(c[1]), 0.0, yaw_to(out)))
                self.occ.add(float(c[0]), float(c[1]), max(dep, 1.0) / 2.0)
                pos += w * 0.985  # abut, with a little overlap on the outside of bends

    # --------------------------------------------------------------------------------------- foliage
    def _grid(self, spacing: float) -> np.ndarray:
        x0, x1, y0, y1 = self.bounds
        xs = np.arange(x0 + spacing / 2, x1, spacing)
        ys = np.arange(y0 + spacing / 2, y1, spacing)
        gx, gy = np.meshgrid(xs, ys)
        pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
        return pts + self.rng.uniform(-0.4, 0.4, pts.shape) * spacing

    def _scatter(self, pts: np.ndarray, assets: list[str], category: str, min_edge: float, cap: int,
                 r_scale: float = 0.7, scale_jitter: float = 0.15) -> int:
        if not len(pts) or not assets:
            return 0
        d = self.field.dist(pts)
        keep = d >= min_edge + 4.0
        pts = pts[keep]
        keep = ~self.in_nogo(pts, 6.0, False)
        pts = pts[keep]
        order = self.rng.permutation(len(pts))
        n = 0
        for k in order:
            if n >= cap:
                break
            a = self.pick(assets)
            s = float(1.0 + self.rng.uniform(-scale_jitter, scale_jitter))
            r = envs.asset_info(a)["r"] * r_scale
            if self.try_place(a, category, pts[k, 0], pts[k, 1], float(self.rng.uniform(0, 2 * math.pi)), min_edge,
                              r=r, scale=s, margin=0.0):
                n += 1
        return n

    def foliage(self) -> None:
        e = self.e
        name = self.env_name
        trees = e.get("trees", [])
        fol = self.fol
        spacing = float(fol["treeSpacing"])
        min_tree = max(float(fol["minTreeM"]), self.edge_min + 4.0)
        cap = int(fol["treeCap"])
        pts = self._grid(spacing)
        if len(pts):
            nz = self.tree_noise.at(pts)
            sel = nz > self.tree_thr
            if self.parcels is not None:  # farmland: hedgerows along the field borders, no trees inside fields
                border = self.parcels.border(pts) < 4.0
                sel = (sel & ~self.parcels.is_field(pts)) | (border & (self.rng.random(len(pts)) < 0.55))
            self._scatter(pts[sel], trees, "trees", min_tree, cap)
        # a ragged tree belt along the terrain edge (as stock bases have), so the edge of the generated ground
        # doesn't read as a line against the far ground; industrial gets its skyline buildings there instead
        belt = int(fol.get("edgeBelt", 0 if name == "industrial" else 700))
        if trees and belt > 0:
            x0, x1, y0, y1 = self.bounds
            pts = self._grid(9.0)
            edge = np.minimum.reduce([pts[:, 0] - x0, x1 - pts[:, 0], pts[:, 1] - y0, y1 - pts[:, 1]])
            depth = 14.0 + 26.0 * Noise2D(seed_for(self.slug, "belt"), self.frame, 120.0, 2).at(pts)
            sel = (edge < depth) & (self.rng.random(len(pts)) < 0.8)
            self._scatter(pts[sel], trees, "trees", min_tree, belt)
        # bushes: along the edge of the open ground and in small clusters
        bushes = e.get("bushes", [])
        pts = self._grid(14.0)
        if len(pts) and bushes:
            d = self.field.dist(pts)
            band = (d > self.edge_min + 8.0) & (d < min_tree + 15.0)
            nz = self.tree_noise.at(pts)
            sel = band & (nz > self.tree_thr - 0.08) & (self.rng.random(len(pts)) < 0.5)
            self._scatter(pts[sel], bushes, "bushes", self.edge_min + 8.0, int(fol["bushCap"]))
        # stock foliage groups (grass and bush clumps, e.g. sandpit03's foliage_group12/10) in the open ground
        groups = list(fol.get("groups", []))
        if groups and int(fol.get("groupCap", 0)) > 0:
            pts = self._grid(26.0)
            d = self.field.dist(pts)
            nz = self.tree_noise.at(pts)
            sel = (d > self.edge_min + 6.0) & (d < 150.0) & (nz < self.tree_thr + 0.05) & (self.rng.random(len(pts)) < 0.6)
            self._scatter(pts[sel], groups, "bushes", self.edge_min + 6.0, int(fol["groupCap"]), r_scale=0.6)
        # saplings (sandpit): clusters in the open sand
        sap = e.get("saplings", [])
        if sap:
            centres = self._grid(70.0 if int(fol["saplingClusters"]) <= 45 else 50.0)
            d = self.field.dist(centres)
            centres = centres[(d > 22.0) & (d < 160.0)]
            pts = []
            for c in centres[self.rng.permutation(len(centres))[:int(fol["saplingClusters"])]]:
                k = int(self.rng.integers(8, 26))
                ang = self.rng.uniform(0, 2 * math.pi, k)
                rad = np.sqrt(self.rng.random(k)) * 14.0
                pts.append(c + np.stack([np.cos(ang) * rad, np.sin(ang) * rad], axis=1))
            if pts:
                self._scatter(np.concatenate(pts), sap, "saplings", max(18.0, self.edge_min + 2.0),
                              int(fol["saplingCap"]), r_scale=1.0, scale_jitter=0.3)
        # ground props: rocks, sand piles, logs, bales, scrap
        gp = e.get("ground_props", [])
        pts = self._grid(60.0 if int(fol["groundPropCap"]) <= 160 else 45.0)
        self._scatter(pts, gp, "groundProps", max(18.0, self.edge_min + 2.0), int(fol["groundPropCap"]), r_scale=1.0)

    def buildings(self) -> None:
        e = self.e
        name = self.env_name
        b = e.get("buildings", [])
        if not b:
            return
        x0, x1, y0, y1 = self.bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if name == "farmland":
            sites = self._grid(140.0)
            d = self.field.dist(sites)
            sites = sites[d > 70.0]
            n = 0
            for c in sites[self.rng.permutation(len(sites))]:
                if n >= 2:
                    break
                toward = np.array([cx - c[0], cy - c[1]])
                toward /= max(np.linalg.norm(toward), 1e-6)
                tang = np.array([-toward[1], toward[0]])
                plan = [("building_barn1", 0, 0), ("prop_house01", 22, 6), ("prop_silo8", -12, -4),
                        ("prop_silo8", -16, -4), ("prop_building_storage_b", 4, -22)]
                ok = 0
                for a, du, dv in plan:
                    q = c + tang * du + toward * dv
                    ok += self.try_place(a, "buildings", q[0], q[1], self.face_yaw(a, toward, tang), 45.0)
                if ok:
                    n += 1
                    self.sc.zones.append(Zone("yard", float(c[0]), float(c[1]), 26.0))
            # utility poles along a random line
            poles = [p for p in b if "utilitypole" in p]
            if poles:
                self._pole_line(poles[0])
        elif name == "industrial":
            yards = self._grid(120.0)
            d = self.field.dist(yards)
            yards = yards[d > 45.0]
            for c in yards[self.rng.permutation(len(yards))[:4]]:
                ang = float(self.rng.uniform(0, math.pi))
                ax = np.array([math.cos(ang), math.sin(ang)])
                ay = np.array([-ax[1], ax[0]])
                placed = 0
                for iu in range(5):
                    for iv in range(3):
                        q = c + ax * (iu * 3.6 - 7.2) + ay * (iv * 8.0 - 8.0)
                        a = self.pick(["prop_container2", "prop_container4", "prop_container4red"])
                        placed += self.try_place(a, "buildings", q[0], q[1], yaw_to(ay), 30.0, r=2.0)
                if placed:
                    self.sc.zones.append(Zone("yard", float(c[0]), float(c[1]), 24.0))
            for a in ("prop_storagebuilding_02", "prop_building_storage_b", "prop_building_storage_b"):
                for c in self._grid(160.0)[self.rng.permutation(len(self._grid(160.0)))]:
                    toward = np.array([cx - c[0], cy - c[1]])
                    toward /= max(np.linalg.norm(toward), 1e-6)
                    if self.try_place(a, "buildings", c[0], c[1], self.face_yaw(a, toward, toward[::-1]), 40.0):
                        self.sc.zones.append(Zone("yard", float(c[0]), float(c[1]), envs.asset_info(a)["r"] + 8.0))
                        break
            # a skyline of tall background buildings along the terrain edge
            per = self._perimeter(30.0, 55.0)
            bg = [a for a in b if a.startswith("bgr_building")]
            for q, toward in per:
                if self.rng.random() < 0.6:
                    a = self.pick(bg)
                    self.try_place(a, "buildings", q[0], q[1], self.face_yaw(a, toward, toward), 90.0, margin=-60.0)
        else:
            sites = self._grid(150.0)
            d = self.field.dist(sites)
            sites = sites[d > 50.0]
            for c in sites[self.rng.permutation(len(sites))[:3]]:
                a = self.pick(b)
                toward = np.array([cx - c[0], cy - c[1]])
                toward /= max(np.linalg.norm(toward), 1e-6)
                self.try_place(a, "buildings", c[0], c[1], self.face_yaw(a, toward, toward[::-1]), 40.0)

    def _perimeter(self, inset: float, step: float) -> list[tuple[np.ndarray, np.ndarray]]:
        x0, x1, y0, y1 = self.bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        out = []
        for (ax, ay, bx, by) in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
            L = math.hypot(bx - ax, by - ay)
            for t in np.arange(step / 2, L, step):
                p = np.array([ax + (bx - ax) * t / L, ay + (by - ay) * t / L])
                toward = np.array([cx, cy]) - p
                toward /= max(np.linalg.norm(toward), 1e-6)
                out.append((p + toward * inset, toward))
        return out

    def _pole_line(self, pole: str) -> None:
        x0, x1, y0, y1 = self.bounds
        horizontal = self.rng.random() < 0.5
        t = float(self.rng.uniform(0.15, 0.85))
        if horizontal:
            y = y0 + (y1 - y0) * t
            pts = [(x, y) for x in np.arange(x0 + 20, x1 - 20, 45.0)]
            tang = (1.0, 0.0)
        else:
            x = x0 + (x1 - x0) * t
            pts = [(x, y) for y in np.arange(y0 + 20, y1 - 20, 45.0)]
            tang = (0.0, 1.0)
        for x, y in pts:
            self.try_place(pole, "buildings", x, y, yaw_to(tang), 30.0, r=0.4)

    # --------------------------------------------------------------------------------------------- run
    def plan(self) -> Scenery:
        corners = self.corners()
        straights = self.straights()
        paddock_side = self.start_finish()
        self.corner_markers(corners)
        self.barriers()
        self.viewing_spots(corners)
        self.ad_fences(straights)
        self.paddock(paddock_side)
        self.lights()
        self.buildings()
        self.foliage()
        self._heights()
        self.sc.info.update({"corners": len(corners), "straights": len(straights), "edgeMinM": self.edge_min,
                             # categories allowed closer to a road edge than edgeMinM: [min, max] metres (the
                             # barrier row on the wall line, corner markers in front of it; under bridge decks
                             # barriers pass beneath the deck)
                             "nearRoadM": {"barriers": [C.WALL_OFFSET - 1.0, C.WALL_SETBACK + 2.0],
                                           "cornerMarkers": [C.SHOULDER_W, None]},
                             "treeThreshold": round(self.tree_thr, 4)})
        return self.sc

    def _heights(self) -> None:
        P = self.sc.placements
        if not P:
            return
        xy = np.array([(p.x, p.y) for p in P])
        z = self.geo.terrain(np.column_stack([xy, np.zeros(len(xy))]))
        big = [k for k, p in enumerate(P) if envs.asset_info(p.asset)["r"] * p.scale > 3.0 and p.category != "gantry"]
        if big:
            pts = []
            for k in big:
                r = envs.asset_info(P[k].asset)["r"] * P[k].scale * 0.7
                for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r)):
                    pts.append((P[k].x + dx, P[k].y + dy, 0.0))
            zz = self.geo.terrain(np.array(pts)).reshape(-1, 4)
            for n, k in enumerate(big):
                z[k] = min(z[k], float(zz[n].min()))
        for k, p in enumerate(P):
            sink = 0.1 if p.category in ("trees", "buildings", "paddock", "grandstand", "bushes") else 0.02
            p.z = float(z[k]) - sink


def plan(geo, field_: DistanceField | None = None) -> tuple[Scenery, Planner]:
    pl = Planner(geo, field_)
    return pl.plan(), pl


def main() -> None:  # pragma: no cover - CLI for the editor / debugging
    import argparse
    import json

    from .geometry import TrackGeometry

    ap = argparse.ArgumentParser(prog="python -m wfbuild.dressing")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    with open(a.model, encoding="utf-8") as f:
        model = json.load(f)
    geo = TrackGeometry(model, spec)
    sc, _ = plan(geo)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(sc.to_json(), f, indent=1)
    print(f"{len(sc.placements)} placements: {sc.counts()}")


if __name__ == "__main__":  # pragma: no cover
    main()
