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
    ElevationMarkerRecord,
    Grid,
    GridSlot,
    Join,
    JumpRecord,
    LayoutRecord,
    Route,
    Sample,
    TrackModel,
    Transform,
)
from .polyline import GeometryError

__all__ = ["GeometryIssue", "GeometryWarning", "Result", "build_track_model"]

FACT_DECIMALS = 3


@dataclass(frozen=True)
class GeometryIssue:
    code: str
    message: str


GeometryWarning = GeometryIssue


@dataclass
class Result:
    """Output of :func:`build_track_model`.

    ``option_result`` is the rules evaluation without facts (option-level rules; geometry rules
    deferred); ``rule_result`` is the full evaluation with the computed facts. ``model``, ``facts``
    and the SVGs are None when the spec is schema-invalid, has no main loop, or the sketch is
    rejected; rejections are listed in ``errors`` (e.g. ``main-loop-too-small``).
    """

    model: TrackModel | None
    facts: dict[str, Any] | None
    rule_result: rules.EvaluationResult
    option_result: rules.EvaluationResult
    preview_svg: str | None
    elevation_svg: str | None
    warnings: list[GeometryIssue] = field(default_factory=list)
    errors: list[GeometryIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.model is not None and not self.errors and self.rule_result.valid


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
        self.m = np.zeros(len(pts))  # height-marker profile (custom: the road; presets: the bumps)
        self.bank = np.zeros(len(pts))  # cross-slope, radians; positive raises the left edge (imported tracks)
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

    def m_at(self, s: float) -> float:
        return float(pl.interp_at(self.cum, self.m, self.closed, s))

    def bank_at(self, s: float) -> float:
        return float(pl.interp_at(self.cum, self.bank, self.closed, s))

    def surface_z(self, s: float, offset: float) -> float:
        """Road surface height ``offset`` metres left of the centreline (negative: right), on the camber."""
        return self.z_at(s) + offset * math.tan(self.bank_at(s))

    def max_grade(self, baseline: float) -> float:
        """Steepest |dz| / baseline over any ``baseline`` metres of the route."""
        if self.length <= baseline:
            return 0.0
        s = self.s if self.closed else self.s[self.s <= self.length - baseline]
        dz = pl.interp_at(self.cum, self.z, self.closed, s + baseline) - pl.interp_at(self.cum, self.z, self.closed, s)
        return float(np.max(np.abs(dz))) / baseline if len(s) else 0.0

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
    climb: float = C.BRIDGE_DECK_HEIGHT_M  # bridges: height the over branch rises above its own profile
    step: float = 0.0  # flat crossings: marker-profile height difference between the branches


@dataclass
class _Marker:
    id: str
    route: str
    s: float
    height: float
    grade: float = 0.0
    feature: str | None = None


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


def _oversized(spec: Any) -> list[rules.SchemaError]:
    """Sketch arrays over their schema ``maxItems``, found without a full validation pass (which is
    slow for huge inputs). Anything else malformed is left to the schema validation."""
    sketch = spec.get("sketch") if isinstance(spec, Mapping) else None
    if not isinstance(sketch, Mapping):
        return []
    defs = rules.load_schema()["$defs"]
    checks: list[tuple[str, Any, Mapping[str, Any]]] = []
    main = sketch.get("mainLoop")
    if isinstance(main, Mapping):
        checks.append(("/sketch/mainLoop/points", main.get("points"), defs["MainLoop"]["properties"]["points"]))
    for key in ("altRoutes", "crossings", "jumps", "elevationMarkers"):
        checks.append((f"/sketch/{key}", sketch.get(key), defs["Sketch"]["properties"][key]))
    alts = sketch.get("altRoutes")
    if isinstance(alts, list):
        limit = defs["Sketch"]["properties"]["altRoutes"]["maxItems"]
        for i, alt in enumerate(alts[: limit + 1]):
            if isinstance(alt, Mapping):
                checks.append(
                    (f"/sketch/altRoutes/{i}/points", alt.get("points"), defs["AltRoute"]["properties"]["points"])
                )
    return [
        rules.SchemaError(path, f"{len(value)} items is more than the maximum of {schema['maxItems']}")
        for path, value, schema in checks
        if isinstance(value, list) and len(value) > schema["maxItems"]
    ]


def build_track_model(spec: Mapping[str, Any] | Any) -> Result:
    """Validate ``spec``, build the TrackModel and geometry facts, and evaluate the rules.

    1. Schema validation and the option-level rules (geometry rules deferred).
    2. Geometry: centreline, alt routes, crossings, elevation, grid, checkpoints -> facts.
    3. The full rules evaluation with the facts.

    Deterministic: the same spec gives byte-identical ``model.to_json()`` and SVGs.
    """
    to_dict = getattr(spec, "to_dict", None)
    spec = to_dict() if callable(to_dict) else spec
    warnings: list[GeometryIssue] = []
    oversized = _oversized(spec)
    if oversized:
        rejected = rules.EvaluationResult(schema_valid=False, schema_errors=oversized)
        return Result(None, None, rejected, rejected, None, None, warnings)
    option_result = rules.evaluate(spec)
    if not option_result.schema_valid:
        return Result(None, None, option_result, option_result, None, None, warnings)
    if "mainLoop" not in spec["sketch"] and "imported" not in spec["sketch"]:
        warnings.append(GeometryWarning("main-loop-missing", "There is no main loop to build."))
        return Result(None, None, option_result, option_result, None, None, warnings)
    symmetric = any(f.flag == "symmetricBridgeRamps" for f in option_result.flags)
    try:
        model, facts = _Builder(spec, symmetric, warnings).build()
    except GeometryError as exc:
        error = GeometryIssue(exc.code, str(exc))
        return Result(None, None, option_result, option_result, None, None, warnings, [error])

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
        self.imported = self.sketch.get("imported")

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(GeometryWarning(code, message))

    # -- routes ----------------------------------------------------------------------------------

    def _imported_routes(self) -> None:
        """An imported (real-world) track: the centreline is used exactly as given (no smoothing, scaling,
        corner easing or centring), with its heights and camber; the lap is its length. Canvas units are its
        metres, with the canvas centre at the origin."""
        pts = np.array([[p["x"], p["y"]] for p in self.imported["centreline"]], dtype=np.float64)
        z = np.array([p["z"] for p in self.imported["centreline"]], dtype=np.float64)
        bank = np.radians([p["bankDeg"] for p in self.imported["centreline"]])
        keep = np.hypot(*(np.roll(pts, -1, axis=0) - pts).T) > 1e-6  # drop repeats (and a repeated first point)
        pts, z, bank = pts[keep], z[keep], bank[keep]
        if len(pts) < 3:
            raise GeometryError("The imported centreline needs at least three distinct points.", "main-loop-degenerate")
        ignored = [k for k in ("mainLoop", "startFinish", "altRoutes", "crossings", "elevationMarkers") if self.sketch.get(k)]
        if ignored:
            self.warn("imported-ignores-drawn", f"The imported centreline is used; ignoring {', '.join(ignored)}.")
        canvas = self.sketch["canvas"]
        self.scale = 1.0
        self.centre = np.array([float(canvas["width"]) / 2.0, -float(canvas["height"]) / 2.0])
        self.main = _Route("main", pts, True, 0)
        self.main.finalize()
        self.main.imported_z = z
        self.main.bank = bank
        self.lap = self.main.length
        want = float(self.options["lapLengthKm"]) * 1000.0
        if abs(want - self.lap) > C.IMPORTED_LAP_TOLERANCE * self.lap:
            self.warn("imported-lap-length", f"The imported centreline is {self.lap:.0f} m; lapLengthKm ({want:.0f} m) is ignored.")
        self.routes = [self.main]
        self.alt_gaps = {}
        alts = _unique_by_id(self.imported.get("altLayouts") or [], "imported-layout", self.warnings)
        for k, alt in enumerate(alts):
            r = self._imported_branch(alt, -(k + 1))
            if r is None:
                continue
            self.routes.append(r)
            s0, s1 = float(self.main.s[r.start_join]), float(self.main.s[r.end_join])
            self.layouts.append(LayoutRecord(alt["id"], alt["name"], s0 + r.length + (self.lap - s1), [
                {"route": "main", "fromS": 0.0, "toS": s0},
                {"route": r.id, "fromS": 0.0, "toS": r.length},
                {"route": "main", "fromS": s1, "toS": self.lap},
            ]))
        if self.layouts:
            name = self.imported.get("mainLayoutName") or "Main"
            self.layouts.insert(0, LayoutRecord("main", name, self.lap, [{"route": "main", "fromS": 0.0, "toS": self.lap}]))
        self.by_id = {r.id: r for r in self.routes}

    def _imported_branch(self, alt: Mapping[str, Any], ai_index: int) -> _Route | None:
        """An imported alternative layout's branch: an open route whose ends are snapped to the nearest main
        centreline points (used as given otherwise). The branch must leave and rejoin the main route after the
        start/finish line in driving order, so every layout shares the line and the grid."""
        pts = np.array([[p["x"], p["y"]] for p in alt["centreline"]], dtype=np.float64)
        z = np.array([p["z"] for p in alt["centreline"]], dtype=np.float64)
        bank = np.radians([p["bankDeg"] for p in alt["centreline"]])
        m = self.main
        i0 = int(np.argmin(np.hypot(*(m.pts - pts[0]).T)))
        i1 = int(np.argmin(np.hypot(*(m.pts - pts[-1]).T)))
        gap0, gap1 = float(np.hypot(*(m.pts[i0] - pts[0]))), float(np.hypot(*(m.pts[i1] - pts[-1])))
        if not m.s[i0] < m.s[i1]:
            self.warn("imported-layout-unsupported",
                      f"Layout {alt['id']} must leave and rejoin the main centreline after the start/finish line; "
                      "it is left out.")
            return None
        pts[0], pts[-1] = m.pts[i0], m.pts[i1]
        z[0], z[-1] = m.imported_z[i0], m.imported_z[i1]
        bank[0], bank[-1] = m.bank[i0], m.bank[i1]
        keep = np.concatenate([[True], np.hypot(*np.diff(pts, axis=0).T) > 1e-6])
        pts, z, bank = pts[keep], z[keep], bank[keep]
        if len(pts) < 3:
            self.warn("imported-layout-unsupported", f"Layout {alt['id']}'s branch is degenerate; it is left out.")
            return None
        r = _Route(alt["id"], pts, False, ai_index)
        r.start_join, r.end_join = i0, i1
        r.finalize()
        r.imported_z = z
        r.bank = bank
        self.alt_gaps[alt["id"]] = (gap0, gap1)
        return r

    def _routes(self) -> None:
        self.layouts: list[LayoutRecord] = []
        if self.imported:
            self._imported_routes()
            return
        need = self.width / 2.0 + float(self.consts["minInnerEdgeRadiusM"])
        target = need * (1.0 + C.RELAX_TARGET_MARGIN)
        raw_main = [(p["x"], p["y"]) for p in self.sketch["mainLoop"]["points"]]
        canvas = self.sketch["canvas"]
        perimeter = pl.length(canvas_to_world(raw_main), True)
        size = max(float(canvas["width"]), float(canvas["height"]))
        if not perimeter >= C.MIN_MAIN_LOOP_CANVAS_FRACTION * size:
            raise GeometryError(
                f"The main loop is too small to build: {perimeter:.3g} canvas units long on a {size:g}-unit canvas.",
                "main-loop-too-small",
            )
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
        self._check_alt_sizes(alts, pts)
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

    def _check_alt_sizes(self, alts: list[Mapping[str, Any]], main_pts: np.ndarray) -> None:
        """Reject absurd alternative-route strokes before any smoothing (cheap, vectorised)."""
        centre = (main_pts.min(axis=0) + main_pts.max(axis=0)) / 2.0
        limit = float(self.consts["maxAbsCoordinateM"])
        for alt in alts:
            raw = canvas_to_world([(p["x"], p["y"]) for p in alt["points"]]) * self.scale
            length = pl.length(raw, False)
            if not length <= C.ALT_ROUTE_MAX_LAP_FACTOR * self.lap:
                raise GeometryError(
                    f"Alternative route {alt['id']} is {length:.3g} m long, more than "
                    f"{C.ALT_ROUTE_MAX_LAP_FACTOR:g} times the lap.",
                    "alt-route-too-long",
                )
            reach = float(np.max(np.abs(raw - centre)))
            if not reach <= limit:
                raise GeometryError(
                    f"Alternative route {alt['id']} reaches {reach:.3g} m from the centre (max {limit:g} m).",
                    "alt-route-out-of-bounds",
                )

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
        annotations = [] if self.imported else _unique_by_id(self.sketch.get("crossings", []), "crossing", self.warnings)
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

    # -- height markers --------------------------------------------------------------------------

    def _markers(self) -> None:
        """Project the height markers onto their routes and build each route's marker profile ``m``:
        custom elevation uses it as the road height; presets add it to the terrain as bumps."""
        self.custom = self.options["elevation"] == "custom"
        self.markers: list[_Marker] = []
        if self.imported:
            # The road heights are the imported ones; they play the part of custom elevation's marker profile
            # (the grid grade fact reads it), on flat preset ground.
            self.controls = {}
            for r in self.routes:
                r.m = r.imported_z.copy()
            return
        for mk in _unique_by_id(self.sketch.get("elevationMarkers", []), "elevation-marker", self.warnings):
            route = self.by_id.get(mk.get("route", "main"))
            if route is None:
                self.warn(
                    "elevation-marker-route-missing",
                    f"Height marker {mk['id']} is on a route that wasn't built; it is left out.",
                )
                continue
            proj = pl.project(route.pts, route.closed, self.world(mk["position"]))
            if proj.distance > C.MARKER_OFF_ROUTE_M:
                self.warn(
                    "elevation-marker-off-route",
                    f"Height marker {mk['id']} is {proj.distance:.0f} m from route {route.id}; it is moved onto it.",
                )
            s = float(proj.s)
            if not route.closed:
                lo, hi = C.JUNCTION_CLEAR_M, route.length - C.JUNCTION_CLEAR_M
                if lo > hi:
                    lo = hi = route.length / 2.0
                if not lo <= s <= hi:
                    self.warn(
                        "elevation-marker-moved",
                        f"Height marker {mk['id']} was moved away from the end of route {route.id}.",
                    )
                    s = min(max(s, lo), hi)
            self.markers.append(_Marker(mk["id"], route.id, s, float(mk["heightM"])))
        additive = not self.custom
        self.controls: dict[str, list[el.Control]] = {}
        for r in self.routes:
            own = [(m.id, m.s, m.height) for m in self.markers if m.route == r.id]
            ends = (0.0, 0.0)
            if not r.closed:
                ends = (float(self.main.m[r.start_join]), float(self.main.m[r.end_join]))
            ctrl = el.marker_controls(own, r.closed, r.length, additive, ends)
            self.controls[r.id] = ctrl
            r.m = el.marker_profile(r.s, ctrl, r.closed, r.length)
            grades = el.control_grades(ctrl, r.closed, r.length)
            for m in self.markers:
                if m.route == r.id:
                    m.grade = grades.get(m.id, 0.0)
        self._level_flat_crossings()

    def _level_flat_crossings(self) -> None:
        """Both branches of a flat crossing must meet at one height: ease each branch's marker profile to
        their mean height there. The step before levelling is reported (rule elevation-flat-crossing-level)."""
        for c in self.crossings:
            if c.type != "flat" or not c.detected or len(c.branches) != 2:
                continue
            (ra_id, sa), (rb_id, sb) = c.branches
            ra, rb = self.by_id[ra_id], self.by_id[rb_id]
            ma, mb = ra.m_at(sa), rb.m_at(sb)
            c.step = abs(ma - mb)
            if c.step <= 1e-9:
                continue
            target = (ma + mb) / 2.0
            half = max(C.FLAT_LEVEL_MIN_M, C.FLAT_LEVEL_FACTOR * c.step)
            for r, s, m in ((ra, sa, ma), (rb, sb, mb)):
                r.m = r.m + (target - m) * el.level_window(r.wrap(r.s - s), half)

    def _bridge_climb(self, c: _Crossing) -> float:
        """Height the over branch must rise above its own profile so the deck clears the under branch's
        local height by BRIDGE_DECK_HEIGHT_M. Only the marker profiles matter: the terrain is the same
        for both branches at the crossing."""
        if len(c.branches) != 2 or c.over is None:
            return C.BRIDGE_DECK_HEIGHT_M

        def window(i: int, pick: Any) -> float:
            rid, s = c.branches[i]
            r = self.by_id[rid]
            near = np.abs(r.wrap(r.s - s)) <= c.halves[i]
            vals = [r.m_at(s)] + r.m[near].tolist()
            return float(pick(vals))

        under = 1 - c.over
        return max(0.0, C.BRIDGE_DECK_HEIGHT_M + window(under, max) - window(c.over, min))

    def _marker_features(self) -> None:
        grid = (-C.START_CLEAR_M, self._grid_zone_end())
        ramps = self._ramp_intervals()
        for m in self.markers:
            r = self.by_id[m.route]
            if m.route == "main" and r.dist_to(m.s, *grid) == 0.0:
                m.feature = "grid"
            elif any(o.route == m.route and r.dist_to(m.s, o.a, o.b) == 0.0 for o in ramps):
                m.feature = "bridge"

    def _grid_max_grade(self) -> float:
        """Steepest grade of the main loop's marker profile over the grid area (behind the line to the
        first checkpoint)."""
        m = self.main
        end = self._grid_zone_end()
        idx = np.flatnonzero((m.s <= end) | (m.s >= m.length - C.START_CLEAR_M))
        steepest = 0.0
        for i in idx:
            j = (i + 1) % len(m.s)
            ds = (m.s[j] - m.s[i]) % m.length
            if ds > 1e-6:
                steepest = max(steepest, abs(float(m.m[j] - m.m[i])) / ds)
        return steepest

    # -- obstacles, bridges and facts --------------------------------------------------------------

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

    def _grid_zone_end(self) -> float:
        """Arc length of the first checkpoint: the front (pole) row plus a margin."""
        rows = math.ceil(int(self.options["gridSize"]) / 2)
        pole = C.GRID_LINE_OFFSET_M + (rows - 1) * float(self.consts["gridRowSpacingM"])
        return pole + C.GRID_CHECKPOINT_MARGIN_M

    def _bridges(self) -> None:
        obstacles = self._obstacles()
        grid = _Obstacle("main", -C.START_CLEAR_M, self._grid_zone_end(), "grid")
        ramp_min = float(self.consts["bridgeRampMinLengthM"])
        for ci, c in enumerate(self.crossings):
            if c.type != "bridge" or c.over is None or not c.detected:
                continue
            c.climb = self._bridge_climb(c)
            ramp_len = el.bridge_ramp_length(ramp_min, c.climb)
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
            if min(c.up, c.down) < ramp_len:
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
        """Facts for annotations that match a real crossing. ``nearestJumpM`` is left out when neither
        branch has a jump or ramp."""
        facts: dict[str, dict[str, float]] = {}
        for ci, c in enumerate(self.crossings):
            if not (c.annotated and c.detected):
                continue
            features = [_Obstacle(j.route, j.a, j.b, "jump") for j in self.jumps] + self._ramp_intervals(ci)
            nearest = math.inf
            for rid, s in c.branches:
                route = self.by_id[rid]
                for f in features:
                    if f.route == rid:
                        nearest = min(nearest, route.dist_to(s, f.a, f.b))
            entry = {"nearestJumpM": _round(nearest)} if math.isfinite(nearest) else {}
            if c.type == "bridge":
                entry["availableRampLengthM"] = _round(c.available or 0.0)
                grade = el.ramp_peak_grade(c.climb, min(c.up, c.down)) if c.climb > 0 else 0.0
                entry["rampGradePct"] = _round(min(100.0 * grade, C.MAX_REPORTED_GRADE_PCT))
            elif c.type == "flat":
                entry["heightStepM"] = _round(c.step)
            facts[c.id] = entry
        return facts

    def _unannotated_facts(self) -> list[dict[str, Any]]:
        """Unannotated crossings in canvas coordinates, so the UI can mark them."""
        out = []
        for c in self.crossings:
            if not c.annotated:
                x, y = self.to_canvas(c.position)
                out.append({"id": c.id, "x": x, "y": y})
        return out

    def to_canvas(self, p: np.ndarray) -> tuple[float, float]:
        w = (p + self.centre) / self.scale
        return _round(w[0], 2), _round(-w[1], 2)

    # -- elevation -------------------------------------------------------------------------------

    def _elevation(self) -> None:
        self.terrain = el.Terrain(self.meta["slug"], "imported" if self.imported else self.options["elevation"])
        self.terrain.limit_grade([(r.pts, r.s, r.closed) for r in self.routes])
        for r in self.routes:
            r.z = self.terrain.height(r.pts) + r.m
        for c in self.crossings:
            if c.type == "bridge" and c.over is not None and c.detected:
                rid, s = c.branches[c.over]
                r = self.by_id[rid]
                r.z = r.z + el.bridge_bump(r.wrap(r.s - s), c.halves[c.over], c.up, c.down, c.climb)
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

    def _first_sector(self) -> float:
        """Clear road ahead of the start line, where the grid sits: up to the first tight corner, a turn of
        more than GRID_MAX_TURN_DEG, or a crossing, jump, bridge ramp or junction."""
        m = self.main
        r = pl.radii(m.pts, True, C.RADIUS_WINDOW)
        sharp = np.flatnonzero(r < C.GRID_SHARP_RADIUS_M)
        turn = np.unwrap(m.heading) - m.heading[0]
        far = np.flatnonzero(np.abs(turn) > math.radians(C.GRID_MAX_TURN_DEG))
        ahead = [float(m.s[sharp[0]]) if len(sharp) else self.lap, float(m.s[far[0]]) if len(far) else self.lap]
        for o in self._obstacles() + self._ramp_intervals():
            if o.route == "main":
                ahead.append(m.fwd_to(0.0, o.a, o.b))
        return min(ahead)

    def _grid(self, first_sector: float) -> Grid:
        """Grid slots ahead of the start line (to verify in game): the back row GRID_LINE_OFFSET_M past the
        line, the pole row furthest ahead, in pairs gridSlotWidthM apart."""
        m = self.main
        n = int(self.options["gridSize"])
        rows = math.ceil(n / 2)
        spacing = float(self.consts["gridRowSpacingM"])
        slot_w = float(self.consts["gridSlotWidthM"])
        k = pl.signed_curvature(m.pts, True, C.RADIUS_WINDOW)
        in_sector = m.s <= max(first_sector, C.SAMPLE_SPACING_M)
        pole_left = float(np.sum(k[in_sector])) >= 0.0
        slots = []
        for i in range(n):
            row = i // 2
            left = pole_left if i % 2 == 0 else not pole_left
            s = C.GRID_LINE_OFFSET_M + (rows - 1 - row) * spacing
            h = m.heading_at(s)
            offset = (slot_w / 2.0) * (1.0 if left else -1.0)
            p = m.at(s) + np.array([-math.sin(h), math.cos(h)]) * offset
            slots.append(
                GridSlot(
                    f"#startpoint{i + 1:02d}", i, row, "left" if left else "right", s,
                    [p[0], p[1], m.surface_z(s, offset)], h,
                )
            )
        p0 = m.at(0.0)
        car_change = Transform("#startpointcarchange", [p0[0], p0[1], m.z_at(0.0)], m.heading_at(0.0))
        return Grid(n, spacing, slot_w, slots, car_change)

    def _checkpoint(self, r: _Route, index: int, s: float, finish: bool = False) -> Checkpoint:
        p = r.at(s)
        return Checkpoint(
            r.id, index, s, [p[0], p[1], r.z_at(s)], r.heading_at(s), self.width + 2 * C.CHECKPOINT_MARGIN_M, finish
        )

    def _checkpoints(self) -> list[Checkpoint]:
        n = min(C.CHECKPOINT_MAX, max(C.CHECKPOINT_MIN, round(self.lap / C.CHECKPOINT_SPACING_M)))
        s1 = self._grid_zone_end()
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
        self._markers()
        self._bridges()
        self._elevation()
        self._marker_features()
        first_sector = self._first_sector()
        grid = self._grid(first_sector)
        checkpoints = self._checkpoints()

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
            rise = half_w * np.tan(r.bank)  # camber: the left edge is this much above the centre, the right below
            zl, zr = (r.z + rise).tolist(), (r.z - rise).tolist()
            samples = [
                Sample(x, y, zz, s, h, b, self.width)
                for (x, y), zz, s, h, b in zip(
                    r.pts.tolist(), z, r.s.tolist(), r.heading.tolist(), r.bank.tolist(), strict=True
                )
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
                    [[x, y, zz] for (x, y), zz in zip(left.tolist(), zl, strict=True)],
                    [[x, y, zz] for (x, y), zz in zip(right.tolist(), zr, strict=True)],
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
                    c.climb if is_bridge else 0.0,
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

        marker_records = []
        for m in self.markers:
            r = self.by_id[m.route]
            p = r.at(m.s)
            marker_records.append(
                ElevationMarkerRecord(m.id, m.route, m.s, [p[0], p[1], r.z_at(m.s)], m.height, 100.0 * m.grade, m.feature)
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
            elevation_markers=marker_records,
            warnings=[f"{w.code}: {w.message}" for w in self.warnings],
            layouts=self.layouts,
        )
        facts: dict[str, Any] = {
            "firstSectorLengthM": _round(first_sector),
            "maxAbsCoordinateM": _round(max_abs),
            "minCornerRadiusM": _round(min(min_radius, 1e5)),
            "crossings": self._crossing_facts(),
            "unannotatedCrossings": self._unannotated_facts(),
            "unmatchedCrossingAnnotations": [c.id for c in self.crossings if c.annotated and not c.detected],
            "altRoutes": {
                rid: {"startGapM": _round(g0), "endGapM": _round(g1)} for rid, (g0, g1) in self.alt_gaps.items()
            },
            "elevationMarkers": {
                m.id: {
                    "sM": _round(m.s),
                    "gradePct": _round(min(100.0 * m.grade, C.MAX_REPORTED_GRADE_PCT)),
                    **({"feature": m.feature} if m.feature else {}),
                }
                for m in self.markers
            },
            "gridMaxGradePct": _round(min(100.0 * self._grid_max_grade(), C.MAX_REPORTED_GRADE_PCT)),
            "heightRangeM": _round(float(all_z.max() - all_z.min())),
            "maxGradePct": _round(
                min(100.0 * max(r.max_grade(C.GRADE_BASELINE_M) for r in self.routes), C.MAX_REPORTED_GRADE_PCT)
            ),
            "maxBankDeg": _round(math.degrees(max(float(np.max(np.abs(r.bank))) for r in self.routes))),
        }
        return model, facts
