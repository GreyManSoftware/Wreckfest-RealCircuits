"""Integration tests for build_track_model: TrackModel, geometry facts, rules and previews."""

from __future__ import annotations

import copy
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from trackgen_geom import build_track_model, rules
from trackgen_geom import constants as C
from trackgen_geom import elevation as el
from trackgen_geom import polyline as pl
from trackgen_geom.generated import GeometryFacts
from trackgen_geom.model import Route

TESTS = Path(__file__).resolve().parent
SAMPLES = sorted((TESTS / "samples").glob("*.json"))
OUTPUT = TESTS / "output"
VECTORS = sorted((TESTS.parents[1] / "schema" / "test-vectors").glob("*.json"))
GEOMETRY_RULES = {r["id"] for r in rules.load_rules()["rules"] if r["evaluatedBy"] == "geometry"}
RULE_CONSTANTS = rules.load_rules()["constants"]


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sample(name: str) -> dict[str, Any]:
    return load(TESTS / "samples" / f"{name}.json")


def xy(route: Route) -> np.ndarray:
    return np.array([[s.x, s.y] for s in route.samples])


def z(route: Route) -> np.ndarray:
    return np.array([s.z for s in route.samples])


def violations(result: Any) -> set[str]:
    return {v.rule_id for v in result.rule_result.violations}


def codes(result: Any) -> set[str]:
    return {w.code for w in result.warnings}


@pytest.fixture(scope="module")
def built() -> dict[str, Any]:
    return {p.stem: build_track_model(load(p)) for p in SAMPLES}


# --- samples ---------------------------------------------------------------------------------


def test_samples_exist() -> None:
    assert {p.stem for p in SAMPLES} >= {"oval", "figure8_bridge", "loop_alt_jump", "hilly_circuit"}


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_sample_builds_valid_track_and_previews(path: Path, built: dict[str, Any]) -> None:
    result = built[path.stem]
    assert result.model is not None
    assert result.rule_result.schema_valid, result.rule_result.schema_errors
    assert result.rule_result.deferred == []
    assert result.valid, [v.message for v in result.rule_result.violations]
    assert result.warnings == []
    assert result.preview_svg.startswith("<svg") and result.preview_svg.rstrip().endswith("</svg>")
    assert result.elevation_svg.startswith("<svg")
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / f"{path.stem}.svg").write_text(result.preview_svg, encoding="utf-8")
    (OUTPUT / f"{path.stem}-elevation.svg").write_text(result.elevation_svg, encoding="utf-8")
    (OUTPUT / f"{path.stem}.model.json").write_text(result.model.to_json(indent=1), encoding="utf-8")


# --- stroke to centreline, scaling, bounds ---------------------------------------------------


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_lap_length_is_exact(path: Path, built: dict[str, Any]) -> None:
    spec = load(path)
    model = built[path.stem].model
    main = model.route("main")
    target = spec["options"]["lapLengthKm"] * 1000
    if "imported" in spec["sketch"]:
        # a real track keeps its own length (lapLengthKm is informational, within 2 %)
        real = pl.length(np.array([[p["x"], p["y"]] for p in spec["sketch"]["imported"]["centreline"]]), True)
        assert model.lap_length_m == pytest.approx(real, rel=1e-9)
        target = real
    assert abs(pl.length(xy(main), True) - target) / target < 0.005
    assert model.lap_length_m == pytest.approx(target, rel=1e-9)
    spacing = pl.seg_lengths(xy(main), True)
    assert spacing.max() <= C.SAMPLE_SPACING_M * 1.05
    s = np.array([smp.s for smp in main.samples])
    assert s[0] == 0.0 and np.all(np.diff(s) > 0)


def test_start_finish_is_s0_and_heading_follows_spec() -> None:
    spec = sample("oval")
    model = build_track_model(spec).model
    main = model.route("main")
    first = main.samples[0]
    # Start/finish marker is on the bottom straight (canvas y = 450), heading +x on the canvas.
    t = model.canvas_transform
    sf = spec["sketch"]["startFinish"]["position"]
    marker = (t.scale * sf["x"] + t.offset_x, -t.scale * sf["y"] + t.offset_y)
    assert math.hypot(first.x - marker[0], first.y - marker[1]) < 1.0
    assert math.cos(first.heading) > 0.99  # canvas heading 0 = +x = world +x

    reverse = copy.deepcopy(spec)
    reverse["sketch"]["startFinish"]["headingDeg"] = 180
    rmain = build_track_model(reverse).model.route("main")
    assert math.cos(rmain.samples[0].heading) < -0.99
    # Same loop, opposite direction.
    assert pl.length(xy(rmain), True) == pytest.approx(pl.length(xy(main), True))


def test_default_start_is_first_point_heading_to_second() -> None:
    spec = sample("oval")
    del spec["sketch"]["startFinish"]
    pts = spec["sketch"]["mainLoop"]["points"]
    model = build_track_model(spec).model
    first = model.route("main").samples[0]
    want = math.atan2(-(pts[1]["y"] - pts[0]["y"]), pts[1]["x"] - pts[0]["x"])
    assert math.cos(first.heading - want) > 0.9


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_bounds_are_centred_and_reported(path: Path, built: dict[str, Any]) -> None:
    result = built[path.stem]
    b = result.model.bounds
    assert b.min_x == pytest.approx(-b.max_x, abs=result.model.width_m)
    assert b.min_y == pytest.approx(-b.max_y, abs=result.model.width_m)
    edges = np.array([p[:2] for r in result.model.routes for p in r.left_edge + r.right_edge])
    assert b.max_abs_road_m == pytest.approx(np.abs(edges).max())
    assert result.facts["maxAbsCoordinateM"] == pytest.approx(b.max_abs_road_m + C.TERRAIN_MARGIN_M, abs=1e-3)
    assert b.limit_m == RULE_CONSTANTS["maxAbsCoordinateM"]
    assert result.facts["maxAbsCoordinateM"] <= b.limit_m


def test_edges_are_half_width_from_centreline(built: dict[str, Any]) -> None:
    route = built["hilly_circuit"].model.route("main")
    c = xy(route)
    left = np.array([p[:2] for p in route.left_edge])
    right = np.array([p[:2] for p in route.right_edge])
    assert np.allclose(np.hypot(*(left - c).T), route.width_m / 2)
    assert np.allclose(np.hypot(*(right - c).T), route.width_m / 2)
    h = np.array([s.heading for s in route.samples])
    normal = np.column_stack([-np.sin(h), np.cos(h)])
    assert np.all(((left - c) * normal).sum(axis=1) > 0)


# --- minimum corner radius ---------------------------------------------------------------------


def square_spec(width: float, lap_km: float) -> dict[str, Any]:
    spec = sample("oval")
    spec["sketch"]["mainLoop"]["points"] = [
        {"x": x, "y": y} for x, y in [(100, 100), (500, 100), (900, 100), (900, 500), (500, 500), (100, 500)]
    ]
    spec["sketch"]["startFinish"] = {"position": {"x": 300, "y": 500}, "headingDeg": 180}
    spec["options"].update(widthM=width, lapLengthKm=lap_km)
    return spec


@pytest.mark.parametrize(("width", "lap_km"), [(20, 0.8), (20, 5.0), (12, 1.5)])
def test_square_corners_are_relaxed_to_the_minimum_radius(width: float, lap_km: float) -> None:
    result = build_track_model(square_spec(width, lap_km))
    need = width / 2 + RULE_CONSTANTS["minInnerEdgeRadiusM"]
    assert result.facts["minCornerRadiusM"] >= need
    assert "min-corner-radius" not in violations(result)
    assert "corner-radius-unresolved" not in codes(result)
    radii = pl.radii(xy(result.model.route("main")), True, C.RADIUS_WINDOW)
    assert radii.min() == pytest.approx(result.facts["minCornerRadiusM"], abs=1e-3)


def test_unfixable_hairpin_reports_the_measured_radius() -> None:
    spec = square_spec(20, 1.0)
    spec["sketch"]["mainLoop"]["points"] = [
        {"x": x, "y": y} for x, y in [(100, 300), (900, 300), (100, 310), (100, 500), (900, 500), (100, 600)]
    ]
    spec["sketch"]["startFinish"] = {"position": {"x": 500, "y": 300}, "headingDeg": 0}
    result = build_track_model(spec)
    assert result.facts["minCornerRadiusM"] < 16
    assert "min-corner-radius" in violations(result)
    assert "corner-radius-unresolved" in codes(result)


# --- crossings -------------------------------------------------------------------------------


def test_figure_eight_crossing_is_detected_and_matched(built: dict[str, Any]) -> None:
    result = built["figure8_bridge"]
    [c] = result.model.crossings
    assert (c.id, c.type, c.annotated, c.detected) == ("bridge1", "bridge", True, True)
    assert math.hypot(*c.position) < 5.0  # the centre of the lemniscate is the origin
    assert 80 < c.angle_deg <= 90
    a, b = c.branches
    assert a.route == b.route == "main"
    assert abs(b.s - a.s) == pytest.approx(result.model.lap_length_m / 2, rel=0.02)
    assert c.over_branch == 0  # over.param 0.25 is the first pass
    assert result.facts["crossings"]["bridge1"]["availableRampLengthM"] >= RULE_CONSTANTS["bridgeRampMinLengthM"]


def test_over_param_selects_the_other_pass() -> None:
    spec = sample("figure8_bridge")
    spec["sketch"]["crossings"][0]["over"]["param"] = 0.75
    model = build_track_model(spec).model
    [c] = model.crossings
    assert c.over_branch == 1
    over, under = c.branches[1], c.branches[0]
    assert over.z - under.z == pytest.approx(C.BRIDGE_DECK_HEIGHT_M, abs=1e-6)


def test_unannotated_crossing_fails_the_rules() -> None:
    spec = sample("figure8_bridge")
    spec["sketch"]["crossings"] = []
    result = build_track_model(spec)
    [c] = result.model.crossings
    assert (c.type, c.annotated, c.id) == ("unannotated", False, "auto1")
    assert "crossing-needs-annotation" in codes(result)
    assert result.facts["crossings"] == {}
    [fact] = result.facts["unannotatedCrossings"]
    assert fact["id"] == "auto1"
    assert math.hypot(fact["x"] - 500, fact["y"] - 350) < 5  # canvas coordinates of the lemniscate centre
    assert "crossing-annotation-required" in violations(result)
    assert not result.valid and result.rule_result.deferred == []
    assert max(z(result.model.route("main"))) < C.BRIDGE_DECK_HEIGHT_M  # no bridge built


@pytest.mark.parametrize("ctype", ["flat", "bridge"])
def test_annotation_without_crossing_is_reported_and_ignored(ctype: str) -> None:
    spec = sample("oval")
    spec["sketch"]["crossings"] = [{"id": "x", "position": {"x": 500, "y": 150}, "type": ctype}]
    if ctype == "bridge":
        spec["sketch"]["crossings"][0]["over"] = {"route": "main"}
    result = build_track_model(spec)
    assert "crossing-annotation-unmatched" in codes(result)
    assert result.facts["unmatchedCrossingAnnotations"] == ["x"]
    assert "x" not in result.facts["crossings"]
    assert {v.rule_id for v in result.rule_result.violations} == {"crossing-annotation-matches"}
    assert result.rule_result.violations[0].severity == "warning"
    assert result.rule_result.deferred == [] and result.valid
    [c] = result.model.crossings
    assert c.detected is False
    assert max(z(result.model.route("main"))) == 0.0  # nothing built for it


def test_no_jump_nearby_leaves_out_nearest_jump(built: dict[str, Any]) -> None:
    spec = sample("figure8_bridge")
    spec["sketch"]["jumps"] = []
    spec["sketch"]["crossings"][0] = {"id": "bridge1", "position": {"x": 500, "y": 350}, "type": "flat"}
    result = build_track_model(spec)
    assert result.facts["crossings"] == {"bridge1": {"heightStepM": 0.0}}
    assert result.valid and result.rule_result.deferred == []
    assert "nearestJumpM" in built["figure8_bridge"].facts["crossings"]["bridge1"]


def test_flat_crossing_near_a_jump_fails_the_clearance_rule() -> None:
    spec = sample("figure8_bridge")
    spec["sketch"]["crossings"] = [{"id": "x", "position": {"x": 500, "y": 350}, "type": "flat"}]
    spec["sketch"]["jumps"] = [{"id": "near", "position": {"x": 515, "y": 365}, "size": "small", "bidirectional": True}]
    result = build_track_model(spec)
    near = result.facts["crossings"]["x"]["nearestJumpM"]
    assert near < RULE_CONSTANTS["flatCrossingJumpClearanceM"]
    assert "flat-crossing-jump-clearance" in violations(result)
    # Flat crossing: both branches at the same height.
    [c] = result.model.crossings
    assert c.branches[0].z == pytest.approx(c.branches[1].z, abs=0.05)

    spec["sketch"]["jumps"] = [{"id": "far", "position": {"x": 100, "y": 350}, "size": "small", "bidirectional": True}]
    far = build_track_model(spec)
    assert far.facts["crossings"]["x"]["nearestJumpM"] > 200
    assert "flat-crossing-jump-clearance" not in violations(far)


# --- bridges -----------------------------------------------------------------------------------


def test_bridge_clearance_and_ramps(built: dict[str, Any]) -> None:
    model = built["figure8_bridge"].model
    [c] = model.crossings
    over, under = c.branches[c.over_branch], c.branches[1 - c.over_branch]
    assert over.z - under.z == pytest.approx(C.BRIDGE_DECK_HEIGHT_M, abs=1e-6)
    ramp = max(RULE_CONSTANTS["bridgeRampMinLengthM"], C.BRIDGE_DECK_HEIGHT_M / (C.BRIDGE_MAX_GRADE * 0.75))
    assert c.ramp_up_m == pytest.approx(ramp) and c.ramp_down_m == pytest.approx(ramp)
    main = model.route("main")
    s = np.array([p.s for p in main.samples])
    zz = z(main)
    grade = np.abs(np.diff(np.append(zz, zz[0]))) / pl.seg_lengths(xy(main), True)
    grade = grade[np.abs(s - over.s) <= c.deck_half_length_m + c.ramp_up_m]
    terrain = C.ELEVATION_LEVELS[model.elevation].max_grade
    assert grade.max() <= C.BRIDGE_MAX_GRADE + terrain + 1e-6
    # The under branch stays on the ground: no bump within the deck.
    near_under = np.abs(s - under.s) < c.deck_half_length_m
    assert zz[near_under].max() < over.z - C.BRIDGE_DECK_HEIGHT_M + 1.0


def test_reverse_layout_bridge_ramps_are_symmetric() -> None:
    ramp = el.bridge_ramp_length(RULE_CONSTANTS["bridgeRampMinLengthM"])
    forward = figure8_jump_after_bridge(30.0, reverse=False)
    assert "symmetricBridgeRamps" not in {f.flag for f in forward.option_result.flags}
    [c] = forward.model.crossings
    assert c.ramp_up_m == pytest.approx(ramp) and c.ramp_down_m == pytest.approx(30.0, abs=1.0)
    reverse = figure8_jump_after_bridge(30.0, reverse=True)
    assert "symmetricBridgeRamps" in {f.flag for f in reverse.option_result.flags}
    [c] = reverse.model.crossings
    assert c.ramp_up_m == c.ramp_down_m == pytest.approx(30.0, abs=1.0)
    main = reverse.model.route("main")
    s = np.array([p.s for p in main.samples])
    over = c.branches[c.over_branch]
    bump = el.bridge_bump(s - over.s, c.deck_half_length_m, c.ramp_up_m, c.ramp_down_m)
    near = np.abs(s - over.s) < c.deck_half_length_m + c.ramp_up_m
    assert np.allclose(bump[near], np.interp(2 * over.s - s[near], s, bump), atol=0.05)


def to_canvas(model: Any, x: float, y: float) -> dict[str, float]:
    t = model.canvas_transform
    return {"x": (x - t.offset_x) / t.scale, "y": -(y - t.offset_y) / t.scale}


def figure8_jump_after_bridge(gap: float, reverse: bool) -> Any:
    """The figure-8 sample with its bidirectional jump ``gap`` m after the bridge deck."""
    spec = sample("figure8_bridge")
    spec["options"]["reverseLayout"] = reverse
    base = build_track_model(spec).model
    [c] = base.crossings
    over = c.branches[c.over_branch]
    lo, _ = el.jump_extent("medium", True)
    s_jump = over.s + c.deck_half_length_m + gap - lo
    pts = xy(base.route("main"))
    p = pl.interp_at(pl.cumulative(pts, True), pts, True, s_jump)
    spec["sketch"]["jumps"][0]["position"] = to_canvas(base, *p)
    return build_track_model(spec)


@pytest.mark.parametrize("gap", [80.0, 30.0])
def test_bridge_ramp_room_is_reported(gap: float) -> None:
    result = figure8_jump_after_bridge(gap, reverse=False)
    [c2] = result.model.crossings
    available = result.facts["crossings"]["bridge1"]["availableRampLengthM"]
    assert available == pytest.approx(gap, abs=1.0)
    assert c2.ramp_down_m == pytest.approx(min(gap, el.bridge_ramp_length(50.0)), abs=1.0)
    assert ("bridge-ramp-length" in violations(result)) == (gap < RULE_CONSTANTS["bridgeRampMinLengthM"])


def test_bridge_carrying_an_alt_route_over_the_main_loop() -> None:
    spec = sample("loop_alt_jump")
    pts = [(300, 150), (380, 350), (470, 550), (560, 650), (800, 650), (850, 550), (850, 400)]
    spec["sketch"]["altRoutes"] = [
        {
            "id": "outer",
            "points": [{"x": x, "y": y} for x, y in pts],
            "start": {"point": {"x": 300, "y": 150}},
            "end": {"point": {"x": 850, "y": 400}},
        }
    ]
    spec["sketch"]["jumps"] = []
    spec["sketch"]["startFinish"]["position"] = {"x": 300, "y": 550}
    spec["sketch"]["crossings"] = [
        {"id": "b", "position": {"x": 470, "y": 550}, "type": "bridge", "over": {"route": "outer"}}
    ]
    result = build_track_model(spec)
    [c] = result.model.crossings
    assert c.detected and c.annotated
    over, under = c.branches[c.over_branch], c.branches[1 - c.over_branch]
    assert (over.route, under.route) == ("outer", "main")
    assert over.z - under.z == pytest.approx(C.BRIDGE_DECK_HEIGHT_M, abs=0.01)
    alt, main = result.model.route("outer"), result.model.route("main")
    assert alt.samples[0].z == main.samples[alt.start_join.sample_index].z
    assert alt.samples[-1].z == main.samples[alt.end_join.sample_index].z
    assert result.facts["altRoutes"]["outer"] == {
        "startGapM": pytest.approx(0, abs=0.5),
        "endGapM": pytest.approx(0, abs=0.5),
    }
    assert result.facts["crossings"]["b"]["availableRampLengthM"] >= RULE_CONSTANTS["bridgeRampMinLengthM"]
    assert result.valid, [v.message for v in result.rule_result.violations]


# --- alternative routes --------------------------------------------------------------------------


def test_alt_route_shares_exact_vertices_with_main(built: dict[str, Any]) -> None:
    model = built["loop_alt_jump"].model
    main, alt = model.route("main"), model.route("shortcut")
    assert alt.ai_index == -1 and not alt.closed
    for join, smp in ((alt.start_join, alt.samples[0]), (alt.end_join, alt.samples[-1])):
        m = main.samples[join.sample_index]
        assert (smp.x, smp.y, smp.z) == (m.x, m.y, m.z)
        assert join.s == m.s
    # Also byte-identical in the serialised model.
    data = json.loads(model.to_json())
    jmain = data["routes"][0]["samples"]
    jalt = data["routes"][1]["samples"]
    assert {k: jalt[0][k] for k in "xyz"} == {k: jmain[alt.start_join.sample_index][k] for k in "xyz"}
    assert {k: jalt[-1][k] for k in "xyz"} == {k: jmain[alt.end_join.sample_index][k] for k in "xyz"}
    assert data["aiLines"][1]["points"][0] == data["aiLines"][0]["points"][alt.start_join.sample_index]


def test_alt_route_elevation_is_continuous_at_joins() -> None:
    spec = sample("loop_alt_jump")
    spec["options"]["elevation"] = "hilly"
    model = build_track_model(spec).model
    alt = model.route("shortcut")
    zz = z(alt)
    dz = np.abs(np.diff(zz)) / pl.seg_lengths(xy(alt), False)
    assert dz[:5].max() < 0.2 and dz[-5:].max() < 0.2
    main = model.route("main")
    assert zz[0] == main.samples[alt.start_join.sample_index].z
    assert zz[-1] == main.samples[alt.end_join.sample_index].z


def test_alt_route_gaps_are_measured_from_the_raw_stroke() -> None:
    spec = sample("loop_alt_jump")
    alt = spec["sketch"]["altRoutes"][0]
    alt["points"][0] = {"x": 350, "y": 160}  # 10 canvas units below the top straight
    alt["points"][-1] = {"x": 650, "y": 151}
    result = build_track_model(spec)
    scale = result.model.canvas_transform.scale
    gaps = result.facts["altRoutes"]["shortcut"]
    assert gaps["startGapM"] == pytest.approx(10 * scale, rel=0.05)
    assert gaps["endGapM"] == pytest.approx(1 * scale, rel=0.2)
    assert "alt-route-joins-main-loop" in violations(result)


def test_alt_route_param_attachment() -> None:
    spec = sample("loop_alt_jump")
    alt = spec["sketch"]["altRoutes"][0]
    model = build_track_model(spec).model
    s_start = model.route("shortcut").start_join.s
    alt["start"] = {"param": s_start / model.lap_length_m}
    again = build_track_model(spec).model
    assert again.route("shortcut").start_join.s == pytest.approx(s_start, abs=C.JUNCTION_MERGE_M + 1e-9)


# --- jumps -----------------------------------------------------------------------------------


def test_jump_profiles_in_the_road(built: dict[str, Any]) -> None:
    model = built["loop_alt_jump"].model
    jumps = {j.id: j for j in model.jumps}
    kicker = jumps["kicker"]
    assert (kicker.route, kicker.size, kicker.bidirectional, kicker.in_base) == ("main", "medium", False, True)
    main = model.route("main")
    s = np.array([p.s for p in main.samples])
    zz = z(main)
    i = int(np.argmin(np.abs(s - kicker.s)))
    ground = zz[int(np.argmin(np.abs(s - (kicker.start_s - 5))))]
    assert zz[i] - ground == pytest.approx(C.JUMP_SIZES["medium"].height_m, abs=0.3)
    assert kicker.profile[0][0] <= kicker.start_s - kicker.s
    assert max(h for _, h in kicker.profile) == C.JUMP_SIZES["medium"].height_m
    assert jumps["hop"].route == "shortcut"


def test_reverse_layout_keeps_only_bidirectional_jumps_in_the_road() -> None:
    spec = sample("loop_alt_jump")
    spec["options"]["reverseLayout"] = True
    spec["options"]["elevation"] = "flat"
    spec["sketch"]["jumps"][1]["bidirectional"] = True
    result = build_track_model(spec)
    assert "reverse-jumps-bidirectional" in {v.rule_id for v in result.rule_result.violations}
    jumps = {j.id: j for j in result.model.jumps}
    assert (jumps["kicker"].in_base, jumps["kicker"].layouts) == (False, ["forward"])
    assert (jumps["hop"].in_base, jumps["hop"].layouts) == (True, ["forward", "reverse"])
    assert max(z(result.model.route("main"))) == 0.0  # the one-way kicker isn't in the shared road
    alt = result.model.route("shortcut")
    za = z(alt)
    sa = np.array([p.s for p in alt.samples])
    hop = jumps["hop"]
    d = sa - hop.s
    inside = np.abs(d) < (hop.end_s - hop.s) - 2
    # Symmetric about the marker: compare heights at mirrored offsets.
    mirrored = np.interp(hop.s - d[inside], sa, za)
    assert np.allclose(za[inside], mirrored, atol=0.05)


# --- grid, sectors, checkpoints ----------------------------------------------------------------


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_grid_slots_are_ahead_of_the_line(path: Path, built: dict[str, Any]) -> None:
    spec = load(path)
    model = built[path.stem].model
    grid = model.grid
    n = spec["options"]["gridSize"]
    rows = math.ceil(n / 2)
    spacing = RULE_CONSTANTS["gridRowSpacingM"]
    assert len(grid.slots) == n
    assert [s.name for s in grid.slots] == [f"#startpoint{i:02d}" for i in range(1, n + 1)]
    for row in range(rows):
        pair = [s for s in grid.slots if s.row == row]
        assert len(pair) == (2 if 2 * row + 1 < n else 1)
        assert pair[0].s == pytest.approx(C.GRID_LINE_OFFSET_M + (rows - 1 - row) * spacing)
        if len(pair) == 2:
            a, b = pair
            assert {a.side, b.side} == {"left", "right"}
            gap = math.hypot(a.position[0] - b.position[0], a.position[1] - b.position[1])
            slot_w = RULE_CONSTANTS["importedGridSlotWidthM" if spec["sketch"].get("imported") else "gridSlotWidthM"]
            assert gap == pytest.approx(slot_w, abs=1e-6)
    assert grid.car_change.name == "#startpointcarchange"
    # Every slot sits on the road surface, also across a cambered road (imported tracks).
    main = model.route("main")
    ss = np.array([q.s for q in main.samples])
    for slot in grid.slots:
        k = int(np.searchsorted(ss, slot.s, side="right") - 1)
        f = (slot.s - ss[k]) / (ss[k + 1] - ss[k])
        left = (1 - f) * np.array(main.left_edge[k]) + f * np.array(main.left_edge[k + 1])
        right = (1 - f) * np.array(main.right_edge[k]) + f * np.array(main.right_edge[k + 1])
        t = float(np.dot(np.array(slot.position[:2]) - right[:2], (left - right)[:2]) / np.sum((left - right)[:2] ** 2))
        assert slot.position[2] == pytest.approx(right[2] + t * (left[2] - right[2]), abs=0.01)
    first = built[path.stem].facts["firstSectorLengthM"]
    assert first >= rows * spacing
    # Every slot is on clear road: inside the first sector and off any jump or ramp.
    assert max(s.s for s in grid.slots) + C.PREVIEW_CAR_LENGTH_M / 2 <= first
    for j in model.jumps:
        if j.route == "main":
            assert all(not (j.start_s <= s.s <= j.end_s) for s in grid.slots)
    for c in model.crossings:
        if c.over_branch is not None and c.branches[c.over_branch].route == "main":
            b = c.branches[c.over_branch]
            lo, hi = b.s - c.deck_half_length_m - c.ramp_up_m, b.s + c.deck_half_length_m + c.ramp_down_m
            assert all(not (lo <= s.s <= hi) for s in grid.slots)


def test_jump_on_the_grid_fails_the_grid_rule() -> None:
    """Regression: loop_alt_jump with a grid of 24 and a large jump where the grid would be."""
    spec = sample("loop_alt_jump")
    spec["options"]["gridSize"] = 24
    spec["sketch"]["jumps"].append({"id": "big", "position": {"x": 440, "y": 550}, "size": "large"})
    result = build_track_model(spec)
    big = next(j for j in result.model.jumps if j.id == "big")
    assert 0 < big.start_s < 96
    assert result.facts["firstSectorLengthM"] == pytest.approx(big.start_s, abs=1e-3)
    assert "grid-first-sector-length" in violations(result)
    assert not result.valid

    clear = sample("loop_alt_jump")
    clear["options"]["gridSize"] = 24
    ok = build_track_model(clear)
    assert ok.facts["firstSectorLengthM"] >= 96 and ok.valid
    assert max(s.s for s in ok.model.grid.slots) < ok.facts["firstSectorLengthM"]


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_checkpoints(path: Path, built: dict[str, Any]) -> None:
    model = built[path.stem].model
    main_cps = [c for c in model.checkpoints if c.route == "main"]
    assert C.CHECKPOINT_MIN <= len(main_cps) <= C.CHECKPOINT_MAX
    assert main_cps[-1].is_finish and main_cps[-1].s == 0.0
    assert not any(c.is_finish for c in main_cps[:-1])
    rows = math.ceil(model.grid.size / 2)
    pole = C.GRID_LINE_OFFSET_M + (rows - 1) * RULE_CONSTANTS["gridRowSpacingM"]
    assert main_cps[0].s == pytest.approx(pole + C.GRID_CHECKPOINT_MARGIN_M)
    assert main_cps[0].s > max(s.s for s in model.grid.slots)
    s = [c.s for c in main_cps[:-1]]
    assert s == sorted(s) and s[-1] < model.lap_length_m
    for c in model.checkpoints:  # the road's width there (real tracks: measured per point) plus the margins
        r = model.route(c.route)
        ss = np.array([q.s for q in r.samples])
        ww = np.array([q.width for q in r.samples])
        road = float(np.interp(c.s, ss, ww, period=r.length_m if r.closed else None))
        assert c.width_m == pytest.approx(road + 2 * C.CHECKPOINT_MARGIN_M, abs=0.05)
    for r in model.routes[1:]:
        assert any(c.route == r.id for c in model.checkpoints)


def test_short_first_sector_fails_the_grid_rule() -> None:
    spec = square_spec(12, 0.8)
    spec["sketch"]["startFinish"] = {"position": {"x": 880, "y": 100}, "headingDeg": 0}
    spec["options"]["gridSize"] = 24
    result = build_track_model(spec)
    assert result.facts["firstSectorLengthM"] < 96
    assert "grid-first-sector-length" in violations(result)


# --- imported (real-world) tracks --------------------------------------------------------------


def imported_vector(name: str = "imported-valid") -> dict[str, Any]:
    return load(TESTS.parents[1] / "schema" / "test-vectors" / f"{name}.json")["spec"]


def test_imported_centreline_is_used_exactly_with_its_camber() -> None:
    spec = imported_vector()
    pts = spec["sketch"]["imported"]["centreline"]
    result = build_track_model(spec)
    assert result.valid, [v.message for v in result.rule_result.violations]
    assert result.warnings == []
    main = result.model.route("main")
    assert len(main.samples) == len(pts)
    assert result.model.lap_length_m == pytest.approx(pl.length(np.array([[p["x"], p["y"]] for p in pts]), True))
    hw = result.model.width_m / 2
    for q, p, left, right in zip(main.samples, pts, main.left_edge, main.right_edge, strict=True):
        assert (q.x, q.y, q.z) == pytest.approx((p["x"], p["y"], p["z"]))
        assert q.banking == pytest.approx(math.radians(p["bankDeg"]))
        assert left[2] - q.z == pytest.approx(hw * math.tan(q.banking))
        assert right[2] - q.z == pytest.approx(-hw * math.tan(q.banking))
    assert main.samples[0].s == 0.0
    f = result.facts
    assert f["maxBankDeg"] == pytest.approx(max(abs(p["bankDeg"]) for p in pts), abs=1e-3)
    z = np.array([p["z"] for p in pts])
    assert f["heightRangeM"] == pytest.approx(z.max() - z.min(), abs=1e-3)
    assert 0 < f["maxGradePct"] < 10
    # canvas units are the import's metres with the canvas centre at the origin
    t = result.model.canvas_transform
    assert (t.scale, t.offset_x, t.offset_y) == (1.0, -spec["sketch"]["canvas"]["width"] / 2, spec["sketch"]["canvas"]["height"] / 2)


def test_imported_track_ignores_drawn_elements() -> None:
    result = build_track_model(imported_vector("imported-with-drawn-routes"))
    assert [w.code for w in result.warnings] == ["imported-ignores-drawn"]
    assert result.model.elevation_markers == []
    assert "imported-no-drawn-routes" in violations(result)


def test_imported_alt_layout_shares_the_base_as_a_branch() -> None:
    """An imported alternative layout is an open branch route snapped to the main centreline; the model lists
    every layout with its lap (main up to the branch, the branch, main after it)."""
    spec = imported_vector("imported-alt-layouts-valid")
    result = build_track_model(spec)
    assert result.valid, [v.message for v in result.rule_result.violations]
    m = result.model
    main, br = m.route("main"), m.route("short")
    assert (br.ai_index, br.closed) == (-1, False)
    assert br.start_join.route == "main" and br.start_join.s < br.end_join.s
    first, last = br.samples[0], br.samples[-1]
    for q, j in ((first, br.start_join), (last, br.end_join)):
        mq = main.samples[j.sample_index]
        assert (q.x, q.y, q.z) == pytest.approx((mq.x, mq.y, mq.z))
    assert [lay.id for lay in m.layouts] == ["main", "short"]
    assert (m.layouts[0].name, m.layouts[0].lap_length_m) == ("Long", m.lap_length_m)
    short = m.layouts[1]
    assert short.lap_length_m == pytest.approx(br.start_join.s + br.length_m + (m.lap_length_m - br.end_join.s))
    assert [s["route"] for s in short.sections] == ["main", "short", "main"]
    assert result.facts["altRoutes"]["short"] == {"startGapM": 0.0, "endGapM": 0.0}
    # drawn and single-layout imported tracks keep the model format without "layouts"
    assert "layouts" not in build_track_model(imported_vector()).model.to_dict()
    assert short.grid is None and short.start_finish is None  # shares the main line and grid
    assert "grid" not in m.to_dict()["layouts"][1]
    # a branch that would bypass the start/finish line is left out...
    alt = spec["sketch"]["imported"]["altLayouts"][0]
    alt["centreline"] = alt["centreline"][::-1]
    bad = build_track_model(spec)
    assert "imported-layout-unsupported" in [w.code for w in bad.warnings] and bad.model.layouts == []
    # ...unless the layout has its own start/finish line (Cadwell Park's Club, Anglesey's National): then its lap
    # (main from the branch end round to the branch start, which may pass main s = 0 or not) starts there and
    # it gets its own grid on that lap
    s0, s1 = br.start_join.s, br.end_join.s
    mid = next(q for q in main.samples if q.s >= s0 + 15.0)  # room for the grid before the branch
    alt["startFinish"] = {"x": mid.x, "y": mid.y}
    own = build_track_model(spec)
    assert own.valid, [v.message for v in own.rule_result.violations]
    lay = own.model.layouts[1]
    assert [s["route"] for s in lay.sections] == ["main", "short", "main"]
    assert lay.sections[0]["fromS"] == pytest.approx(mid.s, abs=0.5) == lay.sections[-1]["toS"]
    assert lay.lap_length_m == pytest.approx((s1 - s0) + own.model.route("short").length_m, abs=0.5)
    assert lay.start_finish["route"] == "main" and lay.start_finish["s"] == pytest.approx(mid.s, abs=0.5)
    assert len(lay.grid.slots) == spec["options"]["gridSize"]
    pole = max(lay.grid.slots, key=lambda q: q.s)
    assert 0 < pole.s < s1 - mid.s  # ahead of the line, before the branch
    assert own.model.to_dict()["layouts"][1]["grid"]["size"] == spec["options"]["gridSize"]
    # the line may lie on the branch too
    bq = own.model.route("short").samples[len(own.model.route("short").samples) // 2]
    alt["startFinish"] = {"x": bq.x, "y": bq.y}
    on_branch = build_track_model(spec).model.layouts[1]
    assert on_branch.sections[0]["route"] == "short" and on_branch.start_finish["route"] == "short"


def test_imported_extra_roads_are_built_and_chained_into_the_layouts_that_list_them() -> None:
    """imported.roads: real tarmac besides the layouts' own branches (Oulton Park's old straight past Hislop's).
    Every road is a route (built road, no layout drives an unlisted one); a layout listing roads drives its branch,
    the roads and the main route between them in driving order, and a road may leave the main route a few metres
    before the previous piece rejoins it (one junction): that piece is cut short there."""
    spec = imported_vector("imported-alt-layouts-valid")
    imp = spec["sketch"]["imported"]
    main_pts = imp["centreline"]

    def chord(a: int, b: int, n: int = 13) -> list[dict[str, float]]:
        p, q = main_pts[a], main_pts[b]
        return [{"x": p["x"] + (q["x"] - p["x"]) * t, "y": p["y"] + (q["y"] - p["y"]) * t,
                 "z": p["z"] + (q["z"] - p["z"]) * t, "bankDeg": 0.0} for t in np.linspace(0.0, 1.0, n)]

    imp["roads"] = [{"id": "far", "name": "Far chord", "centreline": chord(72, 88)},
                    {"id": "spare", "centreline": chord(100, 112)}]
    imp["altLayouts"][0]["roads"] = ["far"]
    result = build_track_model(spec)
    assert result.valid, [v.message for v in result.rule_result.violations]
    m = result.model
    assert [r.id for r in m.routes] == ["main", "short", "far", "spare"]
    assert [lay.id for lay in m.layouts] == ["main", "short"]
    lay = m.layouts[1]
    br, far = m.route("short"), m.route("far")
    assert [s["route"] for s in lay.sections] == ["main", "short", "main", "far", "main"]
    assert lay.lap_length_m == pytest.approx(
        m.lap_length_m - (far.end_join.s - far.start_join.s) - (br.end_join.s - br.start_join.s)
        + br.length_m + far.length_m, abs=1e-6)
    assert all(s["route"] != "spare" for x in m.layouts for s in x.sections)  # built, never driven
    # the next piece leaving 4 samples before the branch rejoins: the branch is cut short, no main section
    k_end = m.route("short").end_join.sample_index
    imp["roads"][0]["centreline"] = chord(k_end - 2, 88)
    m2 = build_track_model(spec).model
    lay = m2.layouts[1]
    assert [s["route"] for s in lay.sections] == ["main", "short", "far", "main"]
    assert lay.sections[1]["toS"] < m.route("short").length_m - 5.0

    def at(rid: str, s: float) -> np.ndarray:
        q = min(m2.route(rid).samples, key=lambda p: abs(p.s - s))
        return np.array([q.x, q.y])

    # the hand-over is where the two lines come closest (here no nearer than the two joins on the main line)
    hand = float(np.hypot(*(at("short", lay.sections[1]["toS"]) - at("far", lay.sections[2]["fromS"]))))
    joins = float(np.hypot(main_pts[k_end]["x"] - main_pts[k_end - 2]["x"], main_pts[k_end]["y"] - main_pts[k_end - 2]["y"]))
    assert hand <= joins + 0.5
    # an unknown road, or pieces that can't chain round one lap, leave the layout out
    imp["altLayouts"][0]["roads"] = ["nowhere"]
    bad = build_track_model(spec)
    assert "imported-layout-unsupported" in [w.code for w in bad.warnings] and bad.model.layouts == []
    imp["altLayouts"][0]["roads"] = ["far"]
    imp["roads"][0]["centreline"] = chord(88, 72)  # driven backwards against the lap
    bad = build_track_model(spec)
    assert "imported-layout-unsupported" in [w.code for w in bad.warnings] and bad.model.layouts == []


def test_steep_imported_road_fails_the_imported_grade_rule() -> None:
    spec = imported_vector()
    for k, p in enumerate(spec["sketch"]["imported"]["centreline"]):
        p["z"] = 8.0 if 10 <= k < 20 else 0.0  # an 8 m step over one 7 m span
    result = build_track_model(spec)
    assert result.facts["maxGradePct"] > 25
    assert "imported-max-grade" in violations(result)


# --- facts, determinism, vectors ---------------------------------------------------------------


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_facts_match_the_schema(path: Path, built: dict[str, Any]) -> None:
    facts = built[path.stem].facts
    assert GeometryFacts.from_dict(facts).to_dict() == facts
    assert rules.evaluate(load(path), facts).schema_valid


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_output_is_deterministic(path: Path, built: dict[str, Any]) -> None:
    again = build_track_model(load(path))
    first = built[path.stem]
    assert again.model.to_json() == first.model.to_json()
    assert again.preview_svg == first.preview_svg
    assert again.elevation_svg == first.elevation_svg
    assert json.dumps(again.facts) == json.dumps(first.facts)


def test_model_json_shape(built: dict[str, Any]) -> None:
    data = json.loads(built["loop_alt_jump"].model.to_json())
    assert data["formatVersion"] == C.MODEL_FORMAT_VERSION
    assert data["coordinateSystem"]["up"] == "+z"
    assert set(data["routes"][0]["samples"][0]) == {"x", "y", "z", "s", "heading", "banking", "width"}
    assert data["routes"][0]["samples"][0]["banking"] == 0
    assert {"routes", "crossings", "jumps", "grid", "checkpoints", "aiLines", "bounds", "reverse"} <= set(data)


def test_schema_invalid_spec_gives_no_model() -> None:
    spec = sample("oval")
    spec["options"]["gridSize"] = 99
    result = build_track_model(spec)
    assert result.model is None and not result.rule_result.schema_valid


@pytest.mark.parametrize("path", VECTORS, ids=[p.stem for p in VECTORS])
def test_vectors_build_with_non_deferred_geometry_rules(path: Path) -> None:
    vector = load(path)
    if not vector["expect"]["schemaValid"]:
        pytest.skip("schema-invalid vector")
    result = build_track_model(vector["spec"])
    if "mainLoop" not in vector["spec"]["sketch"] and "imported" not in vector["spec"]["sketch"]:
        assert result.model is None
        return
    assert result.model is not None
    assert result.rule_result.schema_valid, result.rule_result.schema_errors
    assert GEOMETRY_RULES.isdisjoint(result.rule_result.deferred)
    for crossing in vector["spec"]["sketch"].get("crossings", []):
        cid = crossing["id"]
        assert (cid in result.facts["crossings"]) != (cid in result.facts["unmatchedCrossingAnnotations"])
    for alt in vector["spec"]["sketch"].get("altRoutes", []):
        assert alt["id"] in result.facts["altRoutes"]


# --- size guards -------------------------------------------------------------------------------


def scaled_main_loop(factor: float) -> dict[str, Any]:
    """The loop_alt_jump sample with only the main loop (and start/finish) shrunk by ``factor``."""
    spec = sample("loop_alt_jump")
    for p in spec["sketch"]["mainLoop"]["points"] + [spec["sketch"]["startFinish"]["position"]]:
        p["x"] /= factor
        p["y"] /= factor
    return spec


@pytest.mark.parametrize("factor", [1e3, 1e4])
def test_shrunk_main_loop_is_rejected_quickly(factor: float) -> None:
    start = time.perf_counter()
    result = build_track_model(scaled_main_loop(factor))
    assert time.perf_counter() - start < 2.0
    assert result.model is None and not result.valid
    assert [e.code for e in result.errors] == ["main-loop-too-small"]


def test_tiny_main_loop_is_rejected_quickly() -> None:
    spec = sample("oval")
    spec["sketch"]["mainLoop"]["points"] = [{"x": x * 1e-7, "y": y * 1e-7} for x, y in [(0, 0), (1, 0), (1, 1)]]
    del spec["sketch"]["startFinish"]
    start = time.perf_counter()
    result = build_track_model(spec)
    assert time.perf_counter() - start < 2.0
    assert [e.code for e in result.errors] == ["main-loop-too-small"]
    assert result.model is None and result.facts is None and not result.valid


def test_tiny_canvas_with_a_matching_loop_still_builds() -> None:
    spec = sample("oval")
    k = 1e-6
    spec["sketch"]["canvas"] = {"width": 1000 * k, "height": 600 * k}
    for p in spec["sketch"]["mainLoop"]["points"] + [spec["sketch"]["startFinish"]["position"]]:
        p["x"] *= k
        p["y"] *= k
    result = build_track_model(spec)
    assert result.valid, (result.errors, [v.message for v in result.rule_result.violations])
    assert result.model.lap_length_m == pytest.approx(1200.0)


def test_main_loop_with_too_few_distinct_points_is_rejected() -> None:
    spec = sample("oval")
    spec["sketch"]["mainLoop"]["points"] = [{"x": 0, "y": 0}, {"x": 500, "y": 0}, {"x": 0, "y": 0}]
    del spec["sketch"]["startFinish"]
    result = build_track_model(spec)
    assert [e.code for e in result.errors] == ["main-loop-degenerate"]


@pytest.mark.parametrize(
    ("factor", "code"), [(40.0, "alt-route-too-long"), (2.5, "alt-route-out-of-bounds")], ids=["long", "far"]
)
def test_oversized_alt_route_is_rejected_quickly(factor: float, code: str) -> None:
    spec = sample("loop_alt_jump")
    if code == "alt-route-too-long":
        spec["sketch"]["altRoutes"][0]["points"] = (
            [{"x": 350, "y": 150}]
            + [{"x": 350 + 600 * (i % 2), "y": 150 + 2 * i} for i in range(1, int(factor) * 4)]
            + [{"x": 650, "y": 150}]
        )
    else:
        spec["sketch"]["altRoutes"][0]["points"][2] = {"x": 500, "y": -2130}  # ~2.3 km out, ~4.2 km long
    start = time.perf_counter()
    result = build_track_model(spec)
    assert time.perf_counter() - start < 2.0
    assert [e.code for e in result.errors] == [code]
    assert result.model is None and not result.valid


def test_huge_stroke_point_counts_are_rejected_cheaply() -> None:
    spec = sample("oval")
    t = np.linspace(0, 2 * math.pi, 200_000, endpoint=False)
    circle = [{"x": float(500 + 300 * math.cos(a)), "y": float(300 + 250 * math.sin(a))} for a in t]
    spec["sketch"]["mainLoop"]["points"] = circle
    start = time.perf_counter()
    result = build_track_model(spec)
    assert time.perf_counter() - start < 0.5
    assert result.model is None and not result.rule_result.schema_valid
    assert [e.path for e in result.rule_result.schema_errors] == ["/sketch/mainLoop/points"]

    spec["sketch"]["mainLoop"]["points"] = circle[::20]  # 10,000 points: the maximum
    start = time.perf_counter()
    result = build_track_model(spec)
    assert time.perf_counter() - start < 5.0
    assert result.model is not None
