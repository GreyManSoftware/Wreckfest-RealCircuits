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
KERB_MIN_CURVATURE = 1.0 / 150.0   # kerbs where the corner radius is under 150 m
KERB_DILATE = 4               # samples added either side of a kerbed corner
KERB_TAPER = 3                # samples to taper the kerb width to zero at its ends
SHOULDER_W = 3.0              # sloped verge between the road (or kerb) and the terrain
SHOULDER_DROP = 0.6
RUNOFF_W = 8.0                # walls = "none": a wider, flatter gravel run-off instead
RUNOFF_DROP = 0.4
WALL_OFFSET = 5.5             # wall line, measured outwards from the road edge
WALL_HEIGHT = {"concrete": 1.1, "tyres": 0.9}
WALL_SINK = 0.5               # walls start this far below the terrain
OTHER_ROUTE_S = 60.0          # same-route samples further than this (along s) count as another road
MOUTH_CLEAR = 0.5             # an alt-route cross-section must be this far outside the road it leaves
TERRAIN_CELL = 4.0
TERRAIN_TILE_CELLS = 32       # 128 m terrain tiles
TERRAIN_SINK = 0.15           # terrain under and beside the road sits this far below it
TERRAIN_CORE = 3.0            # within this distance of the road edge the terrain follows the road
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
AI_ROUTE_MARGIN = {"walls": 6.0, "none": 10.0}   # blue route beyond the road edge
AI_SAFE_INSET = 0.5           # red safe line inside the road edge
AI_RACE_INSET = 1.5           # green race line inside the road edge (Bugbear's generator: 1 m inside safe)
AI_END_OVERLAP = 12.0         # the open route strip runs this far past its start (example mod: ~15 m)
CHECKPOINT_MARGIN = 4.0       # checkpoint planes extend past the blue route on both sides
CHECKPOINT_BELOW = 5.0
CHECKPOINT_ABOVE = 10.0
STARTPOINT_LIFT = 0.3
TEX_TILE = {"road": 8.0, "kerb": 2.4, "ground": 16.0, "wall": 4.0, "far": 64.0}

# --- materials -------------------------------------------------------------------------------------
# Single-texture materials (one Principled BSDF, texture in slot 1). The road, shoulder, terrain, edge decal
# and backdrop materials are multi-slot stock-style materials per environment: see materials.py and
# environments.json. key: (material name "name#surface", stock texture path, tiling group)
_T = "data/art/textures/"
MATERIALS: dict[str, tuple[str, str, str]] = {
    "kerb": ("kerb#curb", _T + "prop_curb1_c.tga", "kerb"),
    "wall_concrete": ("wall_concrete#concrete", _T + "texture_concrete_wall_01_c.tga", "wall"),
    "wall_tyres": ("wall_tyres#default", _T + "prop_tirebarrier2_c.tga", "wall"),
    "bridge_concrete": ("bridge_concrete#concrete", _T + "prop_forest5_concretebridge01_c.tga", "wall"),
    "cube_map": ("ghost", _T + "tmp_ghost_c1.tga", "wall"),  # as every stock base; see GHOST_MATERIALS
}

# options.surface -> road material keys (materials.py). "mixed" alternates asphalt and gravel by checkpoint
# sector (the grid sector is asphalt); alternative routes are gravel.
SURFACE_ROADS = {
    "tarmac": ("road_tarmac",),
    "gravel": ("road_gravel",),
    "sand": ("road_sand",),
    "mixed": ("road_tarmac", "road_gravel"),
}

WALL_MATERIAL = {"concrete": "wall_concrete", "tyres": "wall_tyres"}

# Mesh kinds that use the #blend materials and so get UV2 (the generated maps' frame).
BLEND_KINDS = ("road", "shoulder", "terrain")
# Mesh kinds without collision (CustomData empty): the road-edge decal and the backdrop.
NO_COLLISION_KINDS = ("decal", "backdrop", "helper")

# --- generated maps (maps.py) ----------------------------------------------------------------------
MAP_RES = 1024                # blend / colour / clutter maps, square over the terrain extent
FIELD_PX_MIN = 2.0            # distance-field resolution (metres per pixel, at least)
FIELD_CAP = 160.0             # distances beyond this are clamped
GRASS_BAND = (5.0, 80.0)      # clutter grass from this far to this far outside the road edge (stock fields13)

# --- edge decal ------------------------------------------------------------------------------------
EDGE_DECAL_OUT = 0.3          # the decal's broken asphalt edge sits this far outside the road edge
EDGE_DECAL_LIFT = 0.03

# --- backdrop (backdrop.py) ------------------------------------------------------------------------
HORIZON_OFFSET = 900.0        # wanted distance of the #horizon ring beyond the terrain edge (stock ~1400 m radius)
HORIZON_SEGMENTS = 96
HORIZON_REPEATS = 4           # vista texture repeats around the ring
BACKDROP_LIMIT = LIMIT_M - 10.0
TREE_CARD_HEIGHT = (28.0, 40.0)
TREE_CARD_QUAD = 8.0          # card strips are built from 8 m quads (stock foliage_bgrforest00N)

STATIC_CUBE_MAP = ("#static_cube_map", 'dyn = "data/property/object/ghost"')  # as in the example base
CUBE_MAP_SIZE = 0.3           # stock bases and the example: a 0.3 m cube...
CUBE_MAP_HEIGHT = 3.5         # ...2-4 m above the ground near the track
# Materials whose texture also goes into the opacity slot (6), so the object is invisible in game. The example's
# "ghost" material has ghost_c1 in slots 1 and 6; stock bases use data/art/textures/tmp_ghost_c1 the same way.
GHOST_MATERIALS = ("cube_map",)
