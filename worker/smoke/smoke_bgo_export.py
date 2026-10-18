"""Headless Toolbox Mz BGO export smoke test.

usage: blender -b --factory-startup --python-exit-code 1 --python smoke_bgo_export.py -- OUT.bgo3

Enables the wreckfest_toolbox add-on (via /opt/worker/blender/wf_toolbox_headless.py), builds a trivial
track (a collidable 100 x 12 m road plane with material "road#asphalt") and exports it with the add-on's
File > Export operator (wftb.export_bgo_with_dialog). run-smoke.sh runs bgeometry on the result.
"""
import os
import sys

import bpy

sys.path.insert(0, os.environ.get("WORKER_BLENDER_LIB", "/opt/worker/blender"))
import wf_toolbox_headless as tb  # noqa: E402


def fail(msg: str) -> None:
    print(f"SMOKE FAIL: {msg}")
    sys.exit(1)


def main(out_path: str) -> None:
    prefs = tb.enable()
    print(f"enabled {tb.ADDON} {tb.version()}; wf_path={prefs.wf_path!r}")

    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)

    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0.0, 0.0, 0.0))
    road = bpy.context.active_object
    road.name = "road"
    road.scale = (100.0, 12.0, 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    road["CustomData"] = "col = true"

    mat = bpy.data.materials.new("road#asphalt")
    mat.use_nodes = True
    road.data.materials.append(mat)

    try:
        tb.export_bgo(out_path)
    except RuntimeError as e:
        fail(str(e))

    with open(out_path, "rb") as f:
        head = f.read(8)
    if head[4:8] != b"MAIN":
        fail(f"unexpected .bgo3 header {head.hex(' ')}")
    print(f"SMOKE OK: exported {out_path} ({os.path.getsize(out_path)} bytes)")


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        fail("usage: ... -- OUT.bgo3")
    main(argv[0])
