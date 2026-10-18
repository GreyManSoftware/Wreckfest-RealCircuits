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
        self.margin = C.AI_ROUTE_MARGIN["none" if geo.walls == "none" else "walls"]
        self.alts = sorted((r for r in geo.routes if not r.is_main), key=lambda r: -r.ai_index)
        self.joins = sorted({int(j["sampleIndex"]) for r in self.alts for j in (r.start_join, r.end_join) if j})
        self.warnings: list[str] = []

    # ------------------------------------------------------------------------------- sector lines
    def _halfwidths(self, r: Route, i: int, kind: str) -> tuple[float, float]:
        hw = r.w[i] / 2.0
        if kind == "route":
            hl = hr = hw + self.margin
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
        return c + n * hl, c - n * hr

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

    def main_sequence(self, i0: int, direction: int) -> list[int]:
        """Main-route sample indices of the AI sector lines, in driving order from sample ``i0``."""
        r = self.main
        n = r.n
        ov = round(C.AI_END_OVERLAP / 2.0)
        forced = {((j - i0) * direction) % n for j in self.joins}
        ks = set(range(0, n, C.AI_SECTOR_SAMPLES)) | forced | {0}
        out = []
        for k in sorted(ks):
            if k not in forced and k != 0 and any(abs(k - f) < 3 for f in forced):
                continue
            if n - k < 3 and k != 0:
                continue
            out.append(k)
        out.append(n + ov)
        return [(i0 + direction * k) % n for k in out]

    def alt_sequence(self, r: Route) -> list[int]:
        ks = {0, r.n - 1}
        lo, hi = 0, r.n - 1
        for cut in self.geo.cuts:
            if cut.route is r and cut.kind == "mouth":
                if cut.sample < r.n // 2:
                    lo = max(lo, cut.sample)
                else:
                    hi = min(hi, cut.sample)
        ks |= {lo, hi}
        ks |= set(range(lo, hi, C.AI_SECTOR_SAMPLES))
        ks = sorted(ks)
        out = [k for k in ks if k in (0, r.n - 1, lo, hi) or (k - lo >= 3 and hi - k >= 3)]
        out = [k for i, k in enumerate(out) if i == 0 or k - out[i - 1] >= 2 or k == r.n - 1]
        return out

    def ai_meshes(self, layout: Layout, i_line: int, direction: int) -> None:
        seq = self.main_sequence(i_line, direction)
        mesh_seq = seq if direction > 0 else seq[::-1]  # meshes always run forward; otherway flips them
        fixed = {0, len(mesh_seq) - 1} | {k for k, i in enumerate(mesh_seq) if i in self.joins}
        mesh_seq = self._valid_sequence(mesh_seq, lambda i, kind: self._line(self.main, i, kind), fixed)
        seq = mesh_seq if direction > 0 else mesh_seq[::-1]
        custom_route = "otherway = true" if direction < 0 else ""
        mats = {"route": "ai_route", "safe": "ai_safe", "race": "ai_race"}
        for kind in ("route", "safe", "race"):
            lines = [self._line(self.main, i, kind) for i in mesh_seq]
            layout.meshes.append(self._strip(f"#ai_{kind}_main", lines,
                                             custom_route if kind == "route" else "", mats[kind]))
        layout.info["mainSectors"] = len(mesh_seq) - 1
        layout.info["mainSectorSamples"] = mesh_seq
        line_s = float(self.main.s[i_line])
        for r in self.alts:
            sj, ej = int(r.start_join["sampleIndex"]), int(r.end_join["sampleIndex"])
            # The first and last sector lines are the main route's lines at the junction vertices.
            items = [("main", sj)] + [("alt", k) for k in self.alt_sequence(r)[1:-1]] + [("main", ej)]

            def line_of(item, kind, r=r):
                return self._line(self.main if item[0] == "main" else r, item[1], kind)

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
        # The AI line as an open curve for Toolbox's AI Route Generator (not exported).
        pts = [tuple(self.main.p[i]) for i in seq]
        layout.curves.append(Curve("ai_path", pts if direction > 0 else pts[::-1], False))
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
        a, b = c + n * hl, c - n * hr
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
    def startpoints(self, layout: Layout, direction: int) -> tuple[float, float]:
        g = self.model["grid"]
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

    # ------------------------------------------------------------------------------------- layouts
    def build(self) -> list[Layout]:
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
