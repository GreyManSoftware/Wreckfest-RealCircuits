"""Measure a real circuit's run-off surfaces, barriers and trees from open LIDAR and OSM.

    <venv>\\Scripts\\python tools/real_tracks_data/measure_runoff.py doningtonpark [--plots]

Inputs: the raster from lidar_raster.py (E:\\Source\\wreckfest\\.local\\realtracks\\<slug>\\runoff\\raster.npz),
the OSM features from fetch_osm_runoff.py (osm-runoff.json), the committed sample (routes and measured widths),
its local frame (import_frame.py) and the per-circuit evidence in runoff_evidence.json (what kind of barrier the
LIDAR sees, corner facts from published sources).

**Run-off** (every 4 m along every route, each side, a profile from the road edge out to 50 m in 0.5 m bins):

- surface per bin: England from the ground's LIDAR intensity, three clear modes on every circuit (asphalt dark,
  gravel mid-grey, grass bright; thresholds from the circuit's own road and grass levels); Navarre from the
  2024 flight's colour (asphalt dark) plus OSM gravel-trap polygons; Wales (no point cloud) from OSM only. OSM
  ``natural=shingle|sand``, ``surface=gravel|sand`` areas and ``raceway=gravel trap`` are gravel, ``area:highway``
  and paved areas asphalt;
- barrier: the first narrow raised object (0.35-3 m above the ground, not trees, at most 4 m deep) or an OSM
  ``barrier=guard_rail|tyres|wall|jersey_barrier`` (a fence within 25 m is a debris fence); the type comes from
  OSM where a tagged barrier lies within 4 m, else from the evidence (``trapBarrier`` behind a gravel trap,
  ``barrier`` elsewhere) and corner overrides; a bank where the ground rises over 1.2 m within 12 m first;
- each section: tarmac contiguous from the edge (apron depth), the first gravel run of 3 m or more starting within
  15 m (start and depth), the barrier's offset and type; profiles stop half way to another road.

Sections are mode-filtered along the route (7 sections) and merged into zones of at least 16 m: side, fromS/toS
(the sample's metres along that route, and the zone ends' grid coordinates so a re-imported sample can be
matched), surface (gravel / tarmac / grass), tarmacM, startM, depthM, barrier, offsetM, fence, colour, source
and confidence.

**Vegetation**: a canopy raster (DSM - DTM over 3 m and, with a point cloud, classified vegetation) at 1 m,
cover at 5 m; woods = cover >= 0.5 traced as polygons (simplified 3 m, at least 400 m2) with their mean cover;
single trees = canopy peaks over 4 m outside the woods (at least 6 m apart), with their height. OSM
``natural=tree`` nodes confirm (not add) trees; in Wales (DSM only, buildings included) buildings from OSM are
masked.

Outputs (derived, small, committed): tools/real_tracks/runoff/<slug>.json, tools/real_tracks/vegetation/<slug>.json.
With --plots: E:\\Source\\wreckfest\\.local\\audit\\<slug>\\runoff-*.png and vegetation-*.png.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_pointcloud import LOCAL, REGION, ROOT, report_path  # noqa: E402

STEP_M = 4.0
BIN_M = 0.5
MAX_OUT_M = 60.0
SKIP_M = 0.75            # kerb / painted edge
MIN_RUN_M = 3.0          # a gravel or tarmac run-off is at least this deep
GRAVEL_START_MAX_M = 15.0
BARRIER_MAX_DEPTH_M = 4.0
MODE_SECTIONS = 7
MIN_ZONE_M = 16.0
OUT_RUNOFF = ROOT / "tools" / "real_tracks" / "runoff"
OUT_VEG = ROOT / "tools" / "real_tracks" / "vegetation"
EVIDENCE = Path(__file__).resolve().parent / "runoff_evidence.json"
SURF = {"A": "tarmac", "G": "gravel", "R": "grass"}
SOURCES = {
    "england": "Environment Agency National LIDAR Programme point cloud (intensity, classification, heights); Open Government Licence v3.0; Contains Environment Agency information (c) Environment Agency and/or database right",
    "navarra": "Gobierno de Navarra LiDAR 2024 point cloud with RGBI; CC BY 4.0; Servicio proporcionado por el Gobierno de Navarra (IDENA)",
    "wales": "Welsh Government / NRW LIDAR DTM and DSM 1 m (DataMapWales); Open Government Licence v3.0; Contains Natural Resources Wales information (c) Natural Resources Wales and database right",
}
OSM_SOURCE = "(c) OpenStreetMap contributors, ODbL 1.0"


# ------------------------------------------------------------------------------------------------ inputs
def load(slug: str) -> dict:
    sample = ROOT / "geom" / "tests" / "samples" / f"{slug}.json"
    spec = json.loads(sample.read_text(encoding="utf-8"))
    fr = json.loads(report_path(slug).read_text(encoding="utf-8"))["frame"]
    imp = spec["sketch"]["imported"]
    off = np.array([fr["centreE"], fr["centreN"]])
    routes = []
    for rid, name, c, closed in ([("main", imp.get("mainLayoutName", "Main"), imp["centreline"], True)]
                                 + [(a["id"], a["name"], a["centreline"], False) for a in imp.get("altLayouts", [])]):
        xy = np.array([[q["x"], q["y"]] for q in c])
        q = np.vstack([xy, xy[:1]]) if closed else xy
        s_local = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(q, axis=0).T))])
        w = np.array([q_.get("widthM", spec["options"].get("widthM", 12.0)) for q_ in c])
        routes.append({"id": rid, "name": name, "closed": closed, "xy_local": xy, "s_local": s_local[:len(xy)],
                       "length_local": float(s_local[-1]), "xy": xy * fr["scale"] + off, "w": w * fr["scale"]})
    R = np.load(LOCAL / "realtracks" / slug / "runoff" / "raster.npz")
    raster = {k: R[k] for k in R.files}
    osm = json.loads((LOCAL / "realtracks" / slug / "osm-runoff.json").read_text(encoding="utf-8"))
    ev = json.loads(EVIDENCE.read_text(encoding="utf-8"))[slug]
    return {"slug": slug, "spec": spec, "frame": fr, "routes": routes, "raster": raster, "osm": osm, "evidence": ev,
            "region": REGION.get(slug, "england")}


def grid_proj(slug: str, osm: dict):
    """lon/lat -> the recipe's grid (BNG via the importer's Helmert, or UTM 30N in Spain)."""
    sys.path.insert(0, str(ROOT / "tools"))
    import import_real_track as irt  # noqa: PLC0415

    recipe = json.loads((ROOT / "tools" / "real_tracks" / f"{slug}.json").read_text(encoding="utf-8"))
    irt.set_projection(recipe)
    return lambda lon, lat: np.array([irt.to_grid(b, a) for a, b in zip(lon, lat)], dtype=np.float64).reshape(-1, 2)


# ------------------------------------------------------------------------------------------------ OSM
GRAVEL_TAGS = (("natural", ("shingle", "sand")), ("surface", ("gravel", "sand", "fine_gravel", "pebblestone")))
BARRIER_TYPES = {"guard_rail": "armco", "tyres": "tyres", "tyers": "tyres", "wall": "concrete",
                 "jersey_barrier": "concrete", "fence": "fence", "retaining_wall": "concrete"}


def osm_features(osm: dict, proj) -> dict:
    gravel, paved, barriers, trees, buildings = [], [], [], [], []
    for e in osm["elements"]:
        t = e.get("tags", {})
        if e["type"] == "node":
            if t.get("natural") == "tree":
                trees.append(proj([e["lon"]], [e["lat"]])[0])
            continue
        geoms = []
        if e["type"] == "way" and e.get("geometry"):
            geoms = [e["geometry"]]
        elif e["type"] == "relation":
            geoms = [m["geometry"] for m in e.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
        for g in geoms:
            p = proj([q["lon"] for q in g], [q["lat"] for q in g])
            closed = len(p) > 3 and np.hypot(*(p[0] - p[-1])) < 0.5
            if t.get("golf"):
                continue
            if "highway" in t and t.get("area") != "yes":
                if t.get("barrier") in BARRIER_TYPES:
                    pass
                else:
                    continue
            if closed and (any(t.get(k) in v for k, v in GRAVEL_TAGS) or "gravel trap" in t.get("raceway", "")):
                gravel.append(p)
            elif closed and (t.get("area:highway") or (t.get("area") == "yes" and t.get("surface") in ("asphalt", "paved", "concrete"))):
                paved.append(p)
            if t.get("barrier") in BARRIER_TYPES:
                barriers.append((BARRIER_TYPES[t["barrier"]], p))
            if t.get("building") and closed:
                buildings.append(p)
    return {"gravel": gravel, "paved": paved, "barriers": barriers, "trees": np.array(trees).reshape(-1, 2),
            "buildings": buildings}


def rasterise_polys(polys: list[np.ndarray], x0: float, y0: float, res: float, shape) -> np.ndarray:
    from matplotlib.path import Path as MPath  # noqa: PLC0415

    ny, nx = shape
    out = np.zeros(shape, dtype=bool)
    for p in polys:
        lo, hi = p.min(0), p.max(0)
        i0, i1 = max(int((lo[0] - x0) / res), 0), min(int((hi[0] - x0) / res) + 2, nx)
        j0, j1 = max(int((lo[1] - y0) / res), 0), min(int((hi[1] - y0) / res) + 2, ny)
        if i0 >= i1 or j0 >= j1:
            continue
        gx, gy = np.meshgrid(x0 + (np.arange(i0, i1) + 0.5) * res, y0 + (np.arange(j0, j1) + 0.5) * res)
        m = MPath(p).contains_points(np.column_stack([gx.ravel(), gy.ravel()])).reshape(gx.shape)
        out[j0:j1, i0:i1] |= m
    return out


def seg_intersect(p0: np.ndarray, d: np.ndarray, length: float, poly: np.ndarray) -> list[float]:
    """Distances along the ray p0 + t d (0 <= t <= length) where it crosses the polyline."""
    a, b = poly[:-1], poly[1:]
    e = b - a
    den = d[0] * e[:, 1] - d[1] * e[:, 0]
    ok = np.abs(den) > 1e-9
    w = a - p0
    t = np.where(ok, (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / np.where(ok, den, 1), -1)
    u = np.where(ok, (w[:, 0] * d[1] - w[:, 1] * d[0]) / np.where(ok, den, 1), -1)
    hit = ok & (t >= 0) & (t <= length) & (u >= 0) & (u <= 1)
    return sorted(t[hit].tolist())


# ------------------------------------------------------------------------------------------------ helpers
def resample(xy: np.ndarray, s: np.ndarray, w: np.ndarray, closed: bool, step: float, length: float):
    n = int(length // step) if closed else int(length // step) + 1
    t = np.minimum(np.arange(n) * step, length - 1e-6)
    q = np.vstack([xy, xy[:1]]) if closed else xy
    ss = np.append(s, length) if closed else s
    ww = np.append(w, w[:1]) if closed else w
    P = np.column_stack([np.interp(t, ss, q[:, 0]), np.interp(t, ss, q[:, 1])])
    W = np.interp(t, ss, ww)
    if closed:
        d = np.roll(P, -1, axis=0) - np.roll(P, 1, axis=0)
    else:
        d = np.gradient(P, axis=0)
    d /= np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)[:, None]
    nl = np.column_stack([-d[:, 1], d[:, 0]])
    k = np.zeros(n)
    ang = np.arctan2(d[:, 1], d[:, 0])
    da = np.angle(np.exp(1j * ((np.roll(ang, -1) - np.roll(ang, 1)) if closed else np.gradient(ang))))
    k = da / (2 * step if closed else step)
    return P, t, W, d, nl, ndimage.uniform_filter1d(k, 9, mode="wrap" if closed else "nearest")


class Sampler:
    def __init__(self, r: dict):
        self.x0, self.y0, self.res = float(r["x0"]), float(r["y0"]), float(r["res"])
        self.r = r
        self.ny, self.nx = r["dtm"].shape

    def idx(self, xy: np.ndarray):
        i = np.clip(((xy[..., 0] - self.x0) / self.res).astype(np.int64), 0, self.nx - 1)
        j = np.clip(((xy[..., 1] - self.y0) / self.res).astype(np.int64), 0, self.ny - 1)
        return j, i

    def __call__(self, key: str, xy: np.ndarray) -> np.ndarray:
        j, i = self.idx(xy)
        return self.r[key][j, i]


# ------------------------------------------------------------------------------------------------ measure
def calibrate(D: dict, S: Sampler) -> dict:
    """Road and grass intensity (or brightness) levels of this circuit and the class thresholds."""
    ev = D["evidence"].get("thresholds", {})
    key = "inten" if D["region"] == "england" else ("bright" if D["region"] == "navarra" else None)
    if key is None:
        return {"key": None}
    road, far = [], []
    for r in D["routes"][:1]:
        P, t, W, d, nl, k = resample(r["xy"], r["s_local"] * D["frame"]["scale"], r["w"], r["closed"], 8.0,
                                     r["length_local"] * D["frame"]["scale"])
        for f in (-0.3, -0.15, 0.0, 0.15, 0.3):
            road.append(S(key, P + nl * (W * f)[:, None]))
        for side in (1, -1):
            for off in np.arange(12.0, 45.0, 3.0):
                q = P + nl * side * (W / 2 + off)[:, None]
                v = S(key, q)
                v[S("chm", q) > 0.3] = np.nan
                far.append(v)
    rl = float(np.nanmedian(np.concatenate(road)))
    gl = float(np.nanpercentile(np.concatenate(far), 80))
    ta = ev.get("asphaltMax", rl + 0.22 * (gl - rl))
    tg = ev.get("gravelMax", rl + 0.62 * (gl - rl))
    return {"key": key, "road": round(rl, 3), "grass": round(gl, 3), "asphaltMax": round(ta, 3), "gravelMax": round(tg, 3)}


def other_road_tree(D: dict):
    pts, rid, ss = [], [], []
    for k, r in enumerate(D["routes"]):
        pts.append(r["xy"])
        rid.append(np.full(len(r["xy"]), k))
        ss.append(r["s_local"])
    return cKDTree(np.vstack(pts)), np.concatenate(rid), np.concatenate(ss), np.concatenate([r["w"] for r in D["routes"]])


def classify_bins(D, S, cal, P, nl, W, side, gravel_mask, paved_mask):
    """Per section x bin labels for one side: 'A' asphalt, 'G' gravel, 'R' grass, 'T' tree, 'O' object."""
    nb = int(MAX_OUT_M / BIN_M)
    off = (np.arange(nb) + 0.5) * BIN_M
    Q = P[:, None, :] + nl[:, None, :] * side * (W[:, None] / 2 + off[None, :])[..., None]
    H = S("chm", Q)
    lab = np.full(H.shape, "R", dtype="<U1")
    if cal["key"]:
        v = S(cal["key"], Q)
        lab[v < cal["asphaltMax"]] = "A"
        if D["region"] == "england":
            # mid-grey: gravel, or (an old airfield without traps: Bedford) the concrete of its aprons
            mid = "A" if D["evidence"]["defaults"].get("midIntensity") == "tarmac" else "G"
            lab[(v >= cal["asphaltMax"]) & (v < cal["gravelMax"])] = mid
        lab[np.isnan(v)] = "R"
    j, i = S.idx(Q)
    lab[gravel_mask[j, i]] = "G"
    lab[paved_mask[j, i]] = "A"
    veg = S("veg", Q) if "veg" in S.r else np.where(H > 3.0, 1.0, 0.0)
    tree = (H > 3.0) & (veg > 0.25)
    obj = (H > 0.35) & (H < 3.0) & ~tree
    if "low" in S.r:
        obj |= (S("low", Q) > 0.3) & (H < 3.0) & (H > 0.25)
    lab[tree] = "T"
    lab[obj] = "O"
    Z = S("dtm", Q) - S("dtm", P + nl * side * (W / 2)[:, None])[:, None]
    return lab, H, Z, Q, off


def profile_section(lab, H, Z, off, cut: float) -> dict:
    """One profile -> tarmacM, gravel start/depth, barrier offset + fence flag, bank."""
    nb = len(lab)
    lim = int(min(cut, MAX_OUT_M) / BIN_M)
    k0 = int(SKIP_M / BIN_M)
    out = {"tarmacM": 0.0, "startM": None, "depthM": 0.0, "barrierM": None, "fence": False, "bank": None, "treeM": None}
    # barrier: first object run (gap of one bin allowed) no deeper than BARRIER_MAX_DEPTH_M
    k = k0
    while k < lim:
        if lab[k] == "O":
            e = k
            while e + 1 < lim and (lab[e + 1] == "O" or (e + 2 < lim and lab[e + 2] == "O")):
                e += 1
            depth = (e - k + 1) * BIN_M
            if depth <= BARRIER_MAX_DEPTH_M or k * BIN_M < 12.0:
                out["barrierM"] = round(off[k] - BIN_M / 2, 2)
                win = H[max(k - 2, 0):min(e + 3, nb)]
                break
            k = e + 1
            continue
        if lab[k] == "T" and out["treeM"] is None:
            out["treeM"] = round(off[k], 2)
        k += 1
    stop = int((out["barrierM"] if out["barrierM"] is not None else min(cut, MAX_OUT_M)) / BIN_M)
    # bank: ground more than 1.2 m above the edge within 12 m, before any barrier
    rise = np.nonzero(Z[k0:min(stop, int(12.0 / BIN_M))] > 1.2)[0]
    if len(rise):
        kb = k0 + int(rise[0])
        z0 = np.nonzero(Z[k0:kb + 1] > 0.3)[0]
        out["bank"] = round(off[k0 + int(z0[0])] if len(z0) else off[kb], 2)
        stop = min(stop, int(out["bank"] / BIN_M))
    seq = lab[:stop]
    # tarmac contiguous from the edge (one non-asphalt bin tolerated)
    a = 0
    k = 0
    while k < len(seq) and (seq[k] == "A" or (k + 1 < len(seq) and seq[k + 1] == "A" and seq[k] != "G")):
        k += 1
    a = k * BIN_M
    if a >= MIN_RUN_M:
        out["tarmacM"] = round(a, 2)
    # first gravel run (1 m gaps allowed) starting within GRAVEL_START_MAX_M
    g = np.nonzero(seq == "G")[0]
    if len(g):
        st = g[0]
        if st * BIN_M <= GRAVEL_START_MAX_M + out["tarmacM"]:
            e = st
            for x in g[1:]:
                if x - e <= 3:
                    e = x
                else:
                    break
            depth = (e - st + 1) * BIN_M
            if depth >= MIN_RUN_M:
                out["startM"], out["depthM"] = round(st * BIN_M, 2), round(depth, 2)
    return out


def mode_filter(v: list, k: int, closed: bool) -> list:
    n = len(v)
    out = []
    for i in range(n):
        win = [v[(i + j) % n] if closed else v[min(max(i + j, 0), n - 1)] for j in range(-(k // 2), k // 2 + 1)]
        vals, cnt = np.unique(win, return_counts=True)
        best = vals[np.argmax(cnt)]
        out.append(v[i] if cnt.max() == np.sum(np.array(win) == v[i]) else best)
    return out


def corner_side_masks(D: dict, r: dict, t: np.ndarray, k: np.ndarray) -> list:
    """Evidence overrides on this route: (mask over sections, side +1 left / -1 right, override)."""
    out = []
    if r["id"] != "main":
        return out
    corners = {c["name"]: c["sM"] for c in D["spec"]["sketch"]["imported"].get("corners", [])}
    L = r["length_local"]
    tl = t / D["frame"]["scale"]
    for o in D["evidence"].get("overrides", []):
        if o["corner"] not in corners:
            print(f"  evidence: no corner {o['corner']!r} in the sample", file=sys.stderr)
            continue
        s0 = corners[o["corner"]]
        d = (tl - s0 + L / 2) % L - L / 2
        m = (d >= o.get("fromM", -60.0)) & (d <= o.get("toM", 80.0))
        if not m.any():
            continue
        side = o["side"]
        if side in ("outside", "inside"):
            kk = k[m]
            turn = float(np.sum(kk))  # > 0 turns left
            left_turn = turn > 0
            outside = -1 if left_turn else 1
            sd = outside if side == "outside" else -outside
        else:
            sd = 1 if side == "left" else -1
        out.append((m, sd, o))
    return out


def measure(D: dict, log) -> dict:
    S = Sampler(D["raster"])
    proj = grid_proj(D["slug"], D["osm"])
    osm = osm_features(D["osm"], proj)
    shape = D["raster"]["dtm"].shape
    gmask = rasterise_polys(osm["gravel"], S.x0, S.y0, S.res, shape)
    pmask = rasterise_polys(osm["paved"], S.x0, S.y0, S.res, shape)
    cal = calibrate(D, S)
    log(f"{D['slug']}: calibration {cal}; OSM gravel {len(osm['gravel'])}, paved {len(osm['paved'])}, barriers {len(osm['barriers'])}")
    tree, rid_all, s_all, w_all = other_road_tree(D)
    ev = D["evidence"]
    defaults = ev["defaults"]
    scale = D["frame"]["scale"]
    result = {"routes": [], "calibration": cal}
    for ri, r in enumerate(D["routes"]):
        L = r["length_local"] * scale
        P, t, W, d, nl, k = resample(r["xy"], r["s_local"] * scale, r["w"], r["closed"], STEP_M, L)
        overrides = corner_side_masks(D, r, t, k)
        sides = {}
        for side in (1, -1):
            lab, H, Z, Q, off = classify_bins(D, S, cal, P, nl, W, side, gmask, pmask)
            # profiles stop half way to another road (other routes, or this one further than 60 m along)
            cut = np.full(len(P), MAX_OUT_M)
            for kk, oo in enumerate(off[::4]):
                q = Q[:, kk * 4, :]
                dd, jj = tree.query(q)
                other = (rid_all[jj] != ri) | (np.abs(s_all[jj] - r["s_local"][np.clip(np.searchsorted(r["s_local"], t / scale), 0, len(r["s_local"]) - 1)]) > 60.0)
                near = other & (dd < w_all[jj] / 2 * scale + 1.0)
                newcut = (oo - w_all[jj] / 2 * 0) / 2.0
                cut = np.where(near & (cut == MAX_OUT_M), np.maximum(oo / 2.0, 1.0), cut)
            secs = []
            for i in range(len(P)):
                sec = profile_section(lab[i], H[i], Z[i], off, cut[i])
                # OSM barriers across this profile
                p0 = P[i] + nl[i] * side * W[i] / 2
                hits = []
                for typ, poly in osm["barriers"]:
                    if len(poly) < 2:
                        continue
                    for tt in seg_intersect(p0, nl[i] * side, min(cut[i], MAX_OUT_M), poly):
                        if typ == "fence" and tt > 25.0:
                            continue
                        if typ == "concrete" and tt > 30.0:
                            continue
                        hits.append((tt, typ))
                hits.sort()
                sec["osm"] = hits[:3]
                sec["cut"] = float(cut[i])
                secs.append(sec)
            sides[side] = {"secs": secs, "lab": lab}
        result["routes"].append({"route": r, "t": t, "P": P, "nl": nl, "W": W, "k": k, "sides": sides,
                                 "overrides": overrides})
    result["osm"] = osm
    return result


def section_values(sec: dict, defaults: dict) -> dict:
    """Run-off and barrier of one section, with OSM barrier types where they agree with the LIDAR."""
    surface = "gravel" if sec["startM"] is not None else ("tarmac" if sec["tarmacM"] >= MIN_RUN_M else "grass")
    barrier, off, src, fence = "none", None, "lidar", sec["fence"]
    hard = [(t_, y) for t_, y in sec["osm"] if y != "fence"]
    fences = [(t_, y) for t_, y in sec["osm"] if y == "fence"]
    if sec["barrierM"] is not None:
        off = sec["barrierM"]
        near = [y for t_, y in hard if abs(t_ - off) <= 4.0]
        if near:
            barrier, src = near[0], "osm+lidar"
        else:
            barrier = defaults["trapBarrier"] if surface == "gravel" else defaults["barrier"]
            src = "lidar+evidence"
        if any(abs(t_ - off) <= 4.0 for t_, _ in fences):
            fence = True
    elif hard:
        off, barrier, src = hard[0][0], hard[0][1], "osm"
    elif sec["bank"] is not None:
        off, barrier, src = sec["bank"], "bank", "lidar"
    if surface == "gravel" and off is not None and barrier not in ("bank",):
        # the trap ends at the barrier at the latest
        sec["depthM"] = min(sec["depthM"], max(off - 0.5 - sec["startM"], 0.0))
        if sec["depthM"] < MIN_RUN_M:
            surface = "tarmac" if sec["tarmacM"] >= MIN_RUN_M else "grass"
    if barrier == "none":
        off = None
    return {"surface": surface, "tarmacM": sec["tarmacM"], "startM": sec["startM"] or 0.0,
            "depthM": sec["depthM"] if surface == "gravel" else 0.0, "barrier": barrier,
            "offsetM": off, "fence": bool(fence) and barrier not in ("none", "bank"), "src": src}


def zones_for(res_route: dict, side: int, D: dict) -> list[dict]:
    r = res_route["route"]
    secs = res_route["sides"][side]["secs"]
    defaults = D["evidence"]["defaults"]
    vals = [section_values(dict(s), defaults) for s in secs]
    closed = r["closed"]
    n = len(vals)
    # evidence overrides
    ovsrc = [None] * n
    for m, sd, o in res_route["overrides"]:
        if sd != side:
            continue
        for i in np.nonzero(m)[0]:
            v = vals[i]
            if "surface" in o:
                v["surface"] = o["surface"]
                if o["surface"] != "gravel":
                    v["depthM"] = 0.0
            for key in ("startM", "depthM", "offsetM"):
                if key in o:
                    v[key] = o[key]
            if "barrier" in o and (v["barrier"] not in ("none", "bank") or "offsetM" in o):
                v["barrier"] = o["barrier"]
            if "fence" in o and v["barrier"] not in ("none", "bank"):
                v["fence"] = bool(o["fence"])
            ovsrc[i] = o["source"]
    # mode filter on the categorical parts, then zones
    key = [f"{v['surface']}|{v['barrier']}|{int(v['fence'])}" for v in vals]
    key = mode_filter(key, MODE_SECTIONS, closed)
    key = mode_filter(key, MODE_SECTIONS, closed)
    # also break where the barrier offset jumps by more than 5 m (median-filtered)
    offs = np.array([v["offsetM"] if v["offsetM"] is not None else np.nan for v in vals], dtype=float)
    offm = ndimage.generic_filter(offs, np.nanmedian, size=5, mode="wrap" if closed else "nearest")
    band = np.where(np.isfinite(offm), np.round(offm / 6.0), -1).astype(int)
    full = [f"{a}|{b}" for a, b in zip(key, mode_filter(band.tolist(), 5, closed))]
    runs = []
    start = 0
    if closed:
        brk = [i for i in range(n) if full[i] != full[i - 1]]
        if not brk:
            runs = [(0, n)]
        else:
            for a, b in zip(brk, brk[1:] + [brk[0] + n]):
                runs.append((a, b))
    else:
        for i in range(1, n + 1):
            if i == n or full[i] != full[i - 1]:
                runs.append((start, i))
                start = i
    # merge short runs into their longer neighbour
    min_secs = int(math.ceil(MIN_ZONE_M / STEP_M))
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for q, (a, b) in enumerate(runs):
            if b - a < min_secs:
                nb_ = [x for x in (q - 1, q + 1) if 0 <= x < len(runs)] if not closed else [(q - 1) % len(runs), (q + 1) % len(runs)]
                tgt = max(nb_, key=lambda x: runs[x][1] - runs[x][0])
                ta, tb = runs[tgt]
                runs[tgt] = (min(a, ta), max(b, tb)) if tgt != q else runs[tgt]
                if closed and abs(tgt - q) > 1:  # wrap neighbour
                    runs[tgt] = (ta, tb + (b - a)) if tgt < q else (a - n if a >= n else a, tb)
                del runs[q]
                changed = True
                break
    t = res_route["t"]
    scale = D["frame"]["scale"]
    out = []
    for a, b in runs:
        idx = np.arange(a, b) % n
        kk = [full[i] for i in idx]
        dom = max(set(kk), key=kk.count)
        sel = [i for i in idx if full[i] == dom] or list(idx)
        v = [vals[i] for i in sel]
        surface, barrier, fence = dom.split("|")[0], dom.split("|")[1], dom.split("|")[2] == "1"
        agree = len(sel) / len(idx)
        g = [x for x in v if x["surface"] == "gravel"]
        offs_ = [x["offsetM"] for x in v if x["offsetM"] is not None]
        tar = [x["tarmacM"] for x in v]
        srcs = sorted({x["src"] for x in v})
        ev_src = sorted({ovsrc[i] for i in sel if ovsrc[i]})
        conf = round(min(1.0, agree * (0.9 if D["region"] == "england" else 0.6 if D["region"] == "navarra" else 0.4)), 2)
        z = {"side": "left" if side > 0 else "right",
             "fromS": round(float(t[a % n] / scale), 1),
             "toS": round(float(t[(b - 1) % n] / scale + STEP_M / scale), 1),
             "surface": surface,
             "tarmacM": round(float(np.median(tar)), 1) if surface != "grass" else 0.0,
             "startM": round(float(np.median([x["startM"] for x in g])), 1) if g and surface == "gravel" else 0.0,
             "depthM": round(float(np.median([x["depthM"] for x in g])), 1) if g and surface == "gravel" else 0.0,
             "barrier": barrier,
             "offsetM": round(float(np.median(offs_)), 1) if offs_ and barrier != "none" else None,
             "fence": fence,
             "colour": D["evidence"]["defaults"].get("colour", "black"),
             "confidence": conf,
             "source": "; ".join(srcs + (["evidence: " + " | ".join(ev_src)] if ev_src else [])),
             "e": [round(float(res_route["P"][a % n][0]), 1), round(float(res_route["P"][a % n][1]), 1)],
             "e2": [round(float(res_route["P"][(b - 1) % n][0]), 1), round(float(res_route["P"][(b - 1) % n][1]), 1)]}
        if z["surface"] == "gravel" and z["offsetM"] is not None and z["barrier"] in ("armco", "tyres", "concrete"):
            # medians taken separately: keep the trap in front of the zone's barrier
            z["depthM"] = round(min(z["depthM"], z["offsetM"] - 0.5 - z["startM"]), 1)
            if z["depthM"] < MIN_RUN_M:
                z["surface"], z["startM"], z["depthM"] = ("tarmac" if z["tarmacM"] >= MIN_RUN_M else "grass"), 0.0, 0.0
        if z["toS"] > r["length_local"] and not r["closed"]:
            z["toS"] = round(r["length_local"], 1)
        out.append(z)
    out.sort(key=lambda z: z["fromS"])
    return out


# ------------------------------------------------------------------------------------------------ vegetation
def vegetation(D: dict, osm: dict, log) -> dict:
    R = D["raster"]
    x0, y0, res = float(R["x0"]), float(R["y0"]), float(R["res"])
    chm = R["chm"].copy()
    if D["region"] == "wales":
        bmask = rasterise_polys(osm["buildings"], x0, y0, res, chm.shape)
        bmask = ndimage.binary_dilation(bmask, iterations=3)
        chm[bmask] = 0.0
        canopy = chm > 3.0
    else:
        canopy = (chm > 3.0) & (R["veg"] > 0.2)
    # remove thin linear objects (power lines, fences) and single noisy cells
    canopy = ndimage.binary_opening(canopy, iterations=1)
    f = 5
    ny, nx = canopy.shape
    cy, cx = ny // f, nx // f
    cover = canopy[:cy * f, :cx * f].reshape(cy, f, cx, f).mean((1, 3))
    smooth = ndimage.uniform_filter(cover, 3)
    woods_mask = ndimage.binary_closing(smooth >= 0.5, iterations=1)
    lab, n = ndimage.label(woods_mask)
    polys = []
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt  # noqa: PLC0415
    xs = x0 + (np.arange(cx) + 0.5) * f * res
    ys = y0 + (np.arange(cy) + 0.5) * f * res
    sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1))
    keep = np.zeros(n + 1, bool)
    keep[1:] = sizes * (f * res) ** 2 >= 400.0
    km = keep[lab]
    cs = plt.contour(xs, ys, ndimage.gaussian_filter(km.astype(float), 0.6), levels=[0.5])
    for path in cs.allsegs[0]:
        p = np.asarray(path)
        if len(p) < 4:
            continue
        area = 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1)))
        if area < 400.0:
            continue
        p = simplify(p, 3.0)
        if len(p) < 4:
            continue
        # mean cover inside
        from matplotlib.path import Path as MPath  # noqa: PLC0415
        gx, gy = np.meshgrid(xs, ys)
        inside = MPath(p).contains_points(np.column_stack([gx.ravel(), gy.ravel()])).reshape(gx.shape)
        cov = float(cover[inside].mean()) if inside.any() else 0.6
        polys.append({"points": p, "cover": round(cov, 2), "areaM2": round(area)})
    plt.close("all")
    # single trees: canopy peaks outside the woods
    hmax = ndimage.maximum_filter(np.where(canopy, chm, 0), size=7)
    peaks = canopy & (chm >= 4.0) & (chm == hmax)
    wj, wi = np.nonzero(peaks)
    in_wood = km[np.minimum(wj // f, cy - 1), np.minimum(wi // f, cx - 1)]
    tx = x0 + (wi[~in_wood] + 0.5) * res
    ty = y0 + (wj[~in_wood] + 0.5) * res
    th = chm[wj[~in_wood], wi[~in_wood]]
    # at least 6 m apart (tallest first)
    order = np.argsort(-th)
    kept = []
    tr = None
    pts = np.column_stack([tx, ty])
    taken = np.zeros(len(order), bool)
    if len(pts):
        tr = cKDTree(pts)
        for o in order:
            if taken[o]:
                continue
            kept.append(o)
            for q in tr.query_ball_point(pts[o], 6.0):
                taken[q] = True
    trees = [(float(tx[o]), float(ty[o]), float(th[o])) for o in kept]
    osm_trees = osm["trees"]
    confirmed = 0
    if len(osm_trees) and trees:
        d, _ = cKDTree(np.array([(a, b) for a, b, _ in trees])).query(osm_trees)
        confirmed = int((d < 8.0).sum())
    log(f"{D['slug']}: {len(polys)} woods ({sum(p['areaM2'] for p in polys) / 1e4:.1f} ha), {len(trees)} single trees "
        f"(OSM natural=tree nodes {len(osm_trees)}, {confirmed} within 8 m of a measured tree); canopy cover "
        f"{canopy.mean():.1%} of the box")
    return {"woods": polys, "trees": trees, "cover": cover, "coverFrame": (x0, y0, f * res), "canopyFraction": float(canopy.mean()),
            "osmTreesConfirmed": confirmed, "osmTrees": len(osm_trees)}


def simplify(p: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker on a closed ring."""
    def dp(a):
        if len(a) < 3:
            return a
        s, e = a[0], a[-1]
        d = e - s
        n = math.hypot(*d)
        if n < 1e-9:
            dist = np.hypot(*(a - s).T)
        else:
            dist = np.abs(d[0] * (a[:, 1] - s[1]) - d[1] * (a[:, 0] - s[0])) / n
        k = int(np.argmax(dist))
        if dist[k] > tol:
            return np.vstack([dp(a[:k + 1])[:-1], dp(a[k:])])
        return np.vstack([s, e])
    if np.hypot(*(p[0] - p[-1])) < 1e-6:
        p = p[:-1]
    h = len(p) // 2
    out = np.vstack([dp(p[:h + 1])[:-1], dp(np.vstack([p[h:], p[:1]]))[:-1]])
    return out


# ------------------------------------------------------------------------------------------------ outputs
def shares(zones: list[dict], length: float) -> dict:
    tot = {"gravel": 0.0, "tarmac": 0.0, "grass": 0.0}
    bar = {}
    for z in zones:
        L = (z["toS"] - z["fromS"]) % length if length else z["toS"] - z["fromS"]
        tot[z["surface"]] += L
        bar[z["barrier"]] = bar.get(z["barrier"], 0.0) + L
    s = sum(tot.values()) or 1.0
    return {"surface": {k: round(v / s, 3) for k, v in tot.items()}, "barrier": {k: round(v / s, 3) for k, v in sorted(bar.items())}}


def write(D: dict, res: dict, veg: dict, log) -> tuple[Path, Path]:
    OUT_RUNOFF.mkdir(parents=True, exist_ok=True)
    OUT_VEG.mkdir(parents=True, exist_ok=True)
    region = D["region"]
    routes = []
    for rr in res["routes"]:
        r = rr["route"]
        zl = zones_for(rr, 1, D)
        zr = zones_for(rr, -1, D)
        rr["zones"] = zl + zr
        routes.append({"id": r["id"], "name": r["name"], "lengthM": round(r["length_local"], 1),
                       "summary": {"left": shares(zl, r["length_local"] if r["closed"] else 0),
                                   "right": shares(zr, r["length_local"] if r["closed"] else 0)},
                       "zones": sorted(zl + zr, key=lambda z: (z["fromS"], z["side"]))})
    out = {
        "description": f"Measured run-off and barriers of {D['slug']} (tools/real_tracks_data/measure_runoff.py; derived "
                       "data). Per route and side, zones along the sample's centreline (fromS/toS in the sample's "
                       "metres; e/e2 the zone ends on the centreline in the recipe's grid): surface gravel|tarmac|grass "
                       "beyond the road edge, tarmacM (paved apron from the edge), startM/depthM (the gravel trap, "
                       "metres from the edge), barrier armco|tyres|concrete|bank|none at offsetM from the edge, fence "
                       "(a debris fence above it), colour (black unless a source confirms red/white), confidence 0-1 "
                       "and source (lidar, osm, evidence).",
        "sources": [SOURCES[region], f"Gravel/paved areas and barrier types: {OSM_SOURCE}",
                    "Barrier types the LIDAR can't tell and corner facts: tools/real_tracks_data/runoff_evidence.json "
                    "(published track guides and circuit news, each with its URL)"],
        "evidence": D["evidence"]["defaults"]["source"],
        "calibration": res["calibration"],
        "method": f"profiles every {STEP_M:g} m to {MAX_OUT_M:g} m beyond the measured road edge, {BIN_M:g} m bins; "
                  f"zones mode-filtered over {MODE_SECTIONS} sections, at least {MIN_ZONE_M:g} m",
        "routes": routes,
    }
    p1 = OUT_RUNOFF / f"{D['slug']}.json"
    p1.write_text(json.dumps(out, indent=None, separators=(",", ":")).replace('{"side"', '\n{"side"') + "\n",
                  encoding="utf-8", newline="\n")
    vo = {
        "description": f"Measured trees and woods of {D['slug']} (measure_runoff.py; derived data): canopy over 3 m "
                       "(DSM - DTM, and vegetation classes where the point cloud has them). woods: polygons (recipe "
                       "grid, simplified 3 m) of 5 m cells at least half covered, with their mean cover; trees: "
                       "single trees and small clumps outside the woods (grid e, n and canopy height m), at least 6 m "
                       "apart.",
        "sources": [SOURCES[region], f"OSM natural=tree nodes checked against the measured trees: {OSM_SOURCE}"],
        "canopyFraction": round(veg["canopyFraction"], 4),
        "osmTrees": veg["osmTrees"], "osmTreesNearMeasured": veg["osmTreesConfirmed"],
        "woods": [{"cover": w["cover"], "areaM2": w["areaM2"],
                   "points": [[round(float(x), 1), round(float(y), 1)] for x, y in w["points"]]} for w in veg["woods"]],
        "trees": [[round(x, 1), round(y, 1), round(h, 1)] for x, y, h in veg["trees"]],
    }
    p2 = OUT_VEG / f"{D['slug']}.json"
    p2.write_text(json.dumps(vo, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    log(f"wrote {p1} and {p2}")
    for r in routes:
        log(f"  {r['id']}: left {r['summary']['left']}  right {r['summary']['right']}")
    return p1, p2


# ------------------------------------------------------------------------------------------------ plots
COL = {"A": (40, 40, 40), "G": (205, 170, 110), "R": (110, 170, 90), "T": (30, 100, 30), "O": (220, 40, 40)}
ZCOL = {"gravel": (230, 190, 90), "tarmac": (70, 70, 80), "grass": (90, 200, 90)}
BCOL = {"armco": (120, 200, 255), "tyres": (255, 110, 0), "concrete": (150, 150, 150), "bank": (140, 90, 40),
        "none": None}


def plots(D: dict, res: dict, veg: dict, log, zooms: list[tuple[str, float, float]]) -> list[Path]:
    from PIL import Image, ImageDraw  # noqa: PLC0415

    out = LOCAL / "audit" / D["slug"]
    out.mkdir(parents=True, exist_ok=True)
    R = D["raster"]
    x0, y0, rs = float(R["x0"]), float(R["y0"]), float(R["res"])
    cal = res["calibration"]
    if cal.get("key"):
        v = R[cal["key"]]
        g = np.clip((np.nan_to_num(v, nan=cal["grass"]) - cal["road"]) / max(cal["grass"] - cal["road"], 1e-6), 0, 1.3) / 1.3
    else:
        z = R["dtm"]
        gy, gx = np.gradient(np.nan_to_num(z, nan=np.nanmean(z)))
        g = np.clip(0.6 + (gx - gy) * 0.8, 0, 1)
    base = np.stack([g, g, g], -1) * 200 + 30
    canopy = np.clip((R["chm"] - 3.0) / 8.0, 0, 1)
    base = base * (1 - 0.5 * canopy[..., None]) + np.array([20, 110, 20]) * 0.5 * canopy[..., None]
    img = Image.fromarray(np.flipud(base).astype(np.uint8))
    H = img.size[1]
    px = lambda e, n: ((e - x0) / rs, H - (n - y0) / rs)  # noqa: E731
    dr = ImageDraw.Draw(img)
    for rr in res["routes"]:
        P, nl, W, t = rr["P"], rr["nl"], rr["W"], rr["t"]
        scale = D["frame"]["scale"]
        for z in rr["zones"]:
            side = 1 if z["side"] == "left" else -1
            n = len(P)
            a = int(round(z["fromS"] * scale / STEP_M))
            b = int(round(z["toS"] * scale / STEP_M))
            idx = [i % n for i in range(a, max(b, a + 1))] if rr["route"]["closed"] else list(range(a, min(b, n)))
            for i in idx:
                e0 = P[i] + nl[i] * side * W[i] / 2
                col = ZCOL[z["surface"]]
                if z["surface"] == "gravel":
                    p1 = e0 + nl[i] * side * z["startM"]
                    p2 = e0 + nl[i] * side * (z["startM"] + z["depthM"])
                elif z["surface"] == "tarmac":
                    p1, p2 = e0, e0 + nl[i] * side * z["tarmacM"]
                else:
                    p1, p2 = e0, e0 + nl[i] * side * 3.0
                dr.line([px(*p1), px(*p2)], fill=col, width=max(1, int(STEP_M / rs)))
                if z["offsetM"] is not None and BCOL.get(z["barrier"]):
                    q = e0 + nl[i] * side * z["offsetM"]
                    c = BCOL[z["barrier"]]
                    rr_ = max(1, int(0.8 / rs))
                    dr.ellipse([px(*q)[0] - rr_, px(*q)[1] - rr_, px(*q)[0] + rr_, px(*q)[1] + rr_], fill=c,
                               outline=(255, 0, 255) if z["fence"] else c)
        xs, ys = px(P[:, 0], P[:, 1])
        dr.line(list(zip(xs, ys)), fill=(80, 140, 255), width=1)
    written = []
    allxy = np.vstack([rr["P"] for rr in res["routes"]])
    lo, hi = allxy.min(0) - 60, allxy.max(0) + 60
    crop = img.crop((int(px(lo[0], hi[1])[0]), int(px(lo[0], hi[1])[1]), int(px(hi[0], lo[1])[0]), int(px(hi[0], lo[1])[1])))
    big = crop.copy()
    big.thumbnail((2000, 2000))
    p = out / "runoff-overview.png"
    big.save(p)
    written.append(p)
    for name, e, n in zooms:
        c = img.crop((int(px(e - 120, n + 120)[0]), int(px(e - 120, n + 120)[1]), int(px(e + 120, n - 120)[0]), int(px(e + 120, n - 120)[1])))
        c = c.resize((c.size[0] * 3, c.size[1] * 3), Image.NEAREST)
        ImageDraw.Draw(c).text((6, 6), f"{D['slug']} {name}: gravel yellow, tarmac grey, grass green; barrier dots: "
                                       "armco blue, tyres orange, concrete grey, bank brown, magenta ring = fence", fill=(255, 255, 0))
        p = out / f"runoff-{''.join(ch for ch in name.lower() if ch.isalnum())}.png"
        c.save(p)
        written.append(p)
    # vegetation: cover raster + woods + trees
    cov = veg["cover"]
    vx0, vy0, vres = veg["coverFrame"]
    vimg = np.stack([g, g, g], -1) * 120 + 60
    vimg = Image.fromarray(np.flipud(vimg).astype(np.uint8))
    dv = ImageDraw.Draw(vimg)
    for w in veg["woods"]:
        pts = [px(x, y) for x, y in w["points"]]
        dv.polygon(pts, outline=(0, 255, 0))
    for x, y, h in veg["trees"]:
        q = px(x, y)
        rr_ = max(1.0, h / 4.0 / rs)
        dv.ellipse([q[0] - rr_, q[1] - rr_, q[0] + rr_, q[1] + rr_], outline=(255, 220, 0))
    for rr in res["routes"]:
        xs, ys = px(rr["P"][:, 0], rr["P"][:, 1])
        dv.line(list(zip(xs, ys)), fill=(80, 140, 255), width=2)
    vimg.thumbnail((2000, 2000))
    p = out / "vegetation-overview.png"
    vimg.save(p)
    written.append(p)
    return written


def zoom_points(D: dict, names: list[str]) -> list[tuple[str, float, float]]:
    imp = D["spec"]["sketch"]["imported"]
    r = D["routes"][0]
    out = []
    for c in imp.get("corners", []):
        if names and c["name"] not in names:
            continue
        k = int(np.argmin(np.abs(r["s_local"] - c["sM"])))
        out.append((c["name"], float(r["xy"][k, 0]), float(r["xy"][k, 1])))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--plots", action="store_true")
    ap.add_argument("--zoom", nargs="*", default=[], help="corner names to zoom on (default: every corner)")
    a = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    D = load(a.slug)
    res = measure(D, log)
    veg = vegetation(D, res["osm"], log)
    write(D, res, veg, log)
    if a.plots:
        for p in plots(D, res, veg, log, zoom_points(D, a.zoom)):
            log(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
