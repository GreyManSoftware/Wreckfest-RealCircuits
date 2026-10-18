"""Stroke to centreline: low-pass filter, fit a cubic B-spline, relax tight corners, scale and orient."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from . import constants as C
from . import polyline as pl
from .polyline import GeometryError


def canvas_to_world(points: object) -> np.ndarray:
    """Canvas (x right, y down) to unscaled world axes (x east, y north): flip y."""
    p = pl.as_points(points).copy()
    p[:, 1] = -p[:, 1]
    return p


@dataclass
class Relaxation:
    iterations: int
    converged: bool
    max_deviation_m: float


@dataclass
class Centreline:
    points: np.ndarray
    scale: float  # canvas units to metres (before centring)
    relaxation: Relaxation


def _control_points(p: np.ndarray, closed: bool) -> np.ndarray:
    q = pl.gaussian_smooth(p, closed, C.SMOOTH_SIGMA_M, C.SAMPLE_SPACING_M)
    total = pl.length(q, closed)
    if closed:
        return pl.resample_closed(q, max(8, round(total / C.CONTROL_SPACING_M)))
    return pl.resample_open(q, max(2, round(total / C.CONTROL_SPACING_M)))


def relax(
    ctrl: np.ndarray,
    closed: bool,
    build: Callable[[np.ndarray], tuple[np.ndarray, float]],
    target_radius: float,
    max_deviation: float,
    pinned: tuple[int, ...] = (),
) -> tuple[np.ndarray, np.ndarray, Relaxation]:
    """Laplacian-smooth control points near corners tighter than ``target_radius`` until none remain,
    the shape tolerance (max control-point movement) would be exceeded, or the iteration cap is hit.

    ``build(ctrl)`` returns the evenly sampled centreline and the factor it was scaled by.
    Returns the final control points, samples and a summary.
    """
    original = ctrl.copy()
    samples, factor = build(ctrl)
    n = len(ctrl)
    pin = np.zeros(n, dtype=bool)
    for i in pinned:
        pin[i] = True
    deviation = 0.0
    for it in range(C.RELAX_MAX_ITERATIONS):
        r = pl.radii(samples, closed, C.RADIUS_WINDOW)
        tight = np.flatnonzero(r < target_radius)
        if len(tight) == 0:
            return ctrl, samples, Relaxation(it, True, deviation)
        pts = samples[tight] / factor
        d = np.hypot(pts[:, None, 0] - ctrl[None, :, 0], pts[:, None, 1] - ctrl[None, :, 1])
        nearest = np.unique(np.argmin(d, axis=1))
        mask = np.zeros(n, dtype=bool)
        for k in range(-C.RELAX_MASK_SPREAD, C.RELAX_MASK_SPREAD + 1):
            idx = nearest + k
            idx = np.mod(idx, n) if closed else idx[(idx >= 0) & (idx < n)]
            mask[idx] = True
        mask &= ~pin
        if not closed:
            mask[0] = mask[-1] = False
        if not mask.any():
            return ctrl, samples, Relaxation(it, False, deviation)
        if closed:
            lap = (np.roll(ctrl, 1, axis=0) + np.roll(ctrl, -1, axis=0)) / 2.0 - ctrl
        else:
            lap = np.zeros_like(ctrl)
            lap[1:-1] = (ctrl[:-2] + ctrl[2:]) / 2.0 - ctrl[1:-1]
        candidate = ctrl.copy()
        candidate[mask] += C.RELAX_STEP * lap[mask]
        cand_dev = float(np.max(np.hypot(*(candidate - original).T)))
        if cand_dev > max_deviation:
            return ctrl, samples, Relaxation(it, False, deviation)
        ctrl, deviation = candidate, cand_dev
        samples, factor = build(ctrl)
    return ctrl, samples, Relaxation(C.RELAX_MAX_ITERATIONS, False, deviation)


def max_deviation_for(length_m: float) -> float:
    return max(C.RELAX_MAX_DEVIATION_MIN_M, C.RELAX_MAX_DEVIATION_FRACTION * length_m)


def build_main_loop(
    raw_points: object,
    lap_length_m: float,
    target_radius: float,
    start_canvas: tuple[float, float],
    heading_canvas_deg: float | None,
    toward_canvas: tuple[float, float] | None = None,
) -> Centreline:
    """Closed centreline of exactly ``lap_length_m``, sampled about every SAMPLE_SPACING_M, oriented
    to the start/finish heading, with point 0 (s = 0) at the start/finish."""
    p = pl.dedupe(canvas_to_world(raw_points), closed=True)
    if len(p) < 3:
        raise GeometryError("The main loop needs at least three distinct points.")
    raw_len = pl.length(p, True)
    if raw_len <= 0:
        raise GeometryError("The main loop has no length.")
    k0 = lap_length_m / raw_len
    p = p * k0
    n = max(16, round(lap_length_m / C.SAMPLE_SPACING_M))

    def build(ctrl: np.ndarray) -> tuple[np.ndarray, float]:
        dense = pl.subdivide_closed(ctrl, C.SUBDIVISION_LEVELS)
        f = lap_length_m / pl.length(dense, True)
        return pl.resample_closed(dense * f, n), f

    ctrl = _control_points(p, closed=True)
    ctrl, _, relaxation = relax(ctrl, True, build, target_radius, max_deviation_for(lap_length_m))
    dense = pl.subdivide_closed(ctrl, C.SUBDIVISION_LEVELS)
    f = lap_length_m / pl.length(dense, True)
    dense = dense * f
    scale = k0 * f

    start = canvas_to_world([start_canvas])[0] * scale
    if heading_canvas_deg is not None:
        h = math.radians(heading_canvas_deg)
        want = np.array([math.cos(h), -math.sin(h)])
    else:
        assert toward_canvas is not None
        want = canvas_to_world([toward_canvas])[0] * scale - start
    proj = pl.project(dense, True, start)
    ahead = pl.interp_at(pl.cumulative(dense, True), dense, True, proj.s + 1.0)
    if float(np.dot(ahead - proj.point, want)) < 0:
        dense = dense[::-1].copy()
        proj = pl.project(dense, True, start)
    samples = pl.resample_closed(dense, n, start_s=proj.s)
    f2 = lap_length_m / pl.length(samples, True)
    return Centreline(samples * f2, scale * f2, relaxation)


def build_alt_route(
    raw_points: object,
    scale: float,
    start: np.ndarray,
    end: np.ndarray,
    main_tangent_start: np.ndarray,
    main_tangent_end: np.ndarray,
    target_radius: float,
) -> tuple[np.ndarray, Relaxation]:
    """Open centreline from ``start`` to ``end`` (exact main-loop vertices) following the stroke.
    The first and last spans lean towards the main loop's driving direction so the branch blends in."""
    p = pl.dedupe(canvas_to_world(raw_points) * scale, closed=False)
    if len(p) < 2:
        p = np.vstack([start, end])
    p = p.copy()
    p[0], p[-1] = start, end
    p = pl.dedupe(p, closed=False)
    if len(p) < 2:
        raise GeometryError("An alternative route starts and ends at the same place.")
    ctrl = _control_points(p, closed=False)
    total = pl.length(ctrl, False)
    guide = min(C.ALT_TANGENT_GUIDE * C.CONTROL_SPACING_M, 0.25 * total)

    def unit(v: np.ndarray) -> np.ndarray:
        n = math.hypot(*v)
        return v / n if n > 0 else v

    g0 = unit(unit(main_tangent_start) + unit(ctrl[1] - ctrl[0]))
    g1 = unit(unit(main_tangent_end) + unit(ctrl[-1] - ctrl[-2]))
    ctrl = np.vstack([ctrl[:1], ctrl[:1] + g0 * guide, ctrl[1:-1], ctrl[-1:] - g1 * guide, ctrl[-1:]])

    def build(c: np.ndarray) -> tuple[np.ndarray, float]:
        dense = pl.subdivide_open(c, C.SUBDIVISION_LEVELS)
        return pl.resample_open(dense, max(2, round(pl.length(dense, False) / C.SAMPLE_SPACING_M))), 1.0

    _, samples, relaxation = relax(ctrl, False, build, target_radius, max_deviation_for(total))
    samples[0], samples[-1] = start, end
    return samples, relaxation
