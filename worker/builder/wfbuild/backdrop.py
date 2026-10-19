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
        imp = getattr(geo, "imported", None) or {}
        # the sea (coastal: an imported track's seaLevelM): a flat sea plane, and seabed beyond the real ground
        self.sea = float(imp["seaLevelM"]) if (self.e.get("sea") and imp.get("seaLevelM") is not None) else None
        # a horizon painted from the imported skyline profile (e.g. Snowdonia), centred on the drivers' eye level
        self.profile = imp.get("horizon") if self.e.get("horizonFromProfile") else None
        self.eye_z = float(geo.main.p[0, 2]) + 1.5
        self.generated: dict[str, str] = {}

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
            zr = float(self.geo.ground(p[None, :])[0])  # outside the grid: its nearest edge value
            if w > 0:
                z = zr * w + z * (1 - w)
            if self.sea is not None and zr < self.sea:
                z = min(z, zr)  # the sea floor runs on beyond the real ground (no hills out of the sea)
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
        # A painted skyline profile is centred on the drivers' eye level, so a texel row is an elevation angle
        # (v = (60 + HORIZON_SCALE_M * tan(elevation)) / 127) and one repeat spans the whole circle.
        mid = self.eye_z if self.profile else self.base_z
        for k in ks:
            off = float(self.h_off[k])
            dist = float(self.d_rect[k] + off)
            scale = max(dist / C.HORIZON_SCALE_M, 0.25)
            p = self.point(k, off)
            bottom.append(m.vert((p[0], p[1], mid - 60.0 * scale)))
            top.append(m.vert((p[0], p[1], mid + 67.0 * scale)))
        rep = 1 if self.profile else C.HORIZON_REPEATS
        for a in range(len(ks)):
            b = (a + 1) % len(ks)
            u0 = a / len(ks) * rep
            u1 = (a + 1) / len(ks) * rep
            if self.profile:
                # u = azimuth / 360 (clockwise from +y, north): the rays turn anticlockwise from +x
                th0, th1 = 2 * math.pi * ks[a] / self.n, 2 * math.pi * (ks[a] + step) / self.n
                u0, u1 = 0.25 - th0 / (2 * math.pi), 0.25 - th1 / (2 * math.pi)
            ids = (bottom[a], bottom[b], top[b], top[a])
            mid = self.u[ks[a]]
            m.oriented_face(ids, "horizon", np.array([-mid[0], -mid[1], 0.0]),
                            [(u0, 0.0), (u1, 0.0), (u1, 1.0), (u0, 1.0)])
        return m

    def sea_plane(self) -> Mesh | None:
        """A flat sea at the imported sea level over the terrain and the far ground, out to the far ground's
        outer ring (no collision; the ground above the sea pokes through it). UV1 in metres / tile (|uv| < 16)."""
        if self.sea is None:
            return None
        m = Mesh("backdrop_sea#lofi", "", kind="backdrop")
        tile = float(self.e["sea"].get("tileM", 160.0))
        c = m.vert((self.c[0], self.c[1], self.sea))
        ring = []
        for k in range(self.n):
            p = self.point(k, min(1.06 * self.h_off[k], self.room[k]))
            ring.append(m.vert((p[0], p[1], self.sea)))
        for k in range(self.n):
            ids = (c, ring[k], ring[(k + 1) % self.n])
            m.oriented_face(ids, "sea", np.array([0.0, 0.0, 1.0]),
                            [((m.verts[i][0] - self.c[0]) / tile, (m.verts[i][1] - self.c[1]) / tile) for i in ids])
        return m

    def textures(self, root: str) -> dict[str, str]:
        """Generated backdrop textures (TGA under <root>/data/track/<slug>/textures, bimage'd by the pipeline like
        the maps): the painted skyline ``<slug>_horizon_c5`` from the imported horizon profile and the sea
        ``<slug>_sea_c``. Returns {"horizon"|"sea": data path without extension}."""
        from . import maps  # noqa: PLC0415 - avoids a cycle at import time

        out_dir = f"{root}/data/track/{self.slug}/textures"
        rel = lambda name: f"data/track/{self.slug}/textures/{self.slug}_{name}"  # noqa: E731
        if self.profile:
            hp = self.e["horizonFromProfile"]
            W, H = 4096, 256
            step = float(self.profile["stepDeg"])
            elev = np.array(self.profile["elevationDeg"], dtype=np.float64)
            sea = np.array(self.profile["sea"], dtype=bool)
            az = np.arange(W) / W * 360.0
            k = az / step
            k0 = np.floor(k).astype(int) % len(elev)
            f = k - np.floor(k)
            e = elev[k0] * (1 - f) + elev[(k0 + 1) % len(elev)] * f
            ridge = periodic_noise(seed_for(self.slug, "skyline"), W, 160) * 0.05
            e = e + ridge * np.clip(e / 0.8, 0.0, 1.0)  # a little crag on the mountains, none on the sea line
            is_sea = sea[k0] & sea[(k0 + 1) % len(sea)]
            v_of = lambda deg: (60.0 + C.HORIZON_SCALE_M * np.tan(np.radians(deg))) / 127.0  # noqa: E731
            vt = v_of(e)
            v = (np.arange(H) + 0.5) / H
            V = v[:, None]
            rgb = np.zeros((H, W, 3))
            alpha = (V <= vt[None, :]).astype(np.float64) * 255.0
            land = np.array(hp["land"], dtype=np.float64)
            mount = np.array(hp["mountain"], dtype=np.float64)
            seac = np.array(hp["sea"], dtype=np.float64)
            from_deg = float(hp.get("mountainFromDeg", 0.45))
            # elevation of each texel row; mountains above from_deg, nearer land below, the sea below the sea line
            e_row = np.degrees(np.arctan((V * 127.0 - 60.0) / C.HORIZON_SCALE_M))
            mshare = np.clip((e_row - from_deg * 0.6) / (from_deg * 0.8), 0.0, 1.0) * (e[None, :] > from_deg)
            col = land[None, None, :] * (1 - mshare[:, :, None]) + mount[None, None, :] * mshare[:, :, None]
            # lighter towards the ridge tops (haze, sunlit slopes), darker low down
            shade = 0.85 + 0.25 * np.clip((V - 0.45) / np.maximum(vt[None, :] - 0.45, 1e-3), 0.0, 1.0)
            col = col * shade[:, :, None]
            sea_line = v_of(0.0)
            below_sea = (V <= sea_line) & is_sea[None, :]
            col = np.where(below_sea[:, :, None], seac[None, None, :] * (0.9 + 0.2 * V[:, :, None] / sea_line), col)
            rgb[:] = col
            alpha = np.where(is_sea[None, :], np.where(V <= sea_line, 255.0, 0.0), alpha)
            maps.write_tga(f"{out_dir}/{self.slug}_horizon_c5.tga", rgb, alpha)
            self.generated["horizon"] = rel("horizon_c5")
        if self.sea is not None:
            # seamless (integer-frequency) ripples and swell, so the tile repeats without seams
            n = 256
            rng = np.random.default_rng(seed_for(self.slug, "sea"))
            t = np.arange(n) / n * 2 * math.pi
            gx, gy = np.meshgrid(t, t)
            nz = np.zeros((n, n))
            for _ in range(24):
                fx, fy = (int(v) for v in rng.integers(-9, 10, 2))
                if fx == 0 and fy == 0:
                    continue
                nz += rng.normal() / math.hypot(fx, fy) * np.cos(fx * gx + fy * gy + rng.uniform(0, 2 * math.pi))
            nz /= max(float(np.abs(nz).max()), 1e-9)
            base = np.array(self.e["sea"]["colour"], dtype=np.float64)
            rgb = base[None, None, :] * (1.0 + 0.1 * nz[:, :, None])
            maps.write_tga(f"{out_dir}/{self.slug}_sea_c.tga", rgb)
            self.generated["sea"] = rel("sea_c")
        return dict(self.generated)

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
        sea = self.sea_plane()
        if sea is not None:
            meshes.append(sea)
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
        if self.sea is not None:
            info["seaLevelM"] = self.sea
        if self.profile:
            info["horizonTexture"] = "generated from the imported horizon profile"
            info["horizonMidZ"] = round(self.eye_z, 2)
        self.n_textures = n_tex
        return meshes, info
