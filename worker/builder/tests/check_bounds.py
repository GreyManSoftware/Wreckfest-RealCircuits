"""Out-of-bounds checks (wfbuild/bounds.py), shared by check_scene.py (the built .blend files, in Blender) and
check_export.py (the .scne files bgeometry built, plain Python):

- every layout has a ``#resetplayer`` reset wall + lid that a car can't get past without touching it: from the
  layout's road, the area reachable without crossing a reset face never reaches the edge of the collision ground
  (and the infield stays reachable);
- there is collidable ground under every bit of that area (no holes);
- the reset stands at least RESET_GAP outside the layout's blue AI route (no reset on legitimate run-off);
- a ``#resetplayer`` kill floor lies at least KILL_MARGIN below every other collision face and under all of it;
- (scene check, imported tracks) every terrain cell under an imported lake or the sea has a water reset sheet.
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from wfbuild import bounds as B  # noqa: E402

KILL_MARGIN = 5.0


def _quads_dist(pts: np.ndarray, quads: list[np.ndarray]) -> float:
    """Smallest plan distance from points to any of the polygons (0 when inside one)."""
    if not len(pts) or not quads:
        return float("inf")
    best = float("inf")
    for q in quads:
        lo, hi = q.min(0) - B.RESET_GAP - 1.0, q.max(0) + B.RESET_GAP + 1.0
        sel = (pts[:, 0] >= lo[0]) & (pts[:, 0] <= hi[0]) & (pts[:, 1] >= lo[1]) & (pts[:, 1] <= hi[1])
        if sel.any():
            best = min(best, float(B._poly_dist(pts[sel, 0], pts[sel, 1], q).min()))
    return best


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
    for name, reset, quads, seeds in layouts:
        check(len(reset) > 0, f"{name}: reset wall and lid (#resetplayer) present", f"{len(reset)} triangles")
        if len(reset):
            d = _quads_dist(reset.reshape(-1, 3)[:, :2], quads)
            check(d >= B.RESET_GAP - 0.3, f"{name}: reset at least {B.RESET_GAP:.0f} m outside the blue AI route",
                  f"nearest {d:.2f} m")
        r = B.enclosure(reset, ground, seeds)
        check(not r["escaped"] and r["seedsBlocked"] == 0,
              f"{name}: the reset encloses the layout (no way off the collision ground without a reset)",
              f"reachable {r['reachHa']} ha from {r['seeds']} road cells")
        check(r["holes"] == 0, f"{name}: collidable ground under all of the reachable area (no holes)",
              f"{r['holes']} cells without ground, e.g. {r['holeAt']}")


def water_cells_ok(check, terrain_tris: np.ndarray, water_tris: np.ndarray, spec: dict, env: dict) -> None:
    """Every terrain vertex more than WATER_SINK + 0.05 m under an imported lake (inside its outline) or the sea
    has a water reset sheet over it."""
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
        if not n.startswith("walls_"):
            ground.append(t)
    ground = np.concatenate(ground)
    kill = _blend_tris(obs["bounds_kill_floor"]) if "bounds_kill_floor" in obs else np.zeros((0, 3, 3))
    terrain = np.concatenate([_blend_tris(o) for n, o in obs.items() if n.startswith("terrain_")])
    water = _blend_tris(obs["bounds_water"]) if "bounds_water" in obs else np.zeros((0, 3, 3))
    env = envs.env(envs.env_name(spec.get("options", {}).get("environment")))
    water_cells_ok(check, terrain, water, spec, env)
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
        lays.append((lay["name"], reset, quads, seeds))
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

    ground, base_reset, others = split(scne_read.read_collision(base))
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
