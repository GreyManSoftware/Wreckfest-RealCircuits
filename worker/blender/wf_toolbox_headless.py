"""Helpers for driving Toolbox Mz from `blender -b` (no UI).

    import sys; sys.path.insert(0, "/opt/worker/blender")
    import wf_toolbox_headless as tb
    tb.enable(wf_path="/wreckfest")
    tb.export_bgo("/abs/project/data/track/name/name.bgo3")

Quirks handled here (Toolbox Mz v1.1.8):
- Its operators report via WindowManager.popup_menu(), which segfaults Blender 4.2 in background mode.
  In background mode popup_menu is replaced with a print.
- The add-on's "build after export" runs bgeometry in a detached thread that `blender -b` may not wait
  for, and its .bmap builder builds the bimage path with a Windows backslash. Both are switched off; the
  worker runs bgeometry/bimage itself via wf-tool.
- Setting wf_path triggers save_userpref (harmless; writes ~/.config/blender).
- Breckfest.exe is only needed by the SCNE/BMAP importers, not by the exporter or mod generator.
"""
import os

import addon_utils
import bpy

ADDON = "wreckfest_toolbox"


def _headless_popup_menu(self, draw_func, *, title="", icon="NONE"):
    print(f"[popup] {title}")


def patch_popups() -> None:
    if bpy.app.background:
        bpy.types.WindowManager.popup_menu = _headless_popup_menu


def enable(wf_path: str | None = None, username: str = "trackgen"):
    """Enable the add-on and set its preferences for headless use. Returns the preferences."""
    patch_popups()
    mod = addon_utils.enable(ADDON, default_set=True, handle_error=None)
    if mod is None or ADDON not in bpy.context.preferences.addons:
        raise RuntimeError(f"could not enable add-on {ADDON}")
    prefs = bpy.context.preferences.addons[ADDON].preferences
    prefs.wf_path = wf_path or os.environ.get("WRECKFEST_DIR", "/wreckfest")
    prefs.username = username
    prefs.build_after_export = False
    prefs.build_bmap = False
    return prefs


def version() -> tuple:
    for mod in addon_utils.modules():
        if mod.__name__ == ADDON:
            return tuple(mod.bl_info.get("version", ()))
    return ()


def export_bgo(path: str) -> str:
    """Export all visible objects to a .bgo3 with the add-on's File > Export operator."""
    result = bpy.ops.wftb.export_bgo_with_dialog(filepath=path)
    if "FINISHED" not in result or not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError(f"BGO export failed: {result}, {path}")
    return path
