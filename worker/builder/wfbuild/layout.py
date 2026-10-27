"""Layout objects (one set per layout): AI routes, checkpoints, start points, the base-scene xref.

Naming and object types follow Bugbear's example mod (mods/example/.../example_race_track.bgo3, read with
``bgo_read.py``), the handover's recipe and Toolbox Mz (see worker/README.md for the table):

- ``#ai_route_main`` / ``#ai_safe_main`` / ``#ai_race_main``: open quad-strip meshes with identical topology
  (one quad per AI sector, faces in driving order from the start line, the end overlapping the start);
  alternative routes are ``#ai_route_altN`` etc. for TrackModel ``aiIndex == -N``. Their first and last
  sector lines are exactly the main route's sector lines at the junction vertices.
- ``#checkpointNN`` (``_split``, ``_altN``): vertical planes across the whole blue route.
- ``#startpointNN`` and ``#startpointcarchange``: empties, local +Y facing the driving direction.
- ``#xref <slug>_base.scne`` with ``xdir = data/track/<slug>`` (CustomData ``vis = true|col = true``): the
  base scene as a subscene, as the example layouts reference ``exampletrack_base``.
- ``ai_path``: an open curve along the AI line for people editing with Toolbox's AI Route Generator. Curves
  are not exported.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np

from . import config as C
from .geometry import Mesh, Route, TrackGeometry


@dataclass
class Empty:
    name: str
    location: tuple[float, float, float]
    rotation_z: float = 0.0
    custom_data: str = ""
    display: str = "CUBE"
    size: float = 1.0
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    xdir: str | None = None  # Toolbox: folder prefixed to a "#xref <file>" subscene name


@dataclass
class Curve:
    name: str
    points: list[tuple[float, float, float]]
    cyclic: bool = False


@dataclass
class Layout:
    key: str          # forward | reverse
    name: str         # scene/file stem, e.g. <slug>_race
    reverse: bool
    meshes: list[Mesh] = field(default_factory=list)
    empties: list[Empty] = field(default_factory=list)
    curves: list[Curve] = field(default_factory=list)
    info: dict = field(default_factory=dict)


def alt_suffix(r: Route) -> str:
    return "main" if r.is_main else f"alt{-r.ai_index}"


class LayoutBuilder:
    def __init__(self, geo: TrackGeometry):
        self.geo = geo
        self.model = geo.model
        self.main = geo.main
        self.slug = self.model["slug"]
        self.alts = sorted((r for r in geo.routes if not r.is_main), key=lambda r: -r.ai_index)
        self.joins = sorted({int(j["sampleIndex"]) for r in self.alts for j in (r.start_join, r.end_join) if j})
        self.warnings: list[str] = []

    # ------------------------------------------------------------------------------- sector lines
    def _margins(self, r: Route) -> tuple[np.ndarray, np.ndarray]:
        """How far the blue AI route reaches beyond the left and right road edges at each sample of r: over the
        grass run-off, short of a wall and half way to another road (geometry.wall_plan ``ai_margin``). An
        imported layout's composite route takes them from the route samples it was assembled from."""
        cache = self.__dict__.setdefault("_margin_cache", {})
        if id(r) in cache:
            return cache[id(r)]
        plan = self.geo.wall_plan()
        if self.geo.by_id.get(r.id) is r:
            out = (plan[(r.index, 1)]["ai_margin"].copy(), plan[(r.index, -1)]["ai_margin"].copy())
        else:
            ix = self.geo.index
            ml, mr = np.empty(r.n), np.empty(r.n)
            for k in range(r.n):
                j = int(np.argmin(np.hypot(*(ix.xy - r.p[k, :2]).T)))
                src = self.geo.routes[int(ix.ri[j])]
                ml[k] = plan[(src.index, 1)]["ai_margin"][int(ix.si[j])]
                mr[k] = plan[(src.index, -1)]["ai_margin"][int(ix.si[j])]
            out = (ml, mr)
        cache[id(r)] = out
        return out

    def _narrow_folds(self, r: Route, seq: list[int], direction: int) -> None:
        """Where the wide blue route would make a sector fold (round a hairpin), narrow its run-off margins
        there (and either side) until every sector is a convex quad, rather than dropping sector lines."""
        ml, mr = self._margins(r)
        for _ in range(8):
            bad = [k for k, (a, b) in enumerate(itertools.pairwise(seq))
                   if not self._quad_ok(self._dline(r, a, "route", direction), self._dline(r, b, "route", direction))]
            if not bad:
                return
            for k in bad:
                for i in r.window(seq[k], 3 * C.AI_SECTOR_SAMPLES):
                    ml[i] = max(0.5, ml[i] * 0.7)
                    mr[i] = max(0.5, mr[i] * 0.7)

    def _halfwidths(self, r: Route, i: int, kind: str) -> tuple[float, float]:
        hw = r.w[i] / 2.0
        if kind == "route":
            ml, mr = self._margins(r)
            hl, hr = hw + float(ml[i]), hw + float(mr[i])
        elif kind == "safe":
            hl = hr = hw - C.AI_SAFE_INSET
        else:
            hl = hr = hw - C.AI_RACE_INSET
        k = r.curvature[i]
        if abs(k) > 1e-6:
            lim = 0.8 / abs(k)  # keep sector lines from crossing on the inside of tight corners
            if k > 0:
                hl = min(hl, lim)
            else:
                hr = min(hr, lim)
        return hl, hr

    def _line(self, r: Route, i: int, kind: str) -> tuple[np.ndarray, np.ndarray]:
        hl, hr = self._halfwidths(r, i, kind)
        c = r.p[i]
        n = np.append(r.nl[i], 0.0)
        up = np.array([0.0, 0.0, float(r.slope[i])])  # follow the camber (imported tracks)
        return c + n * hl + up * hl, c - n * hr - up * hr

    @staticmethod
    def _quad_ok(a: tuple[np.ndarray, np.ndarray], b: tuple[np.ndarray, np.ndarray]) -> bool:
        """The sector between two lines is a strictly convex quad facing up (bgeometry asserts this)."""
        q = [a[0][:2], a[1][:2], b[1][:2], b[0][:2]]
        for k in range(4):
            o, p, r = q[k - 1], q[k], q[(k + 1) % 4]
            if (p[0] - o[0]) * (r[1] - p[1]) - (p[1] - o[1]) * (r[0] - p[0]) <= 1e-6:
                return False
        return True

    def _valid_sequence(self, items: list, lines_of, fixed: set[int]) -> list:
        """Drop sector lines until every sector of every strip (route, safe, race) is a valid quad.
        ``fixed`` holds positions that must stay (the ends and the junction lines)."""
        keep = list(range(len(items)))
        changed = True
        while changed:
            changed = False
            for a, b in itertools.pairwise(keep):
                if all(self._quad_ok(lines_of(items[a], k), lines_of(items[b], k)) for k in ("route", "safe", "race")):
                    continue
                drop = b if b not in fixed else (a if a not in fixed else None)
                if drop is None:
                    self.warnings.append(f"ai-sector-invalid: sector between fixed lines {a} and {b}")
                    continue
                keep.remove(drop)
                changed = True
                break
        return [items[k] for k in keep]

    def _strip(self, name: str, lines: list[tuple[np.ndarray, np.ndarray]], custom: str, mat: str) -> Mesh:
        mesh = Mesh(name, custom, kind="ai")
        ids = [(mesh.vert(a), mesh.vert(b)) for a, b in lines]
        for k in range(len(ids) - 1):
            (l0, r0), (l1, r1) = ids[k], ids[k + 1]
            mesh.face((l0, r0, r1, l1), mat, [(0, k), (1, k), (1, k + 1), (0, k + 1)])
        return mesh

    def chord_error(self, r: Route, a: int, b: int) -> tuple[float, float]:
        """How well one AI sector from sample ``a`` to ``b`` (route order, wrapping on a closed route) stands for
        the road: the largest distance between a safe or race edge's chord (the straight sector edge) and that
        edge's intended position at the samples between, and the turn of the sector lines (degrees). The game
        constrains its racing line at the sector lines only, so a coarse sector round a tight corner or through a
        chicane lets the line (and the AI's corner speed) take a short cut the road doesn't have."""
        n = r.n
        m = (b - a) % n if r.closed else b - a
        lines = {kind: (self._line(r, a % n, kind), self._line(r, b % n, kind)) for kind in ("safe", "race")}
        worst = 0.0
        for t in range(1, m):
            i = (a + t) % n
            for kind, (la, lb) in lines.items():
                q = self._line(r, i, kind)
                for side in (0, 1):
                    p0, p1, x = la[side][:2], lb[side][:2], q[side][:2]
                    d = p1 - p0
                    L2 = float(d @ d)
                    f = 0.0 if L2 < 1e-12 else min(1.0, max(0.0, float((x - p0) @ d) / L2))
                    worst = max(worst, float(np.hypot(*(x - p0 - d * f))))
        na, nb = r.nl[a % n], r.nl[b % n]
        turn = math.degrees(abs(math.atan2(na[0] * nb[1] - na[1] * nb[0], float(na @ nb))))
        return worst, turn

    def _sector_ok(self, r: Route, a: int, b: int) -> bool:
        err, turn = self.chord_error(r, a, b)
        return err <= C.AI_CHORD_TOL and turn <= C.AI_SECTOR_MAX_TURN

    def _adaptive(self, r: Route, stops: list[int], step, rev: bool = False) -> list[int]:
        """Sector line offsets from 0 to ``stops[-1]`` (ascending), through every one of ``stops``: each
        sector as long as AI_SECTOR_SAMPLES allows while it follows the road (_sector_ok), down to
        AI_SECTOR_MIN_SAMPLES in tight corners; no line within 3 samples before a stop (a junction line).
        ``step(k)`` maps an offset to the route sample; ``rev``: offsets run against the route's sample order."""
        def ok(k: int, k2: int) -> bool:
            a, b = step(k), step(k2)
            return self._sector_ok(r, *((b, a) if rev else (a, b)))

        out = [0]
        for stop in stops:
            k = out[-1]
            while k < stop:
                hi = min(stop, k + C.AI_SECTOR_SAMPLES)
                nxt = hi
                while nxt - k > C.AI_SECTOR_MIN_SAMPLES and not ok(k, nxt):
                    nxt -= 1
                if nxt != stop and stop - nxt < 3:
                    # don't leave a sliver before the stop: end there if it's close, else leave room
                    nxt = stop if stop - 3 <= k or (stop - k <= C.AI_SECTOR_SAMPLES and ok(k, stop)) else stop - 3
                out.append(nxt)
                k = nxt
        return out

    def main_sequence(self, i0: int, direction: int, extra: int = 0) -> list[int]:
        """Main-route sample indices of the AI sector lines, in driving order from sample ``i0`` (adaptive spacing,
        _adaptive, through the junction lines); the strip runs AI_END_OVERLAP past the line (plus ``extra``
        samples, see ai_meshes)."""
        r = self.main
        n = r.n
        ov = round(C.AI_END_OVERLAP / 2.0) + extra
        forced = sorted({((j - i0) * direction) % n for j in self.joins} - {0})
        # Past the line the strip overlaps its own start. A line there must not repeat a start line (bgeometry
        # asserts "AI sector data is non continuous" on repeated vertices): the lap runs to one sample past the
        # line, the overlap's lines sit one sample past the start lines, and the strip ends at least 2 samples
        # from one.
        ks = self._adaptive(r, forced + [n + 1], lambda k: (i0 + direction * k) % n, direction < 0)
        lap = [k for k in ks if k < n]
        end = ov
        while any(end == q or abs(end - q - 1) < 2 for q in lap):
            end += 1
        ks += [n + q + 1 for q in lap if 0 < q and q + 1 < end - 1] + [n + end]
        return [(i0 + direction * k) % n for k in ks]

    def alt_sequence(self, r: Route) -> list[int]:
        lo, hi = 0, r.n - 1
        for cut in self.geo.cuts:
            if cut.route is r and cut.kind == "mouth":
                if cut.sample < r.n // 2:
                    lo = max(lo, cut.sample)
                else:
                    hi = min(hi, cut.sample)
        # lines between the junction mouths only (adaptive, as the main route); none where the branch still runs
        # alongside the main road
        ks = sorted({0, r.n - 1} | {lo + k for k in self._adaptive(r, [hi - lo], lambda k: lo + k)})
        return [k for i, k in enumerate(ks) if i == 0 or k - ks[i - 1] >= 2 or k == r.n - 1]

    def _dline(self, r: Route, i: int, kind: str, direction: int) -> tuple[np.ndarray, np.ndarray]:
        """A sector line as (driver's left, driver's right) for the given driving direction."""
        a, b = self._line(r, i, kind)
        return (a, b) if direction > 0 else (b, a)

    @staticmethod
    def needs_otherway(lines: list[tuple[np.ndarray, np.ndarray]]) -> bool:
        """bgeometry ignores the face order of ``#ai_route_main``: it stores the route in its own canonical
        orientation (the first sector's right end south of, Blender -y, the last sector's left end) unless the
        mesh carries ``otherway = true``, and then in the opposite one; the game drives the stored order.
        (Toolbox Mz's SCNE importer reads ``otherway`` back with the same test; every stock layout's checkpoints
        run in stored order.) So a strip built in the driving order needs ``otherway`` exactly when it is not
        canonical. ``lines`` are (left, right) in driving order."""
        return float(lines[0][1][1]) > float(lines[-1][0][1])

    def ai_meshes(self, layout: Layout, i_line: int, direction: int) -> None:
        """AI strips in driving order (from the start line, ``direction`` along s). ``otherway = true`` on
        ``#ai_route_main`` wherever bgeometry would otherwise store the route the other way (needs_otherway):
        a forward layout can need it and a reverse one can do without it."""
        # bgeometry orients each strip by its own canonical test and applies the route's otherway to all three, so
        # the route, safe and race strips must agree on it (they can differ when the start line runs nearly
        # east-west and the strips' widths decide the test: Navarra's Short; bgeometry then asserts "safe area did
        # not overlap all sectors"). Run the strips further past the line until they agree.
        for extra in range(0, 4 * C.AI_SECTOR_SAMPLES + 1, 2):
            seq = self.main_sequence(i_line, direction, extra)
            self._narrow_folds(self.main, seq, direction)
            fixed = {0, len(seq) - 1} | {k for k, i in enumerate(seq) if i in self.joins}
            seq = self._valid_sequence(seq, lambda i, kind: self._dline(self.main, i, kind, direction), fixed)
            tests = {self.needs_otherway([self._dline(self.main, i, kind, direction) for i in seq])
                     for kind in ("route", "safe", "race")}
            if len(tests) == 1:
                break
        else:
            self.warnings.append("ai-otherway-ambiguous: the route, safe and race strips disagree on bgeometry's "
                                 "canonical orientation")
        route_lines = [self._dline(self.main, i, "route", direction) for i in seq]
        otherway = self.needs_otherway(route_lines)
        custom_route = "otherway = true" if otherway else ""
        mats = {"route": "ai_route", "safe": "ai_safe", "race": "ai_race"}
        for kind in ("route", "safe", "race"):
            lines = [self._dline(self.main, i, kind, direction) for i in seq]
            layout.meshes.append(self._strip(f"#ai_{kind}_main", lines,
                                             custom_route if kind == "route" else "", mats[kind]))
        layout.info["mainSectors"] = len(seq) - 1
        layout.info["mainSectorSamples"] = seq
        fits = [self.chord_error(self.main, *((a, b) if direction > 0 else (b, a))) for a, b in itertools.pairwise(seq)]
        layout.info["aiChordErrM"] = round(max(e for e, _ in fits), 3)
        layout.info["aiMaxTurnDeg"] = round(max(t for _, t in fits), 1)
        layout.info["otherway"] = otherway
        line_s = float(self.main.s[i_line])
        for r in self.alts:
            sj, ej = int(r.start_join["sampleIndex"]), int(r.end_join["sampleIndex"])
            # The first and last sector lines are the main route's lines at the junction vertices.
            items = [("main", sj)] + [("alt", k) for k in self.alt_sequence(r)[1:-1]] + [("main", ej)]
            if direction < 0:
                items = items[::-1]

            def line_of(item, kind, r=r):
                return self._dline(self.main if item[0] == "main" else r, item[1], kind, direction)

            items = self._valid_sequence(items, line_of, {0, len(items) - 1})
            cross = self._spans_line(r, line_s)
            for kind in ("route", "safe", "race"):
                lines = [line_of(it, kind) for it in items]
                custom = "crossstart = true" if (kind == "route" and cross) else ""
                layout.meshes.append(self._strip(f"#ai_{kind}_{alt_suffix(r)}", lines, custom, mats[kind]))
            if cross:
                layout.info.setdefault("warnings", []).append(
                    f"alt-crosses-start: {r.id} bypasses the start/finish line in {layout.key}; crossstart = true "
                    "is set but no proxy checkpoint is generated (unverified in game)")
        # The AI line as an open curve for Toolbox's AI Route Generator (not exported), in driving order.
        layout.curves.append(Curve("ai_path", [tuple(self.main.p[i]) for i in seq], False))
        for r in self.alts:
            layout.curves.append(Curve(f"ai_path_{alt_suffix(r)}", [tuple(p) for p in r.p], False))

    def _spans_line(self, r: Route, line_s: float) -> bool:
        a, b = float(r.start_join["s"]), float(r.end_join["s"])
        L = self.main.length
        return ((line_s - a) % L) < ((b - a) % L)

    # --------------------------------------------------------------------------------- checkpoints
    def _bypass_spans(self) -> list[tuple[float, float, Route]]:
        return [(float(r.start_join["s"]), float(r.end_join["s"]), r) for r in self.alts
                if r.start_join and r.end_join]

    def _relocate(self, s: float) -> float:
        L = self.main.length
        for a, b, _ in self._bypass_spans():
            span = (b - a) % L
            if (s - a + 15.0) % L < span + 30.0:
                return (b + 20.0) % L
        return s

    def _checkpoint_mesh(self, name: str, r: Route, i: int, direction: int) -> Mesh:
        hl, hr = self._halfwidths(r, i, "route")
        hl, hr = hl + C.CHECKPOINT_MARGIN, hr + C.CHECKPOINT_MARGIN
        c = r.p[i]
        n = np.append(r.nl[i], 0.0)
        mesh = Mesh(name, "", kind="checkpoint")
        mesh.origin = tuple(c)
        up = np.array([0.0, 0.0, float(r.slope[i])])  # the plane's foot follows the camber
        a, b = c + n * hl + up * hl, c - n * hr - up * hr
        lo, hi = -C.CHECKPOINT_BELOW, C.CHECKPOINT_ABOVE
        ids = (mesh.vert(a + (0, 0, lo)), mesh.vert(b + (0, 0, lo)), mesh.vert(b + (0, 0, hi)), mesh.vert(a + (0, 0, hi)))
        fwd = np.append(r.tangent[i] * direction, 0.0)
        mesh.oriented_face(ids, "checkpoint", fwd, [(0, 0), (1, 0), (1, 1), (0, 1)])
        return mesh

    def checkpoints(self, layout: Layout, line_s: float, first_s: float, direction: int) -> None:
        L = self.main.length

        def lap_pos(s: float) -> float:
            return ((s - line_s) * direction) % L

        main_cps = [c for c in self.model["checkpoints"] if c["route"] == self.main.id]
        if direction > 0:
            inter = [float(c["s"]) for c in main_cps if not c.get("isFinish") and int(c["index"]) != 0]
        else:
            inter = [float(c["s"]) for c in main_cps if not c.get("isFinish")]
        moved = []
        for s in inter:
            s2 = self._relocate(s)
            if s2 != s:
                layout.info.setdefault("relocatedCheckpoints", []).append({"from": round(s, 2), "to": round(s2, 2)})
            moved.append(s2)
        first = lap_pos(first_s)
        entries = [(first, self.main, first_s)]
        for s in moved:
            p = lap_pos(s)
            if p - first < 40.0 or L - p < 40.0 or any(abs(p - e[0]) < 30.0 for e in entries):
                continue
            entries.append((p, self.main, s))
        for c in self.model["checkpoints"]:
            if c["route"] == self.main.id:
                continue
            r = self.geo.by_id[c["route"]]
            a, b = float(r.start_join["s"]), float(r.end_join["s"])
            frac = float(c["s"]) / max(r.length, 1e-6)
            span = (b - a) % L
            p = lap_pos((a + frac * span) % L) if direction > 0 else lap_pos((b - (1 - frac) * span) % L)
            entries.append((p, r, float(c["s"])))
        entries.sort(key=lambda e: e[0])
        entries.append((L, self.main, line_s))  # the start/finish line is the last checkpoint
        mains = [e for e in entries if e[1] is self.main]
        mid = min(mains[1:-1], key=lambda e: abs(e[0] - L / 2.0)) if len(mains) > 2 else None
        names = []
        for k, (p, r, s) in enumerate(entries):
            name = f"#checkpoint{k + 1:02d}"
            if r is self.main and (k == 0 or e_is(mid, (p, r, s))):
                name += "_split"
            if not r.is_main:
                name += f"_{alt_suffix(r)}"
            i = r.index_at(s)
            layout.meshes.append(self._checkpoint_mesh(name, r, i, direction))
            names.append({"name": name, "route": r.id, "s": round(s, 2), "lapPos": round(p, 2)})
        layout.info["checkpoints"] = names

    # ----------------------------------------------------------------------------------- grid
    def startpoints(self, layout: Layout, direction: int, grid: dict | None = None) -> tuple[float, float]:
        """Start points from the TrackModel's grid (``grid``: an imported layout's own grid, slot s along its own
        lap). Returns the start/finish line's s and the first checkpoint's s on self.main."""
        g = grid or self.model["grid"]
        slots = g["slots"]
        main = self.main
        if direction > 0:
            for sl in slots:
                p = sl["position"]
                layout.empties.append(Empty(sl["name"], (p[0], p[1], p[2] + C.STARTPOINT_LIFT),
                                            float(sl["heading"]) - math.pi / 2.0, scale=(1.0, 2.25, 0.75)))
            cc = g["carChange"]
            p = cc["position"]
            layout.empties.append(Empty(cc["name"], (p[0], p[1], p[2] + C.STARTPOINT_LIFT),
                                        float(cc["heading"]) - math.pi / 2.0, scale=(1.0, 2.25, 0.75)))
            pole_s = max(float(sl["s"]) for sl in slots)
            return 0.0, pole_s + 10.0
        # Reverse: the same clear stretch of road, driven the other way. The forward back row becomes the
        # pole row, the line moves 4 m behind the new back row, the first checkpoint 10 m past the pole.
        rows: dict[int, list[dict]] = {}
        for sl in slots:
            rows.setdefault(int(sl["row"]), []).append(sl)
        order = sorted(rows, reverse=True)
        back_s = min(float(sl["s"]) for sl in slots)
        front_s = max(float(sl["s"]) for sl in slots)
        i_pole = main.index_at(back_s)
        ahead = [(i_pole - k) % main.n for k in range(100)]
        inside = "left" if float(np.sum(main.curvature[ahead])) > 0 else "right"
        k = 0
        for row in order:
            pair = sorted(rows[row], key=lambda sl: 0 if sl["side"] == inside else 1)
            for sl in pair:
                k += 1
                p = sl["position"]
                layout.empties.append(Empty(f"#startpoint{k:02d}", (p[0], p[1], p[2] + C.STARTPOINT_LIFT),
                                            float(sl["heading"]) + math.pi / 2.0, scale=(1.0, 2.25, 0.75)))
        line_s = (front_s + 4.0) % main.length
        i = main.index_at(line_s)
        layout.empties.append(Empty("#startpointcarchange", (main.p[i, 0], main.p[i, 1], main.p[i, 2] + C.STARTPOINT_LIFT),
                                    float(main.h[i]) + math.pi / 2.0, scale=(1.0, 2.25, 0.75)))
        return line_s, (back_s - 10.0) % main.length

    # ------------------------------------------------------------------- imported multi-layout tracks
    def lap_checkpoints(self, layout: Layout, r: Route, first_s: float, d: dict | None = None) -> None:
        """Checkpoints of an imported track's layout along its own closed route: the first one past the pole,
        then evenly spaced (about as far apart as the main route's), the start/finish line last; plus one inside
        every stretch of the lap a shortcut skips (``shortcut_spans`` of the layout ``d``: an untaken branch or
        link road, the main road past a coned-off branch, the open pit lane), so cutting through the cones
        misses a checkpoint (Chris 2026-10-26: Oulton Trackday's bus stop). The first and the one nearest half
        way are split points."""
        main_cps = [c for c in self.model["checkpoints"] if c["route"] == self.geo.main.id]
        spacing = self.geo.main.length / max(len(main_cps), 1)
        L = r.length
        n = max(3, int(round((L - first_s) / spacing)))
        pos = [first_s + k * (L - first_s - 40.0) / n for k in range(n)]
        pos = [self._on_built_road(r, s) for s in pos] + [L]  # the start/finish line stays where it is
        spans = shortcut_spans(self.model, d, self._pit_lanes(), L) if d is not None else []
        for sp in spans:
            a, skip = sp["fromLap"], sp["skipM"]
            if _span_has(pos, a, skip, L):
                continue
            want = (a + skip / 2.0) % L
            got = self._on_built_road(r, want, step=2.0, reach=max(skip / 2.0 - SHORTCUT_CP_MARGIN_M - 1.0, 0.0))
            if not _span_has([got], a, skip, L) or got <= pos[0] + 1e-6:
                self.warnings.append(f"shortcut {sp['via']}: no checkpoint fits inside lap {a:.0f}+{skip:.0f} m")
                continue
            # move an evenly spaced one nearby rather than crowd it, unless another shortcut needs that one
            near = [k for k in range(1, len(pos) - 1) if abs(pos[k] - got) < 30.0
                    and not any(_span_has([pos[k]], o["fromLap"], o["skipM"], L) for o in spans if o is not sp)]
            if near:
                pos[near[0]] = got
            else:
                pos.insert(-1, got)
            pos[1:-1] = sorted(pos[1:-1])
            sp["checkpointLap"] = round(got, 2)
        if spans:
            layout.info["shortcuts"] = spans
        n = len(pos) - 1
        mid = min(range(1, n), key=lambda k: abs(pos[k] - L / 2.0)) if n > 2 else None
        names = []
        for k, s in enumerate(pos):
            name = f"#checkpoint{k + 1:02d}" + ("_split" if k in (0, mid) else "")
            layout.meshes.append(self._checkpoint_mesh(name, r, r.index_at(s % L), 1))
            names.append({"name": name, "route": r.id, "s": round(s % L, 2), "lapPos": round(s, 2)})
        layout.info["checkpoints"] = names

    def _pit_lanes(self) -> list[dict]:
        """The imported track's pit lanes that cars can drive through (pits.OPEN_LANES: no closures)."""
        from . import pits as PITS  # noqa: PLC0415

        lanes = ((self.geo.imported or {}).get("pits") or {}).get("pitLanes", [])
        return [x for x in lanes if PITS.OPEN_LANES or x.get("mode", "closed") != "closed"]

    def _on_built_road(self, r: Route, s: float, step: float = 4.0, reach: float = 40.0) -> float:
        """``s``, or the nearest s within ``reach`` (in ``step`` steps) whose point of the layout's route lies on
        a built road: within half a width less 0.5 m of a road sample that is built (a branch's samples beyond its
        junction cut aren't; Oulton Park's Island Hairpin link ends on Hilltop 30 m after its cut) or on a paved
        apron link."""
        geo = self.geo
        keep = [geo.svalid[rr.index] | (rr.index in geo.apron_routes) for rr in geo.routes]
        pts = [(rr.p[k, :2], rr.w[k] / 2.0 - 0.5) for rr, k in zip(geo.routes, keep)]
        L = r.length
        for d in [0.0] + [sg * k * step for k in range(1, int(reach / step) + 1) for sg in (1, -1)]:
            q = r.p[r.index_at((s + d) % L), :2]
            if any(len(P) and float(np.min(np.hypot(*(P - q).T) - hw)) <= 0.0 for P, hw in pts):
                return s + d
        self.warnings.append(f"checkpoint at s {s:.0f} of {r.id}: no built road within {reach:.0f} m")
        return s

    # --------------------------------------------------------------------------------- junction cones
    def _keep_out(self, r: Route, lay: Layout) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Where no cone of layout ``lay`` (driving ``r``) may stand besides its road: plan quads (k x 4 x 2) of its
        blue AI route strips (``blue`` marks them) and of its run-off reach (road edge out to the wall plan's
        barrier or the end of the measured trap or apron) along every road sample it drives; and its road as
        centreline segments (m x 2 x 2) with their half widths (m)."""
        geo = self.geo
        quads = [np.array([m.verts[i][:2] for i in f]) for m in lay.meshes
                 if m.kind == "ai" and m.name.startswith("#ai_route_") for f in m.faces if len(f) == 4]
        n_blue = len(quads)
        seg_a, seg_b, seg_hw = _segments(r)
        ro = getattr(geo, "runoff", None)
        for (ri, side), pl in geo.wall_plan().items():
            rr = pl["route"]
            on = geo.svalid[rr.index] & (_polyline_dist(rr.p[:, :2], seg_a, seg_b)[0] < 0.5)
            if not on.any():
                continue
            reach = np.where(pl["ok"], np.nan_to_num(pl["offset"]), 0.0)
            if ro is not None and ro.active:
                reach = np.maximum(reach, ro.run_off_end(ri, side))
            edge = rr.left if side > 0 else rr.right
            far = edge[:, :2] + pl["out"] * reach[:, None]
            for a in range(rr.segs):
                b = (a + 1) % rr.n
                if on[a] and on[b] and max(reach[a], reach[b]) > 0.05:
                    quads.append(np.array([edge[a, :2], edge[b, :2], far[b], far[a]]))
        q = np.array(quads).reshape(-1, 4, 2)
        blue = np.arange(len(q)) < n_blue
        return q, blue, np.stack([seg_a, seg_b], axis=1), seg_hw

    def junction_arms(self) -> list[dict]:
        """Every junction (a branch or extra road leaving or rejoining the main route): its plan point and its
        three arms as (route, first sample, step), each walking away from the junction."""
        out = []
        m = self.geo.main
        for br in self.geo.routes:
            if br.is_main or br.closed:
                continue
            for join, own in ((br.start_join, (br, 0, 1)), (br.end_join, (br, br.n - 1, -1))):
                if not join or join.get("route") != "main":
                    continue
                i = int(join["sampleIndex"])
                out.append({"branch": br.id, "kind": "split" if own[2] > 0 else "merge", "at": m.p[i, :2].copy(),
                            "arms": [(m, i, 1), (m, i, -1), own]})
        return out

    def junction_cones(self, r: Route, lay: Layout) -> list[Empty]:
        """Rows of stock traffic cones (CONE_ASSET) across the mouth of every road this layout does not take, at
        every junction it passes (Chris 2026-10-26: cones "on the incorrect corner to make it obvious which way
        around the circuit will go", as Oulton Park's International Trackday first had). Each arm of a junction
        the layout's road runs through is walked away from it, up to CONE_REACH_M; the row goes at the first built
        sample (a built road or paved link) where every cone of a row across that road (CONE_SPACING_M apart,
        edge to edge) is CONE_CLEAR_M clear of the layout's road and outside its run-off reach and, where there
        is room, its blue AI route (_keep_out). Arms the layout drives are skipped; neighbouring junctions that
        share a mouth share its row. Layout scene props: each layout has its own."""
        geo = self.geo
        quads, blue, segs, seg_hw = self._keep_out(r, lay)
        qc = quads.mean(1) if len(quads) else np.zeros((0, 2))
        rows: list[tuple[str, np.ndarray]] = []
        cones: list[Empty] = []
        info = []
        for jn in self.junction_arms():
            d0, j0 = _polyline_dist(jn["at"][None, :], segs[:, 0], segs[:, 1])
            if float(d0[0]) > float(seg_hw[j0[0]]):
                continue  # the junction isn't on the layout's road: it doesn't pass it
            near = np.hypot(*(segs.mean(1) - jn["at"]).T) < C.CONE_REACH_M + 80.0
            sa, sb, shw = segs[near, 0], segs[near, 1], seg_hw[near]
            close = np.hypot(*(qc - jn["at"]).T) < C.CONE_REACH_M + 120.0
            for arm, i0, step in jn["arms"]:
                built = geo.svalid[arm.index] | (arm.index in geo.apron_routes)
                walk = []
                for k in range(arm.n):
                    i = (i0 + step * k) % arm.n
                    if (not arm.closed and not 0 <= i0 + step * k < arm.n) or \
                            arm.s_dist(arm.s[i], arm.s[i0]) > C.CONE_REACH_M:
                        break
                    walk.append(i)
                k_drive = walk[min(len(walk) - 1, 15)]
                if float(_polyline_dist(arm.p[k_drive, None, :2], sa, sb)[0][0]) < 0.3:
                    continue  # the layout drives this arm (30 m on, its centreline is the layout's)
                row = None
                # off the blue AI route too where it can be; a short link between two close roads may lie wholly
                # inside it (the AI route reaches half way to another road: Cadwell Park's Club and Woodlands links)
                for qn in (quads[close], quads[close & ~blue]):
                    for i in walk:
                        if not built[i]:
                            continue
                        pts = _cone_row(arm, i)
                        d, j = _polyline_dist(pts, sa, sb)
                        if float((d - shw[j]).min()) >= C.CONE_CLEAR_M and not _in_quads(pts, qn).any():
                            row = (i, pts)
                            break
                    if row is not None:
                        break
                if row is None:
                    self.warnings.append(f"cones: no room for a row across {arm.id} at the {jn['branch']} "
                                         f"{jn['kind']} within {C.CONE_REACH_M:.0f} m; none placed")
                    continue
                i, pts = row
                c = pts.mean(0)
                if any(rid == arm.id and float(np.hypot(*(c - rc))) < 10.0 for rid, rc in rows):
                    continue  # the same mouth, seen from a neighbouring junction
                rows.append((arm.id, c))
                yaw = math.atan2(arm.tangent[i][1], arm.tangent[i][0])
                z = np.interp(np.linspace(0.04, 0.96, len(pts)), [0.0, 1.0], [arm.left[i, 2], arm.right[i, 2]])
                for q, zq in zip(pts, z):
                    cones.append(Empty(f"#xref ob/{C.CONE_ASSET}.scne", (float(q[0]), float(q[1]), float(zq)), yaw,
                                       display="PLAIN_AXES", size=0.5))
                info.append({"junction": f"{jn['branch']} {jn['kind']}", "route": arm.id, "sample": int(i),
                             "cones": len(pts), "at": [round(float(v), 1) for v in c]})
        lay.info["coneRows"] = info
        lay.info["cones"] = len(cones)
        return cones

    def _build_imported(self, defs: list[dict]) -> list[Layout]:
        """One layout per TrackModel ``layouts`` entry (imported tracks with alternative layouts): the main
        layout drives the main route only (no alternative-route strips), each alternative one its own closed
        route (its sections from its start/finish line: the branch and the main route). A layout with its own
        line (``startFinish``) gets its own grid there; the others share the main line and grid."""
        out = []
        saved = (self.main, self.alts, self.joins)
        for d in defs:
            is_main = d["id"] == "main"
            lay = Layout("forward" if is_main else d["id"], f"{self.slug}_race" if is_main else f"{self.slug}_{d['id']}",
                         False)
            # the main layout too runs from its own timing line when it has one (a real track: behind its grid,
            # which stands behind the real start line; pipeline._layout_grids)
            r = saved[0] if is_main and not d.get("startFinish") else composite_route(self.model, d)
            self.main, self.alts, self.joins = r, [], []
            line_s, first_s = self.startpoints(lay, 1, d.get("grid"))
            if d.get("startFinish"):
                lay.info["startFinish"] = d["startFinish"]
            i_line = r.index_at(line_s)
            lay.info.update({"title": d["name"], "lapLengthM": round(r.length, 1), "lineS": round(line_s, 3)})
            self.ai_meshes(lay, i_line, 1)
            self.lap_checkpoints(lay, r, first_s, d)
            lay.empties.append(Empty(f"#xref {self.slug}_base.scne", (0.0, 0.0, 0.0), 0.0, "vis = true|col = true",
                                     display="PLAIN_AXES", size=5.0, xdir=f"data/track/{self.slug}"))
            lay.empties += self.junction_cones(r, lay)
            if self.warnings:
                lay.info.setdefault("warnings", []).extend(self.warnings)
                self.warnings = []
            out.append(lay)
        self.main, self.alts, self.joins = saved
        return out

    # ------------------------------------------------------------------------------------- layouts
    def build(self) -> list[Layout]:
        if self.model.get("layouts"):
            return self._build_imported(self.model["layouts"])
        layouts = [Layout("forward", f"{self.slug}_race", False)]
        if self.model.get("reverse"):
            layouts.append(Layout("reverse", f"{self.slug}_race_reverse", True))
        for lay in layouts:
            direction = -1 if lay.reverse else 1
            line_s, first_s = self.startpoints(lay, direction)
            i_line = self.main.index_at(line_s)
            lay.info["lineS"] = round(line_s, 3)
            self.ai_meshes(lay, i_line, direction)
            self.checkpoints(lay, line_s, first_s, direction)
            lay.empties.append(Empty(f"#xref {self.slug}_base.scne", (0.0, 0.0, 0.0), 0.0, "vis = true|col = true",
                                     display="PLAIN_AXES", size=5.0, xdir=f"data/track/{self.slug}"))
            lay.meshes += self.geo.build_layout_jumps(lay.key)
            if self.warnings:
                lay.info.setdefault("warnings", []).extend(self.warnings)
                self.warnings = []
        return layouts


SHORTCUT_MIN_SKIP_M = 30.0  # a shortcut may skip at most this much of the lap without passing a checkpoint
SHORTCUT_SLACK = 1.1  # another road counts as a shortcut when it's at most this much longer than what it skips
SHORTCUT_CP_MARGIN_M = 10.0  # the checkpoint on a skipped stretch stands at least this far inside it


def _span_has(pos: list[float], a: float, skip: float, L: float) -> bool:
    """Whether a lap position of ``pos`` (L: the start/finish line) lies inside the lap stretch from ``a``
    ``skip`` metres on, SHORTCUT_CP_MARGIN_M clear of its ends."""
    return any(SHORTCUT_CP_MARGIN_M < (p - a) % L < skip - SHORTCUT_CP_MARGIN_M for p in pos)


def shortcut_spans(model: dict, d: dict, pit_lanes: list[dict], lap_len: float | None = None) -> list[dict]:
    """Every stretch of an imported layout's lap that another road lets a car skip. The road network is the main
    loop, the branches (joined to it) and the open pit lanes (``entrySM`` to ``exitSM``). Roads the layout
    doesn't drive (an untaken branch or link road, the main road past the layout's own branch, the pit lane),
    alone or chained, that join its lap at lap positions a and b, at most SHORTCUT_SLACK times as long as the
    lap from a to b, skip it when that is more than SHORTCUT_MIN_SKIP_M. Returns [{"fromLap", "toLap", "skipM",
    "pathM", "via"}], lap positions from the layout's timing line (scaled to ``lap_len``, its built route)."""
    routes = {r["id"]: r for r in model["routes"]}
    Lm = float(routes["main"]["lengthM"])
    secs = d["sections"]
    lap = sum(float(x["toS"]) - float(x["fromS"]) for x in secs)
    k_lap = (lap_len or lap) / lap
    nodes: list[float] = []  # main s of every junction and section end

    def node(s: float) -> int:
        s %= Lm
        for i, v in enumerate(nodes):
            if min(abs(v - s), Lm - abs(v - s)) < 0.5:
                return i
        nodes.append(s)
        return len(nodes) - 1

    other = []  # (node, node, length, label) of the roads off the main loop
    for rid, r in routes.items():
        sj, ej = r.get("startJoin"), r.get("endJoin")
        if rid == "main" or not sj or not ej or sj.get("route") != "main" or ej.get("route") != "main":
            continue
        other.append((node(float(sj["s"])), node(float(ej["s"])), float(r["lengthM"]), rid))
    for x in pit_lanes:
        a, b = x.get("entrySM"), x.get("exitSM")
        if a is not None and b is not None:
            other.append((node(float(a)), node(float(b)), (float(b) - float(a)) % Lm, f"pit lane {x.get('id', '')}".strip()))
    for x in secs:
        if x["route"] == "main":
            node(float(x["fromS"]))
            node(float(x["toS"]))
    driven = {x["route"] for x in secs}
    edges: dict[int, list[tuple[int, float, str]]] = {}

    def edge(a: int, b: int, length: float, label: str) -> None:
        edges.setdefault(a, []).append((b, length, label))
        edges.setdefault(b, []).append((a, length, label))

    def on_lap(s: float) -> bool:
        return any(float(x["fromS"]) - 1e-6 <= s <= float(x["toS"]) + 1e-6 for x in secs if x["route"] == "main")

    order = sorted(range(len(nodes)), key=lambda i: nodes[i])
    for i, j in zip(order, order[1:] + order[:1]):  # the main loop between neighbouring nodes
        a, b = nodes[i], nodes[j]
        length = (b - a) % Lm
        if length > 1e-6 and not on_lap((a + length / 2.0) % Lm):
            edge(i, j, length, f"main {a:.0f}-{b:.0f}")
    for a, b, length, label in other:
        if label not in driven:
            edge(a, b, length, label)
    lap_pos: dict[int, list[float]] = {}  # lap positions of the nodes the lap passes
    off = 0.0
    for x in secs:
        f, t = float(x["fromS"]), float(x["toS"])
        if x["route"] == "main":
            for i, v in enumerate(nodes):
                if f - 1e-6 <= v <= t + 1e-6:
                    lap_pos.setdefault(i, []).append(off + v - f)
        off += t - f
    lap_pos = {i: sorted({round(p % lap, 3) for p in ps}) for i, ps in lap_pos.items()}
    out: dict[tuple[float, float], dict] = {}
    for u in lap_pos:  # shortest off-lap paths from each lap node to the others
        best: dict[int, tuple[float, list[str]]] = {u: (0.0, [])}
        todo = [u]
        while todo:
            todo.sort(key=lambda q: best[q][0])
            q = todo.pop(0)
            if q != u and q in lap_pos:
                continue  # back on the lap: a chain through it is two shortcuts, each checked
            for v, length, label in edges.get(q, []):
                dv = best[q][0] + length
                if v not in best or dv < best[v][0] - 1e-9:
                    best[v] = (dv, best[q][1] + [label])
                    todo.append(v)
        for v, (dist, via) in best.items():
            if v == u or v not in lap_pos:
                continue
            for pu in lap_pos[u]:
                for pv in lap_pos[v]:
                    skip = (pv - pu) % lap
                    if skip * k_lap > SHORTCUT_MIN_SKIP_M and dist <= SHORTCUT_SLACK * skip:
                        key = (round(pu * k_lap, 1), round(skip * k_lap, 1))
                        if key not in out or dist < out[key]["pathM"]:
                            out[key] = {"fromLap": round(pu * k_lap, 2), "toLap": round(pv * k_lap % (lap * k_lap), 2),
                                        "skipM": round(skip * k_lap, 2), "pathM": round(dist, 1), "via": " + ".join(via)}
    return sorted(out.values(), key=lambda x: (x["fromLap"], x["skipM"]))


def composite_route(model: dict, d: dict) -> Route:
    """The closed route an imported track's alternative layout drives (TrackModel ``layouts[].sections``: main up
    to the branch, the branch, main after it), as a builder Route with s from its start/finish line."""
    by_id = {r["id"]: r for r in model["routes"]}
    smp, left, right = [], [], []
    for sec in d["sections"]:
        src = by_id[sec["route"]]
        lo, hi = float(sec["fromS"]) - 1e-3, float(sec["toS"]) + 1e-3
        for k, q in enumerate(src["samples"]):
            if not lo <= float(q["s"]) <= hi:
                continue
            if smp and math.hypot(q["x"] - smp[-1]["x"], q["y"] - smp[-1]["y"]) < 0.05:
                continue  # the shared junction vertex
            smp.append(dict(q))
            left.append(src["leftEdge"][k])
            right.append(src["rightEdge"][k])
    if math.hypot(smp[-1]["x"] - smp[0]["x"], smp[-1]["y"] - smp[0]["y"]) < 0.05:
        smp, left, right = smp[:-1], left[:-1], right[:-1]
    s = 0.0
    for k, q in enumerate(smp):
        if k:
            s += math.hypot(q["x"] - smp[k - 1]["x"], q["y"] - smp[k - 1]["y"])
        q["s"] = s
    length = s + math.hypot(smp[0]["x"] - smp[-1]["x"], smp[0]["y"] - smp[-1]["y"])
    main = by_id["main"]
    return Route(0, {"id": d["id"], "aiIndex": 0, "closed": True, "samples": smp, "leftEdge": left,
                     "rightEdge": right, "lengthM": length, "widthM": main["widthM"]})


def strip_end_lines(layouts: list[Layout]) -> list[np.ndarray]:
    """First and last sector-line vertices of every AI strip in every layout."""
    out = []
    for lay in layouts:
        for m in lay.meshes:
            if m.kind == "ai" and m.verts:
                v = np.array(m.verts)
                out += [v[:2], v[-2:]]
    return out


def e_is(a, b) -> bool:
    return a is not None and a[0] == b[0] and a[1] is b[1] and a[2] == b[2]


def _segments(r: Route) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A route's centreline as plan segments (start points, end points) with their half widths."""
    nxt = (np.arange(r.segs) + 1) % r.n
    return r.p[:r.segs, :2], r.p[nxt, :2], np.minimum(r.w[:r.segs], r.w[nxt]) / 2.0


def _polyline_dist(q: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Plan distance from points q (n x 2) to the nearest of the segments a->b (m x 2), and that segment's index."""
    if not len(a):
        return np.full(len(q), np.inf), np.zeros(len(q), dtype=int)
    ab = b - a
    ll = np.maximum((ab * ab).sum(1), 1e-12)
    t = np.clip(((q[:, None, :] - a[None]) * ab[None]).sum(2) / ll[None], 0.0, 1.0)
    foot = a[None] + t[..., None] * ab[None]
    d = np.hypot(q[:, None, 0] - foot[..., 0], q[:, None, 1] - foot[..., 1])
    j = np.argmin(d, axis=1)
    return d[np.arange(len(q)), j], j


def _in_quads(q: np.ndarray, quads: np.ndarray) -> np.ndarray:
    """Which points q (n x 2) lie inside any of the convex plan quads (k x 4 x 2), either winding."""
    if not len(quads) or not len(q):
        return np.zeros(len(q), dtype=bool)
    e = np.roll(quads, -1, axis=1) - quads  # k x 4 x 2
    rel = q[:, None, None, :] - quads[None]  # n x k x 4 x 2
    cr = e[None, ..., 0] * rel[..., 1] - e[None, ..., 1] * rel[..., 0]
    return ((cr >= 0).all(2) | (cr <= 0).all(2)).any(1)


def _cone_row(arm: Route, i: int) -> np.ndarray:
    """Plan points of a row of cones across a road at sample i, about CONE_SPACING_M apart from edge to edge."""
    L, R = arm.left[i, :2], arm.right[i, :2]
    n = max(2, int(float(np.hypot(*(L - R))) // C.CONE_SPACING_M) + 1)
    return np.array([L + (R - L) * t for t in np.linspace(0.04, 0.96, n)])
