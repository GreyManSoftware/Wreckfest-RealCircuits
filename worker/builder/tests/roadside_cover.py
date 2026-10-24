"""Continuity of the roadside: no bare terrain between a road edge and its verge (pure numpy).

Along every edge of every route, probes just outside the road edge and just inside the verge's width from it (a
kerb or green strip there counts as cover) must lie on a built surface: the road (any route's, junction fills
and aprons included), a kerb, a green strip, a shoulder or a bridge deck. Terrain doesn't count: where the verge
is missing the bare terrain shows as a dark hard-edged patch beside the road (Oulton Park, Chris 2026-10-24: a
wedge opposite the Island and Fosters junctions, where the kerb also stopped short). Probes are skipped where the
builder deliberately has no roadside: off the valid road, on bridges, and in jump zones.

Used by check_scene.check_roadside (Blender objects) and by hand on TrackGeometry meshes.
"""
from __future__ import annotations

import numpy as np

COVER_PREFIXES = ("track_surface", "track_kerbs", "track_limits", "track_shoulders", "bridge_")
EDGE_IN_M = 0.4          # first probe this far outside the road edge
STRIP_IN_M = 0.4         # second probe this far inside the verge width (SHOULDER_W, RUNOFF_W) from the edge
MAX_RUN_M = 2.5          # an uncovered run up to this long (two probes) is a sliver, not a hole


def triangles(meshes) -> np.ndarray:
    """(name, verts n x 3, faces) -> covering triangles (k x 3 x 3), fan-triangulated."""
    out = []
    for name, verts, faces in meshes:
        if not name.startswith(COVER_PREFIXES) or not len(faces):
            continue
        v = np.asarray(verts, dtype=np.float64)
        for f in faces:
            for k in range(1, len(f) - 1):
                out.append(v[[f[0], f[k], f[k + 1]]])
    return np.array(out).reshape(-1, 3, 3)


def uncovered_runs(geo, meshes, to_scene=None) -> list[str]:
    """Runs of probes longer than MAX_RUN_M along any route edge that aren't on a built road/roadside surface.
    ``meshes``: (name, verts, faces) in the same coordinates as ``to_scene`` returns (default: model
    coordinates, i.e. the TrackGeometry's own)."""
    tris = triangles(meshes)
    if not len(tris):
        return ["no road or roadside meshes"]
    from wfbuild.geometry import TriIndex
    idx = TriIndex(tris)
    from wfbuild import config as C
    sw = C.RUNOFF_W if geo.walls == "none" else C.SHOULDER_W
    out = []
    for r in geo.routes:
        built = geo._built_samples(r)
        skip = ~geo.svalid[r.index] | geo.structure[r.index] | geo.jump_zone[r.index] | ~built
        for side in (1, -1):
            probes = [geo._side_points(r, side, EDGE_IN_M), geo._side_points(r, side, sw - STRIP_IN_M)]
            bad = np.zeros(r.n, dtype=bool)
            for pts in probes:
                q = pts[:, :2] if to_scene is None else to_scene(pts[:, :2])
                for i in np.nonzero(~skip)[0]:
                    if not bad[i] and not idx.covered(q[i]):
                        bad[i] = True
            # runs along the route, measured in metres
            i = 0
            while i < r.n:
                if not bad[i]:
                    i += 1
                    continue
                j = i
                while j + 1 < r.n and bad[j + 1]:
                    j += 1
                length = float(r.s[j] - r.s[i])  # one probe alone: 0 m
                if length > MAX_RUN_M:
                    out.append(f"{r.id}:{'l' if side > 0 else 'r'} {r.s[i]:.0f}-{r.s[j]:.0f} m ({length:.0f} m)")
                i = j + 1
    return out
