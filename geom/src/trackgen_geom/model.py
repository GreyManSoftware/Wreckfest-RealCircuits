"""TrackModel: the authoritative, JSON-serialisable geometry handed to the Blender builder.

See ``geom/README.md`` for the format. Coordinates are metres, Z up (Blender convention):
x east (canvas right), y north (canvas up), z up. Headings are radians counter-clockwise from +x.
Serialised floats are rounded to 4 decimals so output is byte-stable.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any

FLOAT_DECIMALS = 4

Vec2 = list[float]
Vec3 = list[float]


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def to_plain(value: Any) -> Any:
    """Dataclasses to camelCase dicts, floats rounded (and -0.0 normalised)."""
    if is_dataclass(value) and not isinstance(value, type):
        return {_camel(f.name): to_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite value in track model")
        return round(value, FLOAT_DECIMALS) + 0.0
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    return value


@dataclass
class Sample:
    x: float
    y: float
    z: float
    s: float
    heading: float
    banking: float
    width: float


@dataclass
class Join:
    """Where an alternative route meets the main loop: the shared main-loop vertex."""

    route: str
    sample_index: int
    s: float


@dataclass
class Route:
    id: str
    ai_index: int  # 0 for the main loop, -1, -2, ... for alternative routes
    closed: bool
    length_m: float
    width_m: float
    samples: list[Sample]
    left_edge: list[Vec3]
    right_edge: list[Vec3]
    start_join: Join | None = None
    end_join: Join | None = None


@dataclass
class Branch:
    route: str
    s: float
    param: float
    heading: float
    z: float


@dataclass
class CrossingRecord:
    id: str
    annotated: bool
    detected: bool  # False: an annotation with no matching crossing (facts measured at its position)
    type: str  # bridge | flat | unannotated
    position: Vec2
    angle_deg: float
    branches: list[Branch]
    over_branch: int | None = None  # index into branches (bridges)
    deck_half_length_m: float = 0.0
    ramp_up_m: float = 0.0
    ramp_down_m: float = 0.0
    deck_height_m: float = 0.0


@dataclass
class JumpRecord:
    id: str
    route: str
    size: str
    bidirectional: bool
    s: float
    position: Vec3
    heading: float
    height_m: float
    start_s: float
    end_s: float
    layouts: list[str]
    in_base: bool  # True: built into the road surface (the samples' z includes it)
    profile: list[Vec2]  # [offset from s, height added] every JUMP_PROFILE_STEP_M


@dataclass
class ElevationMarkerRecord:
    id: str
    route: str
    s: float
    position: Vec3  # on the road surface
    height_m: float  # as given: absolute (custom elevation) or a bump on the preset terrain
    grade_pct: float  # sustained grade to the neighbouring markers/anchors
    feature: str | None = None  # grid | bridge when the marker sits on one


@dataclass
class Transform:
    name: str
    position: Vec3
    heading: float


@dataclass
class GridSlot:
    name: str
    index: int
    row: int
    side: str  # left | right of the centreline, looking in the driving direction
    s: float
    position: Vec3
    heading: float


@dataclass
class Grid:
    size: int
    row_spacing_m: float
    slot_width_m: float
    slots: list[GridSlot]
    car_change: Transform


@dataclass
class Checkpoint:
    route: str
    index: int
    s: float
    position: Vec3
    heading: float
    width_m: float
    is_finish: bool = False


@dataclass
class AiLine:
    route: str
    ai_index: int
    closed: bool
    points: list[Vec3]


@dataclass
class Bounds:
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float
    max_abs_road_m: float  # largest |x| or |y| of the road edges
    terrain_margin_m: float
    max_abs_coordinate_m: float  # road + terrain margin (the geometry fact)
    limit_m: float


@dataclass
class CanvasTransform:
    """world_x = scale * canvas_x + offset_x; world_y = -scale * canvas_y + offset_y."""

    scale: float
    offset_x: float
    offset_y: float


@dataclass
class LayoutRecord:
    """A layout of an imported track with alternative layouts (``sketch.imported.altLayouts``). The main layout
    is the main route; an alternative one drives its branch route and the main route from the branch end round
    to the branch start, listed as ``sections`` in driving order from the layout's timing line. Every layout
    has its real start line at ``start_line`` (``route``, ``s``, ``position``, ``leadM``: its lap position; its
    own ``altLayouts[].startFinish`` or the main one), the timing line (the finish checkpoint) at
    ``start_finish`` (``route``, ``s`` along that route, ``position``, ``heading``) behind it, and its ``grid``
    (slot ``s`` along the layout's lap from the timing line) between them; layouts sharing the main line share
    the main timing line and grid where their lap allows (pipeline._layout_grids)."""

    id: str
    name: str
    lap_length_m: float
    sections: list[dict[str, Any]]  # [{"route", "fromS", "toS"}] in driving order from the start/finish line
    start_finish: dict[str, Any] | None = None
    grid: Grid | None = None
    start_line: dict[str, Any] | None = None  # the real start line (route, s, position, leadM: its lap position)


@dataclass
class TrackModel:
    format_version: int
    generator: str
    slug: str
    name: str
    coordinate_system: dict[str, str]
    reverse: bool
    lap_length_m: float
    width_m: float
    elevation: str
    canvas_transform: CanvasTransform
    bounds: Bounds
    routes: list[Route]
    crossings: list[CrossingRecord]
    jumps: list[JumpRecord]
    grid: Grid
    checkpoints: list[Checkpoint]
    ai_lines: list[AiLine]
    first_sector_length_m: float
    elevation_markers: list[ElevationMarkerRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    layouts: list[LayoutRecord] = field(default_factory=list)  # imported tracks with alternative layouts only

    def to_dict(self) -> dict[str, Any]:
        d = to_plain(self)
        if not self.layouts:
            d.pop("layouts")  # drawn tracks: unchanged format
        for lay in d.get("layouts", []):
            for k in ("startFinish", "grid", "startLine"):
                if lay.get(k) is None:
                    lay.pop(k, None)  # layouts sharing the main line and grid
        return d

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, allow_nan=False)

    def route(self, route_id: str) -> Route:
        return next(r for r in self.routes if r.id == route_id)


COORDINATE_SYSTEM = {
    "units": "metres",
    "up": "+z",
    "x": "east (canvas right)",
    "y": "north (canvas up)",
    "heading": "radians counter-clockwise from +x about +z",
    "handedness": "right",
}
