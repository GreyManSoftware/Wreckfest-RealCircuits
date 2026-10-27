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
from . import envs, sponsor
from .fields import DistanceField
from .geometry import dist_to_polyline
from .noise import Noise2D, seed_for
from .woods import WoodMask

# categories that may stand inside measured run-off (the gantry spans the road; flags, staff and the start grandstand
# belong to the grid sector)
RUNOFF_EXEMPT = ("gantry", "flags", "staff", "grandstand", "barriers", "shore", "brakeBoards",
                 "pitWall", "bridges")  # the pit wall is the containment on its stretch; bridges span the road

FORMAT = "trackgen-scenery/1"

# The game draws #xref subscenes at their own size: the scale a placement carries is written to the scene (as stock
# bases write it) but not applied in game (2026-10-26: Navarra's footbridge at 1.8 stood unscaled, the span banners
# laid out for it floated 6.06 * (scale - 1) m above its side walls, Brands' grandstands at 0.55 overlapped at full
# size). So every stock prop is placed at scale 1 and planned at its true size, except the categories here, whose
# scale is only a little random variety (at most this far from 1) where a slightly different size breaks nothing:
# foliage and loose rocks and bales, kept clear of the road at their true size (try_place). Sponsor boards are our
# own mesh, built at their size (``scale`` is a banner's height there), not #xref. check_scene enforces this list;
# worker/docs/visual-assets.md explains it.
XREF_SCALE_JITTER = {"trees": 0.25, "bushes": 0.15, "saplings": 0.3, "groundProps": 0.15}

# ---- braking boards (stock gameplay_distanceboard{150,100,50}: breakable 2 x 1 m panels, numbers on local +X)
BRAKE_BOARDS = (150, 100, 50)    # metres before the turn-in, furthest first; a zone gets all three or none
BRAKE_MAX_RADIUS = 80.0          # a heavy-braking corner's apex radius is at most this...
BRAKE_MIN_TURN_DEG = 30.0        # ...it turns through at least this much...
BRAKE_MIN_APPROACH = 175.0       # ...after at least this much straight-ish road (no corner tighter than
#                                  C.KERB_CORNER_RADIUS between the previous corner's exit and the turn-in)...
BRAKE_MIN_DROP_KMH = 80.0        # ...and the lap-speed estimate drops by at least this much into it
BRAKE_SPEED = {"vtop": 55.0, "lat": 11.0, "acc": 4.0, "dec": 9.0}  # m/s, m/s^2: a rough Wreckfest car
BRAKE_GAP = 2.0                  # board's near end this far beyond the road edge (or kerb / green strip)
BRAKE_HALF = 1.0                 # half the board's width (across the road)
BRAKE_APPROACH_SLACK = 5.0       # a board stands at least this far after the previous corner's exit
BRAKE_DEDUP_M = 6.0              # boards of two layouts this close (same number, same facing) are one
BRAKE_BACK_M = (0.0, 3.0, 6.0, 10.0, -3.0, -6.0, -10.0)  # a blocked board moves this much further back (- nearer)...
BRAKE_OUT_M = (0.0, 1.5, 3.0, 5.0)               # ...and / or this much further out from the road edge
BRAKE_SPACING_TOL = 10.0         # so each stands within this of its distance (150 > 100 > 50 always holds)


def yaw_to(d) -> float:
    """Yaw that turns local +Y towards direction d (x, y)."""
    return math.atan2(-float(d[0]), float(d[1]))


def yaw_x_to(d) -> float:
    """Yaw that turns local +X towards direction d (x, y)."""
    return math.atan2(float(d[1]), float(d[0]))


def layout_routes(geo) -> list[tuple[str, object, int]]:
    """The closed lap each layout drives: (layout key, Route, direction along s). An imported track's
    alternative layouts drive their composite routes (``layout.composite_route``); a drawn track has the main
    route forward and, with ``reverse``, backwards."""
    model = geo.model
    if model.get("layouts"):
        from .layout import composite_route
        return [("forward" if d["id"] == "main" else d["id"], geo.main if d["id"] == "main"
                 else composite_route(model, d), 1) for d in model["layouts"]]
    out = [("forward", geo.main, 1)]
    if model.get("reverse"):
        out.append(("reverse", geo.main, -1))
    return out


START_BEFORE_M = 150.0           # a layout's start view: from this far before its line (the finish approach)...
START_BEYOND_M = 220.0           # ...to this far past its front row, every START_STEP_M
START_STEP_M = 10.0


def layout_starts(geo) -> list[dict]:
    """Each layout's start view (for the sponsor guarantee): its route, driving direction, line s and the driver's
    eye points (grid slots first, then the road from START_BEFORE_M before the line to START_BEYOND_M past the
    front row) with the driving direction at each. A drawn track's reverse layout uses the forward grid driven
    the other way, its line 4 m past the forward pole (``layout.LayoutBuilder.startpoints``); an imported
    layout with its own line (``startFinish``) its own grid along its composite route."""
    model = geo.model
    defs = {d["id"]: d for d in model.get("layouts") or []}
    out = []
    for key, r, direction in layout_routes(geo):
        d = defs.get("main" if key == "forward" else key, {})
        slots = (d.get("grid") or model.get("grid") or {}).get("slots", [])
        if direction > 0:
            line_s = 0.0
            ahead = max([float(sl["s"]) for sl in slots] + [0.0])
        else:
            front = max([float(sl["s"]) for sl in slots] + [0.0])
            back = min([float(sl["s"]) for sl in slots] + [0.0])
            line_s = (front + 4.0) % r.length
            ahead = front + 4.0 - back
        eyes, dirs = [], []
        for sl in slots:
            p = np.asarray(sl["position"], dtype=np.float64)
            i = int(np.argmin(np.hypot(r.p[:, 0] - p[0], r.p[:, 1] - p[1])))
            eyes.append((p[0], p[1], p[2] + sponsor.EYE_M))
            dirs.append(r.tangent[i] * direction)
        for u in np.arange(-START_BEFORE_M, ahead + START_BEYOND_M + 1e-6, START_STEP_M):
            i = r.index_at((line_s + direction * u) % r.length)
            eyes.append((r.p[i, 0], r.p[i, 1], r.p[i, 2] + sponsor.EYE_M))
            dirs.append(r.tangent[i] * direction)
        out.append({"layout": key, "route": r, "direction": direction, "lineS": line_s, "aheadM": ahead,
                    "eyes": np.array(eyes, dtype=np.float64).reshape(-1, 3),
                    "dirs": np.array(dirs, dtype=np.float64).reshape(-1, 2)})
    return out


LAP_STEP_M = 10.0  # the lap view (sponsor lap guarantee): the driver's eye every this many metres


def layout_laps(geo) -> list[dict]:
    """Each layout's whole lap as driver's eye points (1.2 m above the route every LAP_STEP_M) with the driving
    direction at each, for the sponsor lap guarantee."""
    out = []
    for key, r, direction in layout_routes(geo):
        idx = [r.index_at(float(s)) for s in np.arange(0.0, r.length, LAP_STEP_M)]
        eyes = np.column_stack([r.p[idx, :2], r.p[idx, 2] + sponsor.EYE_M])
        out.append({"layout": key, "route": r, "direction": direction, "eyes": eyes,
                    "dirs": r.tangent[idx] * direction})
    return out


def _smoothed_curvature(geo, r) -> np.ndarray:
    seg = float(np.median(r.seg_len())) if r.n > 1 else 2.0
    w = max(1, int(round(C.CORNER_SMOOTH_M / max(seg, 1e-6) / 2)))
    ker = np.ones(2 * w + 1) / (2 * w + 1)
    k = r.curvature
    return (np.convolve(np.concatenate([k[-w:], k, k[:w]]), ker, mode="valid") if r.closed
            else np.convolve(np.pad(k, w, mode="edge"), ker, mode="valid"))


def speed_profile(geo, r, direction: int) -> np.ndarray:
    """A rough lap-speed estimate (m/s) per sample of route r driven ``direction`` along s: the cornering limit
    sqrt(lat / |curvature|) capped at vtop, with acceleration and braking limits (forward and backward passes,
    twice round a closed lap)."""
    p = BRAKE_SPEED
    k = np.abs(_smoothed_curvature(geo, r))
    vlim = np.minimum(p["vtop"], np.sqrt(p["lat"] / np.maximum(k, 1e-9)))
    order = np.arange(r.n) if direction > 0 else np.arange(r.n)[::-1]
    seg = r.seg_len()
    v = vlim.copy()
    loops = 2 if r.closed else 1

    def ds(a: int, b: int) -> float:  # distance between neighbouring samples a -> b
        return float(seg[min(a, b)] if abs(a - b) == 1 else seg[-1])

    for _ in range(loops):  # accelerate in the driving direction
        for n in range(1, len(order) + (1 if r.closed else 0)):
            a, b = order[n - 1], order[n % len(order)]
            v[b] = min(v[b], math.sqrt(v[a] ** 2 + 2 * p["acc"] * ds(a, b)))
    for _ in range(loops):  # brake: backwards against it
        for n in range(len(order) - 2 + (1 if r.closed else 0), -1, -1):
            a, b = order[n], order[(n + 1) % len(order)]
            v[a] = min(v[a], math.sqrt(v[b] ** 2 + 2 * p["dec"] * ds(a, b)))
    return v


def corner_name(geo, r, i: int) -> str:
    """The imported track's corner label at sample i of route r (``geometry._named`` of the nearest main-route
    point, when route r's sample lies on the main road there), else ''."""
    if not (geo.imported or {}).get("corners"):
        return ""
    if r is geo.main:
        return geo._named(float(r.s[i]))
    m = geo.main
    j = int(np.argmin(np.hypot(m.p[:, 0] - r.p[i, 0], m.p[:, 1] - r.p[i, 1])))
    return geo._named(float(m.s[j])) if math.hypot(*(m.p[j, :2] - r.p[i, :2])) < 8.0 else ""


def brake_overrides(geo) -> dict[str, set[str]]:
    """An imported track's corrections to the braking-zone rule (recipe ``brakeBoards``, carried in
    ``sketch.imported.brakeBoards``): corner names to ``add`` (braking boards whatever the rule says) and to
    ``remove`` (no boards)."""
    o = (geo.imported or {}).get("brakeBoards") or {}
    return {"add": set(o.get("add") or []), "remove": set(o.get("remove") or [])}


def brake_zones(geo, r, direction: int) -> list[dict]:
    """The heavy-braking corners of route r driven ``direction`` along s: corners (``corner_runs``) with an apex
    radius up to BRAKE_MAX_RADIUS, turning at least BRAKE_MIN_TURN_DEG, after at least BRAKE_MIN_APPROACH of
    straight-ish road (from the previous kerbed corner's exit), into which the lap-speed estimate (``speed_profile``:
    the entry speed the approach allows, down to the corner's cornering limit) drops by at least
    BRAKE_MIN_DROP_KMH; plus the corners an imported track's ``brakeBoards.add`` names (the named corner's run with
    the longest approach) and less the ones ``brakeBoards.remove`` names. Per zone: the turn-in sample and s (the
    corner's first sample in driving order), the outside side (+1 left of the route's s direction), the apex
    radius, the approach (metres from the previous corner's exit to the turn-in), the speeds, the corner's name
    and ``why`` (``rule`` or ``add``)."""
    if r.n < 8 or not r.closed:
        return []
    runs = geo.corner_runs(r)
    if not runs:
        return []
    v = speed_profile(geo, r, direction)
    L = r.length
    ov = brake_overrides(geo)

    def ahead(a: float, b: float) -> float:  # metres driven from s=a to s=b
        return ((b - a) * direction) % L

    ends = [(float(r.s[c["i_in" if direction > 0 else "i_out"]]), float(r.s[c["i_out" if direction > 0 else "i_in"]]))
            for c in runs]
    cands = []
    for c, (s_in, _s_out) in zip(runs, ends):
        # the approach: back to the exit of the previous corner in driving order (any kerbed-radius corner)
        prev = [ahead(e_out, s_in) for (e_in, e_out) in ends if (e_in, e_out) != (s_in, _s_out)]
        approach = min(prev) if prev else L - ahead(s_in, _s_out)
        # speeds: the peak over the approach (up to 250 m back) and the slowest in the corner
        back = r.signed_s(r.s, s_in) * direction
        win = (back <= 0.0) & (back >= -min(approach, 250.0))
        lo, hi = sorted((c["i_in"], c["i_out"]))
        idx = np.arange(lo, hi + 1) if hi - lo < r.n / 2 else np.r_[hi:r.n, 0:lo + 1]
        v_peak = float(v[win].max()) if win.any() else float(v[c["i_in"]])
        v_min = float(v[idx].min())
        drop = (v_peak - v_min) * 3.6
        rule = (c["radius"] <= BRAKE_MAX_RADIUS and c["turnDeg"] >= BRAKE_MIN_TURN_DEG
                and approach >= BRAKE_MIN_APPROACH and drop >= BRAKE_MIN_DROP_KMH)
        i_in = c["i_in"] if direction > 0 else c["i_out"]
        cands.append((rule, {"i_in": int(i_in), "s_in": s_in, "outside": -int(c["inside"]),
                             "radius": float(c["radius"]), "turnDeg": float(c["turnDeg"]),
                             "approach": float(approach), "vPeak": v_peak, "vMin": v_min, "dropKmh": drop,
                             "i_apex": int(c["i_apex"]), "corner": corner_name(geo, r, int(c["i_apex"]))}))
    out = []
    for rule, z in cands:
        if rule and z["corner"] not in ov["remove"]:
            out.append({**z, "why": "rule"})
    for name in sorted(ov["add"] - ov["remove"]):
        named = [z for _rule, z in cands if z["corner"] == name]
        if named and not any(z["corner"] == name for z in out):
            out.append({**max(named, key=lambda z: z["approach"]), "why": "add"})
    return sorted(out, key=lambda z: z["s_in"])


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
            # our own meshes (sponsor boards, "mesh": true) have no stock path
            "assets": {a: {"path": None if envs.asset_info(a).get("mesh") else f"data/art/objects/{a}.scne",
                           **{k: v for k, v in envs.asset_info(a).items()}} for a in assets},
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
        # an imported track's real woods (OSM polygons) replace the seeded tree clusters
        polys = [np.array([[q["x"], q["y"]] for q in w["points"]]) for w in
                 ((getattr(geo, "imported", None) or {}).get("woodland") or [])]
        self.woods = WoodMask(polys, self.frame) if polys else None
        # the sea (an imported track's seaLevelM, in environments with a sea): nothing but shore rocks below it
        imp = getattr(geo, "imported", None) or {}
        self.sea = float(imp["seaLevelM"]) if (self.e.get("sea") and imp.get("seaLevelM") is not None) else None
        # lakes (imported, environments with "lake"): nothing placed on the water or within ~3 m of its shore
        lakes = ([np.array([[q["x"], q["y"]] for q in lk["points"]]) for lk in (imp.get("lakes") or [])]
                 if self.e.get("lake") else [])
        self.lakes = WoodMask(lakes, self.frame, soft=3.0) if lakes else None
        self.main = geo.main
        # an imported track's measured run-off and vegetation (runoff.py; sketch.imported.trees / woodland cover):
        # nothing stands in a gravel trap, on an apron or between a road edge and its barrier, and only the real
        # trees are planted (no seeded scatter, no single park trees, no edge belt)
        self.runoff = getattr(geo, "runoff", None)
        self.measured = bool(self.runoff is not None and self.runoff.active)
        self.real_trees = [(float(t["x"]), float(t["y"]), float(t["h"])) for t in (imp.get("trees") or [])]
        covers = [w.get("cover") for w in (imp.get("woodland") or [])]
        self.measured_veg = bool(self.real_trees) or any(c is not None for c in covers)
        self.wood_cover_mask = (WoodMask(polys, self.frame, soft=1.0, weights=[c if c is not None else 1.0 for c in covers])
                                if polys and self.measured_veg else None)
        self._nogo()

    def dry(self, x: float, y: float, above: float = 0.8) -> bool:
        """Land at least ``above`` metres over the sea (always true without a sea) and not in a lake."""
        if self.lakes is not None and float(self.lakes.at([x, y])[0]) > 0.02:
            return False
        return self.sea is None or float(self.geo.ground(np.array([[x, y]]))[0]) >= self.sea + above

    # ------------------------------------------------------------------------------------- woods
    def wood_cover(self, pts: np.ndarray) -> np.ndarray:
        """~0 in the open, 0.5 on a wood's edge, ~1 inside: the imported woods, else the seeded tree clusters."""
        if self.woods is not None:
            return self.woods.at(pts)
        return np.clip(0.5 + (self.tree_noise.at(pts) - self.tree_thr) / 0.24, 0.0, 1.0)

    def canopy_grid(self, n: int) -> np.ndarray:
        """How deep inside the trees each map pixel is (0 at a wood's edge, 1 well inside), for maps.py."""
        if self.woods is not None:
            g = self.woods.grid(n)
            return np.clip((g - 0.5) / 0.4, 0.0, 1.0) ** 0.8
        t = self.tree_noise.grid(n)
        u = np.clip((t - self.tree_thr) / 0.12, 0.0, 1.0)
        return u * u * (3 - 2 * u)

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
            lo, hi = m.grid_span(slots)  # behind the line on a real track (its grid behind the start line)
            s_hi = max(hi + 15.0, 25.0)
            s_lo = min(-25.0, lo - 15.0)
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
        if abs(scale - 1.0) > XREF_SCALE_JITTER.get(category, 0.0) + 1e-9:
            raise ValueError(f"{asset} ({category}) at scale {scale:.2f}: the game draws #xref props unscaled")
        rr = float(info["r"] if r is None else r)  # the true size: the game ignores the scale (XREF_SCALE_JITTER)
        # Long, thin assets laid along the road (fences, grandstands) only reach towards it by half their depth.
        edge_r = rr
        if "width" in info and info.get("face") != "x":
            edge_r = float(info.get("depth", 1.0)) / 2.0
        if not self.inside(x, y, margin + rr):
            return False
        if float(self.field.dist([x, y])[0]) - edge_r < min_edge:
            return False
        if self.in_nogo([x, y], rr, exempt)[0]:
            return False
        if not self.occ.free(x, y, rr):
            return False
        if category != "shore" and not self.dry(x, y):
            return False
        if self.measured and category not in RUNOFF_EXEMPT and self.in_runoff(np.array([[x, y]]), rr)[0]:
            return False
        if occupy:
            self.occ.add(x, y, rr)
        self.sc.placements.append(Placement(asset, category, float(x), float(y), 0.0, float(yaw), scale))
        return True

    def in_runoff(self, xy: np.ndarray, r: float = 0.0) -> np.ndarray:
        """Inside a measured gravel trap or apron, or between a road edge and its barrier (the centre and four
        points ``r`` away)."""
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        out = np.zeros(len(xy), dtype=bool)
        for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r)) if r > 0 else ((0, 0),):
            q = xy + np.array([dx, dy])
            out |= (self.runoff.at(q) > 0) | self.runoff.at(q, "band")
        return out

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
        return self.geo.corner_apexes()

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
    def start_finish(self, gantry: bool = True, grandstand: bool = True) -> int:
        """The start/finish gantry, a grandstand, flags and staff by the line. An imported track with real pits
        (pits.py) has its own start lights and grandstands: then ``gantry`` / ``grandstand`` are False."""
        r = self.main
        i0 = 0  # the start/finish line is at s = 0 (layout.py)
        p, t = r.p[i0, :2], r.tangent[i0]
        hw = r.w[i0] / 2.0
        legs = np.array([p + r.nl[i0] * 16.35, p - r.nl[i0] * 16.35])
        # unscaled (the game draws #xref props at their own size): only where its legs stand beyond the barrier
        if gantry and hw + C.WALL_OFFSET + 1.5 <= 16.0 and (self.field.dist(legs) > 1.5).all():
            # spans the road (the only asset allowed on it); exempt from the road clearance
            self.sc.placements.append(Placement("prop_start_startlights1", "gantry", float(p[0]), float(p[1]), 0.0,
                                                yaw_to(t)))
        room = []
        for side in (1, -1):
            q, _, _ = self.side_point(r, i0, side, 60.0)
            room.append((float(self.field.dist(q)[0]), side))
        room.sort(reverse=True)
        g_side = room[0][1]
        placed = not grandstand
        for asset in ([] if placed else self.common["grandstand"]):
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
        for k in range(0):  # no trackside staff: they would stand in the open (Chris 2026-10-25: no people without a barrier)
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
        if self.measured:
            # a real circuit: behind the real barrier, no fence line of our own (Chris 2026-10-24: Anglesey's long
            # spectator fence);
            # spectators only ever stand behind a real barrier that is built (the wall plan: a measured barrier the
            # layout rule or the pit wall didn't open), never in the open (Chris 2026-10-25)
            win = self.walk(r, i, length + 6.0, 2.0)
            p = self.geo.wall_plan().get((r.index, side))
            if p is None or not bool(p["ok"][win].all()) or any(str(t) not in ("armco", "tyres", "concrete")
                                                                  for t in p["type"][win]):
                return 0
            off_f = max(off_f, float(np.max(p["offset"][win])) + 3.0)
            if off_f > C.MEASURED_CROWD_MAX_M:
                return 0
        else:  # drawn tracks: the crowd barrier (wall plan "crowd") must be built in front of the whole crowd
            win = self.walk(r, i, length + 6.0, 2.0)
            p = self.geo.wall_plan().get((r.index, side))
            if self.geo.walls == "none" or p is None or not bool(p["ok"][win].all()):
                return 0
            off_f = max(off_f, float(np.max(p["offset"][win])) + 1.0)
        for k in ([] if self.measured else self.walk(r, i, length + 6.0, fw)):
            q, tw, tg = self.side_point(r, k, side, off_f)
            self.try_place(fence, "crowdFence", q[0], q[1], self.face_yaw(fence, tw, tg), off_f - 1.0, r=fw / 2.0)
        fills = (0.8, 0.6, 0.4)
        for row in range(3):
            for k in self.walk(r, i, length, 1.25):
                if self.rng.random() > fills[row]:
                    continue
                q, tw, tg = self.side_point(r, k, side, off_f + 1.6 + 1.4 * row + float(self.rng.uniform(-0.3, 0.3)))
                q = q + tg * float(self.rng.uniform(-0.35, 0.35))
                if float(self.field.dist(q)[0]) < off_f + 0.5 + 1.4 * row:
                    continue  # another road (a layout's branch, the far side of a hairpin) is nearer: no barrier there
                a = self.pick(self.common["spectators"])
                self.try_place(a, "spectators", q[0], q[1], yaw_to(tw) + float(self.rng.normal(0, 0.25)),
                               off_f + 0.5, r=0.55)
        q, _, _ = self.side_point(r, i, side, off_f + 3.0)
        if len(self.sc.placements) > n0:
            self.sc.zones.append(Zone("crowd", float(q[0]), float(q[1]), length / 2.0 + 4.0))
        return len(self.sc.placements) - n0

    CAMERA_ASSET = "prop_cameratower"
    CAMERA_SHARE = 0.35      # of the crowd spots (corners, viewing spots) get a tower, as when it came with the crowd
    CAMERA_BACK = 8.0        # beyond the reset wall (or the barrier, the run-off) on that stretch
    CAMERA_APART = 120.0
    CAMERA_ALONG = (30.0, 45.0, 15.0, 0.0, 60.0)   # the corner's exit first (Chris 2026-10-26: Anglesey)

    def camera_towers(self) -> None:
        """TV camera towers on open grass at the crowd spots (``crowd_spots``: corners and viewing spots), with or
        without spectators (Chris 2026-10-26: they had vanished with the crowds that need a built barrier): a
        CAMERA_SHARE of the spots, by their own seeded draw; each on the outside, past the corner exit first,
        CAMERA_BACK behind the furthest of the barrier line, the AI route's reach plus the reset wall gap and the
        measured run-off over 20 m of road, so neither the AI nor a car running wide reaches it."""
        from . import footprint
        from .bounds import RESET_GAP
        a = self.CAMERA_ASSET
        if a not in self.allowed:
            return
        rng = np.random.default_rng(seed_for(self.slug, "cameras"))
        half = max(abs(v) for v in footprint.EXTENTS[a])  # the tower's platform, 5.8 x 7 m
        plan = self.geo.wall_plan()
        placed: list[np.ndarray] = []
        for r, i, side, _length in self.geo.crowd_spots():
            if rng.random() >= self.CAMERA_SHARE:
                continue
            p = plan.get((r.index, side))
            for along in self.CAMERA_ALONG:
                s = (r.s[i] + along) % r.length if r.closed else min(max(r.s[i] + along, 0.0), r.length)
                j = r.index_at(s)
                win = self.walk(r, j, 20.0, 0.0)
                clear = self.edge_min + 2.0
                if p is not None:
                    clear = max(clear, float(np.max(np.where(p["ok"][win], p["offset"][win], 0.0))),
                                float(np.max(p["ai_margin"][win])) + RESET_GAP)
                off = clear + self.CAMERA_BACK + half
                q, tw, tg = self.side_point(r, j, side, off)
                if any(float(np.hypot(*(q - o))) < self.CAMERA_APART for o in placed):
                    break
                if self.measured and bool(self.in_runoff(q[None, :], half + self.CAMERA_BACK)[0]):
                    continue
                ring = q + half * np.array([[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1]], dtype=np.float64)
                if float(self.wood_cover(ring).max()) > 0.3:
                    continue  # open grass, not in a wood
                if self.try_place(a, "cameras", q[0], q[1], self.face_yaw(a, tw, tg), off - half - 1.0):
                    placed.append(q)
                    break
        self.sc.info["cameraTowers"] = len(placed)

    def viewing_spots(self, corners) -> None:
        """Crowds at the geometry's crowd spots (jump landings, beside bridges, named viewing spots, the outside of
        the sharpest corners), each behind the barrier the wall plan puts in front of it."""
        done = 0
        for r, i, side, length in self.geo.crowd_spots():
            if self.crowd(r, i, side, length) > 4:
                done += 1
        self.sc.info["viewingSpots"] = done

    # ------------------------------------------------------------------------------- road-side props
    PANEL_GAP = 0.04    # ad fence panels end to end this far apart: overlapping panels flicker (Chris 2026-10-26)
    BOARD_CLEAR = 8.0   # no ad fence panels this near a grandstand or building: they carry their own boards

    def _boarded(self) -> list[np.ndarray]:
        """Plan outlines of the grandstands and buildings placed so far, and of an imported track's real pit
        buildings and grandstands (reserved, placed later): ad panels keep BOARD_CLEAR from them."""
        from . import footprint
        out = [footprint.rect(p.asset, p.x, p.y, p.yaw, p.scale, envs.asset_info(p.asset))
               for p in self.sc.placements if p.category in ("grandstand", "realGrandstand", "pitBuildings", "buildings")]
        pits = getattr(self.geo, "pits", None)
        if pits is not None:
            out += [poly for _b, poly in pits.buildings + pits.grandstands if len(poly) >= 3]
        return out

    def _panel_line(self, r, i: int, side: int, run: float, off: float, width: float):
        """Ad fence panels (``width`` wide) end to end along the line ``off`` beyond the road edge, over ``run`` m
        of road centred on sample i: each on the chord between two points ``width`` apart along the line (so
        neighbours never overlap, on curves too), PANEL_GAP between them. Yields (centre, towards the road,
        tangent)."""
        js = self.walk(r, i, run, 0.0)
        if len(js) < 2:
            return
        pts = np.array([self.side_point(r, j, side, off)[0] for j in js])
        u = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        step = width + self.PANEL_GAP
        for a in np.arange(0.0, u[-1] - width + 1e-6, step):
            p0 = np.array([np.interp(a, u, pts[:, 0]), np.interp(a, u, pts[:, 1])])
            p1 = np.array([np.interp(a + width, u, pts[:, 0]), np.interp(a + width, u, pts[:, 1])])
            tg = (p1 - p0) / max(float(np.hypot(*(p1 - p0))), 1e-9)
            tw = np.array([-tg[1], tg[0]])
            j = js[int(np.clip(np.searchsorted(u, a + width / 2.0), 0, len(js) - 1))]
            if float(tw @ self.side_point(r, j, side, off)[1]) < 0:
                tw = -tw
            yield (p0 + p1) / 2.0, tw, tg

    def ad_fences(self, straights) -> None:
        from . import footprint
        r = self.main
        n = 0
        share = float(sponsor.config().get("fenceShare", 0.0)) if sponsor.with_kind("fence") else 0.0
        pick = self.sponsor_picker()
        blocks: dict[int, str | None] = {}
        boarded = self._boarded()
        width = float(envs.asset_info(self.common["adFence"][0])["width"])
        for k, (i, length) in enumerate(straights[:6]):
            side = 1 if k % 2 == 0 else -1
            run = min(length - 10.0, 80.0)
            for q, tw, tg in self._panel_line(r, i, side, run, self.edge_min + 1.0, width):
                if any(footprint.dist_to_poly(q, b) < self.BOARD_CLEAR for b in boarded):
                    continue  # a grandstand's or building's own boards are there
                block = n // 6
                # a share of the 6-panel blocks are our own sponsor panels (evenly spread: Bresenham on the share),
                # each block one sponsor and design (sponsor.Picker: the fence sponsors by share, designs rotating)
                if block not in blocks:
                    blocks[block] = None
                    if share > 0 and math.floor((block + 1) * share) > math.floor(block * share):
                        blocks[block] = pick.peek("fence")
                        if blocks[block]:
                            pick.commit(blocks[block])
                if blocks[block]:
                    a, cat = blocks[block], sponsor.CATEGORY
                else:
                    a, cat = self.common["adFence"][block % len(self.common["adFence"])], "adFence"
                if self.try_place(a, cat, q[0], q[1], self.face_yaw(a, tw, tg), self.edge_min - 0.5, r=1.92):
                    n += 1
            if k < 2:
                q, tw, tg = self.side_point(r, i, -side, self.edge_min + 14.0)
                a = self.pick(self.common["bigBoard"])
                self.try_place(a, "bigBoard", q[0], q[1], self.face_yaw(a, tw, tg), self.edge_min + 8.0)

    def sponsor_billboards(self, straights) -> None:
        """Our sponsor boards on posts (sponsor.py) at the classic spots: beside the start/finish straight facing
        the grid's view and across from the grandstand, then on the outside of the biggest braking zones
        (``info.brakeBoards``) at the turn-in, then mid-straight. The first spots get the big billboards (up to
        ``billboards``, each sponsor up to its own cap, by share: ``sponsor.Picker``), the rest mini billboards
        (up to ``minis``), one board per spot. A billboard stands BILLBOARD_OFF metres beyond the road edge (the
        nearest that fits), a mini MINI_OFF, behind the run-off clearance, its printed face (local -Y) turned to
        the oncoming view: between straight back up the road and square to it."""
        if not sponsor.enabled():
            return
        cfg = sponsor.config()
        caps = {"billboard": int(cfg.get("billboards", 6)), "minibillboard": int(cfg.get("minis", 0))}
        pick = self.sponsor_picker()
        r0 = self.main
        spots: list[tuple[object, int, int, int, str]] = []  # (route, sample, side, direction, why)
        L = r0.length
        for s_off in (90.0, -140.0):  # the start/finish straight, ahead of the grid and before the line
            i = r0.index_at(s_off % L)
            spots.append((r0, i, 1 if s_off > 0 else -1, 1, "startFinish"))
        gs = next((p for p in self.sc.placements if p.category == "grandstand"), None)
        if gs is not None:  # facing the grandstand, across the road from it
            j = int(np.argmin(np.hypot(r0.p[:, 0] - gs.x, r0.p[:, 1] - gs.y)))
            side = 1 if float(np.dot([gs.x - r0.p[j, 0], gs.y - r0.p[j, 1]], r0.nl[j])) > 0 else -1
            spots.append((r0, r0.index_at((r0.s[j] + 30.0) % L), -side, 0, "grandstand"))
        zones = sorted((self.sc.info.get("brakeBoards") or {}).get("zones", []), key=lambda z: -z["dropKmh"])
        routes = {key: (r, d) for key, r, d in layout_routes(self.geo)}
        for z in zones:
            r, d = routes[z["layout"]]
            spots.append((r, r.index_at(z["turnInS"]), 1 if z["side"] == "left" else -1, d, "braking"))
        for k, (i, length) in enumerate(straights[:6]):  # mid-straight, then (minis) the other side
            spots.append((r0, i, -1 if k % 2 == 0 else 1, 1, "straight"))
        for k, (i, length) in enumerate(straights[:6]):
            spots.append((r0, i, 1 if k % 2 == 0 else -1, 1, "straight"))
        n = {"billboard": 0, "minibillboard": 0}
        used: list[tuple[float, float, str]] = []
        for r, i, side, direction, why in spots:
            kind = "billboard" if n["billboard"] < caps["billboard"] and pick.peek("billboard") else "minibillboard"
            if n[kind] >= caps[kind]:
                continue
            a = pick.peek(kind)
            if a is None:
                continue
            info = envs.asset_info(a)
            for off in (self.BILLBOARD_OFF if kind == "billboard" else self.MINI_OFF):
                q, tw, tg = self.side_point(r, i, side, max(off, self.edge_min + (4.0 if kind == "billboard" else 2.0)))
                if any(math.hypot(q[0] - x, q[1] - y) < (120.0 if kind == k2 == "billboard" else 60.0)
                       for x, y, k2 in used):
                    break  # one board per spot (two layouts' zones on the same corner)
                # printed face towards the road and back up it (towards the oncoming view)
                view = tw if direction == 0 else tw + (-tg * direction) * 1.0
                view = view / max(float(np.linalg.norm(view)), 1e-9)
                yaw = yaw_to((-view[0], -view[1]))
                ax = np.array([math.cos(yaw), math.sin(yaw)])
                ends = np.array([q + ax * info["width"] / 2.0, q - ax * info["width"] / 2.0])
                if (self.field.dist(ends) < self.edge_min + 2.0).any() or self.in_nogo(ends, 1.0, False).any():
                    continue
                if self.try_place(a, sponsor.CATEGORY, q[0], q[1], yaw, self.edge_min + 2.0, r=info["r"]):
                    pick.commit(a)
                    used.append((float(q[0]), float(q[1]), kind))
                    n[kind] += 1
                    break
        self.sc.info["sponsorBillboards"] = n["billboard"]
        self.sc.info["sponsorMinis"] = n["minibillboard"]

    def sponsor_picker(self) -> sponsor.Picker:
        """The planner's sponsor.Picker (counts per sponsor and kind across all sponsor placements)."""
        if getattr(self, "_sponsor_pick", None) is None:
            self._sponsor_pick = sponsor.Picker()
        return self._sponsor_pick

    BILLBOARD_OFF = (16.0, 22.0, 30.0, 40.0)  # metres beyond the road edge, nearest first
    MINI_OFF = (0.0, 6.0, 12.0, 20.0)  # mini billboards: at least edgeMinM + 2, nearest first
    GUARANTEE_OFF = (12.0, 16.0, 22.0, 30.0, 40.0, 50.0)  # the relaxed band (first fallback), nearest first
    SIGHT_KEEP = {"gantry", "grandstand", sponsor.CATEGORY,
                  "pitWall", "pitBuildings", "realGrandstand", "bridges"}  # + the real pits (pits.py)  # never removed to clear a guaranteed board's view

    def _terrain_xy(self, xy) -> np.ndarray:
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        return self.geo.terrain(np.column_stack([xy, np.zeros(len(xy))]))

    def _blockers(self) -> sponsor.Blockers:
        P = self.sc.placements
        z = self._terrain_xy([(p.x, p.y) for p in P]) if P else np.zeros(0)
        return sponsor.Blockers([(p.asset, p.category, p.x, p.y, float(z[n]), p.yaw, p.scale)
                                 for n, p in enumerate(P)])

    def _sees(self, st: dict, asset: str, x: float, y: float, yaw: float, blockers, skip: int = -1):
        z = float(self._terrain_xy([(x, y)])[0])
        return sponsor.visible(asset, x, y, z, yaw, st["eyes"], st["dirs"], self._terrain_xy, blockers, skip)

    def _board(self, st: dict, asset: str, q, yaw: float, min_edge: float, blockers) -> int:
        """Place one handle board at q if it fits (behind ``min_edge``, both ends clear of the roads and no-go
        zones, free ground) and is in view of the layout's start (``sponsor.visible``); returns the seeing eye's
        index, or -1 (nothing placed)."""
        info = envs.asset_info(asset)
        ax = np.array([math.cos(yaw), math.sin(yaw)])
        ends = np.array([q + ax * info["width"] / 2.0, q - ax * info["width"] / 2.0])
        if (self.field.dist(ends) < min_edge).any() or self.in_nogo(ends, 1.0, False).any():
            return -1
        if not self.try_place(asset, sponsor.CATEGORY, q[0], q[1], yaw, min_edge, r=info["r"], occupy=False):
            return -1
        ok, eye = self._sees(st, asset, float(q[0]), float(q[1]), yaw, blockers)
        if not ok:
            self.sc.placements.pop()
            return -1
        self.occ.add(float(q[0]), float(q[1]), float(info["r"]))
        return eye

    def sponsor_guarantee(self) -> None:
        """For each sponsor with ``guarantee``, at least one of its boards in clear view of every layout's start
        (``layout_starts``: its grid and its start/finish straight; ``sponsor.visible``: range, view cone, facing
        and sight lines over the terrain and the larger props). A board already placed counts; otherwise, falling
        back in turn, with the sponsor's own board kinds: (1) a posted board (billboard, else mini billboard) at
        the start-straight spots in the relaxed band GUARANTEE_OFF; (2) one anywhere along the start view,
        nearest the spot ahead of the grid first; (3) a row of three fence panels just behind the clearance
        beside the start straight, turned 20 deg back up the road; (4) a mini billboard on the infield facing
        the grid. Each goes through ``try_place`` (no roads, run-off, no-go zones, water or other props); the
        view each relies on is kept clear of later props (``sponsor_clear_sightlines``)."""
        self._guaranteed: list[tuple[str, str, Placement, str, np.ndarray]] = []  # (sponsor, layout, ...)
        sps = [s for s in sponsor.sponsors() if s.get("guarantee")]
        if not sps:
            return
        starts = layout_starts(self.geo)
        for sp in sps:
            for st in starts:
                blockers = self._blockers()
                found = None
                for n, p in enumerate(self.sc.placements):
                    if p.category == sponsor.CATEGORY and sponsor.sponsor_of(p.asset) == sp["id"]:
                        ok, eye = self._sees(st, p.asset, p.x, p.y, p.yaw, blockers, skip=n)
                        if ok:
                            found = (p, "placed", eye)
                            break
                if found is None:
                    found = self._guarantee_fallback(st, blockers, sp)
                    if found is not None:
                        self.sponsor_picker().commit(found[0].asset)
                if found is None:
                    continue
                p, how, eye = found
                self._guaranteed.append((sp["id"], st["layout"], p, how, st["eyes"][eye]))

    def _guarantee_fallback(self, st: dict, blockers, sp: dict):
        r, d, L = st["route"], st["direction"], st["route"].length
        ahead = st["aheadM"]
        pick = self.sponsor_picker()
        kinds = sp["kinds"]

        def at(u: float) -> int:
            return r.index_at((st["lineS"] + d * u) % L)

        def posted(u: float, side: int, off: float, turn: float, min_off: float):
            i = at(u)
            q, tw, tg = self.side_point(r, i, side, max(off, self.edge_min + min_off))
            view = tw + (-tg * d) * turn
            view = view / max(float(np.linalg.norm(view)), 1e-9)
            yaw = yaw_to((-view[0], -view[1]))
            return q, yaw

        # (1) the classic start-straight spots, relaxed band; (2) along the whole start view
        post = "billboard" if "billboard" in kinds else ("minibillboard" if "minibillboard" in kinds else None)
        if post is not None:
            bb = pick.peek(post, sp["id"])
            min_off = 2.5 if post == "billboard" else 2.0
            spots = [(ahead + 60.0, s) for s in (1, -1)] + [(-140.0, s) for s in (-1, 1)]
            us = sorted(np.arange(-START_BEFORE_M + 20.0, ahead + START_BEYOND_M - 20.0, 15.0),
                        key=lambda u: abs(u - (ahead + 60.0)))
            offs = self.GUARANTEE_OFF if post == "billboard" else (0.0,) + self.GUARANTEE_OFF
            steps = [("relaxed", spots, (1.0,)), ("along", [(u, s) for u in us for s in (1, -1)], (1.0, 0.5, 1.8))]
            for how, cand, turns in steps:
                for u, side in cand:
                    for off in offs:
                        for turn in turns:
                            q, yaw = posted(u, side, off, turn, min_off)
                            eye = self._board(st, bb, q, yaw, self.edge_min + 2.0, blockers)
                            if eye >= 0:
                                return self.sc.placements[-1], how, eye
        # (3) a row of three fence panels beside the start straight
        if "fence" in kinds:
            fw = sponsor.FENCE["w"] + 0.06
            fence = pick.peek("fence", sp["id"])
            for u in (ahead + 15.0, ahead + 45.0, -40.0, ahead + 80.0, -80.0, ahead + 120.0):
                for side in (1, -1):
                    for extra in (1.5, 2.5, 4.0):
                        row = []
                        for j in (-1, 0, 1):
                            q, tw, tg = self.side_point(r, at(u + j * fw), side, self.edge_min + extra)
                            a20 = math.radians(20.0)
                            view = tw * math.cos(a20) + (-tg * d) * math.sin(a20)
                            row.append((q, yaw_to((-view[0], -view[1]))))
                        q, yaw = row[1]
                        eye = self._board(st, fence, q, yaw, self.edge_min - 0.5, blockers)
                        if eye < 0:
                            continue
                        mid = self.sc.placements[-1]
                        for qq, yy in (row[0], row[2]):
                            self.try_place(fence, sponsor.CATEGORY, qq[0], qq[1], yy, self.edge_min - 0.5, r=1.92)
                        return mid, "fence", eye
        # (4) a mini billboard on the infield, facing the grid
        if "minibillboard" in kinds:
            inside = 1 if float(np.sum(r.curvature * r.seg_len())) > 0 else -1
            g = st["eyes"][0, :2] if len(st["eyes"]) else r.p[at(0.0), :2]
            mini = pick.peek("minibillboard", sp["id"])
            for u in (ahead + 30.0, 10.0, ahead + 80.0, -30.0, ahead + 130.0, -70.0):
                for off in (self.edge_min + 3.0, 20.0, 28.0, 36.0, 45.0):
                    q, _tw, _tg = self.side_point(r, at(u), inside, max(off, self.edge_min + 3.0))
                    view = g - q
                    view = view / max(float(np.linalg.norm(view)), 1e-9)
                    eye = self._board(st, mini, q, yaw_to((-view[0], -view[1])), self.edge_min + 2.0, blockers)
                    if eye >= 0:
                        return self.sc.placements[-1], "infield", eye
        return None

    LAP_STEP_M = 50.0  # the lap guarantee's fallback: candidate spots this far apart along the lap

    def sponsor_lap_guarantee(self) -> None:
        """For each sponsor with ``lapGuarantee``, at least one of its billboards in clear view somewhere on every
        layout's lap (``layout_laps``: the driver's eye every LAP_STEP m along the route; ``sponsor.visible``). A
        billboard already placed counts; otherwise one is added: at the layout's braking zones' turn-in first
        (outside, then inside), then every LAP_STEP_M along the lap, in the relaxed band GUARANTEE_OFF, turned
        back up the road. Its view is kept clear of later props (``sponsor_clear_sightlines``)."""
        self._lap: list[tuple[str, str, Placement, str, np.ndarray]] = []
        sps = sponsor.lap_sponsors()
        if not sps:
            return
        pick = self.sponsor_picker()
        laps = layout_laps(self.geo)
        zones = (self.sc.info.get("brakeBoards") or {}).get("zones", [])
        for sp in sps:
            for st in laps:
                blockers = self._blockers()
                found = None
                for n, p in enumerate(self.sc.placements):
                    if p.category == sponsor.CATEGORY and sponsor.sponsor_of(p.asset) == sp["id"]:
                        ok, eye = self._sees(st, p.asset, p.x, p.y, p.yaw, blockers, skip=n)
                        if ok:
                            found = (p, "placed", eye)
                            break
                if found is None:
                    found = self._lap_fallback(st, blockers, pick.peek("billboard", sp["id"]), zones)
                    if found is not None:
                        pick.commit(found[0].asset)
                if found is not None:
                    p, how, eye = found
                    self._lap.append((sp["id"], st["layout"], p, how, st["eyes"][eye]))

    def _lap_fallback(self, st: dict, blockers, asset: str, zones):
        r, d = st["route"], st["direction"]
        cand = []
        for z in sorted((z for z in zones if z["layout"] == st["layout"]), key=lambda z: -z["dropKmh"]):
            side = 1 if z["side"] == "left" else -1
            cand += [(r.index_at(z["turnInS"]), side, "braking"), (r.index_at(z["turnInS"]), -side, "braking")]
        for s in np.arange(0.0, r.length, self.LAP_STEP_M):
            i = r.index_at(float(s))
            cand += [(i, 1, "lap"), (i, -1, "lap")]
        for i, side, how in cand:
            for off in self.GUARANTEE_OFF:
                q, tw, tg = self.side_point(r, i, side, max(off, self.edge_min + 2.5))
                for turn in (1.0, 0.5):
                    view = tw + (-tg * d) * turn
                    view = view / max(float(np.linalg.norm(view)), 1e-9)
                    eye = self._board(st, asset, q, yaw_to((-view[0], -view[1])), self.edge_min + 2.0, blockers)
                    if eye >= 0:
                        return self.sc.placements[-1], how, eye
        return None

    def sponsor_credit(self, paddock_side: int) -> None:
        """The "track by GreyManSoftware" credit (``sponsor.credit``): style "plaque" puts one small plaque
        (1.8 x 0.45 m on two short posts, muted slate) behind the clearance near the start/finish line on the
        paddock side, square to the road, before the line and clear of the grid sector; if no spot fits, or with
        style "line", the credit is a small line along the bottom of the billboards' face instead."""
        c = sponsor.credit()
        if c is None:
            return
        info = {"style": c["style"], "text": sponsor.credit_line()}
        if c["style"] == "plaque":
            r, a = self.main, "sponsor_credit"
            done = False
            for u in (-50.0, -65.0, -40.0, -80.0, -95.0, -110.0):
                for side in (paddock_side or 1, -(paddock_side or 1)):
                    for off in (2.0, 4.0, 7.0, 10.0, 13.0):
                        q, tw, tg = self.side_point(r, r.index_at(u % r.length), side, self.edge_min + off)
                        if self.try_place(a, sponsor.CATEGORY, q[0], q[1], self.face_yaw(a, tw, tg),
                                          self.edge_min + 1.0):
                            done = True
                            break
                    if done:
                        break
                if done:
                    break
            if not done:
                info["style"] = "line"
                info["fallback"] = "no room for the plaque"
        self.sc.info["sponsorCredit"] = info

    def sponsor_clear_sightlines(self) -> None:
        """Keep the views the guaranteed boards rely on (start guarantee and lap guarantee): props placed across
        one of a board's three sight lines from the eye that saw it (trees, bushes, paddock, buildings: anything
        but SIGHT_KEEP and the low NO_BLOCK categories) are dropped. Writes ``info.sponsorGuarantee`` and
        ``info.sponsorLap`` ({sponsor: {layout: the board's placement index, asset, how it was found, the eye}})
        and ``info.sponsorCleared``."""
        g = getattr(self, "_guaranteed", []) + getattr(self, "_lap", [])
        removed: set[int] = set()
        if g:
            P = self.sc.placements
            z = self._terrain_xy([(p.x, p.y) for p in P])
            items = [(p.asset, p.category, p.x, p.y, float(z[n]), p.yaw, p.scale) for n, p in enumerate(P)]
            B = sponsor.Blockers(items)
            index = {id(p): n for n, p in enumerate(P)}
            for _sid, _key, p, _how, eye in g:
                n = index[id(p)]
                pts, _f = sponsor.targets(p.asset, p.x, p.y, float(z[n]), p.yaw)
                for T in pts:
                    hit = B.hits(eye[None, :], T, skip=n)[0]
                    removed.update(int(o) for o in B.owner[hit] if P[int(o)].category not in self.SIGHT_KEEP)
            self.sc.placements = [p for n, p in enumerate(P) if n not in removed]
        index = {id(p): n for n, p in enumerate(self.sc.placements)}

        def table(rows):
            out: dict[str, dict] = {}
            for sid, key, p, how, eye in rows:
                out.setdefault(sid, {})[key] = {"placement": index[id(p)], "asset": p.asset, "how": how,
                                                "eye": [round(float(v), 2) for v in eye]}
            return out

        self.sc.info["sponsorGuarantee"] = table(getattr(self, "_guaranteed", []))
        self.sc.info["sponsorLap"] = table(getattr(self, "_lap", []))
        self.sc.info["sponsorCleared"] = len(removed)

    def corner_markers(self, corners) -> None:
        if self.measured:
            return
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

    def _kerb_out(self, q: np.ndarray) -> float:
        """Kerb plus green-strip width at the road edge nearest point q (0 without kerbs)."""
        k = int(self.field.nearest(q)[0])
        if k < 0:
            return 0.0
        r = self.geo.routes[int(self.field.ri[k])]
        i = int(self.field.si[k])
        side = 1 if float(np.dot(q - r.p[i, :2], r.nl[i])) > 0 else -1
        kw, gw = self.geo.kerb_plan().get((r.index, side), (np.zeros(r.n), np.zeros(r.n)))
        return float(kw[i] + gw[i])

    def _wall_lines(self) -> list[np.ndarray]:
        if not hasattr(self, "_wl"):
            self._wl = [np.asarray(w["points"], dtype=np.float64)[:, :2] for w in self.geo.wall_runs()]
        return self._wl

    def _pit_lanes(self) -> list[tuple[np.ndarray, float]]:
        """An imported track's real pit lanes: (centreline samples, half width plus a margin)."""
        pits = getattr(self.geo, "pits", None)
        return [(ln.p, ln.w / 2.0 + 1.5) for ln in (pits.lanes if pits is not None else []) if len(ln.p) > 1]

    def _brake_spot(self, r, z: dict, direction: int, dist: int, side: int, back: float, extra: float):
        """Where braking board ``dist`` of zone z stands on ``side`` of route r, ``back`` metres further from the
        turn-in than its distance and ``extra`` metres further out than BRAKE_GAP beyond the road edge, kerb or
        green strip: (point, facing, s) or the reason it can't (``approach``, ``otherRoad``, ``wall``, ``noGo``,
        ``gravelTrap``, ``pits``, ``terrain``)."""
        if dist + back > z["approach"] - BRAKE_APPROACH_SLACK:
            return "approach"
        s = (z["s_in"] - direction * (dist + back)) % r.length
        i = r.index_at(s)
        edge = (r.left if side > 0 else r.right)[i, :2]
        out = r.nl[i] * side
        off = BRAKE_GAP + self._kerb_out(edge + out * BRAKE_GAP) + extra + BRAKE_HALF
        q = edge + out * off
        face = -r.tangent[i] * direction  # towards the oncoming cars
        ends = np.array([q - out * BRAKE_HALF, q, q + out * BRAKE_HALF])
        # own road nearest and no other road close behind it (a divider between two sections)
        d_ends = self.field.dist(np.vstack([ends, q + out * 9.0]))
        if d_ends[0] < off - BRAKE_HALF - 0.8 or d_ends[3] < off + 9.0 - 2.5:
            return "otherRoad"
        seg = edge[None, :] + out[None, :] * np.linspace(0.0, off + BRAKE_HALF + 0.4, 9)[:, None]
        if any(float(dist_to_polyline(seg, wl).min()) < 0.6 for wl in self._wall_lines() if len(wl) > 1):
            return "wall"
        # bridge decks, jumps and pit buildings; the grid sector is allowed (like the flags and staff): the
        # first corner's 150 m board often stands beside the grid (Old Hall, Hangar Hairpin, Paddock Hill)
        if self.in_nogo(ends, 0.5, True).any():
            return "noGo"
        if self.measured and (self.runoff.at(ends) == 1).any():  # never in a measured gravel trap
            return "gravelTrap"
        if any(float(dist_to_polyline(ends, lp).min()) < hw for lp, hw in self._pit_lanes()):
            return "pits"
        if not self.inside(q[0], q[1], 2.0) or not self.dry(q[0], q[1]):
            return "terrain"
        return q, face, s

    def brake_boards(self) -> None:
        """Braking boards (stock ``gameplay_distanceboard150/100/50``) 150, 100 and 50 m before the turn-in of
        every heavy-braking corner (``brake_zones``) of every layout, in its driving direction, as a complete set
        or not at all. A set stands on the outside of the corner (the side cars brake on: left before a
        right-hander), else on the inside, BRAKE_GAP beyond the road edge or kerb, the numbered face (local +X)
        towards oncoming cars. A board that would stand too close to the previous corner, on or near another road
        (a divider), across a wall line, in a no-go zone, a gravel trap, the pit lane or on another placement moves
        further out (BRAKE_OUT_M) and / or back (BRAKE_BACK_M, within BRAKE_SPACING_TOL); when one of the three
        still has no spot on either side the zone gets no boards. Boards of two layouts that coincide are placed
        once."""
        assets = {d: f"gameplay_distanceboard{d}" for d in BRAKE_BOARDS}
        if not set(assets.values()) <= self.allowed:
            return
        zones_out, placed = [], []  # placed: (number, x, y, facing unit, placement index)
        dropped: dict[str, int] = {}
        shifts = [(b, e) for b in BRAKE_BACK_M for e in BRAKE_OUT_M if abs(b) <= BRAKE_SPACING_TOL]

        def pick_set(r, z: dict, direction: int, side: int):
            """The three boards on one side: [(m, point, facing, s, back, extra, shared placement)] or the
            first board without a spot and why ("50:gravelTrap")."""
            picks = []
            for dist in BRAKE_BOARDS:
                first = None
                for back, extra in shifts:
                    got = self._brake_spot(r, z, direction, dist, side, back, extra)
                    if isinstance(got, str):
                        first = first or got
                        continue
                    q, face, s = got
                    dup = next((k for (dd, x, y, f, k) in placed if dd == dist and math.hypot(
                        x - q[0], y - q[1]) < BRAKE_DEDUP_M and float(np.dot(f, face)) > 0.85), None)
                    if dup is None and not self.occ.free(q[0], q[1], BRAKE_HALF):
                        first = first or "occupied"
                        continue
                    picks.append((dist, q, face, s, back, extra, dup))
                    break
                else:
                    return f"{dist}:{first}"
            return picks

        for key, r, direction in layout_routes(self.geo):
            for z in brake_zones(self.geo, r, direction):
                rec = {"layout": key, "turnInS": round(z["s_in"], 1),
                       "side": "left" if z["outside"] > 0 else "right", "radiusM": round(z["radius"], 1),
                       "turnDeg": round(z["turnDeg"]), "approachM": round(z["approach"], 1),
                       "dropKmh": round(z["dropKmh"]), "why": z["why"], "boards": []}
                if z["corner"]:
                    rec["corner"] = z["corner"]
                why_not, picks = {}, None
                for side, label in ((z["outside"], "outside"), (-z["outside"], "inside")):
                    got = pick_set(r, z, direction, side)
                    if not isinstance(got, str):
                        picks = got
                        break
                    why_not[label] = got
                if picks is None:  # no complete set on either side: no boards at all
                    reason = why_not["outside"].split(":")[1]
                    dropped[reason] = dropped.get(reason, 0) + 1
                    rec["dropped"] = why_not
                    zones_out.append(rec)
                    continue
                rec["boardSide"] = "left" if side > 0 else "right"
                if why_not:
                    rec["blocked"] = why_not  # the outside had no complete set
                for dist, q, face, s, back, extra, dup in picks:
                    b = {"m": dist, "s": round(s, 1)}
                    if back or extra:
                        b["shift"] = [back, extra]
                    if dup is not None:  # another layout's board on the shared road
                        rec["boards"].append({**b, "placement": dup, "shared": True})
                        continue
                    self.occ.add(q[0], q[1], BRAKE_HALF)
                    rec["boards"].append({**b, "placement": len(self.sc.placements)})
                    placed.append((dist, float(q[0]), float(q[1]), face, len(self.sc.placements)))
                    self.sc.placements.append(Placement(assets[dist], "brakeBoards", float(q[0]), float(q[1]), 0.0,
                                                        yaw_x_to(face)))
                zones_out.append(rec)
        self.sc.info["brakeBoards"] = {"zones": zones_out, "placed": len(placed), "dropped": dropped}

    ROW_GAP = 0.04   # between barrier props on a straight run (stock rows: 3.74-3.78 m apart for 3.7 m blocks)

    def barriers(self) -> None:
        """The visible containment: a row of the environment's stock barrier props (concrete blocks, muddy
        barriers, tyre walls; environments.json "barriers") along the wall line of the geometry's wall plan. Each
        prop's road-facing side sits on the invisible collision wall's face; local +Y points away from the road."""
        if self.measured:
            for run in self.geo.wall_runs():
                P = np.asarray(run["points"], dtype=np.float64)[:, :2]
                for k in range(0, len(P), 1):
                    self.occ.add(float(P[k, 0]), float(P[k, 1]), 1.2)
            return
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
                # end to end with a small gap where the line runs straight (overlapping ends there share their side
                # faces, which flicker: Chris 2026-10-26); a little overlap on bends closes the outside corner
                t2 = at(min(pos + 2.0 * w, L)) - p1
                t2 = t2 / max(float(np.linalg.norm(t2)), 1e-9)
                bend = abs(float(t[0] * t2[1] - t[1] * t2[0])) > math.sin(math.radians(1.0))
                pos += w * 0.985 if bend else w + self.ROW_GAP

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

    def measured_foliage(self) -> None:
        """Real trees only (sketch.imported woodland with cover + trees, measured from the canopy height model):
        the woods' tree spots kept with their measured cover, every single tree at its place and height, nearest
        the road first within the environment's cap (far woods thin out first; the backdrop cards stand behind
        them). No seeded clusters, park trees or edge belt."""
        e = self.e
        trees = e.get("trees", [])
        fol = self.fol
        cap = int(fol["treeCap"])
        min_tree = max(float(fol["minTreeM"]) * 0.5, C.MEASURED_TREE_MIN_M, self.edge_min + 0.5)
        cand = []  # (priority distance, x, y, asset or None, scale)
        if self.wood_cover_mask is not None and trees:
            pts = self._grid(float(fol["treeSpacing"]))
            inside = self.woods.at(pts) > 0.45
            cov = self.wood_cover_mask.at(pts)
            keep = inside & (self.rng.random(len(pts)) < np.clip(cov / np.maximum(self.woods.at(pts), 1e-6), 0.0, 1.0))
            pts = pts[keep]
            d = self.field.dist(pts) if len(pts) else np.zeros(0)
            cand += [(float(dd), float(x), float(y), None, None) for (x, y), dd in zip(pts, d)]
        if self.real_trees and trees:
            P = np.array([t[:2] for t in self.real_trees])
            H = np.array([t[2] for t in self.real_trees])
            d = self.field.dist(P)
            ins = self.woods.at(P) > 0.45 if self.woods is not None else np.zeros(len(P), dtype=bool)
            # the stock tree nearest each measured height (the game ignores a scale, so a tree can't be scaled to
            # it): any within 25 % of it, else the nearest
            th = np.array([float(envs.asset_info(a)["h"]) for a in trees])
            for (x, y), h, dd, w in zip(P, H, d, ins):
                if w:
                    continue
                off = np.abs(np.log(th / max(float(h), 1.0)))
                near = [a for a, o in zip(trees, off) if o <= max(math.log(1.25), float(off.min()) + 1e-9)]
                cand.append((float(dd), float(x), float(y), self.pick(near), 1.0))
        cand.sort(key=lambda c: c[0])
        n = 0
        for dd, x, y, a, sc in cand:
            if n >= cap:
                break
            if dd < min_tree:
                continue
            if a is None:
                a = self.pick(trees)
                sc = float(1.0 + self.rng.uniform(-0.15, 0.15))
            r = envs.asset_info(a)["r"] * 0.7
            if self.try_place(a, "trees", x, y, float(self.rng.uniform(0, 2 * math.pi)), min_tree, r=r, scale=sc,
                              margin=0.0):
                n += 1
        self.sc.info["measuredTrees"] = {"placed": n, "candidates": len(cand), "cap": cap}

    def foliage(self) -> None:
        if self.measured_veg:
            self.measured_foliage()
            self.other_foliage()
            return
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
            sel = nz > self.tree_thr if self.woods is None else self.woods.at(pts) > 0.45
            if self.parcels is not None:  # farmland: hedgerows along the field borders, no trees inside fields
                border = self.parcels.border(pts) < 4.0
                sel = (sel & ~self.parcels.is_field(pts)) | (border & (self.rng.random(len(pts)) < 0.55))
            self._scatter(pts[sel], trees, "trees", min_tree, cap)
        # parkland: single deciduous trees standing in the open grass
        park = e.get("parkTrees", [])
        if park:
            pts = self._grid(34.0)
            sel = (self.wood_cover(pts) < 0.2) & (self.rng.random(len(pts)) < 0.45)
            self._scatter(pts[sel], park, "trees", min_tree, int(fol.get("parkTreeCap", 120)), scale_jitter=0.25)
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
        self.other_foliage()

    def other_foliage(self) -> None:
        """Bushes at wood edges, stock foliage groups, saplings and ground props (seeded; around measured woods
        on imported tracks)."""
        e = self.e
        fol = self.fol
        min_tree = max(float(fol["minTreeM"]), self.edge_min + 4.0)
        # bushes: along the edge of the open ground and in small clusters
        bushes = e.get("bushes", [])
        pts = self._grid(14.0)
        if len(pts) and bushes:
            d = self.field.dist(pts)
            band = (d > self.edge_min + 8.0) & (d < min_tree + 15.0)
            near_wood = (self.woods.at(pts) > 0.17) if self.woods is not None else (self.tree_noise.at(pts) > self.tree_thr - 0.08)
            sel = band & near_wood & (self.rng.random(len(pts)) < 0.5)
            self._scatter(pts[sel], bushes, "bushes", self.edge_min + 8.0, int(fol["bushCap"]))
        # stock foliage groups (grass and bush clumps, e.g. sandpit03's foliage_group12/10) in the open ground
        groups = list(fol.get("groups", []))
        if groups and int(fol.get("groupCap", 0)) > 0:
            pts = self._grid(26.0)
            d = self.field.dist(pts)
            open_ = (self.woods.at(pts) < 0.7) if self.woods is not None else (self.tree_noise.at(pts) < self.tree_thr + 0.05)
            sel = (d > self.edge_min + 6.0) & (d < 150.0) & open_ & (self.rng.random(len(pts)) < 0.6)
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

    # ------------------------------------------------------------------------------------ coastal extras
    def stone_walls(self) -> None:
        """Stone field walls (environments.json "stoneWalls") along the borders of a seeded field-parcel grid, in
        the open ground away from the road, on dry land; a share of each border run is left out (gates, gaps)."""
        sw = self.e.get("stoneWalls")
        if not sw:
            return
        parcels = Parcels(seed_for(self.slug, "walls"))
        x0, x1, y0, y1 = self.bounds
        ca, sa = math.cos(parcels.a), math.sin(parcels.a)
        ax, ay = np.array([ca, sa]), np.array([-sa, ca])  # parcel u and v directions in the world
        corners = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]])
        u, v = parcels.local(corners)
        cap, keep, min_road = int(sw.get("cap", 500)), float(sw.get("keep", 0.6)), float(sw.get("minRoadM", 40.0))
        n = 0
        lines = []
        for axis, (lo, hi), (olo, ohi), step_len, other_len, dir_ in (
                (0, (math.floor(u.min()), math.ceil(u.max())), (v.min(), v.max()), parcels.w, parcels.h, ay),
                (1, (math.floor(v.min()), math.ceil(v.max())), (u.min(), u.max()), parcels.h, parcels.w, ax)):
            for k in range(int(lo), int(hi) + 1):
                for m in range(int(math.floor(olo)), int(math.ceil(ohi))):
                    if self.rng.random() > keep:
                        continue
                    lines.append((axis, k, m))
        for axis, k, m in lines:
            # a border segment between two parcel corners, in world coordinates
            if axis == 0:
                a_loc, b_loc = (k, m), (k, m + 1)
            else:
                a_loc, b_loc = (m, k), (m + 1, k)

            def world(uv):
                uu = uv[0] * parcels.w - parcels.o[0]
                vv = uv[1] * parcels.h - parcels.o[1]
                return ax * uu + ay * vv
            pa, pb = world(a_loc), world(b_loc)
            seg = pb - pa
            L = float(np.linalg.norm(seg))
            t = seg / max(L, 1e-9)
            pos = 2.0
            while pos < L - 2.0 and n < cap:
                a = self.pick(sw["assets"])
                w = float(envs.asset_info(a).get("width", 3.4))
                c = pa + t * (pos + w / 2.0)
                if self.try_place(a, "walls", c[0], c[1], yaw_to(t), min_road, r=0.9, margin=0.0):
                    n += 1
                pos += w * 0.97
        self.sc.info["stoneWalls"] = n

    def shore(self) -> None:
        """Rocks along the shoreline and a few cliff faces where the ground drops into the sea (environments.json
        "shore"; needs the sea level of an imported track)."""
        sh = self.e.get("shore")
        if not sh or self.sea is None:
            return
        lo, hi = (float(v) for v in sh.get("band", (-1.0, 3.5)))
        pts = self._grid(7.0)
        z = self.geo.ground(pts)
        band = (z > self.sea + lo) & (z < self.sea + hi)
        d = self.field.dist(pts)
        sel = band & (d > self.edge_min + 10.0)
        cand = pts[sel]
        nr = 0
        for k in self.rng.permutation(len(cand)):
            if nr >= int(sh.get("rockCap", 200)):
                break
            if self.rng.random() < 0.5:
                continue
            a = self.pick(sh["rocks"])  # unscaled (XREF_SCALE_JITTER): the game draws them at their own size
            if self.try_place(a, "shore", cand[k, 0], cand[k, 1], float(self.rng.uniform(0, 2 * math.pi)),
                              self.edge_min + 10.0, r=envs.asset_info(a)["r"] * 0.6, margin=0.0):
                nr += 1
        # cliffs: where the ground falls steeply into the sea, facing the water
        nc = 0
        cliffs = sh.get("cliffs", [])
        if cliffs:
            pts = self._grid(24.0)
            z = self.geo.ground(pts)
            e = 6.0
            gx = (self.geo.ground(pts + [e, 0]) - self.geo.ground(pts - [e, 0])) / (2 * e)
            gy = (self.geo.ground(pts + [0, e]) - self.geo.ground(pts - [0, e])) / (2 * e)
            slope = np.hypot(gx, gy)
            d = self.field.dist(pts)
            sel = (z > self.sea - 1.0) & (z < self.sea + 6.0) & (slope > 0.12) & (d > 60.0)
            for k in self.rng.permutation(int(sel.sum())):
                if nc >= int(sh.get("cliffCap", 20)):
                    break
                p = pts[sel][k]
                g = np.array([gx[sel][k], gy[sel][k]])
                down = -g / max(float(np.linalg.norm(g)), 1e-9)  # towards the sea
                a = self.pick(cliffs)
                if self.try_place(a, "shore", p[0], p[1], self.face_yaw(a, down, np.array([-down[1], down[0]])),
                                  60.0, r=envs.asset_info(a)["r"] * 0.7, margin=0.0):
                    nc += 1
        self.sc.info["shore"] = {"rocks": nr, "cliffs": nc}

    def gorse(self) -> None:
        """Gorse clumps (stock bushes, unscaled) scattered in the open grass (environments.json "gorse")."""
        g = self.e.get("gorse")
        if not g:
            return
        centres = self._grid(55.0)
        d = self.field.dist(centres)
        centres = centres[(d > self.edge_min + 10.0) & (d < float(g.get("maxRoadM", 300.0)))]
        n = 0
        for c in centres[self.rng.permutation(len(centres))[:int(g.get("clusters", 60))]]:
            k = int(self.rng.integers(*[int(v) for v in g.get("perCluster", (3, 8))]))
            ang = self.rng.uniform(0, 2 * math.pi, k)
            rad = np.sqrt(self.rng.random(k)) * float(g.get("radiusM", 8.0))
            for q in c + np.stack([np.cos(ang) * rad, np.sin(ang) * rad], axis=1):
                if n >= int(g.get("cap", 400)):
                    break
                a = self.pick(g["assets"])  # at their own size (3-4 m): the game ignores a scale (XREF_SCALE_JITTER)
                if self.try_place(a, "bushes", q[0], q[1], float(self.rng.uniform(0, 2 * math.pi)), self.edge_min + 8.0,
                                  r=envs.asset_info(a)["r"] * 0.7, margin=0.0):
                    n += 1
        self.sc.info["gorse"] = n

    def buildings(self) -> None:
        e = self.e
        name = self.env_name
        b = e.get("buildings", [])
        if not b:
            return
        x0, x1, y0, y1 = self.bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if name == "farmland" or e.get("farmsteads"):
            fs = e.get("farmsteads", {})
            sites = self._grid(140.0)
            d = self.field.dist(sites)
            sites = sites[d > float(fs.get("minRoadM", 70.0))]
            n = 0
            for c in sites[self.rng.permutation(len(sites))]:
                if n >= int(fs.get("count", 2)):
                    break
                toward = np.array([cx - c[0], cy - c[1]])
                toward /= max(np.linalg.norm(toward), 1e-6)
                tang = np.array([-toward[1], toward[0]])
                plan = [tuple(p) for p in fs.get("plan", [])] or [
                    ("building_barn1", 0, 0), ("prop_house01", 22, 6), ("prop_silo8", -12, -4),
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
        pits = getattr(self.geo, "pits", None)  # an imported track's real pits (pits.py): reserved before anything else is placed
        if pits is not None:
            pits.reserve(self)
        paddock_side = self.start_finish(gantry=pits is None or not any(g["kind"] == "startLights" for g in pits.gantries),
                                         grandstand=pits is None or not pits.grandstands)
        self.brake_boards()
        self.corner_markers(corners)
        self.barriers()
        self.viewing_spots(corners)
        self.sponsor_billboards(straights)
        self.sponsor_credit(paddock_side)
        self.ad_fences(straights)
        if pits is not None:
            pits.place(self)
        self.camera_towers()  # after the real pits (reserved, but their props take the room first)
        # after the real pits (kept by the sight-line clearing, so they must be seen past) and the ad fences
        self.sponsor_guarantee()
        self.sponsor_lap_guarantee()
        if pits is None or not pits.paddocks:  # the generic paddock beside the start, unless the real one is known
            self.paddock(paddock_side)
        self.lights()
        self.buildings()
        self.stone_walls()
        self.shore()
        self.gorse()
        self.foliage()
        self._dedupe()
        self.sponsor_clear_sightlines()  # after everything else is placed
        self._heights()
        if pits is not None:
            pits.after_heights(self)
            self.sc.info["pits"] = pits.report
        self.sc.info.update({"corners": len(corners), "straights": len(straights), "edgeMinM": self.edge_min,
                             # categories allowed closer to a road edge than edgeMinM: [min, max] metres (the
                             # barrier row on the wall line, corner markers in front of it; under bridge decks
                             # barriers pass beneath the deck)
                             "nearRoadM": {"barriers": [C.DIVIDER_MIN - 1.0,
                                                        max(C.CROWD_WALL, C.FAST_CORNER_WALL) + 2.0],
                                           "cornerMarkers": [C.SHOULDER_W, None],
                                           # real pits (pits.py): the pit wall beside the track, the pit buildings
                                           # behind the lane, bridges over the road
                                           **({"pitWall": [0.0, None], "pitBuildings": [2.0, None], "pitPaddock": [2.0, None], "realGrandstand": [1.0, None],
                                               "bridges": [None, None]} if pits is not None else {}),
                                           "brakeBoards": [BRAKE_GAP + BRAKE_HALF - 0.8,
                                                           BRAKE_GAP + BRAKE_HALF + C.KERB_W + C.GREEN_W
                                                           + max(BRAKE_OUT_M) + 0.8]},
                             "treeThreshold": round(self.tree_thr, 4)})
        return self.sc

    DEDUPE_ROWS = {"gantry", "bridges", "barriers", "pitWall", "cornerMarkers", "brakeBoards"}
    DEDUPE_RANK = {"pitBuildings": 0, "realGrandstand": 0, "grandstand": 0, "cameras": 0, "buildings": 1, "adFence": 1}

    def _dedupe(self) -> None:
        """No two props on one footprint (footprint.overlaps: coplanar faces flicker, Chris 2026-10-25): buildings,
        grandstands and camera towers win, then ad fences, then whatever was placed first. Rows along a line and
        spans are exempt (they are placed end to end; check_scene's footprint.coplanar covers them)."""
        from . import footprint
        P = self.sc.placements
        cand = [k for k, p in enumerate(P) if p.category not in self.DEDUPE_ROWS and p.category not in footprint.SKIP
                and not sponsor.is_mesh(p.asset)]
        cand.sort(key=lambda k: (self.DEDUPE_RANK.get(P[k].category, 2), k))
        kept: list[tuple[np.ndarray, np.ndarray, float, float]] = []   # rect, centre, radius, area
        drop = set()
        for k in cand:
            p = P[k]
            q = footprint.ccw(footprint.rect(p.asset, p.x, p.y, p.yaw, p.scale, envs.asset_info(p.asset)))
            c, a = q.mean(0), footprint.area(list(q))
            rad = float(np.hypot(*(q - c).T).max())
            if any(np.hypot(*(c - c2)) < rad + r2 and footprint.area(footprint.clip(list(q), q2)) > 0.15 * min(a, a2)
                   for q2, c2, r2, a2 in kept):
                drop.add(k)
                continue
            kept.append((q, c, rad, a))
        if drop:
            P[:] = [p for k, p in enumerate(P) if k not in drop]
            self.sc.info["dedupedProps"] = len(drop)

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
