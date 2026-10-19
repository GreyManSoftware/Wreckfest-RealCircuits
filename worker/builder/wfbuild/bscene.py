"""Blender side: turn builder meshes, empties and curves into objects, materials and .blend files."""
from __future__ import annotations

import os

import bpy
import numpy as np

from . import config as C
from .geometry import Mesh
from .layout import Curve, Empty

LAYOUT_MATERIALS = {
    # Helper colours like Toolbox's AI Route Generator; no surface tag (these objects are not drawn).
    "ai_route": ("blue-route", (0.0, 0.0, 1.0, 0.1)),
    "ai_safe": ("red-route", (1.0, 0.0, 0.0, 0.15)),
    "ai_race": ("green-route", (0.0, 0.8, 0.1, 1.0)),
    "checkpoint": ("checkpoint", (1.0, 0.8, 0.0, 0.3)),
}


def reset() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.curves,
                 bpy.data.node_groups):
        for item in list(coll):
            coll.remove(item)


def wf_root() -> str:
    return os.environ.get("WRECKFEST_DIR", "/wreckfest")


SPECS: dict[str, dict] = {}   # multi-slot material specs (materials.build), set by build_scene
SLOT_GROUP = "trackgen#slots"  # Toolbox exports a node group named with '#': input k -> texture slot k


def set_specs(specs: dict[str, dict]) -> None:
    SPECS.clear()
    SPECS.update(specs)


def _slot_group() -> bpy.types.NodeTree:
    g = bpy.data.node_groups.get(SLOT_GROUP)
    if g is None:
        g = bpy.data.node_groups.new(SLOT_GROUP, "ShaderNodeTree")
        for i in range(12):
            g.interface.new_socket(f"slot{i}", in_out="INPUT", socket_type="NodeSocketColor")
    return g


def image_path(rel: str, mod_root: str | None = None) -> str:
    """A data/... texture path as Blender sees it: generated maps under the build's mod folder, stock textures
    in the mounted install. Toolbox writes the path from 'data/' on."""
    if rel.startswith("data/track/") and mod_root:
        return os.path.join(mod_root, rel)
    return os.path.join(wf_root(), rel)


MOD_ROOT: list[str] = []


def spec_material(spec: dict) -> bpy.types.Material:
    """A multi-slot material: image nodes (with Mapping scale and a UVMap/UVMap2 node) feeding the 12-input
    '#' node group, which Toolbox exports slot by slot (verified through bgeometry, visual-assets.md)."""
    mat = bpy.data.materials.get(spec["name"])
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(spec["name"])
    mat.use_nodes = True
    nt = mat.node_tree
    gn = nt.nodes.new("ShaderNodeGroup")
    gn.node_tree = _slot_group()
    for n, (slot, rel, uv, scale) in enumerate(spec["slots"]):
        img = bpy.data.images.get(rel) or bpy.data.images.new(rel, width=1, height=1)
        img.source = "FILE"
        img.filepath = image_path(rel, MOD_ROOT[0] if MOD_ROOT else None)
        tn = nt.nodes.new("ShaderNodeTexImage")
        tn.image = img
        tn.location = (-500, 600 - 160 * n)
        nt.links.new(tn.outputs["Color"], gn.inputs[slot])
        mp = nt.nodes.new("ShaderNodeMapping")
        mp.inputs["Scale"].default_value = (scale[0], scale[1], 1.0)
        uvn = nt.nodes.new("ShaderNodeUVMap")
        uvn.uv_map = "UVMap2" if uv == 2 else "UVMap"
        nt.links.new(uvn.outputs["UV"], mp.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], tn.inputs["Vector"])
    return mat


def material(key: str) -> bpy.types.Material:
    if key in SPECS:
        return spec_material(SPECS[key])
    if key in LAYOUT_MATERIALS:
        name, colour = LAYOUT_MATERIALS[key]
        mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        mat.diffuse_color = colour
        return mat
    name, tex, _group = C.MATERIALS[key]
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    if tex:
        # Reference the stock texture by its game path (the mounted install); nothing is copied. Toolbox's
        # exporter writes the path from "data/" on, with a .tga extension (the game builds use .bmap).
        img = bpy.data.images.new(os.path.basename(tex), width=1, height=1)
        img.source = "FILE"
        img.filepath = os.path.join(wf_root(), tex)
        node = nodes.new("ShaderNodeTexImage")
        node.image = img
        node.location = (-400, 200)
        mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
        if key in C.GHOST_MATERIALS:
            # Toolbox maps the Principled "Alpha" input to texture slot 6 (opacity), as the stock ghost material
            mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Alpha"])
    return mat


def collection(name: str, parent: bpy.types.Collection | None = None) -> bpy.types.Collection:
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(coll)
    return coll


def mesh_object(m: Mesh, coll: bpy.types.Collection) -> bpy.types.Object:
    origin = np.array(m.origin if m.origin is not None else (0.0, 0.0, 0.0))
    verts = (np.array(m.verts, dtype=np.float64) - origin).tolist() if m.verts else []
    me = bpy.data.meshes.new(m.name)
    me.from_pydata(verts, [], [list(f) for f in m.faces])
    keys = list(dict.fromkeys(m.mats))
    for k in keys:
        me.materials.append(material(k))
    if m.faces:
        slot = {k: i for i, k in enumerate(keys)}
        me.polygons.foreach_set("material_index", [slot[k] for k in m.mats])
        uv = me.uv_layers.new(name="UVMap")
        flat = [c for face in m.uvs for corner in face for c in corner]
        uv.data.foreach_set("uv", flat)
        if m.uvs2 is not None:
            # Second layer = UV2 in game (Toolbox writes the layers in order)
            uv2 = me.uv_layers.new(name="UVMap2")
            uv2.data.foreach_set("uv", [c for face in m.uvs2 for corner in face for c in corner])
    me.update()
    ob = bpy.data.objects.new(m.name, me)
    ob.location = tuple(origin)
    if m.custom_data:
        ob["CustomData"] = m.custom_data  # Toolbox reads a String property (Blender 3+)
    coll.objects.link(ob)
    return ob


def empty_object(e: Empty, coll: bpy.types.Collection) -> bpy.types.Object:
    ob = bpy.data.objects.new(e.name, None)
    ob.empty_display_type = e.display
    ob.empty_display_size = e.size
    ob.location = e.location
    ob.rotation_euler = (0.0, 0.0, e.rotation_z)
    ob.scale = e.scale
    if e.custom_data:
        ob["CustomData"] = e.custom_data
    if e.xdir:
        ob["xdir"] = e.xdir  # Toolbox prefixes the subscene name with this folder
    coll.objects.link(ob)
    return ob


def curve_object(c: Curve, coll: bpy.types.Collection) -> bpy.types.Object:
    cu = bpy.data.curves.new(c.name, "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(len(c.points) - 1)
    sp.points.foreach_set("co", [v for p in c.points for v in (p[0], p[1], p[2], 1.0)])
    sp.use_cyclic_u = c.cyclic
    ob = bpy.data.objects.new(c.name, cu)
    coll.objects.link(ob)
    return ob


def save(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True, relative_remap=False)


def describe_scene() -> dict:
    """Object summary for the build manifest."""
    objs = []
    for ob in sorted(bpy.context.scene.objects, key=lambda o: o.name):
        d = {"name": ob.name, "type": ob.type, "collection": ob.users_collection[0].name if ob.users_collection else ""}
        if "CustomData" in ob:
            d["customData"] = str(ob["CustomData"])
        if "xdir" in ob:
            d["xdir"] = str(ob["xdir"])
        if ob.type == "MESH":
            d["vertices"] = len(ob.data.vertices)
            d["faces"] = len(ob.data.polygons)
            d["materials"] = [m.name for m in ob.data.materials if m]
        objs.append(d)
    return {"objects": objs, "materials": sorted(m.name for m in bpy.data.materials)}

