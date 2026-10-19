"""The 3D preview's terrain grid (trackgen_geom.terrain)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from trackgen_geom import build_track_model
from trackgen_geom.terrain import MAX_CELLS, SINK_M, terrain_grid

SAMPLES = Path(__file__).parent / "samples"


def _grid(name: str) -> tuple[dict, dict]:
    result = build_track_model(json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8")))
    assert result.model is not None
    model = result.model.to_dict()
    return model, terrain_grid(model)


def _at(t: dict, x: float, y: float) -> float:
    """Bilinear terrain height at (x, y)."""
    fx, fy = (x - t["originX"]) / t["cellM"], (y - t["originY"]) / t["cellM"]
    i, j = int(fx), int(fy)
    u, v = fx - i, fy - j
    z = np.array(t["z"]).reshape(t["ny"] + 1, t["nx"] + 1)
    return float((1 - u) * (1 - v) * z[j, i] + u * (1 - v) * z[j, i + 1] + (1 - u) * v * z[j + 1, i] + u * v * z[j + 1, i + 1])


@pytest.mark.parametrize("name", ["hilly_circuit", "hill_markers", "figure8_bridge"])
def test_grid_covers_the_track_and_follows_the_road(name: str) -> None:
    model, t = _grid(name)
    b = model["bounds"]
    assert len(t["z"]) == (t["nx"] + 1) * (t["ny"] + 1)
    assert max(t["nx"], t["ny"]) <= MAX_CELLS
    assert t["originX"] <= b["minX"] and t["originX"] + t["nx"] * t["cellM"] >= b["maxX"]
    assert t["originY"] <= b["minY"] and t["originY"] + t["ny"] * t["cellM"] >= b["maxY"]
    # Under the main road (away from bridges) the ground sits just below the road surface.
    main = model["routes"][0]
    bridge_s = [c["branches"][c["overBranch"]]["s"] for c in model["crossings"] if c["type"] == "bridge"]
    gaps = []
    for q in main["samples"][::10]:
        if any(abs(q["s"] - s) < 80 for s in bridge_s):
            continue
        gaps.append(q["z"] - _at(t, q["x"], q["y"]))
    # Coarse (8 m+) cells and bilinear interpolation put the ground a little lower than the worker's
    # 4 m grid does, but never above the road by more than a few centimetres.
    assert SINK_M - 0.05 < float(np.median(gaps)) < 1.0, gaps
    assert min(gaps) > -0.25, gaps


def test_ground_stays_under_a_bridge_deck() -> None:
    model, t = _grid("figure8_bridge")
    c = next(c for c in model["crossings"] if c["type"] == "bridge")
    over = c["branches"][c["overBranch"]]
    x, y = c["position"]
    assert over["z"] - _at(t, x, y) > 4.0


def test_deterministic() -> None:
    assert _grid("hilly_circuit")[1] == _grid("hilly_circuit")[1]
