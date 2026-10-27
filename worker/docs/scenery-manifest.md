# Scenery manifest (`scenery.json`, format `trackgen-scenery/1`)

The builder plans all trackside dressing and scenery in pure Python (`worker/builder/wfbuild/dressing.py`, numpy
only, no `bpy`) and writes the plan as `scenery.json` beside the scenes. The Blender builder turns every placement
into a `#xref ob/<asset>.scne` subscene in the base scene. The pipeline copies it beside the zip
(`<out>/scenery.json`). The web editor's 3D preview (in the separate web app) draws the same plan: geom's
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
  Also additive (coastal environment): `walls` (stone field walls, `face: "x"`, local Y along the wall) and
  `shore` (rocks and cliff faces along the shoreline of an imported track with a sea level; the only category
  allowed below the sea).
  Also additive (2026-10-24): `brakeBoards`, the stock breakable distance boards `gameplay_distanceboard150/100/50`
  (`face: "+x"`: the numbered face is local +X, turned towards the oncoming cars; `width` 0.06 m along local X,
  `depth` 2 m along local Y, i.e. across the road). `info.brakeBoards` records the plan: per braking zone the
  layout, `turnInS` (along that layout's route), `side` (the corner's outside, left/right of the route's s
  direction; the set itself stands on `boardSide`, see Rules), `radiusM`,
  `approachM`, `dropKmh`, `boards` (`m`, `placement` index, `s`, `shared` when another layout's board is reused)
  and, since 2026-10-25, `dropped` instead of the per-board `skipped` (complete sets only; see Rules).
  Also additive (2026-10-24): `sponsorBoards`, our own sponsor boards (`sponsor_fence<k>`, `sponsor_billboard<k>`
  for colourway k; `face: "-y"`, `width` along local X). They are not stock objects: their `assets` entry has
  `"path": null` and `"mesh": true`, and the builder builds them as the base mesh `sponsor_boards` rather than
  `#xref` subscenes (see `worker/docs/visual-assets.md`). `info.sponsorBillboards` counts the billboards.
  Also additive (2026-10-24): `sponsor_minibillboard<k>` (the half-size infield fallback board) and `sponsor_credit`
  (the credit plaque); `info.sponsorGuarantee` (per layout: `placement` index of the handle board in view of its
  start, `asset`, `how` = `placed` | `relaxed` | `along` | `fence` | `infield`, and the driver's `eye` that sees it),
  `info.sponsorCleared` (props dropped from those sight lines) and `info.sponsorCredit` (`style` `plaque` | `line`,
  `text`, `fallback` when the plaque found no room).
  Changed (2026-10-25, several sponsors): asset names are `sponsor_<id>_fence<k>`, `sponsor_<id>_billboard<k>` and
  `sponsor_<id>_minibillboard<k>` for design k of sponsor `<id>` (`sponsor_credit` unchanged); `info.sponsorGuarantee`
  is `{sponsor: {layout: {...}}}` for the sponsors with the start guarantee, `info.sponsorLap` the same for the lap
  guarantee (`how` = `placed` | `braking` | `lap`), and `info.sponsorMinis` counts the mini billboards.
  Also additive (2026-10-24, imported tracks with `sketch.imported.pits`, `wfbuild/pits.py`): `pitWall` (stock
  `prop_concrete_element3` on the real pit wall and across both ends of a closed pit lane), `pitBuildings` (stock pit
  garages and buildings tiled along the real footprints, front towards the road), `pitPaddock` (trucks, tents and crew
  buses on the real paddock), `bridges` (`prop_bridge03_green` over the road at a real bridge, footbridge or gantry,
  `face: "x"`); the start lights are the `gantry` (`prop_forest5_startlight01`, a cantilever on the pit wall, its arm
  along local +X over the track). Real grandstands are `realGrandstand` (at their real positions, which may be nearer the road than edgeMinM). `info.pits` reports the lanes (`wallSM`,
  `wallFromEdgeM`, `builtLengthM`), counts, and per gantry the `scale`, `clearanceM` over the road and any `problems`;
  `info.nearRoadM` lists the pit categories. Zones of kind `paved` (the pit lane) carry no clutter grass.

## Rules the planner follows (and `tests/check_scene.py` re-checks)

- Only assets listed for the environment in `wfbuild/environments.json` (shared trackside sets + the environment's
  trees, bushes, foliage groups, ground props, buildings, corner markers, barrier rows and crowd fence).
- Walls (`options.walls` concrete/tyres): the builder writes invisible collision walls (`vis = false | col = true`)
  along a wall line 5.5 m from the road edge where containment is needed (inside of tight corners, grid sector,
  near another road) and 12 m out elsewhere, behind open run-off. `barriers` placements follow that line (between
  4.5 and 14 m from the road edge) with the environment's concrete or tyre style. No barriers when walls are none.
- Nothing else within `edgeMinM - 1` m of any road edge (`edgeMinM` = 14.5 m with walls, i.e. behind the set-back
  barrier, or run-off + 2 m without walls), except the start/finish gantry, the corner markers (dynamic tyre
  stacks and bales, at least 3 m out, in front of the near barrier) and the braking boards (breakable, 2-6 m out). `info.nearRoadM` lists these exceptions as
  `{category: [min, max]}` metres from the road edge (`max` null = unbounded); readers checking clearance should
  use it rather than `edgeMinM` alone.
- An imported track with measured run-off (`sketch.imported.runoff`): no `barriers` or `cornerMarkers` placements
  (its barriers are meshes in the base scene), and nothing but the gantry, flags, staff and start grandstand inside
  a gravel trap, on a paved apron or between a road edge and its barrier; with measured vegetation (`trees`,
  woodland `cover`) the `trees` are the measured ones only and `info.measuredTrees` reports `{placed, candidates,
  cap}`.
- No-go discs: bridge decks and ramps (+25 m), jump take-off to landing + 40 m, and the grid sector (line - 25 m to
  the back row + 15 m), where only the gantry, flags, staff and the start grandstand may stand. The barrier row is
  exempt (containment runs past jumps and the grid).
- Everything inside the terrain extent; footprints don't overlap (spatial hash).
- Standing spectators face the nearest road with local +Y.
- Braking boards (`dressing.brake_boards`, `brake_zones`; revised 2026-10-25 after lone 150 m boards and a
  Redgate set without its 50): for every layout's lap (an imported track's `layouts`, each its composite route; a
  drawn track forward and, with `reverse`, backwards) a heavy-braking corner is a corner (`geometry.corner_runs`)
  with an apex radius up to 80 m, turning at least 30°, after at least 175 m of straight-ish road (no corner
  tighter than the 110 m kerb radius back to the previous corner's exit), into which a rough lap-speed estimate
  (cornering limit, acceleration and braking passes) drops by at least 80 km/h. An imported track's recipe can
  correct the rule by corner name (`brakeBoards: {add, remove}`, carried as `sketch.imported.brakeBoards`; an added
  corner is the named run with the longest approach; it applies to every layout driving the corner). Each zone gets
  the **complete** 150/100/50 m set or nothing: the boards stand before the turn-in (the corner's first sample in
  driving order), measured along the route in the layout's driving direction, on the outside of the corner (left
  before a right-hander) or, when the outside has no complete set, the inside; near end 2 m beyond the road edge,
  kerb or green strip, numbered face to the oncoming cars. A board that would stand within 5 m of the previous
  corner's exit, near another road (a divider), across a wall line, in a bridge / jump / pit-building no-go disc
  (the grid sector is allowed, like flags), in a measured gravel trap, on the pit lane, off the terrain or on
  another placement first moves up to 5 m further out and / or up to 10 m back or forward; if one of the three
  still has no spot on either side, the zone has no boards. Layouts sharing a road share a board (same distance,
  within 6 m, same facing). `info.brakeBoards.zones[]` adds `why` (`rule` / `add`), `turnDeg`, `corner`,
  `boardSide` (where the set stands), `blocked` (why the outside failed), `dropped` (`{outside, inside}`:
  `"<m>:<reason>"` when the zone has no set; reasons as above plus `gravelTrap`, `pits`) and a board's `shift`
  `[back, out]` metres; `info.brakeBoards.dropped` counts dropped zones by reason (it replaces `skipped`).
- Sponsor guarantee (`dressing.sponsor_guarantee`, `layout_starts`, `sponsor.visible`): every layout has at least
  one handle board (billboard, mini billboard or fence panel) in clear view of its start: from one of the driver's
  eye points (1.2 m above its grid slots, or the road from 150 m before its line to 220 m past its front row), in
  range (billboard 12-220 m, mini 10-150 m, fence 8-90 m), within 75° of the driving direction, the printed face
  turned towards the eye, and two of three sight lines to the face clear of the terrain and the larger props
  (not the low/thin categories: barriers, ad and crowd fences, markers, flags, people, cameras, gantry). Fallbacks
  in order: a billboard at the start-straight spots in a relaxed band (12-50 m), anywhere along the start view, a
  row of three fence panels just behind the clearance, a mini billboard on the infield facing the grid. Props
  placed later across the chosen sight lines are dropped (`info.sponsorCleared`), except the gantry, grandstand
  and sponsor boards. Since 2026-10-25 this applies per sponsor with `guarantee` (@flatshiftcars, on its minis and
  fence panels), the posted fallbacks using its mini billboard.
- Sponsor lap guarantee (`dressing.sponsor_lap_guarantee`, `layout_laps`): for each sponsor with `lapGuarantee`
  (TurdChecker, Straight6, OpenTrack), one of its billboards is in clear view (`sponsor.visible`) from the driver's
  eye somewhere on every layout's lap (every 10 m); otherwise one is added at that layout's braking zones, then
  every 50 m along the lap, 12-50 m out. Its sight line is kept clear like the start guarantee's.
- Credit (`sponsor.credit`, `"sponsor": {"credit": ...}`): one plaque (`sponsor_credit`) behind the clearance 40-110
  m before the start/finish line, paddock side first, square to the road; or, with style `line` or when no plaque
  fits, a line of small type on the billboards.
