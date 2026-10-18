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

WF = os.environ.get("WRECKFEST_DIR", "/wreckfest")
failures: list[str] = []


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
    check(bool(walls) == (opts.get("walls") in ("concrete", "tyres")), "base: walls follow options.walls",
          f"{len(walls)} wall objects")
    bridges = [c for c in model["crossings"] if c["type"] == "bridge" and c.get("detected", True)]
    for c in bridges:
        check(f"bridge_{c['id']}_deck" in obs and f"bridge_{c['id']}_barriers" in obs,
              f"base: bridge {c['id']} deck and barriers exist")
    # collision: every solid/drivable mesh has the String property CustomData containing col = true
    bad = [o.name for o in meshes if o.name != "#static_cube_map"
           and not (isinstance(o.get("CustomData"), str) and "col = true" in o["CustomData"])]
    check(not bad, "base: every mesh has CustomData 'col = true' (String)", ", ".join(bad[:5]))
    # materials
    bad_mats, missing_tex = [], []
    for o in meshes:
        for m in o.data.materials:
            parts = m.name.split("#")
            if o.name != "#static_cube_map" and (len(parts) < 2 or parts[1] not in C.VALID_SURFACES):
                bad_mats.append(m.name)
            if m.node_tree:
                for nd in m.node_tree.nodes:
                    if nd.type == "TEX_IMAGE" and nd.image:
                        p = nd.image.filepath
                        rel = p[p.lower().index("/data/") + 1:] if "/data/" in p.lower() else p
                        bm = os.path.join(WF, rel[:-4] + ".bmap")
                        if not (rel.startswith("data/") and os.path.isfile(bm)):
                            missing_tex.append(p)
    check(not bad_mats, "base: materials are name#surface with a valid surface", ", ".join(sorted(set(bad_mats))))
    check(not missing_tex, "base: textures reference existing stock game textures", ", ".join(sorted(set(missing_tex))))
    # props
    props = [o for o in obs.values() if o.name.startswith("#xref ")]
    names = sorted({re.sub(r"\.\d+$", "", o.name)[len("#xref ob/"):-len(".scne")] for o in props})
    allowed = set(C.PROPS[opts.get("environment", "gravel_pit")])
    check(len(props) > 0 and set(names) <= allowed, "base: props are preset stock subscenes", f"{len(props)} props, {names}")
    missing = [n for n in names if not os.path.isfile(os.path.join(WF, "data/art/objects", n + ".scne"))]
    check(not missing, "base: every prop exists in the game data", ", ".join(missing))
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
    check(("otherway = true" in cd) == rev, f"{name}: otherway = true only on the reverse layout", repr(cd))
    main_lines = set(strip_lines(r))
    for rt in model["routes"]:
        if rt["aiIndex"] == 0:
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
