"""Measure a real circuit's tarmac width along every route from open LIDAR (and, in Navarre, the LIDAR's RGBI).

    <venv>\\Scripts\\python tools/real_tracks_data/measure_width.py cadwellpark [--plots]

Needs numpy, scipy, laspy[lazrs] (and matplotlib + Pillow for --plots) in a venv on E:. Point clouds come from
fetch_pointcloud.py (E:\\Source\\wreckfest\\.local\\realtracks\\<slug>\\pointcloud\\); the committed sample
(geom/tests/samples/<slug>.json) and its import report (the local frame) give the routes.

Method (every 5 m along each route, a cross-section +-16 m wide and 6 m long):

1. The points in the section are binned every 0.5 m across the road. Per bin: the median intensity of single and
   last returns (asphalt reads markedly darker than grass in the near-infrared), the fraction of points that are
   single returns, the fraction classified as vegetation/buildings/unclassified objects, and the height off the
   road's cross plane (a robust line through the inner 3 m). In Navarre the RGBI (from the joint camera) adds a
   brightness / NDVI term: asphalt is dark and not green, the dry ground beside it pale.
2. Walking out from the darkest point within 5 m of the line (the built line can sit metres off the tarmac's
   middle), a side's edge is the first place where the surface stops being road for at least 1 m: the score
   above a threshold halfway between the local road and verge levels, or a step off the road plane over
   0.25 m (banks, walls), or objects (tyre walls, barriers, trees, cars). A painted line with 2 m or more of
   paving beyond it (pit lanes, hatched merges, aprons) is the edge.
3. Confidence per section: the road/verge contrast, the point count, whether both edges were found (an edge
   at the window limit is run-off tarmac or a junction), objects over the road (tree cover), no road near the
   line, agreement with the neighbouring sections (35 m and 155 m running medians), and junction zones (40 m
   round a branch end). UNMEASURED lists stretches the survey can't see (checked on the overlays).
4. The width and the line's offset from the tarmac's middle are filled where confidence is low (from the
   neighbours, relaxing to the circuit's median width / zero offset in long gaps), smoothed (width sigma 12 m,
   offset sigma 40 m), clamped (7-18 m) and rate-limited (0.4 m per 5 m for the width, 0.1 m for the offset).

Output: tools/real_tracks/widths/<slug>.json (derived, small, committed): per route the section centres in the
recipe's grid with unit normals and left/right half-widths (left = the driver's left), confidences and summary
statistics; the importer maps them onto its own fitted line by position. Plots and overlays go to
E:\\Source\\wreckfest\\.local\\audit\\<slug>\\width-*.png.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_pointcloud import LOCAL, REGION, ROOT, report_path  # noqa: E402

STEP_M = 5.0          # section spacing along the route
HALF_LEN_M = 3.0      # section half length along the route
MAX_D_M = 16.0        # cross-section half width
BIN_M = 0.5
RIDGE_MIN = 0.12      # a painted line: the mean score this much above the bins 1 m either side
CORRIDOR_M = MAX_D_M + 4.0
SMOOTH_SIGMA_M = 12.0
MIN_WIDTH_M, MAX_WIDTH_M = 7.0, 18.0
MAX_WIDTH_STEP_M = 0.4   # per section (5 m)
SEARCH_M = 5.0           # how far off the line the tarmac's darkest point may be
OFFSET_SIGMA_M = 40.0     # the line's offset from the tarmac's middle
MAX_OFFSET_M = 5.0
MAX_OFFSET_STEP_M = 0.1   # per section (5 m)
OUT_DIR = ROOT / "tools" / "real_tracks" / "widths"
SOURCES = {
    "england": {"source": "Environment Agency National LIDAR Programme point cloud (LAZ, 5 km tiles)",
                "attribution": "Contains Environment Agency information (c) Environment Agency and/or database right",
                "licence": "Open Government Licence v3.0"},
    "navarra": {"source": "Gobierno de Navarra LiDAR 2024 point cloud with RGBI (LAZ, 1 km tiles, filescartografia.navarra.es)",
                "attribution": "Servicio proporcionado por el Gobierno de Navarra (IDENA), LiDAR 2024",
                "licence": "CC BY 4.0"},
}
# stretches the survey can't measure (checked on the overlays), filled from the circuit's median; s in grid m
UNMEASURED = {
    "navarra": [{"route": "main", "fromS": 2640.0, "toS": 3420.0,
                 "reason": "the 2024 extension (Turns 11-14) was unpaved or half-paved in the 2024 LIDAR flight"}],
}


# ------------------------------------------------------------------------------------------------ routes
def load_routes(slug: str) -> tuple[list[dict], dict, dict]:
    spec = json.loads((ROOT / "geom" / "tests" / "samples" / f"{slug}.json").read_text(encoding="utf-8"))
    fr = json.loads(report_path(slug).read_text(encoding="utf-8"))["frame"]
    imp = spec["sketch"]["imported"]
    out = [{"id": "main", "name": imp.get("mainLayoutName", "Main"), "closed": True, "c": imp["centreline"]}]
    out += [{"id": a["id"], "name": a["name"], "closed": False, "c": a["centreline"]} for a in imp.get("altLayouts", [])]
    off = np.array([fr["centreE"], fr["centreN"]])
    for r in out:
        r["xy"] = np.array([[q["x"], q["y"]] for q in r["c"]]) * fr["scale"] + off
        r["z"] = np.array([q["z"] for q in r["c"]]) + imp["datumM"]
    return out, fr, spec


def resample(xy: np.ndarray, step: float, closed: bool) -> tuple[np.ndarray, np.ndarray]:
    q = np.vstack([xy, xy[:1]]) if closed else xy
    s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(q, axis=0).T))])
    n = int(s[-1] // step) if closed else int(s[-1] // step) + 1
    t = np.arange(n) * step
    return np.column_stack([np.interp(t, s, q[:, 0]), np.interp(t, s, q[:, 1])]), t


def normals(xy: np.ndarray, closed: bool) -> np.ndarray:
    if closed:
        d = np.roll(xy, -1, axis=0) - np.roll(xy, 1, axis=0)
    else:
        d = np.gradient(xy, axis=0)
    d /= np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)[:, None]
    return np.column_stack([-d[:, 1], d[:, 0]])  # left of the driving direction


# ------------------------------------------------------------------------------------------------ points
FIELDS = ("x", "y", "z", "intensity", "return_number", "number_of_returns", "classification", "point_source_id")


def _crop_tile(args: tuple[str, np.ndarray, float]) -> dict:
    import laspy  # noqa: PLC0415

    path, dense, corridor = args
    tree = cKDTree(dense)
    parts: dict[str, list] = {k: [] for k in FIELDS + ("red", "green", "blue", "nir")}
    with laspy.open(path) as f:
        h = f.header
        has_rgb = "red" in h.point_format.dimension_names
        has_nir = "nir" in h.point_format.dimension_names
        x0, y0, x1, y1 = h.mins[0], h.mins[1], h.maxs[0], h.maxs[1]
        sel = (dense[:, 0] > x0 - corridor) & (dense[:, 0] < x1 + corridor) & (dense[:, 1] > y0 - corridor) & (dense[:, 1] < y1 + corridor)
        if not sel.any():
            return {}
        for pts in f.chunk_iterator(4_000_000):
            x, y = np.asarray(pts.x), np.asarray(pts.y)
            d, _ = tree.query(np.column_stack([x, y]), distance_upper_bound=corridor)
            m = np.isfinite(d)
            if not m.any():
                continue
            for k in FIELDS:
                v = np.asarray(getattr(pts, k) if k in ("x", "y", "z") else pts[k])
                parts[k].append(v[m])
            if has_rgb:
                for k in ("red", "green", "blue"):
                    parts[k].append(np.asarray(pts[k])[m])
            if has_nir:
                parts["nir"].append(np.asarray(pts["nir"])[m])
    return {k: np.concatenate(v) for k, v in parts.items() if v}


def corridor_points(slug: str, routes: list[dict], workers: int, log) -> dict:
    if REGION.get(slug) == "wales":
        raise SystemExit(f"{slug}: no open point cloud in Wales (DataMapWales has the 2020-22 DTM/DSM only; DTM "
                         "roughness was tried and shows no road edge): the recipe keeps its published width")
    pc = LOCAL / "realtracks" / slug / "pointcloud"
    cache = pc / "corridor.npz"
    tiles = sorted(pc.glob("*.laz"))
    if cache.exists() and cache.stat().st_mtime > max(t.stat().st_mtime for t in tiles):
        return dict(np.load(cache))
    dense = np.vstack([resample(r["xy"], 1.0, r["closed"])[0] for r in routes])
    log(f"{slug}: cropping {len(tiles)} tiles to the road corridor ({workers} workers) ...")
    with ProcessPoolExecutor(max_workers=max(1, min(workers, len(tiles)))) as ex:
        res = [r for r in ex.map(_crop_tile, [(str(t), dense, CORRIDOR_M) for t in tiles]) if r]
    keys = set.intersection(*[set(r) for r in res])
    pts = {k: np.concatenate([r[k] for r in res]) for k in keys}
    for k in ("x", "y", "z"):
        pts[k] = pts[k].astype(np.float64)
    np.savez(cache, **pts)
    log(f"{slug}: {len(pts['x']):,} points in the corridor")
    return pts


# ------------------------------------------------------------------------------------------------ measure
def surface_score(pts: dict, region: str) -> np.ndarray:
    """Per point, a 'not asphalt' score: intensity normalised per flight line (0 = the road, 1 = the verge),
    plus in Navarre a brightness / greenness term from the RGBI."""
    inten = pts["intensity"].astype(np.float64)
    score = np.full(len(inten), np.nan)
    src = pts["point_source_id"]
    road_ref, verge_ref = pts["_road"], pts["_verge"]
    for sid in np.unique(src):
        m = src == sid
        r_, v_ = road_ref.get(int(sid)), verge_ref.get(int(sid))
        if r_ is None or v_ is None or v_ - r_ < 1e-6:
            r_, v_ = road_ref["all"], verge_ref["all"]
        score[m] = (inten[m] - r_) / max(v_ - r_, 1e-6)
    if region == "navarra" and "red" in pts:
        rgb = np.column_stack([pts["red"], pts["green"], pts["blue"]]).astype(np.float64)
        bright = rgb.mean(1)
        rb, vb = pts["_road_bright"], pts["_verge_bright"]
        sb = (bright - rb) / max(vb - rb, 1e-6)
        score = np.where(np.isfinite(score), 0.5 * score + 0.5 * sb, sb)
    return score


def assign(routes: list[dict], pts: dict) -> list[dict]:
    """Every corridor point's along/across coordinates relative to each route's 0.5 m resample."""
    xy = np.column_stack([pts["x"], pts["y"]])
    out = []
    for r in routes:
        dense, s = resample(r["xy"], 0.5, r["closed"])
        nl = normals(dense, r["closed"])
        tree = cKDTree(dense)
        d, k = tree.query(xy, distance_upper_bound=MAX_D_M + 1.0)
        m = np.isfinite(d)
        k = k[m]
        rel = xy[m] - dense[k]
        across = (rel * nl[k]).sum(1)
        tang = np.column_stack([nl[k, 1], -nl[k, 0]])
        along = s[k] + (rel * tang).sum(1)
        out.append({"idx": np.nonzero(m)[0], "s": along, "d": across, "dense": dense, "ds": s, "nl": nl})
    return out


def calibrate(pts: dict, routes_rel: list[dict]) -> None:
    """Road and verge intensity levels per flight line: within 2 m of the main line vs 13-16 m out, single and
    last returns only."""
    rel = routes_rel[0]
    inten = pts["intensity"].astype(np.float64)
    last = pts["return_number"] == pts["number_of_returns"]
    near_ = rel["idx"][(np.abs(rel["d"]) < 2.0)]
    far_ = rel["idx"][(np.abs(rel["d"]) > 13.0)]
    near_ = near_[last[near_]]
    far_ = far_[last[far_] & (pts["number_of_returns"][far_] == 1)]
    road, verge = {"all": float(np.median(inten[near_]))}, {"all": float(np.median(inten[far_]))}
    for sid in np.unique(pts["point_source_id"]):
        a = near_[pts["point_source_id"][near_] == sid]
        b = far_[pts["point_source_id"][far_] == sid]
        if len(a) > 2000 and len(b) > 2000:
            road[int(sid)], verge[int(sid)] = float(np.median(inten[a])), float(np.median(inten[b]))
    pts["_road"], pts["_verge"] = road, verge
    if "red" in pts:
        bright = np.column_stack([pts["red"], pts["green"], pts["blue"]]).astype(np.float64).mean(1)
        pts["_road_bright"], pts["_verge_bright"] = float(np.median(bright[near_])), float(np.median(bright[far_]))


def section_profiles(pts: dict, rel: dict, score: np.ndarray, closed: bool, length: float) -> dict:
    """Per section (every STEP_M), per 0.5 m bin across: median score, height off the road plane, object
    fraction and the point count."""
    nb = int(2 * MAX_D_M / BIN_M)
    ns = int(length // STEP_M) if closed else int(length // STEP_M) + 1
    sc = np.full((ns, nb), np.nan)
    mean = np.full((ns, nb), np.nan)
    dz = np.full((ns, nb), np.nan)
    obj = np.zeros((ns, nb))
    cnt = np.zeros((ns, nb), dtype=np.int32)
    s, d, idx = rel["s"], rel["d"], rel["idx"]
    cls = pts["classification"][idx]
    last = (pts["return_number"] == pts["number_of_returns"])[idx]
    z = pts["z"][idx]
    sv = score[idx]
    # section index of each point: nearest section centre within HALF_LEN_M
    k = np.round(s / STEP_M).astype(np.int64)
    if closed:
        k %= ns
    ok = (np.abs(s - np.round(s / STEP_M) * STEP_M) <= HALF_LEN_M) & (k >= 0) & (k < ns) & (np.abs(d) < MAX_D_M)
    b = np.clip(((d + MAX_D_M) / BIN_M).astype(np.int64), 0, nb - 1)
    order = np.lexsort((b[ok], k[ok]))
    kk, bb = k[ok][order], b[ok][order]
    sv_, z_, d_, cls_, last_ = sv[ok][order], z[ok][order], d[ok][order], cls[ok][order], last[ok][order]
    starts = np.nonzero(np.r_[True, kk[1:] != kk[:-1]])[0]
    ends = np.r_[starts[1:], len(kk)]
    for a0, a1 in zip(starts, ends):
        sec = kk[a0]
        g = slice(a0, a1)
        zz, dd, bins, cc, ll, ss = z_[g], d_[g], bb[g], cls_[g], last_[g], sv_[g]
        ground = (cc == 2) | ((cc <= 1) & ll)
        core = ground & (np.abs(dd) < 1.5)
        if core.sum() < 3:
            continue
        # robust cross plane through the inner 3 m (tilt from the wider inner 5 m)
        inner = ground & (np.abs(dd) < 2.5)
        A = np.column_stack([dd[inner], np.ones(inner.sum())])
        coef, *_ = np.linalg.lstsq(A, zz[inner], rcond=None)
        res = zz - (coef[0] * dd + coef[1])
        isobj = np.isin(cc, (3, 4, 5, 6)) | (res > 0.4)
        for bi in np.unique(bins):
            m = bins == bi
            cnt[sec, bi] = int(m.sum())
            obj[sec, bi] = float(isobj[m].mean())
            gm = m & ground & ll
            if gm.any():
                sc[sec, bi] = float(np.nanmedian(ss[gm]))
                mean[sec, bi] = float(np.nanmean(np.clip(ss[gm], -0.5, 2.0)))
                dz[sec, bi] = float(np.median(res[gm]))
    return {"score": sc, "mean": mean, "dz": dz, "obj": obj, "cnt": cnt}


def find_edges(prof: dict, region: str, closed: bool) -> tuple:
    """Per section: left and right half-widths, whether each edge was a real surface change, and a contrast."""
    sc, dz, obj = prof["score"], prof["dz"], prof["obj"]
    ns, nb = sc.shape
    centre = nb // 2  # bin index of d = 0 .. +0.5
    # fill single empty bins from neighbours along the section and across sections
    fill = np.where(np.isnan(sc), ndimage.generic_filter(np.nan_to_num(sc, nan=np.nan), np.nanmedian, size=(3, 3),
                                                          mode="nearest"), sc)
    sm = ndimage.uniform_filter1d(np.nan_to_num(fill, nan=0.5), 2, axis=1)
    # local threshold halfway between this stretch's road and verge levels (surfaces differ: Navarra's T4 is a
    # paler asphalt), within 0.35-0.75; the circuit's 0.5 where there's no clear verge
    md = "wrap" if closed else "nearest"
    road_l = ndimage.uniform_filter1d(np.median(sm[:, centre - 3:centre + 3], axis=1), 5, mode=md)
    verge_l = ndimage.uniform_filter1d(np.percentile(sm, 80, axis=1), 5, mode=md)
    thr_l = np.where(verge_l - road_l > 0.4, np.clip(0.5 * (road_l + verge_l), 0.35, 0.75), 0.5)
    hw = np.full((ns, 2), np.nan)
    real = np.zeros((ns, 2), dtype=bool)
    lined = np.zeros((ns, 2), dtype=bool)
    ridge = ndimage.uniform_filter1d(np.nan_to_num(prof["mean"], nan=0.5), 5, axis=0, mode=md)
    # the walk starts at the darkest point within SEARCH_M of the line (the built line can be off the tarmac's
    # middle by metres: OSM and the DTM registration), smoothed over 25 m along and 1.5 m across
    rs = ndimage.uniform_filter(sm, size=(5, 3), mode=(md, "nearest"))
    k0 = int(SEARCH_M / BIN_M)
    c0s = centre - k0 + np.argmin(rs[:, centre - k0:centre + k0], axis=1)
    contrast = np.full(ns, np.nan)
    for i in range(ns):
        row = sm[i]
        thr = thr_l[i]
        c0 = int(c0s[i])
        for side, rng in ((0, range(c0, nb)), (1, range(c0 - 1, -1, -1))):  # 0: +d (left), 1: -d (right)
            edge = None
            run = 0
            for j in rng:
                bad = row[j] > thr or (np.isfinite(dz[i, j]) and abs(dz[i, j]) > 0.25) or obj[i, j] > 0.5
                run = run + 1 if bad else 0
                if run >= 2:  # 1 m of not-road
                    jj = j - 1 if side == 0 else j + 1  # first bad bin
                    edge = (jj - centre) * BIN_M if side == 0 else (centre - jj) * BIN_M
                    break
            if edge is None:
                hw[i, side] = MAX_D_M
            else:
                hw[i, side] = edge
                real[i, side] = True
            # a painted line with 2 m or more of paving beyond it (pit lanes and their hatched merges, aprons,
            # run-off): the line is the edge. Lines show as a ridge in the mean score, smoothed over 25 m.
            reach = int(round(hw[i, side] / BIN_M)) + (centre - c0 if side == 0 else c0 - centre)  # bins from c0
            for t in range(int(3.5 / BIN_M), min(reach, int((MAX_D_M - 1.0) / BIN_M)) - int(2.0 / BIN_M)):
                j = c0 + t if side == 0 else c0 - 1 - t
                a_, b_ = (j - 2, j + 2)
                if a_ < 0 or b_ >= nb:
                    break
                if ridge[i, j] - min(ridge[i, a_], ridge[i, b_]) > RIDGE_MIN and ridge[i, j] > 0.15:
                    hw[i, side] = (j + 1 - centre) * BIN_M if side == 0 else (centre - j) * BIN_M
                    real[i, side] = True
                    lined[i, side] = True
                    break
        jl, jr = centre + int(round(hw[i, 0] / BIN_M)), centre - int(round(hw[i, 1] / BIN_M))
        inside = row[max(c0 - 4, 0):c0 + 4]
        outside = np.concatenate([row[:max(jr - 2, 0)][-6:], row[min(jl + 2, nb):][:6]])
        if outside.size:
            contrast[i] = float(np.median(outside) - np.median(inside))
    objroad = np.array([float(np.nanmean(obj[i, max(c0s[i] - 6, 0):c0s[i] + 6])) for i in range(ns)])
    # no road near the line (not paved when surveyed: Navarra's 2024 extension; deep shade): no measurement
    notroad = rs[np.arange(ns), c0s] > 0.35
    real[notroad] = False
    npts = prof["cnt"][:, centre - 12:centre + 12].sum(1)
    return hw, real, contrast, objroad, npts, lined


def running_median(v: np.ndarray, k: int, closed: bool) -> np.ndarray:
    return ndimage.generic_filter(v, np.nanmedian, size=k, mode="wrap" if closed else "nearest")


def smooth_series(v: np.ndarray, good: np.ndarray, closed: bool, fallback: float, ramp: tuple[float, float],
                  sigma_m: float, lo: float, hi: float, step: float) -> np.ndarray:
    """Fill the sections that aren't ``good`` from their measured neighbours, relaxing towards ``fallback``
    inside gaps (from ramp[0] to ramp[1] metres in from the gap's ends), smooth (Gaussian), clamp and limit the
    change per section to ``step``."""
    n = len(v)
    if not good.any():
        return np.full(n, float(np.clip(fallback, lo, hi)))
    idx = np.arange(n)
    if closed:
        ii = np.concatenate([idx[good] - n, idx[good], idx[good] + n])
        vv = np.tile(v[good], 3)
    else:
        ii, vv = idx[good], v[good]
    filled = np.interp(idx, ii, vv)
    lab, nlab = ndimage.label(~good)
    for g in range(1, nlab + 1):
        ii_ = np.nonzero(lab == g)[0]
        touches_end = not closed and (ii_[0] == 0 or ii_[-1] == n - 1)
        e = np.minimum(ii_ - ii_[0] + 1, ii_[-1] - ii_ + 1) * STEP_M
        if touches_end:
            e = (ii_[-1] - ii_ + 1 if ii_[0] == 0 else ii_ - ii_[0] + 1) * STEP_M
        w_ = np.clip((e - ramp[0]) / max(ramp[1] - ramp[0], 1e-6), 0.0, 1.0)
        filled[ii_] = (1.0 - w_) * filled[ii_] + w_ * fallback
    out = ndimage.gaussian_filter1d(filled, sigma_m / STEP_M, mode="wrap" if closed else "nearest")
    return rate_limit(np.clip(out, lo, hi), step, closed)


def rate_limit(v: np.ndarray, step: float, closed: bool) -> np.ndarray:
    """The mean of the upper and lower envelopes that change by at most ``step`` per section: no faster
    change anywhere (both are step-Lipschitz, so is their mean), a narrow dip and a wide spike shrink alike."""
    n = len(v)
    i = np.arange(n)
    d = np.abs(i[:, None] - i[None, :]).astype(np.float64)
    if closed:
        d = np.minimum(d, n - d)
    up = (v[None, :] - step * d).max(1)
    lo = (v[None, :] + step * d).min(1)
    return 0.5 * (up + lo)


def measure(slug: str, workers: int, log) -> dict:
    region = REGION.get(slug, "england")
    routes, fr, spec = load_routes(slug)
    pts = corridor_points(slug, routes, workers, log)
    rels = assign(routes, pts)
    calibrate(pts, rels)
    score = surface_score(pts, region)
    pts["_score"] = score
    result = {"slug": slug, "routes": [], "_pts": pts, "_spec": spec, "_frame": fr}
    for r, rel in zip(routes, rels):
        sec_xy, sec_s = resample(r["xy"], STEP_M, r["closed"])
        q = np.vstack([r["xy"], r["xy"][:1]]) if r["closed"] else r["xy"]
        length = float(np.hypot(*np.diff(q, axis=0).T).sum())
        prof = section_profiles(pts, rel, score, r["closed"], length)
        ns = min(len(sec_xy), prof["score"].shape[0])
        prof = {k: v[:ns] for k, v in prof.items()}
        hw, real, contrast, objroad, npts, lined = find_edges(prof, region, r["closed"])
        result["routes"].append({"id": r["id"], "name": r["name"], "closed": r["closed"], "xy": sec_xy[:ns],
                                 "s": sec_s[:ns], "nl": normals(sec_xy[:ns], r["closed"]), "hw_raw": hw,
                                 "real": real, "lined": lined, "contrast": contrast, "objroad": objroad, "npts": npts, "prof": prof})
    finish(result, routes)
    return result


def finish(result: dict, routes: list[dict]) -> None:
    """Confidence, outlier rejection, junction masking, smoothing and clamps; circuit statistics."""
    # junction zones: within 40 m of a branch end (on every route)
    ends = [r["xy"][[0, -1]] for r in routes if not r["closed"]]
    ends = np.vstack(ends) if ends else np.zeros((0, 2))
    allraw = []
    for rr in result["routes"]:
        hw, real = rr["hw_raw"], rr["real"]
        both = real.all(1)
        allraw.append(hw[both].sum(1))
    allraw = np.concatenate(allraw) if allraw else np.array([10.0])
    circuit_median = float(np.median(allraw)) if allraw.size else 10.0
    for rr in result["routes"]:
        hw, real, closed = rr["hw_raw"], rr["real"], rr["closed"]
        ns = len(hw)
        conf = np.ones((ns, 2))
        conf *= np.clip(rr["contrast"] / 0.6, 0.0, 1.0)[:, None] ** 0.5 if np.isfinite(rr["contrast"]).any() else 1.0
        conf[~real] = 0.0
        conf *= np.clip(1.0 - rr["objroad"] * 2.0, 0.0, 1.0)[:, None]
        conf *= np.clip(rr["npts"] / 60.0, 0.0, 1.0)[:, None]
        conf = np.nan_to_num(conf)
        for side in (0, 1):
            med = running_median(np.where(real[:, side], hw[:, side], np.nan), 7, closed)
            dev = np.abs(hw[:, side] - med)
            conf[dev > 1.5, side] *= 0.2
            # adjacent paved areas (pit lanes, aprons, run-off): a side far wider than its 155 m running median
            long_med = running_median(np.where(real[:, side], hw[:, side], np.nan), 31, closed)
            conf[hw[:, side] > long_med + 2.0, side] *= 0.1
        if len(ends):
            dj = cKDTree(ends).query(rr["xy"])[0]
            near_j = dj < 40.0
            conf[near_j] *= 0.3
            rr["junction"] = near_j
        else:
            rr["junction"] = np.zeros(ns, dtype=bool)
        for ov in UNMEASURED.get(result["slug"], []):
            if ov["route"] == rr["id"]:
                conf[(rr["s"] >= ov["fromS"]) & (rr["s"] <= ov["toS"])] = 0.0
        rr["conf"] = conf
        # the tarmac's width and the line's offset from its middle, smoothed separately: the width over ~40 m,
        # the offset over ~130 m (it moves the driven line, which must stay free of wobble on straights)
        good = conf.min(1) >= 0.5
        width = hw.sum(1)
        offset = 0.5 * (hw[:, 0] - hw[:, 1])
        w_s = smooth_series(width, good, closed, circuit_median, (10.0, 40.0), SMOOTH_SIGMA_M, MIN_WIDTH_M,
                            MAX_WIDTH_M, MAX_WIDTH_STEP_M)
        o_s = smooth_series(offset, good, closed, 0.0, (60.0, 200.0), OFFSET_SIGMA_M, -MAX_OFFSET_M, MAX_OFFSET_M,
                            MAX_OFFSET_STEP_M)
        rr["hl"], rr["hr"] = 0.5 * w_s + o_s, 0.5 * w_s - o_s
    result["circuitRawMedianM"] = circuit_median


# ------------------------------------------------------------------------------------------------ output
def summary(rr: dict) -> dict:
    w = rr["hl"] + rr["hr"]
    c = rr["conf"].min(1)
    i_min, i_max = int(np.argmin(w)), int(np.argmax(w))
    return {"medianM": round(float(np.median(w)), 2), "minM": round(float(w.min()), 2), "maxM": round(float(w.max()), 2),
            "minAtS": round(float(rr["s"][i_min]), 0), "maxAtS": round(float(rr["s"][i_max]), 0),
            "p10M": round(float(np.percentile(w, 10)), 2), "p90M": round(float(np.percentile(w, 90)), 2),
            "confidentFraction": round(float((c >= 0.5).mean()), 3),
            "maxStepPer5mM": round(float(np.abs(np.diff(w)).max()), 3) if len(w) > 1 else 0.0}


def to_json(result: dict, meta: dict) -> dict:
    out = {**meta, "routes": []}
    for rr in result["routes"]:
        out["routes"].append({
            "id": rr["id"], "name": rr["name"], "closed": rr["closed"], "stepM": STEP_M, "summary": summary(rr),
            "sections": [{"e": round(float(x), 2), "n": round(float(y), 2), "nx": round(float(a), 4), "ny": round(float(b), 4),
                          "leftM": round(float(hl), 2), "rightM": round(float(hr), 2), "conf": round(float(min(c)), 2)}
                         for (x, y), (a, b), hl, hr, c in zip(rr["xy"], rr["nl"], rr["hl"], rr["hr"], rr["conf"])],
        })
    return out


# ------------------------------------------------------------------------------------------------ plots
ZOOMS = {  # sections worth a close look (corner labels in the sample), besides the narrowest and widest
    "cadwellpark": ["The Mountain", "Hall Bends", "Park Straight", "The Hairpin", "Coppice"],
    "oultonpark": ["Cascades", "Island Hairpin", "Lodge", "Knickerbrook", "Old Hall"],
    "brandshatch": ["Paddock Hill Bend", "Druids", "Clearways", "Hawthorns"],
    "snetterton": ["Riches", "Bombhole", "Coram", "Agostini"],
    "doningtonpark": ["Craner Curves", "Melbourne Hairpin", "Redgate", "Old Hairpin"],
    "bedfordautodrome": ["Hangar Hairpin", "Turn 11", "Turn 15"],
    "navarra": ["Turn 1", "Turn 11", "Turn 17", "Turn 3"],
    "anglesey": ["Corkscrew", "Banking", "Bus Stop", "Rocket"],
}


def intensity_raster(pts: dict, lo: np.ndarray, hi: np.ndarray, res: float, region: str) -> np.ndarray:
    """A grey image of the corridor: per-flight-line normalised intensity (or RGB in Navarre), north up."""
    nx, ny = int((hi[0] - lo[0]) / res) + 1, int((hi[1] - lo[1]) / res) + 1
    m = (pts["x"] >= lo[0]) & (pts["x"] < hi[0]) & (pts["y"] >= lo[1]) & (pts["y"] < hi[1])
    m &= pts["return_number"] == pts["number_of_returns"]
    ix = ((pts["x"][m] - lo[0]) / res).astype(np.int64)
    iy = ((hi[1] - pts["y"][m]) / res).astype(np.int64)
    flat = iy * nx + ix
    sc = np.clip(pts["_score"][m], -0.2, 1.4)
    s_ = np.bincount(flat, weights=sc, minlength=nx * ny)
    c_ = np.bincount(flat, minlength=nx * ny)
    # splat with a Gaussian the size of the point spacing, normalised by the point density
    sig = max(0.35 / res, 0.6)
    s2 = ndimage.gaussian_filter(s_.reshape(ny, nx), sig)
    c2 = ndimage.gaussian_filter(c_.reshape(ny, nx).astype(np.float64), sig)
    with np.errstate(invalid="ignore", divide="ignore"):
        img = np.where(c2 > 0.05 * c2.max() / max(1.0, (1.0 / res) ** 2) + 1e-9, s2 / c2, np.nan)
    out = np.where(np.isnan(img), 0.0, np.clip((img + 0.2) / 1.6, 0, 1) * 0.85 + 0.15)
    return (out * 255).astype(np.uint8)


def write_plots(slug: str, result: dict, pts: dict, spec: dict, fr: dict, extra_s: list[float] = ()) -> list[Path]:
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    out = LOCAL / "audit" / slug
    out.mkdir(parents=True, exist_ok=True)
    region = REGION.get(slug, "england")
    written = []
    corners = spec["sketch"]["imported"].get("corners", [])
    for rr in result["routes"]:
        s, hl, hr, hw = rr["s"], rr["hl"], rr["hr"], rr["hw_raw"]
        conf = rr["conf"].min(1)
        fig, ax = plt.subplots(2, 1, figsize=(16, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        raw = np.where(rr["real"].all(1), hw.sum(1), np.nan)
        ax[0].plot(s, raw, ".", ms=2, color="0.6", label="raw (both edges found)")
        ax[0].plot(s, hl + hr, "-", color="C0", lw=2, label="width (smoothed)")
        ax[0].plot(s, hl, "-", color="C2", lw=1, label="left half")
        ax[0].plot(s, hr, "-", color="C3", lw=1, label="right half")
        ax[0].set_ylim(0, 20)
        ax[0].set_ylabel("m")
        ax[0].grid(alpha=0.3)
        if rr["id"] == "main":
            for c in corners:
                ax[0].axvline(c["sM"] * fr["scale"], color="0.8", lw=0.8)
                ax[0].text(c["sM"] * fr["scale"], 19.5, c["name"], rotation=90, va="top", fontsize=7)
        ax[0].legend(loc="lower right", fontsize=8)
        ax[0].set_title(f"{slug} {rr['name']}: width {summary(rr)}", fontsize=9)
        ax[1].plot(s, conf, color="C1")
        ax[1].fill_between(s, 0, rr["junction"].astype(float), color="0.85", step="mid", label="junction zone")
        ax[1].set_ylabel("confidence")
        ax[1].set_xlabel("s along the route (grid m)")
        ax[1].legend(fontsize=8)
        fig.tight_layout()
        p = out / f"width-{rr['id']}.png"
        fig.savefig(p, dpi=110)
        plt.close(fig)
        written.append(p)

    from PIL import Image, ImageDraw  # noqa: PLC0415

    def overlay(lo: np.ndarray, hi: np.ndarray, res: float, name: str, label: str) -> Path:
        g = intensity_raster(pts, lo, hi, res, region)
        im = Image.fromarray(g).convert("RGB")
        dr = ImageDraw.Draw(im)
        px = lambda q: ((q[:, 0] - lo[0]) / res, (hi[1] - q[:, 1]) / res)  # noqa: E731
        lw = max(1, int(round(0.4 / res)))
        for rr in result["routes"]:
            c = rr["xy"]
            for side, col, h in ((1, (40, 220, 40), rr["hl"]), (-1, (230, 50, 50), rr["hr"])):
                e = c + side * rr["nl"] * h[:, None]
                x, y = px(e)
                dr.line(list(zip(x, y)), fill=col, width=lw)
                raw = c + side * rr["nl"] * rr["hw_raw"][:, 0 if side == 1 else 1][:, None]
                xr, yr = px(raw)
                si = 0 if side == 1 else 1
                for a_, b_, ok, ln in zip(xr, yr, rr["real"][:, si], rr["lined"][:, si]):
                    if ok:
                        dr.ellipse([a_ - lw, b_ - lw, a_ + lw, b_ + lw], outline=(0, 230, 255) if ln else (255, 200, 0))
            x, y = px(c)
            dr.line(list(zip(x, y)), fill=(80, 140, 255), width=max(1, lw // 2))
        dr.text((6, 6), label, fill=(255, 255, 0))
        p = out / f"width-{name}.png"
        im.save(p)
        return p

    allxy = np.vstack([rr["xy"] for rr in result["routes"]])
    lo, hi = allxy.min(0) - 25, allxy.max(0) + 25
    res = max(0.5, float((hi - lo).max()) / 2400)
    written.append(overlay(lo, hi, res, "overlay", f"{slug}: green = left edge, red = right edge, yellow = raw edges (cyan: at a painted line), "
                                                  f"blue = built centreline; {res:.2f} m/px"))
    main = result["routes"][0]
    w = main["hl"] + main["hr"]
    picks = []
    for c in corners:
        if c["name"] in ZOOMS.get(slug, []):
            picks.append((c["name"], c["sM"] * fr["scale"]))
    picks += [("narrowest", float(main["s"][int(np.argmin(w))])), ("widest", float(main["s"][int(np.argmax(w))]))]
    picks += [(f"s{int(v)}", float(v)) for v in extra_s]
    for name, s0 in picks:
        i = int(np.argmin(np.abs(main["s"] - s0)))
        cxy = main["xy"][i]
        lo_, hi_ = cxy - 70, cxy + 70
        k = np.abs(main["s"] - s0) < 70
        ws = w[k]
        slugname = "".join(ch for ch in name.lower() if ch.isalnum())
        written.append(overlay(lo_, hi_, 0.3, f"zoom-{slugname}",
                               f"{slug} {name}: width {ws.min():.1f}-{ws.max():.1f} m within 70 m"))
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--workers", type=int, default=4, help="processes for cropping the tiles (at most 8)")
    ap.add_argument("--plots", action="store_true", help="write width plots and overlays to .local/audit/<slug>/")
    ap.add_argument("--at", type=float, nargs="*", default=[], help="also zoom at these s on the main route (m)")
    a = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    result = measure(a.slug, min(a.workers, 8), log)
    for rr in result["routes"]:
        log(f"{a.slug} {rr['name']}: {summary(rr)}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tiles = sorted(t.name for t in (LOCAL / "realtracks" / a.slug / "pointcloud").glob("*.laz"))
    region = REGION.get(a.slug, "england")
    meta = {
        "description": f"Measured tarmac widths of {a.slug} (tools/real_tracks_data/measure_width.py; derived data). "
                       "Per route, sections every 5 m: centre (e, n) in the recipe's grid (BNG, or ETRS89 / UTM 30N "
                       "in Spain), unit left normal (nx, ny), half-widths to the left and right tarmac edges "
                       "(leftM, rightM; the built line's offset from the tarmac's middle is (leftM - rightM) / 2) "
                       "and the lower edge confidence (conf, 0-1; below 0.5 the value is filled from neighbours or "
                       "the circuit's median).",
        "source": SOURCES[region]["source"], "attribution": SOURCES[region]["attribution"],
        "licence": SOURCES[region]["licence"], "tiles": tiles,
        "method": "LIDAR intensity (and RGB in Navarre) across the road every 5 m, edges where the surface stops "
                  "being asphalt for 1 m (or a height step, objects, a painted line with paving beyond); "
                  f"width smoothed (sigma {SMOOTH_SIGMA_M:g} m, at most {MAX_WIDTH_STEP_M:g} m per 5 m, "
                  f"{MIN_WIDTH_M:g}-{MAX_WIDTH_M:g} m), the line's offset smoothed (sigma {OFFSET_SIGMA_M:g} m)",
        "circuitRawMedianM": round(result["circuitRawMedianM"], 2),
        "unmeasured": UNMEASURED.get(a.slug, []),
    }
    out = OUT_DIR / f"{a.slug}.json"
    out.write_text(json.dumps(to_json(result, meta), separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    log(f"wrote {out}")
    if a.plots:
        for p in write_plots(a.slug, result, result["_pts"], result["_spec"], result["_frame"], a.at):
            log(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
