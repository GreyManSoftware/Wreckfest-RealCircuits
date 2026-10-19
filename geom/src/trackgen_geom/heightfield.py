"""Ground heights of an imported (real-world) track: the spec's ``sketch.imported.terrain`` grid.

Shared by the geometry service's preview terrain (``terrain.py``) and the worker's ``TerrainField``
(``worker/builder/wfbuild/geometry.py``), so both use the same real ground. The grid holds heights at its
nodes, ``z[j * (nx + 1) + i]`` at ``(originX + i * cellM, originY + j * cellM)``, in the TrackModel frame
(imported tracks aren't moved or scaled, so the spec's metres are the model's).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np


class Heightfield:
    """Bilinear heights over a regular node grid; points outside take the nearest edge value."""

    def __init__(self, grid: Mapping[str, Any]) -> None:
        self.x0 = float(grid["originX"])
        self.y0 = float(grid["originY"])
        self.cell = float(grid["cellM"])
        self.nx = int(grid["nx"])
        self.ny = int(grid["ny"])
        z = np.asarray(grid["z"], dtype=np.float64)
        if z.size != (self.nx + 1) * (self.ny + 1):
            raise ValueError(f"heightfield has {z.size} heights, expected {(self.nx + 1) * (self.ny + 1)}")
        self.z = z.reshape(self.ny + 1, self.nx + 1)

    @property
    def extent(self) -> tuple[float, float, float, float]:
        return (self.x0, self.x0 + self.nx * self.cell, self.y0, self.y0 + self.ny * self.cell)

    def height(self, xy: np.ndarray) -> np.ndarray:
        """Heights at (N, 2+) points (x, y; further columns are ignored)."""
        xy = np.atleast_2d(np.asarray(xy, dtype=np.float64))
        u =np.clip((xy[:, 0] - self.x0) / self.cell, 0.0, self.nx)
        v = np.clip((xy[:, 1] - self.y0) / self.cell, 0.0, self.ny)
        i = np.minimum(np.floor(u).astype(int), self.nx - 1)
        j = np.minimum(np.floor(v).astype(int), self.ny - 1)
        fu, fv = u - i, v - j
        z = self.z
        return (z[j, i] * (1 - fu) * (1 - fv) + z[j, i + 1] * fu * (1 - fv)
                + z[j + 1, i] * (1 - fu) * fv + z[j + 1, i + 1] * fu * fv)

    def inside(self, xy: np.ndarray, margin: float = 0.0) -> np.ndarray:
        """True where a point lies at least ``margin`` inside the grid."""
        x0, x1, y0, y1 = self.extent
        xy = np.asarray(xy, dtype=np.float64)
        return ((xy[:, 0] >= x0 + margin) & (xy[:, 0] <= x1 - margin)
                & (xy[:, 1] >= y0 + margin) & (xy[:, 1] <= y1 - margin))

    def limit_grade(self, routes: object) -> float:
        """Real ground is never rescaled (the elevation presets' grade limit doesn't apply)."""
        return 0.0


def imported_terrain(spec: Mapping[str, Any] | None) -> Heightfield | None:
    """The imported track's heightfield, if the spec has one."""
    grid = (((spec or {}).get("sketch") or {}).get("imported") or {}).get("terrain")
    return Heightfield(grid) if grid else None
