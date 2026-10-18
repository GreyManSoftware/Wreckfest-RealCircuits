"""Track spec -> TrackModel, GeometryFacts, rule results and SVG previews.

Public entry point: :func:`build_track_model`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import __version__, rules
from . import constants as C
from . import elevation as el
from . import polyline as pl
from .centreline import build_alt_route, build_main_loop, canvas_to_world
from .model import (
    COORDINATE_SYSTEM,
    AiLine,
    Bounds,
    Branch,
    CanvasTransform,
    Checkpoint,
    CrossingRecord,
    Grid,
    GridSlot,
    Join,
    JumpRecord,
    Route,
    Sample,
    TrackModel,
    Transform,
)
from .polyline import GeometryError

__all__ = ["GeometryWarning", "Result", "build_track_model"]

FACT_DECIMALS = 3


@dataclass(frozen=True)
class GeometryWarning:
    code: str
    message: str


@dataclass
class Result:
    """Output of :func:`build_track_model`.

    ``option_result`` is the rules evaluation without facts (option-level rules; geometry rules
    deferred); ``rule_result`` is the full evaluation with the computed facts. ``model``, ``facts``
    and the SVGs are None when the spec is schema-invalid or has no buildable main loop.
    """

    model: TrackModel | None
    facts: dict[str, Any] | None
    rule_result: rules.EvaluationResult
    option_result: rules.EvaluationResult
    preview_svg: str | None
    elevation_svg: str | None
    warnings: list[GeometryWarning] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.model is not None and self.rule_result.valid


# ---------------------------------------------------------------------------------------------
# Internal route geometry
# ---------------------------------------------------------------------------------------------


class _Route:
    def __init__(self, route_id: str, pts: np.ndarray, closed: bool, ai_index: int) -> None:
        self.id = route_id
        self.pts = pts
        self.closed = closed
        self.ai_index = ai_index
        self.z = np.zeros(len(pts))
        self.start_join: int | None = None  # main-loop vertex index (alt routes)
        self.end_join: int | None = None

    def finalize(self) -> None:
        self.cum = pl.cumulative(self.pts, self.closed)
        self.s = self.cum[: len(self.pts)]
        self.length = float(self.cum[-1])
        self.heading = pl.headings(self.pts, self.closed)

    def at(self, s: float) -> np.ndarray:
        return pl.interp_at(self.cum, self.pts, self.closed, s)

    def z_at(self, s: float) -> float:
        return float(pl.interp_at(self.cum, self.z, self.closed, s))

    def heading_at(self, s: float) -> float:
        h = 0.5
        if not self.closed:
            s = min(max(s, h), self.length - h)
        d = self.at(s + h) - self.at(s - h)
        return math.atan2(d[1], d[0])

    def wrap(self, d: np.ndarray) -> np.ndarray:
        if not self.closed:
            return d
        half = self.length / 2.0
        return np.mod(d + half, self.length) - half

    def fwd_to(self, x: float, a: float, b: float) -> float:
        """Distance driving forward from ``x`` to the interval [a, b] (0 if inside)."""
        if self.closed:
            span = b - a
            if span >= self.length or (x - a) % self.length <= span:
                return 0.0
            return (a - x) % self.length
        if a <= x <= b:
            return 0.0
        return a - x if a > x else math.inf

    def bwd_to(self, x: float, a: float, b: float) -> float:
        if self.closed:
            span = b - a
            if span >= self.length or (x - a) % self.length <= span:
                return 0.0
            return (x - b) % self.length
        if a <= x <= b:
            return 0.0
        return x - b if b < x else math.inf

    def dist_to(self, x: float, a: float, b: float) -> float:
        return min(self.fwd_to(x, a, b), self.bwd_to(x, a, b))

    def param(self, s: float) -> float:
        return s / self.length if self.length > 0 else 0.0


@dataclass
class _Obstacle:
    route: str
    a: float
    b: float
    kind: str
    ref: Any = None


@dataclass
class _Crossing:
    id: str
    annotated: bool
    detected: bool
    type: str
    position: np.ndarray
    angle_deg: float
    branches: list[tuple[str, float]]
    halves: list[float]
    over: int | None = None
    up: float = 0.0
    down: float = 0.0
    available: float | None = None


@dataclass
class _Jump:
    id: str
    route: str
    size: str
    bidirectional: bool
    s: float
    a: float
    b: float
    in_base: bool
    layouts: list[str]


def _round(v: float, nd: int = FACT_DECIMALS) -> float:
    return round(float(v), nd) + 0.0


def _unique_by_id(
    items: list[Mapping[str, Any]], kind: str, warnings: list[GeometryWarning]
) -> list[Mapping[str, Any]]:
    seen: set[str] = set()
    out = []
    for item in items:
        if item["id"] in seen:
            warnings.append(
                GeometryWarning(f"{kind}-duplicate-id", f"Duplicate {kind} id {item['id']}; using the first.")
            )
            continue
        seen.add(item["id"])
        out.append(item)
    return out


def _half_footprint(width: float, angle_deg: float) -> float:
    """Half the length of road a branch needs clear at a crossing (other road's width plus margins)."""
    a = math.radians(angle_deg)
    sin = max(math.sin(a), C.CROSSING_MIN_SIN)
    return ((width / 2.0 + C.CROSSING_CLEAR_MARGIN_M) + (width / 2.0) * abs(math.cos(a))) / sin


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------


def build_track_model(spec: Mapping[str, Any] | Any) -> Result:
    """Validate ``spec``, build the TrackModel and geometry facts, and evaluate the rules.

    1. Schema validation and the option-level rules (geometry rules deferred).
    2. Geometry: centreline, alt routes, crossings, elevation, grid, checkpoints -> facts.
    3. The full rules evaluation with the facts.

    Deterministic: the same spec gives byte-identical ``model.to_json()`` and SVGs.
    """
    to_dict = getattr(spec, "to_dict", None)
    spec = to_dict() if callable(to_dict) else spec
    option_result = rules.evaluate(spec)
    warnings: list[GeometryWarning] = []
    if not option_result.schema_valid:
        return Result(None, None, option_result, option_result, None, None, warnings)
    if "mainLoop" not in spec["sketch"]:
        warnings.append(GeometryWarning("main-loop-missing", "There is no main loop to build."))
        return Result(None, None, option_result, option_result, None, None, warnings)
    symmetric = any(f.flag == "symmetricBridgeRamps" for f in option_result.flags)
    try:
        model, facts = _Builder(spec, symmetric, warnings).build()
    except GeometryError as exc:
        warnings.append(GeometryWarning("geometry-error", str(exc)))
        return Result(None, None, option_result, option_result, None, None, warnings)

    from .preview import elevation_svg, preview_svg

    rule_result = rules.evaluate(spec, facts)
    return Result(model, facts, rule_result, option_result, preview_svg(model), elevation_svg(model), warnings)


class _Builder:
    def __init__(self, spec: Mapping[str, Any], symmetric: bool, warnings: list[GeometryWarning]) -> None:
        self.spec = spec
        self.sketch = spec["sketch"]
        self.options = spec["options"]
        self.meta = spec["meta"]
        self.symmetric = symmetric
        self.warnings = warnings
        self.consts = rules.load_rules()["constants"]
        self.width = float(self.options["widthM"])
        self.lap = float(self.options["lapLengthKm"]) * 1000.0
        self.reverse = bool(self.options["reverseLayout"])

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(GeometryWarning(code, message))

    # -- routes ----------------------------------------------------------------------------------

    def _routes(self) -> None:
        need = self.width / 2.0 + float(self.consts["minInnerEdgeRadiusM"])
        target = need * (1.0 + C.RELAX_TARGET_MARGIN)
        raw_main = [(p["x"], p["y"]) for p in self.sketch["mainLoop"]["points"]]
        sf = self.sketch.get("startFinish")
        if sf:
            start, heading, toward = (sf["position"]["x"], sf["position"]["y"]), sf["headingDeg"], None
        else:
            start, heading, toward = raw_main[0], None, raw_main[1]
        main = build_main_loop(raw_main, self.lap, target, start, heading, toward)
        if not main.relaxation.converged:
            self.warn(
                "corner-radius-unresolved",
                f"The main loop's tightest corners couldn't be eased to {need:.1f} m without distorting the shape.",
            )
        self.scale = main.scale
        pts = main.points

        def w0(pt: Mapping[str, float]) -> np.ndarray:
            return canvas_to_world([(pt["x"], pt["y"])])[0] * self.scale

        alts = _unique_by_id(self.sketch.get("altRoutes", []), "alt-route", self.warnings)
        attach: list[tuple[float, float, np.ndarray, np.ndarray]] = []
        for alt in alts:
            raw_s, raw_e = w0(alt["points"][0]), w0(alt["points"][-1])

            def resolve(
                att: Mapping[str, Any] | None, fallback: np.ndarray, name: str, alt_id: str = alt["id"]
            ) -> float:
                if att and "param" in att:
                    return float(att["param"]) * self.lap
                if not att:
                    self.warn(
                        "alt-route-attachment-missing",
                        f"Route {alt_id} has no {name} attachment; snapping its {name} to the nearest point.",
                    )
                pt = w0(att["point"]) if att and "point" in att else fallback
                return pl.project(pts, True, pt).s

            attach.append(
                (resolve(alt.get("start"), raw_s, "start"), resolve(alt.get("end"), raw_e, "end"), raw_s, raw_e)
            )

        pts = self._insert_vertices(pts, [s for a in attach for s in a[:2]])
        cum = pl.cumulative(pts, True)
        tang = pl.tangents(pts, True)

        def vertex(s: float) -> int:
            s = s % self.lap
            d = np.abs(cum[: len(pts)] - s)
            return int(np.argmin(np.minimum(d, self.lap - d)))

        built: list[tuple[str, np.ndarray, int, int, float, float]] = []
        for alt, (s0, s1, raw_s, raw_e) in zip(alts, attach, strict=True):
            i0, i1 = vertex(s0), vertex(s1)
            if i0 == i1:
                self.warn(
                    "alt-route-degenerate", f"Route {alt['id']} starts and ends at the same point; it is left out."
                )
                continue
            try:
                route_pts, relax = build_alt_route(
                    [(p["x"], p["y"]) for p in alt["points"]], self.scale, pts[i0], pts[i1], tang[i0], tang[i1], target
                )
            except GeometryError as exc:
                self.warn("alt-route-degenerate", f"Route {alt['id']}: {exc}")
                continue
            if not relax.converged:
                self.warn(
                    "corner-radius-unresolved",
                    f"Route {alt['id']}'s tightest corners couldn't be eased to {need:.1f} m.",
                )
            gap0 = math.hypot(*(raw_s - pts[i0]))
            gap1 = math.hypot(*(raw_e - pts[i1]))
            built.append((alt["id"], route_pts, i0, i1, gap0, gap1))

        everything = np.vstack([pts] + [b[1] for b in built])
        self.centre = (everything.min(axis=0) + everything.max(axis=0)) / 2.0
        self.main = _Route("main", pts - self.centre, True, 0)
        self.main.finalize()
        self.routes = [self.main]
        self.alt_gaps: dict[str, tuple[float, float]] = {}
        for k, (rid, rp, i0, i1, g0, g1) in enumerate(built):
            r = _Route(rid, rp - self.centre, False, -(k + 1))
            r.start_join, r.end_join = i0, i1
            r.finalize()
            self.routes.append(r)
            self.alt_gaps[rid] = (g0, g1)
        self.by_id = {r.id: r for r in self.routes}

    def _insert_vertices(self, pts: np.ndarray, s_list: list[float]) -> np.ndarray:
        cum = pl.cumulative(pts, True)
        total = cum[-1]
        sv = cum[: len(pts)]
        extra: list[float] = []
        for s in s_list:
            s = s % total
            d = np.abs(sv - s)
            if float(np.min(np.minimum(d, total - d))) <= C.JUNCTION_MERGE_M:
                continue
            if any(abs(s - e) <= C.JUNCTION_MERGE_M for e in extra):
                continue
            extra.append(s)
        if not extra:
            return pts
        ins = pl.interp_at(cum, pts, True, np.array(extra))
        all_s = np.concatenate([sv, extra])
        order = np.argsort(all_s, kind="stable")
        return np.vstack([pts, ins])[order]

    def world(self, pt: Mapping[str, float]) -> np.ndarray:
        return canvas_to_world([(pt["x"], pt["y"])])[0] * self.scale - self.centre

    # -- crossings -------------------------------------------------------------------------------

    def _detect(self) -> list[_Crossing]:
        hits = pl.intersections(
            [(r.pts, r.closed) for r in self.routes], C.INTERSECTION_CELL_M, C.SELF_INTERSECTION_MIN_GAP
        )
        found: list[_Crossing] = []
        pairs: list[tuple[str, str]] = []
        for h in hits:
            ra, rb = self.routes[h.route_a], self.routes[h.route_b]
            sa = float(ra.cum[h.seg_a] + h.t_a * (ra.cum[h.seg_a + 1] - ra.cum[h.seg_a]))
            sb = float(rb.cum[h.seg_b] + h.t_b * (rb.cum[h.seg_b + 1] - rb.cum[h.seg_b]))
            near_junction = any(
                not r.closed and min(s, r.length - s) < C.JUNCTION_EXCLUSION_M for r, s in ((ra, sa), (rb, sb))
            )
            if near_junction:
                continue
            point = np.array(h.point)
            pair = tuple(sorted((ra.id, rb.id)))
            if any(
                p == pair and math.hypot(*(c.position - point)) < C.CROSSING_DEDUPE_M
                for p, c in zip(pairs, found, strict=True)
            ):
                continue
            branches = sorted([(ra.id, sa), (rb.id, sb)], key=lambda b: (self.by_id[b[0]].ai_index * -1, b[1]))
            ha = self.by_id[branches[0][0]].heading_at(branches[0][1])
            hb = self.by_id[branches[1][0]].heading_at(branches[1][1])
            angle = math.degrees(math.acos(min(1.0, abs(math.cos(ha - hb)))))
            half = _half_footprint(self.width, angle)
            found.append(_Crossing("", False, True, "unannotated", point, angle, branches, [half, half]))
            pairs.append(pair)
        return found

    def _crossings(self) -> None:
        detected = self._detect()
        annotations = _unique_by_id(self.sketch.get("crossings", []), "crossing", self.warnings)
        cand = []
        for ai, ann in enumerate(annotations):
            pos = self.world(ann["position"])
            for di, det in enumerate(detected):
                d = math.hypot(*(det.position - pos))
                if d <= C.CROSSING_MATCH_RADIUS_M:
                    cand.append((d, ai, di))
        cand.sort()
        match: dict[int, int] = {}
        used: set[int] = set()
        for _, ai, di in cand:
            if ai not in match and di not in used:
                match[ai] = di
                used.add(di)

        self.crossings: list[_Crossing] = []
        for ai, ann in enumerate(annotations):
            ctype = ann.get("type", "bridge")
            if ai in match:
                c = detected[match[ai]]
                c.id, c.annotated, c.type = ann["id"], True, ctype
            else:
                c = self._virtual_crossing(ann, ctype)
                self.warn(
                    "crossing-annotation-unmatched",
                    f"Crossing {ann['id']} doesn't mark a place where the track crosses itself; "
                    "its facts are measured at the marker.",
                )
            if ctype == "bridge":
                c.over = self._over_branch(c, ann.get("over"))
            self.crossings.append(c)
        auto = 0
        for di, c in enumerate(detected):
            if di in used:
                continue
            auto += 1
            c.id = f"auto{auto}"
            x, y = c.position
            self.warn(
                "crossing-needs-annotation",
                f"The track crosses itself at ({x:.0f}, {y:.0f}) m ({c.branches[0][0]} / {c.branches[1][0]}); "
                "mark it as a bridge or a flat crossing.",
            )
            self.crossings.append(c)
        for c in self.crossings:
            if c.detected and c.angle_deg < C.CROSSING_SHALLOW_DEG:
                self.warn("crossing-shallow", f"Crossing {c.id} meets at only {c.angle_deg:.0f} degrees.")

    def _virtual_crossing(self, ann: Mapping[str, Any], ctype: str) -> _Crossing:
        pos = self.world(ann["position"])
        over_route = (ann.get("over") or {}).get("route")
        if ctype == "bridge" and over_route in self.by_id:
            route = self.by_id[over_route]
        else:
            route = min(self.routes, key=lambda r: pl.project(r.pts, r.closed, pos).distance)
        proj = pl.project(route.pts, route.closed, pos)
        half = _half_footprint(self.width, 90.0)
        return _Crossing(ann["id"], True, False, ctype, proj.point, 90.0, [(route.id, proj.s)], [half])

    def _over_branch(self, c: _Crossing, over: Mapping[str, Any] | None) -> int:
        if not over:
            return 0
        route = over.get("route", "main")
        cands = [i for i, (r, _) in enumerate(c.branches) if r == route]
        if not cands:
            if c.detected:
                self.warn(
                    "bridge-over-not-at-crossing",
                    f"Bridge {c.id}: route {route} doesn't pass through it; {c.branches[0][0]} goes over.",
                )
            return 0
        if len(cands) == 1:
            return cands[0]
        param = over.get("param")
        if param is None:
            self.warn(
                "bridge-over-ambiguous",
                f"Bridge {c.id}: both branches are on {route}; give over.param. Using the first pass.",
            )
            return cands[0]

        def cyc(i: int) -> float:
            r = self.by_id[c.branches[i][0]]
            d = abs(r.param(c.branches[i][1]) - float(param))
            return min(d, 1.0 - d) if r.closed else d

        return min(cands, key=lambda i: (cyc(i), i))

    # -- jumps -----------------------------------------------------------------------------------

    def _jumps(self) -> None:
        self.jumps: list[_Jump] = []
        for j in _unique_by_id(self.sketch.get("jumps", []), "jump", self.warnings):
            route = self.by_id.get(j.get("route", "main"))
            if route is None:
                self.warn("jump-route-missing", f"Jump {j['id']} is on a route that wasn't built; it is left out.")
                continue
            size = j.get("size", "medium")
            bidir = bool(j.get("bidirectional", False))
            a, b = el.jump_extent(size, bidir)
            s = pl.project(route.pts, route.closed, self.world(j["position"])).s
            if not route.closed:
                lo, hi = C.JUNCTION_CLEAR_M - a, route.length - C.JUNCTION_CLEAR_M - b
                if lo > hi:
                    self.warn("jump-no-room", f"Route {route.id} is too short for jump {j['id']}; it is left out.")
                    continue
                if not lo <= s <= hi:
                    self.warn("jump-moved", f"Jump {j['id']} was moved away from the end of route {route.id}.")
                    s = min(max(s, lo), hi)
            in_base = bidir or not self.reverse
            layouts = ["forward", "reverse"] if (self.reverse and bidir) else ["forward"]
            self.jumps.append(_Jump(j["id"], route.id, size, bidir, s, s + a, s + b, in_base, layouts))

    # -- obstacles, bridges and facts --------------------------------------------------------------

    def _grid_length(self) -> float:
        rows = math.ceil(int(self.options["gridSize"]) / 2)
        return rows * float(self.consts["gridRowSpacingM"])

    def _obstacles(self) -> list[_Obstacle]:
        obs: list[_Obstacle] = []
        for ci, c in enumerate(self.crossings):
            if not c.detected:
                continue
            for bi, (rid, s) in enumerate(c.branches):
                obs.append(_Obstacle(rid, s - c.halves[bi], s + c.halves[bi], "crossing", (ci, bi)))
        for j in self.jumps:
            obs.append(_Obstacle(j.route, j.a, j.b, "jump", j.id))
        for r in self.routes[1:]:
            for idx in (r.start_join, r.end_join):
                s = float(self.main.s[idx])
                obs.append(_Obstacle("main", s - C.JUNCTION_CLEAR_M, s + C.JUNCTION_CLEAR_M, "junction", r.id))
            obs.append(_Obstacle(r.id, -1.0, C.JUNCTION_CLEAR_M, "junction", "start"))
            obs.append(_Obstacle(r.id, r.length - C.JUNCTION_CLEAR_M, r.length + 1.0, "junction", "end"))
        return obs

    def _bridges(self) -> None:
        obstacles = self._obstacles()
        grid = _Obstacle(
            "main", self.lap - self._grid_length() - C.GRID_FRONT_OFFSET_M, self.lap + C.START_CLEAR_M, "grid"
        )
        ramp_len = el.bridge_ramp_length(float(self.consts["bridgeRampMinLengthM"]))
        for ci, c in enumerate(self.crossings):
            if c.type != "bridge" or c.over is None:
                continue
            rid, s = c.branches[c.over]
            route = self.by_id[rid]
            half = c.halves[c.over]
            own = (ci, c.over)
            relevant = [o for o in obstacles + [grid] if o.route == rid and o.ref != own]
            before = min([route.bwd_to(s - half, o.a, o.b) for o in relevant] + [math.inf])
            after = min([route.fwd_to(s + half, o.a, o.b) for o in relevant] + [math.inf])
            if not route.closed:
                before, after = min(before, max(0.0, s - half)), min(after, max(0.0, route.length - s - half))
            else:
                cap = route.length - 2.0 * half
                before, after = min(before, cap), min(after, cap)
            c.available = max(0.0, min(before, after))
            if self.symmetric:
                c.up = c.down = min(ramp_len, c.available)
            else:
                c.up, c.down = min(ramp_len, max(0.0, before)), min(ramp_len, max(0.0, after))
            if c.detected and min(c.up, c.down) < ramp_len:
                self.warn(
                    "bridge-ramps-short",
                    f"Bridge {c.id} only has room for {min(c.up, c.down):.0f} m ramps "
                    f"(wants {ramp_len:.0f} m); they will be steeper.",
                )

    def _ramp_intervals(self, exclude: int | None = None) -> list[_Obstacle]:
        out = []
        for ci, c in enumerate(self.crossings):
            if ci == exclude or c.type != "bridge" or c.over is None or not c.detected:
                continue
            rid, s = c.branches[c.over]
            half = c.halves[c.over]
            out.append(_Obstacle(rid, s - half - c.up, s + half + c.down, "ramp", c.id))
        return out

    def _crossing_facts(self) -> dict[str, dict[str, float]]:
        facts: dict[str, dict[str, float]] = {}
        for ci, c in enumerate(self.crossings):
            if not c.annotated:
                continue
            features = [_Obstacle(j.route, j.a, j.b, "jump") for j in self.jumps] + self._ramp_intervals(ci)
            nearest = math.inf
            for rid, s in c.branches:
                route = self.by_id[rid]
                for f in features:
                    if f.route == rid:
                        nearest = min(nearest, route.dist_to(s, f.a, f.b))
            entry = {"nearestJumpM": _round(nearest if math.isfinite(nearest) else self.lap)}
            if c.type == "bridge":
                entry["availableRampLengthM"] = _round(c.available or 0.0)
            facts[c.id] = entry
        return facts

    # -- elevation -------------------------------------------------------------------------------

    def _elevation(self) -> None:
        self.terrain = el.Terrain(self.meta["slug"], self.options["elevation"])
        self.terrain.limit_grade([(r.pts, r.s, r.closed) for r in self.routes])
        for r in self.routes:
            r.z = self.terrain.height(r.pts)
        for c in self.crossings:
            if c.type == "bridge" and c.over is not None and c.detected:
                rid, s = c.branches[c.over]
                r = self.by_id[rid]
                r.z = r.z + el.bridge_bump(r.wrap(r.s - s), c.halves[c.over], c.up, c.down)
        for j in self.jumps:
            if j.in_base:
                r = self.by_id[j.route]
                r.z = r.z + el.jump_profile(j.size, j.bidirectional, r.wrap(r.s - j.s))
        for r in self.routes[1:]:
            z0, z1 = float(self.main.z[r.start_join]), float(self.main.z[r.end_join])
            w0 = np.clip(1.0 - r.s / C.JUNCTION_BLEND_M, 0.0, 1.0)
            w1 = np.clip(1.0 - (r.length - r.s) / C.JUNCTION_BLEND_M, 0.0, 1.0)
            r.z = r.z + (z0 - r.z[0]) * w0 + (z1 - r.z[-1]) * w1
            r.z[0], r.z[-1] = z0, z1

    # -- grid, first sector, checkpoints --------------------------------------------------------

    def _first_sector(self) -> tuple[float, float]:
        """Clear run ahead of the start line (the first-sector length) and behind it."""
        m = self.main
        r = pl.radii(m.pts, True, C.RADIUS_WINDOW)
        sharp = np.flatnonzero(r < C.GRID_SHARP_RADIUS_M)
        turn = np.unwrap(m.heading) - m.heading[0]
        limit = math.radians(C.GRID_MAX_TURN_DEG)
        ahead = [float(m.s[sharp[0]]) if len(sharp) else self.lap]
        far = np.flatnonzero(np.abs(turn) > limit)
        ahead.append(float(m.s[far[0]]) if len(far) else self.lap)
        behind = [(self.lap - float(m.s[sharp[-1]])) % self.lap if len(sharp) else self.lap]
        back = np.concatenate([[m.heading[0]], m.heading[:0:-1]])
        far_b = np.flatnonzero(np.abs(np.unwrap(back) - m.heading[0]) > limit)
        behind.append(self.lap - float(m.s[len(m.s) - far_b[0]]) if len(far_b) else self.lap)
        for o in self._obstacles() + self._ramp_intervals():
            if o.route == "main":
                ahead.append(m.fwd_to(0.0, o.a, o.b))
                behind.append(m.bwd_to(0.0, o.a, o.b))
        return min(ahead), min(behind)

    def _grid(self, first_sector: float, behind: float) -> Grid:
        m = self.main
        n = int(self.options["gridSize"])
        spacing = float(self.consts["gridRowSpacingM"])
        slot_w = float(self.consts["gridSlotWidthM"])
        need_behind = C.GRID_FRONT_OFFSET_M + self._grid_length()
        if behind < need_behind:
            self.warn(
                "grid-behind-line-obstructed",
                f"The grid needs {need_behind:.0f} m clear behind the start line; there are {behind:.0f} m.",
            )
        k = pl.signed_curvature(m.pts, True, C.RADIUS_WINDOW)
        in_sector = m.s <= max(first_sector, C.SAMPLE_SPACING_M)
        pole_left = float(np.sum(k[in_sector])) >= 0.0
        slots = []
        for i in range(n):
            row = i // 2
            left = pole_left if i % 2 == 0 else not pole_left
            s = (-(C.GRID_FRONT_OFFSET_M + row * spacing)) % self.lap
            h = m.heading_at(s)
            normal = np.array([-math.sin(h), math.cos(h)]) * (slot_w / 2.0) * (1.0 if left else -1.0)
            p = m.at(s) + normal
            slots.append(
                GridSlot(f"#startpoint{i + 1:02d}", i, row, "left" if left else "right", s, [p[0], p[1], m.z_at(s)], h)
            )
        p0 = m.at(0.0)
        car_change = Transform("#startpointcarchange", [p0[0], p0[1], m.z_at(0.0)], m.heading_at(0.0))
        return Grid(n, spacing, slot_w, slots, car_change)

    def _checkpoint(self, r: _Route, index: int, s: float, finish: bool = False) -> Checkpoint:
        p = r.at(s)
        return Checkpoint(
            r.id, index, s, [p[0], p[1], r.z_at(s)], r.heading_at(s), self.width + 2 * C.CHECKPOINT_MARGIN_M, finish
        )

    def _checkpoints(self, first_sector: float) -> list[Checkpoint]:
        n = min(C.CHECKPOINT_MAX, max(C.CHECKPOINT_MIN, round(self.lap / C.CHECKPOINT_SPACING_M)))
        s1 = max(C.SAMPLE_SPACING_M, min(first_sector, max(self._grid_length(), self.lap / n)))
        step = (self.lap - s1) / (n - 1)
        out = [self._checkpoint(self.main, k, s1 + k * step) for k in range(n - 1)]
        out.append(self._checkpoint(self.main, n - 1, 0.0, finish=True))
        for r in self.routes[1:]:
            count = max(1, int(r.length // step))
            out += [self._checkpoint(r, j, r.length * (j + 1) / (count + 1)) for j in range(count)]
        return out

    # -- assembly --------------------------------------------------------------------------------

    def build(self) -> tuple[TrackModel, dict[str, Any]]:
        self._routes()
        self._crossings()
        self._jumps()
        self._bridges()
        self._elevation()
        first_sector, behind = self._first_sector()
        grid = self._grid(first_sector, behind)
        checkpoints = self._checkpoints(first_sector)

        half_w = self.width / 2.0
        routes: list[Route] = []
        edges = []
        min_radius = math.inf
        for r in self.routes:
            normal = np.column_stack([-np.sin(r.heading), np.cos(r.heading)]) * half_w
            left, right = r.pts + normal, r.pts - normal
            edges += [left, right]
            min_radius = min(min_radius, float(np.min(pl.radii(r.pts, r.closed, C.RADIUS_WINDOW))))
            z = r.z.tolist()
            samples = [
                Sample(x, y, zz, s, h, 0.0, self.width)
                for (x, y), zz, s, h in zip(r.pts.tolist(), z, r.s.tolist(), r.heading.tolist(), strict=True)
            ]
            joins = (None, None)
            if not r.closed:
                joins = tuple(Join("main", i, float(self.main.s[i])) for i in (r.start_join, r.end_join))
            routes.append(
                Route(
                    r.id,
                    r.ai_index,
                    r.closed,
                    r.length,
                    self.width,
                    samples,
                    [[x, y, zz] for (x, y), zz in zip(left.tolist(), z, strict=True)],
                    [[x, y, zz] for (x, y), zz in zip(right.tolist(), z, strict=True)],
                    *joins,
                )
            )
        all_edges = np.vstack(edges)
        all_z = np.concatenate([r.z for r in self.routes])
        max_abs_road = float(np.max(np.abs(all_edges)))
        max_abs = max_abs_road + C.TERRAIN_MARGIN_M
        bounds = Bounds(
            *(
                float(v)
                for v in (all_edges[:, 0].min(), all_edges[:, 0].max(), all_edges[:, 1].min(), all_edges[:, 1].max())
            ),
            float(all_z.min()),
            float(all_z.max()),
            max_abs_road,
            C.TERRAIN_MARGIN_M,
            max_abs,
            float(self.consts["maxAbsCoordinateM"]),
        )

        crossings = []
        for c in self.crossings:
            branches = []
            for rid, s in c.branches:
                r = self.by_id[rid]
                branches.append(Branch(rid, s, r.param(s), r.heading_at(s), r.z_at(s)))
            is_bridge = c.type == "bridge" and c.over is not None
            crossings.append(
                CrossingRecord(
                    c.id,
                    c.annotated,
                    c.detected,
                    c.type,
                    [float(c.position[0]), float(c.position[1])],
                    c.angle_deg,
                    branches,
                    c.over if is_bridge else None,
                    c.halves[c.over] if is_bridge else 0.0,
                    c.up,
                    c.down,
                    C.BRIDGE_DECK_HEIGHT_M if is_bridge else 0.0,
                )
            )

        jumps = []
        for j in self.jumps:
            r = self.by_id[j.route]
            p = r.at(j.s)
            offsets = np.arange(
                math.floor(j.a - j.s), math.ceil(j.b - j.s) + C.JUMP_PROFILE_STEP_M, C.JUMP_PROFILE_STEP_M
            )
            dz = el.jump_profile(j.size, j.bidirectional, offsets)
            jumps.append(
                JumpRecord(
                    j.id,
                    j.route,
                    j.size,
                    j.bidirectional,
                    j.s,
                    [p[0], p[1], r.z_at(j.s)],
                    r.heading_at(j.s),
                    C.JUMP_SIZES[j.size].height_m,
                    j.a,
                    j.b,
                    j.layouts,
                    j.in_base,
                    [[float(o), float(h)] for o, h in zip(offsets, dz, strict=True)],
                )
            )

        ai_lines = [
            AiLine(
                r.id, r.ai_index, r.closed, [[x, y, z] for (x, y), z in zip(r.pts.tolist(), r.z.tolist(), strict=True)]
            )
            for r in self.routes
        ]
        model = TrackModel(
            format_version=C.MODEL_FORMAT_VERSION,
            generator=f"trackgen_geom {__version__}",
            slug=self.meta["slug"],
            name=self.meta["name"],
            coordinate_system=dict(COORDINATE_SYSTEM),
            reverse=self.reverse,
            lap_length_m=self.main.length,
            width_m=self.width,
            elevation=self.options["elevation"],
            canvas_transform=CanvasTransform(self.scale, float(-self.centre[0]), float(-self.centre[1])),
            bounds=bounds,
            routes=routes,
            crossings=crossings,
            jumps=jumps,
            grid=grid,
            checkpoints=checkpoints,
            ai_lines=ai_lines,
            first_sector_length_m=first_sector,
            warnings=[f"{w.code}: {w.message}" for w in self.warnings],
        )
        facts: dict[str, Any] = {
            "firstSectorLengthM": _round(first_sector),
            "maxAbsCoordinateM": _round(max_abs),
            "minCornerRadiusM": _round(min(min_radius, 1e5)),
            "crossings": self._crossing_facts(),
            "altRoutes": {
                rid: {"startGapM": _round(g0), "endGapM": _round(g1)} for rid, (g0, g1) in self.alt_gaps.items()
            },
        }
        return model, facts
