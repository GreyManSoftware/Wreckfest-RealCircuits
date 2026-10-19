"""A coarse terrain height grid around a TrackModel, for the web editor's 3D preview.

This is a *preview approximation* of the worker's terrain (``worker/builder/wfbuild/geometry.py``
``TerrainField``): the same seeded natural ground (:class:`trackgen_geom.elevation.Terrain`, scaled by
the same grade limit), pulled to just below the road near it and blended back to the ground further
out. Road samples on a bridge deck or its ramps don't pull the terrain up, so the ground stays under
a bridge. The cell is coarse (8 m or more) to keep the response small; the worker uses 4 m.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import elevation as el

#: Terrain under and beside the road sits this far below it (as the worker).
SINK_M = 0.15
#: Within this distance of the road edge the terrain follows the road.
CORE_M = 3.0
#: Then it blends to the natural ground over this distance.
BLEND_M = 30.0
MIN_CELL_M = 8.0
#: At most this many cells along the longer side.
MAX_CELLS = 128


def _smoothstep(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _bridge_mask(model: dict[str, Any], route: dict[str, Any], s: np.ndarray) -> np.ndarray:
    """Samples of ``route`` on a bridge deck or ramp (they don't shape the ground)."""
    mask = np.zeros(len(s), dtype=bool)
    length = float(route["lengthM"])
    for c in model.get("crossings", []):
        k = c.get("overBranch")
        if c.get("type") != "bridge" or k is None or not c.get("detected", True):
            continue
        b = c["branches"][k]
        if b.get("route") != route["id"]:
            continue
        lo = float(c.get("deckHalfLengthM") or 0.0) + float(c.get("rampUpM") or 0.0)
        hi = float(c.get("deckHalfLengthM") or 0.0) + float(c.get("rampDownM") or 0.0)
        d = s - float(b["s"])
        if route.get("closed"):
            d = (d + length / 2.0) % length - length / 2.0
        mask |= (d > -lo) & (d < hi)
    return mask


def terrain_grid(model: dict[str, Any]) -> dict[str, Any]:
    """The terrain heights on a regular grid covering the model's bounds plus the terrain margin.

    Returns ``{originX, originY, cellM, nx, ny, z}``: ``z`` has ``(nx + 1) * (ny + 1)`` heights in
    metres, row by row (``z[j * (nx + 1) + i]`` is at ``x = originX + i * cellM``,
    ``y = originY + j * cellM``), rounded to 2 decimals.
    """
    b = model["bounds"]
    margin = float(b["terrainMarginM"])
    lim = float(b["limitM"]) - 1.0
    x0, x1 = max(b["minX"] - margin, -lim), min(b["maxX"] + margin, lim)
    y0, y1 = max(b["minY"] - margin, -lim), min(b["maxY"] + margin, lim)
    cell = max(MIN_CELL_M, max(x1 - x0, y1 - y0) / MAX_CELLS)
    nx, ny = max(1, math.ceil((x1 - x0) / cell)), max(1, math.ceil((y1 - y0) / cell))

    # The natural ground, scaled exactly as the pipeline scaled it (same routes, same grade limit).
    ground = el.Terrain(model["slug"], model["elevation"])
    grade_routes: list[tuple[np.ndarray, np.ndarray, bool]] = []
    xys, zs, hws = [], [], []
    for r in model["routes"]:
        samples = r["samples"]
        p = np.array([[q["x"], q["y"]] for q in samples], dtype=np.float64)
        s = np.array([q["s"] for q in samples], dtype=np.float64)
        grade_routes.append((p, s, bool(r["closed"])))
        keep = ~_bridge_mask(model, r, s)
        xys.append(p[keep])
        zs.append(np.array([q["z"] for q in samples], dtype=np.float64)[keep])
        hws.append(np.array([q["width"] for q in samples], dtype=np.float64)[keep] / 2.0)
    ground.limit_grade(grade_routes)
    road_xy = np.concatenate(xys)
    road_z = np.concatenate(zs)
    road_hw = np.concatenate(hws)

    xs = x0 + np.arange(nx + 1) * cell
    ys = y0 + np.arange(ny + 1) * cell
    gx, gy = np.meshgrid(xs, ys)  # row j = y index
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
    z = ground.height(pts)
    if len(road_xy):
        near_band = 3.0 + cell * 0.75
        for a in range(0, len(pts), 512):
            p = pts[a : a + 512]
            d = np.hypot(p[:, None, 0] - road_xy[None, :, 0], p[:, None, 1] - road_xy[None, :, 1]) - road_hw[None, :]
            e_min = d.min(axis=1)
            band = d <= e_min[:, None] + near_band
            zr = np.where(band, road_z[None, :], np.inf).min(axis=1) - SINK_M
            t = _smoothstep((e_min - CORE_M) / BLEND_M)
            z[a : a + 512] = zr * (1.0 - t) + z[a : a + 512] * t
    return {
        "originX": round(float(x0), 2),
        "originY": round(float(y0), 2),
        "cellM": round(float(cell), 4),
        "nx": int(nx),
        "ny": int(ny),
        "z": [round(float(v), 2) for v in z],
    }
