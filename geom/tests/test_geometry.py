"""Unit tests for the low-level geometry: polylines, smoothing, curvature, intersections, profiles."""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

from trackgen_geom import constants as C
from trackgen_geom import elevation as el
from trackgen_geom import polyline as pl


def circle(r: float, n: int, cx: float = 0.0, cy: float = 0.0) -> np.ndarray:
    t = np.arange(n) * 2 * math.pi / n
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)])


def test_resample_closed_is_even_and_starts_at_offset() -> None:
    square = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=float)
    q = pl.resample_closed(square, 40, start_s=10.0)
    assert np.allclose(pl.seg_lengths(q, True), 10.0)
    assert np.allclose(q[0], [10.0, 0.0])
    assert pl.length(q, True) == pytest.approx(400.0)


def test_resample_open_keeps_end_points_exactly() -> None:
    p = np.array([[0.1, 0.2], [3.1, 4.2], [9.1, 12.2]])
    q = pl.resample_open(p, 9)
    assert len(q) == 10
    assert tuple(q[0]) == tuple(p[0]) and tuple(q[-1]) == tuple(p[-1])
    assert np.allclose(pl.seg_lengths(q, False), pl.length(p, False) / 9)


def test_dedupe_drops_repeats_and_closing_point() -> None:
    p = np.array([[0, 0], [0, 0], [1, 0], [1, 1], [0, 0]], dtype=float)
    assert pl.dedupe(p, closed=True).tolist() == [[0, 0], [1, 0], [1, 1]]


def test_gaussian_smoothing_removes_jitter() -> None:
    rng = random.Random(7)
    noisy = circle(300, 400) + np.array([[rng.uniform(-3, 3), rng.uniform(-3, 3)] for _ in range(400)])
    smooth = pl.gaussian_smooth(noisy, True, C.SMOOTH_SIGMA_M, C.SAMPLE_SPACING_M)
    r_noisy = pl.radii(pl.resample_closed(noisy, 900), True, C.RADIUS_WINDOW)
    r_smooth = pl.radii(pl.resample_closed(smooth, 900), True, C.RADIUS_WINDOW)
    assert r_smooth.min() > 5 * r_noisy.min()


def test_gaussian_smoothing_open_keeps_ends() -> None:
    p = np.array([[0, 0], [50, 10], [100, 0]], dtype=float)
    q = pl.gaussian_smooth(p, False, 15.0, 2.0)
    assert tuple(q[0]) == (0.0, 0.0) and tuple(q[-1]) == (100.0, 0.0)


def test_subdivision_converges_to_a_smooth_curve() -> None:
    ctrl = circle(100, 24)
    dense = pl.subdivide_closed(ctrl, 4)
    assert len(dense) == 24 * 16
    r = np.hypot(dense[:, 0], dense[:, 1])
    assert r.min() > 98.0 and r.max() < 100.0  # B-splines shrink slightly towards the centre
    open_dense = pl.subdivide_open(ctrl[:6], 3)
    assert tuple(open_dense[0]) == tuple(ctrl[0]) and tuple(open_dense[-1]) == tuple(ctrl[5])


def test_curvature_of_a_circle() -> None:
    p = circle(50, 157)
    r = pl.radii(p, True, C.RADIUS_WINDOW)
    assert np.allclose(r, 50.0, rtol=1e-6)
    k = pl.signed_curvature(p, True, C.RADIUS_WINDOW)
    assert np.all(k > 0)  # counter-clockwise turns left
    assert np.all(pl.signed_curvature(p[::-1], True, C.RADIUS_WINDOW) < 0)


def test_straight_line_has_infinite_radius() -> None:
    p = np.column_stack([np.arange(20.0), np.zeros(20)])
    assert np.all(np.isinf(pl.radii(p, False, 3)))


def test_project_onto_closed_polyline() -> None:
    square = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=float)
    proj = pl.project(square, True, np.array([-5.0, 50.0]))
    assert proj.s == pytest.approx(350.0)
    assert proj.distance == pytest.approx(5.0)


def test_figure_eight_self_intersection() -> None:
    t = np.arange(400) * 2 * math.pi / 400
    d = 1 + np.sin(t) ** 2
    p = np.column_stack([400 * np.cos(t) / d, 400 * np.sin(t) * np.cos(t) / d])
    hits = pl.intersections([(p, True)], 10.0, 5)
    assert len(hits) == 1
    assert math.hypot(*hits[0].point) < 1e-6
    assert 199 <= abs(hits[0].seg_a - hits[0].seg_b) <= 201


def test_intersections_between_routes() -> None:
    a = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
    b = np.array([[5, -5], [5, 15]], dtype=float)
    hits = pl.intersections([(a, True), (b, False)], 4.0, 2)
    assert sorted(h.point for h in hits) == [(5.0, 0.0), (5.0, 10.0)]


def test_ramp_shape() -> None:
    u = np.linspace(0, 1, 1001)
    f = el.ramp_shape(u)
    assert f[0] == 0.0 and f[-1] == pytest.approx(1.0)
    assert np.all(np.diff(f) >= 0)
    grade = np.max(np.diff(f) / np.diff(u))
    assert grade == pytest.approx(1 / (1 - C.RAMP_TRANSITION_FRACTION), rel=1e-2)


def test_bridge_ramp_length_respects_rules_and_grade() -> None:
    length = el.bridge_ramp_length(50.0)
    assert length >= 50.0
    assert el.ramp_peak_grade(C.BRIDGE_DECK_HEIGHT_M, length) <= C.BRIDGE_MAX_GRADE + 1e-12
    assert el.bridge_ramp_length(80.0) == 80.0


@pytest.mark.parametrize("size", ["small", "medium", "large"])
def test_bidirectional_jump_is_symmetric(size: str) -> None:
    d = np.linspace(-40, 40, 801)
    z = el.jump_profile(size, True, d)
    assert np.allclose(z, z[::-1])
    assert z.max() == pytest.approx(C.JUMP_SIZES[size].height_m)
    lo, hi = el.jump_extent(size, True)
    assert lo == -hi
    assert np.all(z[(d < lo) | (d > hi)] == 0)


@pytest.mark.parametrize("size", ["small", "medium", "large"])
def test_one_way_jump_has_takeoff_lip_and_landing(size: str) -> None:
    js = C.JUMP_SIZES[size]
    d = np.linspace(-js.takeoff_m - 5, C.JUMP_LIP_M + js.landing_m + 5, 4001)
    z = el.jump_profile(size, False, d)
    takeoff = (d > -js.takeoff_m) & (d < 0)
    assert np.all(np.diff(z[takeoff]) > 0)  # climbs to the lip
    assert np.all(z[(d >= 0) & (d <= C.JUMP_LIP_M)] == js.height_m)  # flat lip
    landing = (d > C.JUMP_LIP_M) & (d < C.JUMP_LIP_M + js.landing_m)
    assert np.all(np.diff(z[landing]) < 0)
    lip_grade = 2 * js.height_m / js.takeoff_m
    land_grade = np.max(-np.diff(z[landing]) / np.diff(d[landing]))
    assert land_grade < lip_grade  # gentler landing than takeoff
    assert z[0] == 0 and z[-1] == 0


def test_terrain_is_deterministic_and_grade_limited() -> None:
    p = circle(300, 940)
    s = pl.cumulative(p, True)[:-1]
    a, b = el.Terrain("hills", "hilly"), el.Terrain("hills", "hilly")
    assert np.array_equal(a.height(p), b.height(p))
    assert not np.array_equal(a.height(p), el.Terrain("other", "hilly").height(p))
    steepest = a.limit_grade([(p, s, True)])
    assert steepest <= C.ELEVATION_LEVELS["hilly"].max_grade + 1e-12
    z = a.height(p)
    grades = np.abs(np.diff(np.append(z, z[0]))) / pl.seg_lengths(p, True)
    assert grades.max() <= C.ELEVATION_LEVELS["hilly"].max_grade + 1e-9
    assert np.abs(z).max() <= C.ELEVATION_LEVELS["hilly"].amplitude_m + 1e-9
    assert np.all(el.Terrain("x", "flat").height(p) == 0)
