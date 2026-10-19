"""Backdrop beyond the terrain, as stock tracks have it (pure numpy): a far-ground ring (#noshadow filler with
gentle hills), a #horizon cylinder with the environment's vista texture, and #xtree background tree cards (or
a city skyline for industrial). No collision. Built in TrackModel coordinates after the placement transform is
known, so every vertex stays within +-BACKDROP_LIMIT once the track is moved (manifest ``transform``).

Shape: rays from the terrain centre. Along each ray the far ground runs from just inside the terrain edge to
past the horizon; the horizon sits HORIZON_OFFSET beyond the terrain edge, or closer where the +-2000 m limit
leaves less room.
"""
from __future__ import annotations

import math

import numpy as np

from . import config as C
from . import envs
from .geometry import Mesh
from .noise import periodic_noise, seed_for


def smooth(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def _rot(x, y, deg):
    c, s = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[deg]
    return x * c - y * s, x * s + y * c


class Backdrop:
    def __init__(self, geo, rot: int = 0, shift: float = 0.0, segments: int = 192):
        self.geo = geo
        self.slug = geo.model["slug"]
        self.env_name = envs.env_name(geo.options.get("environment"))
        self.e = envs.env(self.env_name)
        x0, x1, y0, y1 = geo.extent
        self.c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        self.hx, self.hy = (x1 - x0) / 2, (y1 - y0) / 2
        self.n = segments
        th = np.arange(segments) / segments * 2 * math.pi
        self.u = np.stack([np.cos(th), np.sin(th)], axis=1)
        with np.errstate(divide="ignore"):
            self.d_rect = np.minimum(self.hx / np.maximum(np.abs(self.u[:, 0]), 1e-9),
                                     self.hy / np.maximum(np.abs(self.u[:, 1]), 1e-9))
        # how far each ray may go before leaving +-limit in scene coordinates (scene = rot(model) + (0, shift))
        lim = C.BACKDROP_LIMIT
        cs = np.array(_rot(self.c[0], self.c[1], rot)) + np.array([0.0, shift])
        us = np.stack(_rot(self.u[:, 0], self.u[:, 1], rot), axis=1)
        tmax = np.full(segments, np.inf)
        for ax in (0, 1):
            with np.errstate(divide="ignore", invalid="ignore"):
                t_pos = np.where(us[:, ax] > 1e-9, (lim - cs[ax]) / us[:, ax], np.inf)
                t_neg = np.where(us[:, ax] < -1e-9, (-lim - cs[ax]) / us[:, ax], np.inf)
            tmax = np.minimum(tmax, np.minimum(t_pos, t_neg))
        self.room = np.maximum(tmax - self.d_rect, 0.0)
        self.h_off = np.clip(np.minimum(C.HORIZON_OFFSET, self.room - 20.0), 15.0, None)
        self.h_off = np.minimum(self.h_off, np.maximum(self.room - 2.0, 1.0))
        # ground height along the terrain edge (the berm top) and the general level
        edge = self.c + self.u * (self.d_rect[:, None] - 0.5)
        self.edge_z = geo.terrain(np.column_stack([edge, np.zeros(segments)]))
        self.base_z = float(np.median(self.edge_z))
        self.hills = periodic_noise(seed_for(self.slug, "hills"), segments, 7)
        self.hills2 = periodic_noise(seed_for(self.slug, "hills2"), segments, 12)
        self.phase = 0.5 + 0.5 * periodic_noise(seed_for(self.slug, "hills-phase"), segments, 4)
        self.hill_amp = tuple(float(v) for v in self.e.get("farHills", (2.0, 12.0)))
        self.rng = np.random.default_rng(seed_for(self.slug, "backdrop"))
        # an imported track's real ground continues into the far ground as far as its heightfield reaches
        self.real_extent = getattr(geo.ground, "extent", None) if getattr(geo, "real_ground", False) else None

    def point(self, k: int, off: float) -> np.ndarray:
        return self.c + self.u[k] * (self.d_rect[k] + off)

    def ground_z(self, k: int, t: float) -> float:
        """Far-ground height on ray k at fraction t of the way to the horizon (t < 0: under the terrain edge).

        Rolling hills: they start at the terrain's edge berm, stay low close by (so they don't hide the #horizon
        vista from the track) and grow towards the horizon, where they cover the vista's lower edge."""
        if t < 0:
            return float(self.edge_z[k]) - 0.6
        if t == 0:
            return float(self.edge_z[k]) - 0.05
        near_amp, far_amp = self.hill_amp
        amp = near_amp + (far_amp - near_amp) * t * t
        roll = 0.55 + 0.45 * math.sin(2.0 * math.pi * (2.3 * t + self.phase[k]))
        z_hill = self.base_z + 1.0 + amp * (0.5 + 0.5 * self.hills[k]) * roll + 3.0 * self.hills2[k] * t
        blend = smooth(min(1.0, t / 0.1))
        z = float(self.edge_z[k] + 0.4) * (1 - blend) + z_hill * blend
        if self.real_extent is not None:
            p = self.point(k, min(t * self.h_off[k], self.room[k]))
            x0, x1, y0, y1 = self.real_extent
            inside = min(p[0] - x0, x1 - p[0], p[1] - y0, y1 - p[1])
            w = smooth(inside / C.REAL_GROUND_FADE)
            if w > 0:
                z = float(self.geo.ground(p[None, :])[0]) * w + z * (1 - w)
        return z

    def ground_at(self, k: float, t: float) -> float:
        """ground_z at a fractional ray index (linear between rays)."""
        k0 = int(math.floor(k)) % self.n
        f = k - math.floor(k)
        return self.ground_z(k0, t) * (1 - f) + self.ground_z((k0 + 1) % self.n, t) * f

    # ----------------------------------------------------------------------------------- meshes
    def far_ground(self) -> Mesh:
        m = Mesh("backdrop_ground#lofi", "", kind="backdrop")
        ts = [-1.0, 0.0, 0.03, 0.07, 0.12, 0.18, 0.26, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.93, 1.0, 1.06]
        rings = []
        for t in ts:
            ring = []
            for k in range(self.n):
                if t < 0:
                    off = -3.0
                elif t == 0:
                    off = 0.5
                else:
                    off = min(t * self.h_off[k], self.room[k])
                p = self.point(k, off)
                ring.append(m.vert((p[0], p[1], self.ground_z(k, min(t, 1.0)))))
            rings.append(ring)
        tile = C.TEX_TILE["far"]
        for a in range(len(rings) - 1):
            for k in range(self.n):
                k2 = (k + 1) % self.n
                ids = (rings[a][k], rings[a][k2], rings[a + 1][k2], rings[a + 1][k])
                m.oriented_face(ids, "far_ground", np.array([0.0, 0.0, 1.0]),
                                [(m.verts[i][0] / tile, m.verts[i][1] / tile) for i in ids])
        return m

    def horizon(self) -> Mesh:
        m = Mesh("backdrop_horizon#lofi", "", kind="backdrop")
        n = C.HORIZON_SEGMENTS
        step = self.n // n
        ks = list(range(0, self.n, step))
        bottom, top = [], []
        for k in ks:
            off = float(self.h_off[k])
            dist = float(self.d_rect[k] + off)
            scale = max(dist / C.HORIZON_SCALE_M, 0.25)
            p = self.point(k, off)
            bottom.append(m.vert((p[0], p[1], self.base_z - 60.0 * scale)))
            top.append(m.vert((p[0], p[1], self.base_z + 67.0 * scale)))
        rep = C.HORIZON_REPEATS
        for a in range(len(ks)):
            b = (a + 1) % len(ks)
            u0 = a / len(ks) * rep
            u1 = (a + 1) / len(ks) * rep
            ids = (bottom[a], bottom[b], top[b], top[a])
            mid = self.u[ks[a]]
            m.oriented_face(ids, "horizon", np.array([-mid[0], -mid[1], 0.0]),
                            [(u0, 0.0), (u1, 0.0), (u1, 1.0), (u0, 1.0)])
        return m

    def cards(self) -> tuple[list[Mesh], int]:
        """Background tree (or city skyline) cards: bands of 8 m quads on the far-ground hills just in front of
        the #horizon ring (environments.json "cardBands": t = fraction of the way to the horizon, coverage,
        height). A full band is one closed ring (no ends); partial bands are staggered strips whose ends sink
        into the hills, and every card's foot is buried in the ground, so no card reads as a rectangle."""
        e = self.e
        trees = list(e.get("treeCards", []))
        skyline = list(e.get("skylineCards", []))
        meshes = []
        idx = 0
        for bi, band in enumerate(e.get("cardBands", [])):
            texs, tex0 = (skyline, len(trees)) if band.get("skyline") else (trees, 0)
            if not texs:
                continue
            t, cov = float(band["t"]), float(band.get("coverage", 1.0))
            hmin, hmax = (float(v) for v in band.get("height", C.TREE_CARD_HEIGHT))
            if cov >= 0.999:
                pick = int(self.rng.integers(len(texs)))
                aspect = float(envs.table()["cardAspect"].get(texs[pick], 2.0))
                m = self._strip(f"backdrop_cards_{idx:02d}", 0, self.n, t, (hmin + hmax) / 2.0, f"card_{tex0 + pick}",
                                aspect, closed=True)
                if m is not None:
                    meshes.append(m)
                    idx += 1
                continue
            k = int(self.rng.integers(self.n)) + bi * 7
            walked = 0
            while walked < self.n:
                span = int(self.rng.integers(5, 11))  # rays per strip (~200-450 m)
                if self.rng.random() < cov:
                    pick = int(self.rng.integers(len(texs)))
                    aspect = float(envs.table()["cardAspect"].get(texs[pick], 2.0))
                    tj = t + float(self.rng.uniform(-0.03, 0.03))  # staggered depth
                    m = self._strip(f"backdrop_cards_{idx:02d}", k, span, tj, float(self.rng.uniform(hmin, hmax)),
                                    f"card_{tex0 + pick}", aspect)
                    if m is not None:
                        meshes.append(m)
                        idx += 1
                gap = int(self.rng.integers(1, 4))
                k = (k + span + gap) % self.n
                walked += span + gap
        return meshes, len(trees) + len(skyline)

    def _strip(self, name: str, k0: int, span: int, frac: float, height: float, mat: str,
               aspect: float = 2.0, closed: bool = False) -> Mesh | None:
        pts = []
        last = span if closed else span + 1
        for j in range(last):
            k = (k0 + j) % self.n
            off = min(frac * self.h_off[k], self.room[k] - 5.0)
            if off < 20.0:
                return None
            pts.append((self.point(k, off), k0 + j, off / max(self.h_off[k], 1e-6)))
        P = np.array([p for p, _, _ in pts])
        if closed:
            P = np.vstack([P, P[:1]])
            pts = pts + [(pts[0][0], k0 + span, pts[0][2])]
        # resample the polyline into ~8 m quads
        seg = np.hypot(*np.diff(P, axis=0).T)
        L = float(seg.sum())
        nq = max(1, int(round(L / C.TREE_CARD_QUAD)))
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        tq = np.linspace(0.0, L, nq + 1)
        xs = np.interp(tq, cum, P[:, 0])
        ys = np.interp(tq, cum, P[:, 1])
        ks = np.interp(tq, cum, np.array([kk for _, kk, _ in pts], dtype=np.float64))
        fr = np.interp(tq, cum, np.array([f for _, _, f in pts]))
        m = Mesh(name, "", kind="backdrop")
        bot, top = [], []
        taper = min(max(60.0, 0.3 * L), L / 2.0)  # long, gentle ends: the trees thin out behind the hill
        for q in range(nq + 1):
            if closed and q == nq:
                bot.append(bot[0])
                top.append(top[0])
                continue
            z = self.ground_at(float(ks[q]), float(fr[q])) - C.TREE_CARD_SINK
            if not closed:  # the strip's ends sink into the hill
                end = min(tq[q], L - tq[q])
                z -= height * (1.0 - smooth(end / taper))
            bot.append(m.vert((xs[q], ys[q], z)))
            top.append(m.vert((xs[q], ys[q], z + height + C.TREE_CARD_SINK)))
        # one texture repeat is aspect x height metres wide; a closed ring gets a whole number of repeats
        reps = L / (aspect * height)
        u_per_m = (max(1, round(reps)) if closed else reps) / L
        for q in range(nq):
            u0, u1 = tq[q] * u_per_m, tq[q + 1] * u_per_m
            mid = np.array([(xs[q] + xs[q + 1]) / 2 - self.c[0], (ys[q] + ys[q + 1]) / 2 - self.c[1], 0.0])
            m.oriented_face((bot[q], bot[q + 1], top[q + 1], top[q]), mat, -mid,
                            [(u0, 0.0), (u1, 0.0), (u1, 1.0), (u0, 1.0)])
        return m

    def build(self) -> tuple[list[Mesh], dict]:
        meshes = [self.far_ground(), self.horizon()]
        cards, n_tex = self.cards()
        meshes += cards
        dist = self.d_rect + self.h_off
        info = {
            "centre": [round(float(v), 2) for v in self.c],
            "horizonDistanceM": [round(float(dist.min()), 1), round(float(dist.max()), 1)],
            "horizonOffsetM": [round(float(self.h_off.min()), 1), round(float(self.h_off.max()), 1)],
            "horizonTexture": envs.stem(self.e["horizon"]["c"]),
            "farGroundTexture": envs.stem(self.e["farGround"]),
            "cards": len(cards),
            "cardTextures": [envs.stem(c) for c in list(self.e.get("treeCards", [])) + list(self.e.get("skylineCards", []))],
            "baseZ": round(self.base_z, 2),
        }
        self.n_textures = n_tex
        return meshes, info
