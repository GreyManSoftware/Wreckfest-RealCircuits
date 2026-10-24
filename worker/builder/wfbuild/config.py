"""Builder constants: dimensions, single-texture materials and stock textures.

Per-environment textures and stock objects live in ``environments.json`` (see ``envs.py``). Every texture and
prop is a stock game asset referenced by its game path (data/...); nothing is copied. ``tests/check_scene.py``
checks that each one exists under the mounted game folder.
"""
from __future__ import annotations

# Valid surface tags (data/property/settings/surfacelist.srfl, as listed by the unofficial wiki's
# Modding:Object_Properties page). A material is "name#surface[#more tags]".
VALID_SURFACES = (
    "default", "gravel", "graveldark", "gravelpacked", "mud", "offroadrocks", "offroadfoliage",
    "offroadsand", "asphalt", "concrete", "metal", "water", "wood", "nofriction", "slowdown",
    "slowdownsand", "foliage", "wettarmac", "killplayer", "resetplayer", "curb", "asphaltdusty",
    "offroadasphalt", "blender_glass", "superslowdown", "gravelredclay", "global_barrier",
    "concrete_barrier", "metal_barrier", "speedup", "loop", "resetplayer_delay", "snow", "crm_solid",
    "crm_water_reset",
)

# --- dimensions (metres) ---------------------------------------------------------------------------
LIMIT_M = 2000.0              # car physics breaks beyond +-2 km (handover)
ROAD_CELL_M = 2.0             # lateral road subdivision (handover: 1.5-4 m squares, 2 m typical)
KERB_W = 1.2
KERB_RISE = 0.05
KERB_MIN_CURVATURE = 1.0 / 150.0   # dressing: corner apexes (crowds, markers) turn tighter than a 150 m radius
KERB_CORNER_RADIUS = 110.0    # kerbed corners turn tighter than this (curvature smoothed over CORNER_SMOOTH_M)...
CORNER_MIN_TURN_DEG = 15.0    # ...and through at least this much
CORNER_SMOOTH_M = 16.0
KERB_TAPER = 3                # samples to taper the kerb width to zero at its ends
KERB_PERIOD = 2.0             # one red + one white block (stock prop_curb1_c along its long axis), metres
GREEN_W = 2.5                 # green track-limit strip beyond exit kerbs...
GREEN_MIN_RADIUS = 40.0       # ...of corners at least this fast (apex radius)
GREEN_LIFT = 0.03             # its outer edge sits this far above the grass
SHOULDER_W = 3.0              # sloped verge between the road (or kerb) and the terrain
SHOULDER_DROP = 0.6
RUNOFF_W = 8.0                # walls = "none": a wider, flatter gravel run-off instead
RUNOFF_DROP = 0.4
WALL_OFFSET = 5.5             # grid sector, under bridges, ground dropping away: barrier this far from the road edge
WALL_SETBACK = 12.0           # scenery stays behind this (+2.5 m) from the road edge
# Barriers only where needed (geometry.wall_plan); elsewhere open grass run-off.
DIVIDER_GAP = 30.0            # another section of track within this of the road edge gets a divider mid-gap
DIVIDER_MIN = 1.5             # ...at least this far from either edge
DIVIDER_OVERRUN = 2           # ...and on for this many samples (~4 m) past the end of the gap
DIVIDER_JUNCTION_CLEAR = 40.0 # no divider within this (along the road) of a junction, flat crossing or bridge
DIVIDER_APRON_CLEAR = 4.0     # ...but only this far past the paved run of an apron link (_apron) across the gap
DIVIDER_MERGE_M = 60.0       # imported tracks: no divider where a branch within this of its junction merges into its road
DIVIDER_BANK_MIN_GAP = 9.0    # drawn tracks: a grass bank under the divider where the gap is at least this...
DIVIDER_BANK_HALF = 4.0       # ...up to this half width (2.5 m clear of each road edge)...
DIVIDER_BANK_H = 0.8          # ...and this high
CROWD_WALL = 12.0            # barrier in front of crowds, this far out...
CROWD_WALL_EXTEND = 10.0      # ...running this much beyond each end of the crowd
FAST_CORNER_RADIUS = 60.0     # corners with an apex radius from this...
FAST_CORNER_MAX_RADIUS = 250.0  # ...to this, turning through at least...
FAST_CORNER_MIN_TURN_DEG = 30.0  # ...this much, are fast: a barrier outside...
FAST_CORNER_WALL = 14.0       # ...this far out, behind the grass run-off
DROP_PROBE = 15.0             # the terrain this far out of the edge...
DROP_H = 3.0                  # ...more than this below the road: barrier (the ground drops away)
WALL_RAMP_SLOPE = 0.25        # where barriers at different distances meet, the line moves this much per metre
WALL_HEIGHT = {"concrete": 1.1, "tyres": 1.2, "armco": 1.0}
WALL_SINK = 0.5               # walls start this far below the terrain
WALL_HALF = {"concrete": 0.35, "tyres": 0.5, "armco": 0.25}   # half thickness of the collision wall (the barrier's face)
# The wall mesh is an invisible collision wall (as stock bases' "wall_col" / "#col" meshes); the visible barrier is
# a row of stock props on the same line (dressing.barriers, environments.json "barriers").
WALL_CUSTOM_DATA = "vis = false | col = true"
OTHER_ROUTE_S = 60.0          # same-route samples further than this (along s) count as another road
MOUTH_CLEAR = 0.5             # an alt-route cross-section must be this far outside the road it leaves
TERRAIN_CELL = 4.0
TERRAIN_TILE_CELLS = 32       # 128 m terrain tiles
TERRAIN_SINK = 0.15           # terrain under the road sits this far below it (no poke-through on a 4 m grid)...
CAMBER_RUNOUT = 1.5           # beside a cambered road's low edge the ground follows the camber this far
TERRAIN_FLUSH = 0.04         # ...and beside it this far: grass meets the road edge a few cm down (stock-like)
TERRAIN_PULL_FULL = 1.5       # another, lower road takes the terrain down to its surface within this of its edge...
TERRAIN_PULL_FADE = 2.5       # ...fading out over this (between two roads the ground slopes across the gap)
GROUND_DIP_MAX = 0.25         # check: ground beside a road edge at most this below it (beyond the natural ground)
TERRAIN_CORE = 3.0          # within this distance of the road edge the terrain follows the road
TERRAIN_BLEND = 30.0          # then blends to the natural ground over this distance
BERM_W = 20.0                 # the outer edge of the terrain rises into a berm
BERM_H = 5.0
BRIDGE_SLAB = 0.8             # deck slab thickness under the road surface
BRIDGE_STRUCT_EXTRA = 15.0    # elevated structure (not embankment) this far beyond the under road's edge
BRIDGE_BARRIER_H = 1.0
BRIDGE_PILLAR = 1.2
BRIDGE_PILLAR_STEP = 10.0
BRIDGE_MIN_CLEARANCE = 4.5    # checked by the tests
JUMP_LIFT = 0.02              # layout-only jump surfaces sit just above the shared road
AI_SECTOR_SAMPLES = 8         # ~16 m AI sectors (Toolbox's route generator uses 15 m)
AI_ROUTE_MARGIN = {"walls": 12.0, "none": 12.0}  # blue route over the grass run-off (less at walls, other roads)
AI_SAFE_INSET = 0.5           # red safe line inside the road edge
AI_RACE_INSET = 1.5           # green race line inside the road edge (Bugbear's generator: 1 m inside safe)
AI_END_OVERLAP = 12.0         # the open route strip runs this far past its start (example mod: ~15 m)
CHECKPOINT_MARGIN = 4.0       # checkpoint planes extend past the blue route on both sides
CHECKPOINT_BELOW = 5.0
CHECKPOINT_ABOVE = 10.0
STARTPOINT_LIFT = 0.3
TEX_TILE = {"road": 8.0, "kerb": 2.4, "ground": 16.0, "wall": 4.0, "far": 1000.0, "limits": 4.0}

# --- measured run-off of imported tracks (runoff.py) -----------------------------------------------
TRAP_KERB_CLEAR = 0.3         # a gravel trap starts at least this far beyond a kerb or green strip
TRAP_BARRIER_GAP = 0.5        # a barrier stands at least this far beyond the end of its trap or apron
TRAP_DIP_M = 0.15             # the ground inside a gravel trap sinks this much (loose gravel below the verge)...
TRAP_DIP_EASE_M = 2.0         # ...easing in over about this much from the trap's edge
MEASURED_WALL_SLOPE = 1.0     # measured barriers at different offsets join at 45 degrees
MEASURED_CROWD_MAX_M = 75.0   # crowds stand behind the measured barrier, but no further out than this
MEASURED_TREE_MIN_M = 12.0    # measured trees: none nearer a road edge than this (and never in the run-off band)
MEASURED_JOIN_M = 30.0        # ...across gaps up to this long between two barrier zones
BARRIER_SMOOTH_M = 24.0       # measured barrier offsets: steps between zones ease over about this...
BARRIER_MEDIAN_M = 60.0       # ...after a running median over this (short excursions dropped)
BARRIER_CHUNK = 100           # visible barrier meshes: at most this many line samples per object
ARMCO_BEAMS = ((0.30, 0.62), (0.64, 0.96))   # W-beams (bottom, top above the ground), double-height Armco
ARMCO_POST_M = 2.0            # post spacing
FENCE_HEIGHT = 3.0            # debris fence above the barrier
FENCE_V = (0.22, 1.0)         # the chain-link part of prop_chainlinkfence04_small_c5 (v)
FENCE_TILE_U = 3.0
# visible barrier per measured type: material key, texture repeat along (m), and for walls height / depth
BARRIER_MESH = {
    "armco": {"mat": "barrier_armco", "tileU": 2.8},
    "tyres": {"mat": "barrier_tyres", "tileU": 4.0, "height": 1.05, "depth": 1.0},
    "concrete": {"mat": "barrier_concrete", "tileU": 2.0, "height": 1.0, "depth": 0.5},
}

# --- materials -------------------------------------------------------------------------------------
# Single-texture materials (one Principled BSDF, texture in slot 1). The road, shoulder, terrain, edge decal
# and backdrop materials are multi-slot stock-style materials per environment: see materials.py and
# environments.json. key: (material name "name#surface", stock texture path, tiling group)
_T = "data/art/textures/"
MATERIALS: dict[str, tuple[str, str, str]] = {
    "kerb": ("kerb#curb", _T + "prop_curb1_c.tga", "kerb"),
    # invisible collision walls (vis = false): the stock ghost texture, the barrier surface for impacts
    "wall_concrete": ("wall_col_concrete#concrete_barrier", _T + "tmp_ghost_c1.tga", "wall"),
    "wall_tyres": ("wall_col_tyres#global_barrier", _T + "tmp_ghost_c1.tga", "wall"),
    "wall_armco": ("wall_col_armco#metal_barrier", _T + "tmp_ghost_c1.tga", "wall"),
    # visible measured barriers (no collision; the wall_col_* wall under them collides): stock textures as stock
    # bases use them on their own guardrail and tyre-wall meshes (fields08/forest05 guardrail, forest04 tyres)
    "barrier_armco": ("barrier_armco#metal_barrier", _T + "prop_guardrail01_c.tga", "wall"),
    "barrier_tyres": ("barrier_tyres#global_barrier", _T + "prop_tirerubberbarrier_c.tga", "wall"),
    "barrier_concrete": ("barrier_concrete#concrete_barrier", _T + "a_concrete2_c.tga", "wall"),
    "barrier_fence": ("barrier_fence#metal_barrier", _T + "prop_chainlinkfence04_small_c5.tga", "wall"),
    "bridge_concrete": ("bridge_concrete#concrete", _T + "prop_forest5_concretebridge01_c.tga", "wall"),
    "cube_map": ("ghost", _T + "tmp_ghost_c1.tga", "wall"),  # as every stock base; see GHOST_MATERIALS
    # out of bounds (bounds.py): invisible reset walls/lids/kill floor/water (stock "resetwall" meshes) and the
    # far ground's collision
    "bounds_reset": ("bounds_reset#resetplayer", _T + "tmp_ghost_c1.tga", "wall"),
    "bounds_far": ("bounds_far_ground#offroadfoliage", _T + "tmp_ghost_c1.tga", "wall"),
}

# options.surface -> road material keys (materials.py). "mixed" alternates asphalt and gravel by checkpoint
# sector (the grid sector is asphalt); alternative routes are gravel.
SURFACE_ROADS = {
    "tarmac": ("road_tarmac",),
    "gravel": ("road_gravel",),
    "sand": ("road_sand",),
    "mixed": ("road_tarmac", "road_gravel"),
}

WALL_MATERIAL = {"concrete": "wall_concrete", "tyres": "wall_tyres", "armco": "wall_armco"}

# Mesh kinds that use the #blend materials and so get UV2 (the generated maps' frame).
BLEND_KINDS = ("road", "shoulder", "terrain")
# Mesh kinds without collision (CustomData empty): the road-edge decal and the backdrop.
NO_COLLISION_KINDS = ("decal", "backdrop", "helper", "barrier")

# --- generated maps (maps.py) ----------------------------------------------------------------------
MAP_RES = 1024                # blend / colour / clutter maps, square over the terrain extent
FIELD_PX_MIN = 2.0            # distance-field resolution (metres per pixel, at least)
FIELD_CAP = 160.0             # distances beyond this are clamped
GRASS_BAND = (5.0, 80.0)      # clutter grass from this far to this far outside the road edge (stock fields13)
# Clutter budget. The game builds the clutter (grass, bushes, flowers scattered by the clutter mask) into vertex
# buffers when it sets up the scene; the visuals-v2 masks (mean 0.29-0.39, ~11-32 ha at full density) crashed
# the game with "Out of vertex buffer memory" (bagmodelsetup.cpp). Stock clutter masks (R+G+B over the map
# frame): 3.6-7.2 ha at full density, at most 0.13-0.24 mean within any 400 m window (worker/docs/visual-assets.md).
CLUTTER_MAX_HA = 5.0          # sum of the mask (R+G+B, 0..1) x pixel area
CLUTTER_MAX_LOCAL = 0.18      # highest mean of the mask within a CLUTTER_WINDOW_M square
CLUTTER_WINDOW_M = 400.0
CLUTTER_MIN_BAND_M = 45.0     # over budget, the grass band's outer edge comes in to this before thinning

# --- edge decal ------------------------------------------------------------------------------------
EDGE_DECAL_OUT = 0.3          # the decal's broken asphalt edge sits this far outside the road edge
EDGE_DECAL_LIFT = 0.03

# --- mixed-surface transitions (geometry.build_transition_decals, maps.py) ---------------------------
TRANSITION_RAMP = 22.0        # the asphalt blends into gravel over this distance before each switch
TRANSITION_DECAL = (7.0, 1.5) # stock asphalt/sand transition decal: metres on the asphalt / gravel side

# --- backdrop (backdrop.py) ------------------------------------------------------------------------
HORIZON_OFFSET = 900.0        # wanted distance of the #horizon ring beyond the terrain edge (stock ~1400 m radius)
HORIZON_SEGMENTS = 96
HORIZON_SCALE_M = 1100.0      # the ring is 127 m tall (z -60..+67, stock fields13) per this distance
HORIZON_REPEATS = 4           # vista texture repeats around the ring
BACKDROP_LIMIT = LIMIT_M - 10.0
TREE_CARD_HEIGHT = (28.0, 40.0)
TREE_CARD_QUAD = 8.0          # card strips are built from 8 m quads (stock foliage_bgrforest00N)
LAKE_CELL_M = 4.0             # imported lakes: water plane cell size
LAKE_REACH_M = 8.0            # ...over ground below the water level up to this far outside the lake outline
REAL_GROUND_FADE = 120.0      # imported tracks: the far ground follows the real heightfield, fading to hills over this
TREE_CARD_SINK = 5.0          # card feet are buried this deep in the far-ground hills (soft base)

STATIC_CUBE_MAP = ("#static_cube_map", 'dyn = "data/property/object/ghost"')  # as in the example base
CUBE_MAP_SIZE = 0.3           # stock bases and the example: a 0.3 m cube...
CUBE_MAP_HEIGHT = 3.5         # ...2-4 m above the ground near the track
# Materials whose texture also goes into the opacity slot (6), so the object is invisible in game. The example's
# "ghost" material has ghost_c1 in slots 1 and 6; stock bases use data/art/textures/tmp_ghost_c1 the same way.
GHOST_MATERIALS = ("cube_map", "wall_concrete", "wall_tyres", "wall_armco", "bounds_reset", "bounds_far")
# Materials whose _c5 texture's alpha cuts the surface out (Toolbox: Principled Alpha -> slot 6), e.g. a fence
ALPHA_MATERIALS = ("barrier_fence",)
