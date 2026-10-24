"""Pits, paddock, pit buildings, grandstands, bridges and gantries of a real circuit from open data, for the importer
and the builder: ``tools/real_tracks/pits/<slug>.json``.

    python tools/real_tracks_data/pits.py <slug> [--plots]        # rewrite the derived part of the file (+ overlays)
    python tools/real_tracks_data/pits.py <slug> --candidates      # list OSM candidates near the pits

The file has a hand-written part, ``select`` and ``sources`` (which OSM ways are the pit lane, the buildings, the
paddock; the gantries and bridges with their sources; the pit lane's width and mode), and a derived part this tool
writes: ``pitLanes``, ``pitWalls``, ``buildings``, ``paddocks``, ``grandstands``, ``gantries``. Geometry is in the
recipe's grid (British National Grid, or ETRS89 / UTM 30N for Navarre), metres, so a re-import (whose local frame
may move slightly) converts it again; ``sM`` values are distances along the committed sample's main lap from its
start/finish line, for checking.

Data: OpenStreetMap (c) OpenStreetMap contributors, ODbL 1.0 (fetch_pits_osm.py); heights above the ground from the
open LIDAR (pits_lidar.py: EA National LIDAR Programme point cloud and LIDAR Composite DTM, OGL v3; Gobierno de
Navarra LiDAR 2024, CC BY 4.0; Welsh Government / NRW LIDAR DSM/DTM, OGL v3). Run with the widths venv (numpy,
laspy, matplotlib).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pits_common import LOCAL, ROOT, Circuit, polygon_area, polyline_length, seg_intersections  # noqa: E402
from pits_lidar import ndsm  # noqa: E402

PITS_DIR = ROOT / "tools" / "real_tracks" / "pits"
FORMAT = "trackgen-pits/1"
DERIVED = ("pitLanes", "pitWalls", "buildings", "paddocks", "grandstands", "gantries")
WALL_MIN_GAP_M = 1.2      # the pit wall stands where the pit lane's edge is at least this far from the track's edge
WALL_HEIGHT_M = 1.0       # concrete pit wall (the fence on top is decoration)
WALL_LANE_GAP_M = 1.5     # the wall stands this far off the pit lane's edge (midway when the gap is narrower)


# ------------------------------------------------------------------------------------------------ geometry helpers
def resample(p: np.ndarray, step: float) -> np.ndarray:
    seg = np.hypot(*np.diff(p, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    t = np.linspace(0.0, s[-1], max(int(round(s[-1] / step)), 1) + 1)
    return np.column_stack([np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])])


def smooth_open(p: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0 or len(p) < 5:
        return p
    r = int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.vstack([np.repeat(p[:1], r, 0), p, np.repeat(p[-1:], r, 0)])
    out = np.column_stack([np.convolve(pad[:, 0], k, "valid"), np.convolve(pad[:, 1], k, "valid")])
    out[0], out[-1] = p[0], p[-1]
    return out


def tangents(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = np.gradient(p, axis=0)
    t = t / np.maximum(np.linalg.norm(t, axis=1), 1e-9)[:, None]
    return t, np.column_stack([-t[:, 1], t[:, 0]])


def min_area_rect(poly: np.ndarray) -> dict:
    """Smallest rotated rectangle around a polygon: centre, length (long side), depth, the long axis' angle."""
    best = None
    q = poly[:-1] if np.allclose(poly[0], poly[-1]) else poly
    for i in range(len(q)):
        e = q[(i + 1) % len(q)] - q[i]
        if np.hypot(*e) < 1e-6:
            continue
        a = math.atan2(e[1], e[0])
        R = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])
        r = q @ R.T
        lo, hi = r.min(0), r.max(0)
        area = float(np.prod(hi - lo))
        if best is None or area < best[0]:
            c = ((lo + hi) / 2.0) @ R
            best = (area, c, hi - lo, a)
    _, c, ext, a = best
    if ext[1] > ext[0]:
        ext = ext[::-1]
        a += math.pi / 2
    a = (a + math.pi / 2) % math.pi - math.pi / 2
    return {"centre": c, "length": float(ext[0]), "depth": float(ext[1]), "angle": a}


def point_in_poly(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi, xj, yj = poly[i, 0], poly[i, 1], poly[j, 0], poly[j, 1]
        cross = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / np.where(yj - yi == 0, 1e-12, yj - yi) + xi)
        inside ^= cross
        j = i
    return inside


def rnd(a, nd: int = 2):
    return np.round(np.asarray(a, dtype=float), nd).tolist()


# ------------------------------------------------------------------------------------------------ the survey
class Pits:
    def __init__(self, slug: str):
        self.c = Circuit(slug)
        self.path = PITS_DIR / f"{slug}.json"          # hand-written: select + sources
        self.out = PITS_DIR / "derived" / f"{slug}.json"  # written by this tool
        self.doc = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.sel = self.doc.get("select", {})
        self._nd = None

    # nDSM over the pit area (lazy, one box around everything selected)
    def nd(self, pts_local: np.ndarray):
        lo, hi = pts_local.min(0) - 20, pts_local.max(0) + 20
        if self._nd is not None:
            r, box = self._nd
            if (lo >= box[0]).all() and (hi <= box[1]).all():
                return r
        g0, g1 = self.c.grid_of_local(lo), self.c.grid_of_local(hi)
        try:
            r, _ = ndsm(self.c, g0[0], g0[1], g1[0], g1[1])
        except Exception as exc:  # noqa: BLE001 - no LIDAR here
            print("  no nDSM:", exc)
            r = None
        self._nd = (r, (lo, hi))
        return r

    def heights_in(self, poly: np.ndarray, erode: float = 1.0) -> dict | None:
        """nDSM statistics inside a footprint (local coordinates), its edge cells left out."""
        lo, hi = poly.min(0), poly.max(0)
        xs = np.arange(math.floor(lo[0]), math.ceil(hi[0]) + 1, 1.0) + 0.5
        ys = np.arange(math.floor(lo[1]), math.ceil(hi[1]) + 1, 1.0) + 0.5
        gx, gy = np.meshgrid(xs, ys)
        pts = np.column_stack([gx.ravel(), gy.ravel()])
        ins = point_in_poly(pts, poly)
        if erode > 0 and ins.any():
            d = np.min(np.hypot(pts[ins, None, 0] - poly[None, :, 0], pts[ins, None, 1] - poly[None, :, 1]), axis=1)
            keep = np.zeros(len(pts), dtype=bool)
            keep[np.nonzero(ins)[0][d > erode]] = True
            if keep.sum() >= 4:
                ins = keep
        if ins.sum() < 2:
            return None
        nd = self.nd(np.vstack([poly, self.c.main]))
        if nd is None:
            return None
        g = self.c.grid_of_local(pts[ins])
        h = nd.sample(g[:, 0], g[:, 1])
        h = h[np.isfinite(h)]
        if not len(h):
            return None
        return {"p50": float(np.percentile(h, 50)), "p85": float(np.percentile(h, 85)), "max": float(h.max()),
                "cells": int(len(h)), "coverAbove2m": float(np.mean(h > 2.0))}

    # ---------------------------------------------------------------------------------------- pit lanes
    def lane_line(self, spec: dict) -> np.ndarray:
        """The pit lane's ways stitched in order, oriented in the driving direction (local coordinates)."""
        parts = []
        for item in spec["ways"]:
            wid = int(item[0]) if isinstance(item, list) else int(item)
            g = self.c.geom(self.c.ways[wid])
            if isinstance(item, list) and len(item) >= 3:  # [way, from_node, to_node]
                nodes = self.c.ways[wid]["nodes"]
                a, b = nodes.index(int(item[1])), nodes.index(int(item[2]))
                g = g[a:b + 1] if a <= b else g[b:a + 1][::-1]
            if parts:
                last = parts[-1][-1]
                if np.hypot(*(g[-1] - last)) < np.hypot(*(g[0] - last)):
                    g = g[::-1]
            elif len(spec["ways"]) > 1:
                nxt = self.c.geom(self.c.ways[int(spec["ways"][1][0] if isinstance(spec["ways"][1], list) else spec["ways"][1])])
                if min(np.hypot(*(g[0] - nxt[0])), np.hypot(*(g[0] - nxt[-1]))) < min(np.hypot(*(g[-1] - nxt[0])), np.hypot(*(g[-1] - nxt[-1]))):
                    g = g[::-1]
            parts.append(g)
        p = np.vstack([parts[0]] + [q[1:] if np.hypot(*(q[0] - parts[k][-1])) < 1.0 else q for k, q in enumerate(parts[1:])])
        # driving direction: the lane's direction should follow the main lap's (mean tangent dot product)
        i, _, _ = self.c.nearest_main(p)
        t_main = np.roll(self.c.main, -1, 0) - np.roll(self.c.main, 1, 0)
        t_lane = np.gradient(p, axis=0)
        if float((t_main[i] * t_lane).sum()) < 0:
            p = p[::-1]
        for cut in ("trimStartM", "trimEndM"):
            if spec.get(cut):
                q = resample(p, 0.5)
                n_cut = int(float(spec[cut]) / 0.5)
                p = q[n_cut:] if cut == "trimStartM" else q[:len(q) - n_cut]
        p = resample(p, 1.0)
        p = smooth_open(p, float(spec.get("smoothM", 3.0)))
        if spec.get("centreFromTrackM"):
            p = self.push_out(p, float(spec["centreFromTrackM"]), float(spec.get("taperM", 60.0)))
        return resample(p, 2.0)

    def push_out(self, p: np.ndarray, target: float, taper: float) -> np.ndarray:
        """OSM often maps a pit lane on its fast lane or near its track-side edge: move it sideways, away from the
        track, until its centre is ``target`` metres from the track's centreline, fading the move in over ``taper``
        metres from each end (the merges stay where OSM has them). Never moves a point towards the track."""
        c = self.c
        i, d, lat = c.nearest_main(p)
        t_m, nl_m = tangents(np.vstack([c.main[-1:], c.main, c.main[:1]]))
        nl_m = nl_m[1:-1]
        s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])
        f = np.clip(np.minimum(s, s[-1] - s) / taper, 0.0, 1.0)
        f = f * f * (3.0 - 2.0 * f)
        move = np.maximum(target - d, 0.0) * f
        side = np.sign(lat)
        side[side == 0] = 1.0
        q = p + nl_m[i] * (side * move)[:, None]
        return smooth_open(resample(q, 1.0), 4.0)

    def pit_lane(self, spec: dict) -> tuple[dict, dict | None]:
        c = self.c
        p = self.lane_line(spec)
        w = float(spec["widthM"])
        i, d, lat = c.nearest_main(p)
        mw = c.main_w[i]
        # merged where the lane's road overlaps the track's (centre within half both widths)
        merged = d < (mw / 2.0 + w / 2.0 - 0.5)
        gap = d - mw / 2.0 - w / 2.0   # clear ground between the track's edge and the lane's edge
        n = len(p)
        sep = np.nonzero(~merged)[0]
        if not len(sep):
            raise ValueError("the pit lane never leaves the track")
        k0, k1 = int(sep[0]), int(sep[-1])
        side = int(np.sign(np.median(lat[k0:k1 + 1])))
        lane = {
            "id": spec.get("id", "pit"),
            "mode": spec["mode"],
            "modeReason": spec.get("modeReason", ""),
            "widthM": w,
            "widthSource": spec.get("widthSource", ""),
            "side": "left" if side > 0 else "right",
            "centreline": rnd(c.grid_of_local(p)),
            "lengthM": round(polyline_length(p), 1),
            "entry": {"sM": round(float(c.s[i[k0]]), 1), "laneIndex": k0, "mergedAtStart": bool(merged[0]),
                      "point": rnd(c.grid_of_local(p[k0]))},
            "exit": {"sM": round(float(c.s[i[k1]]), 1), "laneIndex": k1, "mergedAtEnd": bool(merged[-1]),
                     "point": rnd(c.grid_of_local(p[k1]))},
            # how much shorter the lane is than the track between its ends (> 0: a short cut if drivable)
            "shortCutM": round(float((c.s[i[-1]] - c.s[i[0]]) % c.L) - polyline_length(p), 1),
            "minGapToTrackM":round(float(gap[k0:k1 + 1][gap[k0:k1 + 1] > -1e9].max() if k1 > k0 else 0.0), 2),
            "osmWays": [int(x[0]) if isinstance(x, list) else int(x) for x in spec["ways"]],
        }
        # the pit wall: midway between the track's edge and the lane's edge, where they are at least WALL_MIN_GAP_M apart
        wall = None
        ok = gap >= float(spec.get("wallMinGapM", WALL_MIN_GAP_M))
        if ok.any():
            idx = np.nonzero(ok)[0]
            # the longest run
            runs, cur = [], [idx[0]]
            for a in idx[1:]:
                if a == cur[-1] + 1:
                    cur.append(a)
                else:
                    runs.append(cur)
                    cur = [a]
            runs.append(cur)
            run = max(runs, key=len)
            a, b = run[0], run[-1]
            t_m, nl_m = tangents(np.vstack([c.main[-1:], c.main, c.main[:1]]))
            t_m, nl_m = t_m[1:-1], nl_m[1:-1]
            pts, offs = [], []
            for k in range(a, b + 1):
                j = i[k]
                edge = mw[k] / 2.0
                # beside the pit lane (1.5 m off its edge), or midway where the gap is narrower than 3 m
                off = edge + gap[k] - min(gap[k] * 0.5, WALL_LANE_GAP_M)
                q = c.main[j] + nl_m[j] * side * off
                pts.append(q)
                offs.append(off)
            pts = np.array(pts)
            wall = {"lane": lane["id"], "side": lane["side"], "heightM": WALL_HEIGHT_M,
                    "line": rnd(c.grid_of_local(pts)),
                    "s0": round(float(c.s[i[a]]), 1), "s1": round(float(c.s[i[b]]), 1),
                    "offsetFromCentreM": [round(float(min(offs)), 2), round(float(max(offs)), 2)],
                    "lengthM": round(polyline_length(pts), 1),
                    "gapsAt": "entry and exit only (the merges)",
                    "source": spec.get("wallSource", "derived: %.1f m off the pit lane's edge towards the track (midway "
                                       "where narrower), where the edges are at least %.1f m apart"
                                       % (WALL_LANE_GAP_M, WALL_MIN_GAP_M))}
            osm_share = self.osm_barrier_share(pts)
            if osm_share is not None:
                wall["osmBarrierWithin3mShare"] = round(osm_share, 2)
        return lane, wall

    def osm_barrier_share(self, pts: np.ndarray) -> float | None:
        lines = [self.c.geom(w) for w in self.c.ways.values()
                 if w.get("tags", {}).get("barrier") in ("wall", "fence", "guard_rail", "jersey_barrier", "yes", "concrete")
                 and w.get("geometry")]
        lines = [g for g in lines if len(g) > 1]
        if not lines:
            return None
        segs_a = np.vstack([g[:-1] for g in lines])
        segs_b = np.vstack([g[1:] for g in lines])
        near = []
        for q in pts[::3]:
            ab = segs_b - segs_a
            t = np.clip(((q - segs_a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-9), 0, 1)
            dd = np.hypot(*(segs_a + ab * t[:, None] - q).T)
            near.append(dd.min() < 3.0)
        return float(np.mean(near))

    # ---------------------------------------------------------------------------------------- buildings
    def footprint(self, wid: int, rel: bool = False) -> np.ndarray:
        if rel:
            outs = self.c.rel_outer(self.c.rels[wid])
            return max(outs, key=polygon_area)
        return self.c.geom(self.c.ways[wid])

    def building(self, item: dict) -> dict:
        c = self.c
        rel = item.get("relation", False)
        wid = int(item.get("way") or item.get("relation"))
        poly = self.footprint(wid, bool(item.get("relation")))
        tags = (c.rels if rel else c.ways)[wid].get("tags", {})
        rect = min_area_rect(poly)
        h = self.heights_in(poly)
        osm_h = None
        if tags.get("height"):
            try:
                osm_h = float(str(tags["height"]).split()[0])
            except ValueError:
                pass
        if osm_h is None and tags.get("building:levels"):
            try:
                osm_h = 3.2 * float(tags["building:levels"]) + 1.0
            except ValueError:
                pass
        if h and h["coverAbove2m"] > 0.4:
            height, hsrc = round(h["p85"], 1), "LIDAR (85th percentile of DSM - DTM inside the footprint)"
        elif osm_h:
            height, hsrc = round(osm_h, 1), "OSM height / building:levels"
        else:
            height, hsrc = float(item.get("heightM", 4.0)), "assumed"
        # facing: towards the nearest road (track or pit lane) from the centre
        cen = rect["centre"]
        roads = [c.main] + list(c.alts.values()) + self._lanes_local
        best = min((np.hypot(*(r - cen).T).min(), r) for r in roads)[1] if roads else c.main
        q = best[np.argmin(np.hypot(*(best - cen).T))]
        face = q - cen
        face = face / max(np.linalg.norm(face), 1e-9)
        dist_track = float(c.nearest_main(poly)[1].min())
        return {"kind": item["kind"], "name": item.get("name") or tags.get("name") or tags.get("building") or "",
                "osmWay" if not rel else "osmRelation": wid,
                "footprint": rnd(c.grid_of_local(poly[:-1] if np.allclose(poly[0], poly[-1]) else poly)),
                "rect": {"centre": rnd(c.grid_of_local(rect["centre"])), "lengthM": round(rect["length"], 1),
                         "depthM": round(rect["depth"], 1), "angleDeg": round(math.degrees(rect["angle"]), 1)},
                "areaM2": round(polygon_area(poly), 1),
                "heightM": height, "heightSource": hsrc,
                **({"lidar": {k: round(v, 2) if isinstance(v, float) else v for k, v in h.items()}} if h else {}),
                "facing": rnd(face, 3), "trackDistanceM": round(dist_track, 1),
                "sM": round(c.s_of(rect["centre"]), 1),
                "osmTags": {k: v for k, v in tags.items() if k in ("building", "name", "amenity", "building:levels",
                                                                  "height", "description", "leisure")}}

    # ---------------------------------------------------------------------------------------- areas
    def area(self, item: dict) -> dict:
        c = self.c
        if "polygon" in item:  # hand-drawn (local, from the overlays), e.g. where OSM has no paddock polygon
            poly = np.asarray(item["polygon"], dtype=float)
            tags = {}
            src = item.get("source", "")
        else:
            rel = bool(item.get("relation"))
            wid = int(item.get("way") or item.get("relation"))
            poly = self.footprint(wid, rel)
            tags = (c.rels if rel else c.ways)[wid].get("tags", {})
            src = item.get("source", f"OSM {'relation' if rel else 'way'} {wid}")
        if np.allclose(poly[0], poly[-1]):
            poly = poly[:-1]
        i, d, _ = c.nearest_main(poly)
        return {"kind": item.get("kind", "paddock"), "name": item.get("name") or tags.get("name") or "",
                "polygon": rnd(c.grid_of_local(poly)), "areaM2": round(polygon_area(np.vstack([poly, poly[:1]])), 1),
                "trackDistanceM": round(float(d.min()), 1), "source": src,
                **({"osmTags": {k: v for k, v in tags.items() if k in ("landuse", "amenity", "name", "parking", "surface", "access")}} if tags else {})}

    # ---------------------------------------------------------------------------------------- gantries and bridges
    def gantry(self, item: dict) -> dict:
        c = self.c
        route = item.get("route", "main")
        rp = c.main if route == "main" else c.alts[route]
        rw = c.main_w if route == "main" else c.alt_w[route]
        seg = np.hypot(*np.diff(np.vstack([rp, rp[:1]]) if route == "main" else rp, axis=0).T)
        rs = np.concatenate([[0.0], np.cumsum(seg)])[:len(rp)]
        span_from = None
        if "osmWay" in item:  # a bridge way crossing the road: where and how long
            g = self.c.geom(self.c.ways[int(item["osmWay"])])
            hits = seg_intersections(g, np.vstack([rp, rp[:1]]) if route == "main" else rp)
            if not hits:
                raise ValueError(f"OSM way {item['osmWay']} doesn't cross route {route}")
            j, u, pt = hits[0]
            s = float(rs[j] + u * seg[j])
            span_from = round(polyline_length(g), 1)
        elif "node" in item:
            n = c.nodes[int(item["node"])]
            s = c.s_of(c.local(n["lat"], n["lon"])) if route == "main" else None
        else:
            s = float(item["sM"])
        k = int(np.argmin(np.abs(rs - s)))
        p = rp[k]
        t = rp[(k + 1) % len(rp)] - rp[k - 1 if k > 0 else 0]
        t = t / np.linalg.norm(t)
        out = {"kind": item["kind"], "name": item.get("name", ""), "route": route, "sM": round(s, 1),
               "at": rnd(c.grid_of_local(p)), "roadDirection": rnd(t, 3),
               "roadWidthM": round(float(rw[k]), 2), "source": item["source"]}
        if span_from:
            out["osmSpanM"] = span_from
        # the LIDAR over the road around s: the structure's top (what the point cloud sees)
        nd = self.nd(np.vstack([p - 40, p + 40]))
        if nd is not None:
            nl = np.array([-t[1], t[0]])
            best = None
            for ds in np.arange(-6.0, 6.01, 1.0):
                q = p + t * ds + nl[None, :] * np.linspace(-0.45, 0.45, 19)[:, None] * rw[k]
                g = c.grid_of_local(q)
                h = nd.sample(g[:, 0], g[:, 1])
                hi = h[np.isfinite(h) & (h > 3.0)]
                cov = len(hi) / len(h)
                if best is None or cov > best[0]:
                    best = (cov, ds, float(np.median(hi)) if len(hi) else None, float(hi.max()) if len(hi) else None)
            out["lidar"] = {"coverAcrossRoad": round(best[0], 2), "atOffsetM": best[1],
                            "topMedianM": None if best[2] is None else round(best[2], 1),
                            "topMaxM": None if best[3] is None else round(best[3], 1)}
        for key in ("deckDepthM", "note", "spanM", "pairedWith"):
            if key in item:
                out[key] = item[key]
        return out

    # ---------------------------------------------------------------------------------------- run
    def derive(self) -> dict:
        sel = self.sel
        out = {"format": FORMAT, "slug": self.c.slug, "grid": "utm30" if self.c.recipe.get("dtm") in ("es-mdt05", "es-navarra") else "bng"}
        lanes, walls = [], []
        self._lanes_local = []
        for spec in sel.get("pitLanes", []):
            lane, wall = self.pit_lane(spec)
            lanes.append(lane)
            self._lanes_local.append(self.c.local_of_grid(lane["centreline"]))
            if wall:
                walls.append(wall)
        out["pitLanes"], out["pitWalls"] = lanes, walls
        out["buildings"] = [self.building(b) for b in sel.get("buildings", [])]
        out["grandstands"] = [self.building({**b, "kind": "grandstand"}) for b in sel.get("grandstands", [])]
        out["paddocks"] = [self.area(a) for a in sel.get("paddocks", [])]
        out["gantries"] = [self.gantry(g) for g in sel.get("gantries", [])]
        return out

    def write(self, derived: dict) -> None:
        res = {"format": derived["format"], "slug": derived["slug"], "grid": derived["grid"],
               "generatedBy": "tools/real_tracks_data/pits.py from ../" + self.path.name + " (do not edit)",
               "sources": self.doc.get("sources", {})}
        for k in DERIVED:
            res[k] = derived[k]
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_text(dumps(res) + "\n", encoding="utf-8", newline="\n")


def dumps(doc: dict) -> str:
    """JSON with one line per point list (a [x, y] pair, or a list of numbers, stays on one line)."""
    import re  # noqa: PLC0415

    text = json.dumps(doc, indent=1, ensure_ascii=False)
    num = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
    return re.sub(r"\[\s*(" + num + r"(?:,\s*" + num + r")*)\s*\]",
                  lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",")) + "]", text)


def candidates(p: Pits, maxd: float = 250.0) -> None:
    c = p.c
    for wid, w in c.ways.items():
        t = w.get("tags", {})
        if not ("building" in t or t.get("amenity") == "parking" or t.get("landuse") or t.get("highway") == "raceway"
                or t.get("bridge") or t.get("man_made") == "bridge" or t.get("leisure")):
            continue
        if wid in c.lap_ways:
            continue
        g = c.geom(w)
        if len(g) < 2:
            continue
        i, d, lat = c.nearest_main(g)
        if d.min() > maxd:
            continue
        k = int(np.argmin(d))
        print(wid, "s", round(float(c.s[i[k]])), "d", round(float(d.min())), "lat", round(float(lat[k])),
              {k2: v for k2, v in t.items() if not k2.startswith(("source", "addr", "fhrs", "ref"))})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--plots", action="store_true")
    ap.add_argument("--candidates", action="store_true")
    a = ap.parse_args(argv)
    p = Pits(a.slug)
    if a.candidates:
        candidates(p)
        return 0
    d = p.derive()
    p.write(d)
    print(f"{p.out}: {len(d['pitLanes'])} pit lanes, {len(d['pitWalls'])} walls, {len(d['buildings'])} buildings, "
          f"{len(d['grandstands'])} grandstands, {len(d['paddocks'])} paddocks, {len(d['gantries'])} gantries")
    for lane in d["pitLanes"]:
        print("  lane", lane["id"], lane["mode"], "entry s", lane["entry"]["sM"], "exit s", lane["exit"]["sM"],
              "length", lane["lengthM"], "side", lane["side"], "short cut", lane["shortCutM"])
    for wall in d["pitWalls"]:
        print("  wall s", wall["s0"], "-", wall["s1"], "len", wall["lengthM"], "offset", wall["offsetFromCentreM"],
              "osm barrier share", wall.get("osmBarrierWithin3mShare"))
    for b in d["buildings"] + d["grandstands"]:
        print("  ", b["kind"], b["name"], "h", b["heightM"], b["heightSource"][:5], "rect", b["rect"]["lengthM"], "x",
              b["rect"]["depthM"], "track", b["trackDistanceM"])
    for g in d["gantries"]:
        print("  gantry", g["kind"], g["name"], "s", g["sM"], g.get("lidar"))
    if a.plots:
        from pits_plot import overlay  # noqa: PLC0415
        doc = json.loads(p.out.read_text(encoding="utf-8"))
        c = p.c
        pts = [c.main[:1]]
        for lane in doc["pitLanes"]:
            pts.append(c.local_of_grid(lane["centreline"]))
        for b in doc["buildings"]:
            pts.append(c.local_of_grid(b["footprint"]))
        allp = np.vstack(pts)
        centre = (allp.min(0) + allp.max(0)) / 2
        half = float(max((allp.max(0) - allp.min(0)).max() / 2 + 40, 120))
        out = LOCAL / "audit" / a.slug
        overlay(c, centre, half, out / "pits-overview.png", f"{a.slug}: pits, paddock, buildings, gantries", doc, labels=False)
        for k, lane in enumerate(doc["pitLanes"]):
            for end in ("entry", "exit"):
                q = c.local_of_grid(lane[end]["point"])
                overlay(c, q, 70, out / f"pits-{lane['id']}-{end}.png", f"{a.slug}: pit lane {end} (s {lane[end]['sM']})", doc, labels=False)
        for k, g in enumerate(doc["gantries"]):
            q = c.local_of_grid(g["at"])
            overlay(c, q, 60, out / f"pits-gantry-{k}.png", f"{a.slug}: {g['kind']} {g['name']} s {g['sM']}", doc, labels=False)
        if doc["grandstands"]:
            allg = np.vstack([c.local_of_grid(g["footprint"]) for g in doc["grandstands"]])
            cen = (allg.min(0) + allg.max(0)) / 2
            overlay(c, cen, float(max((allg.max(0) - allg.min(0)).max() / 2 + 40, 100)), out / "pits-grandstands.png",
                    f"{a.slug}: grandstands", doc, labels=False)
        print("plots in", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
