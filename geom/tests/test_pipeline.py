"""Integration tests for build_track_model: TrackModel, geometry facts, rules and previews."""

from __future__ import annotations

import copy
import json
import math
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


def test_unannotated_crossing_needs_annotation() -> None:
    spec = sample("figure8_bridge")
    spec["sketch"]["crossings"] = []
    result = build_track_model(spec)
    [c] = result.model.crossings
    assert (c.type, c.annotated, c.id) == ("unannotated", False, "auto1")
    assert "crossing-needs-annotation" in codes(result)
    assert result.facts["crossings"] == {}
    assert max(z(result.model.route("main"))) < C.BRIDGE_DECK_HEIGHT_M  # no bridge built


def test_annotation_without_crossing_is_reported_but_measured() -> None:
    spec = sample("oval")
    spec["sketch"]["crossings"] = [{"id": "x", "position": {"x": 500, "y": 150}, "type": "flat"}]
    result = build_track_model(spec)
    assert "crossing-annotation-unmatched" in codes(result)
    assert result.facts["crossings"]["x"]["nearestJumpM"] == pytest.approx(1200.0)
    assert "flat-crossing-jump-clearance" not in result.rule_result.deferred
    [c] = result.model.crossings
    assert c.detected is False


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
def test_grid_slots(path: Path, built: dict[str, Any]) -> None:
    spec = load(path)
    model = built[path.stem].model
    grid = model.grid
    n = spec["options"]["gridSize"]
    assert len(grid.slots) == n
    assert [s.name for s in grid.slots] == [f"#startpoint{i:02d}" for i in range(1, n + 1)]
    lap = model.lap_length_m
    for row in range(math.ceil(n / 2)):
        pair = [s for s in grid.slots if s.row == row]
        assert len(pair) == (2 if 2 * row + 1 < n else 1)
        behind = lap - pair[0].s
        assert behind == pytest.approx(C.GRID_FRONT_OFFSET_M + row * RULE_CONSTANTS["gridRowSpacingM"])
        if len(pair) == 2:
            a, b = pair
            assert {a.side, b.side} == {"left", "right"}
            gap = math.hypot(a.position[0] - b.position[0], a.position[1] - b.position[1])
            assert gap == pytest.approx(RULE_CONSTANTS["gridSlotWidthM"], abs=1e-6)
    assert grid.car_change.name == "#startpointcarchange"
    first = built[path.stem].facts["firstSectorLengthM"]
    assert first >= math.ceil(n / 2) * RULE_CONSTANTS["gridRowSpacingM"]


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_checkpoints(path: Path, built: dict[str, Any]) -> None:
    model = built[path.stem].model
    main_cps = [c for c in model.checkpoints if c.route == "main"]
    assert C.CHECKPOINT_MIN <= len(main_cps) <= C.CHECKPOINT_MAX
    assert main_cps[-1].is_finish and main_cps[-1].s == 0.0
    assert not any(c.is_finish for c in main_cps[:-1])
    grid_len = math.ceil(model.grid.size / 2) * RULE_CONSTANTS["gridRowSpacingM"]
    assert main_cps[0].s >= min(grid_len, model.first_sector_length_m) - 1e-9
    s = [c.s for c in main_cps[:-1]]
    assert s == sorted(s) and s[-1] < model.lap_length_m
    for c in model.checkpoints:
        assert c.width_m == model.width_m + 2 * C.CHECKPOINT_MARGIN_M
    for r in model.routes[1:]:
        assert any(c.route == r.id for c in model.checkpoints)


def test_short_first_sector_fails_the_grid_rule() -> None:
    spec = square_spec(12, 0.8)
    spec["sketch"]["startFinish"] = {"position": {"x": 880, "y": 100}, "headingDeg": 0}
    spec["options"]["gridSize"] = 24
    result = build_track_model(spec)
    assert result.facts["firstSectorLengthM"] < 96
    assert "grid-first-sector-length" in violations(result)


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
    if "mainLoop" not in vector["spec"]["sketch"]:
        assert result.model is None
        return
    assert result.model is not None
    assert result.rule_result.schema_valid, result.rule_result.schema_errors
    assert GEOMETRY_RULES.isdisjoint(result.rule_result.deferred)
    for crossing in vector["spec"]["sketch"].get("crossings", []):
        assert crossing["id"] in result.facts["crossings"]
    for alt in vector["spec"]["sketch"].get("altRoutes", []):
        assert alt["id"] in result.facts["altRoutes"]
