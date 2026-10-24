"""Shared helpers for the pits survey (pits.py): a circuit's recipe, local frame, committed sample routes, cached OSM
pits data, projection into the sample's local frame and the main lap's s (distance from the start/finish line)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import import_real_track as irt  # noqa: E402

LOCAL = Path(r"E:\Source\wreckfest\.local")
SLUGS = ["cadwellpark", "brandshatch", "oultonpark", "snetterton", "doningtonpark", "bedfordautodrome", "navarra",
         "anglesey"]


class Circuit:
    def __init__(self, slug: str):
        self.slug = slug
        self.recipe = json.loads((ROOT / "tools" / "real_tracks" / f"{slug}.json").read_text(encoding="utf-8"))
        irt.set_projection(self.recipe)
        fr = json.loads((LOCAL / "realtracks" / slug / "frame.json").read_text(encoding="utf-8"))["frame"]
        self.cE, self.cN, self.k = fr["centreE"], fr["centreN"], fr["scale"]
        self.sample = json.loads((ROOT / "geom" / "tests" / "samples" / f"{slug}.json").read_text(encoding="utf-8"))
        imp = self.sample["sketch"]["imported"]
        self.imp = imp
        self.main = np.array([[q["x"], q["y"]] for q in imp["centreline"]])
        self.main_w = np.array([q.get("widthM", self.sample["options"]["widthM"]) for q in imp["centreline"]])
        self.main_z = np.array([q["z"] for q in imp["centreline"]])
        self.alts = {a["id"]: np.array([[q["x"], q["y"]] for q in a["centreline"]]) for a in imp.get("altLayouts", [])}
        self.alt_w = {a["id"]: np.array([q.get("widthM", self.sample["options"]["widthM"]) for q in a["centreline"]])
                      for a in imp.get("altLayouts", [])}
        seg = np.hypot(*np.diff(np.vstack([self.main, self.main[:1]]), axis=0).T)
        self.s = np.concatenate([[0.0], np.cumsum(seg)[:-1]])
        self.L = float(seg.sum())
        # where the importer cached this circuit's downloads (the DTM tiles): the MSV imports used realtracks/<slug>
        self.cache = LOCAL / "realtracks" / slug
        if not (self.cache / "osm.json").exists():
            self.cache = irt.DEFAULT_CACHE_ROOT / irt.recipe_slug(self.recipe)
        p = LOCAL / "realtracks" / slug / "osm-pits.json"
        self.osm = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"elements": []}
        self.ways = {e["id"]: e for e in self.osm["elements"] if e["type"] == "way"}
        self.rels = {e["id"]: e for e in self.osm["elements"] if e["type"] == "relation"}
        self.nodes = {e["id"]: e for e in self.osm["elements"] if e["type"] == "node"}
        self.lap_ways = set(self._lap_ids(self.recipe["lap"]))
        for a in self.recipe.get("altLayouts", []):
            self.lap_ways |= set(self._lap_ids(a["lap"]))
        self.lap_ways |= set(self.recipe.get("extraWays", []))

    @staticmethod
    def _lap_ids(lap) -> list[int]:
        return [int(x[0]) if isinstance(x, list) else int(x) for x in lap]

    # ---------------------------------------------------------------- projection
    def grid(self, lat: float, lon: float) -> tuple[float, float]:
        return irt.to_grid(lat, lon)

    def local(self, lat: float, lon: float) -> np.ndarray:
        e, n = irt.to_grid(lat, lon)
        return np.array([(e - self.cE) / self.k, (n - self.cN) / self.k])

    def local_of_grid(self, en: np.ndarray) -> np.ndarray:
        return (np.asarray(en, dtype=float) - [self.cE, self.cN]) / self.k

    def grid_of_local(self, xy: np.ndarray) -> np.ndarray:
        return np.asarray(xy, dtype=float) * self.k + [self.cE, self.cN]

    def geom(self, way: dict) -> np.ndarray:
        return np.array([self.local(q["lat"], q["lon"]) for q in way.get("geometry", []) if q])

    def rel_outer(self, rel: dict) -> list[np.ndarray]:
        out = []
        for m in rel.get("members", []):
            if m.get("type") == "way" and m.get("role", "outer") in ("outer", "") and m.get("geometry"):
                out.append(np.array([self.local(q["lat"], q["lon"]) for q in m["geometry"] if q]))
        return out

    # ---------------------------------------------------------------- along the main lap
    def nearest_main(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Per point: index of the nearest main sample, distance, signed lateral offset (+ left of driving)."""
        xy = np.atleast_2d(xy)
        d = np.hypot(xy[:, None, 0] - self.main[None, :, 0], xy[:, None, 1] - self.main[None, :, 1])
        i = d.argmin(1)
        t = np.roll(self.main, -1, 0) - np.roll(self.main, 1, 0)
        t = t / np.linalg.norm(t, axis=1)[:, None]
        nl = np.column_stack([-t[:, 1], t[:, 0]])
        lat = ((xy - self.main[i]) * nl[i]).sum(1)
        return i, d[np.arange(len(xy)), i], lat

    def s_of(self, xy) -> float:
        i, _, _ = self.nearest_main(np.atleast_2d(xy))
        return float(self.s[i[0]])

    def point_at(self, s: float) -> tuple[np.ndarray, np.ndarray]:
        """Point and unit tangent on the main lap at s."""
        s = s % self.L
        q = np.vstack([self.main, self.main[:1]])
        ss = np.concatenate([self.s, [self.L]])
        p = np.array([np.interp(s, ss, q[:, 0]), np.interp(s, ss, q[:, 1])])
        i = int(np.searchsorted(ss, s, side="right") - 1) % len(self.main)
        t = self.main[(i + 1) % len(self.main)] - self.main[i]
        return p, t / np.linalg.norm(t)

    def all_routes(self) -> list[np.ndarray]:
        return [self.main] + list(self.alts.values())


def polygon_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def polyline_length(p: np.ndarray) -> float:
    return float(np.hypot(*np.diff(p, axis=0).T).sum()) if len(p) > 1 else 0.0


def seg_intersections(a: np.ndarray, b: np.ndarray) -> list[tuple[int, float, np.ndarray]]:
    """Where polyline a crosses polyline b: (segment index in b, fraction, point)."""
    out = []
    for i in range(len(a) - 1):
        p, r = a[i], a[i + 1] - a[i]
        q, s_ = b[:-1], b[1:] - b[:-1]
        den = r[0] * s_[:, 1] - r[1] * s_[:, 0]
        with np.errstate(all="ignore"):
            t = ((q[:, 0] - p[0]) * s_[:, 1] - (q[:, 1] - p[1]) * s_[:, 0]) / den
            u = ((q[:, 0] - p[0]) * r[1] - (q[:, 1] - p[1]) * r[0]) / den
        hit = np.nonzero((np.abs(den) > 1e-12) & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1))[0]
        for j in hit:
            out.append((int(j), float(u[j]), p + r * t[j]))
    return out


def deg(rad: float) -> float:
    return math.degrees(rad)
