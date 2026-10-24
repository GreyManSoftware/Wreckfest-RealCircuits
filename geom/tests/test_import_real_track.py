"""tools/import_real_track.py: the parts that need no downloads (projection, stitching, registration, fits)."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

_PATH = Path(__file__).resolve().parents[2] / "tools" / "import_real_track.py"
_spec = importlib.util.spec_from_file_location("import_real_track", _PATH)
irt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(irt)


def test_utm30_projection_and_scale() -> None:
    # ETRS89 / UTM 30N (Spain's IGN MDT): on the central meridian (3 W) the easting is 500 km and the northing the
    # meridian arc times 0.9996 (integrated here); the scale factor grows off the meridian
    irt.set_projection({"dtm": "es-mdt05"})
    try:
        e, n = irt.to_grid(40.0, -3.0)
        a, b = irt.GRS80
        e2 = 1 - b * b / (a * a)
        phi = np.linspace(0.0, math.radians(40.0), 20001)
        m = np.trapezoid(a * (1 - e2) / (1 - e2 * np.sin(phi) ** 2) ** 1.5, phi)
        assert e == pytest.approx(500000.0, abs=1e-6) and n == pytest.approx(0.9996 * m, abs=0.01)
        assert irt.grid_scale_at(40.0, -3.0) == pytest.approx(0.9996, abs=1e-9)
        assert irt.grid_scale_at(42.56, -2.17) > 0.9996
    finally:
        irt.set_projection({})
    assert irt.to_grid(53.31, -0.063) == irt.to_bng(53.31, -0.063)  # back on the National Grid


def test_parse_arcgrid_reads_a_wcs_multipart_response() -> None:
    text = ("--wcs\nContent-Type: application/asc\n\nncols 3\nnrows 2\nxllcorner 100.0\nyllcorner 200.0\n"
            "cellsize 5.0\nNODATA_value -9999\n 1 2 3\n 4 -9999 6\n--wcs--\n")
    x0, y1, cs, z = irt.parse_arcgrid(text)
    assert (x0, y1, cs) == (100.0, 210.0, 5.0) and z.shape == (2, 3) and math.isnan(z[1, 1]) and z[0, 2] == 3.0


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
    assert [m[1] for m in marks] == [0, 2, 3]  # each way's first point (shared with the previous way's last)
    with pytest.raises(ValueError, match="doesn't start"):
        irt.stitch(osm, [1, 3, 2])
    with pytest.raises(ValueError, match="doesn't close"):
        irt.stitch(_osm([(1, [1, 2, 3]), (2, [3, 4])]), [1, 2])
    # [way, from, to] takes part of a way (an alternative layout leaving it half way)
    osm = _osm([(1, [1, 2, 3, 4]), (2, [2, 5, 6]), (3, [6, 1])])
    pts, _ = irt.stitch(osm, [[1, 1, 2], 2, 3])
    assert len(pts) == 4
    # [way, from, to] with from after to drives that part backwards (a link mapped the other way)
    osm = _osm([(1, [1, 2, 3]), (2, [4, 3]), (3, [4, 1])])
    pts, _ = irt.stitch(osm, [1, [2, 3, 4], 3])
    assert len(pts) == 4 and pts[3] == (53 + 4e-4, 4e-4)


def test_stitch_takes_parts_of_closed_loops_either_way_through_the_closing_node() -> None:
    # Bedford: each circuit is one closed way; a layout takes a part of a loop, forwards or backwards
    osm = _osm([(1, [1, 2, 3, 4, 5, 1]), (2, [4, 9, 2])])
    w = osm["elements"][0]
    assert irt.way_part(w, [1, 4, 2, "fwd"])[0] == [4, 5, 1, 2]       # through the closing node
    assert irt.way_part(w, [1, 2, 4, "rev"])[0] == [2, 1, 5, 4]
    assert irt.way_part(w, [1, 3, 3, "fwd"])[0] == [3, 4, 5, 1, 2, 3]  # the whole loop from 3
    with pytest.raises(ValueError, match="closed"):
        irt.way_part(osm["elements"][1], [2, 4, 2, "fwd"])
    _, _, ids = irt.stitch_nodes(osm, [[1, 2, 4, "fwd"], 2])
    assert ids == [2, 3, 4, 9]
    # nodeJoins: a way starting at a node a few metres from the previous way's end (not joined in OSM)
    o2 = _osm([(1, [1, 2, 3]), (2, [8, 4]), (3, [4, 1])])
    with pytest.raises(ValueError, match="doesn't start"):
        irt.stitch_nodes(o2, [1, 2, 3])
    _, _, j_ids = irt.stitch_nodes(o2, [1, 2, 3], joins=[[3, 8]])
    assert j_ids == [1, 2, 3, 4]
    # non_main_span works on segments: the alternative uses another part of the way the main lap drives whole
    m_ll, _, m_ids = irt.stitch_nodes(osm, [1])
    ll, _, a_ids = irt.stitch_nodes(osm, [[1, 1, 4, "fwd"], [2, 4, 2], [1, 2, 1, "rev"]])  # 1-2-3-4, 4-9-2, 2-1
    assert a_ids == [1, 2, 3, 4, 9, 2]
    a, b = irt.non_main_span(ll, a_ids, m_ids)
    assert 0.0 < a < b <= 1.0  # one run: the link and 2-1 driven against the main lap
    with pytest.raises(ValueError, match="only the main"):
        irt.non_main_span(m_ll, m_ids, m_ids)


def test_branch_of_cuts_the_part_that_leaves_the_main_lap() -> None:
    # main: a 200 x 100 m rectangle-ish loop; alt: the same loop cut short by a chord across the middle
    t = np.linspace(0, 2 * math.pi, 400, endpoint=False)
    main = irt.resample_closed(np.column_stack([100 * np.cos(t), 50 * np.sin(t)]), 2.0)
    keep = main[:, 0] < 0.0
    alt = np.vstack([main[keep], np.column_stack([np.zeros(20), np.linspace(-50, 50, 20)])])
    alt = irt.resample_closed(alt[np.argsort(np.arctan2(alt[:, 1], alt[:, 0] - 30))], 2.0)
    br = irt.branch_of(alt, main, 2.0)
    for end in (br[0], br[-1]):
        assert np.min(np.hypot(*(main - end).T)) < 1e-6  # ends lie on main points
    assert np.max(np.min(np.hypot(br[:, None, 0] - main[None, :, 0], br[:, None, 1] - main[None, :, 1]), axis=1)) > 20
    with pytest.raises(ValueError, match="doesn't share"):
        irt.branch_of(main + 500.0, main, 2.0)


def _hairpin_case() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cadwell Woodlands: a main loop whose two straights run 9 m apart (cambered 1.8 and 4 degrees), and a
    branch that leaves the one along it, turns back through a 4.5 m hairpin and joins the other."""
    x = np.arange(-200.0, 200.0, 2.0)
    arc_ = lambda cx, a0: np.column_stack([cx + 4.5 * np.cos(np.linspace(a0, a0 + math.pi, 8)[1:-1]),  # noqa: E731
                                           4.5 * np.sin(np.linspace(a0, a0 + math.pi, 8)[1:-1])])
    main = np.vstack([np.column_stack([x, np.full_like(x, -4.5)]), arc_(200.0, -math.pi / 2),
                      np.column_stack([x[::-1], np.full_like(x, 4.5)]), arc_(-200.0, math.pi / 2)])
    bank_m = np.where(main[:, 1] < 0, -1.8, -4.0)
    th = np.linspace(-math.pi / 2, math.pi / 2, 9)
    br = np.vstack([np.column_stack([np.arange(-20.0, 0.0, 2.0), np.full(10, -4.5)]),
                    np.column_stack([4.5 * np.cos(th), 4.5 * np.sin(th)]),
                    np.column_stack([np.arange(-2.0, -21.0, -2.0), np.full(10, 4.5)])])
    return irt.resample_open(br, 2.0), main, bank_m


def _departure_case() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Oulton's Fosters link: a 100 m branch leaving and rejoining a circle (R 200 m, cambered 4 degrees) at about
    50 degrees."""
    t = np.linspace(0, 2 * math.pi, 628, endpoint=False)
    main = np.column_stack([200 * np.cos(t), 200 * np.sin(t)])
    th = np.linspace(0.0, 0.5, 60)
    r = 200 + 40 * np.sin(math.pi * th / 0.5)
    return irt.resample_open(np.column_stack([r * np.cos(th), r * np.sin(th)]), 2.0), main, np.full(len(main), -4.0)


@pytest.mark.parametrize("case", [_hairpin_case, _departure_case], ids=["hairpin", "departure"])
def test_branch_joins_take_the_main_surface_without_a_camber_flick(case) -> None:
    # merge_onto_main + join_blend as build() runs them on a branch cambered 1 degree the other way: its ends take
    # the main road's camber, its own away from them. The roads' overlap is crossed in a few metres sideways, and
    # in a hairpin the nearest main road changes from the one straight to the other: fading the main surface over
    # half a width sideways flicked the camber at 0.77 deg/m (Cadwell Woodlands) and 0.40 (Oulton's Fosters link)
    br, main, bank_m = case()
    s = irt.arc(br)
    z_b, bank_b = irt.merge_onto_main(br, np.zeros(len(br)), np.full(len(br), 1.0), main, np.zeros(len(main)),
                                      bank_m, 10.0)
    assert np.abs(np.gradient(bank_b, s)).max() <= irt.MERGE_MAX_BANK_RATE + 0.01
    j0, j1 = (int(np.argmin(np.hypot(*(main - br[e]).T))) for e in (0, -1))
    z_b, bank_b = irt.join_blend(s, z_b, bank_b, (0.0, 0.0), (bank_m[j0], bank_m[j1]))
    assert (bank_b[0], bank_b[-1]) == pytest.approx((bank_m[j0], bank_m[j1]))
    assert np.abs(np.gradient(bank_b, s)).max() <= ARTEFACT_LIMITS["maxCamberRateDegPerM"]
    if s[-1] > 80:
        assert bank_b.max() == pytest.approx(1.0)  # its own camber away from the joins

def _road_dtm(offset: float, bank: float, drift: float = 0.0) -> irt.Dtm:
    """A hillside (5 % across x) with a flat 10 m road strip along y at x = offset (+ drift * y), cambered by
    ``bank``."""
    xs = np.arange(-60, 60) + 0.5
    ys = np.arange(-200, 200)[::-1] + 0.5
    gx, gy = np.meshgrid(xs, ys)
    offset = offset + drift * gy
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
    # an OSM line drawn skew to the road (2 m over 300 m): the shift follows it smoothly. It used to step in
    # 0.5 m increments smoothed over 5 m, a sideways jink every few tens of metres on a straight (2026-10-24 audit:
    # Cadwell's Park Straight wandered 1 m over 20 m)
    dtm = _road_dtm(2.5, 0.0, drift=2.0 / 300.0)
    shift = irt.register(dtm, p, nl, 9.5, 2.0)
    inner = slice(40, -40)  # away from the ends of the open test line (the smoothing wraps round a loop)
    assert np.abs(shift[inner] + (2.5 + 2.0 / 300.0 * p[inner, 1])).max() < 0.8  # the road is 0.5 m wider than the fit
    assert np.abs(np.diff(shift[inner])).max() < 0.04  # per 2 m sample: no steps


def test_lakes_take_the_lidar_water_level_and_keep_clear_of_the_road() -> None:
    # a 5 % hillside with a flat 60 x 40 m pond at 2.0 m; a second pond the road runs through; a third too small
    xs = np.arange(-100, 100) + 0.5
    ys = np.arange(-100, 100)[::-1] + 0.5
    gx, gy = np.meshgrid(xs, ys)
    z = 5.0 + 0.05 * gx
    pond = (np.abs(gx - 20) < 30) & (np.abs(gy - 40) < 20)
    z = np.where(pond, 2.0, z)
    z[(gx > 40) & (gx < 45) & (gy > 25) & (gy < 30)] = np.nan  # a few no-data cells on the water
    dtm = irt.Dtm(-100.0, 100.0, 1.0, z)
    sq = lambda x0, y0, w, h: np.array([[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]], dtype=float)  # noqa: E731
    road = np.column_stack([np.linspace(-90, 90, 91), np.full(91, -50.0)])
    water = [("Pond", sq(-10, 20, 60, 40)), ("Crossed", sq(-30, -60, 40, 20)), ("Puddle", sq(80, 80, 5, 5))]
    lakes = irt.lakes_from(water, dtm, road, 500.0, 9.0)
    assert [lk["name"] for lk in lakes] == ["Pond"]
    assert lakes[0]["level"] == pytest.approx(2.0, abs=1e-6)
    assert lakes[0]["areaM2"] == pytest.approx(2400.0)
    # a pond within clear_m of the road (but not crossed by it) is left out too
    near = irt.lakes_from([("Near", sq(-10, -45, 40, 20))], dtm, road, 500.0, 9.0)
    assert near == []


def test_corner_easing_reaches_the_rule_radius() -> None:
    t = np.linspace(0, 2 * math.pi, 200, endpoint=False)
    sq = np.column_stack([np.clip(np.cos(t) * 2, -1, 1), np.clip(np.sin(t) * 2, -1, 1)]) * 60.0  # a rounded square
    p = irt.resample_closed(sq, 2.0)
    assert irt.radii(p).min() < 10.0
    q, moved = irt.ease_corners(p, 11.0)
    assert irt.radii(q).min() >= 11.0 * 0.999
    assert 0 < moved < 8.0


def test_sharp_ways_keep_a_chicanes_corners() -> None:
    # a 600 x 300 m loop with a bus-stop chicane on its long bottom straight: 12 m right, 25 m along, 12 m left
    pts = [(0, 0), (250, 0), (262, -12), (287, -12), (299, 0), (600, 0), (600, 300), (0, 300)]
    bng = irt.resample_closed(np.array(pts, dtype=float), 5.0)
    k = np.flatnonzero((bng[:, 0] > 245) & (bng[:, 0] < 305) & (bng[:, 1] < 1))
    marks = [(1, 0, ""), (2, int(k[0]), "chicane"), (3, int(k[-1]) + 1, "")]
    flat = irt.Dtm(-200.0, 500.0, 2.0, np.zeros((400, 500)))

    def chicane_radius(sharp) -> float:
        p, _, _ = irt.fit_loop(bng, flat, 10.0, 2.0, 0.0, sharp)
        sel = (p[:, 0] > 240) & (p[:, 0] < 310) & (p[:, 1] < 5)
        return float(irt.radii(p)[sel].min())

    assert irt.sharp_segments(marks, len(bng), [2]).sum() == len(k)
    smooth, sharp = chicane_radius(None), chicane_radius(irt.sharp_segments(marks, len(bng), [2]))
    assert smooth > 20.0  # the 5 m smoothing makes it a gentle S (Oulton Park, 2026-10-23)
    assert sharp < 0.6 * smooth and sharp >= (5.0 + irt.IMPORTED_MIN_INNER_EDGE_RADIUS_M) * 0.999  # rule radius


_ROOT = Path(__file__).resolve().parents[2]
_RECIPES = sorted(p for p in (_ROOT / "tools" / "real_tracks").glob("*.json")
                  if "expectedCornerOrder" in json.loads(p.read_text(encoding="utf-8")))


def test_corner_order_problem_accepts_a_rotation_and_rejects_the_reverse() -> None:
    want = ["A", "B", "C", "D"]
    assert irt.corner_order_problem(["x", "A", "B", "C", "D"], want) is None
    assert irt.corner_order_problem(["C", "D", "A", "B"], want) is None  # the lap may start mid-cycle
    assert irt.corner_order_problem(["A", "C", "D"], want) is None       # B not labelled
    assert "reversed" in irt.corner_order_problem(["D", "C", "B", "A"], want)
    assert "labelled" in irt.corner_order_problem(["A"], want)


def test_recipe_slug_is_the_plain_track_name() -> None:
    assert irt.recipe_slug({"name": "Cadwell Park"}) == "cadwellpark"
    assert irt.recipe_slug({"name": "Brands Hatch", "slug": "brandshatch"}) == "brandshatch"
    assert irt.recipe_slug({"name": "Circuito de Navarra", "slug": "navarra"}) == "navarra"  # the name's last word
    with pytest.raises(ValueError, match="Real"):
        irt.recipe_slug({"name": "RealCadwell"})
    with pytest.raises(ValueError, match="default"):
        irt.recipe_slug({"name": "Cadwell Park", "slug": "realcadwell"})


def test_recipe_without_verified_sources_fails() -> None:
    ok = {k: "an official circuit map" for k in irt.SOURCE_KEYS}
    assert irt.source_problems({**ok, "altLayouts": [{"id": "club", **ok}]}) == []
    probs = irt.source_problems({**ok, "directionSource": "", "altLayouts": [{"id": "club", "layoutSource": "x" * 20}]})
    assert probs == ["main layout: no directionSource", "layout club: no directionSource",
                     "layout club: no startFinishSource"]
    with pytest.raises(ValueError, match="sources"):
        irt.build({"name": "Cadwell Park", **ok, "startFinishSource": None}, Path("."))


def _lap(model, secs: list[dict]) -> np.ndarray:
    """A layout's lap as points in driving order from its start/finish line (sections of the model's routes)."""
    out = []
    for sec in secs:
        r = model.route(sec["route"])
        out += [[q.x, q.y] for q in r.samples if sec["fromS"] - 1e-6 <= q.s <= sec["toS"] + 1e-6]
    return np.array(out)


@pytest.mark.parametrize("recipe_path", _RECIPES, ids=[p.stem for p in _RECIPES])
def test_imported_sample_drives_the_recipes_corner_order_in_every_layout(recipe_path: Path) -> None:
    """The committed sample, built into a TrackModel, is driven (increasing s from the start line, which the
    builder's layouts and the built-.scne driving-order check follow) through the published corner order, the
    right way round, in the main and every alternative layout; every layout has a 24-car grid on the tarmac of
    its own lap, ahead of its line and facing the driving direction, its line where the recipe puts it, a lap
    within 3 % of the published length, and the recipe names the source of its route, direction and line."""
    from trackgen_geom import build_track_model

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    assert irt.source_problems(recipe) == []
    spec = json.loads((_ROOT / "geom" / "tests" / "samples" / f"{irt.recipe_slug(recipe)}.json").read_text(encoding="utf-8"))
    res = build_track_model(spec)
    assert res.model is not None and res.valid, [e.message for e in res.errors]
    model = res.model
    alts = {a["id"]: a for a in recipe.get("altLayouts", [])}
    spec_alts = {a["id"]: a for a in spec["sketch"]["imported"].get("altLayouts", [])}
    assert set(alts) == set(spec_alts) == {lay.id for lay in model.layouts if lay.id != "main"}
    layouts = model.layouts or [None]
    corners = sorted(spec["sketch"]["imported"]["corners"], key=lambda c: c["sM"])
    hw = float(spec["options"]["widthM"]) / 2.0
    for lay in layouts:
        secs = lay.sections if lay else [{"route": "main", "fromS": 0.0, "toS": model.lap_length_m}]
        src = alts.get(lay.id, recipe) if lay else recipe
        xy = _lap(model, secs)
        if recipe.get("clockwise") is not None:
            assert (irt.signed_area(xy) < 0) == bool(src.get("clockwise", recipe["clockwise"])), lay.id if lay else "main"
        if src.get("publishedLapLengthM"):
            lap = lay.lap_length_m if lay else model.lap_length_m
            # within 3 %, unless the recipe explains a larger gap (OSM provably short or the published figure off)
            tol = float(src.get("lapLengthTolerancePct", 3.0))
            assert tol <= 3.0 or len(src.get("lapLengthNote", "")) >= 40, (src.get("id"), "no lapLengthNote")
            assert abs(lap / src["publishedLapLengthM"] - 1.0) <= tol / 100.0, (src.get("id"), lap)
        # the line: the recipe's own startFinish gives the layout its own line and grid, else it shares main's
        own = lay is not None and lay.id in spec_alts and "startFinish" in spec_alts[lay.id]
        assert own == (lay is not None and lay.grid is not None and lay.start_finish is not None)
        if own:
            want = spec_alts[lay.id]["startFinish"]
            assert math.hypot(lay.start_finish["position"][0] - want["x"], lay.start_finish["position"][1] - want["y"]) < 2.0
            assert np.hypot(*(xy[0] - [want["x"], want["y"]])) < 2.5  # the lap starts at it
        else:
            assert np.hypot(*(xy[0] - [model.route("main").samples[0].x, model.route("main").samples[0].y])) < 1e-6
        # the game races up to 24 cars and puts any without a start point beside the grid (on the grass)
        grid = lay.grid if own else model.grid
        assert spec["options"]["gridSize"] == 24 == len(grid.slots)
        tangent = np.gradient(xy, axis=0)
        for sl in grid.slots:
            d = np.hypot(*(xy - np.array(sl.position[:2])).T)
            k = int(np.argmin(d))
            assert float(d[k]) + 1.0 <= hw, (sl.name, float(d[k]))  # a 2 m wide car fully on the tarmac
            assert 0.0 < sl.s < 0.2 * (lay.lap_length_m if lay else model.lap_length_m)  # just ahead of the line
            fwd = math.atan2(tangent[k, 1], tangent[k, 0])
            assert abs((sl.heading - fwd + math.pi) % (2 * math.pi) - math.pi) < math.radians(20), sl.name
        names = [c["name"] for sec in secs if sec["route"] == "main"
                 for c in corners if sec["fromS"] <= c["sM"] <= sec["toS"]]
        expected = recipe["expectedCornerOrder"]
        if len(secs) > 1:  # an alternative layout skips part of the lap: only the corners it passes count
            expected = [n for n in expected if n in names]
        assert irt.corner_order_problem(names, expected) is None, (secs, names)


# Profile artefact limits for every committed real-track sample (2026-10-24 accuracy audit). The worst real values
# when they were set: grade ripple RMS 0.31 % (Cadwell Park's hilly straights), wobble 0.34 m (Brands Hatch),
# camber rate 0.27 deg/m (Cadwell Park), tightest crest/dip 93 m (Navarra Short's ramp onto the T7-T8 road).
# Navarra's whole-metre MDT05 terraces read 0.53 % ripple RMS.
ARTEFACT_LIMITS = {"straightGradeRippleRmsPct": 0.4, "straightWobbleMaxM": 0.5, "maxCamberRateDegPerM": 0.35}
MIN_VERTICAL_RADIUS_M = 75.0


def test_profile_artefacts_flag_terraces_wobble_and_camber_flips() -> None:
    # a 1 km straight with a 2 % climb: smooth, then as a DTM with 1 m terraces (smoothed 10 m like the importer)
    s = np.arange(0.0, 1000.0, 2.0)
    xy = np.column_stack([s, np.zeros_like(s)])
    smooth = 0.02 * s
    terraced = irt.gauss_open(np.floor(smooth + 0.5), 5.0)
    bank = np.full_like(s, 1.0)
    good = irt.profile_artefacts(xy, smooth, bank, closed=False)
    bad = irt.profile_artefacts(xy, terraced, bank, closed=False)
    assert good["straightsM"] > 800 and good["straightGradeRippleRmsPct"] < 0.05 and good["straightCamberFlips"] == 0
    assert bad["straightGradeRippleRmsPct"] > ARTEFACT_LIMITS["straightGradeRippleRmsPct"]
    # a 0.8 m lateral wobble (20 m wavelength, OSM-node jitter) on the straight, and camber flipping sign along it
    wob = irt.profile_artefacts(np.column_stack([s, 0.8 * np.sin(2 * np.pi * s / 20.0)]), smooth, bank, closed=False)
    assert wob["straightWobbleMaxM"] > ARTEFACT_LIMITS["straightWobbleMaxM"]
    flip = irt.profile_artefacts(xy, smooth, 1.5 * np.sign(np.sin(2 * np.pi * s / 100.0)), closed=False)
    assert flip["straightCamberFlips"] >= 10 and flip["maxCamberRateDegPerM"] > ARTEFACT_LIMITS["maxCamberRateDegPerM"]


@pytest.mark.parametrize("recipe_path", _RECIPES, ids=[p.stem for p in _RECIPES])
def test_imported_sample_has_no_profile_artefacts(recipe_path: Path) -> None:
    """Every layout line of the committed sample: straights straight, no grade ripple (DTM terraces, tile seams),
    no camber flicking, no crest or dip tighter than 75 m (DTM spikes, bridges)."""
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    im = json.loads((_ROOT / "geom" / "tests" / "samples" / f"{irt.recipe_slug(recipe)}.json")
                    .read_text(encoding="utf-8"))["sketch"]["imported"]
    lines = [("main", im["centreline"], True)] + [(a["id"], a["centreline"], False) for a in im.get("altLayouts", [])]
    for name, cl, closed in lines:
        c = np.array([[p["x"], p["y"], p["z"], p["bankDeg"]] for p in cl])
        m = irt.profile_artefacts(c[:, :2], c[:, 2], c[:, 3], closed)
        for k, lim in ARTEFACT_LIMITS.items():
            assert m[k] <= lim, (name, k, m)
        assert min(m["minCrestRadiusM"], m["minDipRadiusM"]) >= MIN_VERTICAL_RADIUS_M, (name, m)
    # a branch meets the main line along it, not at a kink (Navarra Short's hook ended at 47 degrees, Bedford SEN's
    # ends at 43 and 27: fillet_ends); over its last 6 m, at most 25 degrees off the main line's direction
    main = np.array([[p["x"], p["y"]] for p in im["centreline"]])
    for a in im.get("altLayouts", []):
        b = np.array([[p["x"], p["y"]] for p in a["centreline"]])
        for end, chord in ((0, b[3] - b[0]), (-1, b[-1] - b[-4])):
            j = int(np.argmin(np.hypot(*(main - b[end]).T)))
            tm = main[(j + 2) % len(main)] - main[(j - 2) % len(main)]
            ang = math.degrees(math.acos(float(np.clip(tm @ chord / np.hypot(*tm) / np.hypot(*chord), -1.0, 1.0))))
            assert ang <= 25.0, (a["id"], end, ang)


def _lap_profile(model, secs: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A layout's lap resampled every 1 m: (x, y) points, heights and camber (degrees), closed."""
    pts = []
    for sec in secs:
        for q in model.route(sec["route"]).samples:
            if sec["fromS"] - 1e-6 <= q.s <= sec["toS"] + 1e-6 and (not pts or math.hypot(q.x - pts[-1][0], q.y - pts[-1][1]) > 0.3):
                pts.append((q.x, q.y, q.z, math.degrees(q.banking)))
    a = np.array(pts)
    s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(np.vstack([a[:, :2], a[:1, :2]]), axis=0).T))])
    t = np.arange(0.0, s[-1], 1.0)
    c = [np.interp(t, s, np.concatenate([a[:, k], a[:1, k]])) for k in range(4)]
    return np.column_stack(c[:2]), c[2], c[3]


@pytest.mark.parametrize("recipe_path", _RECIPES, ids=[p.stem for p in _RECIPES])
def test_imported_sample_has_no_wobble_height_steps_or_camber_flips(recipe_path: Path) -> None:
    """Artefact classes found by the 2026-10-24 accuracy audit (docs/real-tracks.md, "Audit notes"), on every
    layout of the committed sample:

    - straight wobble: on a straight (30 m-scale curvature under 1/300 m for 80 m or more) no 6 m-scale bend
      tighter than R 130 m; the registration shift's 0.5 m steps gave Cadwell's straights R 115 m jinks;
    - height steps: the grade changes by at most 12 % over 10 m (a vertical radius of 83 m or more; the sharpest
      real ones are Navarra's back-straight hump, 10 %, and Cadwell's Mountain crest, 8 %): a bridge deck, a DTM
      gap or a registration jump would show here;
    - camber flips: on straights the camber stays within 1.5 degrees of its 25 m-scale mean, and nowhere does it
      change faster than 0.5 degrees per metre."""
    from trackgen_geom import build_track_model

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    spec = json.loads((_ROOT / "geom" / "tests" / "samples" / f"{irt.recipe_slug(recipe)}.json").read_text(encoding="utf-8"))
    model = build_track_model(spec).model
    for lay in model.layouts or []:
        xy, z, bank = _lap_profile(model, lay.sections)
        n = len(z)
        d = np.roll(xy, -3, axis=0) - np.roll(xy, 3, axis=0)
        th = np.arctan2(d[:, 1], d[:, 0])
        k = ((np.roll(th, -3) - np.roll(th, 3) + math.pi) % (2 * math.pi) - math.pi) / 6.0  # 1/R per metre
        flat = np.abs(irt.gauss_closed(k, 15.0)) < 1 / 300.0
        straight = np.zeros(n, dtype=bool)
        if not flat.all():
            k0 = int(np.flatnonzero(~flat)[0])
            run: list[int] = []
            for i in range(n + 1):
                j = (k0 + i) % n
                if i < n and flat[j]:
                    run.append(j)
                    continue
                if len(run) >= 80:
                    straight[run] = True
                run = []
        where = lambda m: f"{lay.id} at s {int(np.argmax(m))} m"  # noqa: E731
        bend = np.abs(irt.gauss_closed(k, 3.0)) * straight
        assert bend.max() <= 1 / 130.0, (where(bend), 1 / bend.max())
        dg = np.abs((np.roll(z, -10) - z) - (z - np.roll(z, 10))) / 10.0
        assert dg.max() <= 0.12, (where(dg), dg.max())
        flip = np.abs(bank - irt.gauss_closed(bank, 25.0)) * straight
        assert flip.max() <= 1.5, (where(flip), flip.max())
        rate = np.abs(np.roll(bank, -2) - np.roll(bank, 2)) / 4.0
        assert rate.max() <= 0.5, (where(rate), rate.max())
