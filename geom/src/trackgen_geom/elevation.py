"""Elevation: deterministic terrain noise, bridge ramps and jump profiles."""

from __future__ import annotations

import hashlib
import math
import random

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


def bridge_ramp_length(min_length: float) -> float:
    """Ramp length needed to climb the deck height within BRIDGE_MAX_GRADE (at least ``min_length``)."""
    need = C.BRIDGE_DECK_HEIGHT_M / (C.BRIDGE_MAX_GRADE * (1.0 - C.RAMP_TRANSITION_FRACTION))
    return max(min_length, need)


def bridge_bump(d: np.ndarray, deck_half: float, up: float, down: float) -> np.ndarray:
    """Height added to the over branch at signed distance ``d`` from the crossing."""
    out = np.zeros_like(d)
    on_deck = np.abs(d) <= deck_half
    out[on_deck] = C.BRIDGE_DECK_HEIGHT_M
    before = (d < -deck_half) & (d >= -deck_half - up)
    if up > 0:
        out[before] = C.BRIDGE_DECK_HEIGHT_M * ramp_shape((d[before] + deck_half + up) / up)
    after = (d > deck_half) & (d <= deck_half + down)
    if down > 0:
        out[after] = C.BRIDGE_DECK_HEIGHT_M * ramp_shape((deck_half + down - d[after]) / down)
    return out


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
