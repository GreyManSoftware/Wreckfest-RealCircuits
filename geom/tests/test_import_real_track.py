"""tools/import_real_track.py: the parts that need no downloads (projection, stitching, registration, fits)."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

_PATH = Path(__file__).resolve().parents[2] / "tools" / "import_real_track.py"
_spec = importlib.util.spec_from_file_location("import_real_track", _PATH)
irt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(irt)


def test_national_grid_projection_matches_the_os_worked_example() -> None:
    # Ordnance Survey, "A guide to coordinate systems in Great Britain", annex C: OSGB36 52 39 27.2531 N,
    # 1 43 4.5177 E -> E 651409.903, N 313177.270
    lat = math.radians(52 + 39 / 60 + 27.2531 / 3600)
    lon = math.radians(1 + 43 / 60 + 4.5177 / 3600)
    e, n = irt.tm_project(lat, lon)
    assert (e, n) == pytest.approx((651409.903, 313177.270), abs=0.005)
    # and WGS84 -> OSGB36 moves a Lincolnshire point by the expected ~100 m datum shift
    e2, n2 = irt.to_bng(53.31, -0.063)
    assert 528000 < e2 < 530000 and 380000 < n2 < 382000


def _osm(ways: list[tuple[int, list[int]]]) -> dict:
    return {"elements": [{"type": "way", "id": wid, "nodes": nodes, "tags": {"name": f"w{wid}"},
                          "geometry": [{"lat": 53 + k * 1e-4, "lon": k * 1e-4} for k in nodes]}
                         for wid, nodes in ways]}


def test_stitch_joins_ways_in_order_and_checks_continuity() -> None:
    osm = _osm([(1, [1, 2, 3]), (2, [3, 4]), (3, [4, 1])])
    pts, marks = irt.stitch(osm, [1, 2, 3])
    assert len(pts) == 4 and [m[0] for m in marks] == [1, 2, 3]
    with pytest.raises(ValueError, match="doesn't start"):
        irt.stitch(osm, [1, 3, 2])
    with pytest.raises(ValueError, match="doesn't close"):
        irt.stitch(_osm([(1, [1, 2, 3]), (2, [3, 4])]), [1, 2])


def _road_dtm(offset: float, bank: float) -> irt.Dtm:
    """A hillside (5 % across x) with a flat 10 m road strip along y at x = offset, cambered by ``bank``."""
    xs = np.arange(-60, 60) + 0.5
    ys = np.arange(-200, 200)[::-1] + 0.5
    gx, _ = np.meshgrid(xs, ys)
    z = 0.05 * gx
    road = np.abs(gx - offset) <= 5.0
    z = np.where(road, 0.05 * offset + math.tan(math.radians(bank)) * -(gx - offset), z)
    z = np.where((np.abs(gx - offset) > 5.0) & (np.abs(gx - offset) < 7.0), z + 0.4, z)  # verge banks
    return irt.Dtm(-60.0, 200.0, 1.0, z)


def test_registration_finds_the_road_and_the_fit_its_camber() -> None:
    # OSM line along y at x = 0 (heading north, left = -x); the real road is 2.5 m east of it
    dtm = _road_dtm(2.5, 3.0)
    p = np.column_stack([np.zeros(150), np.linspace(-150, 148, 150)])
    nl = np.tile([-1.0, 0.0], (150, 1))
    shift = irt.register(dtm, p, nl, 9.5, 2.0)
    assert np.median(shift) == pytest.approx(-2.5, abs=0.5)  # along the left normal (-x)
    q = p + np.median(shift) * nl
    _, b, res = irt.cross_fit(dtm, q, nl, np.arange(-3.8, 3.81, 0.5))
    assert math.degrees(math.atan(np.median(b))) == pytest.approx(3.0, abs=0.2)  # left (-x) edge higher
    assert np.median(res) < 0.02


def test_corner_easing_reaches_the_rule_radius() -> None:
    t = np.linspace(0, 2 * math.pi, 200, endpoint=False)
    sq = np.column_stack([np.clip(np.cos(t) * 2, -1, 1), np.clip(np.sin(t) * 2, -1, 1)]) * 60.0  # a rounded square
    p = irt.resample_closed(sq, 2.0)
    assert irt.radii(p).min() < 10.0
    q, moved = irt.ease_corners(p, 11.0)
    assert irt.radii(q).min() >= 11.0 * 0.999
    assert 0 < moved < 8.0
