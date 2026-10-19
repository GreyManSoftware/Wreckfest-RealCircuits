# Scenery manifest (`scenery.json`, format `trackgen-scenery/1`)

The builder plans all trackside dressing and scenery in pure Python (`worker/builder/wfbuild/dressing.py`, numpy
only, no `bpy`) and writes the plan as `scenery.json` beside the scenes. The Blender builder turns every placement
into a `#xref ob/<asset>.scne` subscene in the base scene. The pipeline copies it beside the zip
(`<out>/scenery.json`). The web editor's 3D preview (`src/web/src/view3d/`) draws the same plan: geom's
`POST /scenery` runs this planner on the unsaved spec (`wfbuild` is installed into geom from
`worker/builder/pyproject.toml`), so keep `dressing.py` free of `bpy` and of files outside the package.

```powershell
# Without Blender (needs trackgen_geom importable for the ground heights; falls back to flat ground)
cd worker\builder
python -m wfbuild.dressing --spec spec.json --model model.json --out scenery.json
```

From Python: `geo = TrackGeometry(model, spec); scenery, planner = dressing.plan(geo); scenery.to_json()`.
The same slug always gives the same file (seeded by the slug).

## Format

```jsonc
{
  "format": "trackgen-scenery/1",
  "slug": "cadwell",
  "environment": "forest",                        // options.environment (unknown -> gravel_pit)
  "coordinates": "TrackModel metres (x east, y north, z up); yaw radians CCW about +z, 0 = local +Y north",
  "transform": {"rotateDeg": 0, "shift": [0, -48.8, 0], "note": "scene = rotate(model, rotateDeg about +z) + shift"},
  "assets": {                                      // every asset used, with its stock path and footprint
    "foliage_x_spruce2": {"path": "data/art/objects/foliage_x_spruce2.scne", "r": 5.0, "h": 20.6, "face": "+y"}
  },
  "counts": {"trees": 1049, "spectators": 312},  // per category
  "total": 1701,
  "placements": [
    {"asset": "spectator_man_standing03", "category": "spectators", "position": [12.3, -40.1, 3.42], "yaw": 1.5708, "scale": 1.0}
  ],
  "zones": [{"kind": "crowd", "position": [10.0, -42.0], "r": 19.0}],   // trampled ground (dirt in the blend map)
  "noGo": [{"kind": "grid", "position": [0.0, 12.0], "r": 21.5}],      // every 4th no-go disc, for debugging/preview
  "backdrop": {"horizonDistanceM": [1109, 1266], "horizonTexture": "data/art/textures/landscape1_c5", "cards": 42},
  "info": {"viewingSpots": 8, "corners": 10, "straights": 2, "edgeMinM": 6.5}
}
```

- **position** is the asset origin on the terrain (TrackModel coordinates, before the placement `transform`; the
  base scene applies the transform). **yaw**: at 0 the asset's local +Y points north.
- **face** says which local axis faces the road: `+y` standing spectators, tyre stacks, trees; `-y` grandstands,
  tents, ad fences, staff, buildings; `x` long objects laid along the road (gantry legs across it, trucks,
  containers) with local Y along the driving direction. `r` is the footprint radius used for spacing, `h` the
  height (for proxies).
- **categories**: `gantry`, `flags`, `staff`, `grandstand`, `spectators`, `crowdFence`, `cameras`, `cornerMarkers`,
  `adFence`, `bigBoard`, `paddock`, `lights`, `trees`, `bushes`, `saplings`, `groundProps`, `buildings`, and
  (since 2026-10-19, additive) `barriers`: the visible containment, a row of stock barrier props (`face: "-y"`,
  `width` along local X, `depth`) standing on the wall line. Readers that don't know a category should draw it as
  a generic prop; the format name is unchanged. `bushes` may also hold stock foliage groups (`foliage_group10/12/23`).

## Rules the planner follows (and `tests/check_scene.py` re-checks)

- Only assets listed for the environment in `wfbuild/environments.json` (shared trackside sets + the environment's
  trees, bushes, foliage groups, ground props, buildings, corner markers, barrier rows and crowd fence).
- Walls (`options.walls` concrete/tyres): the builder writes invisible collision walls (`vis = false | col = true`)
  along a wall line 5.5 m from the road edge where containment is needed (inside of tight corners, grid sector,
  near another road) and 12 m out elsewhere, behind open run-off. `barriers` placements follow that line (between
  4.5 and 14 m from the road edge) with the environment's concrete or tyre style. No barriers when walls are none.
- Nothing else within `edgeMinM - 1` m of any road edge (`edgeMinM` = 14.5 m with walls, i.e. behind the set-back
  barrier, or run-off + 2 m without walls), except the start/finish gantry and the corner markers (dynamic tyre
  stacks and bales, at least 3 m out, in front of the near barrier). `info.nearRoadM` lists these exceptions as
  `{category: [min, max]}` metres from the road edge (`max` null = unbounded); readers checking clearance should
  use it rather than `edgeMinM` alone.
- No-go discs: bridge decks and ramps (+25 m), jump take-off to landing + 40 m, and the grid sector (line - 25 m to
  the back row + 15 m), where only the gantry, flags, staff and the start grandstand may stand. The barrier row is
  exempt (containment runs past jumps and the grid).
- Everything inside the terrain extent; footprints don't overlap (spatial hash).
- Standing spectators face the nearest road with local +Y.
