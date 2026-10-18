"""Check exported .bgo3 files and the .scne files bgeometry built from them (plain Python, no Blender).

usage: python check_export.py OUT_DIR PROJECT_ROOT

Reads OUT_DIR/manifest.json and OUT_DIR/model.json, then PROJECT_ROOT/data/track/<slug>/<scene>.bgo3 with
bgo_read: object names, kinds and CustomData survive the export, subscene references resolve to the base
scene and to stock props, curves are not exported, materials keep their name#surface and texture paths,
and every .scne is non-empty. Exits non-zero on any failure.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import bgo_read

failures: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {what}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(what)


def main() -> None:
    out, root = sys.argv[1], sys.argv[2]
    with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    with open(os.path.join(out, "model.json"), encoding="utf-8") as f:
        model = json.load(f)
    slug = manifest["slug"]
    track = os.path.join(root, "data", "track", slug)
    for scene, desc in manifest["scenes"].items():
        bgo_path = os.path.join(track, scene + ".bgo3")
        scne = os.path.join(track, scene + ".scne")
        check(os.path.isfile(scne) and os.path.getsize(scne) > 0, f"{scene}: .scne built",
              f"{os.path.getsize(scne) if os.path.isfile(scne) else 0} bytes")
        b = bgo_read.read(bgo_path)
        names = {o.name: o for o in b.objects}
        exported = [o for o in desc["objects"] if o["type"] in ("MESH", "EMPTY") and o["collection"] != "editing#exclude"]
        want = set()
        for o in exported:
            n = o["name"]
            if n.startswith("#xref "):
                base = re.sub(r"\.(\d+)$", r"#xref\1", n.split("/")[-1].replace("#xref ", ""))
                want.add(base.replace(".scne#xref", "#xref").replace(".scne", "#xref"))
            else:
                want.add(n)
        check(want <= set(names), f"{scene}: all mesh/empty objects exported", f"{len(names)} objects; missing "
              f"{sorted(want - set(names))[:5]}")
        check(not any(n.startswith("ai_path") for n in names), f"{scene}: editing curves not exported")
        for o in exported:
            if o["name"] in names and o.get("customData"):
                got = names[o["name"]].custom_data.replace("\r\n", "|").rstrip("|")
                check(got == o["customData"], f"{scene}: CustomData of {o['name']}", repr(got))
                break
        xrefs = [o for o in b.objects if o.kind == "OBJX"]
        if scene.endswith("_base"):
            props = [o for o in xrefs]
            bad = [o.xref for o in props if not re.fullmatch(r"data/art/objects/[a-z0-9_]+\.scn", o.xref or "")]
            check(props and not bad, f"{scene}: props reference data/art/objects/*.scn", f"{len(props)}; bad {bad[:3]}")
            mats = [m for m in b.materials if not m.name.startswith("static_cube_map")]
            badm = [m.name for m in mats if "#" not in m.name or not m.textures
                    or not all(t.path.startswith("data/") and t.path.endswith(".tga") for t in m.textures)]
            check(not badm, f"{scene}: materials keep name#surface and data/... texture paths", ", ".join(badm))
            col = [o.name for o in b.objects if o.kind == "OBJM" and o.name != "#static_cube_map"
                   and "col = true" not in o.custom_data]
            check(not col, f"{scene}: collision CustomData exported", ", ".join(col[:5]))
        else:
            base = [o for o in xrefs if o.name == f"{slug}_base#xref"]
            check(len(base) == 1 and base[0].xref == f"data/track/{slug}/{slug}_base.scn"
                  and "col = true" in base[0].custom_data,
                  f"{scene}: base subscene {slug}_base#xref -> data/track/{slug}/{slug}_base.scn",
                  base[0].xref if base else "missing")
            sp = [o for o in b.objects if o.name.startswith("#startpoint")]
            check(all(o.kind == "OBJD" for o in sp) and len(sp) == model["grid"]["size"] + 1,
                  f"{scene}: start points exported as dummies", f"{len(sp)}")
            ai = [o for o in b.objects if o.name.startswith("#ai_")]
            check(all(o.kind == "OBJM" and o.tri_count > 0 for o in ai) and len(ai) >= 3,
                  f"{scene}: AI routes exported as meshes", f"{len(ai)}")
    print(f"EXPORT CHECKS {'FAILED' if failures else 'OK'}: {len(failures)} failures")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
