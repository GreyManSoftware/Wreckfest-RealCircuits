"""Turn cached OSM + LIDAR downloads for a real circuit into a compact dataset.

    py tools/real_tracks_data/process_track.py tools/real_tracks_data/anglesey.json

Inputs (all cached by fetch_osm.py / fetch_dtm.py in cfg.raw_dir):
  osm_raceway.json, osm_env.json, <dtm>.npy/.json, <dtm_far>, <dtm_horizon>
Outputs in cfg.out_dir:
  <slug>.json          layouts (2 m centreline with z, camber, grade), corners,
                       joins, start/finish, stats, terrain + sea + horizon summary
  terrain_4m.npy       4 m block-mean DTM around the site (north-up, NaN = sea/no data)
  environment.json     OSM landuse / natural / building / wall summary
  preview_<layout>.png quick-look plots

Frames: BNG (EPSG:27700) metres; "local" = BNG minus frame.centre, x east, y north,
z metres above Ordnance Datum Newlyn. Lengths are ground metres (grid / scale factor).
Conventions: s = 0 at the start/finish line, increasing in the racing direction.
curvature > 0 = left turn. crossfall = (z_left - z_right) / (2 * offset) over the
track edges; bank = crossfall signed so that positive = inside of the turn lower.
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
from scipy import interpolate, ndimage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from osgb import wgs84_to_bng  # noqa: E402

STEP = 2.0  # output sample spacing (m)
EARTH_R = 6371000.0


# --------------------------------------------------------------------- grids
class Grid:
    def __init__(self, stem: str, smooth_sigma_m: float = 0.0):
        self.a = np.load(stem + ".npy").astype(np.float64)
        m = json.load(open(stem + ".json"))
        self.meta = m
        self.x0, self.y0, self.res = m["x0_centre"], m["y0_centre"], m["res"]
        self.nan = np.isnan(self.a)
        if smooth_sigma_m > 0:
            filled = np.where(self.nan, 0.0, self.a)
            w = ndimage.gaussian_filter((~self.nan).astype(float), smooth_sigma_m / self.res)
            s = ndimage.gaussian_filter(filled, smooth_sigma_m / self.res)
            with np.errstate(invalid="ignore", divide="ignore"):
                self.a = np.where(self.nan, np.nan, s / w)

    def sample(self, e, n):
        c = (np.asarray(e) - self.x0) / self.res
        r = (self.y0 - np.asarray(n)) / self.res
        return ndimage.map_coordinates(self.a, [r, c], order=1, mode="nearest", cval=np.nan)

    def coords(self):
        cols = self.x0 + np.arange(self.a.shape[1]) * self.res
        rows = self.y0 - np.arange(self.a.shape[0]) * self.res
        return cols, rows


# --------------------------------------------------------------------- osm
def load_osm(raw):
    d = json.load(open(os.path.join(raw, "osm_raceway.json"), encoding="utf-8"))
    ways, nodes = {}, {}
    for e in d["elements"]:
        if e["type"] == "way" and "geometry" in e:
            g = e["geometry"]
            E, N = wgs84_to_bng([p["lat"] for p in g], [p["lon"] for p in g])
            ways[e["id"]] = {"nodes": e["nodes"], "E": np.asarray(E), "N": np.asarray(N), "tags": e.get("tags", {})}
        elif e["type"] == "node":
            E, N = wgs84_to_bng(e["lat"], e["lon"])
            nodes[e["id"]] = {"E": float(E), "N": float(N), "tags": e.get("tags", {}), "lat": e["lat"], "lon": e["lon"]}
    return ways, nodes


def chain(ways, spec):
    """Concatenate way pieces; return points and a per-point list of source way ids."""
    pts, src = [], []
    for item in spec:
        if isinstance(item, list):
            wid, a, b = item
            w = ways[wid]
            i, j = w["nodes"].index(a), w["nodes"].index(b)
            sl = slice(i, j + 1)
        else:
            wid, w, sl = item, ways[item], slice(None)
        P = np.column_stack([w["E"][sl], w["N"][sl]])
        if pts:
            gap = np.hypot(*(pts[-1][-1] - P[0]))
            assert gap < 0.5, f"layout not connected before way {wid}: gap {gap:.1f} m"
            P = P[1:]
        pts.append(P)
        src += [wid] * len(P)
    P = np.vstack(pts)
    assert np.hypot(*(P[-1] - P[0])) < 0.5, "layout is not closed"
    return P[:-1], src[:-1]


def resample_closed(P, step, smooth_m=0.15):
    keep = np.r_[True, np.hypot(*np.diff(P, axis=0).T) > 0.5]
    P = P[keep]
    # densify long OSM segments so the spline cannot bow away from straights
    Q = densify(P, 4.0)
    Q = np.vstack([Q, Q[:1]])
    tck, u = interpolate.splprep([Q[:, 0], Q[:, 1]], s=len(Q) * smooth_m ** 2, per=1)
    uu = np.linspace(0, 1, 20000, endpoint=False)
    x, y = interpolate.splev(uu, tck)
    seg = np.hypot(np.diff(np.r_[x, x[0]]), np.diff(np.r_[y, y[0]]))
    cum = np.r_[0, np.cumsum(seg)]
    L = cum[-1]
    n = int(round(L / step))
    st = np.linspace(0, L, n, endpoint=False)
    ui = np.interp(st, cum, np.r_[uu, 1.0])
    x, y = interpolate.splev(ui, tck)
    dx, dy = interpolate.splev(ui, tck, der=1)
    ddx, ddy = interpolate.splev(ui, tck, der=2)
    k = (dx * ddy - dy * ddx) / (dx * dx + dy * dy) ** 1.5
    t = np.column_stack([dx, dy]) / np.hypot(dx, dy)[:, None]
    return np.column_stack([x, y]), t, k, L, (tck, P)


def densify(P, step=0.25):
    """Closed polyline -> points every <= step metres (for nearest-point queries)."""
    Q = np.vstack([P, P[:1]])
    out = []
    for a, b in zip(Q[:-1], Q[1:]):
        m = max(int(np.ceil(np.hypot(*(b - a)) / step)), 1)
        out.append(a + (b - a) * (np.arange(m)[:, None] / m))
    return np.vstack(out)


def roll_to(arr_list, i0):
    return [np.roll(a, -i0, axis=0) for a in arr_list]


def gsmooth(v, sigma_samples):
    return ndimage.gaussian_filter1d(v, sigma_samples, mode="wrap")


# --------------------------------------------------------------------- registration
def register(grid, XY, T, half=5.0, span=6.0, step=0.5):
    """Shift the OSM centreline so the DTM cross-sections are flattest (road surface)."""
    nrm = np.column_stack([-T[:, 1], T[:, 0]])
    offs = np.arange(-half, half + 0.01, 1.0)
    sub = slice(None, None, 2)
    best = None
    table = {}
    A = np.column_stack([offs, np.ones_like(offs)])
    pinv = np.linalg.pinv(A)
    for dx in np.arange(-span, span + 0.01, step):
        for dy in np.arange(-span, span + 0.01, step):
            E = XY[sub, 0, None] + dx + nrm[sub, 0, None] * offs
            N = XY[sub, 1, None] + dy + nrm[sub, 1, None] * offs
            z = grid.sample(E.ravel(), N.ravel()).reshape(E.shape)
            coef = z @ pinv.T
            res = z - coef @ A.T
            r = float(np.sqrt(np.nanmean(res ** 2)))
            table[(round(dx, 2), round(dy, 2))] = r
            if best is None or r < best[0]:
                best = (r, dx, dy)
    return best, table[(0.0, 0.0)]


# --------------------------------------------------------------------- per layout
def sample_layout(name, cfg, ways, nodes, grid, shift, scale_k):
    P, src = chain(ways, cfg["layouts"][name])
    XYg, T, k, Lg, (tck, Praw) = resample_closed(P + shift, STEP)
    sf = nodes[cfg["start_finish_node"]]
    d = np.hypot(XYg[:, 0] - (sf["E"] + shift[0]), XYg[:, 1] - (sf["N"] + shift[1]))
    i0 = int(np.argmin(d))
    sf_offset = float(d[i0])
    XYg, T, k = roll_to([XYg, T, k], i0)
    n = len(XYg)
    s = np.arange(n) * (Lg / n) / scale_k  # ground metres

    # which OSM way each sample comes from (nearest point of the densified raw chain)
    from scipy.spatial import cKDTree
    Psh = P + shift
    D = densify(Psh)
    # segment P[i] -> P[i+1] belongs to the way of its end point (chain() drops each way's first node)
    Dsrc = np.repeat(np.roll(np.asarray(src), -1), [max(int(np.ceil(np.hypot(*(b - a)) / 0.25)), 1)
                                       for a, b in zip(Psh, np.roll(Psh, -1, axis=0))])
    dev, nearest = cKDTree(D).query(XYg)
    src_s = Dsrc[nearest]

    widths = []
    for wid in src_s:
        w = ways[wid]["tags"].get("width")
        widths.append(float(w) if w else cfg["default_width_m"])
    widths = np.asarray(widths)

    nrm = np.column_stack([-T[:, 1], T[:, 0]])
    zc = grid.sample(XYg[:, 0], XYg[:, 1])
    z = gsmooth(zc, 2.0)  # sigma 4 m along track
    h = widths / 2 - 1.0
    zl = grid.sample(XYg[:, 0] + nrm[:, 0] * h, XYg[:, 1] + nrm[:, 1] * h)
    zr = grid.sample(XYg[:, 0] - nrm[:, 0] * h, XYg[:, 1] - nrm[:, 1] * h)
    crossfall = gsmooth((zl - zr) / (2 * h), 2.5)  # sigma 5 m
    ks = gsmooth(k, 2.0)
    bank = -np.sign(ks) * crossfall
    turning = np.abs(ks) > 1 / 300.0

    def grade(win):
        m = int(round(win / 2 / STEP))
        return (np.roll(z, -m) - np.roll(z, m)) / (2 * m * STEP / scale_k)

    g10, g20 = grade(10), grade(20)
    dz = np.diff(np.r_[z, z[0]])
    stats = {
        "lapLengthM": round(float(s[-1] + Lg / n / scale_k), 1),
        "lapLengthGridM": round(float(Lg), 1),
        "osmPolylineLengthM": round(float(np.sum(np.hypot(*np.diff(np.vstack([Praw, Praw[:1]]), axis=0).T)) / scale_k), 1),
        "publishedLapLengthM": cfg["published"][name]["length_m"],
        "direction": None,
        "heightMinM": round(float(z.min()), 2),
        "heightMaxM": round(float(z.max()), 2),
        "heightRangeM": round(float(z.max() - z.min()), 2),
        "totalClimbM": round(float(dz[dz > 0].sum()), 1),
        "maxUpGradePct10m": round(float(g10.max() * 100), 1),
        "maxUpGradeAtS10m": round(float(s[np.argmax(g10)]), 0),
        "maxDownGradePct10m": round(float(g10.min() * 100), 1),
        "maxDownGradeAtS10m": round(float(s[np.argmin(g10)]), 0),
        "maxUpGradePct20m": round(float(g20.max() * 100), 1),
        "maxDownGradePct20m": round(float(g20.min() * 100), 1),
        "maxCrossfallDeg": round(float(np.degrees(np.arctan(np.abs(crossfall).max()))), 2),
        "maxCrossfallAtS": round(float(s[np.argmax(np.abs(crossfall))]), 0),
        "crossfallP95Deg": round(float(np.degrees(np.arctan(np.percentile(np.abs(crossfall), 95)))), 2),
        "maxBankIntoTurnDeg": round(float(np.degrees(np.arctan(bank[turning].max()))), 2),
        "maxBankIntoTurnAtS": round(float(s[turning][np.argmax(bank[turning])]), 0),
        "maxOffCamberDeg": round(float(np.degrees(np.arctan(-bank[turning].min()))), 2),
        "maxOffCamberAtS": round(float(s[turning][np.argmin(bank[turning])]), 0),
        "minRadiusM": round(float(1 / np.abs(ks).max()), 1),
        "rmsRawVsSmoothedZM": round(float(np.sqrt(np.mean((zc - z) ** 2))), 3),
        "startFinishSnapM": round(sf_offset, 2),
        "maxSplineDeviationFromOsmM": round(float(dev.max()), 2),
        "meanSplineDeviationFromOsmM": round(float(dev.mean()), 2),
        "maxSplineDeviationAtS": round(float(s[np.argmax(dev)]), 0),
        "splineDeviationOver1mAtS": [round(float(v), 0) for v in s[dev > 1.0][::5]],
    }
    area = 0.5 * np.sum(XYg[:, 0] * np.roll(XYg[:, 1], -1) - np.roll(XYg[:, 0], -1) * XYg[:, 1])
    stats["direction"] = "anticlockwise" if area > 0 else "clockwise"

    # corners from named OSM ways
    corners = []
    for cname, wids in cfg["corner_ways"].items():
        wids = wids if isinstance(wids, list) else [wids]
        idx = np.where(np.isin(src_s, wids))[0]
        if len(idx) == 0:
            continue
        # contiguous run (handle wrap)
        if idx[0] == 0 and idx[-1] == n - 1:
            gaps = np.where(np.diff(idx) > 1)[0]
            idx = np.r_[idx[gaps[0] + 1:], idx[:gaps[0] + 1]] if len(gaps) else idx
        a = idx[np.argmax(np.abs(ks[idx]))]
        corners.append({
            "name": cname, "sStartM": round(float(s[idx[0]]), 1), "sEndM": round(float(s[idx[-1]]), 1),
            "apexSM": round(float(s[a]), 1), "turn": "left" if ks[a] > 0 else "right",
            "apexRadiusM": round(float(1 / abs(ks[a])), 1), "apexZM": round(float(z[a]), 2),
            "zInM": round(float(z[idx[0]]), 2), "zOutM": round(float(z[idx[-1]]), 2),
            "bankAtApexDeg": round(float(np.degrees(np.arctan(bank[a]))), 2),
            "maxGradePctInRange": round(float(g10[idx][np.argmax(np.abs(g10[idx]))] * 100), 1),
            "osmWays": wids,
        })
    corners.sort(key=lambda c: c["apexSM"])

    samples = {
        "sM": np.round(s, 2).tolist(),
        "e": np.round(XYg[:, 0], 2).tolist(), "n": np.round(XYg[:, 1], 2).tolist(),
        "zM": np.round(z, 3).tolist(),
        "crossfall": np.round(crossfall, 4).tolist(),
        "bankDeg": np.round(np.degrees(np.arctan(bank)), 2).tolist(),
        "curvature": np.round(ks, 5).tolist(),
        "grade10m": np.round(g10, 4).tolist(),
        "widthM": widths.tolist(),
        "osmWay": [int(w) for w in src_s],
    }
    return {"stats": stats, "corners": corners, "samples": samples, "_raw": D,
            "_XY": XYg, "_z": z, "_src": src_s, "_s": s, "_cf": crossfall}


# --------------------------------------------------------------------- site context
def sea_summary(far, cx, cy):
    """Per 15° sector from the site centre: distance to the first sea / no-data cell."""
    out = []
    for az in range(0, 360, 15):
        a = math.radians(az)
        r = np.arange(0, 6000, far.res)
        e, nn = cx + r * math.sin(a), cy + r * math.cos(a)
        z = far.sample(e, nn)
        nan_hit = np.where(np.isnan(z) | (z < 0.5))[0]
        out.append({"azimuthDeg": az, "seaWithinM": None if len(nan_hit) == 0 else round(float(r[nan_hit[0]]), 0)})
    return out


def horizon(hgrid, e0, n0, z0):
    """Max elevation angle per 1° azimuth (earth curvature + refraction k = 0.13)."""
    res = []
    zz = np.where(np.isnan(hgrid.a), 0.0, hgrid.a)
    hgrid2 = Grid.__new__(Grid)
    hgrid2.a, hgrid2.x0, hgrid2.y0, hgrid2.res = zz, hgrid.x0, hgrid.y0, hgrid.res
    r = np.arange(400, 45000, hgrid.res)
    for az in range(360):
        a = math.radians(az)
        z = Grid.sample(hgrid2, e0 + r * math.sin(a), n0 + r * math.cos(a))
        drop = r ** 2 / (2 * EARTH_R) * 0.87
        ang = np.degrees(np.arctan((z - drop - z0) / r))
        i = int(np.argmax(ang))
        res.append({"az": az, "elevDeg": round(float(ang[i]), 3), "distM": round(float(r[i]), 0), "zM": round(float(z[i]), 1)})
    return res


def env_summary(raw, cx, cy):
    p = os.path.join(raw, "osm_env.json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding="utf-8"))
    from collections import Counter, defaultdict
    area = defaultdict(float)
    count = Counter()
    length = defaultdict(float)
    named = []
    feats = []
    for e in d["elements"]:
        t = e.get("tags", {})
        if e["type"] == "node":
            E, N = wgs84_to_bng(e["lat"], e["lon"])
            named.append({"tags": t, "x": round(float(E - cx), 1), "y": round(float(N - cy), 1)})
            continue
        geoms = []
        if e["type"] == "way" and "geometry" in e:
            geoms = [e["geometry"]]
        elif e["type"] == "relation":
            geoms = [m["geometry"] for m in e.get("members", []) if m.get("role") == "outer" and "geometry" in m]
        key = None
        for k in ("natural", "landuse", "leisure", "waterway", "barrier", "building", "highway", "power", "amenity"):
            if k in t:
                key = f"{k}={t[k]}"
                break
        if key is None:
            continue
        count[key] += 1
        for g in geoms:
            g = [p for p in g if p]
            if len(g) < 2:
                continue
            E, N = wgs84_to_bng([p["lat"] for p in g], [p["lon"] for p in g])
            E, N = np.asarray(E), np.asarray(N)
            L = float(np.sum(np.hypot(np.diff(E), np.diff(N))))
            length[key] += L
            closed = e["type"] == "way" and e["nodes"][0] == e["nodes"][-1] or e["type"] == "relation"
            A = 0.0
            if closed:
                A = abs(0.5 * float(np.sum(E * np.roll(N, -1) - np.roll(E, -1) * N)))
                area[key] += A
            dist = float(np.min(np.hypot(E - cx, N - cy)))
            feats.append({"key": key, "name": t.get("name"), "closed": bool(closed), "areaM2": round(A),
                          "lengthM": round(L), "nearestM": round(dist),
                          "centroid": [round(float(E.mean() - cx), 1), round(float(N.mean() - cy), 1)],
                          "tags": {k: v for k, v in t.items() if k in ("natural", "landuse", "leisure", "barrier", "building", "highway", "surface", "wall", "material", "crop", "produce", "name", "power", "waterway")}})
    return {"counts": dict(count.most_common()),
            "areaHa": {k: round(v / 1e4, 2) for k, v in sorted(area.items(), key=lambda kv: -kv[1])},
            "lengthKm": {k: round(v / 1e3, 2) for k, v in sorted(length.items(), key=lambda kv: -kv[1])},
            "pointFeatures": named, "features": feats}


# --------------------------------------------------------------------- preview
def preview(path, lay, grid, frame):
    from PIL import Image, ImageDraw
    XY, z, cf = lay["_XY"], lay["_z"], lay["_cf"]
    W, H = 1400, 900
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    # plan, coloured by height
    x0, x1 = XY[:, 0].min() - 50, XY[:, 0].max() + 50
    y0, y1 = XY[:, 1].min() - 50, XY[:, 1].max() + 50
    sc = min(880 / (x1 - x0), 560 / (y1 - y0))
    zmin, zmax = z.min(), z.max()
    def P(e, nn): return (10 + (e - x0) * sc, 10 + (y1 - nn) * sc)
    for i in range(len(XY)):
        j = (i + 1) % len(XY)
        f = (z[i] - zmin) / max(zmax - zmin, 1e-6)
        dr.line([P(*XY[i]), P(*XY[j])], fill=(int(255 * f), 60, int(255 * (1 - f))), width=5)
    for c in lay["corners"]:
        i = int(round(c["apexSM"] / (lay["_s"][1] - lay["_s"][0]))) % len(XY)
        p = P(*XY[i]); dr.text((p[0] + 6, p[1] - 6), c["name"], fill="black")
    p = P(*XY[0]); dr.rectangle([p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5], fill="black"); dr.text((p[0] + 6, p[1] + 4), "S/F", fill="black")
    # profile
    s = lay["_s"]
    def Q(si, zi): return (10 + si / s[-1] * (W - 20), 880 - (zi - zmin) / max(zmax - zmin, 1) * 220)
    dr.line([Q(a, b) for a, b in zip(s, z)], fill="black", width=2)
    def R(si, c): return (10 + si / s[-1] * (W - 20), 620 - c * 600)
    dr.line([(10, 620), (W - 10, 620)], fill="#ccc")
    dr.line([R(a, b) for a, b in zip(s, cf)], fill="green", width=1)
    dr.text((W - 330, 600), "green: crossfall (L-R), 10 px = 1 %", fill="green")
    dr.text((W - 330, 860), f"height {zmin:.1f}..{zmax:.1f} m", fill="black")
    img.save(path)


# --------------------------------------------------------------------- main
def main(cfg_path):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    raw, out = cfg["raw_dir"], cfg["out_dir"]
    os.makedirs(out, exist_ok=True)
    ways, nodes = load_osm(raw)
    grid_raw = Grid(os.path.join(raw, cfg["dtm"]))
    grid = Grid(os.path.join(raw, cfg["dtm"]), smooth_sigma_m=1.5)

    first = next(iter(cfg["layouts"]))
    P, _ = chain(ways, cfg["layouts"][first])
    cE, cN = float(np.round(P[:, 0].mean())), float(np.round(P[:, 1].mean()))
    # National Grid point scale factor at the site (Airy, F0) — ground = grid / k
    dE = cE - 400000.0
    k = 0.9996012717 * (1 + dE ** 2 / (2 * 6381000.0 ** 2))

    XY0, T0, *_ = resample_closed(P, STEP)
    (rms_best, dx, dy), rms0 = register(grid_raw, XY0, T0)
    shift = np.array([dx, dy])
    print(f"registration: shift ({dx:+.1f}, {dy:+.1f}) m, cross-section RMS {rms0:.3f} -> {rms_best:.3f} m")

    result = {
        "name": cfg["name"], "slug": cfg["slug"],
        "frame": {"crs": "EPSG:27700", "centreE": cE, "centreN": cN, "gridScaleFactor": round(k, 7),
                  "local": "x = E - centreE, y = N - centreN (grid metres), z = metres ODN"},
        "registrationShiftM": [round(dx, 2), round(dy, 2)],
        "registrationCrossSectionRmsM": {"before": round(rms0, 3), "after": round(rms_best, 3)},
        "conventions": __doc__.split("Conventions:")[1].strip(),
        "sampleStepM": STEP,
        "layouts": {},
    }
    lays = {}
    for name in cfg["layouts"]:
        lay = sample_layout(name, cfg, ways, nodes, grid, shift, k)
        lays[name] = lay
        smp = lay["samples"]
        smp["x"] = [round(v - cE, 2) for v in smp.pop("e")]
        smp["y"] = [round(v - cN, 2) for v in smp.pop("n")]
        result["layouts"][name] = {"stats": lay["stats"], "corners": lay["corners"], "samples": smp}
        preview(os.path.join(out, f"preview_{name}.png"), lay, grid, (cE, cN))
        print(name, json.dumps(lay["stats"]))

    # shared sections / joins (by OSM way membership)
    names = list(cfg["layouts"])
    if len(names) >= 2:
        a, b = lays[names[0]], lays[names[1]]
        joins = []
        for nm, lay in ((names[0], a), (names[1], b)):
            other = b if lay is a else a
            # shared = this sample's OSM source point also lies on the other layout's OSM chain
            from scipy.spatial import cKDTree
            _, ii = cKDTree(lay["_raw"]).query(lay["_XY"])
            dist, _ = cKDTree(other["_raw"]).query(lay["_raw"][ii])
            shared = dist < 0.3
            edges = np.where(np.diff(np.r_[shared, shared[0]].astype(int)) != 0)[0]
            for i in edges:
                j = (i + 1) % len(shared)
                kind = "split" if shared[i] and not shared[j] else "rejoin"
                joins.append({"layout": nm, "kind": kind, "sM": round(float(lay["_s"][j]), 1),
                              "x": round(float(lay["_XY"][j, 0] - cE), 1), "y": round(float(lay["_XY"][j, 1] - cN), 1)})
            result["layouts"][nm]["sharedFraction"] = round(float(shared.mean()), 3)
            result["layouts"][nm]["sharedLengthM"] = round(float(shared.sum() * STEP / k), 0)
        result["joins"] = joins

    sf = nodes[cfg["start_finish_node"]]
    result["startFinish"] = {"osmNode": cfg["start_finish_node"], "lat": sf["lat"], "lon": sf["lon"],
                             "x": round(sf["E"] + dx - cE, 2), "y": round(sf["N"] + dy - cN, 2),
                             "headingDegFromNorth": None}
    t0 = lays[first]["_XY"][1] - lays[first]["_XY"][0]
    result["startFinish"]["headingDegFromNorth"] = round(float(np.degrees(np.arctan2(t0[0], t0[1])) % 360), 1)
    result["namedNodes"] = [{"name": v["tags"].get("name"), "x": round(v["E"] + dx - cE, 1), "y": round(v["N"] + dy - cN, 1)}
                            for v in nodes.values() if v["tags"].get("name")]
    result["otherRaceways"] = [{"osmWay": wid, "name": w["tags"].get("name"), "sport": w["tags"].get("sport"),
                                "surface": w["tags"].get("surface"),
                                "x": np.round(w["E"] + dx - cE, 1).tolist(), "y": np.round(w["N"] + dy - cN, 1).tolist()}
                               for wid, w in ways.items()
                               if w["tags"].get("highway") == "raceway" and not any(
                                   wid == (it[0] if isinstance(it, list) else it) for L in cfg["layouts"].values() for it in L)]

    # terrain: 4 m block mean of the 1 m DTM
    a = grid_raw.a
    r4, c4 = a.shape[0] // 4, a.shape[1] // 4
    blk = a[:r4 * 4, :c4 * 4].reshape(r4, 4, c4, 4)
    import warnings
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        t4 = np.nanmean(blk, axis=(1, 3)).astype(np.float32)
    np.save(os.path.join(out, "terrain_4m.npy"), t4)
    land = t4[np.isfinite(t4)]
    far = Grid(os.path.join(raw, cfg["dtm_far"]))
    result["terrain"] = {
        "file": "terrain_4m.npy", "res": 4.0, "rows": int(r4), "cols": int(c4), "rowOrder": "north-to-south",
        "x0Centre": round(grid_raw.x0 + 1.5 - cE, 2), "y0Centre": round(grid_raw.y0 - 1.5 - cN, 2),
        "nanMeans": "sea or no LIDAR (check seaMask)",
        "landMinM": round(float(land.min()), 2), "landMaxM": round(float(land.max()), 2),
        "landP5M": round(float(np.percentile(land, 5)), 2), "landP95M": round(float(np.percentile(land, 95)), 2),
        "nanFraction": round(float(np.isnan(t4).mean()), 3),
        "farGrid": {"file": os.path.join(raw, cfg["dtm_far"]) + ".npy", "res": far.res,
                    "x0Centre": round(far.x0 - cE, 1), "y0Centre": round(far.y0 - cN, 1),
                    "rows": int(far.a.shape[0]), "cols": int(far.a.shape[1])},
        "seaBySector": sea_summary(far, cE, cN),
    }
    sea = np.isnan(t4)
    lab, nlab = ndimage.label(sea)
    if nlab:
        big = np.argmax(ndimage.sum(sea, lab, range(1, nlab + 1))) + 1
        seadist = ndimage.distance_transform_edt(lab != big) * 4.0
        for name, lay in lays.items():
            XY = lay["_XY"]
            cc = np.clip(((XY[:, 0] - (grid_raw.x0 + 1.5)) / 4).round().astype(int), 0, c4 - 1)
            rr = np.clip((((grid_raw.y0 - 1.5) - XY[:, 1]) / 4).round().astype(int), 0, r4 - 1)
            dd = seadist[rr, cc]
            result["layouts"][name]["stats"]["nearestSeaM"] = round(float(dd.min()), 0)
            result["layouts"][name]["stats"]["nearestSeaAtS"] = round(float(lay["_s"][np.argmin(dd)]), 0)
    hz = Grid(os.path.join(raw, cfg["dtm_horizon"]))
    sfz = float(lays[first]["_z"][0]) + 1.5
    result["horizon"] = {"eyeE": sf["E"] + dx, "eyeN": sf["N"] + dy, "eyeZ": round(sfz, 2),
                         "note": "max terrain elevation angle per azimuth from the start line (NaN = sea at 0 m)",
                         "profile": horizon(hz, sf["E"] + dx, sf["N"] + dy, sfz)}
    with open(os.path.join(out, f"{cfg['slug']}.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    env = env_summary(raw, cE, cN)
    if env:
        with open(os.path.join(out, "environment.json"), "w", encoding="utf-8") as f:
            json.dump(env, f, ensure_ascii=False, indent=0)
    print("joins", json.dumps(result.get("joins")))
    print("startFinish", result["startFinish"])
    print("terrain", {k: v for k, v in result["terrain"].items() if k != "seaBySector"})
    print("sea", [(s["azimuthDeg"], s["seaWithinM"]) for s in result["terrain"]["seaBySector"]])


if __name__ == "__main__":
    main(sys.argv[1])
