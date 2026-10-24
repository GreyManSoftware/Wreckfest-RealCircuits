"""Real pits for imported tracks (``sketch.imported.pits``, tools/real_tracks/pits): the pit lane and its wall,
stock pit buildings and grandstands on the real footprints, paddock props on the real paddock, and the start
lights, bridges and gantries at their real positions over the road. Pure numpy (no bpy), like dressing.py.

- **Pit lane** (``Pits.meshes``): a tarmac strip draped on the terrain along the real lane, built where the pit
  wall runs (between the merges). Every lane is ``closed`` for now: a barrier across each end of the lane where
  the wall ends, so nothing drives in; the lane is never in a layout's AI route, checkpoints or minimap (those
  come from the TrackModel's routes only). ``drivable`` is in the schema for later (it needs gaps in the track's
  barrier plan at the merges and a finish checkpoint that spans the lane).
- **Pit wall**: the invisible collision wall (``walls_pit_<id>``, as the track's walls) plus a row of stock
  concrete elements on the real wall line, continuous between the two closures. On that stretch the track's own
  barrier plan on the pit side is switched off (``wall_hook``) and the AI route's run-off margin ends in front of
  the pit wall.
- **Buildings, grandstands, paddock, gantries** (``Pits.place``, called by the dressing planner): see PROPS.

The planner reserves the pit areas first (``reserve``: occupancy for the lane, buildings, paddock and gantry
legs, no-go discs over the buildings so the start camera keeps out), so no generic scenery lands on them.
"""
from __future__ import annotations

import math

import numpy as np

from . import config as C
from . import envs
from .geometry import Mesh

LANE_LIFT = 0.06           # the lane's surface above the terrain (the terrain's 4 m triangles stay below it)
LANE_SKIRT = 0.8           # the lane's edges slope down to the terrain over this
LANE_CELL = 2.0
WALL_ASSET = "prop_concrete_element3"   # 6.0 x 1.41 x 2.54 m stock concrete element (static, collision)
WALL_TOP = 1.1             # collision wall height above the ground
WALL_HALF = 0.35
CLOSURE_BEYOND = 1.5       # the closure runs this far past the lane's outer edge
MIN_CLEARANCE = 5.0        # bridges and gantries: at least this between the road and their lowest part
LIGHTS_ASSET = "prop_forest5_startlight01"  # cantilever: a pole at local x = 0, the lights' arm 7.2 m up to x = 10.5
LIGHTS_ARM = 10.5
LIGHTS_LOW = 7.2
BRIDGE_ASSET = "prop_bridge03_green"      # 55 m footbridge: inner pillars +-11 m, deck underside 5.97 m, stairs to +-27.5 m
BRIDGE_INNER = 11.0
BRIDGE_REACH = 27.5
BRIDGE_LOW = 5.97
# building props per kind: (asset, length along local X, depth, height, face): tiled along the footprint
PROPS = {
    "garages": [("prop_tarmac2_pitstop_part2", 28.4, 12.4, 10.4, "+y")],
    "control": [("prop_startbuilding_fields14", 21.8, 9.3, 10.5, "+y")],
    "media": [("prop_startbuilding_fields14", 21.8, 9.3, 10.5, "+y")],
    "hospitality": [("prop_start_building_forest11", 21.5, 14.8, 6.9, "-y"),
                    ("prop_start_building_forest09", 20.9, 8.8, 6.1, "-y")],
    "paddock": [("prop_tarmac2_tenthall", 35.0, 15.0, 6.0, "-y")],
    "other": [("prop_start_building_forest09", 20.9, 8.8, 6.1, "-y")],
    "small": [("prop_commentatorbuilding_forest09", 6.9, 6.3, 5.6, "-y")],
    "grandstand": [("prop_start_grandstand3g", 48.1, 14.8, 9.7, "-y"), ("prop_start_grandstand1", 25.0, 7.8, 8.1, "-y")],
}
SCALE_RANGE = (0.55, 1.4)
PADDOCK_ASSETS = ["vehicle_t2_cargotruck01", "vehicle_t2_crewbus01", "prop_start_tent8", "prop_trailer2",
                  "prop_hightent_03", "vehicle_t2_cargotruck01", "prop_start_tent4", "vehicle_t2_crewbus01"]
PADDOCK_SPACING = 18.0
PADDOCK_CAP = 45           # per paddock area (subscene budget)
CATEGORIES = ("pitWall", "pitBuildings", "pitPaddock", "bridges")


def present(geo) -> bool:
    imp = getattr(geo, "imported", None) or {}
    return bool(imp.get("pits"))


def _pts(a) -> np.ndarray:
    return np.array([[q["x"], q["y"]] for q in a], dtype=np.float64).reshape(-1, 2)


def _resample(p: np.ndarray, step: float) -> np.ndarray:
    seg = np.hypot(*np.diff(p, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    t = np.linspace(0.0, s[-1], max(int(round(s[-1] / step)), 1) + 1)
    return np.column_stack([np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])])


def _normals(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = np.gradient(p, axis=0)
    t = t / np.maximum(np.linalg.norm(t, axis=1), 1e-9)[:, None]
    return t, np.column_stack([-t[:, 1], t[:, 0]])


def point_in_poly(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi, xj, yj = poly[i, 0], poly[i, 1], poly[j, 0], poly[j, 1]
        inside ^= ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / np.where(yj - yi == 0, 1e-12, yj - yi) + xi)
        j = i
    return inside


def min_area_rect(poly: np.ndarray) -> tuple[np.ndarray, float, float, np.ndarray]:
    """Smallest rotated rectangle: centre, length (long side), depth, unit long axis."""
    best = None
    for i in range(len(poly)):
        e = poly[(i + 1) % len(poly)] - poly[i]
        L = float(np.hypot(*e))
        if L < 1e-6:
            continue
        ax = e / L
        ay = np.array([-ax[1], ax[0]])
        u, v = poly @ ax, poly @ ay
        area = (u.max() - u.min()) * (v.max() - v.min())
        if best is None or area < best[0]:
            c = ax * (u.max() + u.min()) / 2 + ay * (v.max() + v.min()) / 2
            best = (area, c, u.max() - u.min(), v.max() - v.min(), ax)
    _, c, lu, lv, ax = best
    if lv > lu:
        return c, float(lv), float(lu), np.array([-ax[1], ax[0]])
    return c, float(lu), float(lv), ax


def yaw_to(d) -> float:
    return math.atan2(-float(d[0]), float(d[1]))


class Lane:
    def __init__(self, d: dict, geo):
        self.id = d["id"]
        self.mode = d.get("mode", "closed")
        self.w = float(d["widthM"])
        self.side = 1 if d.get("side", "right") == "left" else -1
        self.p = _resample(_pts(d["centreline"]), LANE_CELL)
        self.t, self.nl = _normals(self.p)
        w = d.get("wall") or {}
        self.wall = _resample(_pts(w["points"]), 1.0) if w.get("points") else None
        self.wall_s = tuple(w.get("sM", (0.0, 0.0)))
        m = geo.main
        # every lane sample's nearest main sample, signed lateral offset and s
        d2 = np.hypot(self.p[:, None, 0] - m.p[None, :, 0], self.p[:, None, 1] - m.p[None, :, 1])
        self.mi = d2.argmin(1)
        self.ms = m.s[self.mi]
        # built where the wall runs: lane samples whose main s lies in the wall's s range
        if self.wall is not None:
            s0, s1 = self.wall_s
            span = (s1 - s0) % m.length
            rel = (self.ms - s0) % m.length
            self.built = rel <= span
        else:
            self.built = np.zeros(len(self.p), dtype=bool)
        idx = np.nonzero(self.built)[0]
        self.b0, self.b1 = (int(idx[0]), int(idx[-1])) if len(idx) else (0, -1)
        # away from the track: the lane normal pointing from the track to the lane
        to = self.p - m.p[self.mi, :2]
        self.away = np.where(((to * self.nl).sum(1) >= 0)[:, None], self.nl, -self.nl)

    def outer_edge(self, k: int) -> np.ndarray:
        return self.p[k] + self.away[k] * self.w / 2.0

    def polygon(self) -> np.ndarray:
        """The built lane's outline (local metres)."""
        k = np.arange(self.b0, self.b1 + 1)
        if not len(k):
            return np.zeros((0, 2))
        left = self.p[k] + self.nl[k] * self.w / 2
        right = self.p[k] - self.nl[k] * self.w / 2
        return np.vstack([left, right[::-1]])

    def closures(self) -> list[np.ndarray]:
        """The two closure lines (closed lanes): from the wall's end across the lane, past its outer edge."""
        if self.mode != "closed" or self.wall is None or self.b1 < self.b0:
            return []
        out = []
        for k, wall_pt in ((self.b0, self.wall[0]), (self.b1, self.wall[-1])):
            end = self.outer_edge(k) + self.away[k] * CLOSURE_BEYOND
            out.append(np.array([wall_pt, end]))
        return out


class Pits:
    """The pits of one imported track (built once per TrackGeometry: ``geo.pits``)."""

    def __init__(self, geo):
        self.geo = geo
        d = (geo.imported or {}).get("pits") or {}
        self.d = d
        self.lanes = [Lane(x, geo) for x in d.get("pitLanes", [])]
        self.buildings = [(b, _pts(b["footprint"])) for b in d.get("buildings", [])]
        self.grandstands = [(b, _pts(b["footprint"])) for b in d.get("grandstands", [])]
        self.paddocks = [(a, _pts(a["points"])) for a in d.get("paddocks", [])]
        self.gantries = list(d.get("gantries", []))
        self.report: dict = {"lanes": [], "buildings": 0, "grandstands": 0, "paddockProps": 0, "gantries": [],
                             "warnings": []}

    # ------------------------------------------------------------------------------------ the wall plan
    def wall_hook(self, plan: dict) -> None:
        """geometry.wall_plan hook: on the pit side, over the pit wall's stretch, the pit wall is the containment:
        no track barrier there, and the AI route's run-off margin stops 0.8 m in front of the pit wall."""
        g = self.geo
        m = g.main
        for lane in self.lanes:
            if lane.wall is None:
                continue
            key = (m.index, lane.side)
            if key not in plan:
                continue
            p = plan[key]
            s0, s1 = lane.wall_s
            rel = (m.s - s0) % m.length
            on = rel <= (s1 - s0) % m.length
            # the wall's distance from the road edge per main sample
            dd = np.hypot(m.p[:, None, 0] - lane.wall[None, :, 0], m.p[:, None, 1] - lane.wall[None, :, 1]).min(1)
            off = np.maximum(dd - m.w / 2.0, 0.3)
            p["ok"] = p["ok"] & ~on
            p["divider"] = p["divider"] & ~on
            margin = np.where(on, np.maximum(off - 0.8, 0.5), np.inf)
            p["ai_margin"] = np.minimum(p["ai_margin"], g._cone(m, on, margin, 0.5))
            # every route (alternative layouts' branches too): the AI run-off stops 0.8 m short of the built lane
            if lane.b1 > lane.b0:
                lp = lane.p[lane.b0:lane.b1 + 1]
                for (ri, side), q in plan.items():
                    rr = q["route"]
                    d = np.hypot(rr.p[:, None, 0] - lp[None, :, 0], rr.p[:, None, 1] - lp[None, :, 1])
                    k = d.argmin(1)
                    towards = ((lp[k] - rr.p[:, :2]) * rr.nl).sum(1) * side > 0
                    room = d[np.arange(rr.n), k] - lane.w / 2.0 - rr.w / 2.0 - 0.8
                    cap = np.where(towards & (room < q["ai_margin"]), np.maximum(room, 0.5), np.inf)
                    if np.isfinite(cap).any():
                        q["ai_margin"] = np.minimum(q["ai_margin"], cap)
            self.report["lanes"].append({"id": lane.id, "mode": lane.mode, "side": "left" if lane.side > 0 else "right",
                                         "wallSM": [round(s0, 1), round(s1, 1)],
                                         "wallFromEdgeM": [round(float(off[on].min()), 2), round(float(off[on].max()), 2)]
                                         if on.any() else None,
                                         "builtLengthM": round(float(max(lane.b1 - lane.b0, 0) * LANE_CELL), 1)})

    # ---------------------------------------------------------------------------------------- meshes
    def meshes(self, road_material: str) -> list[Mesh]:
        g = self.geo
        out = []
        tile = C.TEX_TILE["road"]
        for lane in self.lanes:
            if lane.b1 <= lane.b0:
                continue
            mesh = Mesh(f"pitlane_{lane.id}", "col = true", kind="pit")  # bounds.ALLOWED_KINDS: drivable off the AI routes
            m = max(1, int(round(lane.w / LANE_CELL)))
            offs = np.concatenate([[-lane.w / 2 - LANE_SKIRT], np.linspace(-lane.w / 2, lane.w / 2, m + 1),
                                   [lane.w / 2 + LANE_SKIRT]])
            rows = []
            for k in range(lane.b0, lane.b1 + 1):
                xy = lane.p[k] + lane.nl[k] * offs[:, None]
                z = g.terrain(np.column_stack([xy, np.zeros(len(xy))]))
                z = z + LANE_LIFT
                z[0] -= LANE_LIFT + 0.08
                z[-1] -= LANE_LIFT + 0.08
                rows.append([mesh.vert((x, y, zz)) for (x, y), zz in zip(xy, z)])
            for a in range(len(rows) - 1):
                v0, v1 = (lane.b0 + a) * LANE_CELL / tile, (lane.b0 + a + 1) * LANE_CELL / tile
                for j in range(len(offs) - 1):
                    u0, u1 = (offs[j] + lane.w / 2) / tile, (offs[j + 1] + lane.w / 2) / tile
                    mesh.oriented_face((rows[a][j], rows[a][j + 1], rows[a + 1][j + 1], rows[a + 1][j]), road_material,
                                       np.array([0.0, 0.0, 1.0]), [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
            out.append(mesh)
            # the pit wall and the closures: invisible collision (as the track's walls); the props are visible
            walls = Mesh(f"walls_pit_{lane.id}", C.WALL_CUSTOM_DATA, kind="wall")
            profile = [(-WALL_HALF, 0.0), (-WALL_HALF, 1.0), (WALL_HALF, 1.0), (WALL_HALF, 0.0)]
            for line in ([lane.wall] if lane.wall is not None else []) + [_resample(c, 1.0) for c in lane.closures()]:
                t, nl = _normals(line)
                z = g.terrain(np.column_stack([line, np.zeros(len(line))]))
                s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))])
                g._extrude(walls, np.column_stack([line, z]), nl, z - C.WALL_SINK, z + WALL_TOP, profile,
                           C.WALL_MATERIAL["concrete"], False, s)
            if not walls.empty:
                out.append(walls)
        return out

    # ------------------------------------------------------------------------------------ reservations
    def reserve(self, planner) -> None:
        """Occupancy over the lane, buildings, grandstands, paddock and gantry legs (before any other scenery),
        and no-go discs over the buildings and grandstands (the start camera keeps out of them)."""
        occ = planner.occ
        for lane in self.lanes:
            for k in range(lane.b0, lane.b1 + 1, 2):
                occ.add(float(lane.p[k, 0]), float(lane.p[k, 1]), lane.w / 2.0 + 1.0)
            for line in ([lane.wall] if lane.wall is not None else []) + lane.closures():
                for q in _resample(line, 3.0):
                    occ.add(float(q[0]), float(q[1]), 1.5)
        discs = []
        for _b, poly in self.buildings + self.grandstands:
            for q in self._cover(poly, 6.0):
                occ.add(float(q[0]), float(q[1]), 4.5)
                discs.append((q, 4.5))
        for _a, poly in self.paddocks:
            for q in self._cover(poly, 10.0):
                occ.add(float(q[0]), float(q[1]), 6.0)
        if discs:
            xy = np.array([q for q, _ in discs])
            r = np.array([rr for _, rr in discs])
            planner.nogo_xy = np.vstack([planner.nogo_xy, xy])
            planner.nogo_r = np.concatenate([planner.nogo_r, r])
            planner.nogo_exempt = np.concatenate([planner.nogo_exempt, np.zeros(len(xy), dtype=bool)])

    @staticmethod
    def _cover(poly: np.ndarray, step: float) -> np.ndarray:
        lo, hi = poly.min(0), poly.max(0)
        xs = np.arange(lo[0] + step / 2, hi[0], step)
        ys = np.arange(lo[1] + step / 2, hi[1], step)
        if not len(xs) or not len(ys):
            return poly.mean(0)[None, :]
        gx, gy = np.meshgrid(xs, ys)
        pts = np.column_stack([gx.ravel(), gy.ravel()])
        ins = pts[point_in_poly(pts, poly)]
        return np.vstack([ins, poly]) if len(ins) else poly

    # ---------------------------------------------------------------------------------------- props
    def place(self, planner) -> None:
        from .dressing import Placement, Zone  # noqa: PLC0415 - dressing imports this module

        P = planner.sc.placements
        add = lambda a, cat, q, yaw, s=1.0: P.append(Placement(a, cat, float(q[0]), float(q[1]), 0.0, float(yaw), float(s)))  # noqa: E731
        # pit wall and closures: a row of concrete elements on the line
        for lane in self.lanes:
            lines = ([lane.wall] if lane.wall is not None else []) + lane.closures()
            for line in lines:
                for q, t in self._row(line, 6.0 * 0.985):
                    add(WALL_ASSET, "pitWall", q, math.atan2(-t[0], t[1]) + math.pi / 2)
            poly = lane.polygon()
            for q in self._cover(poly, 8.0) if len(poly) else []:
                planner.sc.zones.append(Zone("paved", float(q[0]), float(q[1]), 6.0))
        # buildings and grandstands: stock props tiled along each footprint's long side, facing the road
        for b, poly in self.buildings:
            if self._building(planner, b, poly, add):
                self.report["buildings"] += 1
        for b, poly in self.grandstands:
            if self._building(planner, {**b, "kind": "grandstand"}, poly, add):
                self.report["grandstands"] += 1
        # paddock: trucks, tents and crew buses on the real paddock
        rng = planner.rng
        for a, poly in self.paddocks:
            n = 0
            c, L, D, ax = min_area_rect(poly)
            ay = np.array([-ax[1], ax[0]])
            cand = self._cover(poly, PADDOCK_SPACING)
            placed: list[tuple[np.ndarray, float]] = []
            for q in cand[rng.permutation(len(cand))]:
                if n >= PADDOCK_CAP or not point_in_poly(q[None, :], poly)[0]:
                    continue
                asset = PADDOCK_ASSETS[int(rng.integers(len(PADDOCK_ASSETS)))]
                rr = float(envs.asset_info(asset)["r"])
                q = q + rng.uniform(-2, 2, 2)
                if float(planner.field.dist(q)[0]) < planner.edge_min + rr or self._on_pits(q, rr * 1.45 + 1.0):
                    continue
                if any(np.hypot(*(q - q2)) < (rr + r2) * 0.85 for q2, r2 in placed):
                    continue
                placed.append((q, rr))
                yaw = yaw_to(ay) if rng.random() < 0.8 else yaw_to(ax)
                add(asset, "pitPaddock", q, yaw)
                planner.sc.zones.append(Zone("paddock", float(q[0]), float(q[1]), 9.0))
                n += 1
            self.report["paddockProps"] += n
        # gantries and bridges over the road
        for gnt in self.gantries:
            self._gantry(planner, gnt, add)

    def _row(self, line: np.ndarray, step: float):
        seg = np.hypot(*np.diff(line, axis=0).T)
        s = np.concatenate([[0.0], np.cumsum(seg)])
        L = float(s[-1])
        n = max(1, int(math.ceil(L / step)))
        for k in range(n):
            a, b = k * L / n, (k + 1) * L / n
            p0 = np.array([np.interp(a, s, line[:, 0]), np.interp(a, s, line[:, 1])])
            p1 = np.array([np.interp(b, s, line[:, 0]), np.interp(b, s, line[:, 1])])
            t = (p1 - p0) / max(float(np.hypot(*(p1 - p0))), 1e-9)
            yield (p0 + p1) / 2.0, t

    def _on_pits(self, q: np.ndarray, r: float) -> bool:
        for lane in self.lanes:
            if lane.b1 >= lane.b0 and np.hypot(*(lane.p[lane.b0:lane.b1 + 1] - q).T).min() < lane.w / 2 + r:
                return True
        for _b, poly in self.buildings + self.grandstands:
            if point_in_poly(q[None, :], poly)[0] or np.hypot(*(poly - q).T).min() < r:
                return True
        return False

    def _building(self, planner, b: dict, poly: np.ndarray, add) -> bool:
        c, L, D, ax = min_area_rect(poly)
        kind = b["kind"]
        if L < 12.0 and kind != "grandstand":
            kind = "small"
        choices = PROPS.get(kind, PROPS["other"])
        face = np.array([b.get("facing", {}).get("x", 0.0), b.get("facing", {}).get("y", 1.0)])
        ay = np.array([-ax[1], ax[0]])
        if float(ay @ face) < 0:
            ay = -ay
        best = None
        for asset, lm, dm, hm, fside in choices:
            s0 = float(np.clip(float(b["heightM"]) / hm, *SCALE_RANGE))
            n = max(1, int(round(L / (lm * s0))))
            s = float(np.clip(L / (n * lm), *SCALE_RANGE))
            err = abs(n * lm * s - L) + abs(hm * s - float(b["heightM"])) * 2.0
            if best is None or err < best[0]:
                best = (err, asset, lm, dm, hm, fside, n, s)
        _, asset, lm, dm, hm, fside, n, s = best
        # the front of the props on the footprint's front edge (towards the road), tiles along the long axis
        front = c + ay * (D / 2.0)
        yaw = yaw_to(ay) if fside == "+y" else yaw_to(-ay)
        cat = "realGrandstand" if kind == "grandstand" else "pitBuildings"
        lanes = [ln.polygon() for ln in self.lanes if ln.b1 > ln.b0]

        def tiles(back: float) -> list[np.ndarray]:
            cl = front - ay * (back + dm * s / 2.0)
            return [cl + ax * ((k + 0.5) * lm * s - n * lm * s / 2.0) for k in range(n)]

        X = np.array([math.cos(yaw), math.sin(yaw)])  # the prop's local axes as placed
        Y = np.array([-math.sin(yaw), math.cos(yaw)])
        hw, hd = lm * s / 2.0 + 0.5, dm * s / 2.0 + 0.5

        def hits(back: float) -> bool:
            # every tile's corners, edge midpoints and centre, grown 0.5 m, off the pit lane (OSM footprints often
            # reach it)
            pts = np.array([q + X * u + Y * v for q in tiles(back) for u in (-hw, 0.0, hw) for v in (-hd, 0.0, hd)])
            return any(point_in_poly(pts, lp).any() for lp in lanes)

        back = 0.0
        while lanes and back < D + 40.0 and hits(back):
            back += 0.5
        if lanes and hits(back):
            self.report["warnings"].append(f"{b.get('name') or kind}: no room off the pit lane, left out")
            return False
        for q in tiles(back):
            add(asset, cat, q, yaw, s)
        return True

    def _gantry(self, planner, gnt: dict, add) -> None:
        g = self.geo
        r = g.main if gnt.get("route", "main") == "main" else g.by_id.get(gnt["route"])
        if r is None:
            self.report["warnings"].append(f"gantry {gnt.get('name')}: no route {gnt.get('route')}")
            return
        i = r.index_at(float(gnt["sM"]))
        p, t, nl = r.p[i], r.tangent[i], r.nl[i]
        hw = float(r.w[i]) / 2.0
        # what the structure must clear on each side (+nl, -nl): the road edge plus the track's wall offset, or a
        # pit lane's outer edge beyond it
        need = {1: hw + C.WALL_OFFSET + 1.5, -1: hw + C.WALL_OFFSET + 1.5}
        for lane in self.lanes:
            if lane.b1 < lane.b0:
                continue
            k = int(np.argmin(np.hypot(*(lane.p - p[:2]).T)))
            if not (lane.b0 <= k <= lane.b1):
                continue
            o = lane.p[k] - p[:2]
            if np.hypot(*o) > 40.0:
                continue
            sd = 1 if float(o @ nl) > 0 else -1
            need[sd] = max(need[sd], abs(float(o @ nl)) + lane.w / 2.0 + 1.5)
        kind = gnt["kind"]
        if kind == "startLights":
            self._start_lights(planner, gnt, r, i, add)
            return
        asset, leg, low, reach = BRIDGE_ASSET, BRIDGE_INNER, BRIDGE_LOW, BRIDGE_REACH
        half = (need[1] + need[-1]) / 2.0
        shift = (need[1] - need[-1]) / 2.0     # along +nl
        s = max(half / leg, 1.0)
        centre = p[:2] + nl * shift
        legs = np.array([centre + nl * leg * s, centre - nl * leg * s])
        z_legs = g.terrain(np.column_stack([legs, np.zeros(2)]))
        road_top = float(max(r.left[i, 2], r.right[i, 2], p[2]))
        z = float(min(z_legs.min(), road_top))
        # deck / banner at least MIN_CLEARANCE over the road
        s = max(s, (MIN_CLEARANCE + road_top - z) / low)
        legs = np.array([centre + nl * leg * s, centre - nl * leg * s])
        ends = np.array([centre + nl * reach * s, centre - nl * reach * s])
        clear = low * s + z - road_top
        problems = []
        for q in np.vstack([legs, ends]):
            if self._on_pits(q, 0.5):
                problems.append("a leg or stair on the pits")
                break
        dist_legs = planner.field.dist(legs)
        if (dist_legs < 1.0).any():
            problems.append("a leg on a road")
        P = planner.sc.placements
        add(asset, "gantry" if kind == "startLights" else "bridges", centre, yaw_to(t), s)
        P[-1].z = z
        self.report["gantries"].append({"kind": kind, "name": gnt.get("name", ""), "sM": round(float(r.s[i]), 1),
                                        "scale": round(s, 3), "clearanceM": round(clear, 2),
                                        "legsFromCentreM": [round(float(np.hypot(*(q - p[:2]))), 1) for q in legs],
                                        "problems": problems})
        self._fixed_z = getattr(self, "_fixed_z", [])
        self._fixed_z.append((len(P) - 1, z))

    def _start_lights(self, planner, gnt: dict, r, i: int, add) -> None:
        """The start lights: a stock cantilever (pole and an arm 7.2 m up reaching 10.5 m) on the pit wall, the arm
        over the track; without a pit wall at the line, beside the road where the wall plan puts the barrier."""
        g = self.geo
        p, nl = r.p[i], r.nl[i]
        hw = float(r.w[i]) / 2.0
        pole, side = None, None
        for lane in self.lanes:
            if lane.wall is None:
                continue
            d = np.hypot(*(lane.wall - p[:2]).T)
            k = int(np.argmin(d))
            if d[k] < 30.0 and 0 < k < len(lane.wall) - 1:
                side = 1 if float((lane.wall[k] - p[:2]) @ nl) > 0 else -1
                pole = lane.wall[k] + nl * side * 0.4
                break
        if pole is None:
            side = 1
            pole = p[:2] + nl * side * (hw + C.WALL_OFFSET)
        reach = float(np.hypot(*(pole - p[:2])))
        s = max(1.0, (reach + 3.0) / LIGHTS_ARM)
        z = float(g.terrain(np.array([[pole[0], pole[1], 0.0]]))[0])
        road_top = float(max(r.left[i, 2], r.right[i, 2], p[2]))
        s = max(s, (MIN_CLEARANCE + road_top - z) / LIGHTS_LOW)
        over = -nl * side                       # the arm (local +X) points across the track
        add(LIGHTS_ASSET, "gantry", pole, math.atan2(over[1], over[0]), s)
        planner.sc.placements[-1].z = z
        self.report["gantries"].append({"kind": "startLights", "name": gnt.get("name", ""), "sM": round(float(r.s[i]), 1),
                                        "asset": LIGHTS_ASSET, "scale": round(s, 3),
                                        "clearanceM": round(LIGHTS_LOW * s + z - road_top, 2),
                                        "poleFromCentreM": round(reach, 1), "armReachM": round(LIGHTS_ARM * s, 1),
                                        "problems": ["the pole on a road"] if float(planner.field.dist(pole)[0]) < 0.3 else []})
        self._fixed_z = getattr(self, "_fixed_z", [])
        self._fixed_z.append((len(planner.sc.placements) - 1, z))

    def after_heights(self, planner) -> None:
        """dressing._heights puts every prop on the terrain; gantries and bridges keep their own base height."""
        for k, z in getattr(self, "_fixed_z", []):
            planner.sc.placements[k].z = z
