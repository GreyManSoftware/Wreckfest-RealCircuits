"""Deterministic smooth value noise (pure numpy), shared by the generated maps and the scenery planner so that,
e.g., the forest-floor texture and the trees use the same clusters."""
from __future__ import annotations

import hashlib

import numpy as np


def seed_for(slug: str, what: str) -> int:
    return int.from_bytes(hashlib.sha256(f"trackgen-{what}:{slug}".encode()).digest()[:8], "big")


class Noise2D:
    """Fractal value noise over a square frame (x0, y0, size), values roughly 0..1 (mean 0.5).

    ``feature`` is the size of the largest blobs in metres; each further octave halves it."""

    def __init__(self, seed: int, frame: tuple[float, float, float], feature: float, octaves: int = 3,
                 res: int = 256):
        self.frame = frame
        x0, y0, size = frame
        rng = np.random.default_rng(seed)
        self.res = res
        acc = np.zeros((res, res))
        amp, total = 1.0, 0.0
        f = feature
        for _ in range(octaves):
            cells = max(1, int(round(size / f)))
            g = rng.random((cells + 2, cells + 2))
            t = np.linspace(0.0, cells, res)
            i = np.minimum(np.floor(t).astype(int), cells - 1) if cells > 0 else np.zeros(res, int)
            u = t - i
            u = u * u * (3 - 2 * u)
            a = g[np.ix_(i, i)]
            b = g[np.ix_(i, i + 1)]
            c = g[np.ix_(i + 1, i)]
            d = g[np.ix_(i + 1, i + 1)]
            uu = u[None, :]
            vv = u[:, None]
            acc += amp * ((a * (1 - uu) + b * uu) * (1 - vv) + (c * (1 - uu) + d * uu) * vv)
            total += amp
            amp *= 0.5
            f /= 2.0
        self.g = acc / total  # rows = y, cols = x

    def at(self, xy) -> np.ndarray:
        x0, y0, size = self.frame
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        fx = np.clip((xy[:, 0] - x0) / size * (self.res - 1), 0, self.res - 1.001)
        fy = np.clip((xy[:, 1] - y0) / size * (self.res - 1), 0, self.res - 1.001)
        i, j = np.floor(fx).astype(int), np.floor(fy).astype(int)
        u, v = fx - i, fy - j
        g = self.g
        return (g[j, i] * (1 - u) + g[j, i + 1] * u) * (1 - v) + (g[j + 1, i] * (1 - u) + g[j + 1, i + 1] * u) * v

    def grid(self, n: int) -> np.ndarray:
        x0, y0, size = self.frame
        c = (np.arange(n) + 0.5) / n * size
        gx, gy = np.meshgrid(x0 + c, y0 + c)
        return self.at(np.stack([gx.ravel(), gy.ravel()], axis=1)).reshape(n, n)


def periodic_noise(seed: int, n: int, waves: int = 6) -> np.ndarray:
    """Smooth noise around a circle (n samples, periodic), roughly -1..1."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / n * 2 * np.pi
    out = np.zeros(n)
    for k in range(1, waves + 1):
        out += rng.normal() / k * np.cos(k * t + rng.uniform(0, 2 * np.pi))
    m = np.abs(out).max()
    return out / m if m > 0 else out
