"""Geometry-core constants.

Rule thresholds (grid row spacing and slot width, the bounds limit, the minimum inner-edge radius,
flat-crossing clearance, bridge ramp length and the attachment tolerance) are deliberately NOT
repeated here: read them from ``track-rules.json`` with :func:`rule_constants`. Everything below is
a modelling choice of the geometry core, not a pass/fail threshold.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .rules import load_rules

MODEL_FORMAT_VERSION = 1


def rule_constants() -> Mapping[str, Any]:
    """The ``constants`` block of ``track-rules.json`` (single source of truth for thresholds)."""
    return load_rules()["constants"]


# --- Stroke to centreline --------------------------------------------------------------------
SAMPLE_SPACING_M = 2.0  # centreline sample spacing (the road is modelled in ~2 m squares)
SMOOTH_SIGMA_M = 15.0  # Gaussian low-pass on the scaled raw stroke (removes hand-drawn jitter)
CONTROL_SPACING_M = 25.0  # spacing of the B-spline control points fitted to the smoothed stroke
SUBDIVISION_LEVELS = 4  # cubic B-spline subdivision rounds (16 points per control span)
RADIUS_WINDOW = 3  # samples either side used for the circumradius (about a 12 m chord)
MAX_CONTROL_POINTS = 2_000  # defensive cap (a 15 km route at 25 m is 600)

# --- Input size guards (checked before any smoothing) ----------------------------------------
MIN_MAIN_LOOP_CANVAS_FRACTION = 0.01  # main-loop length must be at least 1% of the larger canvas side
ALT_ROUTE_MAX_LAP_FACTOR = 3.0  # an alternative-route stroke may be at most 3x the lap length

# --- Minimum-radius relaxation ---------------------------------------------------------------
RELAX_TARGET_MARGIN = 0.03  # aim 3% above the required radius
RELAX_STEP = 0.5  # Laplacian step applied to control points near tight corners
RELAX_MASK_SPREAD = 2  # control points either side of a tight spot that are relaxed too
RELAX_MAX_ITERATIONS = 300
RELAX_MAX_DEVIATION_MIN_M = 8.0  # shape tolerance: max control-point movement is the larger of
RELAX_MAX_DEVIATION_FRACTION = 0.015  # this minimum and this fraction of the route length

# --- Bounds ----------------------------------------------------------------------------------
TERRAIN_MARGIN_M = 60.0  # terrain/run-off beyond the road edges, included in maxAbsCoordinateM

# --- Alternative routes and junctions --------------------------------------------------------
JUNCTION_MERGE_M = 0.05  # reuse an existing main-loop vertex this close to a junction
JUNCTION_EXCLUSION_M = (
    25.0  # alt/main intersections this close (along the alt route) to its ends are the junction itself
)
JUNCTION_CLEAR_M = 10.0  # bridge ramps and jumps keep this far from junctions
JUNCTION_BLEND_M = 20.0  # length over which an alt route's heights blend into the main loop's
ALT_TANGENT_GUIDE = 0.5  # guide control point distance, as a fraction of CONTROL_SPACING_M

# --- Crossings -------------------------------------------------------------------------------
INTERSECTION_CELL_M = 10.0  # spatial-hash cell for segment intersection tests
SELF_INTERSECTION_MIN_GAP = 5  # ignore intersections between segments this close in index
CROSSING_DEDUPE_M = 15.0  # intersections of the same pair of routes closer than this are one crossing
CROSSING_MATCH_RADIUS_M = 40.0  # max distance between an annotation and the crossing it labels
CROSSING_MIN_SIN = 0.3  # clamp for very shallow crossing angles when sizing decks
CROSSING_SHALLOW_DEG = 25.0  # warn below this crossing angle
CROSSING_CLEAR_MARGIN_M = 3.0  # clear road beyond the other branch's edge, either side

# --- Bridges ---------------------------------------------------------------------------------
BRIDGE_DECK_HEIGHT_M = 6.5  # over-branch road surface above the under branch
BRIDGE_MAX_GRADE = 0.18  # peak ramp grade (ramps are lengthened to stay within it)
RAMP_TRANSITION_FRACTION = 0.25  # parabolic vertical curves at each end of a ramp


# --- Elevation -------------------------------------------------------------------------------
@dataclass(frozen=True)
class ElevationLevel:
    amplitude_m: float
    wavelength_min_m: float
    wavelength_max_m: float
    max_grade: float


ELEVATION_LEVELS: Mapping[str, ElevationLevel] = {
    "flat": ElevationLevel(0.0, 1.0, 1.0, 0.0),
    "gentle": ElevationLevel(3.0, 250.0, 600.0, 0.04),
    "hilly": ElevationLevel(10.0, 180.0, 450.0, 0.09),
    "custom": ElevationLevel(0.0, 1.0, 1.0, 0.0),  # flat ground; the height markers shape the road
    "imported": ElevationLevel(0.0, 1.0, 1.0, 0.0),  # the road is the imported centreline's z (real tracks)
}
GRADE_BASELINE_M = 10.0  # fact maxGradePct: steepest height change over this distance along a route
IMPORTED_LAP_TOLERANCE = 0.02  # warn when options.lapLengthKm differs from an imported centreline by more
# an imported layout's next piece (an extra road) may leave the main centreline up to this far before the previous
# piece rejoins it: one junction where both meet the main road (Oulton Park's Fosters link onto the old straight)
IMPORTED_PIECE_OVERLAP_M = 30.0
NOISE_COMPONENTS = 6

# --- Height markers --------------------------------------------------------------------------
MARKER_BUMP_MIN_RADIUS_M = 80.0  # presets: a marker's bump eases back to the terrain over at least this
MARKER_BUMP_GRADE = 0.08  # ...and over at least |height| / this, so a lone bump is never too steep
MARKER_MERGE_M = 0.5  # control points closer than this along a route are merged (the first wins)
MARKER_MIN_SPACING_M = 1.0  # floor on the distance used for a marker's grade (coincident markers)
MARKER_OFF_ROUTE_M = 30.0  # warn when a marker is further than this from its route
FLAT_LEVEL_MIN_M = 40.0  # flat crossings: half-width of the correction that makes both branches meet
FLAT_LEVEL_FACTOR = 15.0  # ...and at least this times the height step
MAX_REPORTED_GRADE_PCT = 1000.0  # grade facts are capped here (coincident markers, ramps with no room)


# --- Jumps -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class JumpSize:
    height_m: float
    takeoff_m: float
    landing_m: float


JUMP_SIZES: Mapping[str, JumpSize] = {
    "small": JumpSize(1.0, 8.0, 20.0),
    "medium": JumpSize(1.8, 12.0, 30.0),
    "large": JumpSize(2.8, 16.0, 40.0),
}
JUMP_LIP_M = 2.0  # flat lip after a one-directional takeoff
JUMP_TABLE_M = 6.0  # flat table top between the two takeoffs of a bidirectional jump
JUMP_PROFILE_STEP_M = 1.0

# --- Grid, sectors and checkpoints -----------------------------------------------------------
GRID_LINE_OFFSET_M = 4.0  # back row distance ahead of the start line (the grid is ahead of the line)
# imported tracks: the back row this much further ahead of the timing line (the finish checkpoint). Stock race layouts
# have 13-200 m (median ~90 m) between their finish checkpoint and back row, with the compiled AI route starting in
# between; ours had 4 m, the back row on the route's first sector line (2026-10-26, first-lap gaps blank; a guess
# to verify in game, the static data shows no other difference from stock)
IMPORTED_TIMING_CLEAR_M = 20.0
GRID_CHECKPOINT_MARGIN_M = 10.0  # first checkpoint this far beyond the front (pole) row
GRID_SHARP_RADIUS_M = 50.0  # a corner tighter than this ends the first sector
GRID_MAX_TURN_DEG = 45.0  # ...as does turning further than this from the start heading
START_CLEAR_M = 10.0  # kept free of ramps behind the start line
CHECKPOINT_SPACING_M = 250.0
CHECKPOINT_MIN = 6
CHECKPOINT_MAX = 10
CHECKPOINT_MARGIN_M = 2.0  # checkpoint planes extend this far beyond each road edge

# --- Preview ---------------------------------------------------------------------------------
PREVIEW_SIZE_PX = 1000
PREVIEW_CAR_LENGTH_M = 4.2
PREVIEW_CAR_WIDTH_M = 2.0
