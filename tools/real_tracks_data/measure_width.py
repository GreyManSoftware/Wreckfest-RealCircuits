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
2. Walking out from the line, a side's edge is the first place where the surface stops being road for at least
   1 m: intensity above a threshold halfway between the circuit's road and verge levels (per flight line), or a
   step off the road plane over 0.25 m (banks, walls), or objects (tyre walls, barriers, trees, cars).
3. Confidence per section: the road/verge contrast, the point count, whether both edges were found by the
   surface change (an edge at the window limit is run-off tarmac or a junction), objects over the road
   (tree cover), and agreement with the neighbouring sections.
4. Per side: outliers against a 35 m running median are rejected, low-confidence sections (and those near a
   junction with another route) are filled from their neighbours or the circuit's median, then smoothed
   (Gaussian sigma 12 m, about a 40 m window) and clamped to 3.0-9.0 m per side and 7-18 m in total.

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
CORRIDOR_M = MAX_D_M + 4.0
SMOOTH_SIGMA_M = 12.0
MIN_HALF_M, MAX_HALF_M = 3.0, 9.0
MIN_WIDTH_M, MAX_WIDTH_M = 7.0, 18.0
OUT_DIR = ROOT / "tools" / "real_tracks" / "widths"


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
                dz[sec, bi] = float(np.median(res[gm]))
    return {"score": sc, "dz": dz, "obj": obj, "cnt": cnt}


def find_edges(prof: dict, region: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per section: left and right half-widths, whether each edge was a real surface change, and a contrast."""
    sc, dz, obj = prof["score"], prof["dz"], prof["obj"]
    ns, nb = sc.shape
    centre = nb // 2  # bin index of d = 0 .. +0.5
    # fill single empty bins from neighbours along the section and across sections
    fill = np.where(np.isnan(sc), ndimage.generic_filter(np.nan_to_num(sc, nan=np.nan), np.nanmedian, size=(3, 3),
                                                          mode="nearest"), sc)
    sm = ndimage.uniform_filter1d(np.nan_to_num(fill, nan=0.5), 2, axis=1)
    thr = 0.5
    hw = np.full((ns, 2), np.nan)
    real = np.zeros((ns, 2), dtype=bool)
    contrast = np.full(ns, np.nan)
    for i in range(ns):
        row = sm[i]
        for side, rng in ((0, range(centre, nb)), (1, range(centre - 1, -1, -1))):  # 0: +d (left), 1: -d (right)
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
                hw[i, side] = max(edge, BIN_M)
                real[i, side] = True
        inside = row[max(centre - 4, 0):centre + 4]
        outside = np.concatenate([row[:max(centre - int(hw[i, 1] / BIN_M) - 2, 0)][-6:],
                                  row[min(centre + int(hw[i, 0] / BIN_M) + 2, nb):][:6]])
        if outside.size:
            contrast[i] = float(np.median(outside) - np.median(inside))
    objroad = np.array([float(np.nanmean(obj[i, centre - 6:centre + 6])) for i in range(ns)])
    npts = prof["cnt"][:, centre - 12:centre + 12].sum(1)
    return hw, real, contrast, objroad, npts


def running_median(v: np.ndarray, k: int, closed: bool) -> np.ndarray:
    return ndimage.generic_filter(v, np.nanmedian, size=k, mode="wrap" if closed else "nearest")


def smooth_side(h: np.ndarray, conf: np.ndarray, closed: bool, fallback: float) -> np.ndarray:
    good = conf >= 0.5
    v = np.where(good, h, np.nan)
    if not np.isfinite(v).any():
        return np.full_like(h, fallback)
    n = len(v)
    idx = np.arange(n)
    if closed:
        ii = np.concatenate([idx[good] - n, idx[good], idx[good] + n])
        vv = np.tile(v[good], 3)
    else:
        ii, vv = idx[good], v[good]
    filled = np.interp(idx, ii, vv)
    # gaps longer than 60 m relax towards the circuit's median half-width
    gap = ~good
    lab, nlab = ndimage.label(gap)
    for g in range(1, nlab + 1):
        m = lab == g
        if m.sum() * STEP_M > 60.0:
            filled[m] = 0.5 * filled[m] + 0.5 * fallback
    out = ndimage.gaussian_filter1d(filled, SMOOTH_SIGMA_M / STEP_M, mode="wrap" if closed else "nearest")
    return np.clip(out, MIN_HALF_M, MAX_HALF_M)


def measure(slug: str, workers: int, log) -> dict:
    region = REGION.get(slug, "england")
    routes, fr, spec = load_routes(slug)
    pts = corridor_points(slug, routes, workers, log)
    rels = assign(routes, pts)
    calibrate(pts, rels)
    score = surface_score(pts, region)
    result = {"slug": slug, "routes": []}
    for r, rel in zip(routes, rels):
        sec_xy, sec_s = resample(r["xy"], STEP_M, r["closed"])
        q = np.vstack([r["xy"], r["xy"][:1]]) if r["closed"] else r["xy"]
        length = float(np.hypot(*np.diff(q, axis=0).T).sum())
        prof = section_profiles(pts, rel, score, r["closed"], length)
        ns = min(len(sec_xy), prof["score"].shape[0])
        prof = {k: v[:ns] for k, v in prof.items()}
        hw, real, contrast, objroad, npts = find_edges(prof, region)
        result["routes"].append({"id": r["id"], "name": r["name"], "closed": r["closed"], "xy": sec_xy[:ns],
                                 "s": sec_s[:ns], "nl": normals(sec_xy[:ns], r["closed"]), "hw_raw": hw,
                                 "real": real, "contrast": contrast, "objroad": objroad, "npts": npts, "prof": prof})
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
        if len(ends):
            dj = cKDTree(ends).query(rr["xy"])[0]
            near_j = dj < 40.0
            conf[near_j] *= 0.3
            rr["junction"] = near_j
        else:
            rr["junction"] = np.zeros(ns, dtype=bool)
        rr["conf"] = conf
        fb = circuit_median / 2.0
        hl = smooth_side(hw[:, 0], conf[:, 0], closed, fb)
        hr = smooth_side(hw[:, 1], conf[:, 1], closed, fb)
        tot = hl + hr
        scale = np.clip(tot, MIN_WIDTH_M, MAX_WIDTH_M) / np.maximum(tot, 1e-6)
        rr["hl"], rr["hr"] = hl * scale, hr * scale
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--workers", type=int, default=4, help="processes for cropping the tiles (at most 8)")
    ap.add_argument("--plots", action="store_true", help="write width plots and overlays to .local/audit/<slug>/")
    a = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    result = measure(a.slug, min(a.workers, 8), log)
    for rr in result["routes"]:
        log(f"{a.slug} {rr['name']}: {summary(rr)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
