"""Assertions on a builder output folder (run inside Blender, in the worker container).

usage: blender -b --factory-startup --python-exit-code 1 --python check_scene.py -- OUT_DIR SPEC.json

Checks the base and layout .blend files against the TrackModel (OUT_DIR/model.json) and the spec: object
names and counts, collision properties, material naming and textures, the +-2000 m limit, 2 m road
squares, shared junction vertices, no overlapping or cracked road faces, bridge clearance, terrain under
the road, AI route topology, checkpoints and start points. Prints one PASS/FAIL line per check and exits
non-zero on any failure.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import defaultdict

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from wfbuild import config as C
from wfbuild import envs
from wfbuild.layout import composite_route

WF = os.environ.get("WRECKFEST_DIR", "/wreckfest")
failures: list[str] = []
ENGINE_TAGS = {"blend", "decal", "noshadow", "lofi", "horizon", "xtree"}  # shader / render tags used here
GRID_EXEMPT = {"gantry", "flags", "staff", "grandstand"}


def no_collision(name: str) -> bool:
    return name in ("#static_cube_map", "track_edges", "track_transitions") or name.startswith("backdrop_")


def known_tag(t: str) -> bool:
    return (t in C.VALID_SURFACES or t in ENGINE_TAGS or (t.startswith("priority") and t[8:].isdigit())
            or os.path.isfile(os.path.join(WF, "data/scene/surface", t + ".suse")))


def texture_ok(p: str, out: str) -> bool:
    """A Blender image path resolves: a stock .bmap in the install, or a generated map under OUT/mod."""
    low = p.replace("\\", "/")
    if "/data/" not in low.lower():
        return False
    rel = low[low.lower().index("/data/") + 1:]
    if rel.startswith("data/track/"):
        return os.path.isfile(os.path.join(out, "mod", rel)) and rel.endswith(".tga")
    return os.path.isfile(os.path.join(WF, rel[:-4] + ".bmap"))


def check_scenery(out: str, model: dict, spec: dict, manifest: dict, n_props: int) -> None:
    """scenery.json: matches the props in the base scene, assets fit the environment, and nothing stands on a
    road, run-off, the grid sector, a bridge or a jump landing (recomputed here from the TrackModel)."""
    from wfbuild.dressing import Planner
    from wfbuild.fields import exact_edge_distance
    from wfbuild.geometry import TrackGeometry

    sc = load_json(os.path.join(out, "scenery.json"))
    P = sc["placements"]
    check(len(P) == n_props and sc["total"] == n_props, "scenery: placements match the base scene's prop subscenes",
          f"{len(P)} placements, {n_props} subscenes")
    geo = TrackGeometry(model, spec)
    pl = Planner(geo)
    bad_env = sorted({p["asset"] for p in P} - pl.allowed)
    check(not bad_env, f"scenery: only assets allowed for {pl.env_name}", ", ".join(bad_env))
    if not P:
        return
    xy = np.array([p["position"][:2] for p in P])
    cats = [p["category"] for p in P]
    d = exact_edge_distance(geo, xy)
    # barriers run under bridge decks and their ramps: measure them against the roads that aren't elevated bridge
    # structure, and where that is too close, against the roads that don't pass > 3 m overhead
    sel = np.concatenate([geo.svalid[r.index] & ~geo.structure[r.index] for r in geo.routes])
    rxy, rz, rhw = geo.index.xy[sel], geo.index.z[sel], geo.index.hw[sel]
    for k, p in enumerate(P):
        if p["category"] == "barriers":
            dd = np.hypot(*(rxy - xy[k]).T) - rhw
            if dd.min() < C.DIVIDER_MIN - 1.0:
                dd[rz - p["position"][2] > 3.0] = np.inf
            d[k] = float(dd.min())
    # the barrier row stands on the wall line and the corner markers in front of it; everything else behind
    lanes = {"gantry": (-np.inf, np.inf),
             "barriers": (C.DIVIDER_MIN - 1.0, max(C.CROWD_WALL, C.FAST_CORNER_WALL) + 2.0),
             "cornerMarkers": (C.SHOULDER_W, np.inf)}
    on_road = [f"{p['asset']} {d[k]:.1f} m" for k, p in enumerate(P)
               if not (lanes.get(p["category"], (pl.edge_min - 1.3, np.inf))[0] <= d[k]
                       <= lanes.get(p["category"], (0, np.inf))[1])]
    check(not on_road, "scenery: nothing but the gantry, the barrier row (on the wall line) and corner markers "
          "within the road/run-off clearance", f"clearance {pl.edge_min - 1.0:.1f} m; {on_road[:3]}")
    barriers = [k for k, c in enumerate(cats) if c == "barriers"]
    row = set(envs.barrier_row(pl.env_name, geo.walls))
    check(bool(barriers) == bool(row) and {P[k]["asset"] for k in barriers} <= row,
          f"scenery: barrier row follows options.walls ({geo.walls}: stock {sorted(row) or 'none'})",
          f"{len(barriers)} barrier props")
    ex = np.array([c in GRID_EXEMPT for c in cats])
    wall = np.array([c == "barriers" for c in cats])  # containment follows the road, no-go zones included
    hit = pl.in_nogo(xy, 0.0, False) & ~ex & ~wall
    hit |= pl.in_nogo(xy, 0.0, True) & ex
    check(not hit.any(), "scenery: no-go zones (grid sector, bridge decks/ramps, jump landings) are clear",
          ", ".join(f"{P[k]['asset']}" for k in np.nonzero(hit)[0][:3]))
    x0, x1, y0, y1 = geo.extent
    outside = [P[k]["asset"] for k in range(len(P)) if not (x0 <= xy[k, 0] <= x1 and y0 <= xy[k, 1] <= y1)]
    check(not outside, "scenery: every placement on the terrain", ", ".join(outside[:3]))
    gantry = [p for p in P if p["category"] == "gantry"]
    sp = [k for k, c in enumerate(cats) if c == "spectators"]
    if sp:
        # standing spectators face the road with local +Y: the nearest road point lies within 60 deg of +Y
        roads = geo.index.xy
        bad = 0
        for k in sp:
            j = int(np.argmin(np.hypot(*(roads - xy[k]).T)))
            to = roads[j] - xy[k]
            yaw = P[k]["yaw"]
            fy = np.array([-math.sin(yaw), math.cos(yaw)])
            if np.dot(fy, to) / max(np.linalg.norm(to), 1e-9) < 0.5:
                bad += 1
        check(bad <= max(2, len(sp) // 20), "scenery: standing spectators face the road (local +Y)",
              f"{bad} of {len(sp)} facing away")
    check(len(gantry) <= 1, "scenery: at most one start/finish gantry", f"{len(gantry)}")
    print(f"INFO scenery: {sc['total']} placements {sc['counts']}")


def _segments_cross(p0, p1, a, b) -> np.ndarray:
    """2D: does segment p0-p1 cross any of the segments a[k]-b[k]?"""
    d = p1 - p0
    e = b - a
    den = d[0] * e[:, 1] - d[1] * e[:, 0]
    w = a - p0
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / den
        u = (w[:, 0] * d[1] - w[:, 1] * d[0]) / den
    return bool(np.any((np.abs(den) > 1e-12) & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)))


def check_roadside(obs: dict, model: dict, spec: dict, manifest: dict) -> None:
    """Kerbs, track limits and barriers as on real circuits (Chris, 2026-10-19): kerbs only at corners (apex
    and exit), green strips only beside kerbs and flush; barriers not round the whole lap; a continuous
    collision wall between every pair of close sections; the AI route reaching over the run-off."""
    from wfbuild.geometry import TrackGeometry

    geo = TrackGeometry(model, spec)
    opts = spec["options"]
    # --- kerbs: only near corners, nowhere near continuous
    if opts.get("kerbs") and "track_kerbs" in obs:
        far, total, kerbed = 0, 0, 0
        for (ri, side), (kw, gw) in geo.kerb_plan().items():
            r = geo.routes[ri]
            near = np.zeros(r.n, dtype=bool)
            for c in geo.corner_runs(r):  # a corner from 30 m before its entry to 40 m past its exit
                d_in = r.signed_s(r.s, float(r.s[c["i_in"]]))
                span = float(r.s_dist(r.s[c["i_out"]], r.s[c["i_in"]]))
                near |= (d_in >= -30.0) & (d_in <= span + 40.0)
            total += r.n
            kerbed += int((kw > 0).sum())
            far += int(((kw > 0) & ~near).sum())
        check(far == 0 and kerbed <= 0.45 * total, "base: kerbs only at corners (apex and exit), not along straights",
              f"{kerbed / max(total, 1):.0%} of the edges kerbed; {far} kerbed samples away from any corner")
        uv = obs["track_kerbs"].data.uv_layers[0].data
        us = np.array([d.uv[0] for d in uv])
        check(float(us.max() - us.min()) > 0.5 or len(us) < 8,
              "base: kerb UVs run along the road (red/white blocks along it, not across)")
        check(("track_limits" in obs) == any((gw > 0).any() for _kw, gw in geo.kerb_plan().values()),
              "base: green track-limit strips built where the kerb plan has them")
        kerbs, _sh, lim = geo.build_roadside()
        if not lim.empty:
            kset = {key(v, 3) for v in kerbs.verts}
            lv = np.array(lim.verts)
            inner = [v for v in lim.verts if key(v, 3) in kset]
            check(len(inner) >= len(lim.faces), "base: green track-limit strips start at the kerbs' outer edge "
                  "(shared vertices)", f"{len(inner)} shared vertices, {len(lim.faces)} faces")
            outer = np.array([v for v in lim.verts if key(v, 3) not in kset])
            if len(outer):  # flush: the strip's outer edge sits just above the grass
                dz = outer[:, 2] - geo.terrain(outer)
                check(float(np.percentile(dz, 2)) >= -0.02 and float(np.percentile(dz, 98)) <= 0.12,
                      "base: green strips meet the grass flush (0-12 cm above it)",
                      f"p2 {np.percentile(dz, 2):.3f}, p98 {np.percentile(dz, 98):.3f} m")
            del lv
    # --- barriers: not round the whole lap (walls: none has none)
    plan = geo.wall_plan()
    if geo.walls != "none":
        share = max(float(p["ok"].mean()) for p in plan.values())
        check(share <= 0.7,"base: no continuous barrier round the lap (grass run-off first; barriers where needed)",
              f"largest share of one side with a barrier {share:.0%}")
        # every close pair of sections has a continuous collision wall between them
        segs = []
        for o in obs.values():
            if o.type == "MESH" and o.name.startswith("walls_"):
                V = world_verts(o)
                for f in faces_of(o):
                    for a, b in zip(f, f[1:] + f[:1]):
                        segs.append((V[a][:2], V[b][:2]))
        A = np.array([s[0] for s in segs]).reshape(-1, 2)
        B = np.array([s[1] for s in segs]).reshape(-1, 2)
        open_, pairs = [], 0
        for (ri, side), g in geo.road_gaps().items():
            r = geo.routes[ri]
            edge = r.left if side > 0 else r.right
            for i in np.nonzero(np.isfinite(g["gap"]))[0][::2]:
                ends = np.array([edge[i, :2] + r.nl[i] * side * 0.3, edge[i, :2] + r.nl[i] * side * (g["gap"][i] - 0.3)])
                p0, p1 = to_scene(ends, manifest)
                pairs += 1
                near = (np.minimum(A[:, 0], B[:, 0]) <= max(p0[0], p1[0]) + 1) & (np.maximum(A[:, 0], B[:, 0]) >= min(p0[0], p1[0]) - 1) \
                    & (np.minimum(A[:, 1], B[:, 1]) <= max(p0[1], p1[1]) + 1) & (np.maximum(A[:, 1], B[:, 1]) >= min(p0[1], p1[1]) - 1)
                if not len(A) or not _segments_cross(p0, p1, A[near], B[near]):
                    open_.append(f"{r.id}:{'l' if side > 0 else 'r'}@{r.s[i]:.0f}m gap {g['gap'][i]:.1f}")
        check(not open_ or len(open_) <= max(2, pairs // 50),
              f"base: a collision wall divides every pair of close sections (gap < {C.DIVIDER_GAP:.0f} m)",
              f"{pairs} probes across gaps, {len(open_)} open: {open_[:4]}")
    # the AI route covers the grass run-off (cars off the tarmac aren't reset at once)
    med = float(np.median(np.concatenate([p["ai_margin"] for p in plan.values()])))
    check(med >= 8.0, "base: the AI route reaches over the grass run-off", f"median margin {med:.1f} m beyond the edge")


def read_tga(path: str) -> np.ndarray:
    """The builder's uncompressed TGA maps as RGB(A) rows from v = 0 (south) up."""
    with open(path, "rb") as f:
        h = f.read(18)
        w, ht, bpp = int.from_bytes(h[12:14], "little"), int.from_bytes(h[14:16], "little"), h[16] // 8
        a = np.frombuffer(f.read(w * ht * bpp), dtype=np.uint8).reshape(ht, w, bpp).astype(np.float64)
    a[:, :, :3] = a[:, :, 2::-1]
    return a


def check_maps(out: str, model: dict, spec: dict, manifest: dict) -> None:
    """Generated maps: a strong clutter grass band beside the road, and on mixed tracks the asphalt's blend ramp
    (B = the environment's gravel) leading into each surface switch."""
    from wfbuild.fields import exact_edge_distance
    from wfbuild.geometry import TrackGeometry

    mp = manifest["maps"]
    x0, y0, size = mp["frame"]
    root = os.path.join(out, "mod")
    clutter = read_tga(os.path.join(root, mp["paths"]["clutter_t"] + ".tga"))
    road = read_tga(os.path.join(root, mp["paths"]["blend_road_c"] + ".tga"))
    n = clutter.shape[0]
    geo = TrackGeometry(model, spec)

    def px(p):
        return (np.clip(((np.asarray(p)[:, 1] - y0) / size * n).astype(int), 0, n - 1),
                np.clip(((np.asarray(p)[:, 0] - x0) / size * n).astype(int), 0, n - 1))

    rng = np.random.default_rng(1)
    pts = np.column_stack([rng.uniform(x0, x0 + size, 6000), rng.uniform(y0, y0 + size, 6000)])
    pts = pts[(pts[:, 0] <= geo.extent[1]) & (pts[:, 1] <= geo.extent[3])]
    d = exact_edge_distance(geo, pts)
    j, i = px(pts)
    band = clutter[j, i, 1][(d > 8.0) & (d < 35.0)]
    far = clutter[j, i, 1][d > 100.0]
    fb = float(np.mean(far)) if len(far) else 0.0
    check(len(band) > 20 and float(np.mean(band)) >= max(20.0, 3.0 * fb),
          "maps: clutter grass (G) forms a band beside the road and fades out far from it",
          f"band mean {np.mean(band) if len(band) else 0:.0f}, far {fb:.0f}")
    # the whole mask within the stock clutter budget (more crashed the game: "Out of vertex buffer memory")
    from wfbuild.maps import clutter_density
    ha, local = clutter_density(clutter[:, :, :3] / 255.0, size)
    check(ha <= C.CLUTTER_MAX_HA + 0.05 and local <= C.CLUTTER_MAX_LOCAL + 0.005,
          "maps: clutter mask within the stock budget",
          f"{ha:.1f} ha at full density (max {C.CLUTTER_MAX_HA}), 400 m window max {local:.3f} (max {C.CLUTTER_MAX_LOCAL})")
    sw = geo.surface_switches()
    if sw:
        before, inside = [], []
        for s in sw:
            r = s["route"]
            for into, acc in ((3.0, before), (C.TRANSITION_RAMP + 15.0, inside)):
                k = r.index_at((s["s"] + s["asphalt"] * into) % r.length)
                jj, ii = px(r.p[k:k + 1, :2])
                acc.append(road[jj[0], ii[0], 2])
        check(min(before) >= 140.0 and np.median(inside) < 120.0,
              "maps: mixed tracks blend the asphalt into gravel (road blend B) just before each surface switch",
              f"{len(sw)} switches; B at 3 m {min(before):.0f}, past the ramp median {np.median(inside):.0f}")


def check(ok: bool, what: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}: {what}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(what)
    return ok


def world_verts(ob) -> np.ndarray:
    co = np.empty(len(ob.data.vertices) * 3)
    ob.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    m = np.array(ob.matrix_world)
    return co @ m[:3, :3].T + m[:3, 3]


def faces_of(ob) -> list[tuple[int, ...]]:
    return [tuple(p.vertices) for p in ob.data.polygons]


def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def to_scene(p: np.ndarray, manifest: dict) -> np.ndarray:
    """TrackModel coordinates -> scene coordinates (the builder may rotate and move the whole track)."""
    t = manifest.get("transform", {})
    a = math.radians(t.get("rotateDeg", 0))
    c, s = round(math.cos(a)), round(math.sin(a))
    p = np.array(p, dtype=np.float64).copy()
    x, y = p[:, 0].copy(), p[:, 1].copy()
    p[:, 0], p[:, 1] = x * c - y * s, x * s + y * c
    return p + np.array(t.get("shift", [0.0, 0.0, 0.0]))[: p.shape[1]]


def key(v, nd=4):
    return (round(float(v[0]), nd), round(float(v[1]), nd), round(float(v[2]), nd))


# ------------------------------------------------------------------------------------------- base
def check_base(out: str, slug: str, model: dict, spec: dict, manifest: dict) -> None:
    bpy.ops.wm.open_mainfile(filepath=os.path.join(out, f"{slug}_base.blend"))
    obs = {o.name: o for o in bpy.context.scene.objects}
    opts = spec["options"]
    meshes = [o for o in obs.values() if o.type == "MESH"]
    check("track_surface" in obs and "track_shoulders" in obs, "base: road and shoulders exist")
    check(any(n.startswith("terrain_") for n in obs), "base: terrain tiles exist",
          f"{sum(n.startswith('terrain_') for n in obs)} tiles")
    check("#static_cube_map" in obs, "base: #static_cube_map exists")
    if "#static_cube_map" in obs:
        cm = obs["#static_cube_map"]
        mats = [m.name for m in cm.data.materials]
        dims = max(cm.dimensions)
        bsdf = next((n for n in cm.data.materials[0].node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if mats else None
        check(mats == ["ghost"] and bsdf is not None and bsdf.inputs["Alpha"].is_linked and dims <= 0.31
              and cm.get("CustomData") == C.STATIC_CUBE_MAP[1],
              "base: #static_cube_map is a 0.3 m ghost cube (texture in the opacity slot), as the example",
              f"materials {mats}, size {dims:.2f} m")
    check(("track_kerbs" in obs) == bool(opts.get("kerbs")), "base: kerbs follow options.kerbs")
    walls = [n for n in obs if n.startswith("walls_")]
    check(bool(walls) == (opts.get("walls") in ("concrete", "tyres"))
          and all(obs[n].get("CustomData") == C.WALL_CUSTOM_DATA for n in walls),
          "base: walls follow options.walls (invisible collision walls, vis = false | col = true; the barrier "
          "props are the visible part)", f"{len(walls)} wall objects")
    trans = obs.get("track_transitions")
    want_trans = opts.get("surface") == "mixed" and len(C.SURFACE_ROADS["mixed"]) > 1
    check((trans is not None) == want_trans and (trans is None or all(
        "#decal" in m.name for m in trans.data.materials)),
        "base: mixed surface switches carry the stock transition decal (only on mixed tracks)",
        f"{len(trans.data.polygons) if trans else 0} decal faces")
    bridges = [c for c in model["crossings"] if c["type"] == "bridge" and c.get("detected", True)]
    for c in bridges:
        check(f"bridge_{c['id']}_deck" in obs and f"bridge_{c['id']}_barriers" in obs,
              f"base: bridge {c['id']} deck and barriers exist")
    # collision: every solid/drivable mesh has the String property CustomData containing col = true; the edge
    # decal and the backdrop have none
    nocol = [o for o in meshes if no_collision(o.name)]
    bad = [o.name for o in meshes if not no_collision(o.name)
           and not (isinstance(o.get("CustomData"), str) and "col = true" in o["CustomData"])]
    check(not bad, "base: every solid mesh has CustomData 'col = true' (String)", ", ".join(bad[:5]))
    check(not [o.name for o in nocol if "col = true" in str(o.get("CustomData", ""))],
          "base: edge decal and backdrop have no collision", f"{len(nocol)} objects")
    # materials: name#tag#... with a valid surface on solid meshes and only known tags
    bad_mats, missing_tex = [], []
    for o in meshes:
        for m in o.data.materials:
            tags = m.name.split("#")[1:]
            unknown = [t for t in tags if not known_tag(t)]
            if unknown or (not no_collision(o.name) and not any(t in C.VALID_SURFACES for t in tags)):
                bad_mats.append(m.name)
            if m.node_tree:
                for nd in m.node_tree.nodes:
                    if nd.type == "TEX_IMAGE" and nd.image and not texture_ok(nd.image.filepath, out):
                        missing_tex.append(nd.image.filepath)
    check(not bad_mats, "base: materials are name#tags with a valid surface on solid meshes and known tags",
          ", ".join(sorted(set(bad_mats))))
    check(not missing_tex, "base: textures resolve (stock .bmap, or a generated map in the mod)",
          ", ".join(sorted(set(missing_tex))[:5]))
    # #blend materials: 12-slot layout (stock), maps on UV2
    env = envs.env_name(opts.get("environment"))
    blends = {m.name: m for o in meshes for m in o.data.materials if "#blend" in m.name}
    slots_ok = []
    for name, m in blends.items():
        g = next((n for n in m.node_tree.nodes if n.type == "GROUP" and "#" in n.node_tree.name), None)
        linked = [k for k, s in enumerate(g.inputs) if s.is_linked] if g else []
        uv2 = set()
        for k in (6, 8, 9):
            if g and g.inputs[k].is_linked:
                img = g.inputs[k].links[0].from_node
                mp = img.inputs["Vector"].links[0].from_node if img.inputs["Vector"].is_linked else None
                uvn = mp.inputs["Vector"].links[0].from_node if mp and mp.inputs["Vector"].is_linked else None
                if uvn is not None and "2" in uvn.uv_map:
                    uv2.add(k)
        slots_ok.append(set(range(10)) <= set(linked) and uv2 == {6, 8, 9})
    check(len(blends) >= 3 and all(slots_ok), "base: road/verge/terrain use stock-layout #blend materials "
          "(slots 0-9, blend/macro/colour maps on UV2)", f"{sorted(blends)}")
    # UVs: Wreckfest stores them as int16 / 2048 -> every UV within +-16; UV2 (maps) within 0..1
    worst_uv, uv2_bad, uv2_missing = 0.0, [], []
    for o in meshes:
        layers = o.data.uv_layers
        for k, lay in enumerate(layers):
            if not len(lay.data):
                continue
            uv = np.empty(len(lay.data) * 2)
            lay.data.foreach_get("uv", uv)
            worst_uv = max(worst_uv, float(np.abs(uv).max()))
            if k == 1 and (uv.min() < -1e-4 or uv.max() > 1 + 1e-4):
                uv2_bad.append(o.name)
        if any("#blend" in m.name for m in o.data.materials) and len(layers) < 2:
            uv2_missing.append(o.name)
    check(worst_uv < 16.0, "base: every UV within +-16 (int16 / 2048 storage)", f"max |uv| {worst_uv:.2f}")
    check(not uv2_bad and not uv2_missing, "base: #blend meshes have UV2 within 0..1",
          f"outside {uv2_bad[:3]}, missing {uv2_missing[:3]}")
    # backdrop: far ground, horizon, cards (per environment)
    e = envs.env(env)
    want_cards = bool(e.get("treeCards")) or bool(e.get("skylineCards"))
    cards = [n for n in obs if n.startswith("backdrop_cards_")]
    check("backdrop_ground#lofi" in obs and "backdrop_horizon#lofi" in obs and (bool(cards) == want_cards),
          "base: backdrop far ground, #horizon and background cards (per environment)", f"{len(cards)} card strips")
    # props: stock subscenes allowed for this environment, existing in the game
    props = [o for o in obs.values() if o.name.startswith("#xref ")]
    names = sorted({re.sub(r"\.\d+$", "", o.name)[len("#xref ob/"):-len(".scne")] for o in props})
    allowed = envs.allowed_assets(env)
    check(len(props) > 0 and set(names) <= allowed, f"base: props are stock subscenes allowed for {env}",
          f"{len(props)} props, {len(names)} kinds; not allowed: {sorted(set(names) - allowed)}")
    missing = [n for n in names if not os.path.isfile(os.path.join(WF, "data/art/objects", n + ".scne"))]
    check(not missing, "base: every prop exists in the game data", ", ".join(missing))
    check_scenery(out, model, spec, manifest, len(props))
    check_roadside(obs, model, spec, manifest)
    check_maps(out, model, spec, manifest)
    # bounds
    worst = max(float(np.abs(world_verts(o)[:, :2]).max()) for o in meshes if len(o.data.vertices))
    check(worst <= C.LIMIT_M, "base: all vertices within +-2000 m", f"max |x|,|y| = {worst:.1f} m")

    road = obs["track_surface"]
    V = world_verts(road)
    F = faces_of(road)
    quads = [f for f in F if len(f) == 4]
    # 2 m squares
    along, lateral = [], []
    for f in quads:
        a, b, c, d = (V[i] for i in f)
        lateral += [np.linalg.norm((b - a)[:2]), np.linalg.norm((c - d)[:2])]
        along += [np.linalg.norm((d - a)[:2]), np.linalg.norm((c - b)[:2])]
    along, lateral = np.array(along), np.array(lateral)
    check(1.6 <= np.median(along) <= 2.4 and 1.4 <= np.median(lateral) <= 2.6 and along.max() < 4.5,
          "base: road built from ~2 m squares",
          f"along median {np.median(along):.2f} max {along.max():.2f}, lateral median {np.median(lateral):.2f}")
    # shared vertices: no two distinct vertices at the same place
    keys = defaultdict(list)
    for i, v in enumerate(V):
        keys[key(v, 3)].append(i)
    dups = sum(1 for k, v in keys.items() if len(v) > 1)
    check(dups == 0, "base: road vertices are unique (junction vertices shared, not duplicated)", f"{dups} duplicates")
    # manifold + cracks
    edges = defaultdict(int)
    for f in F:
        for a, b in zip(f, f[1:] + f[:1]):
            edges[(min(a, b), max(a, b))] += 1
    nonman = sum(1 for c in edges.values() if c > 2)
    check(nonman == 0, "base: road mesh has no edge shared by more than two faces", f"{nonman}")
    bnd = [e for e, c in edges.items() if c == 1]
    bverts = sorted({i for e in bnd for i in e})
    cracks = 0
    grid = defaultdict(list)
    for e in bnd:
        a, b = V[e[0]], V[e[1]]
        for gx in range(int(min(a[0], b[0]) // 4), int(max(a[0], b[0]) // 4) + 1):
            for gy in range(int(min(a[1], b[1]) // 4), int(max(a[1], b[1]) // 4) + 1):
                grid[gx, gy].append(e)
    for i in bverts:
        p = V[i]
        for e in grid.get((int(p[0] // 4), int(p[1] // 4)), []):
            if i in e:
                continue
            a, b = V[e[0]], V[e[1]]
            ab = b - a
            t = float(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-12))
            if 1e-3 < t < 1 - 1e-3 and np.linalg.norm(a + t * ab - p) < 1e-3:
                cracks += 1
    check(cracks == 0, "base: no cracks (T-junctions) in the road mesh", f"{cracks}")
    # overlaps: a face centre inside another face at nearly the same height
    tris = []
    for fi, f in enumerate(F):
        for k in range(1, len(f) - 1):
            tris.append((fi, V[f[0]], V[f[k]], V[f[k + 1]]))
    tgrid = defaultdict(list)
    for t in tris:
        xs = [t[1][0], t[2][0], t[3][0]]
        ys = [t[1][1], t[2][1], t[3][1]]
        for gx in range(int(min(xs) // 4), int(max(xs) // 4) + 1):
            for gy in range(int(min(ys) // 4), int(max(ys) // 4) + 1):
                tgrid[gx, gy].append(t)
    overlaps = 0
    for fi, f in enumerate(F):
        c = V[list(f)].mean(0)
        for t in tgrid.get((int(c[0] // 4), int(c[1] // 4)), []):
            if t[0] == fi:
                continue
            a, b, d = t[1], t[2], t[3]
            m = np.array([[b[0] - a[0], d[0] - a[0]], [b[1] - a[1], d[1] - a[1]]])
            det = np.linalg.det(m)
            if abs(det) < 1e-9:
                continue
            u, w = np.linalg.solve(m, c[:2] - a[:2])
            if u > 1e-4 and w > 1e-4 and u + w < 1 - 1e-4:
                z = a[2] + u * (b[2] - a[2]) + w * (d[2] - a[2])
                if abs(z - c[2]) < 1.0:
                    overlaps += 1
                    break
    check(overlaps == 0, "base: no overlapping road faces", f"{overlaps} faces overlap another")
    # junctions: stitched edge vertices are road vertices
    jv = to_scene(np.array([v for j in manifest.get("junctions", []) for v in j["edgeVertices"]]).reshape(-1, 3), manifest)
    miss = [v for v in jv if np.min(np.linalg.norm(V - v, axis=1)) > 1e-3]
    njoins = sum(1 for r in model["routes"] for j in (r.get("startJoin"), r.get("endJoin")) if j)
    check(not miss and len(manifest.get("junctions", [])) >= njoins,
          "base: junction stitches share the main road's edge vertices",
          f"{len(manifest.get('junctions', []))} stitches, {len(jv)} shared vertices")
    # bridge clearance
    for c in bridges:
        ob, ub = c["branches"][c["overBranch"]], c["branches"][1 - c["overBranch"]]
        clear = ob["z"] - C.BRIDGE_SLAB - ub["z"]
        check(clear >= C.BRIDGE_MIN_CLEARANCE, f"base: bridge {c['id']} clearance", f"{clear:.2f} m")
    # terrain stays below the road
    tiles = [o for o in meshes if o.name.startswith("terrain_")]
    tv, tf = [], []
    for o in tiles:
        base = len(tv)
        tv += [tuple(v) for v in world_verts(o)]
        tf += [tuple(i + base for i in f) for f in faces_of(o)]
    bvh = BVHTree.FromPolygons(tv, tf)
    above = 0
    for v in V[:: max(1, len(V) // 4000)]:
        hit = bvh.ray_cast(Vector((v[0], v[1], v[2] + 0.01)), Vector((0, 0, 1)))
        if hit[0] is not None:
            above += 1
    check(above == 0, "base: terrain stays below the road surface", f"{above} sampled road vertices under terrain")


# ----------------------------------------------------------------------------------------- layouts
def strip_lines(ob) -> list[tuple[tuple, tuple]]:
    V = world_verts(ob)
    F = faces_of(ob)
    lines = []
    for k, f in enumerate(F):
        if k == 0:
            lines.append((key(V[f[0]]), key(V[f[1]])))
        lines.append((key(V[f[3]]), key(V[f[2]])))
    return lines


def check_layout(out: str, lay: dict, slug: str, model: dict, spec: dict, manifest: dict) -> None:
    name = lay["name"]
    bpy.ops.wm.open_mainfile(filepath=os.path.join(out, lay["file"]))
    obs = {o.name: o for o in bpy.context.scene.objects}
    rev = lay["reverse"]
    for k in ("route", "safe", "race"):
        check(f"#ai_{k}_main" in obs and obs[f"#ai_{k}_main"].type == "MESH", f"{name}: #ai_{k}_main mesh exists")
    r, s, c = (obs[f"#ai_{k}_main"] for k in ("route", "safe", "race"))
    nf = len(r.data.polygons)
    check(len(s.data.polygons) == nf == len(c.data.polygons) and len(r.data.vertices) == 2 * (nf + 1),
          f"{name}: AI meshes are open strips with identical topology", f"{nf} sectors")
    cd = r.get("CustomData", "")
    rl = strip_lines(r)
    # bgeometry stores the route in its canonical orientation unless otherway is set (layout.needs_otherway)
    canonical = rl[0][1][1] <= rl[-1][0][1]
    check(("otherway = true" in cd) == (not canonical),
          f"{name}: otherway = true exactly when the driving-order strip isn't bgeometry's canonical orientation",
          f"{cd!r}, firstR.y {rl[0][1][1]:.1f}, lastL.y {rl[-1][0][1]:.1f}")
    # the racing strip (inside the road) lies on the road surface, across its camber too
    multi = model.get("layouts") or []
    lay_def = next((d for d in multi if d["id"] == ("main" if lay["key"] == "forward" else lay["key"])), None)
    if lay_def is not None and lay_def["id"] != "main":
        # an imported alternative layout drives its own closed route (main, branch, main)
        cr = composite_route(model, lay_def)
        zs, le, ri = (to_scene(a, manifest) for a in (cr.p, cr.left, cr.right))
    else:
        rm = next(rt for rt in model["routes"] if rt["aiIndex"] == 0)
        zs = to_scene(np.array([[q["x"], q["y"], q["z"]] for q in rm["samples"]]), manifest)
        le, ri = to_scene(np.array(rm["leftEdge"]), manifest), to_scene(np.array(rm["rightEdge"]), manifest)
    worst = 0.0
    n = len(zs)
    for v in world_verts(c):
        # nearest sample, ignoring the other level at a bridge (the strip passes over or under it)
        k = int(np.argmin(np.hypot(*(zs[:, :2] - v[:2]).T) + 100.0 * (np.abs(zs[:, 2] - v[2]) > 3.0)))
        j = min(((k - 1) % n, (k + 1) % n), key=lambda q: float(np.hypot(*(zs[q, :2] - v[:2]))))
        seg = zs[j, :2] - zs[k, :2]
        f = float(np.clip(np.dot(v[:2] - zs[k, :2], seg) / max(float(np.dot(seg, seg)), 1e-9), 0.0, 1.0))
        L, R = le[k] + (le[j] - le[k]) * f, ri[k] + (ri[j] - ri[k]) * f
        across = (L - R)[:2]
        t = float(np.dot(v[:2] - R[:2], across) / max(float(np.dot(across, across)), 1e-9))
        worst = max(worst, abs(float(v[2]) - (R[2] + t * (L[2] - R[2]))))
    check(worst < 0.05, f"{name}: #ai_race_main lies on the (cambered) road surface", f"max |dz| {worst:.3f} m")
    main_lines = set(strip_lines(r))
    if multi:
        alts = [o for o in obs if re.match(r"^#ai_(route|safe|race)_alt\d+$", o)]
        check(not alts, f"{name}: an imported layout has no alternative-route strips", ", ".join(alts))
        if lay_def is not None and lay_def["id"] != "main":
            check(abs(cr.length - float(lay_def["lapLengthM"])) < 1.0, f"{name}: layout route length matches the model",
                  f"{cr.length:.1f} vs {lay_def['lapLengthM']:.1f} m")
    for rt in model["routes"]:
        if rt["aiIndex"] == 0 or multi:
            continue
        sfx = f"alt{-rt['aiIndex']}"
        okn = all(f"#ai_{k}_{sfx}" in obs for k in ("route", "safe", "race"))
        check(okn, f"{name}: alt route {rt['id']} -> #ai_*_{sfx}")
        if okn:
            al = strip_lines(obs[f"#ai_route_{sfx}"])
            check(al[0] in main_lines and al[-1] in main_lines,
                  f"{name}: #ai_route_{sfx} starts and ends on main route sector lines (exact coordinates)")
    # checkpoints
    cps = sorted(n for n in obs if n.startswith("#checkpoint"))
    pat = re.compile(r"^#checkpoint(\d\d)(_split)?(_alt\d+)?$")
    nums = [int(pat.match(n).group(1)) for n in cps if pat.match(n)]
    check(len(nums) == len(cps) and nums == list(range(1, len(cps) + 1)) and len(cps) >= 2,
          f"{name}: checkpoints #checkpointNN numbered 1..N", ", ".join(cps))
    rw = np.linalg.norm(np.subtract(*strip_lines(r)[0])[:2])
    narrow = [n for n in cps if np.linalg.norm((world_verts(obs[n])[0] - world_verts(obs[n])[1])[:2]) < rw]
    check(not narrow, f"{name}: checkpoint planes span the AI route", ", ".join(narrow))
    first = lay["checkpoints"][0]
    grid = model["grid"]
    check(cps[0].startswith("#checkpoint01") and first["lapPos"] > 0, f"{name}: checkpoint 01 lies after the grid",
          f"lap position {first['lapPos']} m")
    # start points
    n = grid["size"]
    sp = [f"#startpoint{k:02d}" for k in range(1, n + 1)]
    check(all(x in obs and obs[x].type == "EMPTY" for x in sp) and "#startpointcarchange" in obs,
          f"{name}: {n} start points + #startpointcarchange")
    main = next(rt for rt in model["routes"] if rt["aiIndex"] == 0)
    pts = to_scene(np.array([[q["x"], q["y"]] for q in main["samples"]]), manifest)
    hd = np.array([q["heading"] for q in main["samples"]]) + math.radians(manifest.get("transform", {}).get("rotateDeg", 0))
    off = []
    for x in sp + ["#startpointcarchange"]:
        o = obs[x]
        i = int(np.argmin(np.hypot(*(pts - np.array(o.location[:2])).T)))
        fwd = o.rotation_euler.z + math.pi / 2.0
        want = hd[i] + (math.pi if rev else 0.0)
        off.append(abs((fwd - want + math.pi) % (2 * math.pi) - math.pi))
    check(max(off) < math.radians(15), f"{name}: start points face the driving direction (local +Y)",
          f"max {math.degrees(max(off)):.1f} deg")
    # the AI strip runs in the driving order (faces from the start line onwards), as the start points face
    m0 = (np.array(rl[0][0]) + np.array(rl[0][1])) / 2.0
    m1 = (np.array(rl[1][0]) + np.array(rl[1][1])) / 2.0
    a = obs["#startpoint01"].rotation_euler.z + math.pi / 2.0
    check(float(np.dot((m1 - m0)[:2], (math.cos(a), math.sin(a)))) > 0,
          f"{name}: #ai_route_main faces run in the driving direction (from the start line)")
    # base subscene and editing helpers
    xr = f"#xref {slug}_base.scne"
    check(xr in obs and obs[xr].get("xdir") == f"data/track/{slug}" and obs[xr].get("CustomData") == "vis = true|col = true",
          f"{name}: base subscene reference")
    check("ai_path" in obs and obs["ai_path"].type == "CURVE" and not obs["ai_path"].data.splines[0].use_cyclic_u
          and obs["ai_path"].users_collection[0].name.endswith("#exclude"), f"{name}: ai_path is an open curve (not exported)")
    key_ = "reverse" if rev else "forward"
    for j in model["jumps"]:
        want = (not j["inBase"]) and key_ in j["layouts"]
        check((f"jump_{j['id']}" in obs) == want, f"{name}: layout-only jump {j['id']} {'present' if want else 'absent'}")
    worst = max([float(np.abs(world_verts(o)[:, :2]).max()) for o in obs.values() if o.type == "MESH"] + [0.0])
    check(worst <= C.LIMIT_M, f"{name}: all vertices within +-2000 m", f"{worst:.1f} m")


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    out, spec_path = argv[0], argv[1]
    spec, model, manifest = (load_json(p) for p in (spec_path, os.path.join(out, "model.json"),
                                                     os.path.join(out, "manifest.json")))
    slug = model["slug"]
    if model.get("layouts"):  # imported track with alternative layouts: one layout each, main first
        want = ["forward"] + [d["id"] for d in model["layouts"] if d["id"] != "main"]
    else:
        want = ["forward"] + (["reverse"] if model["reverse"] else [])
    check([lay["key"] for lay in manifest["layouts"]] == want, "manifest: layouts", ", ".join(want))
    check_base(out, slug, model, spec, manifest)
    for lay in manifest["layouts"]:
        check_layout(out, lay, slug, model, spec, manifest)
    ends = []
    for lay in manifest["layouts"]:
        bpy.ops.wm.open_mainfile(filepath=os.path.join(out, lay["file"]))
        for o in bpy.context.scene.objects:
            if o.name.startswith("#ai_") and o.type == "MESH":
                v = world_verts(o)
                ends.append(max(v[:2, 1].max(), v[-2:, 1].max()))
    check(max(ends) <= 0.0, "layouts: every AI strip starts and ends at y <= 0 (bgeometry startZ assert)",
          f"max {max(ends):.2f} m")
    print(f"CHECKS {'FAILED' if failures else 'OK'}: {len(failures)} failures")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
