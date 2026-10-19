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
        self.rng = np.random.default_rng(seed_for(self.slug, "backdrop"))

    def point(self, k: int, off: float) -> np.ndarray:
        return self.c + self.u[k] * (self.d_rect[k] + off)

    def ground_z(self, k: int, t: float) -> float:
        """Far-ground height on ray k at fraction t of the way to the horizon (t < 0: under the terrain edge)."""
        if t < 0:
            return float(self.edge_z[k]) - 0.6
        if t == 0:
            return float(self.edge_z[k]) - 0.05
        # gentle: stock far-ground rings stay within ~15 m of the track level; the #horizon vista provides the hills
        amp = 2.0 + 10.0 * t * t
        z_hill = self.base_z + 1.0 + amp * (0.55 + 0.45 * self.hills[k]) + 3.0 * self.hills2[k] * t
        blend = min(1.0, t / 0.12)
        return float(self.edge_z[k] + 0.4) * (1 - blend) + z_hill * blend

    # ----------------------------------------------------------------------------------- meshes
    def far_ground(self) -> Mesh:
        m = Mesh("backdrop_ground#lofi", "", kind="backdrop")
        ts = [-1.0, 0.0, 0.03, 0.12, 0.3, 0.55, 0.8, 1.0, 1.06]
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
                ring.append(m.vert((p[0], p[1], self.ground_z(k, t))))
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
            scale = max(dist / 1400.0, 0.25)
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
        """Background tree (or skyline) cards: strips of 8 m quads following the ring, facing the track."""
        e = self.e
        textures = list(e.get("treeCards", []))
        skyline = list(e.get("skylineCards", []))
        cov = float(e.get("treeCardCoverage", 0.0))
        meshes = []
        layers = []
        if textures and cov > 0:
            layers.append((0.12, cov, textures, C.TREE_CARD_HEIGHT, 0))
            if cov > 0.6:
                layers.append((0.32, cov * 0.8, textures, (34.0, 46.0), 0))
        if skyline:
            layers.append((0.45, 0.7, skyline, (60.0, 90.0), len(textures)))
        n_tex = len(textures) + len(skyline)
        idx = 0
        for frac, coverage, texs, (hmin, hmax), tex0 in layers:
            k = int(self.rng.integers(self.n))
            walked = 0
            while walked < self.n:
                span = int(self.rng.integers(4, 9))  # rays per strip (~150-250 m)
                if self.rng.random() < coverage:
                    pick = int(self.rng.integers(len(texs)))
                    aspect = float(envs.table()["cardAspect"].get(texs[pick], 2.0))
                    m = self._strip(f"backdrop_cards_{idx:02d}", k, span, frac, float(self.rng.uniform(hmin, hmax)),
                                    f"card_{tex0 + pick}", aspect)
                    if m is not None:
                        meshes.append(m)
                        idx += 1
                gap = int(self.rng.integers(1, 3)) if coverage > 0.6 else int(self.rng.integers(2, 6))
                k = (k + span + gap) % self.n
                walked += span + gap
        return meshes, n_tex

    def _strip(self, name: str, k0: int, span: int, frac: float, height: float, mat: str,
               aspect: float = 2.0) -> Mesh | None:
        pts = []
        for j in range(span + 1):
            k = (k0 + j) % self.n
            off = min(frac * self.h_off[k] + 30.0, self.room[k] - 5.0)
            if off < 20.0:
                return None
            pts.append((self.point(k, off), k, off / max(self.h_off[k], 1e-6)))
        # resample the polyline into ~8 m quads
        P = np.array([p for p, _, _ in pts])
        seg = np.hypot(*np.diff(P, axis=0).T)
        L = float(seg.sum())
        nq = max(1, int(round(L / C.TREE_CARD_QUAD)))
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        tq = np.linspace(0.0, L, nq + 1)
        xs = np.interp(tq, cum, P[:, 0])
        ys = np.interp(tq, cum, P[:, 1])
        ks = np.interp(tq, cum, np.arange(len(pts)))
        m = Mesh(name, "", kind="backdrop")
        bot, top = [], []
        for q in range(nq + 1):
            kk = (k0 + int(round(ks[q]))) % self.n
            z = self.ground_z(kk, pts[min(int(round(ks[q])), len(pts) - 1)][2]) - 2.0
            bot.append(m.vert((xs[q], ys[q], z)))
            top.append(m.vert((xs[q], ys[q], z + height)))
        u_per_m = 1.0 / (aspect * height)  # one texture repeat is aspect x height metres wide
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
