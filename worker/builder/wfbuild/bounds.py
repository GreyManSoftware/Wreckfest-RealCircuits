"""Out-of-bounds handling as stock tracks do it (pure numpy): reset walls, a kill floor, water resets and
collidable ground out to them.

How stock tracks keep cars in (decoded from the compiled collision shapes of the stock scenes, 2026-10-24;
worker/docs/visual-assets.md "Out of bounds"): almost every stock base or layout has an invisible collision
mesh whose material carries the surface ``#resetplayer`` (``data/scene/surface/resetplayer.suse``; Toolbox's
importer names surface id 19 "resetplayer"). Its objects are called ``resetwall``, ``reset_wall``,
``great_collision_wall``, ``boundcollision``, ``resetplane`` ... (forest01, forest11, fields05, fields08, sandpit03,
urban03, urban05 bases; fields13 and forest11_2 layouts). A car touching it is put back on the track whatever the
race's reset setting. They run just outside the blue AI route (``#ai_route_*``, "outside this, you are totally
lost", Bugbear's Technical Art Guide): a vertical wall 1-4 m outside the route border, its top 1-4 m above the
ground and its foot 4-9 m below it, with a flat lid 1-4 m above the ground reaching 10-50 m further out
(fields13: a flat sheet 0.5 m above the ground from ~2 m outside the route). The collidable ground runs on
185-1500 m beyond the route border, so nothing falls through before it is reset; far ground (#lofi) mostly has
no collision (902 of 1,044 stock #lofi/#noshadow models), though some stock outer terrain does. ``resetplayer_delay``
(fields11, wrecker01) and ``crm_water_reset`` are variants; there is no .tcat or .envi field for it (the .tcat's
per-route special zones only switch resets *off*).

Ours (Chris 2026-10-26: the first walls, RESET_GAP beyond the blue route, were "too extreme"; cars may stray at
least twice as far, but are reset at once if they fall through the map, enter water or get into a crowd):

- ``bounds_reset`` (layout scene): a reset wall on the 4 m terrain grid, top RESET_HEIGHT above the terrain and
  foot RESET_SINK below it, plus a lid RESET_BAND wide at the wall's top height; only round the outside (the
  infield isn't reset, as stock fields05). It stands at least RESET_FACTOR times as far from the road edge as the
  first walls did (RESET_GAP beyond the blue AI route or the run-off reach, whichever is further out) and at least
  RESET_MIN_M from it, except where the terrain ends sooner: it stays RESET_EDGE_KEEP inside the terrain's edge.
  Every layout's roads, run-off and blue AI routes (and imported extra roads, ``imported.roads``) are inside it on
  every layout, so the same mesh serves each layout and no reset touches any layout's road.
- ``bounds_kill_floor`` (base): a ``#resetplayer`` plane KILL_DEPTH below the lowest collision, under the terrain,
  the far ground and beyond, so anything that falls through the map is reset.
- ``bounds_water`` (base): ``#resetplayer`` sheets WATER_SINK under imported lakes and the sea, where the terrain
  is below the water (stock crm water resets); shallow edges stay drivable, and no sheet reaches any layout's
  road, run-off or blue AI route (``protected``).
- ``bounds_crowd`` (base): the game can't tell a car hit a spectator, so every crowd (the dressing's spectators)
  is boxed in by ``#resetplayer`` walls CROWD_PAD round the people, from CROWD_SINK below the ground up to a lid
  CROWD_LID above it: a car that gets past the barrier into the crowd is reset. The box never reaches a road,
  run-off or blue AI route.
- ``bounds_far_ground`` (base): invisible collision on the far ground (``#offroadfoliage``) from under the
  terrain edge out to FAR_COLLISION_T of the way to the horizon (~230 m), the same surface the backdrop draws.

All in TrackModel coordinates (build_scene transforms them with the rest of the track).
"""
from __future__ import annotations

import math

import numpy as np

from . import config as C
from .geometry import Mesh

RESET_GAP = 4.0        # the first reset walls stood this far outside the blue AI route (and the run-off)...
RESET_FACTOR = 2.0     # ...the reset now stands at least this many times as far from the road edge...
RESET_MIN_M = 48.0     # ...and at least this far from it...
RESET_EDGE_KEEP = 8.0  # ...where the terrain allows: it stays this far inside the terrain's edge
RESET_HEIGHT = 2.0     # wall top and lid above the terrain (stock 1-4 m)
RESET_SINK = 4.0       # wall foot below the terrain (stock 4-9 m)
RESET_BAND = 16.0      # lid width beyond the wall (stock 10-50 m)
KILL_DEPTH = 25.0      # kill floor below the lowest collision
KILL_REACH = 600.0     # kill floor reaches this far beyond the terrain (clamped to the +-2 km limit)
WATER_SINK = 0.3       # water reset sheets this far under the water level
FAR_COLLISION_T = 0.26  # the far ground has collision out to this fraction of the way to the horizon
CROWD_PAD = 1.5        # crowd resets: walls this far round the spectators...
CROWD_SINK = 1.0       # ...from this far below the ground...
CROWD_LID = 3.0        # ...up to a lid this far above it (over the spectators' heads)
CROWD_CELL = 0.5       # ...on a raster this fine
PROTECT_PAD = 0.5      # water and crowd resets stay this far off any layout's road, run-off and blue AI route
CROWD_CATEGORIES = ("spectators",)  # dressing placements that are people standing in the open
RESET_CUSTOM_DATA = "vis = false | col = true"
# base mesh kinds a car may also drive on outside the AI routes (none yet; e.g. a pit lane that isn't an AI route)
ALLOWED_KINDS: tuple[str, ...] = ("pit",)


def _dilate(m: np.ndarray, diagonal: bool = True) -> np.ndarray:
    out = m.copy()
    out[1:, :] |= m[:-1, :]
    out[:-1, :] |= m[1:, :]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    if diagonal:
        out[1:, 1:] |= m[:-1, :-1]
        out[:-1, :-1] |= m[1:, 1:]
        out[1:, :-1] |= m[:-1, 1:]
        out[:-1, 1:] |= m[1:, :-1]
    return out


def flood_outside(blocked: np.ndarray) -> np.ndarray:
    """Cells reachable (4-connected) from the grid border without crossing ``blocked`` cells."""
    free = ~blocked
    reach = np.zeros_like(free)
    reach[0, :], reach[-1, :], reach[:, 0], reach[:, -1] = free[0, :], free[-1, :], free[:, 0], free[:, -1]
    while True:
        nxt = _dilate(reach, diagonal=False) & free
        if (nxt == reach).all():
            return reach
        reach = nxt


def _poly_dist(px: np.ndarray, py: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Distance from points to a convex-or-not polygon (0 inside), vectorised over the points."""
    n = len(poly)
    inside = np.zeros(px.shape, dtype=bool)
    d = np.full(px.shape, np.inf)
    for k in range(n):
        a, b = poly[k], poly[(k + 1) % n]
        cond = (a[1] > py) != (b[1] > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xc = a[0] + (py - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
        inside ^= cond & (px < xc)
        ab = b - a
        ll = max(float(ab @ ab), 1e-12)
        t = np.clip(((px - a[0]) * ab[0] + (py - a[1]) * ab[1]) / ll, 0.0, 1.0)
        d = np.minimum(d, np.hypot(px - (a[0] + t * ab[0]), py - (a[1] + t * ab[1])))
    return np.where(inside, 0.0, d)


class Bounds:
    """Built after the base (needs ``geo.terrain_grid``, the 4 m collision grid of the terrain tiles)."""

    def __init__(self, geo, base_meshes: list[Mesh] | None = None, layouts: list | None = None):
        self.geo = geo
        xs, ys, z = geo.terrain_grid
        self.xs, self.ys, self.z = np.asarray(xs), np.asarray(ys), np.asarray(z)
        self.nx, self.ny = len(self.xs) - 1, len(self.ys) - 1
        self.cx = (self.xs[:-1] + self.xs[1:]) / 2.0
        self.cy = (self.ys[:-1] + self.ys[1:]) / 2.0
        self.cell = float(max(np.diff(self.xs).max(), np.diff(self.ys).max()))
        self.extra = [np.array([m.verts[i][:2] for i in f]) for m in (base_meshes or []) if m.kind in ALLOWED_KINDS
                      for f in m.faces]
        self.extra += self._shared_road_quads()
        self.layouts = list(layouts or [])
        self._allowed: np.ndarray | None = None
        self._protected: list[np.ndarray] | None = None
        self.info: dict = {}

    def _shared_road_quads(self) -> list[np.ndarray]:
        """Plan quads of the imported extra roads (``imported.roads``: real tarmac in the base that is no layout's
        own branch, e.g. Oulton Park's Hislop's straight beside the chicane). They stay drivable and inside the
        reset on every layout, also those whose AI route doesn't use them, so no reset wall cuts across them."""
        layouts = self.geo.model.get("layouts") or []
        if not layouts:
            return []
        own = {"main"} | {lay["id"] for lay in layouts}
        out = []
        for r in self.geo.routes:
            if r.id in own:
                continue
            ok = self.geo.svalid[r.index]
            for i in range(r.segs):
                k = (i + 1) % r.n
                if ok[i] and ok[k]:
                    out.append(np.array([r.left[i, :2], r.left[k, :2], r.right[k, :2], r.right[i, :2]]))
        return out

    # ------------------------------------------------------------------------------------ per layout
    def _stamp(self, mask: np.ndarray, poly: np.ndarray, reach: float) -> None:
        lo, hi = poly.min(0) - reach, poly.max(0) + reach
        i0, i1 = np.searchsorted(self.cx, lo[0]), np.searchsorted(self.cx, hi[0], side="right")
        j0, j1 = np.searchsorted(self.cy, lo[1]), np.searchsorted(self.cy, hi[1], side="right")
        if i0 >= i1 or j0 >= j1:
            return
        px, py = np.meshgrid(self.cx[i0:i1], self.cy[j0:j1], indexing="ij")
        mask[i0:i1, j0:j1] |= _poly_dist(px, py, poly) <= reach

    def _blue_quads(self, layouts) -> list[np.ndarray]:
        return [np.array([m.verts[i][:2] for i in f]) for lay in layouts for m in lay.meshes
                if m.kind == "ai" and m.name.startswith("#ai_route_") for f in m.faces]

    def _side_reach(self):
        """Per (route, side) of the wall plan: (plan dict, built samples, run-off reach beyond the edge: out to the
        barrier or the end of the measured trap or apron)."""
        geo = self.geo
        ro = getattr(geo, "runoff", None)
        for (ri, side), p in geo.wall_plan().items():
            r = p["route"]
            reach = np.where(p["ok"], np.nan_to_num(p["offset"]), 0.0)
            if ro is not None and ro.active:
                reach = np.maximum(reach, ro.run_off_end(ri, side))
            yield p, geo.svalid[r.index] | (r.index in geo.apron_routes), reach

    @staticmethod
    def _out_quads(p: dict, use: np.ndarray, dist: np.ndarray) -> list[np.ndarray]:
        """Plan quads from a route side's edge out ``dist`` per sample, over consecutive ``use`` samples."""
        r = p["route"]
        edge = r.left if p["side"] > 0 else r.right
        far = edge[:, :2] + p["out"] * dist[:, None]
        return [np.array([edge[a, :2], edge[(a + 1) % r.n, :2], far[(a + 1) % r.n], far[a]])
                for a in range(r.segs) if use[a] and use[(a + 1) % r.n]]

    def protected(self) -> list[np.ndarray]:
        """Plan polygons no reset may touch: every layout's blue AI route strips (which hold its road and AI lines),
        every built road, its run-off out to the barrier or the end of the measured run-off, ALLOWED_KINDS base
        meshes (the pit lane) and imported extra roads."""
        if self._protected is not None:
            return self._protected
        out = self._blue_quads(self.layouts) + list(self.extra)
        for p, built, reach in self._side_reach():
            out += self._out_quads(p, built, np.maximum(reach, 0.0))
        self._protected = out
        return out

    def allowed(self, layout=None) -> np.ndarray:
        """Cells (nx x ny) a car may be on, the same for every layout (Bounds.layouts, else ``layout``): within
        RESET_GAP (plus the cell's half diagonal, so no point of a disallowed cell is nearer) of any layout's blue
        AI route strips, of an ALLOWED_KINDS base mesh or of an imported extra road (_shared_road_quads), and of the
        run-off out to the barrier or the end of a measured trap; and beyond those, out from each built road edge to
        RESET_FACTOR times the first walls' distance (the blue route's margin or the run-off reach, whichever is
        further, plus RESET_GAP) or RESET_MIN_M, whichever is further, but not within RESET_EDGE_KEEP of the
        terrain's edge (the reset wall and lid stand on the terrain's collision ground)."""
        if self._allowed is not None:
            return self._allowed
        layouts = self.layouts or ([layout] if layout is not None else [])
        hd = self.cell * math.sqrt(0.5)
        core = np.zeros((self.nx, self.ny), dtype=bool)
        for poly in self._blue_quads(layouts) + self.extra:
            self._stamp(core, poly, RESET_GAP + hd)
        far = np.zeros_like(core)
        worst = []
        for p, built, reach in self._side_reach():
            run = np.where(reach > C.AI_ROUTE_MARGIN["walls"], reach, 0.0)
            first = np.maximum(p["ai_margin"], run) + RESET_GAP  # where the first walls stood
            for poly in self._out_quads(p, built, run):
                self._stamp(core, poly, RESET_GAP + hd)
            for poly in self._out_quads(p, built, np.maximum(RESET_FACTOR * first, RESET_MIN_M)):
                self._stamp(far, poly, hd)
            if built.any():
                worst.append(float(first[built].max()))
        edge = RESET_EDGE_KEEP + hd
        inner = ((self.cx[:, None] - self.xs[0] >= edge) & (self.xs[-1] - self.cx[:, None] >= edge)
                 & (self.cy[None, :] - self.ys[0] >= edge) & (self.ys[-1] - self.cy[None, :] >= edge))
        self._allowed = core | (far & inner)
        self.info["reset"] = {"firstGapM": RESET_GAP, "factor": RESET_FACTOR, "minM": RESET_MIN_M,
                              "edgeKeepM": RESET_EDGE_KEEP, "firstMaxM": round(max(worst or [0.0]), 1),
                              "edgeClippedHa": round(float((far & ~inner & ~core).sum()) * self.cell ** 2 / 1e4, 2)}
        return self._allowed

    def regions(self, layout) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(allowed, outside, band): the allowed cells, the cells reachable from the terrain edge without
        crossing them, and the band of outside cells within RESET_BAND of the allowed ones (the reset lid)."""
        allowed = self.allowed(layout)
        outside = flood_outside(allowed)
        near = allowed.copy()
        for _ in range(max(1, int(math.ceil(RESET_BAND / self.cell)))):
            near = _dilate(near)
        return allowed, outside, outside & near

    def layout_reset(self, layout) -> Mesh | None:
        """The reset wall and lid round the outside of a layout (``bounds_reset``, #resetplayer)."""
        allowed, outside, band = self.regions(layout)
        m = Mesh("bounds_reset", RESET_CUSTOM_DATA, kind="bounds")
        top: dict[tuple[int, int], int] = {}
        bot: dict[tuple[int, int], int] = {}

        def vt(i: int, j: int) -> int:
            if (i, j) not in top:
                top[i, j] = m.vert((self.xs[i], self.ys[j], self.z[i, j] + RESET_HEIGHT))
            return top[i, j]

        def vb(i: int, j: int) -> int:
            if (i, j) not in bot:
                bot[i, j] = m.vert((self.xs[i], self.ys[j], self.z[i, j] - RESET_SINK))
            return bot[i, j]

        up = np.array([0.0, 0.0, 1.0])
        walls = 0
        for i, j in zip(*np.nonzero(band)):
            i, j = int(i), int(j)
            m.oriented_face((vt(i, j), vt(i + 1, j), vt(i + 1, j + 1), vt(i, j + 1)), "bounds_reset", up)
            # a wall on every edge towards a cell that isn't outside (the allowed area), facing it
            for di, dj, (a, b) in ((-1, 0, ((i, j), (i, j + 1))), (1, 0, ((i + 1, j), (i + 1, j + 1))),
                                   (0, -1, ((i, j), (i + 1, j))), (0, 1, ((i, j + 1), (i + 1, j + 1)))):
                ni, nj = i + di, j + dj
                if 0 <= ni < self.nx and 0 <= nj < self.ny and not outside[ni, nj]:
                    m.oriented_face((vb(*a), vb(*b), vt(*b), vt(*a)), "bounds_reset", np.array([di, dj, 0.0]))
                    walls += 1
        if m.empty:
            return None
        layout.info["bounds"] = {
            **self.info["reset"], "resetHeightM": RESET_HEIGHT, "resetBandM": RESET_BAND,
            "allowedHa": round(float(allowed.sum()) * self.cell ** 2 / 1e4, 2),
            "lidCells": int(band.sum()), "wallFaces": walls, "faces": len(m.faces)}
        return m

    # ----------------------------------------------------------------------------------------- base
    def protected_cells(self) -> np.ndarray:
        """Terrain cells (nx x ny) that any part of lies within PROTECT_PAD of a protected polygon."""
        if getattr(self, "_protected_cells", None) is None:
            mask = np.zeros((self.nx, self.ny), dtype=bool)
            for poly in self.protected():
                self._stamp(mask, poly, PROTECT_PAD + self.cell * math.sqrt(0.5))
            self._protected_cells = mask
        return self._protected_cells

    def water_cells(self) -> list[tuple[float, np.ndarray]]:
        """[(level, cell mask)]: terrain cells with a corner under an imported lake (within LAKE_REACH_M of its
        outline, as the lake planes) or the sea, off every layout's road, run-off and blue AI route
        (protected_cells)."""
        return [(lvl, wet & ~self.protected_cells()) for lvl, wet in self._wet_cells()]

    def _wet_cells(self) -> list[tuple[float, np.ndarray]]:
        from .backdrop import _in_polygon, _ring_distance
        imp = getattr(self.geo, "imported", None) or {}
        from . import envs
        e = envs.env(envs.env_name(self.geo.options.get("environment")))
        zmin = np.minimum.reduce([self.z[:-1, :-1], self.z[1:, :-1], self.z[:-1, 1:], self.z[1:, 1:]])
        out = []
        if e.get("sea") and imp.get("seaLevelM") is not None:
            lvl = float(imp["seaLevelM"])
            out.append((lvl, zmin < lvl - WATER_SINK))
        if e.get("lake"):
            px, py = np.meshgrid(self.cx, self.cy, indexing="ij")
            cen = np.column_stack([px.ravel(), py.ravel()])
            for lake in imp.get("lakes") or []:
                poly = np.array([[q["x"], q["y"]] for q in lake["points"]], dtype=np.float64)
                lvl = float(lake["levelM"])
                lo, hi = poly.min(0) - C.LAKE_REACH_M, poly.max(0) + C.LAKE_REACH_M
                box = (cen[:, 0] >= lo[0]) & (cen[:, 0] <= hi[0]) & (cen[:, 1] >= lo[1]) & (cen[:, 1] <= hi[1])
                near = np.zeros(len(cen), dtype=bool)
                if box.any():
                    c = cen[box]
                    near[box] = _in_polygon(c, poly) | (_ring_distance(c, poly) < C.LAKE_REACH_M)
                wet = near.reshape(zmin.shape) & (zmin < lvl - WATER_SINK)
                if wet.any():
                    out.append((lvl, wet))
        return out

    def water(self) -> Mesh | None:
        """Flat #resetplayer sheets WATER_SINK under the water, one quad per run of wet cells along x."""
        m = Mesh("bounds_water", RESET_CUSTOM_DATA, kind="bounds")
        up = np.array([0.0, 0.0, 1.0])
        n = 0
        for lvl, wet in self.water_cells():
            z = lvl - WATER_SINK
            for j in range(self.ny):
                col = wet[:, j]
                if not col.any():
                    continue
                d = np.diff(np.concatenate([[0], col.astype(np.int8), [0]]))
                for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
                    ids = (m.vert((self.xs[a], self.ys[j], z)), m.vert((self.xs[b], self.ys[j], z)),
                           m.vert((self.xs[b], self.ys[j + 1], z)), m.vert((self.xs[a], self.ys[j + 1], z)))
                    # invisible: a unit UV square per quad (a run across a lake can be hundreds of metres long,
                    # beyond the +-16 UV range world-space UVs would need)
                    m.oriented_face(ids, "bounds_reset", up, [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
                n += int(col.sum())
        self.info["waterCells"] = n
        return None if m.empty else m

    def far_ground(self, bd) -> Mesh:
        """Invisible collision on the backdrop's far ground (the rays and heights it draws), from under the
        terrain edge out to FAR_COLLISION_T of the way to the horizon."""
        m = Mesh("bounds_far_ground", RESET_CUSTOM_DATA, kind="bounds")
        ts = [t for t in (-1.0, 0.0, 0.03, 0.07, 0.12, 0.18, 0.26, 0.35, 0.45) if t <= FAR_COLLISION_T + 1e-9]
        rings = []
        for t in ts:
            ring = []
            for k in range(bd.n):
                off = -3.0 if t < 0 else (0.5 if t == 0 else min(t * bd.h_off[k], bd.room[k]))
                p = bd.point(k, off)
                ring.append(m.vert((p[0], p[1], bd.ground_z(k, min(t, 1.0)))))
            rings.append(ring)
        up = np.array([0.0, 0.0, 1.0])
        for a in range(len(rings) - 1):
            for k in range(bd.n):
                k2 = (k + 1) % bd.n
                m.oriented_face((rings[a][k], rings[a][k2], rings[a + 1][k2], rings[a + 1][k]), "bounds_far", up)
        reach = [float(np.hypot(*(np.array(m.verts[r[k]][:2]) - bd.point(k, 0.0)))) for r in rings[-1:] for k in range(bd.n)]
        self.info["farCollisionM"] = [round(min(reach), 1), round(max(reach), 1)]
        return m

    def kill_floor(self, z_low: float, rot: int, shift: float) -> Mesh:
        """A #resetplayer plane KILL_DEPTH below ``z_low``, KILL_REACH beyond the terrain, kept within the
        +-2 km limit after the placement (scene = rotate(model, rot) + (0, shift))."""
        x0, x1, y0, y1 = self.geo.extent
        lim = C.BACKDROP_LIMIT
        corners = np.array([[x0 - KILL_REACH, y0 - KILL_REACH], [x1 + KILL_REACH, y1 + KILL_REACH]])
        c, s = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[rot]
        R = np.array([[c, -s], [s, c]], dtype=np.float64)
        sc = corners @ R.T + np.array([0.0, shift])
        lo, hi = np.clip(sc.min(0), -lim, lim), np.clip(sc.max(0), -lim, lim)
        box = np.array([[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]])
        back = (box - np.array([0.0, shift])) @ R  # inverse rotation
        z = z_low - KILL_DEPTH
        m = Mesh("bounds_kill_floor", RESET_CUSTOM_DATA, kind="bounds")
        ids = tuple(m.vert((p[0], p[1], z)) for p in back)
        m.oriented_face(ids, "bounds_reset", np.array([0.0, 0.0, 1.0]),
                        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
        self.info["killFloorZ"] = round(z, 2)
        return m

    def ground(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Terrain height (bilinear on the terrain grid) at plan points."""
        x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
        i = np.clip(np.searchsorted(self.xs, x) - 1, 0, self.nx - 1)
        j = np.clip(np.searchsorted(self.ys, y) - 1, 0, self.ny - 1)
        u = np.clip((x - self.xs[i]) / (self.xs[i + 1] - self.xs[i]), 0.0, 1.0)
        v = np.clip((y - self.ys[j]) / (self.ys[j + 1] - self.ys[j]), 0.0, 1.0)
        z = self.z
        return ((1 - u) * (1 - v) * z[i, j] + u * (1 - v) * z[i + 1, j] + (1 - u) * v * z[i, j + 1]
                + u * v * z[i + 1, j + 1])

    def crowd_mask(self, placements) -> tuple[np.ndarray, np.ndarray, int]:
        """(mask, origin, spectators): CROWD_CELL cells within CROWD_PAD of a spectator (CROWD_CATEGORIES
        placements), less those within PROTECT_PAD of a protected polygon (any layout's road, run-off, blue AI
        route, pit lane), over a raster from ``origin``."""
        pts = np.array([(p.x, p.y) for p in placements if p.category in CROWD_CATEGORIES],
                       dtype=np.float64).reshape(-1, 2)
        if not len(pts):
            return np.zeros((0, 0), dtype=bool), np.zeros(2), 0
        cell = CROWD_CELL
        lo = pts.min(0) - CROWD_PAD - 4 * cell
        nx, ny = (int(v) for v in np.ceil((pts.max(0) + CROWD_PAD + 4 * cell - lo) / cell))
        mask = np.zeros((nx, ny), dtype=bool)
        k = int(math.ceil(CROWD_PAD / cell))
        ij = np.floor((pts - lo) / cell).astype(np.int64)
        for di in range(-k, k + 1):
            for dj in range(-k, k + 1):
                i, j = ij[:, 0] + di, ij[:, 1] + dj
                c = lo + (np.stack([i, j], axis=1) + 0.5) * cell
                ok = np.hypot(*(c - pts).T) <= CROWD_PAD
                mask[i[ok], j[ok]] = True
        # the protected polygons near the crowds, rasterised (cell centres inside), grown by the pad
        coarse = {(int(a), int(b)) for a, b in np.floor(pts / 20.0)}
        tris = []
        for poly in self.protected():
            a0, b0 = np.floor((poly.min(0) - 10.0) / 20.0).astype(int)
            a1, b1 = np.floor((poly.max(0) + 10.0) / 20.0).astype(int)
            if (a1 - a0 + 1) * (b1 - b0 + 1) > 400 or any((a, b) in coarse for a in range(a0, a1 + 1)
                                                         for b in range(b0, b1 + 1)):
                tris += [poly[[0, q, q + 1]] for q in range(1, len(poly) - 1)]
        prot = raster_tris(np.array(tris).reshape(-1, 3, 2), lo[0], lo[1], cell, nx, ny)
        for _ in range(int(math.ceil((PROTECT_PAD + cell * math.sqrt(0.5)) / cell))):
            prot = _dilate(prot)
        return mask & ~prot, lo, len(pts)

    def crowd(self, placements) -> Mesh | None:
        """``bounds_crowd``: #resetplayer walls round every crowd (crowd_mask), CROWD_SINK below the ground to
        CROWD_LID above it, and a lid at that height over it; runs of cell edges and cells merged into quads."""
        mask, lo, n_people = self.crowd_mask(placements)
        self.info["crowd"] = {"spectators": n_people, "cells": int(mask.sum()), "cellM": CROWD_CELL,
                              "padM": CROWD_PAD, "lidM": CROWD_LID}
        if not mask.any():
            return None
        cell = CROWD_CELL
        m = Mesh("bounds_crowd", RESET_CUSTOM_DATA, kind="bounds")
        nx, ny = mask.shape
        X = lo[0] + np.arange(nx + 1) * cell
        Y = lo[1] + np.arange(ny + 1) * cell
        uv = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

        def runs(row: np.ndarray):
            d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
            return zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1))

        def zs(px: np.ndarray, py: np.ndarray) -> tuple[float, float]:
            g = self.ground(px, py)
            return float(g.min()) - CROWD_SINK, float(g.max()) + CROWD_LID

        up = np.array([0.0, 0.0, 1.0])
        walls = 0
        for j in range(ny):  # lid: one quad per run of cells along x, flat at the highest ground + CROWD_LID
            for a, b in runs(mask[:, j]):
                px = np.repeat(X[a:b + 1], 2)
                _, top = zs(px, np.tile([Y[j], Y[j + 1]], b - a + 1))
                corners = ((X[a], Y[j]), (X[b], Y[j]), (X[b], Y[j + 1]), (X[a], Y[j + 1]))
                ids = tuple(m.vert((x, y, top)) for x, y in corners)
                m.oriented_face(ids, "bounds_reset", up, uv)
        pad = np.pad(mask, 1)
        for j in range(ny + 1):  # walls along x between rows j-1 and j, facing out of the crowd
            for side, edge in ((-1.0, pad[1:-1, j + 1] & ~pad[1:-1, j]), (1.0, pad[1:-1, j] & ~pad[1:-1, j + 1])):
                for a, b in runs(edge):
                    px = np.linspace(X[a], X[b], max(2, b - a + 1))
                    bot, top = zs(px, np.full(len(px), Y[j]))
                    ids = (m.vert((X[a], Y[j], bot)), m.vert((X[b], Y[j], bot)), m.vert((X[b], Y[j], top)),
                           m.vert((X[a], Y[j], top)))
                    m.oriented_face(ids, "bounds_reset", np.array([0.0, side, 0.0]), uv)
                    walls += 1
        for i in range(nx + 1):  # walls along y between columns i-1 and i
            for side, edge in ((-1.0, pad[i + 1, 1:-1] & ~pad[i, 1:-1]), (1.0, pad[i, 1:-1] & ~pad[i + 1, 1:-1])):
                for a, b in runs(edge):
                    py = np.linspace(Y[a], Y[b], max(2, b - a + 1))
                    bot, top = zs(np.full(len(py), X[i]), py)
                    ids = (m.vert((X[i], Y[a], bot)), m.vert((X[i], Y[b], bot)), m.vert((X[i], Y[b], top)),
                           m.vert((X[i], Y[a], top)))
                    m.oriented_face(ids, "bounds_reset", np.array([side, 0.0, 0.0]), uv)
                    walls += 1
        self.info["crowd"].update({"faces": len(m.faces), "wallFaces": walls})
        return m

    def base_meshes(self, bd, base_meshes: list[Mesh], rot: int, shift: float, placements=()) -> list[Mesh]:
        """Far-ground collision, water and crowd resets and the kill floor for the base scene (after the
        backdrop and the dressing: ``placements``)."""
        far = self.far_ground(bd)
        out = [far]
        w = self.water()
        if w is not None:
            out.append(w)
        cr = self.crowd(placements)
        if cr is not None:
            out.append(cr)
        zs = [float(np.min(np.array(mm.verts)[:, 2])) for mm in base_meshes + [far]
              if mm.verts and "col = true" in mm.custom_data and mm.kind != "bounds"]
        zs.append(float(np.min(np.array(far.verts)[:, 2])))
        out.append(self.kill_floor(min(zs), rot, shift))
        return out


# ------------------------------------------------------------------------------------------ checks
def raster_tris(tris: np.ndarray, x0: float, y0: float, cell: float, nx: int, ny: int) -> np.ndarray:
    """Cells (nx x ny, centres x0 + (i + 0.5) cell, ...) whose centre lies inside the plan view of any of the
    triangles (m x 3 x 2+). Used by the checks on built and exported scenes."""
    out = np.zeros((nx, ny), dtype=bool)
    t = np.asarray(tris, dtype=np.float64)
    if not len(t):
        return out
    t = t[:, :, :2]
    a, b, c = t[:, 0], t[:, 1], t[:, 2]
    den = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    keep = np.abs(den) > 1e-9
    a, b, c, den = a[keep], b[keep], c[keep], den[keep]
    org = np.array([x0, y0])
    lo = np.ceil((np.minimum(np.minimum(a, b), c) - org) / cell - 0.5).astype(np.int64)
    hi = np.floor((np.maximum(np.maximum(a, b), c) - org) / cell - 0.5).astype(np.int64)
    lo, hi = np.maximum(lo, 0), np.minimum(hi, [nx - 1, ny - 1])
    span = hi - lo + 1
    small = (span[:, 0] <= 6) & (span[:, 1] <= 6)

    def inside(px, py, aa, bb, cc, dd):
        u = ((px - aa[..., 0]) * (cc[..., 1] - aa[..., 1]) - (cc[..., 0] - aa[..., 0]) * (py - aa[..., 1])) / dd
        v = ((bb[..., 0] - aa[..., 0]) * (py - aa[..., 1]) - (px - aa[..., 0]) * (bb[..., 1] - aa[..., 1])) / dd
        return (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)

    sel = np.flatnonzero(small & (span > 0).all(1))
    for di in range(6):
        for dj in range(6):
            i, j = lo[sel, 0] + di, lo[sel, 1] + dj
            ok = (i <= hi[sel, 0]) & (j <= hi[sel, 1])
            ins = ok & inside(x0 + (i + 0.5) * cell, y0 + (j + 0.5) * cell, a[sel], b[sel], c[sel], den[sel])
            out[i[ins], j[ins]] = True
    for k in np.flatnonzero(~small & (span > 0).all(1)):
        i0, j0, i1, j1 = lo[k, 0], lo[k, 1], hi[k, 0], hi[k, 1]
        px, py = np.meshgrid(x0 + (np.arange(i0, i1 + 1) + 0.5) * cell, y0 + (np.arange(j0, j1 + 1) + 0.5) * cell,
                             indexing="ij")
        out[i0:i1 + 1, j0:j1 + 1] |= inside(px, py, a[k], b[k], c[k], den[k])
    return out


def flood_from(seeds: np.ndarray, free: np.ndarray) -> np.ndarray:
    """Cells reachable (4-connected) through ``free`` cells from the seed cells."""
    reach = seeds & free
    while True:
        nxt = _dilate(reach, diagonal=False) & free
        if (nxt == reach).all():
            return reach
        reach = nxt


def enclosure(reset_tris: np.ndarray, ground_tris: np.ndarray, seeds_xy: np.ndarray, cell: float = 2.0,
              pad: float = 40.0) -> dict:
    """Can a car leave its layout without touching a reset, and is there ground under it everywhere it can go?

    Over the plan extent of the ground (plus ``pad``), cells under a reset face in plan (the lid) block; the region
    reachable from the seeds (points on the layout's road) through unblocked cells must not reach the grid's border
    (``escaped``), and every reached cell must have a ground triangle over it in plan (``holes``). Cracks narrower
    than ``cell`` may go unnoticed (the junction checks look at those)."""
    g = np.asarray(ground_tris, dtype=np.float64)
    lo = g[:, :, :2].reshape(-1, 2).min(0) - pad
    hi = g[:, :, :2].reshape(-1, 2).max(0) + pad
    nx, ny = (int(math.ceil(v)) for v in (hi - lo) / cell)
    blocked = raster_tris(reset_tris, lo[0], lo[1], cell, nx, ny)
    ground = raster_tris(g, lo[0], lo[1], cell, nx, ny)
    seeds = np.zeros((nx, ny), dtype=bool)
    ij = np.floor((np.asarray(seeds_xy, dtype=np.float64)[:, :2] - lo) / cell).astype(int)
    ok = (ij[:, 0] >= 0) & (ij[:, 0] < nx) & (ij[:, 1] >= 0) & (ij[:, 1] < ny)
    seeds[ij[ok, 0], ij[ok, 1]] = True
    reach = flood_from(seeds, ~blocked)
    escaped = bool(reach[0, :].any() or reach[-1, :].any() or reach[:, 0].any() or reach[:, -1].any())
    holes = reach & ~ground
    return {"escaped": escaped, "holes": int(holes.sum()),
            "holeAt": [[round(float(lo[0] + (i + 0.5) * cell), 1), round(float(lo[1] + (j + 0.5) * cell), 1)]
                       for i, j in np.argwhere(holes)[:5]],
            "seeds": int(seeds.sum()), "seedsBlocked": int((seeds & blocked).sum()),
            "reachHa": round(float(reach.sum()) * cell * cell / 1e4, 2)}
