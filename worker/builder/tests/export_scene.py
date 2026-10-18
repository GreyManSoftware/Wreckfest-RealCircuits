"""Export a builder .blend to .bgo3 with Toolbox Mz, headless.

usage: blender -b --factory-startup --python-exit-code 1 --python export_scene.py -- SCENE.blend OUT.bgo3
"""
import os
import sys

import bpy

sys.path.insert(0, os.environ.get("WORKER_BLENDER_LIB", "/opt/worker/blender"))
import wf_toolbox_headless as tb


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    blend, out = argv[0], argv[1]
    bpy.ops.wm.open_mainfile(filepath=blend)
    tb.enable()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tb.export_bgo(out)
    print(f"EXPORTED {out} ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
    main()
