"""Check exported .bgo3 files and the .scne files bgeometry built from them (plain Python, no Blender).

usage: python check_export.py OUT_DIR PROJECT_ROOT
       python check_export.py --track-data TRACK_DIR   (the shipped files only: every layout's .tcat)

Reads OUT_DIR/manifest.json and OUT_DIR/model.json, then PROJECT_ROOT/data/track/<slug>/<scene>.bgo3 with
bgo_read: object names, kinds and CustomData survive the export, subscene references resolve to the base
scene and to stock props, curves are not exported, materials keep their name#surface and texture paths,
and every .scne is non-empty; the built base .scne stays within the stock scene limits (scne_read.LIMITS);
every layout has its track data (<scene>.tcat: minimap, a start camera above the exported terrain looking at
the grid, trackside cameras covering the lap) and the compiled AI route the minimap draws matches it; the camera
decoding is pinned on stock layouts (check_stock_cameras).
Exits non-zero on any failure.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import bgo_read
import scne_read
sys.path.insert(0, HERE)
import check_bounds
import numpy as np
from wfbuild import config as C
from wfbuild import tcat

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
    # the whole car on the tarmac: a 2.0 x 4.5 m footprint (the empties are scaled 1 x 2.25 along local X / Y)
    feet = [(f"{o.name} corner {k}", p) for o in lay.objects if o.name.startswith("#startpoint")
            and o.name != "#startpointcarchange"
            for k, p in enumerate(to_world(o, [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)]))]
    report("grid slot footprints (2.0 x 4.5 m)", feet, 1.5)
    cps = []
    for o in lay.objects:
        if o.name.startswith("#checkpoint") and o.tris:
            # the object's origin is the road centre it spans (the plane's sides differ with the run-off margins)
            cps.append((o.name, to_world(o, [(0.0, 0.0, 0.0)])[0]))
    report("checkpoint centres", cps, 2.0)
    ai = []
    for o in lay.objects:
        if o.name.startswith("#ai_race_") and o.tris:
            first = to_world(o, np.array(o.tris[0]))  # a triangle of the first sector
            ai.append((o.name + " start", first.mean(0)))
    report("AI race route starts", ai, 1.5)


class Surface:
    """Height lookup on exported triangles (world space, x/y binned)."""

    def __init__(self, tris: np.ndarray, cell: float = 8.0):
        self.tri, self.cell = tris, cell
        lo, hi = tris[:, :, :2].min(1), tris[:, :, :2].max(1)
        self.bins: dict[tuple[int, int], list[int]] = {}
        for k in range(len(tris)):
            for i in range(int(lo[k, 0] // cell), int(hi[k, 0] // cell) + 1):
                for j in range(int(lo[k, 1] // cell), int(hi[k, 1] // cell) + 1):
                    self.bins.setdefault((i, j), []).append(k)

    def height(self, x: float, y: float) -> float | None:
        for k in self.bins.get((int(x // self.cell), int(y // self.cell)), ()):
            a, b, c = self.tri[k]
            m = np.array([[b[0] - a[0], c[0] - a[0]], [b[1] - a[1], c[1] - a[1]]])
            det = m[0, 0] * m[1, 1] - m[0, 1] * m[1, 0]
            if abs(det) < 1e-12:
                continue
            u = ((x - a[0]) * m[1, 1] - (y - a[1]) * m[0, 1]) / det
            w = ((y - a[1]) * m[0, 0] - (x - a[0]) * m[1, 0]) / det
            if u >= -1e-6 and w >= -1e-6 and u + w <= 1 + 1e-6:
                return float(a[2] + u * (b[2] - a[2]) + w * (c[2] - a[2]))
        return None


CAMBER_RUNOUT = 1.5  # wfbuild.config.CAMBER_RUNOUT


def check_road_flush(full, model: dict, manifest: dict) -> None:
    """Road and grass meet like stock tracks: on the exported base, the terrain 2 m outside a plain road edge
    (away from other roads) lies 0-20 cm below the edge (no lip, no step up; beside a low edge the camber continues 1.5 m
    past it, and that run-out is allowed for: Brands Hatch's 6-7 degree banking), and between 2 and 15 m out it
    doesn't dip more than 0.3 m below both the edge and the ground 30 m out (no ditch or embankment look).
    Cadwell Park sat on a 0.5-1 m lip (2026-10-19): the terrain took the lowest road within ~10 m along a
    graded road."""
    tris = [to_world(o, np.array(o.tris).reshape(-1, 3)).reshape(-1, 3, 3)
            for o in full.objects if o.name.startswith("terrain_") and o.tris]
    if not tris:
        check(False, "base: terrain exported")
        return
    ter = Surface(np.concatenate(tris))
    t = manifest.get("transform", {})
    a = math.radians(t.get("rotateDeg", 0))
    ca, sa = round(math.cos(a)), round(math.sin(a))
    shift = np.array(t.get("shift", [0.0, 0.0, 0.0]))

    def scene(p: np.ndarray) -> np.ndarray:
        p = np.array(p, dtype=np.float64)
        x, y = p[:, 0].copy(), p[:, 1].copy()
        p[:, 0], p[:, 1] = x * ca - y * sa, x * sa + y * ca
        return p + shift[: p.shape[1]]

    routes = model["routes"]
    allc = np.concatenate([np.array([[q["x"], q["y"], q["z"]] for q in r["samples"]]) for r in routes])
    allw = np.concatenate([np.array([q["width"] for q in r["samples"]]) / 2.0 for r in routes])
    allr = np.concatenate([np.full(len(r["samples"]), k) for k, r in enumerate(routes)])
    alls = np.concatenate([np.array([q["s"] for q in r["samples"]]) for r in routes])
    steps, dips, n = [], 0, 0
    for k, r in enumerate(routes):
        smp = r["samples"]
        for side, key_ in ((1, "leftEdge"), (-1, "rightEdge")):
            edge = np.array(r[key_])
            for i in range(0, len(smp), 5):
                h = smp[i]["heading"]
                nl = np.array([-math.sin(h), math.cos(h)]) * side
                probe = edge[i, :2] + nl * 15.0
                # plain edges only: no other road (or this one further along) within 40 m of the probe line
                d = np.hypot(*(allc[:, :2] - probe).T) - allw
                sd = np.abs(alls - smp[i]["s"])
                sd = np.minimum(sd, float(r["lengthM"]) - sd) if r["closed"] else sd
                if np.any((d < 25.0) & ((allr != k) | (sd > 60.0))):
                    continue
                pts = scene(np.array([[*(edge[i, :2] + nl * o), 0.0] for o in (2.0, 5.0, 10.0, 15.0, 30.0)]))
                z = [ter.height(p[0], p[1]) for p in pts]
                if any(v is None for v in z):
                    continue
                ez = float(scene(edge[i:i + 1])[0, 2])
                n += 1
                # beside a low (cambered) edge the ground follows the camber CAMBER_RUNOUT (1.5 m) further down
                bank = float(smp[i].get("banking", 0.0)) * side
                runout = CAMBER_RUNOUT * math.tan(-bank) if bank < 0 else 0.0
                steps.append(ez - z[0] - runout)
                if min(z[:4]) < min(ez, z[4]) - 0.3:
                    dips += 1
    steps = np.array(steps)
    lo, hi = (np.percentile(steps, 2), np.percentile(steps, 98)) if len(steps) else (0, 0)
    check(n > 20 and -0.02 <= lo and hi <= 0.20,
          "base: terrain 2 m outside the road edge lies 0-20 cm below it (flush, stock-like; a cambered low side runs on)",
          f"{n} probes, edge - terrain p2 {lo:.3f} m, median {np.median(steps):.3f} m, p98 {hi:.3f} m")
    check(n > 20 and dips <= 0.02 * n, "base: no ditch beside the road (terrain 2-15 m out not 0.3 m below the "
          "edge and the ground 30 m out)", f"{dips} of {n} probes dip")


def to_scene_xy(p, manifest: dict) -> np.ndarray:
    """TrackModel x/y -> scene x/y (the builder's rotation and shift, manifest ``transform``)."""
    t = manifest.get("transform", {})
    a = math.radians(t.get("rotateDeg", 0))
    ca, sa = round(math.cos(a)), round(math.sin(a))
    shift = np.array(t.get("shift", [0.0, 0.0, 0.0]))[:2]
    return np.array([p[0] * ca - p[1] * sa, p[0] * sa + p[1] * ca]) + shift


def check_start_finish(scene: str, lay, model: dict, manifest: dict, info: dict | None) -> None:
    """The layout's start/finish line in the exported layout: the last checkpoint (the finish) sits on the line
    the TrackModel gives the layout (its own ``startFinish``, e.g. Cadwell Park's Club after Mansfield, else
    the main line at main s = 0), and the start points face away from it along the road, the way the race
    runs (the first sector of the compiled route; check_driving_order checks the rest of the lap)."""
    own = (info or {}).get("startFinish")
    main = next(r for r in model["routes"] if r["aiIndex"] == 0)
    line = own["position"][:2] if own else [main["samples"][0]["x"], main["samples"][0]["y"]]
    want = to_scene_xy(line, manifest)
    cps = sorted((o for o in lay.objects if o.name.startswith("#checkpoint") and o.tris), key=lambda o: o.name)
    if not cps:
        check(False, f"{scene}: checkpoints exported")
        return
    fin = to_world(cps[-1], [(0.0, 0.0, 0.0)])[0]
    d = float(np.hypot(*(fin[:2] - want)))
    check(d < 3.0, f"{scene}: the finish checkpoint {cps[-1].name} is on the layout's start/finish line",
          f"{d:.1f} m from it ({'own line' if own else 'main line'})")
    sp = [o for o in lay.objects if o.name.startswith("#startpoint") and o.name != "#startpointcarchange"]
    first = to_world(cps[0], [(0.0, 0.0, 0.0)])[0]
    bad = []
    for o in sp:
        p0, p1 = to_world(o, [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0)])  # local +Y is the car's forward
        fwd = (p1 - p0)[:2]
        to_cp1 = (first - p0)[:2]
        if float(np.dot(fwd, to_cp1)) <= 0.0 or float(np.dot(fwd, p0[:2] - want)) <= 0.0:
            bad.append(o.name)
    check(sp and not bad, f"{scene}: start points ahead of the line, facing the first checkpoint",
          f"{len(sp)} checked; wrong way: {bad[:3]}")


def check_driving_order(scene: str, scne: str) -> None:
    """The game drives a compiled AI route in its stored sector order (every stock layout's checkpoints run in
    that order, with or without otherway), and bgeometry picks that order itself (layout.needs_otherway). Our
    checkpoints are numbered in the driving order, so in the built .scne they must follow the stored main
    route forwards; backwards means the race runs the wrong way round (Cadwell Park, 2026-10-19)."""
    lay = scne_read.read_layout(scne)
    secs = lay.checkpoint_sectors()
    n = len(lay.routes[0])
    back = [(a, b) for a, b in zip(secs, secs[1:]) if (b - a) % n > n // 2]
    check(len(secs) >= 2 and not back, f"{scene}: built AI route runs in the checkpoint (driving) order",
          f"checkpoint sectors {secs[:12]}; stored otherway {lay.main_otherway()}")


# Cameras, decoded here from the raw .tcat bytes without wfbuild.tcat, so the check can't agree with a wrong
# writer. Convention (verified on stock, check_stock_cameras): BagEdit's bbTrackCamera v11 field order; a data
# entry's position is world space in Bugbear axes (x, y up, z), i.e. Blender (x, z, y); flag bit 2 "start
# camera", bit 4 "sector activation". Stock: start cameras 2-10 m above the ground near the grid, trackside
# cameras covering the lap by sector ranges.
CAM_MIN_ABOVE_M, CAM_MAX_ABOVE_M = 2.0, 150.0
CAM_TARGET_NEAR_ROUTE_M = 25.0
CAM_NEAR_SECTORS_M = 250.0
_terrain_cache: dict[str, "Surface | None"] = {}


def raw_cameras(payload: bytes) -> list[dict]:
    """Every camera of a v20 .tcat payload: type, flags, active sectors and its first data entry as world
    position (Blender x, y, z up) and pitch/yaw (degrees, yaw from +y towards +x)."""
    import struct
    p = payload.find(b"mact")  # 'tcam' stored reversed
    if p < 0:
        raise ValueError("no 'tcam' section")
    _ver, n = struct.unpack_from("<ii", payload, p + 4)
    p += 12
    out = []
    for _ in range(n):
        typ, flags, _fov, _zoom, _route, a0, a1, _m0, _m1, _ts, _te = struct.unpack_from("<IIffiiiiiff", payload, p)
        p += 44
        if payload[p:p + 4] == bytes(4):  # empty dof reference
            p += 4
        elif payload[p:p + 4] == b"fdxf":
            p += 4
        else:
            raise ValueError(f"camera {len(out)}: unexpected dof reference at {p}")
        p += 4 + struct.unpack_from("<i", payload, p)[0]
        if payload[p:p + 4] != b"edct":
            raise ValueError(f"camera {len(out)}: no 'tcde' at {p}")
        _ev, k = struct.unpack_from("<ii", payload, p + 4)
        p += 12
        if k < 1:
            raise ValueError(f"camera {len(out)} has no data entry")
        x, up, z, pitch, yaw = struct.unpack_from("<5f", payload, p)
        p += 48 * k
        out.append({"type": typ, "flags": flags, "sectors": (a0, a1), "pos": np.array([x, z, up]),
                    "pitch": (pitch + 180.0) % 360.0 - 180.0, "yaw": yaw % 360.0})
    if payload[p:p + 4] != b"uort":
        raise ValueError("cameras don't end at the 'trou' section")
    return out


def _view(c: dict) -> np.ndarray:
    p, y = math.radians(c["pitch"]), math.radians(c["yaw"])
    return np.array([math.cos(p) * math.sin(y), math.cos(p) * math.cos(y), math.sin(p)])


def scene_ground(path: str, prefix: str) -> "Surface | None":
    """World-space ground of a .scne: its meshes named ``prefix``*, each model's matrix applied (scne_read)."""
    if path not in _terrain_cache:
        tris = []
        if os.path.isfile(path):
            for m in scne_read.read(path, geometry=prefix).meshes:
                if not m.name.startswith(prefix):
                    continue
                for b in m.batches:
                    if b.pos is not None and len(b.idx):
                        tris.append(b.pos[b.idx])
        _terrain_cache[path] = Surface(np.concatenate(tris)) if tris else None
    return _terrain_cache[path]


def _exported_terrain(track: str) -> "Surface | None":
    """The built base scene's terrain (world space: the layouts reference the base at the origin), decoded
    from the shipped .scne, so the check also works on a zip's files."""
    slug = os.path.basename(os.path.normpath(track))
    return scene_ground(os.path.join(track, f"{slug}_base.scne"), "terrain_")


def check_track_data(scene: str, scne: str, track: str) -> dict | None:
    """The layout's shipped track data, which the game looks for as <scene dir>/<scene>.tcat (wfbuild/tcat.py):
    it resolves beside the .scne and parses (v20, stock layout), lists every compiled AI route with nothing
    hidden from the minimap and a non-zero zoom, and has cameras like stock layouts (decoded by raw_cameras):
    one start camera with the pole in view, and trackside cameras with sector activation whose sector ranges
    cover every sector of the compiled main route (the race intro cuts between them; without them it was under
    the ground on every circuit, Chris 2026-10-23/24), each near its sectors; every camera
    CAM_MIN_ABOVE_M..CAM_MAX_ABOVE_M above the exported terrain (the base .scne's raw vertices) and its view
    ray reaching the ground within CAM_TARGET_NEAR_ROUTE_M of a compiled AI route. Needs only the shipped
    files (layout and base .scne, .tcat), so tools/build_track_bundle.py runs it on every track of a bundle:
    put further checks of what a .tcat ships here. Returns the parsed track data, or None when it doesn't
    parse."""
    path = os.path.join(track, scene + ".tcat")
    if not os.path.isfile(path):
        check(False, f"{scene}: track data {scene}.tcat beside the .scne", "missing")
        return None
    raw = open(path, "rb").read()
    try:
        payload = scne_read.unbag(raw)
        td = tcat.read(payload)
        cams = raw_cameras(payload)
    except ValueError as e:
        check(False, f"{scene}: track data parses", str(e))
        return None
    check(tcat.header(raw) == (1, "tcat", tcat.VERSION) and td["zoomFactor"] > 0 and td["highlightRange"] > 0,
          f"{scene}: minimap track data {scene}.tcat resolves (v{tcat.VERSION}, zoom {td['zoomFactor']}, "
          f"highlight {td['highlightRange']})")
    lay = scne_read.read_layout(scne)
    check(len(td["routes"]) == len(lay.routes) and all(r["flags"] == 0 for r in td["routes"]),
          f"{scene}: track data lists every AI route, none hidden from the minimap",
          f"{len(td['routes'])} entries, {len(lay.routes)} compiled routes")
    starts = [c for c in cams if c["flags"] & 2]
    sector = [c for c in cams if c["flags"] & 4]
    check(len(starts) == 1, f"{scene}: track data has one start camera (as stock layouts)",
          f"{len(cams)} cameras, {len(starts)} start")
    n = len(lay.routes[0])
    covered = np.zeros(n, dtype=bool)
    bad_ranges = []
    for c in sector:
        a, b = c["sectors"]
        if not 0 <= a <= b < n:
            bad_ranges.append(f"{a}-{b}")
            continue
        covered[a:b + 1] = True
    check(sector and not bad_ranges and covered.all(),
          f"{scene}: trackside cameras (sector activation) cover every sector of the compiled main route",
          f"{len(sector)} cameras over {n} sectors; {int((~covered).sum())} uncovered; bad ranges {bad_ranges[:3]}")
    ter = _exported_terrain(track)
    if ter is None:
        check(not cams, f"{scene}: exported base terrain found for the camera checks")
        return td
    route_pts = np.array([p[:2] for r in lay.routes for a, b in r for p in (a, b, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))])
    mids = np.array([((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in lay.routes[0]])
    pole = next((sp for sp in lay.startpoints if sp[0] == "#startpoint01"), None)
    bad = []
    for k, c in enumerate(cams):
        pos = c["pos"]
        g = ter.height(pos[0], pos[1])
        if g is None:
            bad.append(f"camera {k} at ({pos[0]:.0f}, {pos[1]:.0f}) outside the terrain")
            continue
        above = pos[2] - g
        if not CAM_MIN_ABOVE_M <= above <= CAM_MAX_ABOVE_M:
            bad.append(f"camera {k} {above:.1f} m above the terrain")
            continue
        if c["flags"] & 4 and not (c["flags"] & 2):
            a, b = c["sectors"]
            if 0 <= a <= b < n:
                d = float(np.min(np.hypot(*(mids[a:b + 1] - pos[:2]).T)))
                if d > CAM_NEAR_SECTORS_M:
                    bad.append(f"camera {k} {d:.0f} m from its sectors {a}-{b}")
            continue  # a rotating camera turns to its target: its stored view doesn't matter
        d = _view(c)
        hit = None
        for t in np.arange(1.0, 400.0, 1.0):  # march the view ray down to the ground
            q = pos + d * t
            gz = ter.height(q[0], q[1])
            if gz is None:
                break
            if q[2] <= gz + 0.5:
                hit = q
                break
        if hit is None:
            bad.append(f"camera {k}'s view doesn't reach the ground within 400 m")
            continue
        near = float(np.min(np.hypot(*(route_pts - hit[:2]).T)))
        if near > CAM_TARGET_NEAR_ROUTE_M:
            bad.append(f"camera {k} looks at ground {near:.0f} m from the AI routes")
            continue
        if c["flags"] & 2 and pole is not None:
            to_pole = np.array([pole[1], pole[2], pole[3] + 1.0]) - pos
            ang = math.degrees(math.acos(float(np.clip(d @ to_pole / np.linalg.norm(to_pole), -1, 1))))
            if ang > 29.0:
                bad.append(f"start camera {k}: the pole is {ang:.0f} deg off the view centre")
    desc = "; ".join(f"({c['pos'][0]:.0f}, {c['pos'][1]:.0f}, {c['pos'][2]:.1f}) flags {c['flags']}" for c in cams[:2])
    check(cams and not bad, f"{scene}: cameras {CAM_MIN_ABOVE_M:.0f}-{CAM_MAX_ABOVE_M:.0f} m above the exported "
          "terrain, the start camera looking at the grid, trackside cameras by their sectors",
          "; ".join(bad[:3]) or desc)
    return td


# Stock layouts whose cameras check_stock_cameras reads with raw_cameras and scene_ground: the regression that
# pins the convention (an axis swap or sign flip puts these cameras hundreds of metres from their pole, or
# under their ground). Measured 2026-10-24: start cameras 4.2, 10.4 m above the terrain, 14-15 m from the pole.
STOCK_CAMERA_LAYOUTS = (("sandpit", "sandpit01_1", "sandpit01_base"), ("forest", "forest01_1", "forest01_base"))


def check_stock_cameras(wf: str = WF) -> None:
    """Our camera decoding against stock: each listed stock layout's start camera, read from the stock .tcat
    with raw_cameras, lies within 60 m of its pole and 1-30 m above the stock base terrain at its x/y (stock
    models placed by their matrices); its trackside cameras too (0-60 m). Skipped without the game data."""
    for env, lay_name, base in STOCK_CAMERA_LAYOUTS:
        tc = os.path.join(wf, "data", "property", "track", lay_name + ".tcat")
        lv = os.path.join(wf, "data", "art", "levels", env, lay_name)
        if not (os.path.isfile(tc) and os.path.isdir(lv)):
            print(f"SKIP: stock cameras of {lay_name} (no game data under {wf})")
            continue
        cams = raw_cameras(scne_read.unbag(open(tc, "rb").read()))
        ground = scene_ground(os.path.join(lv, base + ".scne"), "terrain")
        lay = scne_read.read_layout(os.path.join(lv, lay_name + ".scne"))
        pole = next(sp for sp in lay.startpoints if sp[0] == "#startpoint01")
        start = next(c for c in cams if c["flags"] & 2)
        g = ground.height(start["pos"][0], start["pos"][1]) if ground else None
        dist = float(np.hypot(start["pos"][0] - pole[1], start["pos"][1] - pole[2]))
        check(g is not None and 1.0 <= start["pos"][2] - g <= 30.0 and dist <= 60.0,
              f"stock {lay_name}: our camera reading puts its start camera above its ground near the pole",
              f"{(start['pos'][2] - g) if g is not None else float('nan'):.1f} m above the terrain, {dist:.0f} m from the pole")
        heights = [c["pos"][2] - h for c in cams if not c["flags"] & 2
                   for h in [ground.height(c["pos"][0], c["pos"][1]) if ground else None] if h is not None]
        check(len(heights) >= 3 and min(heights) >= 0.0 and max(heights) <= 60.0,
              f"stock {lay_name}: its trackside cameras are above its ground too",
              f"{len(heights)} over the terrain, {min(heights, default=0):.1f}-{max(heights, default=0):.1f} m")


def check_minimap(scene: str, scne: str, track: str, info: dict | None) -> None:
    """The in-race minimap: Wreckfest 1 draws it from the layout's compiled AI routes, set up from the layout's
    track data (check_track_data first). Checks that the compiled main route - what the minimap draws, in world
    x/y seen from above - covers the layout's own road: its bounds within 15 m of the layout's centreline, the
    same direction of travel (signed area) and the lap length within 5 %."""
    if check_track_data(scene, scne, track) is None:
        return
    lay = scne_read.read_layout(scne)
    if not info or "minimap" not in info:
        check(False, f"{scene}: manifest has the layout's minimap outline")
        return
    want = info["minimap"]
    mids = np.array([((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0) for a, b in lay.routes[0]])
    q = np.roll(mids, -1, axis=0)
    area = 0.5 * float(np.sum(mids[:, 0] * q[:, 1] - q[:, 0] * mids[:, 1]))
    length = float(np.sum(np.hypot(*(q - mids).T)))
    got = [*mids.min(0), *mids.max(0)]
    off = max(abs(a - b) for a, b in zip(got, want["bounds"]))
    check(off <= 15.0 and np.sign(area) == np.sign(want["signedAreaM2"])
          and abs(length - want["lengthM"]) <= 0.05 * want["lengthM"],
          f"{scene}: minimap (compiled main AI route) matches the layout's bounds, direction and length",
          f"bounds off by {off:.1f} m; {'anti' if area > 0 else ''}clockwise as driven "
          f"(layout {'anti' if want['signedAreaM2'] > 0 else ''}clockwise); {length:.0f} m vs {want['lengthM']:.0f} m")


def main() -> None:
    check_stock_cameras()  # the camera convention, pinned on stock files (skipped without the game data)
    if sys.argv[1] == "--track-data":  # shipped files only, e.g. an unpacked zip's data/track/<slug>
        track = sys.argv[2]
        for f in sorted(os.listdir(track)):
            if f.endswith(".scne") and not f.endswith("_base.scne"):
                check_track_data(f[:-5], os.path.join(track, f), track)
        check_bounds.export(check, track)
        print(f"TRACK DATA CHECKS {'FAILED' if failures else 'OK'}: {len(failures)} failures")
        sys.exit(1 if failures else 0)
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
        # our sponsor boards and credit plaque (wfbuild/sponsor.py): exported with their own rendered textures
        scen = os.path.join(out, "scenery.json")
        n_sp = 0
        if os.path.isfile(scen):
            with open(scen, encoding="utf-8") as f:
                n_sp = sum(1 for p in json.load(f)["placements"] if p["asset"].startswith("sponsor_"))
        sp = [m for m in full.materials if m.name.startswith("sponsor_")]
        own = f"data/track/{slug}/textures/{slug}_sponsor_"
        check(bool(sp) == (n_sp > 0) and all(any(t.path.startswith(own) and texture_exists(t.path, root)
                                                 for t in m.textures) for m in sp),
              "base: sponsor boards exported with their own textures, present in the mod",
              f"{n_sp} boards, {len(sp)} materials")
        check_road_flush(full, model, manifest)
    check(road is not None, "base: exported road surface found")
    for scene, desc in manifest["scenes"].items():
        bgo_path = os.path.join(track, scene + ".bgo3")
        scne = os.path.join(track, scene + ".scne")
        check(os.path.isfile(scne) and os.path.getsize(scne) > 0, f"{scene}: .scne built",
              f"{os.path.getsize(scne) if os.path.isfile(scne) else 0} bytes")
        if scene.endswith("_base") and os.path.isfile(scne):
            # what the game loads, read back from the built .scne: draw batches, vertices with every prop
            # instance, clutter, all below the stock-observed limits (more crashed the game: "Out of vertex
            # buffer memory", 2026-10-19)
            for what, v, lim in scne_read.limits(scne, [root, WF]):
                check(v <= lim, f"{scene}: {what} within the stock limit", f"{v:,} (limit {lim:,})")
        if not scene.endswith("_base") and os.path.isfile(scne):
            check_driving_order(scene, scne)
            check_minimap(scene, scne, track, next((l for l in manifest["layouts"] if l["name"] == scene), None))
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
            # braking boards: one stock gameplay_distanceboard<m> subscene per planned board, each resolving
            sc_path = os.path.join(out, "scenery.json")
            if os.path.isfile(sc_path):
                with open(sc_path, encoding="utf-8") as f:
                    planned = sorted(p["asset"] for p in json.load(f)["placements"] if p["category"] == "brakeBoards")
                built = sorted(o.xref.split("/")[-1][:-4] for o in props
                               if re.fullmatch(r"data/art/objects/gameplay_distanceboard\d+\.scn", o.xref or ""))
                gone = [a for a in set(built) if not os.path.isfile(os.path.join(WF, "data/art/objects", a + ".scne"))]
                check(built == planned and not gone, f"{scene}: braking boards exported as stock distance-board subscenes",
                      f"{len(built)} built, {len(planned)} planned; unresolved {gone}")
            badm = [m.name for m in b.materials if m.name != "ghost" and ("#" not in m.name or not m.textures
                    or not all(t.path.startswith("data/") and t.path.endswith(".tga") for t in m.textures))]
            check(not badm, f"{scene}: materials keep name#surface and data/... texture paths", ", ".join(badm))
            ghost = next((m for m in b.materials if m.name == "ghost"), None)
            check(ghost is not None and {t.slot for t in ghost.textures} == {1, 6},
                  f"{scene}: #static_cube_map material 'ghost' has its texture in slots 1 and 6 (invisible)",
                  str([(t.slot, t.path) for t in ghost.textures]) if ghost else "missing")
            col = [o.name for o in b.objects if o.kind == "OBJM" and o.name != "#static_cube_map"
                   and o.name not in ("track_edges", "track_transitions") and not o.name.startswith("backdrop_")
                   and not o.name.startswith("barrier_") and "col = true" not in o.custom_data]
            check(not col, f"{scene}: collision CustomData exported", ", ".join(col[:5]))
            ro = manifest.get("runoff") or {}
            if ro:
                # measured run-off: the gravel traps' terrain material carries the stock #slowdown surface, the
                # visible barriers their stock textures (Armco guardrail, black belted tyre wall, concrete)
                mats = {m.name: m for m in b.materials}
                traps = [n for n in mats if n.startswith("terrain_trap")]
                want_trap = any(v.get("gravel", 0) > 0 for v in ro.values())
                check((not want_trap) or (traps and all("#slowdown" in n for n in traps)),
                      f"{scene}: gravel traps exported with the #slowdown surface", ", ".join(traps))
                tex = {"barrier_armco": "prop_guardrail01_c", "barrier_tyres": "prop_tirerubberbarrier_c",
                       "barrier_concrete": "a_concrete2_c"}
                badb = [n for n in mats for k, t in tex.items() if n.startswith(k + "#")
                        and not any(t in tt.path for tt in mats[n].textures)]
                built = sorted(k for k in tex if any(n.startswith(k + "#") for n in mats))
                check(built and not badb, f"{scene}: measured barriers exported with their stock textures",
                      f"{built}; bad {badb}")
        else:
            base = [o for o in xrefs if o.name == f"{slug}_base#xref"]
            check(len(base) == 1 and base[0].xref == f"data/track/{slug}/{slug}_base.scn"
                  and "col = true" in base[0].custom_data,
                  f"{scene}: base subscene {slug}_base#xref -> data/track/{slug}/{slug}_base.scn",
                  base[0].xref if base else "missing")
            if base and road is not None:
                check_on_road(scene, b, base[0], road)
            if model.get("layouts"):  # imported multi-layout tracks: each layout's own or shared line
                check_start_finish(scene, b, model, manifest,
                                   next((l for l in manifest["layouts"] if l["name"] == scene), None))
            sp = [o for o in b.objects if o.name.startswith("#startpoint")]
            check(all(o.kind == "OBJD" for o in sp) and len(sp) == model["grid"]["size"] + 1,
                  f"{scene}: start points exported as dummies", f"{len(sp)}")
            ai = [o for o in b.objects if o.name.startswith("#ai_")]
            check(all(o.kind == "OBJM" and o.tri_count > 0 for o in ai) and len(ai) >= 3,
                  f"{scene}: AI routes exported as meshes", f"{len(ai)}")
    check_bounds.export(check, track)  # out of bounds on the built scenes: resets, kill floor, ground coverage
    print(f"EXPORT CHECKS {'FAILED' if failures else 'OK'}: {len(failures)} failures")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
