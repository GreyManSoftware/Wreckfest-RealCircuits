"""Deterministic scatter of stock props around the track (environment presets).

Props are Toolbox Mz subscene references: an empty named ``#xref ob/<prop>.scne`` exports as an OBJX
object pointing at the stock ``data/art/objects/<prop>.scn(e)``, like the example mod's
``prop_trafficcone01#xref`` objects. Nothing is copied from the game.
"""
from __future__ import annotations

import hashlib
import math
import random

import numpy as np

from . import config as C
from .layout import Empty


def seed_for(slug: str) -> int:
    return int.from_bytes(hashlib.sha256(f"trackgen-props:{slug}".encode()).digest()[:8], "big")


def scatter(geo) -> list[Empty]:
    env = geo.options.get("environment", "gravel_pit")
    props = C.PROPS.get(env, C.PROPS["gravel_pit"])
    x0, x1, y0, y1 = geo.extent
    inset = C.BERM_W
    if x1 - x0 <= 2 * inset or y1 - y0 <= 2 * inset:
        return []
    area_ha = (x1 - x0) * (y1 - y0) / 10_000.0
    target = int(min(C.PROP_MAX, round(area_ha * C.PROP_DENSITY_PER_HA)))
    rng = random.Random(seed_for(geo.model["slug"]))
    n = target * 20
    cand = np.array([(rng.uniform(x0 + inset, x1 - inset), rng.uniform(y0 + inset, y1 - inset),
                      rng.uniform(0.0, 2.0 * math.pi), rng.randrange(len(props))) for _ in range(n)])
    if not len(cand):
        return []
    xy = geo.index.xy
    hw = geo.index.hw
    ok = np.ones(len(cand), dtype=bool)
    for a in range(0, len(cand), 512):
        p = cand[a:a + 512, :2]
        d = np.hypot(p[:, None, 0] - xy[None, :, 0], p[:, None, 1] - xy[None, :, 1]) - hw[None, :]
        ok[a:a + 512] = d.min(1) > C.PROP_CLEARANCE
    placed: list[np.ndarray] = []
    out: list[Empty] = []
    for c in cand[ok]:
        if len(out) >= target:
            break
        if placed and np.min(np.hypot(*(np.array(placed) - c[:2]).T)) < 8.0:
            continue
        placed.append(c[:2])
        z = float(geo.terrain(np.array([[c[0], c[1], 0.0]]))[0])
        name = props[int(c[3])]
        out.append(Empty(f"#xref ob/{name}.scne", (float(c[0]), float(c[1]), z - 0.05), float(c[2]),
                         display="PLAIN_AXES", size=2.0))
    return out
