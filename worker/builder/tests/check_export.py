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
import numpy as np
from wfbuild import config as C

failures: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {what}{(' - ' + detail) if detail else ''}")
    if not ok:
        failures.append(what)


WF = os.environ.get("WRECKFEST_DIR", "/wreckfest")


def texture_exists(path: str, root: str) -> bool:
    """A texture path as written in the .bgo3 (data/....tga) resolves when the game has the .bmap, or the mod
    (project root) has the .bmap or its .tga source. Anything else shows as the pink missing texture."""
    if not path.lower().startswith("data/"):
        return False
    stem = path[:-4] if path.lower().endswith(".tga") else path
    return any(os.path.isfile(os.path.join(base, *(stem + ext).split("/")))
               for base in (WF, root) for ext in (".bmap", ".tga") if not (base == WF and ext == ".tga"))


def to_world(obj, pts) -> np.ndarray:
    """Blender-space points local to ``obj`` -> world, through the object's exported (Bugbear Y-up, row-vector)
    matrix, as the game places them."""
    m = np.array(obj.matrix, dtype=np.float64).reshape(4, 4)
    p = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
    pb = np.stack([p[:, 0], p[:, 2], p[:, 1], np.ones(len(p))], axis=1) @ m
    return np.stack([pb[:, 0], pb[:, 2], pb[:, 1]], axis=1)


class Road:
    """The base scene's road triangles in world space (the base scene placed at the origin)."""

    def __init__(self, obj):
        t = np.array(obj.tris, dtype=np.float64)
        self.tri = to_world(obj, t.reshape(-1, 3)).reshape(-1, 3, 3)
        self.lo = self.tri[:, :, :2].min(1)
        self.hi = self.tri[:, :, :2].max(1)

    def placed(self, xref) -> "Road":
        """The road as the layout's base subscene reference places it."""
        r = Road.__new__(Road)
        r.tri = to_world(xref, self.tri.reshape(-1, 3)).reshape(-1, 3, 3)
        r.lo, r.hi = r.tri[:, :, :2].min(1), r.tri[:, :, :2].max(1)
        return r

    def height_at(self, p) -> float | None:
        """Road height under p (x, y), or None when p is not on the road."""
        cand = np.nonzero((self.lo[:, 0] <= p[0]) & (self.hi[:, 0] >= p[0]) &
                          (self.lo[:, 1] <= p[1]) & (self.hi[:, 1] >= p[1]))[0]
        best = None
        for k in cand:
            a, b, c = self.tri[k]
            m = np.array([[b[0] - a[0], c[0] - a[0]], [b[1] - a[1], c[1] - a[1]]])
            if abs(np.linalg.det(m)) < 1e-12:
                continue
            u, w = np.linalg.solve(m, np.asarray(p[:2]) - a[:2])
            if u >= -1e-6 and w >= -1e-6 and u + w <= 1 + 1e-6:
                z = a[2] + u * (b[2] - a[2]) + w * (c[2] - a[2])
                if best is None or abs(z - p[2]) < abs(best - p[2]):
                    best = z
        return best

    def nearest(self, p) -> float:
        return float(np.min(np.hypot(*(self.tri.reshape(-1, 3)[:, :2] - np.asarray(p[:2])).T)))


def check_on_road(scene: str, lay, xref, base_road: Road) -> None:
    """Gameplay objects of an exported layout must sit on the road of the exported base, placed through the
    layout's base subscene reference (catches a transform applied to one but not the other)."""
    road = base_road.placed(xref)

    def report(what: str, items: list[tuple[str, np.ndarray]], dz: float) -> None:
        bad = []
        for name, p in items:
            z = road.height_at(p)
            if z is None or abs(p[2] - z) > dz:
                bad.append(f"{name} at ({p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f}) "
                           f"{'off the road, ' + format(road.nearest(p), '.1f') + ' m from it' if z is None else f'{p[2] - z:+.1f} m above it'}")
        check(items and not bad, f"{scene}: {what} on the base road", f"{len(items)} checked; " + "; ".join(bad[:3]))

    sp = [(o.name, to_world(o, [(0.0, 0.0, 0.0)])[0]) for o in lay.objects if o.name.startswith("#startpoint")]
    report("start points", sp, 1.5)
    cps = []
    for o in lay.objects:
        if o.name.startswith("#checkpoint") and o.tris:
            v = to_world(o, np.array(o.tris).reshape(-1, 3))
            mid = (v.min(0) + v.max(0)) / 2.0
            cps.append((o.name, np.array([mid[0], mid[1], v[:, 2].min() + C.CHECKPOINT_BELOW])))
    report("checkpoint centres", cps, 2.0)
    ai = []
    for o in lay.objects:
        if o.name.startswith("#ai_race_") and o.tris:
            first = to_world(o, np.array(o.tris[0]))  # a triangle of the first sector
            ai.append((o.name + " start", first.mean(0)))
    report("AI race route starts", ai, 1.5)


def main() -> None:
    out, root = sys.argv[1], sys.argv[2]
    with open(os.path.join(out, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    with open(os.path.join(out, "model.json"), encoding="utf-8") as f:
        model = json.load(f)
    slug = manifest["slug"]
    track = os.path.join(root, "data", "track", slug)
    road = None
    base_bgo = os.path.join(track, f"{slug}_base.bgo3")
    if os.path.isfile(base_bgo):
        full = bgo_read.read(base_bgo, tris=True)
        ro = next((o for o in full.objects if o.name == "track_surface"), None)
        road = Road(ro) if ro is not None and ro.tris else None
        # UVs as written (int16 / 2048 in the game): within +-16; UV2 (generated maps) within 0..1
        worst, bad2 = 0.0, []
        for o in full.objects:
            for k, (lo, hi) in enumerate(zip(o.uv_lo or [], o.uv_hi or [])):
                worst = max(worst, max(abs(v) for v in lo + hi))
                if k == 1 and (min(lo) < -1e-3 or max(hi) > 1 + 1e-3):
                    bad2.append(o.name)
        check(worst < 16.0, "base: exported UVs within +-16 (int16 / 2048)", f"max |uv| {worst:.2f}")
        check(not bad2, "base: exported UV2 within 0..1", ", ".join(bad2[:3]))
        # stock-layout #blend materials: albedo/_ns on UV1, blend/macro/colour/clutter maps on UV2
        blends = [m for m in full.materials if "#blend" in m.name]
        bad = [m.name for m in blends if not set(range(10)) <= {t.slot for t in m.textures}
               or any(t.uv != 2 for t in m.textures if t.slot in (6, 8, 9, 11))
               or any(t.uv != 1 for t in m.textures if t.slot < 6)]
        check(blends and not bad, "base: #blend materials keep the 12-slot layout and UV channels",
              f"{len(blends)} materials; bad {bad[:3]}")
        gen = sorted({t.path for m in full.materials for t in m.textures if t.path.startswith(f"data/track/{slug}/")})
        check(gen and all(texture_exists(p, root) for p in gen),
              "base: generated maps referenced by mod path and present in the mod as .bmap", f"{len(gen)} maps")
    check(road is not None, "base: exported road surface found")
    for scene, desc in manifest["scenes"].items():
        bgo_path = os.path.join(track, scene + ".bgo3")
        scne = os.path.join(track, scene + ".scne")
        check(os.path.isfile(scne) and os.path.getsize(scne) > 0, f"{scene}: .scne built",
              f"{os.path.getsize(scne) if os.path.isfile(scne) else 0} bytes")
        b = bgo_read.read(bgo_path, tris=not scene.endswith("_base"))
        unresolved = sorted({f"{m.name}: {t.path}" for m in b.materials for t in m.textures
                             if not texture_exists(t.path, root)})
        check(not unresolved, f"{scene}: every material texture resolves (stock .bmap or mod texture)",
              "; ".join(unresolved[:5]))
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
            badm = [m.name for m in b.materials if m.name != "ghost" and ("#" not in m.name or not m.textures
                    or not all(t.path.startswith("data/") and t.path.endswith(".tga") for t in m.textures))]
            check(not badm, f"{scene}: materials keep name#surface and data/... texture paths", ", ".join(badm))
            ghost = next((m for m in b.materials if m.name == "ghost"), None)
            check(ghost is not None and {t.slot for t in ghost.textures} == {1, 6},
                  f"{scene}: #static_cube_map material 'ghost' has its texture in slots 1 and 6 (invisible)",
                  str([(t.slot, t.path) for t in ghost.textures]) if ghost else "missing")
            col = [o.name for o in b.objects if o.kind == "OBJM" and o.name != "#static_cube_map"
                   and o.name != "track_edges" and not o.name.startswith("backdrop_")
                   and "col = true" not in o.custom_data]
            check(not col, f"{scene}: collision CustomData exported", ", ".join(col[:5]))
        else:
            base = [o for o in xrefs if o.name == f"{slug}_base#xref"]
            check(len(base) == 1 and base[0].xref == f"data/track/{slug}/{slug}_base.scn"
                  and "col = true" in base[0].custom_data,
                  f"{scene}: base subscene {slug}_base#xref -> data/track/{slug}/{slug}_base.scn",
                  base[0].xref if base else "missing")
            if base and road is not None:
                check_on_road(scene, b, base[0], road)
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
