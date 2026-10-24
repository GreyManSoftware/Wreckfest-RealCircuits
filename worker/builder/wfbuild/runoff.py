"""Measured run-off of an imported track (``sketch.imported.runoff``): gravel traps, paved aprons, grass and the
barrier beyond them, per route, side and sample (pure numpy).

The zones come from tools/real_tracks_data/measure_runoff.py (open LIDAR, OSM and published sources). The plan
turns them into per-sample arrays along every route side and into a 1 m raster over the terrain (nearest road
sample, its side and the distance from its edge), which the geometry (terrain faces, the trap's dip, the wall
plan), the generated maps and the scenery planner share:

- **gravel trap**: from ``startM`` to ``startM + depthM`` beyond the road edge, never under a kerb or green strip,
  always in front of the zone's barrier; terrain faces there get the trap material with the stock ``#slowdown``
  surface, as the stock sandpit sand banks use it (surfacelist.srfl: the slowdown tyre model, twice gravel's
  rolling resistance; ``#slowdownsand`` is listed too but bgeometry has no surface settings for it), and the
  ground dips TRAP_DIP_M inside it;
- **paved apron**: from the edge out to ``tarmacM`` (``#asphalt``, an older tarmac texture than the track's);
- **barrier**: ``armco`` / ``tyres`` / ``concrete`` at ``offsetM`` (a ``bank`` or ``none`` builds no wall);
  ``fence`` adds a debris fence on it. The colour is black unless a source confirms red/white.
"""
from __future__ import annotations

import math

import numpy as np

from . import config as C

SURFACE = {"grass": 0, "gravel": 1, "tarmac": 2}
BARRIERS = ("none", "armco", "tyres", "concrete", "bank")
HARD = ("armco", "tyres", "concrete")
RASTER_M = 1.0
REACH_M = 62.0              # the raster covers this far beyond the road edges


class RunoffPlan:
    def __init__(self, geo):
        imp = getattr(geo, "imported", None) or {}
        zones = list(imp.get("runoff") or [])
        self.active = bool(zones)
        self.geo = geo
        self.side: dict[tuple[int, int], dict] = {}
        if not self.active:
            return
        for r in geo.routes:
            for side in (1, -1):
                self.side[(r.index, side)] = {
                    "zone": np.full(r.n, -1, dtype=np.int32),
                    "surface": np.zeros(r.n, dtype=np.int8),
                    "tarmac": np.zeros(r.n), "g0": np.zeros(r.n), "g1": np.zeros(r.n),
                    "barrier": np.zeros(r.n, dtype=np.int8), "offset": np.full(r.n, np.nan),
                    "fence": np.zeros(r.n, dtype=bool), "redwhite": np.zeros(r.n, dtype=bool)}
        self.zones = zones
        for zi, z in enumerate(zones):
            r = geo.by_id.get(z["route"])
            if r is None:
                continue
            a = self.side[(r.index, 1 if z["side"] == "left" else -1)]
            f, t = float(z["fromS"]), float(z["toS"])
            if r.closed and t < f:
                m = (r.s >= f) | (r.s < t)
            else:
                m = (r.s >= f) & (r.s < t)
            if not m.any():
                continue
            a["zone"][m] = zi
            a["surface"][m] = SURFACE[z["surface"]]
            a["tarmac"][m] = float(z.get("tarmacM", 0.0)) if z["surface"] != "grass" else 0.0
            if z["surface"] == "gravel":
                a["g0"][m] = float(z.get("startM", 0.0))
                a["g1"][m] = float(z.get("startM", 0.0)) + float(z.get("depthM", 0.0))
            b = z["barrier"] if z.get("offsetM") is not None or z["barrier"] == "none" else "none"
            a["barrier"][m] = BARRIERS.index(b)
            if b != "none":
                a["offset"][m] = float(z["offsetM"])
            a["fence"][m] = bool(z.get("fence", False)) and b in HARD
            a["redwhite"][m] = z.get("colour", "black") == "redwhite"
        self._raster = None
        # dividers and spectator barriers where the data has no barrier near: the circuit's most common measured
        # type (plain black tyres where it has none, e.g. Bedford's open airfield)
        length = {b: 0.0 for b in HARD}
        for z in zones:
            if z["barrier"] in HARD:
                length[z["barrier"]] += max(float(z["toS"]) - float(z["fromS"]), 0.0)
        self.default_type = max(length, key=length.get) if any(length.values()) else "tyres"

    # ------------------------------------------------------------------------------------- per sample
    def finalise(self) -> None:
        """Clamp against the kerb plan (no trap under a kerb or green strip, tarmac aprons start at the edge)
        and keep every hard barrier behind its gravel trap."""
        if not self.active:
            return
        plan = self.geo.kerb_plan()
        for (ri, side), a in self.side.items():
            kw, gw = plan[(ri, side)]
            inner = kw + gw + C.TRAP_KERB_CLEAR
            trap = a["surface"] == 1
            a["g0"] = np.where(trap, np.maximum(a["g0"], inner), a["g0"])
            short = trap & (a["g1"] - a["g0"] < 2.0)
            a["surface"][short] = 0
            a["g1"][short] = a["g0"][short]
            hard = np.isin(a["barrier"], [BARRIERS.index(b) for b in HARD])
            trap = a["surface"] == 1
            a["offset"] = np.where(hard & trap, np.maximum(a["offset"], a["g1"] + C.TRAP_BARRIER_GAP), a["offset"])
            ap = a["surface"] == 2
            a["offset"] = np.where(hard & ap, np.maximum(a["offset"], a["tarmac"] + C.TRAP_BARRIER_GAP), a["offset"])
            # zone-to-zone steps in the measured offset ease over ~BARRIER_SMOOTH_M (a barrier line doesn't jump a
            # few metres sideways), still behind every trap and apron
            r = self.geo.routes[ri]
            k = max(1, int(round(C.BARRIER_SMOOTH_M / max(float(np.median(r.seg_len())), 0.5) / 2)))
            # a barrier line runs straight on: short excursions (another object read as the barrier for a zone or
            # two) give way to the running median over BARRIER_MEDIAN_M of the measured offsets
            km = max(1, int(round(C.BARRIER_MEDIAN_M / max(float(np.median(r.seg_len())), 0.5) / 2)))
            vals = np.where(hard, a["offset"], np.nan)
            pad = np.concatenate([vals[-km:], vals, vals[:km]]) if r.closed else np.pad(vals, km, constant_values=np.nan)
            win = np.lib.stride_tricks.sliding_window_view(pad, 2 * km + 1)
            with np.errstate(all="ignore"):
                cnt = np.sum(np.isfinite(win), axis=1)
                med = np.where(cnt > 0, np.nanmedian(np.where(cnt[:, None] > 0, win, 0.0), axis=1), np.nan)
            off = np.where(hard & np.isfinite(med), med, np.where(hard, a["offset"], 0.0))
            w = hard.astype(np.float64)
            ker = np.ones(2 * k + 1)
            if r.closed:
                so = np.convolve(np.concatenate([off[-k:], off, off[:k]]), ker, "valid")
                sw = np.convolve(np.concatenate([w[-k:], w, w[:k]]), ker, "valid")
            else:
                so = np.convolve(np.pad(off, k), ker, "valid")
                sw = np.convolve(np.pad(w, k), ker, "valid")
            sm = np.where(sw > 0, so / np.maximum(sw, 1e-9), np.nan)
            end = np.maximum(np.where(trap, a["g1"], 0.0), np.where(ap, a["tarmac"], 0.0))
            a["offset"] = np.where(hard, np.maximum(sm, np.where(end > 0, end + C.TRAP_BARRIER_GAP, 0.0)), a["offset"])

    def barrier(self, ri: int, side: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(type names per sample, offset from the edge per sample (nan without one), fence flags)."""
        a = self.side[(ri, side)]
        return np.array(BARRIERS)[a["barrier"]], a["offset"], a["fence"]

    def run_off_end(self, ri: int, side: int) -> np.ndarray:
        """How far beyond the edge the measured run-off (apron or trap) reaches per sample."""
        a = self.side[(ri, side)]
        return np.maximum(a["tarmac"], np.where(a["surface"] == 1, a["g1"], 0.0))

    # ------------------------------------------------------------------------------------- raster
    def raster(self):
        """1 m raster over the terrain extent: code 0 grass/none, 1 gravel trap, 2 paved apron; ``band`` marks the
        ground between a road edge and its barrier (or the end of its run-off), where nothing may stand."""
        if self._raster is not None:
            return self._raster
        g = self.geo
        x0, x1, y0, y1 = g.extent
        nx, ny = int(math.ceil((x1 - x0) / RASTER_M)) + 1, int(math.ceil((y1 - y0) / RASTER_M)) + 1
        dist = np.full((ny, nx), np.inf, dtype=np.float32)
        near = np.full((ny, nx), -1, dtype=np.int32)
        lat = np.zeros((ny, nx), dtype=np.float32)
        xs = x0 + np.arange(nx) * RASTER_M
        ys = y0 + np.arange(ny) * RASTER_M
        sel = np.concatenate([g.svalid[r.index] for r in g.routes])
        idx = np.nonzero(sel)[0]
        for k in idx:
            cx, cy = g.index.xy[k]
            rad = REACH_M + float(g.index.hw[k])
            i0, i1 = max(0, int((cx - rad - x0) / RASTER_M)), min(nx, int((cx + rad - x0) / RASTER_M) + 2)
            j0, j1 = max(0, int((cy - rad - y0) / RASTER_M)), min(ny, int((cy + rad - y0) / RASTER_M) + 2)
            if i0 >= i1 or j0 >= j1:
                continue
            dx = xs[None, i0:i1] - cx
            dy = ys[j0:j1, None] - cy
            dd = np.hypot(dx, dy) - float(g.index.hw[k])
            win = dist[j0:j1, i0:i1]
            m = dd < win
            win[m] = dd[m]
            near[j0:j1, i0:i1][m] = k
            nl = g.index.nl[k]
            lat[j0:j1, i0:i1][m] = (dx * nl[0] + dy * nl[1])[m]
        code = np.zeros((ny, nx), dtype=np.int8)
        band = np.zeros((ny, nx), dtype=bool)
        ok = (near >= 0) & (dist >= 0) & (dist <= REACH_M)
        kk = near[ok]
        ri, si = g.index.ri[kk], g.index.si[kk]
        sd = np.where(lat[ok] >= 0, 1, -1)
        e = dist[ok]
        c = np.zeros(len(kk), dtype=np.int8)
        b = np.zeros(len(kk), dtype=bool)
        for (r_i, side), a in self.side.items():
            m = (ri == r_i) & (sd == side)
            if not m.any():
                continue
            s_i = si[m]
            em = e[m]
            surf = a["surface"][s_i]
            trap = (surf == 1) & (em >= a["g0"][s_i]) & (em <= a["g1"][s_i])
            apron = (a["tarmac"][s_i] > 0) & (em <= a["tarmac"][s_i])
            cm = np.where(trap, 1, np.where(apron, 2, 0)).astype(np.int8)
            off = a["offset"][s_i]
            reach = np.where(np.isfinite(off), off, self.run_off_end(r_i, side)[s_i])
            c[m] = cm
            b[m] = em <= reach + 0.5
        code[ok] = c
        band[ok] = b
        self._raster = {"x0": x0, "y0": y0, "nx": nx, "ny": ny, "code": code, "band": band, "dist": dist}
        return self._raster

    def at(self, xy, what: str = "code") -> np.ndarray:
        R = self.raster()
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        i = np.clip(np.round((xy[:, 0] - R["x0"]) / RASTER_M).astype(int), 0, R["nx"] - 1)
        j = np.clip(np.round((xy[:, 1] - R["y0"]) / RASTER_M).astype(int), 0, R["ny"] - 1)
        return R[what][j, i]

    def soft(self, xy, code: int, radius: float) -> np.ndarray:
        """Fraction of the raster cells within ``radius`` (a box) that carry ``code``: 0 outside, 1 well inside."""
        R = self.raster()
        key = ("soft", code, radius)
        if key not in R:
            m = (R["code"] == code).astype(np.float64)
            r = max(1, int(round(radius / RASTER_M)))
            c = np.pad(m, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
            n = 2 * r + 1
            p = np.pad(c, ((r, r), (r, r)), mode="edge")
            s = p[n:, n:] - p[:-n, n:] - p[n:, :-n] + p[:-n, :-n]
            R[key] = (s / (n * n))[: m.shape[0], : m.shape[1]]
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        i = np.clip(np.round((xy[:, 0] - R["x0"]) / RASTER_M).astype(int), 0, R["nx"] - 1)
        j = np.clip(np.round((xy[:, 1] - R["y0"]) / RASTER_M).astype(int), 0, R["ny"] - 1)
        return R[key][j, i]

    def grid(self, n: int, frame: tuple[float, float, float], what: str = "code") -> np.ndarray:
        """The raster resampled to an n x n map over ``frame`` (rows from the south)."""
        x0, y0, size = frame
        c = (np.arange(n) + 0.5) / n * size
        gx, gy = np.meshgrid(x0 + c, y0 + c)
        return self.at(np.stack([gx.ravel(), gy.ravel()], axis=1), what).reshape(n, n)

    def dip(self, xy) -> np.ndarray:
        """How far the ground sinks at xy: TRAP_DIP_M well inside a gravel trap, easing to 0 at its edge."""
        if not self.active:
            return np.zeros(len(np.asarray(xy).reshape(-1, 2)))
        f = self.soft(xy, 1, C.TRAP_DIP_EASE_M)
        return C.TRAP_DIP_M * np.clip((f - 0.5) * 2.0, 0.0, 1.0)

    # ------------------------------------------------------------------------------------- report
    def summary(self) -> dict:
        """Share of each surface and barrier type along every route side (sample counts), for the manifest."""
        out = {}
        if not self.active:
            return out
        for (ri, side), a in self.side.items():
            r = self.geo.routes[ri]
            n = max(r.n, 1)
            out[f"{r.id}:{'left' if side > 0 else 'right'}"] = {
                "gravel": round(float((a["surface"] == 1).sum()) / n, 3),
                "tarmac": round(float((a["surface"] == 2).sum()) / n, 3),
                "grass": round(float((a["surface"] == 0).sum()) / n, 3),
                **{b: round(float((a["barrier"] == k).sum()) / n, 3) for k, b in enumerate(BARRIERS)}}
        return out
