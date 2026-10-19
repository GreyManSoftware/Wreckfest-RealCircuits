"""Comparison renders of a built base scene (or a stock base) with stock textures and props (Blender, CPU).

Not part of the build; a review tool (worker/builder/render/README.md). Run in the worker image:

    blender -b --factory-startup --python render_scene.py -- library R               # R/props/*.raw -> R/props.blend
    blender -b --factory-startup --python render_scene.py -- stock R BASE.raw LAYOUT.raw   # -> R/stock.blend
    blender -b --factory-startup --python render_scene.py -- needs R SCENE_DIR...      # -> R/needed.json
    blender -b --factory-startup --python render_scene.py -- render R SCENE_DIR|stock OUT_PREFIX [samples]

SCENE_DIR is a builder output folder (<slug>_base.blend, manifest.json, model.json, mod/ with the generated
TGA maps). Stock textures come from R/tex/<data path>.png, made on the host by prep.py (the image has no lz4).
Props are imported once with Toolbox's SCNE importer into R/props.blend and instanced at every #xref empty.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import types

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, os.environ.get("WORKER_BLENDER_LIB", "/opt/worker/blender"))
import wf_toolbox_headless as tb  # noqa: E402

JUNK = re.compile(r"(#col|collision|colision|colizi|#shadow|shadow\d|_lod[1-9]|#lod[1-9]|resetbox|^box\d*$|college)", re.I)
XREF = re.compile(r"(?:ob/|objects/)([A-Za-z0-9_]+)\.scn")
CAPTURED: dict[str, dict] = {}


# ----------------------------------------------------------------------------------------- import
def importer():
    tb.enable()
    import importlib
    imp = importlib.import_module("wreckfest_toolbox.addons.io_import_wreckfest")

    def cap(txtrList, matName, filepath, spec, gloss, imp_tga, textureUV, textureScale):  # noqa: N803
        CAPTURED[matName] = {"slots": [[int(k), p.replace(".bmap", ".tga")] for k, p in txtrList],
                             "uv": {str(k): int(v) for k, v in textureUV.items()},
                             "scale": {str(k): [float(x) for x in v][:2] for k, v in textureScale.items()}}
        if bpy.data.materials.get(matName) is None:
            bpy.data.materials.new(matName)

    def uv(mesh, uvs):
        name = "UVMap" if not mesh.uv_layers else "UVMap2"
        lay = mesh.uv_layers.new(name=name)
        a = np.asarray(uvs, dtype=np.float64).reshape(-1, 2) / 2048.0
        vi = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("vertex_index", vi)
        if len(a) <= vi.max(initial=0):
            return
        out = np.stack([a[vi, 0], 1.0 - a[vi, 1]], axis=1)
        lay.data.foreach_set("uv", out.ravel())

    imp.add_wf_material = cap
    imp.uv = types.SimpleNamespace(uv=uv)
    return imp


def cmd_library(root: str) -> None:
    imp = importer()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    lib = bpy.data.collections.new("props")
    bpy.context.scene.collection.children.link(lib)
    pdir = os.path.join(root, "props")
    for f in sorted(os.listdir(pdir)):
        if not f.endswith(".scne.raw"):
            continue
        name = f[:-len(".scne.raw")]
        before = set(bpy.data.objects)
        try:
            imp.read_scne(bpy.context, os.path.join(pdir, f), imp_model=True, imp_mat=True, imp_subscn=False)
        except Exception as e:  # noqa: BLE001
            print("IMPORT FAIL", name, e)
            continue
        coll = bpy.data.collections.new("prop:" + name)
        lib.children.link(coll)
        for ob in set(bpy.data.objects) - before:
            for c in list(ob.users_collection):
                c.objects.unlink(ob)
            if ob.type != "MESH" or JUNK.search(ob.name):
                bpy.data.objects.remove(ob)
                continue
            coll.objects.link(ob)
        print("prop", name, len(coll.objects))
    for c in list(bpy.data.collections):
        if c.name not in ("props",) and not c.name.startswith("prop:") and c.users == 0:
            bpy.data.collections.remove(c)
    with open(os.path.join(root, "props_materials.json"), "w") as fh:
        json.dump(CAPTURED, fh, indent=0)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(root, "props.blend"))


def cmd_stock(root: str, base: str, layout: str) -> None:
    imp = importer()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    imp.read_scne(bpy.context, base, imp_model=True, imp_mat=True, imp_subscn=True, imp_subscn_mdl=False)
    if layout:
        imp.read_scne(bpy.context, layout, imp_model=False, imp_mat=False, imp_startpt=True)
    with open(os.path.join(root, "stock_materials.json"), "w") as fh:
        json.dump(CAPTURED, fh, indent=0)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(root, "stock.blend"))


def cmd_needs(root: str, dirs: list[str]) -> None:
    need: set[str] = set()
    for fn in ("props_materials.json", "stock_materials.json"):
        p = os.path.join(root, fn)
        if os.path.isfile(p):
            for m in json.load(open(p)).values():
                need |= {s[1] for s in m["slots"]}
    for d in dirs:
        man = json.load(open(os.path.join(d, "manifest.json")))
        for m in man.get("materials", {}).values():
            need |= {s[1] for s in m["slots"]}
    from wfbuild import config as C  # noqa: E402
    need |= {v[1] for v in C.MATERIALS.values()}
    with open(os.path.join(root, "needed.json"), "w") as fh:
        json.dump(sorted(need), fh, indent=0)
    print(len(need), "textures")


# --------------------------------------------------------------------------------------- materials
class Tex:
    def __init__(self, root: str, mod_root: str | None):
        self.root, self.mod_root = root, mod_root
        self.cache: dict[str, bpy.types.Image | None] = {}

    def get(self, rel: str):
        rel = rel.replace("\\", "/")
        if rel in self.cache:
            return self.cache[rel]
        stem = rel.rsplit(".", 1)[0]
        cands = []
        if rel.startswith("data/track/") and self.mod_root:
            cands.append(os.path.join(self.mod_root, stem + ".tga"))
        cands.append(os.path.join(self.root, "tex", stem + ".png"))
        img = None
        for c in cands:
            if os.path.isfile(c):
                img = bpy.data.images.load(c, check_existing=True)
                if "_n." in c or "_ns." in c or "_s." in c or "blend" in c or "clutter" in c:
                    img.colorspace_settings.name = "Non-Color"
                break
        self.cache[rel] = img
        return img


def _img_node(nt, img, uv_name, scale, x, y):
    tn = nt.nodes.new("ShaderNodeTexImage")
    tn.image = img
    tn.location = (x, y)
    uvn = nt.nodes.new("ShaderNodeUVMap")
    uvn.uv_map = uv_name
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (scale[0] or 1.0, scale[1] or 1.0, 1.0)
    nt.links.new(uvn.outputs["UV"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], tn.inputs["Vector"])
    return tn


def render_material(name: str, slots: list[tuple[int, str, int, tuple[float, float]]], tex: Tex, has_uv2: bool):
    """Approximate Wreckfest's shaders in Cycles: #blend mixes three albedos by the blend map with the colour map
    as soft light; decals, foliage, horizon and cards use the albedo's alpha."""
    mat = bpy.data.materials.new("R_" + name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    outn = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = 0.85
    nt.links.new(bsdf.outputs["BSDF"], outn.inputs["Surface"])
    by = {s[0]: s for s in slots}
    tags = name.lower().split("#")[1:]
    uvn = lambda u: "UVMap2" if (u == 2 and has_uv2) else "UVMap"  # noqa: E731
    if "blend" in tags and 0 in by and 6 in by:
        imgs = [(tex.get(by[k][1]), by[k]) for k in (0, 2, 4) if k in by]
        bm = tex.get(by[6][1])
        if bm is None or any(i is None for i, _ in imgs):
            bsdf.inputs["Base Color"].default_value = (0.3, 0.35, 0.2, 1)
            return mat
        nodes = [_img_node(nt, i, uvn(s[2]), s[3], -900, 300 - 300 * k) for k, (i, s) in enumerate(imgs)]
        bn = _img_node(nt, bm, uvn(2), (1, 1), -900, -700)
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        nt.links.new(bn.outputs["Color"], sep.inputs["Color"])
        acc = None
        wsum = None
        for k, n in enumerate(nodes):
            mul = nt.nodes.new("ShaderNodeMix")
            mul.data_type = "RGBA"
            mul.blend_type = "MULTIPLY"
            mul.inputs["Factor"].default_value = 1.0
            nt.links.new(n.outputs["Color"], mul.inputs[6])
            nt.links.new(sep.outputs[k], mul.inputs[7])
            if acc is None:
                acc, wsum = mul.outputs[2], sep.outputs[k]
            else:
                add = nt.nodes.new("ShaderNodeMix")
                add.data_type = "RGBA"
                add.blend_type = "ADD"
                add.inputs["Factor"].default_value = 1.0
                nt.links.new(acc, add.inputs[6])
                nt.links.new(mul.outputs[2], add.inputs[7])
                acc = add.outputs[2]
                m2 = nt.nodes.new("ShaderNodeMath")
                m2.operation = "ADD"
                nt.links.new(wsum, m2.inputs[0])
                nt.links.new(sep.outputs[k], m2.inputs[1])
                wsum = m2.outputs[0]
        mx = nt.nodes.new("ShaderNodeMath")
        mx.operation = "MAXIMUM"
        mx.inputs[1].default_value = 1e-3
        nt.links.new(wsum, mx.inputs[0])
        div = nt.nodes.new("ShaderNodeMix")
        div.data_type = "RGBA"
        div.blend_type = "DIVIDE"
        div.inputs["Factor"].default_value = 1.0
        nt.links.new(acc, div.inputs[6])
        comb = nt.nodes.new("ShaderNodeCombineColor")
        for k in range(3):
            nt.links.new(mx.outputs[0], comb.inputs[k])
        nt.links.new(comb.outputs[0], div.inputs[7])
        col = div.outputs[2]
        if 9 in by and tex.get(by[9][1]) is not None:
            cn = _img_node(nt, tex.get(by[9][1]), uvn(2), (1, 1), -900, -1000)
            sl = nt.nodes.new("ShaderNodeMix")
            sl.data_type = "RGBA"
            sl.blend_type = "SOFT_LIGHT"
            sl.inputs["Factor"].default_value = 1.0
            nt.links.new(col, sl.inputs[6])
            nt.links.new(cn.outputs["Color"], sl.inputs[7])
            col = sl.outputs[2]
        nt.links.new(col, bsdf.inputs["Base Color"])
        return mat
    s1 = by.get(1)
    img = tex.get(s1[1]) if s1 else None
    if img is None:
        bsdf.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1)
        if "ghost" in name.lower() or "tmp_ghost" in str(slots):
            bsdf.inputs["Alpha"].default_value = 0.0
        return mat
    tn = _img_node(nt, img, uvn(s1[2]), s1[3], -600, 200)
    nt.links.new(tn.outputs["Color"], bsdf.inputs["Base Color"])
    alpha_src = None
    if 6 in by and tex.get(by[6][1]) is not None:
        an = _img_node(nt, tex.get(by[6][1]), uvn(by[6][2]), by[6][3], -600, -200)
        alpha_src = an.outputs["Alpha"]
    elif (s1[1].lower().endswith(("_c5.tga", "_c5.png")) or "xtree" in tags or "twosided" in tags
          or any(k in s1[1].lower() for k in ("foliage", "chainlink", "fence"))):  # alpha-tested in game
        alpha_src = tn.outputs["Alpha"]
    if "ghost" in name.lower():
        bsdf.inputs["Alpha"].default_value = 0.0
    elif alpha_src is not None:
        nt.links.new(alpha_src, bsdf.inputs["Alpha"])
    if "horizon" in tags:
        em = nt.nodes.new("ShaderNodeEmission")
        nt.links.new(tn.outputs["Color"], em.inputs["Color"])
        em.inputs["Strength"].default_value = 0.9
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        if alpha_src is not None:
            nt.links.new(alpha_src, mix.inputs[0])
        nt.links.new(tr.outputs[0], mix.inputs[1])
        nt.links.new(em.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], outn.inputs["Surface"])
    return mat


def slots_from_spec(spec: dict) -> list:
    return [(int(s[0]), s[1], int(s[2]), tuple(s[3])) for s in spec["slots"]]


def slots_from_captured(c: dict) -> list:
    out = []
    for slot, path in c["slots"]:
        key = str(slot - (slot % 2)) if slot < 6 else ("7" if slot == 7 else None)
        uv = 1 + int(c["uv"].get(key, 0)) if key is not None else 2
        sc = tuple(c["scale"].get(key, [1.0, 1.0])) if key is not None else (1.0, 1.0)
        if slot >= 6 and slot != 7:
            uv, sc = 2, (1.0, 1.0)
        if slot in (1,) and not any(s == 0 for s, _ in c["slots"]):
            uv, sc = 1, (1.0, 1.0)
        out.append((slot, path, uv, sc))
    return out


# -------------------------------------------------------------------------------------- render
def link_props(root: str, names: set[str]) -> dict[str, bpy.types.Collection]:
    path = os.path.join(root, "props.blend")
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.collections = [c for c in src.collections if c.startswith("prop:") and c[5:] in names]
    out = {}
    for c in dst.collections:
        if c is not None:
            out[c.name[5:]] = c
    return out


def apply_materials(objs, resolve) -> None:
    done: dict[str, bpy.types.Material] = {}
    for ob in objs:
        if ob.type != "MESH":
            continue
        has_uv2 = len(ob.data.uv_layers) > 1
        for k, m in enumerate(ob.data.materials):
            if m is None:
                continue
            key = (m.name, has_uv2)
            if key not in done:
                slots = resolve(m.name)
                done[key] = render_material(m.name, slots, TEX, has_uv2) if slots is not None else m
            ob.data.materials[k] = done[key]


TEX: Tex


def cmd_render(root: str, target: str, prefix: str, samples: int) -> None:
    global TEX
    stock = target == "stock"
    if stock:
        bpy.ops.wm.open_mainfile(filepath=os.path.join(root, "stock.blend"))
        caps = json.load(open(os.path.join(root, "stock_materials.json")))
        TEX = Tex(root, None)
        resolve = lambda n: slots_from_captured(caps[n]) if n in caps else None  # noqa: E731
        man = None
    else:
        man = json.load(open(os.path.join(target, "manifest.json")))
        bpy.ops.wm.open_mainfile(filepath=os.path.join(target, f"{man['slug']}_base.blend"))
        TEX = Tex(root, os.path.join(target, "mod"))
        from wfbuild import config as C  # noqa: E402
        specs = {v["name"]: slots_from_spec(v) for v in man["materials"].values()}
        for _k, (nm, path, _g) in C.MATERIALS.items():
            specs[nm] = [(1, path, 1, (1.0, 1.0))] + ([(6, path, 1, (1.0, 1.0))] if nm == "ghost" else [])
        resolve = lambda n: specs.get(n)  # noqa: E731
    scene_objs = list(bpy.context.scene.objects)
    apply_materials(scene_objs, resolve)
    # props
    xrefs = [o for o in scene_objs if o.name.startswith("#xref") and XREF.search(o.name)]
    names = {XREF.search(o.name).group(1).lower() for o in xrefs}
    lib = link_props(root, names)
    pcaps = json.load(open(os.path.join(root, "props_materials.json")))
    for c in lib.values():
        apply_materials(list(c.all_objects), lambda n: slots_from_captured(pcaps[n]) if n in pcaps else None)
    missing = set()
    for o in xrefs:
        n = XREF.search(o.name).group(1).lower()
        if n not in lib:
            missing.add(n)
            continue
        if o.type == "EMPTY":
            o.instance_type = "COLLECTION"
            o.instance_collection = lib[n]
        else:  # the importer's placeholder cube: replace it with an instancer at the same transform
            e = bpy.data.objects.new(o.name + "_inst", None)
            loc, rq, _sc = o.matrix_world.decompose()
            e.location, e.rotation_mode, e.rotation_quaternion = loc, "QUATERNION", rq
            e.instance_type = "COLLECTION"
            e.instance_collection = lib[n]
            bpy.context.scene.collection.objects.link(e)
            o.hide_render = True
    print(f"props: {len(xrefs)} instances, {len(lib)} kinds, missing {sorted(missing)}")
    # hide helpers
    for o in scene_objs:
        if (o.name.startswith("#static_cube_map") or o.name.startswith("#checkpoint") or o.name.startswith("#ai_")
                or "vis = false" in str(o.get("CustomData", ""))):  # invisible collision walls, as in game
            o.hide_render = True
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.render.threads_mode = "FIXED"
    sc.render.threads = int(os.environ.get("RENDER_THREADS", "8"))
    sc.render.resolution_x, sc.render.resolution_y = 1600, 900
    sc.view_settings.view_transform = "AgX"
    world = bpy.data.worlds.new("sky")
    sc.world = world
    world.use_nodes = True
    sky = world.node_tree.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "NISHITA"
    sky.sun_elevation = math.radians(32)
    sky.sun_rotation = math.radians(140)
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Strength"].default_value = 0.35
    world.node_tree.links.new(sky.outputs["Color"], bg.inputs["Color"])
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 2.6
    sun.rotation_euler = (math.radians(58), 0, math.radians(140 + 180))
    sc.collection.objects.link(sun)
    cams = cameras(stock, man, target, scene_objs)
    for tag, (loc, look, lens) in cams.items():
        cd = bpy.data.cameras.new(tag)
        cd.lens = lens
        cd.clip_start, cd.clip_end = 0.3, 8000.0
        cam = bpy.data.objects.new(tag, cd)
        sc.collection.objects.link(cam)
        cam.location = loc
        d = Vector(look) - Vector(loc)
        cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        sc.camera = cam
        sc.render.filepath = f"{prefix}-{tag}.png"
        bpy.ops.render.render(write_still=True)
        print("RENDERED", sc.render.filepath)


def horizon_camera(c, R) -> tuple:
    """Across the terrain from its south edge at 30 m, looking north just below level: the far ground, the
    background cards and the #horizon ring, as a driver sees them on a crest."""
    loc = (c[0], c[1] - 0.95 * R, c[2] + 30.0)
    return (loc, (c[0], c[1] + 2.0 * R, c[2] - 0.08 * 3.0 * R), 22.0)


def cameras(stock: bool, man, target: str, objs) -> dict:
    # the oblique and horizon views frame the terrain (generated and stock alike), so they compare like for like
    terr = [o for o in objs if o.type == "MESH" and o.name.startswith("terrain")]
    pts = np.concatenate([np.array([o.matrix_world @ Vector(c) for c in o.bound_box]) for o in terr])
    c = (pts.min(0) + pts.max(0)) / 2
    R = float(np.linalg.norm(pts.max(0)[:2] - pts.min(0)[:2])) / 2
    # oblique: over the south-west corner, looking past the centre so the frame's top edge sits at the horizon
    # (far ground, background cards and the #horizon ring show, as they do in game from a crest)
    views = {"oblique": ((c[0] - 0.55 * R, c[1] - 0.75 * R, c[2] + 0.42 * R), (c[0], c[1] + 0.35 * R, c[2]), 30.0),
             "horizon": horizon_camera(c, R)}
    # keep the horizon camera 12 m above any ground within 150 m (real ground can rise above the terrain's middle)
    loc, look, lens = views["horizon"]
    near = []
    for o in objs:
        if o.type == "MESH" and (o.name.startswith("terrain") or o.name.startswith("backdrop_ground")):
            v = np.array([o.matrix_world @ vv.co for vv in o.data.vertices])
            if len(v):
                near.append(v[np.hypot(v[:, 0] - loc[0], v[:, 1] - loc[1]) < 150.0, 2])
    top = max((float(z.max()) for z in near if len(z)), default=-1e9)
    if top + 12.0 > loc[2]:
        dz = top + 12.0 - loc[2]
        views["horizon"] = ((loc[0], loc[1], loc[2] + dz), (look[0], look[1], look[2] + dz), lens)
    if stock:
        sp = next((o for o in objs if o.name.startswith("#startpoint01")), None)
        out = dict(views)
        if sp is not None:
            m = sp.matrix_world
            fwd = (m.to_3x3() @ Vector((0, 1, 0))).normalized()
            p = m.translation
            out["driver"] = (tuple(p - fwd * 7 + Vector((0, 0, 2.2))), tuple(p + fwd * 60 + Vector((0, 0, 0.5))), 24.0)
        return out
    model = json.load(open(os.path.join(target, "model.json")))
    t = man["transform"]
    rot = t["rotateDeg"]
    shift = np.array(t["shift"])

    def tp(p):
        cc, ss = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[rot]
        return np.array([p[0] * cc - p[1] * ss, p[0] * ss + p[1] * cc, p[2]]) + shift

    pole = model["grid"]["slots"][0]
    h = pole["heading"] + math.radians(rot)
    fwd = np.array([math.cos(h), math.sin(h), 0.0])
    p = tp(pole["position"])
    out = {**views,
           "driver": (tuple(p - fwd * 7 + np.array([0, 0, 2.2])), tuple(p + fwd * 60 + np.array([0, 0, 0.5])), 24.0)}
    # extra driver views along the main route: RENDER_DRIVER_AT="mountain=2580,hallbends=2900" (name=metres from
    # the start line), 2.2 m above the road centre looking at the road 60 m ahead
    main = next(r for r in model["routes"] if r["aiIndex"] == 0)
    smp = main["samples"]
    s_all = np.array([q["s"] for q in smp])
    for item in filter(None, os.environ.get("RENDER_DRIVER_AT", "").split(",")):
        name, _, s_txt = item.partition("=")
        s0 = float(s_txt) % main["lengthM"]
        i = int(np.argmin(np.abs(s_all - s0)))
        j = int(np.argmin(np.abs(s_all - (s0 + 60.0) % main["lengthM"])))
        a, b = tp([smp[i]["x"], smp[i]["y"], smp[i]["z"]]), tp([smp[j]["x"], smp[j]["y"], smp[j]["z"]])
        out[f"driver-{name.strip()}"] = (tuple(a + np.array([0, 0, 2.2])), tuple(b + np.array([0, 0, 0.8])), 24.0)
    return out


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    cmd, root = argv[0], argv[1]
    sys.path.insert(0, os.environ.get("BUILDER_DIR", "/opt/worker/builder"))
    if cmd == "library":
        cmd_library(root)
    elif cmd == "stock":
        cmd_stock(root, argv[2], argv[3] if len(argv) > 3 else "")
    elif cmd == "stocklist":
        bpy.ops.wm.open_mainfile(filepath=os.path.join(root, "stock.blend"))
        names = sorted({XREF.search(o.name).group(1).lower() for o in bpy.data.objects
                        if o.name.startswith("#xref") and XREF.search(o.name)})
        with open(os.path.join(root, "stock_props.txt"), "w") as fh:
            fh.write("\n".join(names))
        print(len(names), "stock prop kinds")
    elif cmd == "needs":
        cmd_needs(root, argv[2:])
    elif cmd == "render":
        cmd_render(root, argv[2], argv[3], int(argv[4]) if len(argv) > 4 else 24)


if __name__ == "__main__":
    main()
