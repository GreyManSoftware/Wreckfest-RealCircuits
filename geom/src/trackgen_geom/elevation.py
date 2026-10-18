"""Elevation: deterministic terrain noise, bridge ramps and jump profiles."""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass

import numpy as np

from . import constants as C


def seed_for(slug: str) -> int:
    return int.from_bytes(hashlib.sha256(f"trackgen-terrain:{slug}".encode()).digest()[:8], "big")


class Terrain:
    """A smooth 2D height field: a sum of a few plane waves with seeded directions, wavelengths and
    phases. Heights depend only on (x, y), so shared vertices and flat crossings get identical heights
    on every route. ``scale`` is reduced (globally) if the routes would exceed the level's max grade."""

    def __init__(self, slug: str, level: str) -> None:
        self.level = C.ELEVATION_LEVELS[level]
        rng = random.Random(seed_for(slug))
        comps = []
        for _ in range(C.NOISE_COMPONENTS):
            theta = rng.uniform(0.0, 2.0 * math.pi)
            wavelength = rng.uniform(self.level.wavelength_min_m, self.level.wavelength_max_m)
            phase = rng.uniform(0.0, 2.0 * math.pi)
            k = 2.0 * math.pi / wavelength
            comps.append((k * math.cos(theta), k * math.sin(theta), phase, wavelength / self.level.wavelength_max_m))
        self._comps = comps
        self._norm = sum(c[3] for c in comps)
        self.scale = 1.0

    @property
    def amplitude_m(self) -> float:
        return self.level.amplitude_m * self.scale

    def height(self, xy: np.ndarray) -> np.ndarray:
        if self.amplitude_m == 0.0:
            return np.zeros(len(xy))
        z = np.zeros(len(xy))
        for kx, ky, phase, weight in self._comps:
            z += weight * np.sin(kx * xy[:, 0] + ky * xy[:, 1] + phase)
        return z * (self.amplitude_m / self._norm)

    def limit_grade(self, routes: list[tuple[np.ndarray, np.ndarray, bool]]) -> float:
        """Scale the field down so no route (points, arc lengths, closed) exceeds the max grade.
        Returns the steepest terrain grade after scaling."""
        steepest = 0.0
        for pts, s, closed in routes:
            z = self.height(pts)
            if closed:
                dz = np.diff(np.concatenate([z, z[:1]]))
                ds = np.diff(np.concatenate([s, [s[-1] + math.hypot(*(pts[0] - pts[-1]))]]))
            else:
                dz, ds = np.diff(z), np.diff(s)
            ok = ds > 1e-6
            if ok.any():
                steepest = max(steepest, float(np.max(np.abs(dz[ok] / ds[ok]))))
        if steepest > self.level.max_grade > 0:
            self.scale = self.level.max_grade / steepest
            steepest = self.level.max_grade
        return steepest


def ramp_shape(u: np.ndarray) -> np.ndarray:
    """0 -> 1 over u in [0, 1]: constant grade with parabolic vertical curves at both ends."""
    tau = C.RAMP_TRANSITION_FRACTION
    g = 1.0 / (1.0 - tau)
    u = np.clip(u, 0.0, 1.0)
    return np.where(
        u < tau,
        g * u * u / (2.0 * tau),
        np.where(u <= 1.0 - tau, g * (u - tau / 2.0), 1.0 - g * (1.0 - u) ** 2 / (2.0 * tau)),
    )


def ramp_peak_grade(height: float, length: float) -> float:
    return height / (length * (1.0 - C.RAMP_TRANSITION_FRACTION)) if length > 0 else math.inf


def bridge_ramp_length(min_length: float, height: float = C.BRIDGE_DECK_HEIGHT_M) -> float:
    """Ramp length needed to climb ``height`` within BRIDGE_MAX_GRADE (at least ``min_length``)."""
    need = height / (C.BRIDGE_MAX_GRADE * (1.0 - C.RAMP_TRANSITION_FRACTION))
    return max(min_length, need)


def bridge_bump(
    d: np.ndarray, deck_half: float, up: float, down: float, height: float = C.BRIDGE_DECK_HEIGHT_M
) -> np.ndarray:
    """Height added to the over branch at signed distance ``d`` from the crossing."""
    out = np.zeros_like(d)
    on_deck = np.abs(d) <= deck_half
    out[on_deck] = height
    before = (d < -deck_half) & (d >= -deck_half - up)
    if up > 0:
        out[before] = height * ramp_shape((d[before] + deck_half + up) / up)
    after = (d > deck_half) & (d <= deck_half + down)
    if down > 0:
        out[after] = height * ramp_shape((deck_half + down - d[after]) / down)
    return out


# --- Height markers ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Control:
    """A control point of a route's marker profile: a marker (``marker`` = its id) or an anchor."""

    s: float
    h: float
    marker: str | None = None


def bump_radius(height: float) -> float:
    """Half-width of the bump a marker adds on top of a preset terrain: wide enough that a lone bump's
    sustained grade stays within MARKER_BUMP_GRADE."""
    return max(C.MARKER_BUMP_MIN_RADIUS_M, abs(height) / C.MARKER_BUMP_GRADE)


def marker_controls(
    markers: list[tuple[str, float, float]],
    closed: bool,
    length: float,
    additive: bool,
    ends: tuple[float, float] = (0.0, 0.0),
) -> list[Control]:
    """Control points of a route's marker profile, sorted by s.

    ``markers`` are (id, s, height). Open routes get anchors at both ends with the heights in ``ends``
    (the main loop's profile at the joins). ``additive`` (preset terrain): each marker is a bump, so
    zero anchors are added ``bump_radius`` either side of it wherever neighbouring bumps don't overlap.
    """
    pts = [Control(float(s), float(h), mid) for mid, s, h in sorted(markers, key=lambda m: (m[1], m[0]))]
    if not closed:
        pts = [Control(0.0, float(ends[0])), *pts, Control(float(length), float(ends[1]))]
    if not additive or not pts:
        return pts
    out: list[Control] = []
    pairs = len(pts) if closed else len(pts) - 1
    for i in range(pairs):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        gap = b.s - a.s if (not closed or i + 1 < len(pts)) else b.s + length - a.s
        out.append(a)
        ra, rb = bump_radius(a.h), bump_radius(b.h)
        if gap > ra + rb + C.MARKER_MERGE_M:
            out.append(Control((a.s + ra) % length if closed else a.s + ra, 0.0))
            out.append(Control((b.s - rb) % length if closed else b.s - rb, 0.0))
    if not closed:
        out.append(pts[-1])
    return sorted(out, key=lambda c: (c.s, c.marker is None, c.marker or ""))


def control_grades(controls: list[Control], closed: bool, length: float) -> dict[str, float]:
    """Sustained grade each marker demands: the larger |dh| / ds to its neighbouring control points."""
    out: dict[str, float] = {}
    n = len(controls)
    for i, c in enumerate(controls):
        if c.marker is None:
            continue
        grade = 0.0
        for j in (i - 1, i + 1):
            if not closed and not 0 <= j < n:
                continue
            o = controls[j % n]
            if o is c:
                continue
            ds = abs(o.s - c.s)
            if closed and (j < 0 or j >= n):
                ds = length - ds
            grade = max(grade, abs(o.h - c.h) / max(ds, C.MARKER_MIN_SPACING_M))
        out[c.marker] = max(out.get(c.marker, 0.0), grade)
    return out


def _pchip_slopes(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Fritsch-Butland (PCHIP) slopes: monotone between control points, so no overshoot. The end slopes
    are the end secants."""
    h = np.diff(x)
    delta = np.diff(y) / h
    d = np.zeros_like(y)
    d[0], d[-1] = delta[0], delta[-1]
    for k in range(1, len(x) - 1):
        if delta[k - 1] * delta[k] <= 0.0:
            continue
        w1, w2 = 2.0 * h[k] + h[k - 1], h[k] + 2.0 * h[k - 1]
        d[k] = (w1 + w2) / (w1 / delta[k - 1] + w2 / delta[k])
    return d


def _hermite(x: np.ndarray, y: np.ndarray, d: np.ndarray, t: np.ndarray) -> np.ndarray:
    t = np.clip(t, x[0], x[-1])
    k = np.clip(np.searchsorted(x, t, side="right") - 1, 0, len(x) - 2)
    h = x[k + 1] - x[k]
    u = (t - x[k]) / h
    u2, u3 = u * u, u * u * u
    return (
        (2 * u3 - 3 * u2 + 1) * y[k]
        + (u3 - 2 * u2 + u) * h * d[k]
        + (-2 * u3 + 3 * u2) * y[k + 1]
        + (u3 - u2) * h * d[k + 1]
    )


def _dedupe(controls: list[Control]) -> list[Control]:
    out: list[Control] = []
    for c in controls:
        if out and c.s - out[-1].s < C.MARKER_MERGE_M:
            continue
        out.append(c)
    return out


def marker_profile(s: np.ndarray, controls: list[Control], closed: bool, length: float) -> np.ndarray:
    """Monotone cubic (PCHIP) interpolation of the control points at arc lengths ``s``: it passes through
    every control point and never overshoots them. Closed routes wrap around, so the loop closes with a
    continuous height and grade."""
    s = np.asarray(s, dtype=np.float64)
    pts = _dedupe(controls)
    if closed and len(pts) >= 2 and pts[0].s + length - pts[-1].s < C.MARKER_MERGE_M:
        pts = pts[:-1]
    if not pts:
        return np.zeros_like(s)
    if len(pts) == 1:
        return np.full_like(s, pts[0].h)
    x = np.array([c.s for c in pts])
    y = np.array([c.h for c in pts])
    if not closed:
        return _hermite(x, y, _pchip_slopes(x, y), s)
    n = len(pts)
    xe = np.concatenate([x[-2:] - length, x, x[:2] + length]) if n >= 2 else x
    ye = np.concatenate([y[-2:], y, y[:2]])
    de = _pchip_slopes(xe, ye)
    t = x[0] + np.mod(s - x[0], length)
    return _hermite(xe[2 : n + 3], ye[2 : n + 3], de[2 : n + 3], t)


def level_window(d: np.ndarray, half_width: float) -> np.ndarray:
    """1 at d = 0, easing to 0 at |d| = half_width (raised cosine)."""
    a = np.abs(d)
    return np.where(a < half_width, 0.5 * (1.0 + np.cos(np.pi * a / half_width)), 0.0)


def jump_extent(size: str, bidirectional: bool) -> tuple[float, float]:
    """Footprint of a jump relative to its marker: (start offset <= 0, end offset >= 0)."""
    js = C.JUMP_SIZES[size]
    if bidirectional:
        half = C.JUMP_TABLE_M / 2.0 + js.takeoff_m
        return -half, half
    return -js.takeoff_m, C.JUMP_LIP_M + js.landing_m


def jump_profile(size: str, bidirectional: bool, d: np.ndarray) -> np.ndarray:
    """Height added by a jump at signed distance ``d`` from its marker (driving direction positive).

    One-directional: a concave takeoff ramp up to the lip at d = 0, a short flat lip, then a long
    smooth landing. Bidirectional: two identical takeoffs either side of a flat table, so the
    profile is symmetric (d and -d give the same height) and drivable both ways.
    """
    js = C.JUMP_SIZES[size]
    h, lt = js.height_m, js.takeoff_m
    d = np.asarray(d, dtype=np.float64)
    out = np.zeros_like(d)
    if bidirectional:
        half_table = C.JUMP_TABLE_M / 2.0
        a = np.abs(d)
        out[a <= half_table] = h
        ramp = (a > half_table) & (a <= half_table + lt)
        u = (half_table + lt - a[ramp]) / lt
        out[ramp] = h * u * u
        return out
    take = (d >= -lt) & (d < 0)
    u = (d[take] + lt) / lt
    out[take] = h * u * u
    out[(d >= 0) & (d <= C.JUMP_LIP_M)] = h
    land = (d > C.JUMP_LIP_M) & (d <= C.JUMP_LIP_M + js.landing_m)
    u = (d[land] - C.JUMP_LIP_M) / js.landing_m
    out[land] = h * (1.0 - (3.0 * u * u - 2.0 * u**3))
    return out
