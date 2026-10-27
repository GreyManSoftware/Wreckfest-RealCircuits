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
- **Span banners** (``Pits._banners``): our sponsor banners (sponsor.py ``span`` panels, two or three side by side)
  on every bridge face a layout drives towards, "Welcome to <track>" on the one nearest each layout's start.

The planner reserves the pit areas first (``reserve``: occupancy for the lane, buildings, paddock and gantry
legs, no-go discs over the buildings so the start camera keeps out), so no generic scenery lands on them.
"""
from __future__ import annotations

import math
import zlib

import numpy as np

from . import config as C
from . import envs, footprint
from .geometry import Mesh

LANE_LIFT = 0.06           # the lane's surface above the terrain (the terrain's 4 m triangles stay below it)
LANE_SKIRT = 0.8           # the lane's edges slope down to the terrain over this
LANE_CELL = 2.0
WALL_ASSET = "prop_barrier8"   # 3.7 x 0.82 x 1.09 m plain grey concrete barrier (Chris 2026-10-25: half height, all grey)
WALL_LEN = 3.7
WALL_GAP = 0.04            # between the elements, as stock rows (3.74-3.78 m apart): overlapping ends share their side
                           # and top faces, which flicker (Chris 2026-10-26: every circuit's pit wall)
OPEN_LANES = True   # no closures across the lane ends (Chris 2026-10-25: no walls across junctions); the pit-bypassed
                    # stretch of the lap carries a checkpoint instead (trackgen_geom.pipeline._checkpoints)
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
GANTRY_ASSET = "prop_start_archway01"     # 32.7 m start arch (measured, .local/probe): posts at x +-11 m (y 0.1-1.4 m),
GANTRY_LEG = 11.0                         # stays from them down to feet at x +-16.4 m (y -4 / +4.4); its stock banner
GANTRY_FOOT = 16.35                       # (x -9.8..8.7 m, y 0.73-0.98 m) hangs lowest, 5.32 m up mid-span
GANTRY_LOW = 5.32
SPANS = {"bridge": (BRIDGE_ASSET, BRIDGE_INNER, BRIDGE_LOW), "gantry": (GANTRY_ASSET, GANTRY_LEG, GANTRY_LOW)}
PART_CLEAR = 4.0 + 3.7     # preferred: every pillar, stair and base 4 m beyond the verge (shoulder, or kerb + green strip)
LEG_CLEAR = 3.7 + 0.5      # required of the pillars / posts of a real (approved) span: off the road and its verge
LEG_MIN = 0.5              # a real span is replaced by the gantry only where its pillars would stand on a road
SPAN_SCALE_MAX = 1.8
NUDGE_M = 6.0              # a span may move this far across the road to get a pillar off it
# span faces for banners (sponsor.py span / welcome panels), unscaled: the usable half-length along the span (between
# the pillars / posts), the y of the -Y and +Y facing surfaces, the lowest part, and the banner's lower and highest
# edge. Footbridge: side walls at y +-1.88 m from 6.04 to 9.03 m, pillars x +-11..13 (y +-2); arch: the stock
# banner's -Y face at y 0.73, the frame's +Y face at 1.09, the top beam from 7.42 m.
FACES = {BRIDGE_ASSET: {"half": 10.5, "neg": -1.88, "pos": 1.88, "low": BRIDGE_LOW, "bottom": 6.05, "top": 9.0},
         GANTRY_ASSET: {"half": 8.5, "neg": 0.73, "pos": 1.09, "low": GANTRY_LOW, "bottom": 5.4, "top": 7.4}}
BANNER_GAP = 0.03          # between the span's face and the banner
BANNER_MIN_H = 1.2         # three sponsors side by side when each panel is still this tall, else two


def is_span(asset: str) -> bool:
    return asset in FACES


def span_parts(asset: str, centre, yaw: float, s: float, legs_only: bool = False) -> np.ndarray:
    """Ground points of a span's legs, pillars, stairs, stays and bases (local X across the road), placed."""
    X = np.array([math.cos(yaw), math.sin(yaw)])
    Y = np.array([-math.sin(yaw), math.cos(yaw)])
    c = np.asarray(centre, dtype=np.float64)[:2]
    if asset == BRIDGE_ASSET:
        xs = [BRIDGE_INNER] if legs_only else list(np.arange(BRIDGE_INNER - 1.0, BRIDGE_REACH + 0.01, 1.0))
        loc = [(sd * x, y) for sd in (1, -1) for x in xs for y in (-1.5, 0.0, 1.5)]
    else:
        loc = [(sd * x, y) for sd in (1, -1) for x in (GANTRY_LEG - 0.5, GANTRY_LEG + 0.5) for y in (0.1, 0.75, 1.4)]
        if not legs_only:  # the stays, from the posts out to their feet (the -X foot at y -4, the +X one at +4.4)
            loc += [(sd * (GANTRY_LEG + t * (GANTRY_FOOT - GANTRY_LEG)), 0.75 + t * ((4.4 if sd > 0 else -4.0) - 0.75))
                    for sd in (1, -1) for t in np.linspace(0.0, 1.0, 7)]
    return np.array([c + X * x * s + Y * y * s for x, y in loc])


def _axes(yaw: float) -> tuple[np.ndarray, np.ndarray]:
    """A placement's local X and Y in the plane (a span: X across the road, Y along it)."""
    return np.array([math.cos(yaw), math.sin(yaw)]), np.array([-math.sin(yaw), math.cos(yaw)])


def driven_faces(geo, asset: str, centre, yaw: float, s: float) -> dict[int, list[dict]]:
    """The faces of a span that layouts drive towards: {-1: passes, +1: passes}, -1 being the -Y face (seen by cars
    driving along +Y). A pass is one stretch of a layout's lap under the span (within the usable half-length plus
    2 m across, 4 m along): {layout, s (along that layout's route), fromLineM (from its start line, either way
    round the lap)}."""
    from .dressing import layout_starts  # noqa: PLC0415 - dressing imports this module

    X, Y = _axes(yaw)
    c = np.asarray(centre, dtype=np.float64)[:2]
    half = FACES[asset]["half"] * s + 2.0
    out: dict[int, list[dict]] = {-1: [], 1: []}
    for st in layout_starts(geo):
        r, direction = st["route"], st["direction"]
        rel = r.p[:, :2] - c
        u, v = rel @ X, rel @ Y
        idx = np.nonzero((np.abs(u) <= half) & (np.abs(v) <= 4.0))[0]
        if not len(idx):
            continue
        for run in np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1):
            j = int(run[np.argmin(np.abs(v[run]))])
            face = -1 if float((r.tangent[j] * direction) @ Y) > 0 else 1
            d = ((float(r.s[j]) - st["lineS"]) * direction) % r.length
            out[face].append({"layout": st["layout"], "s": float(r.s[j]), "fromLineM": float(min(d, r.length - d))})
    return out
# building props per kind: (asset, length along the row, depth, height, face): tiled along the footprint; the row
# runs along local X when the face (towards the road) is +-y, along local Y when it is +-x
PROPS = {
    "garages": [("prop_tarmac2_pitstop_part2", 28.4, 12.4, 10.4, "+y")],
    "control": [("prop_startbuilding_fields14", 21.8, 9.3, 10.5, "+y")],
    "media": [("prop_startbuilding_fields14", 21.8, 9.3, 10.5, "+y")],
    "hospitality": [("prop_start_building_forest11", 21.5, 14.8, 6.9, "-y"),
                    ("prop_start_building_forest09", 20.9, 8.8, 6.1, "-y")],
    "paddock": [("prop_tarmac2_tenthall", 35.0, 15.2, 6.0, "+x")],   # its long side is local Y (15.2 x 35 m)
    "other": [("prop_start_building_forest09", 20.9, 8.8, 6.1, "-y")],
    "small": [("prop_commentatorbuilding_forest09", 6.9, 6.3, 5.6, "-y")],
    # not prop_start_grandstand3g: its front row of ad boards is two strips of adverts 13 mm apart over 38 m (and the
    # wood boards 14 mm behind), which flicker against each other (Chris 2026-10-26: Brands' start grandstand)
    "grandstand": [("prop_start_grandstand1", 25.0, 7.8, 8.1, "-y")],
}
SCALE_RANGE = (0.55, 1.4)
TILE_GAP = 1.03           # building and grandstand tiles: 3 % of their length apart
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


def _abutting(line: np.ndarray) -> np.ndarray:
    """The pit wall line trimmed evenly at both ends to a whole number of WALL_LEN + WALL_GAP slots, so the
    unscaled elements (the game draws #xref subscenes at their own size) abut along all of it: no stretched
    elements, no gaps, and the collision wall ends where the last element does."""
    seg = np.hypot(*np.diff(line, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    pitch = WALL_LEN + WALL_GAP
    n = int(math.floor(s[-1] / pitch + 1e-9))
    if n < 1:
        return line
    a = (s[-1] - n * pitch) / 2.0
    t = np.concatenate([[a], s[(s > a) & (s < s[-1] - a)], [s[-1] - a]])
    return _resample(np.column_stack([np.interp(t, s, line[:, 0]), np.interp(t, s, line[:, 1])]), 1.0)


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
        self.wall = _abutting(_resample(_pts(w["points"]), 1.0)) if w.get("points") else None
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
        if self.mode != "closed" or self.wall is None or self.b1 < self.b0 or OPEN_LANES:
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
            if "fence" in p:
                p["fence"] = p["fence"] & ~on
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
        # the stock building and grandstand tiles, laid out now (place() adds them) and their real footprints
        # occupied, 1 m grown: the stock props are bigger than the OSM outline or moved back off the pit lane, and
        # nothing placed before place() (sponsor boards: Donington's billboard stood 58 % inside a hospitality
        # building) may stand on one
        self._tiles: list[tuple] = []
        keep = lambda a, cat, q, yaw, s=1.0: self._tiles.append((a, cat, q, yaw, s))  # noqa: E731
        for b, poly in self.buildings:
            if self._building(planner, b, poly, keep):
                self.report["buildings"] += 1
        for b, poly in self.grandstands:
            if self._building(planner, {**b, "kind": "grandstand"}, poly, keep):
                self.report["grandstands"] += 1
        for a, _cat, q, yaw, s in self._tiles:
            for c in self._cover(footprint.rect(a, q[0], q[1], yaw, s, envs.asset_info(a)), 3.0):
                occ.add(float(c[0]), float(c[1]), 3.0)
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
                for q, t in self._row(line, WALL_LEN, WALL_GAP):
                    # the element's mesh runs from x = -1.83 to 1.87 m: centre that span on the slot
                    off = sum(footprint.EXTENTS[WALL_ASSET][:2]) / 2.0
                    add(WALL_ASSET, "pitWall", q - t * off, math.atan2(-t[0], t[1]) + math.pi / 2)
            poly = lane.polygon()
            for q in self._cover(poly, 8.0) if len(poly) else []:
                planner.sc.zones.append(Zone("paved", float(q[0]), float(q[1]), 6.0))
        # buildings and grandstands: stock props tiled along each footprint's long side, facing the road (laid out
        # by reserve)
        for tile in getattr(self, "_tiles", []):
            add(*tile)
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
                if any(np.hypot(*(q - q2)) < (rr + r2) * 1.05 for q2, r2 in placed):  # no shared footprints
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
        self._banners(planner, add)

    @staticmethod
    def _row(line: np.ndarray, length: float, gap: float):
        """Elements ``length`` long end to end along the line, never overlapping: equal slots at least ``length +
        gap`` long, each element unscaled (the game ignores #xref scale) on its slot's chord. On a line trimmed by
        ``_abutting`` (the pit wall) every slot is exactly ``length + gap``, so the elements abut ``gap`` apart.
        Yields (centre, unit direction)."""
        seg = np.hypot(*np.diff(line, axis=0).T)
        s = np.concatenate([[0.0], np.cumsum(seg)])
        L = float(s[-1])
        n = max(1, int(math.floor(L / (length + gap) + 0.02)))  # a trimmed line a few mm short on a curve
        for k in range(n):
            a, b = k * L / n, (k + 1) * L / n
            p0 = np.array([np.interp(a, s, line[:, 0]), np.interp(a, s, line[:, 1])])
            p1 = np.array([np.interp(b, s, line[:, 0]), np.interp(b, s, line[:, 1])])
            chord = max(float(np.hypot(*(p1 - p0))), 1e-9)
            yield (p0 + p1) / 2.0, (p1 - p0) / chord

    def _on_pits(self, q: np.ndarray, r: float, lanes_only: bool = False) -> bool:
        for lane in self.lanes:
            if lane.b1 >= lane.b0 and np.hypot(*(lane.p[lane.b0:lane.b1 + 1] - q).T).min() < lane.w / 2 + r:
                return True
        if lanes_only:
            return False
        for _b, poly in self.buildings + self.grandstands:
            if point_in_poly(q[None, :], poly)[0] or np.hypot(*(poly - q).T).min() < r:
                return True
        return False

    def _building(self, planner, b: dict, poly: np.ndarray, add) -> bool:
        c, L, D, ax = min_area_rect(poly)
        kind = b["kind"]
        if (L < 12.0 or b.get("_small")) and kind != "grandstand":
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
        toward = ay if fside[0] == "+" else -ay
        yaw = yaw_to(toward) if fside[1] == "y" else math.atan2(float(toward[1]), float(toward[0]))
        cat = "realGrandstand" if kind == "grandstand" else "pitBuildings"
        lanes = [ln.polygon() for ln in self.lanes if ln.b1 > ln.b0]

        def tiles(back: float, along: float = 0.0) -> list[np.ndarray]:
            cl = front - ay * (back + dm * s / 2.0) + ax * along
            # a small gap between tiles: abutting end walls and ad panels are coplanar and flicker
            return [cl + ax * ((k + 0.5) * lm * s * TILE_GAP - n * lm * s * TILE_GAP / 2.0) for k in range(n)]

        hw, hd = lm * s / 2.0 + 0.5, dm * s / 2.0 + 0.5   # along the row, towards the road

        def hits(back: float, along: float = 0.0) -> bool:
            # every tile's corners, edge midpoints and centre, grown 0.5 m, off the pit lane (OSM footprints often
            # reach it)
            pts = np.array([q + ax * u + ay * v for q in tiles(back, along) for u in (-hw, 0.0, hw) for v in (-hd, 0.0, hd)])
            if any(point_in_poly(pts, lp).any() for lp in lanes) or bool((planner.field.dist(pts) < 1.5).any()):
                return True
            # measured run-off (runoff.py): not in a gravel trap, on an apron or between a road and its barrier
            return bool(getattr(planner, "measured", False) and planner.in_runoff(pts, 0.0).any())

        # the smallest move that clears the lane: back from the road first, then also sideways along the row
        moves = sorted(((bk, al) for bk in np.arange(0.0, D + 40.0, 0.5) for al in np.arange(-20.0, 20.01, 1.0)),
                       key=lambda m: m[0] + 1.5 * abs(m[1]))
        back, along = 0.0, 0.0
        if hits(0.0):
            ok = next((m for m in moves if not hits(*m)), None)
            if ok is None:
                if kind not in ("small", "grandstand"):  # a smaller stock building fits where a long one doesn't
                    return self._building(planner, {**b, "_small": True}, poly, add)
                self.report["warnings"].append(f"{b.get('name') or kind}: no room off the pit lane, left out")
                return False
            back, along = ok
        info = envs.asset_info(asset)
        done = getattr(self, "_rects", None)
        if done is None:
            done = self._rects = []
        n_added = 0
        for q in tiles(back, along):
            rq = footprint.ccw(footprint.rect(asset, q[0], q[1], yaw, s, info))
            # never on another pit building's or grandstand's footprint (overlapping OSM footprints)
            if any(footprint.area(footprint.clip(list(rq), o)) > 0.1 * min(footprint.area(list(rq)), footprint.area(list(o)))
                   for o in done):
                continue
            done.append(rq)
            add(asset, cat, q, yaw, s)
            n_added += 1
        if not n_added:
            self.report["warnings"].append(f"{b.get('name') or kind}: on another building's footprint, left out")
        return n_added > 0

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
        self._span(planner, gnt, r, i, need, add)

    def _span(self, planner, gnt: dict, r, i: int, need: dict, add) -> None:
        """A bridge, footbridge or gantry across the road (every kind: the real spans are approved, Chris 2026-10-26):
        the stock footbridge, scaled (deck at least MIN_CLEARANCE over the road) and nudged up to NUDGE_M across the
        road so that, in order of preference, every pillar, stair and base stands PART_CLEAR beyond every road and
        verge edge; or its pillars at least LEG_CLEAR (off the verge, the clearest parts; reported); or at least
        LEG_MIN (off the road; reported). Never a pillar, stair or base on a road or the pit lane, never a pillar in
        a pit building (stairs may run into one, as a real footbridge lands on it), and the deck never over a second
        road (Cadwell's Woodland footbridge would reach the start straight, whose bridge stays removed). Only where
        no footbridge position does, the stock archway gantry
        (posts at +-11 m, banner 5.32 m up mid-span) is tried the same way (Chris 2026-10-25: Cadwell's footbridge
        pillars on the track edge wrecked his car)."""
        g = self.geo
        p, t, nl = r.p[i], r.tangent[i], r.nl[i]
        base = p[:2] + nl * (need[1] - need[-1]) / 2.0     # centred between what it must clear on each side
        road_top = float(max(r.left[i, 2], r.right[i, 2], p[2]))
        yaw = yaw_to(t)
        dist = planner.field.dist
        choice = None
        for k in ("bridge", "gantry"):
            asset, leg, low = SPANS[k]
            cands = []
            for sh in sorted(np.arange(-NUDGE_M, NUDGE_M + 1e-6, 0.5), key=lambda v: (abs(v), v)):
                centre = base + nl * sh
                for s0 in np.arange(1.0, SPAN_SCALE_MAX + 1e-6, 0.04):
                    legs = span_parts(asset, centre, yaw, float(s0), legs_only=True)
                    z = float(min(g.terrain(np.column_stack([legs, np.zeros(len(legs))])).min(), road_top))
                    s = max(float(s0), (MIN_CLEARANCE + road_top - z) / low)
                    legs = span_parts(asset, centre, yaw, s, legs_only=True)
                    dl = float(dist(legs).min())
                    parts = span_parts(asset, centre, yaw, s)
                    dp = float(dist(parts).min())
                    # never a pillar, stair or base on a road or the pit lane; pillars never in a pit building (stairs
                    # may run into one: a real footbridge often lands on the pit building, Donington)
                    if (min(dl, dp) < LEG_MIN or any(self._on_pits(q, 0.5) for q in legs)
                            or any(self._on_pits(q, 0.5, lanes_only=True) for q in parts[::2])
                            or self._spans_other_road(p, centre, yaw, (leg + 2.0) * s)):
                        continue
                    full = dp >= PART_CLEAR and not any(self._on_pits(q, 0.5) for q in parts[::4])
                    tier = 0 if full else (1 if dl >= LEG_CLEAR else 2)
                    cands.append((tier, sh, s, z, dl, dp, centre))
                    if full:
                        break  # a bigger scale at this nudge gains nothing
            if cands:
                best_tier = min(c[0] for c in cands)
                pool = [c for c in cands if c[0] == best_tier]
                # every part clear: the smallest, least moved; else within 1 m of the clearest parts (pillars off the
                # verge) or pillars (on the verge), the smallest, least moved
                if best_tier:
                    m = 5 if best_tier == 1 else 4
                    top = max(c[m] for c in pool)
                    pool = [c for c in pool if c[m] >= min(top - 1.0, PART_CLEAR if m == 5 else LEG_CLEAR)]
                choice = (k, asset, low, *min(pool, key=lambda c: (c[2] + 0.1 * abs(c[1]),)))
                break  # a real span stays the footbridge whenever its pillars can stand off the roads
        if choice is None:
            self.report["warnings"].append(f"{gnt['kind']} {gnt.get('name', '')}: every pillar position is on a road, "
                                           "left out")
            return
        k, asset, low, tier, sh, s, z, dl, dp, centre = choice
        P = planner.sc.placements
        add(asset, "bridges", centre, yaw, s)
        P[-1].z = z
        name = f"{gnt['kind']} {gnt.get('name', '')}".strip()
        if tier > 0:  # approved real spans stay; report what doesn't meet the preferred clearance
            why = ("its stairs run into a pit building" if dp >= PART_CLEAR else
                   f"parts {dp:.1f} m from a road edge (preferred {PART_CLEAR:.1f})")
            self.report["warnings"].append(
                f"{name}: {why}, pillars {dl:.1f} m" + (f" (below {LEG_CLEAR:.1f}: on the verge)" if tier == 2 else "")
                + f"; kept as the {k}, approved real span")
        rep = {"kind": gnt["kind"], "name": gnt.get("name", ""), "sM": round(float(r.s[i]), 1),
               "asset": asset, "built": k, "scale": round(s, 3), "clearanceM": round(low * s + z - road_top, 2),
               "partsFromRoadM": round(dp, 2), "legsFromRoadM": round(dl, 2), "nudgeM": round(float(sh), 2),
               "problems": [], "banners": []}
        self.report["gantries"].append(rep)
        self._fixed_z = getattr(self, "_fixed_z", [])
        self._fixed_z.append((P[-1], z))
        self._spans = getattr(self, "_spans", [])
        self._spans.append({"asset": asset, "centre": np.asarray(centre, dtype=np.float64), "yaw": yaw, "s": s, "z": z,
                            "report": rep})

    def _spans_other_road(self, p: np.ndarray, centre: np.ndarray, yaw: float, half: float) -> bool:
        """Does the deck (``half`` either side of ``centre`` across, 3 m along) also pass over another road than
        the one at ``p`` (Cadwell: the start straight 18 m beside Woodland; its bridge stays removed)?"""
        X, Y = _axes(yaw)
        for rr in self.geo.routes:
            rel = rr.p[:, :2] - centre
            under = (np.abs(rel @ X) <= half) & (np.abs(rel @ Y) <= 3.0)
            if under.any() and (np.abs((rr.p[under, :2] - p[:2]) @ X) > 3.0).any():
                return True
        return False

    def _banners(self, planner, add) -> None:
        """Banners (sponsor.py span / welcome panels) on every span face a layout drives towards (``driven_faces``):
        the face of the span nearest each layout's start line along its lap carries "Welcome to <track>"
        (``sponsor.welcome``), every other driven face two or three sponsors side by side (three when each panel is
        still BANNER_MIN_H tall), rotating through ``sponsor.span_rotation`` across the circuit's faces in span order
        from a start that depends on the track's name. Each panel hangs flush on the face between the pillars, its
        lower edge FACES ``bottom`` (above the span's lowest part), no higher than ``top``."""
        from . import sponsor  # noqa: PLC0415 - sponsor imports envs only, but keep pits light

        spans = getattr(self, "_spans", [])
        rot = sponsor.span_rotation()
        wc = sponsor.welcome()
        if not spans or not (rot or wc):
            return
        faces = []
        for n, sp in enumerate(spans):
            df = driven_faces(self.geo, sp["asset"], sp["centre"], sp["yaw"], sp["s"])
            faces += [(n, f, df[f]) for f in (-1, 1) if df[f]]
        welcome: set[tuple[int, int]] = set()
        if wc:
            near: dict[str, tuple[float, tuple[int, int]]] = {}
            for n, f, passes in faces:
                for ps in passes:
                    if ps["layout"] not in near or ps["fromLineM"] < near[ps["layout"]][0]:
                        near[ps["layout"]] = (ps["fromLineM"], (n, f))
            welcome = {v[1] for v in near.values()}
        k0 = zlib.crc32(str((self.geo.model or {}).get("name", "")).encode("utf-8")) % len(rot) if rot else 0
        count = 0
        P = planner.sc.placements
        for n, f, passes in faces:
            span = spans[n]
            F, s = FACES[span["asset"]], span["s"]
            X, Y = _axes(span["yaw"])
            usable = 2.0 * F["half"] * s
            tall = (F["top"] - F["bottom"]) * s
            if (n, f) in welcome:
                h = min(usable / sponsor.WELCOME_ASPECT, tall)
                panels = [("sponsor_welcome", 0.0)]
            elif rot:
                m = min(3 if usable / (3 * sponsor.SPAN_ASPECT) >= BANNER_MIN_H else 2, len(rot))
                h = min(usable / (m * sponsor.SPAN_ASPECT), tall)
                panels = [(sponsor.asset(rot[(k0 + count + j) % len(rot)], "span", 0),
                           (j - (m - 1) / 2.0) * sponsor.SPAN_ASPECT * h) for j in range(m)]
                count += m
            else:
                continue
            plane = (F["neg"] if f < 0 else F["pos"]) * s + f * (BANNER_GAP + sponsor.SPAN_T / 2.0)
            yaw = span["yaw"] if f < 0 else span["yaw"] + math.pi   # the printed face (local -Y) outwards
            bottom = span["z"] + F["bottom"] * s
            for asset, xo in panels:
                add(asset, "bridges", span["centre"] + X * xo + Y * plane, yaw, h)
                P[-1].z = bottom
                self._fixed_z.append((P[-1], bottom))
            rep = span["report"]
            rep["banners"].append({"face": "-y" if f < 0 else "+y", "layouts": sorted({p["layout"] for p in passes}),
                                   "welcome": (n, f) in welcome, "panels": [a for a, _x in panels],
                                   "heightM": round(h, 2), "widthM": round(len(panels) * h * (
                                       sponsor.WELCOME_ASPECT if (n, f) in welcome else sponsor.SPAN_ASPECT), 2),
                                   "clearanceM": round(rep["clearanceM"] + (F["bottom"] - F["low"]) * s, 2)})

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
        z = float(g.terrain(np.array([[pole[0], pole[1], 0.0]]))[0])
        road_top = float(max(r.left[i, 2], r.right[i, 2], p[2]))
        # unscaled: the game draws #xref subscenes at their own size, so the arm reaches LIGHTS_ARM at LIGHTS_LOW
        # whatever scale we write (it was scaled up to 1.46 to reach 3 m past the centre line)
        clear = LIGHTS_LOW + z - road_top
        problems = ["the pole on a road"] if float(planner.field.dist(pole)[0]) < 0.3 else []
        if reach - LIGHTS_ARM > hw - 1.0:
            problems.append(f"the arm ends {reach - LIGHTS_ARM:.1f} m from the centre line, not over the road")
        if clear < MIN_CLEARANCE:
            problems.append(f"the arm {clear:.1f} m over the road (under {MIN_CLEARANCE:.0f})")
        over = -nl * side                       # the arm (local +X) points across the track
        add(LIGHTS_ASSET, "gantry", pole, math.atan2(over[1], over[0]))
        planner.sc.placements[-1].z = z
        self.report["gantries"].append({"kind": "startLights", "name": gnt.get("name", ""), "sM": round(float(r.s[i]), 1),
                                        "asset": LIGHTS_ASSET, "scale": 1.0, "clearanceM": round(clear, 2),
                                        "poleFromCentreM": round(reach, 1), "armReachM": LIGHTS_ARM,
                                        "armShortOfCentreM": round(max(reach - LIGHTS_ARM, 0.0), 1),
                                        "problems": problems})
        self._fixed_z = getattr(self, "_fixed_z", [])
        self._fixed_z.append((planner.sc.placements[-1], z))

    def after_heights(self, planner) -> None:
        """dressing._heights puts every prop on the terrain; gantries and bridges keep their own base height."""
        for p, z in getattr(self, "_fixed_z", []):  # Placement objects: the sponsor sight lines may drop others
            p.z = z
