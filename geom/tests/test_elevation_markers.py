"""Height markers: the marker profile (PCHIP), custom elevation, preset bumps, joins, bridges and rules."""

from __future__ import annotations

import copy
import itertools
import json
import math
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from trackgen_geom import build_track_model, rules
from trackgen_geom import constants as C
from trackgen_geom import elevation as el
from trackgen_geom.model import Route, TrackModel

TESTS = Path(__file__).resolve().parent
RULE_CONSTANTS = rules.load_rules()["constants"]


def sample(name: str) -> dict[str, Any]:
    return json.loads((TESTS / "samples" / f"{name}.json").read_text(encoding="utf-8"))


def violations(result: Any) -> set[str]:
    return {v.rule_id for v in result.rule_result.violations}


def z_at(route: Route, s: float) -> float:
    ss = np.array(
        [p.s for p in route.samples] + ([route.length_m] if route.closed else [])
    )
    zz = np.array(
        [p.z for p in route.samples] + ([route.samples[0].z] if route.closed else [])
    )
    return float(np.interp(s % route.length_m if route.closed else s, ss, zz))


def canvas_at(model: TrackModel, route_id: str, s: float) -> dict[str, float]:
    """Canvas coordinates of the point at arc length ``s`` on a built route."""
    route = model.route(route_id)
    ss = np.array([p.s for p in route.samples])
    i = int(np.argmin(np.abs(ss - s)))
    p = route.samples[i]
    t = model.canvas_transform
    return {
        "x": round((p.x - t.offset_x) / t.scale, 2),
        "y": round(-(p.y - t.offset_y) / t.scale, 2),
    }


def with_markers(
    spec: dict[str, Any], markers: list[dict[str, Any]], elevation: str = "custom"
) -> dict[str, Any]:
    out = copy.deepcopy(spec)
    out["options"]["elevation"] = elevation
    out["sketch"]["elevationMarkers"] = markers
    return out


# --- the marker profile ------------------------------------------------------------------------


def test_profile_passes_through_controls() -> None:
    ctrl = [
        el.Control(10.0, 0.0, "a"),
        el.Control(120.0, 8.0, "b"),
        el.Control(300.0, -2.0, "c"),
    ]
    for closed in (True, False):
        got = el.marker_profile(np.array([c.s for c in ctrl]), ctrl, closed, 500.0)
        assert np.allclose(got, [c.h for c in ctrl], atol=1e-9)


@pytest.mark.parametrize("closed", [False, True])
def test_profile_is_monotone_between_controls_without_overshoot(closed: bool) -> None:
    rng = random.Random(7)
    length = 2000.0
    for _ in range(30):
        n = rng.randint(2, 9)
        xs = sorted(rng.sample(range(0, int(length) - 50, 7), n))
        ctrl = [
            el.Control(float(x), float(rng.randint(-30, 40)), f"m{i}")
            for i, x in enumerate(xs)
        ]
        s = np.linspace(0.0, length, 8001)
        z = el.marker_profile(s, ctrl, closed, length)
        pts = ctrl + ([el.Control(ctrl[0].s + length, ctrl[0].h)] if closed else [])
        for a, b in itertools.pairwise(pts):
            seg = (
                z[(s >= a.s) & (s <= b.s)]
                if b.s <= length
                else np.concatenate([z[s >= a.s], z[s <= b.s - length]])
            )
            lo, hi = min(a.h, b.h), max(a.h, b.h)
            assert seg.min() >= lo - 1e-9 and seg.max() <= hi + 1e-9
            d = np.diff(seg)
            assert (d >= -1e-9).all() or (d <= 1e-9).all()


def test_closed_profile_closes_with_continuous_height_and_grade() -> None:
    ctrl = [
        el.Control(100.0, 5.0, "a"),
        el.Control(700.0, -4.0, "b"),
        el.Control(1300.0, 12.0, "c"),
    ]
    length, eps = 1500.0, 1e-4
    z = el.marker_profile(
        np.array([length - eps, 0.0, eps, 2 * eps, length - 2 * eps]),
        ctrl,
        True,
        length,
    )
    assert abs(z[0] - z[1]) < 1e-3
    slope_before = (z[0] - z[4]) / eps
    slope_after = (z[3] - z[2]) / eps
    assert abs(slope_before - slope_after) < 1e-3


def test_single_marker_and_no_markers() -> None:
    s = np.linspace(0, 1000, 11)
    assert np.all(el.marker_profile(s, [], True, 1000.0) == 0.0)
    assert np.allclose(
        el.marker_profile(s, [el.Control(300.0, 7.0, "a")], True, 1000.0), 7.0
    )


def test_preset_bump_gets_zero_anchors_and_a_gentle_grade() -> None:
    ctrl = el.marker_controls([("a", 1000.0, 20.0)], True, 3000.0, additive=True)
    radius = el.bump_radius(20.0)
    assert [(c.s, c.h) for c in ctrl] == [
        (1000.0 - radius, 0.0),
        (1000.0, 20.0),
        (1000.0 + radius, 0.0),
    ]
    grades = el.control_grades(ctrl, True, 3000.0)
    assert grades["a"] == pytest.approx(C.MARKER_BUMP_GRADE)


def test_control_grades_use_the_steeper_neighbour() -> None:
    ctrl = el.marker_controls(
        [("a", 0.0, 0.0), ("b", 100.0, 10.0), ("c", 400.0, 13.0)],
        False,
        500.0,
        False,
        (0.0, 0.0),
    )
    grades = el.control_grades(ctrl, False, 500.0)
    assert grades["b"] == pytest.approx(0.1)
    assert grades["c"] == pytest.approx(0.13)


# --- custom elevation in the pipeline ------------------------------------------------------------


@pytest.fixture(scope="module")
def hill() -> Any:
    return build_track_model(sample("hill_markers"))


def test_hill_markers_sample_is_valid(hill: Any) -> None:
    assert hill.valid, [v.message for v in hill.rule_result.violations]
    assert hill.rule_result.deferred == []
    assert set(hill.facts["elevationMarkers"]) == {f"h{i}" for i in range(1, 8)}
    assert hill.facts["heightRangeM"] == pytest.approx(17.0, abs=0.1)


def test_custom_profile_hits_each_marker_height(hill: Any) -> None:
    main = hill.model.route("main")
    for m in hill.model.elevation_markers:
        assert abs(z_at(main, m.s) - m.height_m) < 0.1, m.id
        assert abs(m.position[2] - m.height_m) < 0.1


def test_custom_main_loop_closes_with_continuity(hill: Any) -> None:
    z = np.array([s.z for s in hill.model.route("main").samples])
    steps = np.abs(np.diff(np.concatenate([z, z[:1]])))
    assert steps[-1] <= steps.max() + 1e-9 and steps[-1] < 0.5
    grades = np.diff(np.concatenate([z[-2:], z, z[:2]]))
    assert np.abs(np.diff(grades)).max() < 0.05  # no kink anywhere, including the seam


def test_custom_without_markers_is_flat() -> None:
    spec = sample("hill_markers")
    spec["sketch"].pop("elevationMarkers")
    result = build_track_model(spec)
    assert result.valid
    assert all(s.z == 0.0 for r in result.model.routes for s in r.samples)
    assert result.facts["heightRangeM"] == 0.0


def test_markers_are_deterministic() -> None:
    a = build_track_model(sample("hill_markers"))
    b = build_track_model(sample("hill_markers"))
    assert a.model.to_json() == b.model.to_json()
    assert a.preview_svg == b.preview_svg and a.elevation_svg == b.elevation_svg


def test_previews_show_the_markers(hill: Any) -> None:
    assert hill.preview_svg.count('<circle class="height-marker"') == 7
    assert hill.elevation_svg.count('<circle class="height-marker"') == 7
    assert ">+14</text>" in hill.preview_svg and ">-3</text>" in hill.elevation_svg


# --- presets: markers add bumps -----------------------------------------------------------------


def test_preset_marker_adds_a_bump_on_the_terrain() -> None:
    base = sample("hilly_circuit")
    plain = build_track_model(base)
    s = 1200.0
    pos = canvas_at(plain.model, "main", s)
    bumped = build_track_model(
        with_markers(base, [{"id": "b", "position": pos, "heightM": 10}], "gentle")
    )
    flat_ref = build_track_model(with_markers(base, [], "gentle"))
    m = bumped.model.elevation_markers[0]
    main_b, main_0 = bumped.model.route("main"), flat_ref.model.route("main")
    assert z_at(main_b, m.s) - z_at(main_0, m.s) == pytest.approx(10.0, abs=0.1)
    far = m.s + el.bump_radius(10.0) + 50.0
    assert z_at(main_b, far) == pytest.approx(z_at(main_0, far), abs=1e-6)
    assert bumped.valid


# --- alternative routes ------------------------------------------------------------------------


def test_alt_route_joins_stay_identical_with_markers() -> None:
    base = sample("loop_alt_jump")
    base["sketch"]["jumps"] = []
    plain = build_track_model(base)
    alt = plain.model.routes[1]
    markers = [
        {
            "id": "m1",
            "position": canvas_at(plain.model, "main", 0.3 * plain.model.lap_length_m),
            "heightM": 12,
        },
        {
            "id": "m2",
            "position": canvas_at(plain.model, "main", 0.75 * plain.model.lap_length_m),
            "heightM": -5,
        },
        {
            "id": "a1",
            "position": canvas_at(plain.model, alt.id, alt.length_m / 2),
            "route": alt.id,
            "heightM": 9,
        },
    ]
    for elevation in ("custom", "gentle"):
        result = build_track_model(with_markers(base, markers, elevation))
        assert result.model is not None
        main = result.model.route("main")
        for r in result.model.routes[1:]:
            for join, sample_ in (
                (r.start_join, r.samples[0]),
                (r.end_join, r.samples[-1]),
            ):
                shared = main.samples[join.sample_index]
                assert (sample_.x, sample_.y, sample_.z) == (
                    shared.x,
                    shared.y,
                    shared.z,
                )
        a1 = next(m for m in result.model.elevation_markers if m.id == "a1")
        if elevation == "custom":
            assert abs(z_at(result.model.route(alt.id), a1.s) - 9.0) < 0.1


# --- bridges and flat crossings ------------------------------------------------------------------


def _figure8_markers(
    model: TrackModel, over_h: float, under_h: float
) -> list[dict[str, Any]]:
    c = next(c for c in model.crossings if c.type == "bridge")
    over, under = c.branches[c.over_branch], c.branches[1 - c.over_branch]
    out = []
    for name, b, h in (("o", over, over_h), ("u", under, under_h)):
        for k, ds in enumerate((-130.0, 130.0)):
            out.append(
                {
                    "id": f"{name}{k}",
                    "position": canvas_at(model, b.route, b.s + ds),
                    "heightM": h,
                }
            )
    return out


def test_bridge_clearance_holds_with_markers() -> None:
    base = sample("figure8_bridge")
    plain = build_track_model(base)
    result = build_track_model(
        with_markers(base, _figure8_markers(plain.model, -2.0, 6.0))
    )
    assert result.model is not None
    c = next(c for c in result.model.crossings if c.type == "bridge")
    over, under = c.branches[c.over_branch], c.branches[1 - c.over_branch]
    assert over.z - under.z >= C.BRIDGE_DECK_HEIGHT_M - 0.05
    assert c.deck_height_m > C.BRIDGE_DECK_HEIGHT_M
    plain_grade = plain.facts["crossings"][c.id]["rampGradePct"]
    assert result.facts["crossings"][c.id]["rampGradePct"] > plain_grade


def test_bridge_too_steep_fires_the_ramp_grade_rule() -> None:
    base = sample("figure8_bridge")
    plain = build_track_model(base)
    result = build_track_model(
        with_markers(base, _figure8_markers(plain.model, -25.0, 30.0))
    )
    c = next(c for c in result.model.crossings if c.type == "bridge")
    assert (
        result.facts["crossings"][c.id]["rampGradePct"]
        > RULE_CONSTANTS["bridgeRampMaxGradePct"]
    )
    assert "elevation-bridge-ramp-grade" in violations(result)


def test_flat_crossing_branches_meet_and_a_step_is_reported() -> None:
    base = sample("figure8_bridge")
    plain = build_track_model(base)
    markers = _figure8_markers(plain.model, 0.0, 5.0)
    spec = with_markers(base, markers)
    spec["sketch"]["jumps"] = []
    for c in spec["sketch"]["crossings"]:
        c["type"] = "flat"
        c.pop("over", None)
    result = build_track_model(spec)
    c = next(c for c in result.model.crossings if c.type == "flat")
    assert c.branches[0].z == pytest.approx(c.branches[1].z, abs=0.05)
    assert result.facts["crossings"][c.id]["heightStepM"] == pytest.approx(5.0, abs=0.3)
    assert "elevation-flat-crossing-level" in violations(result)


# --- rules from the facts -----------------------------------------------------------------------


def test_grade_violation_is_detected_not_fixed() -> None:
    base = sample("oval")
    plain = build_track_model(base)
    s = 0.5 * plain.model.lap_length_m
    markers = [
        {"id": "low", "position": canvas_at(plain.model, "main", s), "heightM": -10},
        {
            "id": "high",
            "position": canvas_at(plain.model, "main", s + 80.0),
            "heightM": 20,
        },
    ]
    result = build_track_model(with_markers(base, markers))
    facts = result.facts["elevationMarkers"]
    assert facts["low"]["gradePct"] > RULE_CONSTANTS["elevationMaxGradePct"]
    assert facts["high"]["gradePct"] > RULE_CONSTANTS["elevationMaxGradePct"]
    assert "elevation-marker-grade" in violations(result) and not result.valid
    main = result.model.route("main")
    for m in result.model.elevation_markers:  # heights are kept as given
        assert abs(z_at(main, m.s) - m.height_m) < 0.1


def test_grid_level_rule_and_marker_on_grid_warning() -> None:
    base = sample("oval")
    plain = build_track_model(base)
    markers = [
        {"id": "line", "position": canvas_at(plain.model, "main", 2.0), "heightM": 0},
        {"id": "pole", "position": canvas_at(plain.model, "main", 70.0), "heightM": 4},
    ]
    result = build_track_model(with_markers(base, markers))
    assert result.facts["gridMaxGradePct"] > RULE_CONSTANTS["elevationGridMaxGradePct"]
    assert result.facts["elevationMarkers"]["line"]["feature"] == "grid"
    found = violations(result)
    assert {"elevation-grid-level", "elevation-marker-on-feature"} <= found
    level = build_track_model(
        with_markers(base, [{**markers[0]}, {**markers[1], "heightM": 0}])
    )
    assert level.facts["gridMaxGradePct"] == 0.0
    assert "elevation-grid-level" not in violations(level)


def test_marker_on_a_bridge_ramp_is_flagged() -> None:
    base = sample("figure8_bridge")
    plain = build_track_model(base)
    c = next(c for c in plain.model.crossings if c.type == "bridge")
    over = c.branches[c.over_branch]
    pos = canvas_at(
        plain.model, over.route, over.s - c.deck_half_length_m - c.ramp_up_m / 2
    )
    result = build_track_model(
        with_markers(base, [{"id": "r", "position": pos, "heightM": 0}], "gentle")
    )
    assert result.facts["elevationMarkers"]["r"]["feature"] == "bridge"
    assert "elevation-marker-on-feature" in violations(result)


def test_marker_on_a_missing_route_is_left_out() -> None:
    spec = with_markers(
        sample("oval"),
        [{"id": "x", "position": {"x": 500, "y": 100}, "route": "nope", "heightM": 5}],
    )
    result = build_track_model(spec)
    assert result.facts["elevationMarkers"] == {}
    assert "elevation-marker-route-missing" in {w.code for w in result.warnings}
    assert "elevation-marker-route-exists" in violations(result)
    assert math.isclose(result.facts["heightRangeM"], 0.0)
