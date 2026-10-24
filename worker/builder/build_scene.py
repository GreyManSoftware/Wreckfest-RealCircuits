"""Build Wreckfest-ready Blender scenes from a track spec (and optionally its TrackModel).

    blender -b --factory-startup --python-exit-code 1 --python build_scene.py -- \
        --spec spec.json [--model model.json] --out DIR

Without --model the spec is run through trackgen_geom.build_track_model and DIR/model.json is written.
Writes, in DIR:
    <slug>_base.blend            road, kerbs, shoulders, walls, bridges, terrain tiles, props (subscenes)
    <slug>_race.blend            forward layout: AI routes, checkpoints, start points, base subscene
    <slug>_race_reverse.blend    reverse layout (when the model has reverse: true)
    manifest.json                objects, layouts (with each one's minimap outline), junctions, bridges, warnings
    mod/data/track/<slug>/       generated maps (textures/*.tga) and one track data file <layout>.tcat per layout
Exit codes: 0 built, 2 bad input (no model), 3 geometry out of bounds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("TRACKGEN_GEOM_SRC"):
    sys.path.insert(0, os.environ["TRACKGEN_GEOM_SRC"])

import numpy as np
from wfbuild import BUILDER_VERSION
from wfbuild import config as C
from wfbuild.geometry import Mesh, TrackGeometry
from wfbuild.layout import LayoutBuilder, strip_end_lines
from wfbuild import backdrop as backdrop_mod
from wfbuild import bounds as bounds_mod
from wfbuild import cameras, dressing, maps, materials, sponsor, tcat
from wfbuild.fields import DistanceField
from wfbuild.layout import Empty


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="build_scene.py")
    ap.add_argument("--spec", required=True, help="track spec JSON")
    ap.add_argument("--model", help="TrackModel JSON (default: build it from the spec)")
    ap.add_argument("--out", required=True, help="output directory")
    return ap.parse_args(argv)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_model(spec: dict, model_path: str | None, out: str, warnings: list[str]) -> dict:
    if model_path:
        with open(model_path, encoding="utf-8") as f:
            return json.load(f)
    from trackgen_geom import build_track_model

    result = build_track_model(spec)
    for w in result.warnings:
        warnings.append(f"geom {w.code}: {w.message}")
    if result.model is None:
        for e in result.errors:
            print(f"ERROR geom {e.code}: {e.message}", file=sys.stderr)
        sys.exit(2)
    for v in result.rule_result.violations:
        warnings.append(f"rule {v.severity} {v.rule_id}: {v.message}")
    text = result.model.to_json(indent=1)
    with open(os.path.join(out, "model.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return json.loads(text)


def static_cube_map(geo: TrackGeometry) -> Mesh:
    """Reflection probe like every stock base and the example: a 0.3 m cube with the "ghost" material (stock
    tmp_ghost_c1 in the colour and opacity slots, so it doesn't render), ~3.5 m above the start line."""
    r = geo.main
    c = r.p[0].copy()
    c[2] += C.CUBE_MAP_HEIGHT
    h = C.CUBE_MAP_SIZE / 2.0
    m = Mesh(C.STATIC_CUBE_MAP[0], C.STATIC_CUBE_MAP[1], kind="helper")
    m.origin = tuple(c)
    TrackGeometry._box(m, c[:2], 0.0, C.CUBE_MAP_SIZE, c[2] - h, c[2] + h, "cube_map")
    bottom = [i for i, v in enumerate(m.verts) if v[2] < c[2]]
    m.oriented_face(tuple(bottom), "cube_map", np.array([0, 0, -1.0]))
    return m


def _rot(x: float, y: float, deg: int) -> tuple[float, float]:
    c, s = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[deg]
    return x * c - y * s, x * s + y * c


def placement(layouts, base_meshes) -> tuple[int, float]:
    """bgeometry asserts ("createaidata.cpp: startZ <= 0") that every AI route starts at Bugbear Z <= 0,
    i.e. Blender y <= 0 (found by experiment: only the AI meshes' absolute position matters). Returns a
    rotation (multiple of 90 degrees, about the origin) and a y translation for the whole track (base and
    layouts) that put both ends of every AI strip at y <= -2 while keeping the track within +-LIMIT_M.
    No rotation is preferred; (0, 0.0) when nothing needs to move."""
    ends = np.concatenate(strip_end_lines(layouts)) if strip_end_lines(layouts) else np.zeros((0, 3))
    pts = np.concatenate([np.array(m.verts) for m in base_meshes if m.verts] + [ends])
    forced = os.environ.get("BUILDER_ROTATE_DEG")
    options = [int(forced)] if forced else [0, 90, 180, 270]
    best = None
    for deg in options:
        ey = np.array([_rot(x, y, deg)[1] for x, y, _z in ends]) if len(ends) else np.zeros(1)
        top = float(ey.max())
        shift = 0.0 if top <= -2.0 else -(top + 2.0)
        r = np.array([_rot(x, y, deg) for x, y in pts[:, :2]])
        worst = max(float(np.abs(r[:, 0]).max()), float(np.abs(r[:, 1] + shift).max()))
        if worst <= C.LIMIT_M and (best is None or abs(shift) < abs(best[1]) - 1e-6):
            best = (deg, shift)
        if best is not None and best[0] == 0 and not forced:
            break
    if best is None:
        print("ERROR no rotation/translation keeps the AI route starts at y <= 0 within the +-2000 m limit",
              file=sys.stderr)
        sys.exit(3)
    return best


def is_base_xref(e) -> bool:
    return e.name.startswith("#xref") and bool(e.xdir) and e.name.endswith("_base.scne")


def transform(meshes, empties, curves, deg: int, dy: float) -> None:
    def tp(p):
        x, y = _rot(p[0], p[1], deg)
        return (x, y + dy, p[2])

    for m in meshes:
        m.verts = [tp(v) for v in m.verts]
        if m.origin is not None:
            m.origin = tp(m.origin)
    for e in empties:
        e.location = tp(e.location)
        e.rotation_z += math.radians(deg)
    for c in curves:
        c.points = [tp(p) for p in c.points]


def write_tcat(lay, slug: str, mod_root: str, cams: list) -> dict:
    """mod/data/track/<slug>/<layout>.tcat with one route entry per AI route (main, alt1, ...) and the layout's
    cameras (scene coordinates), and what the minimap will show: the main route's outline (``ai_path``, the
    TrackModel centreline in driving order, in scene coordinates) as bounds, signed area (> 0 counter-clockwise
    seen from above) and length, for check_export to compare with the compiled AI route the game draws."""
    routes = sorted((m.name for m in lay.meshes if m.kind == "ai" and m.name.startswith("#ai_route_")),
                    key=lambda n: (n != "#ai_route_main", int(n.rsplit("alt", 1)[-1]) if "_alt" in n else 0))
    path = next(c for c in lay.curves if c.name == "ai_path")
    p = np.array(path.points, dtype=np.float64)[:, :2]
    q = np.roll(p, -1, axis=0)
    area = 0.5 * float(np.sum(p[:, 0] * q[:, 1] - q[:, 0] * p[:, 1]))
    length = float(np.sum(np.hypot(*(q - p).T)))
    rel = f"data/track/{slug}/{lay.name}.tcat"
    out = os.path.join(mod_root, *rel.split("/"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(tcat.build(len(routes), length, cams))
    return {"tcat": rel, "routes": routes, "cameras": len(cams),
            "bounds": [round(float(v), 2) for v in (*p.min(0), *p.max(0))],
            "signedAreaM2": round(area, 1), "lengthM": round(length, 1)}


def check_bounds(meshes: list[Mesh], what: str) -> float:
    worst = 0.0
    for m in meshes:
        if m.verts:
            worst = max(worst, float(np.abs(np.array(m.verts)[:, :2]).max()))
    if worst > C.LIMIT_M:
        print(f"ERROR {what}: a vertex is {worst:.1f} m from the origin (limit {C.LIMIT_M} m)", file=sys.stderr)
        sys.exit(3)
    return worst


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    t0 = time.time()
    with open(args.spec, "rb") as f:
        spec_bytes = f.read()
    spec = json.loads(spec_bytes)
    warnings: list[str] = []
    model = load_model(spec, args.model, args.out, warnings)
    warnings += [f"model {w}" for w in model.get("warnings", [])]
    slug = model["slug"]

    geo = TrackGeometry(model, spec)
    base_meshes = geo.build_base()
    base_meshes.append(static_cube_map(geo))
    layouts = LayoutBuilder(geo).build()
    rot, shift = placement(layouts, base_meshes)
    t_core = time.time() - t0

    # Scenery: dressing plan (placement manifest), generated maps, backdrop - all in TrackModel coordinates.
    t1 = time.time()
    field = DistanceField(geo)
    scenery, planner = dressing.plan(geo, field)
    t_dress = time.time() - t1
    mod_root = os.path.join(args.out, "mod")
    t1 = time.time()
    map_info = maps.generate(geo, field, planner, mod_root)
    t_maps = time.time() - t1
    bd = backdrop_mod.Backdrop(geo, rot, shift)
    backdrop_meshes, backdrop_info = bd.build()
    fr = tuple(map_info["frame"])
    for m in base_meshes:
        if m.kind in C.BLEND_KINDS:
            m.set_map_uv(fr)
    base_meshes += backdrop_meshes
    # out of bounds as stock: a #resetplayer wall + lid round each layout, collidable far ground, water resets and
    # a kill floor (wfbuild/bounds.py)
    bnd = bounds_mod.Bounds(geo, base_meshes)
    for lay in layouts:
        reset = bnd.layout_reset(lay)
        if reset is not None:
            lay.meshes.append(reset)
    base_meshes += bnd.base_meshes(bd, base_meshes, rot, shift)
    generated = bd.textures(mod_root)
    specs = materials.build(geo.options.get("environment"), slug, map_info, bd.n_textures, generated)
    # our own sponsor boards: one mesh with our rendered textures (wfbuild/sponsor.py); the rest are stock props
    sponsor_mesh = sponsor.build_mesh(scenery.placements)
    if sponsor_mesh is not None:
        base_meshes.append(sponsor_mesh)
        specs.update(sponsor.material_specs(sponsor.write_textures(
            mod_root, slug, (scenery.info.get("sponsorCredit") or {}).get("style"))))
    props = [Empty(f"#xref ob/{p.asset}.scne", (p.x, p.y, p.z), p.yaw, display="PLAIN_AXES", size=1.0,
                   scale=(p.scale, p.scale, p.scale)) for p in scenery.placements if not sponsor.is_mesh(p.asset)]
    # cameras per layout (wfbuild/cameras.py): the start camera and the trackside cameras the race intro and
    # replays cut between, model coordinates
    start_cams = {lay.name: cameras.start_camera(geo, lay, field, planner, scenery.placements) for lay in layouts}
    side_cams = {lay.name: cameras.trackside_cameras(geo, lay, field, planner, scenery.placements) for lay in layouts}
    transform_info = {"rotateDeg": rot, "shift": [0.0, round(shift, 4), 0.0],
                      "note": "scene = rotate(model, rotateDeg about +z) + shift"}
    with open(os.path.join(args.out, "scenery.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(scenery.to_json(transform_info, backdrop_info), f, indent=1)

    if rot or shift:
        transform(base_meshes, props, [], rot, shift)
        for lay in layouts:
            # The base subscene reference stays at the origin: the base scene's own vertices already carry
            # the transform, so moving the reference too would shift the whole world a second time.
            gameplay = [e for e in lay.empties if not is_base_xref(e)]
            transform(lay.meshes, gameplay, lay.curves, rot, shift)
        warnings.append(f"placement: the whole track is rotated {rot} deg and moved {shift:.1f} m along y so every "
                        "AI route starts at y <= 0 (bgeometry assert startZ <= 0)")
    t_geom = time.time() - t0

    worst = check_bounds(base_meshes, "base")
    for lay in layouts:
        worst = max(worst, check_bounds(lay.meshes, lay.name))
    def to_scene(p):
        return np.array([*_rot(p[0], p[1], rot), p[2]]) + np.array([0.0, shift, 0.0])

    for lay in layouts:  # track data (.tcat) per layout: minimap and cameras (wfbuild/tcat.py, cameras.py)
        eye, target, note = start_cams[lay.name]
        eye, target = to_scene(eye), to_scene(target)
        cam = tcat.Camera.looking_at(eye, target, cameras.FOV_DEG)
        lay.info["startCamera"] = {"position": [round(float(v), 2) for v in eye],
                                   "target": [round(float(v), 2) for v in target],
                                   "pitchDeg": round(cam.pitch_deg, 2), "yawDeg": round(cam.yaw_deg, 2), "note": note}
        cams = [cam]
        for e, t, sectors, _note in side_cams[lay.name]:
            cams.append(tcat.Camera.looking_at(to_scene(e), to_scene(t), cameras.FOV_DEG, tcat.CAM_ROTATING,
                                               tcat.CAM_SECTOR, sectors))
        fallback = sum(1 for c in side_cams[lay.name] if c[3].startswith("fallback"))
        lay.info["tracksideCameras"] = {"count": len(cams) - 1, "fallback": fallback}
        lay.info["minimap"] = write_tcat(lay, slug, mod_root, cams)

    import bpy
    from wfbuild import bscene

    files = {}
    scenes = {}
    bscene.reset()
    bscene.set_specs(specs)
    bscene.MOD_ROOT[:] = [mod_root]
    bpy.context.scene.name = f"{slug}_base"
    groups = {"road": ("road", "kerb", "limits", "shoulder", "decal"), "walls": ("wall", "barrier"), "bridges": ("bridge",),
              "terrain": ("terrain",), "backdrop": ("backdrop",), "helpers": ("helper",), "sponsor": ("sponsor",),
              "bounds": ("bounds",)}
    for group, kinds in groups.items():
        coll = bscene.collection(group)
        for m in base_meshes:
            if m.kind in kinds and not m.empty:
                bscene.mesh_object(m, coll)
    coll = bscene.collection("props")
    for e in props:
        bscene.empty_object(e, coll)
    base_file = os.path.join(args.out, f"{slug}_base.blend")
    scenes[f"{slug}_base"] = bscene.describe_scene()
    bscene.save(base_file)
    files["base"] = os.path.basename(base_file)

    layout_info = []
    for lay in layouts:
        bscene.reset()
        bpy.context.scene.name = lay.name
        bscene.empty_object(next(e for e in lay.empties if e.name.startswith("#xref")), bscene.collection("base"))
        ai = bscene.collection("airoutes")
        cps = bscene.collection("checkpoints")
        jumps = bscene.collection("jumps")
        for m in lay.meshes:
            target = {"ai": ai, "checkpoint": cps}.get(m.kind) or (bscene.collection("bounds") if m.kind == "bounds" else jumps)
            bscene.mesh_object(m, target)
        sp = bscene.collection("startpoints")
        for e in lay.empties:
            if not e.name.startswith("#xref"):
                bscene.empty_object(e, sp)
        ed = bscene.collection("editing#exclude")  # Toolbox skips collections ending in #exclude
        for c in lay.curves:
            bscene.curve_object(c, ed)
        path = os.path.join(args.out, f"{lay.name}.blend")
        scenes[lay.name] = bscene.describe_scene()
        bscene.save(path)
        files[lay.key] = os.path.basename(path)
        info = dict(lay.info)
        warnings += [f"{lay.key} {w}" for w in info.pop("warnings", [])]
        layout_info.append({"key": lay.key, "name": lay.name, "reverse": lay.reverse, "file": files[lay.key],
                            **{k: v for k, v in info.items() if k != "mainSectorSamples"}})

    warnings += geo.warnings
    warnings.append("unverified: reverse layouts, alt-route checkpoints, layout-only jumps, the base subscene "
                    "reference and prop subscenes still need an in-game test")
    manifest = {
        "builderVersion": BUILDER_VERSION,
        "slug": slug,
        "name": model.get("name"),
        "specSha256": sha256(spec_bytes),
        "modelSha256": sha256(json.dumps(model, sort_keys=True).encode()),
        "options": spec.get("options", {}),
        "files": files,
        "trackFolder": f"data/track/{slug}",
        "layouts": layout_info,
        "maxAbsCoordinateM": round(worst, 3),
        "transform": {"rotateDeg": rot, "shift": [0.0, round(shift, 4), 0.0],
                      "note": "scene = rotate(model, rotateDeg about +z) + shift"},
        "junctions": getattr(geo, "stitched", []),
        "bridges": [{"id": b.id, "over": b.over.id, "under": b.under.id, "sOver": b.s_over, "sUnder": b.s_under,
                     "structureSamples": int(b.structure.sum())} for b in geo.bridges],
        "kerbs": getattr(geo, "kerb_report", []),
        "barriers": {f"{p['route'].id}:{'l' if side > 0 else 'r'}": {
            "wallShare": round(float(p["ok"].mean()), 3), "dividerShare": round(float(p["divider"].mean()), 3)}
            for (_ri, side), p in geo.wall_plan().items()},
        # measured run-off of an imported track (runoff.py): per route side, shares of surfaces and barrier types
        "runoff": geo.runoff.summary(),
        "barrierTypes": {f"{p['route'].id}:{'l' if side > 0 else 'r'}": {
            str(t): round(float(np.mean([(x == t) and ok for x, ok in zip(p["type"], p["ok"])])), 3)
            for t in sorted({str(x) for x, ok in zip(p["type"], p["ok"]) if ok})}
            for (_ri, side), p in geo.wall_plan().items()},
        "props": len(props),
        "bounds": bnd.info,
        "scenery": {"file": "scenery.json", "counts": scenery.counts(), "total": len(scenery.placements),
                    "info": scenery.info, "backdrop": backdrop_info},
        "maps": map_info,
        "materials": {k: {"name": v["name"], "slots": [list(x) for x in v["slots"]]} for k, v in specs.items()},
        "timings": {"core": round(t_core, 2), "dressing": round(t_dress, 2), "maps": round(t_maps, 2)},
        "scenes": scenes,
        "warnings": warnings,
        "seconds": round(time.time() - t0, 2),
        "geometrySeconds": round(t_geom, 2),
    }
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1)
    print(f"built {slug}: {', '.join(files.values())} in {manifest['seconds']} s; {len(warnings)} warnings")
    for w in warnings:
        print(f"  warning: {w}")


if __name__ == "__main__":
    main()
