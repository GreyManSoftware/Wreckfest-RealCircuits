"""Out-of-bounds checks (wfbuild/bounds.py), shared by check_scene.py (the built .blend files, in Blender) and
check_export.py (the .scne files bgeometry built, plain Python):

- every layout has a ``#resetplayer`` reset wall + lid that a car can't get past without touching it: from the
  layout's road, the area reachable without crossing a reset face never reaches the edge of the collision ground
  (and the infield stays reachable);
- there is collidable ground under every bit of that area (no holes);
- the reset stands at least RESET_FACTOR x RESET_GAP outside every layout's blue AI route (which holds its road and
  AI lines), so no reset touches any layout's road;
- (scene check) every reset vertex is at least RESET_FACTOR times as far from the nearest road edge as the first
  walls were (RESET_GAP beyond the blue route there) and at least RESET_MIN_M, except near the terrain's edge;
- a ``#resetplayer`` kill floor lies at least KILL_MARGIN below every other collision face (the lowest terrain
  included) and under all of it;
- (scene check, imported tracks) every terrain cell under an imported lake or the sea has a water reset sheet, and
  no sheet reaches a layout's blue AI route;
- (scene check) every spectator stands under the ``bounds_crowd`` reset lid, the lid over their heads, and no crowd
  reset reaches a layout's blue AI route.
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from wfbuild import bounds as B  # noqa: E402

KILL_MARGIN = 5.0
MIN_GAP = B.RESET_FACTOR * B.RESET_GAP  # every reset at least this far outside every layout's blue AI route


def _quads_dists(pts: np.ndarray, quads: list[np.ndarray], window: float) -> np.ndarray:
    """Per point, the plan distance to the nearest of the polygons (0 inside one), looked for within ``window``
    of each polygon (inf beyond)."""
    out = np.full(len(pts), np.inf)
    for q in quads:
        lo, hi = q.min(0) - window, q.max(0) + window
        sel = np.flatnonzero((pts[:, 0] >= lo[0]) & (pts[:, 0] <= hi[0]) & (pts[:, 1] >= lo[1]) & (pts[:, 1] <= hi[1]))
        if len(sel):
            out[sel] = np.minimum(out[sel], B._poly_dist(pts[sel, 0], pts[sel, 1], q))
    return out


def _quads_dist(pts: np.ndarray, quads: list[np.ndarray], window: float = MIN_GAP + 1.0) -> float:
    """Smallest plan distance from points to any of the polygons (0 when inside one; inf beyond ``window``)."""
    if not len(pts) or not quads:
        return float("inf")
    return float(_quads_dists(pts, quads, window).min())


def road_distance(pts: np.ndarray, roads: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    """Per plan point, the distance to the nearest road edge (centreline samples less the half width)."""
    best = np.full(len(pts), np.inf)
    for P, hw in roads:
        for k in range(0, len(P), 400):
            sl = slice(k, k + 401)
            d = np.hypot(pts[:, None, 0] - P[None, sl, 0], pts[:, None, 1] - P[None, sl, 1]) - hw[None, sl]
            best = np.minimum(best, d.min(1))
    return best


def overlap(tris: np.ndarray, polys: list[np.ndarray], cell: float = 1.0) -> int:
    """How many ``cell`` cells have their centre inside both the triangles (m x 3 x 2+) and the polygons, in plan."""
    t = np.asarray(tris, dtype=np.float64)[:, :, :2]
    p = np.array([q[[0, k, k + 1]] for q in polys for k in range(1, len(q) - 1)]).reshape(-1, 3, 2)
    if not len(t) or not len(p):
        return 0
    lo = t.reshape(-1, 2).min(0) - cell
    nx, ny = (int(v) for v in np.ceil((t.reshape(-1, 2).max(0) + cell - lo) / cell))
    near = (p.max(1) >= lo).all(1) & (p.min(1) <= lo + cell * np.array([nx, ny])).all(1)
    a = B.raster_tris(t, lo[0], lo[1], cell, nx, ny)
    b = B.raster_tris(p[near], lo[0], lo[1], cell, nx, ny)
    return int((a & b).sum())


def crowd_ok(check, crowd: np.ndarray, people: np.ndarray, blue: list[np.ndarray]) -> None:
    """Every spectator (``people``: plan positions and heights) stands under the bounds_crowd lid, which is over
    their heads; no crowd reset is over any layout's road or blue AI route."""
    if not len(people):
        return
    n = np.cross(crowd[:, 1] - crowd[:, 0], crowd[:, 2] - crowd[:, 0]) if len(crowd) else np.zeros((0, 3))
    lid = crowd[np.abs(n[:, 2]) > 0.5 * np.linalg.norm(n, axis=1)] if len(crowd) else crowd
    cell = 0.25
    lo = people[:, :2].min(0) - 5.0
    nx, ny = (int(v) for v in np.ceil((people[:, :2].max(0) + 5.0 - lo) / cell))
    cov = B.raster_tris(lid, lo[0], lo[1], cell, nx, ny) if len(lid) else np.zeros((nx, ny), dtype=bool)
    ij = np.floor((people[:, :2] - lo) / cell).astype(int)
    under = cov[ij[:, 0], ij[:, 1]]
    low = 0
    if len(lid):
        c = lid.mean(1)
        for p in people[under]:
            k = np.argmin(np.hypot(*(c[:, :2] - p[:2]).T))
            low += int(float(lid[k, :, 2].min()) < p[2] + 2.0)
    check(bool(under.all()) and low == 0,
          "base: every spectator inside a crowd reset (bounds_crowd, lid over their heads)",
          f"{int(under.sum())} of {len(people)} under the lid, {low} with it below 2 m over them")
    if len(crowd) and len(blue):
        k = overlap(crowd, blue, 0.5)
        check(k == 0, "base: no crowd reset over any layout's road or blue AI route", f"{k} 0.5 m cells overlap")


def reset_distance(check, name: str, reset: np.ndarray, blue: list[np.ndarray], roads, extent) -> None:
    """The reset stands at least RESET_FACTOR times as far from the road edge as the first walls did (RESET_GAP
    beyond the blue AI route: d_first = d_road - d_blue + RESET_GAP) and at least RESET_MIN_M from it, except within
    RESET_EDGE_KEEP + two cells of the terrain's ``extent`` (lo, hi), where the terrain ends sooner."""
    v = np.unique(reset.reshape(-1, 3)[:, :2].round(2), axis=0)
    if not len(v):
        return
    d_road = road_distance(v, roads)
    d_blue = _quads_dists(v, blue, B.RESET_MIN_M * 4)
    d_blue = np.minimum(d_blue, d_road)  # beyond the search window: no nearer than the road itself
    first = d_road - d_blue + B.RESET_GAP
    lo, hi = extent
    edge = np.minimum.reduce([v[:, 0] - lo[0], hi[0] - v[:, 0], v[:, 1] - lo[1], hi[1] - v[:, 1]])
    tol = 4.0 * math.sqrt(0.5) + 0.5  # the 4 m terrain grid
    short = (d_road < B.RESET_FACTOR * first - tol) | (d_road < B.RESET_MIN_M - tol)
    near_edge = short & (edge < B.RESET_EDGE_KEEP + 2 * 4.0)  # where the terrain ends sooner
    bad = short & ~near_edge
    k = int(np.argmin(np.where(near_edge, np.inf, d_road / np.maximum(first, 1e-6))))
    check(not bad.any(), f"{name}: reset at least {B.RESET_FACTOR:g}x as far from the road as the first walls "
          f"({B.RESET_GAP:.0f} m beyond the blue AI route) and at least {B.RESET_MIN_M:.0f} m",
          f"{int(bad.sum())} of {len(v)} vertices too near, e.g. {v[bad][:3].round(1).tolist()}; nearest "
          f"{d_road.min():.1f} m from a road, tightest {d_road[k]:.1f} m vs first {first[k]:.1f} m; "
          f"{int(near_edge.sum())} nearer by the terrain's edge")


def evaluate(check, where: str, ground: np.ndarray, kill: np.ndarray, others_z: float,
             layouts: list[tuple[str, np.ndarray, list[np.ndarray], np.ndarray]]) -> None:
    """``ground``: collidable driving-surface triangles (m x 3 x 3, no resets, no walls); ``kill``: the kill floor's
    triangles; ``others_z``: the lowest z of every other collision face; per layout (name, reset triangles, blue
    AI route quads (plan), seed points on its road)."""
    check(len(kill) > 0, f"{where}: kill floor (#resetplayer) present")
    if len(kill):
        kz = float(kill[:, :, 2].max())
        check(kz <= others_z - KILL_MARGIN, f"{where}: kill floor at least {KILL_MARGIN:.0f} m below every other "
              "collision face", f"kill floor z {kz:.1f}, lowest other {others_z:.1f}")
        klo, khi = kill[:, :, :2].reshape(-1, 2).min(0), kill[:, :, :2].reshape(-1, 2).max(0)
        glo, ghi = ground[:, :, :2].reshape(-1, 2).min(0), ground[:, :, :2].reshape(-1, 2).max(0)
        check(bool((klo <= glo).all() and (khi >= ghi).all()), f"{where}: kill floor under all the collision ground",
              f"floor {klo.round(0)}..{khi.round(0)}, ground {glo.round(0)}..{ghi.round(0)}")
    every = [q for _n, _r, quads, _s in layouts for q in quads]
    for name, reset, quads, seeds in layouts:
        check(len(reset) > 0, f"{name}: reset wall and lid (#resetplayer) present", f"{len(reset)} triangles")
        if len(reset):
            d = _quads_dist(reset.reshape(-1, 3)[:, :2], every)
            check(d >= MIN_GAP - 0.3, f"{name}: reset at least {MIN_GAP:.0f} m outside every layout's blue AI route "
                  "(none on any layout's road or AI lines)",
                  f"nearest {d:.2f} m" if d < np.inf else f"none within {MIN_GAP + 1.0:.0f} m")
        r = B.enclosure(reset, ground, seeds)
        check(not r["escaped"] and r["seedsBlocked"] == 0,
              f"{name}: the reset encloses the layout (no way off the collision ground without a reset)",
              f"reachable {r['reachHa']} ha from {r['seeds']} road cells")
        check(r["holes"] == 0, f"{name}: collidable ground under all of the reachable area (no holes)",
              f"{r['holes']} cells without ground, e.g. {r['holeAt']}")


def water_cells_ok(check, terrain_tris: np.ndarray, water_tris: np.ndarray, spec: dict, env: dict,
                   blue: list[np.ndarray] = ()) -> None:
    """Every terrain vertex more than WATER_SINK + 0.05 m under an imported lake (inside its outline) or the sea
    has a water reset sheet over it, unless it is by a layout's blue AI route (``blue``: no water reset reaches
    those); and no sheet reaches one."""
    if len(water_tris) and len(blue):
        n = overlap(water_tris, blue)
        check(n == 0, "base: no water reset sheet over any layout's road or blue AI route", f"{n} 1 m cells overlap")
    near_blue = 4.0 * math.sqrt(2.0) + B.PROTECT_PAD + 0.1  # a 4 m terrain cell within PROTECT_PAD is left dry
    imp = (spec.get("sketch") or {}).get("imported") or {}
    levels = []
    if env.get("sea") and imp.get("seaLevelM") is not None:
        levels.append((float(imp["seaLevelM"]), None))
    if env.get("lake"):
        levels += [(float(lk["levelM"]), np.array([[q["x"], q["y"]] for q in lk["points"]])) for lk in imp.get("lakes") or []]
    if not levels:
        return
    from wfbuild.backdrop import _in_polygon
    v = terrain_tris.reshape(-1, 3)
    v = np.unique(v.round(3), axis=0)
    miss, wet_n = 0, 0
    for lvl, poly in levels:
        wet = v[:, 2] < lvl - B.WATER_SINK - 0.05
        if poly is not None:
            wet &= _in_polygon(v[:, :2], poly)
        if len(blue) and wet.any():
            k = np.flatnonzero(wet)
            wet[k[_quads_dists(v[k, :2], list(blue), near_blue) < near_blue]] = False
        w = v[wet]
        wet_n += len(w)
        if not len(w):
            continue
        lo = np.minimum(w[:, :2].min(0), water_tris[:, :, :2].reshape(-1, 2).min(0) if len(water_tris) else w[:, :2].min(0)) - 2
        cell = 1.0
        nx, ny = (int(np.ceil(x)) + 2 for x in (w[:, :2].max(0) - lo) / cell)
        cov = B.raster_tris(water_tris, lo[0], lo[1], cell, nx, ny) if len(water_tris) else np.zeros((nx, ny), bool)
        ij = np.floor((w[:, :2] - lo) / cell).astype(int)
        # a vertex on a cell corner: any of the four cells around it covered
        ok = np.zeros(len(w), dtype=bool)
        for di in (0, -1):
            for dj in (0, -1):
                i, j = np.clip(ij[:, 0] + di, 0, nx - 1), np.clip(ij[:, 1] + dj, 0, ny - 1)
                ok |= cov[i, j]
        miss += int((~ok).sum())
    check(miss == 0, "base: water reset sheets (#resetplayer) under the lakes / sea",
          f"{wet_n} terrain vertices under water, {miss} without a sheet")


# --------------------------------------------------------------------------------- built .blend files
def _blend_tris(ob) -> np.ndarray:
    m = np.array(ob.matrix_world)
    v = np.array([tuple(x.co) for x in ob.data.vertices], dtype=np.float64).reshape(-1, 3)
    v = v @ m[:3, :3].T + m[:3, 3]
    out = []
    for p in ob.data.polygons:
        f = list(p.vertices)
        out += [v[[f[0], f[k], f[k + 1]]] for k in range(1, len(f) - 1)]
    return np.array(out).reshape(-1, 3, 3)


def _is_reset(ob) -> bool:
    return any(m is not None and "#resetplayer" in m.name for m in ob.data.materials)


def scene(check, out: str, slug: str, spec: dict, manifest: dict) -> None:
    """The checks on the builder's .blend files (in Blender): objects, CustomData and materials of the bounds
    meshes, then evaluate() on their geometry, and the water sheets on imported tracks."""
    import bpy
    from wfbuild import envs
    bpy.ops.wm.open_mainfile(filepath=os.path.join(out, f"{slug}_base.blend"))
    obs = {o.name: o for o in bpy.context.scene.objects if o.type == "MESH"}
    want = ["bounds_kill_floor", "bounds_far_ground"]
    check(all(n in obs for n in want) and all(obs[n].get("CustomData") == B.RESET_CUSTOM_DATA for n in want)
          and _is_reset(obs["bounds_kill_floor"]) and "#offroadfoliage" in obs["bounds_far_ground"].data.materials[0].name,
          "base: kill floor (#resetplayer) and far-ground collision (#offroadfoliage), invisible (vis = false | col = true)")
    ground, others = [], []
    for n, o in obs.items():
        cd = str(o.get("CustomData", ""))
        if "col = true" not in cd or n in ("bounds_kill_floor", "bounds_water"):
            continue
        t = _blend_tris(o)
        if not len(t):
            continue
        others.append(float(t[:, :, 2].min()))
        if not n.startswith("walls_") and n != "bounds_crowd":
            ground.append(t)
    ground = np.concatenate(ground)
    kill = _blend_tris(obs["bounds_kill_floor"]) if "bounds_kill_floor" in obs else np.zeros((0, 3, 3))
    terrain = np.concatenate([_blend_tris(o) for n, o in obs.items() if n.startswith("terrain_")])
    water = _blend_tris(obs["bounds_water"]) if "bounds_water" in obs else np.zeros((0, 3, 3))
    crowd = _blend_tris(obs["bounds_crowd"]) if "bounds_crowd" in obs else np.zeros((0, 3, 3))
    if "bounds_crowd" in obs:
        check(obs["bounds_crowd"].get("CustomData") == B.RESET_CUSTOM_DATA and _is_reset(obs["bounds_crowd"]),
              "base: bounds_crowd is an invisible #resetplayer mesh (vis = false | col = true)")
    tz = terrain.reshape(-1, 3)
    kz = float(kill[:, :, 2].max()) if len(kill) else float("nan")
    check(kz < float(tz[:, 2].min()) - KILL_MARGIN, "base: kill floor below the lowest terrain (a car falling "
          "through the map is reset)", f"floor {kz:.1f}, terrain from {float(tz[:, 2].min()):.1f}")
    env = envs.env(envs.env_name(spec.get("options", {}).get("environment")))
    model = json.load(open(os.path.join(out, "model.json"), encoding="utf-8"))

    def to_scene(xy: np.ndarray) -> np.ndarray:
        t = manifest.get("transform", {})
        a = math.radians(t.get("rotateDeg", 0))
        c, s = round(math.cos(a)), round(math.sin(a))
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        return np.stack([xy[:, 0] * c - xy[:, 1] * s, xy[:, 0] * s + xy[:, 1] * c], axis=1) \
            + np.array(t.get("shift", [0.0, 0.0, 0.0]))[:2]

    road_lines = [(to_scene([[q["x"], q["y"]] for q in rt["samples"]]),
                   np.array([q["width"] for q in rt["samples"]]) / 2.0) for rt in model["routes"]]
    sc = json.load(open(os.path.join(out, "scenery.json"), encoding="utf-8"))
    people = [p["position"] for p in sc["placements"] if p["category"] in B.CROWD_CATEGORIES]
    people = (np.column_stack([to_scene([p[:2] for p in people]), [p[2] for p in people]]) if people
              else np.zeros((0, 3)))
    # imported extra roads (shared tarmac that is no layout's own branch, e.g. Oulton Park's Hislop's straight): inside
    # every layout's reset, none of it under a lid, also on the layouts that don't drive it
    roads = ((spec.get("sketch") or {}).get("imported") or {}).get("roads") or []
    shared = np.array([[q["x"], q["y"]] for rd in roads for q in rd["centreline"]], dtype=np.float64).reshape(-1, 2)
    if len(shared):
        t = manifest.get("transform", {})
        a = math.radians(t.get("rotateDeg", 0))
        c, s = round(math.cos(a)), round(math.sin(a))
        shared = np.stack([shared[:, 0] * c - shared[:, 1] * s, shared[:, 0] * s + shared[:, 1] * c], axis=1)
        shared += np.array(t.get("shift", [0.0, 0.0, 0.0]))[:2]
    lays = []
    for lay in manifest["layouts"]:
        bpy.ops.wm.open_mainfile(filepath=os.path.join(out, lay["file"]))
        lobs = {o.name: o for o in bpy.context.scene.objects if o.type == "MESH"}
        r = lobs.get("bounds_reset")
        check(r is not None and r.get("CustomData") == B.RESET_CUSTOM_DATA and _is_reset(r),
              f"{lay['name']}: bounds_reset is an invisible #resetplayer mesh (vis = false | col = true)")
        reset = _blend_tris(r) if r is not None else np.zeros((0, 3, 3))
        if len(reset):
            others.append(float(reset[:, :, 2].min()))
        quads = [t for n, o in lobs.items() if n.startswith("#ai_route_")
                 for t in (_blend_tris(o)[:, :, :2])]
        seeds = np.concatenate([_blend_tris(o).reshape(-1, 3) for n, o in lobs.items() if n.startswith("#ai_race_")])
        if len(shared):
            r = B.enclosure(reset, ground, shared)
            check(r["seedsBlocked"] == 0 and not r["escaped"],
                  f"{lay['name']}: imported extra roads inside the reset (drivable, no reset across them)",
                  f"{r['seedsBlocked']} of {r['seeds']} road cells under a reset")
            seeds = np.concatenate([seeds[:, :2], shared])
        lays.append((lay["name"], reset, quads, seeds))
    blue = [q for _n, _r, quads, _s in lays for q in quads]
    tl = terrain[:, :, :2].reshape(-1, 2)
    for name, reset, _q, _s in lays:
        reset_distance(check, name, reset, blue, road_lines, (tl.min(0), tl.max(0)))
    water_cells_ok(check, terrain, water, spec, env, blue)
    crowd_ok(check, crowd, people, blue)
    evaluate(check, "base", ground, kill, min(others), lays)

def export(check, track: str) -> None:
    """The checks on bgeometry's output (``track`` = data/track/<slug> with <slug>_base.scne and the layouts)."""
    import scne_read
    slug = os.path.basename(os.path.normpath(track))
    base = os.path.join(track, f"{slug}_base.scne")
    if not os.path.isfile(base):
        return
    resets = scne_read.RESET_SURFACES

    def split(cols):
        g, r, o = [], [], []
        for c in cols:
            t = c.pos[c.tris]
            if not len(t):
                continue
            isr = np.isin(c.surface, list(resets))
            n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
            flat = np.abs(n[:, 2]) > 0.2 * np.linalg.norm(n, axis=1)
            r.append(t[isr])
            g.append(t[~isr & flat])
            o.append(t[~isr])
        cat = lambda x: np.concatenate(x) if x else np.zeros((0, 3, 3))  # noqa: E731
        return cat(g), cat(r), cat(o)

    try:
        ground, base_reset, others = split(scne_read.read_collision(base))
    except (ValueError, IndexError, struct.error) as e:
        check(False, f"{slug}: collision decoded from the built base", str(e))
        return
    lows = [float(others[:, :, 2].min())] if len(others) else []
    kz = float(base_reset[:, :, 2].min()) if len(base_reset) else np.inf
    kill = base_reset[base_reset[:, :, 2].max(1) <= kz + 0.5] if len(base_reset) else base_reset
    lays = []
    for f in sorted(os.listdir(track)):
        if not f.endswith(".scne") or f.endswith("_base.scne"):
            continue
        p = os.path.join(track, f)
        _g, reset, oth = split(scne_read.read_collision(p))
        if len(reset):
            lows.append(float(reset[:, :, 2].min()))
        if len(oth):
            lows.append(float(oth[:, :, 2].min()))
        try:
            L = scne_read.read_layout(p)
        except (ValueError, IndexError):
            continue
        quads, seeds = [], []
        for route in L.routes:
            for k in range(len(route) - 1):
                (a, b), (c, d) = route[k], route[k + 1]
                quads.append(np.array([a[:2], b[:2], d[:2], c[:2]]))
                seeds.append((np.array(a[:2]) + np.array(b[:2])) / 2.0)
        lays.append((f[:-5], reset, quads, np.array(seeds)))
    if not len(ground):
        check(False, f"{slug}: collision ground decoded from the built base")
        return
    evaluate(check, f"{slug}_base", ground, kill, min(lows) if lows else 0.0, lays)
