"""Builder constants: dimensions, materials, stock textures, environment presets and props.

Every texture and prop is a stock game asset referenced by its game path (data/...); nothing is copied.
``tests/check_scene.py`` checks that each one exists under the mounted game folder.
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
PROP_CLEARANCE = 16.0         # props stay this far outside every road edge (beyond walls and run-off)
TEX_TILE = {"road": 8.0, "kerb": 2.4, "ground": 16.0, "wall": 4.0}

# --- materials -------------------------------------------------------------------------------------
# key: (material name "name#surface", stock texture path, tiling group)
_T = "data/art/textures/"
_FIELDS = "data/art/levels/fields/textures/"
_FOREST = "data/art/levels/forest/textures/"
MATERIALS: dict[str, tuple[str, str, str]] = {
    "road_asphalt": ("road_asphalt#asphalt", _T + "ground_race_tarmack_01_c.tga", "road"),
    "road_gravel": ("road_gravel#gravel", _FOREST + "ground_gravel_01_c.tga", "road"),
    "road_sand": ("road_sand#gravel", _FIELDS + "road_sand_05_fields_c.tga", "road"),
    "kerb": ("kerb#curb", _T + "prop_curb1_c.tga", "kerb"),
    "shoulder": ("shoulder#gravel", _FIELDS + "ground_fields08_gravelsoil_c.tga", "ground"),
    "runoff": ("runoff#gravel", _FIELDS + "gravel01_filler_c.tga", "ground"),
    "wall_concrete": ("wall_concrete#concrete", _T + "texture_concrete_wall_01_c.tga", "wall"),
    "wall_tyres": ("wall_tyres#default", _T + "prop_tirebarrier2_c.tga", "wall"),
    "bridge_concrete": ("bridge_concrete#concrete", _T + "prop_forest5_concretebridge01_c.tga", "wall"),
    "terrain_gravel_pit": ("terrain_gravel_pit#offroadsand", _FIELDS + "ground_sand_pebbles_01_c.tga", "ground"),
    "terrain_farmland": ("terrain_farmland#offroadfoliage", _FIELDS + "ground_farmfield_01_c.tga", "ground"),
    "terrain_forest": ("terrain_forest#offroadfoliage", _FOREST + "ground_forestfloor4_c.tga", "ground"),
    "terrain_industrial": ("terrain_industrial#concrete", _FIELDS + "ground_concrete_slab_01_c.tga", "ground"),
    "terrain_sandpit": ("terrain_sandpit#offroadsand", _FIELDS + "ground_sand01_fields_c.tga", "ground"),
    "cube_map": ("static_cube_map#default", "", "wall"),
}

# options.surface -> road material keys. "mixed" alternates asphalt and gravel by checkpoint sector
# (the grid sector is asphalt); alternative routes are gravel.
SURFACE_ROADS = {
    "tarmac": ("road_asphalt",),
    "gravel": ("road_gravel",),
    "sand": ("road_sand",),
    "mixed": ("road_asphalt", "road_gravel"),
}

WALL_MATERIAL = {"concrete": "wall_concrete", "tyres": "wall_tyres"}

# Stock props (data/art/objects/<name>.scne), referenced as Toolbox "#xref ob/<name>.scne" subscenes.
PROPS: dict[str, tuple[str, ...]] = {
    "gravel_pit": ("prop_tirepile_black05", "prop_tirepile_black07", "prop_rock_pile_01", "prop_rock_pile_02",
                   "prop_sandpile_02", "prop_car_rusty01", "prop_bigtire_01", "prop_rock_large2"),
    "farmland": ("prop_bale1", "prop_straw_bale1", "prop_straw_bale2", "foliage_tree_deciduous1",
                 "foliage_tree_deciduous2", "foliage_bush1", "foliage_oak2", "prop_logpile2"),
    "forest": ("foliage_pine1", "foliage_pine2", "foliage_spruce1", "foliage_spruce2", "foliage_birch5",
               "foliage_bush14", "foliage_tree_alder1", "prop_logpile2"),
    "industrial": ("prop_container2", "prop_container4", "prop_container4red", "prop_tirepile_big01",
                   "prop_car_rusty_pile01", "prop_concrete_element3", "prop_pallet1a", "prop_barrel4"),
    "sandpit": ("prop_sandpile_02", "prop_sandpile_pg_01", "prop_rock_desert_01", "prop_rock_desert_02",
                "foliage_desert_bush_01", "foliage_desert_tree_01", "prop_sandbank01_pg"),
}
PROP_DENSITY_PER_HA = 1.5     # props per hectare of terrain, capped below
PROP_MAX = 160

STATIC_CUBE_MAP = ("#static_cube_map", 'dyn = "data/property/object/ghost"')  # as in the example base
