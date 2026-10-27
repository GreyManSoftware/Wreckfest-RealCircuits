# Stock visual assets for generated tracks

Research notes from 2026-10-18, used to plan the visual work on generated tracks (implemented 2026-10-19; see the end). Every path below is a stock game path, so reference it and never copy it.

## How we found this

- **Base scenes.** Stock base scenes are `data/art/levels/<env>/<track>_1/<track>_base.scne`, compressed with lz4: a `04 00 00 00` dword, the tag, a dword, then blocks of `u32 size` + lz4 block. A block that fails on its own decodes with the previous 64 KB as its dictionary.
- **Importing.** Once decompressed to `.scne.raw`, Toolbox Mz's SCNE importer reads them without Breckfest. We imported fields13, fields05, fields14, forest11, sandpit03 and urban06.
- **Textures.** `.bmap` files are compressed the same way. Inside, they hold a DDS (or a raw TGA for `_t` maps).
- **Technical Art Guide.** The guide in `tools/Wreckfest - Technical Art Guide.doc` documents the blend material.

## Materials

### `#blend` terrain and road material

A blend material mixes three albedos through an RGB blend map on **UV2**. Stock tracks use the explicit 12-slot layout, which is also what Toolbox's "#blend Advanced" node group writes:

| Slot | Texture |
|---|---|
| 0 | albedo R |
| 1 | normal/AO/roughness (`_ns`) for R |
| 2 | albedo G |
| 3 | `_ns` for G |
| 4 | albedo B |
| 5 | `_ns` for B |
| 6 | blend map `_c` (UV2) |
| 7 | `data/art/textures/detailblend_white_c` |
| 8 | macro `_ns` (UV2) |
| 9 | colour map `_c` (UV2, soft-light, 128 grey = neutral) |
| 10 | clutter colour `_t` (`default_clutter_colorgray_t` works) |
| 11 | clutter mask `_t` |

- **Name tags.** The material name carries surface, clutter group and shader tags, e.g. `fields13_near#blend#gravel#fields13`. The clutter group is `data/scene/surface/<name>.suse`.
- **UV channel and tiling per albedo.** Each albedo picks UV1 or UV2 and a tiling scale; sandpit03 uses UV2 × 300. Toolbox writes the scale from a Mapping node's Scale. It writes UV2 when the image's UVMap node names a map containing "2".
- **Verified (2026-10-18).** A 12-slot material from a node group named with `#`, plus #xtree, #horizon, a ghost material and a scaled `#xref`, went through Toolbox and bgeometry with no errors. The rebuilt `.scne` holds the same slots, UV channels and scales as the stock files. (The game then ignores the `#xref`'s scale: see "Real pits" below.)

### Clutter

- **Stock textures.** Stock clutter masks are 2048² `_t` maps. They are mostly black, with **G = grass** in a band roughly 5–80 m from the road, R and B sparse (bushes and flowers), and alpha 128 (forest11 uses 255). The band is thin: only 1–8 % of the map is covered (see "Scene budgets").
- **Clutter groups.**
  - `fields13`: `foliage_group20` grass, bushes and saplings.
  - `forest11`: `foliage_group_forest11`.
  - `sandpit3`: `foliage_group12`.
  - `fields05`: `fg_fields05`.

### Scene budgets (measured 2026-10-19 with `worker/builder/scne_read.py`)

All 43 stock `*_base.scne` against our builds. The visuals-v2 mods crashed at load with `Out of vertex buffer
memory!` (`bagmodelsetup.cpp(3155)`); only the clutter was outside the stock range.

| Measure | Stock (43 bases) | v2 builds that crashed | Pre-visuals Cadwell (loaded) | Limit (`scne_read.LIMITS`) |
|---|---|---|---|---|
| Largest draw batch (bgeometry splits per material) | 330-50,506 verts | 2,260-3,639 | 4,285 | 32,768 |
| Scene's own vertices | 25k-1.07M | 49k-142k | 44k | 800k |
| Vertices with every subscene instance (props, recursively) | 0.03-9.8M (urban03 21M) | 2.4-6.2M | 0.28M | 10M |
| Subscenes | 143-8,440 | 2,384-3,833 | 38 | 9,000 |
| Clutter at full density: mask (R+G+B) × area | 3.6-7.2 ha | 11-32 ha | none | 6 ha (builder caps 5) |
| Clutter: highest mask mean within 400 m | 0.13-0.24 | 0.44-0.60 | none | 0.22 (builder caps 0.18) |

Stock clutter masks cover 1-8 % of their frame (fields13 3.4 %); ours were a continuous band at 29-39 %. The
builder now thins the mask evenly to the budget (`config.CLUTTER_MAX_HA`, `CLUTTER_MAX_LOCAL`, `maps.py`).
Clutter is the best-supported cause, not yet confirmed in game.

### UV range: likely cause of "missing textures"

Stock UVs are stored as int16 / 2048, so they must stay within ±16. The stock terrain's UV1 spans exactly −15.9..13.8. Our builder writes UV1 in metres / tile, which reaches about 375 along a 3 km road and ±60 on the terrain. That probably overflows. **Fix:** wrap UVs per face by integers, and put the terrain albedos on a 0..1 UV2 with a tiling scale. This is still to do.

### Background and horizon

These meshes have no collision. Stock bases have no `CustomData` on them.

| What | Stock example | Texture slots |
|---|---|---|
| Horizon ring `#horizon` | fields13 `tauski`: cylinder r ≈ 1300–1450 m, z −60..+67, 329 verts, u wraps around, v = 1 top / 0 bottom (Blender) | 1 and 6 = `*_c5`, 7 = `*_fog` |
| Background tree cards `#xtree` | `foliage_bgrforest00N`: strips ≈ 190 m long, 30–42 m tall, 8 m quads using different u ranges of the texture | 1 = `foliage_bgrforest{3,4,5,9,10,11}_c5` |
| Far ground (`#noshadow` / `#lofi`) | fields13 `cylinder001#lofi` disc out to r ≈ 2000 m; urban06 `terrain_newN#lofi` hills up to 150 m | 1 = filler texture, 9 = `default_zero_spec_s` |
| City skyline | fields14 `landscape_urban#noshadow` | `bgr_city_01_c5` |

- **Horizon textures:** `fields_vistaplane_c5` + `_fog` (fields), `landscape1_c5` + `_fog` (forest, sandpit), `highdesert_vista_c5`, `landscape03_c5`, `landscape04_urban_c5` (urban).
- **Fillers:**
  - `levels/fields/textures/{gravel01_filler_c, fields_filler01_c, fields11_filler1_c}`;
  - `levels/forest/textures/filler_forest04_1_c`;
  - `levels/urban/textures/urban05_filler1_c`.
- **Bounds.** Stock rings reach r ≈ 2000 m. Ours must stay within ±2000 m after the placement shift.

### `#static_cube_map`

Every stock base and the example use a **0.3 m cube mesh** 2–4 m above the ground near the track. Its CustomData is `dyn = "data/property/object/ghost"` and its material is `ghost`, with `data/art/textures/tmp_ghost_c1` in slots 1 and 6. The builder now does the same (commit 383b358). The old untextured 1 m cube rendered pink.

## Texture palettes per environment

Each albedo has an `_ns` with the same stem unless noted. All of these exist.

| Env | Terrain R / G / B | Asphalt | Gravel | Sand | Macro | Clutter | Horizon / background trees |
|---|---|---|---|---|---|---|---|
| farmland | fields `ground_sand_pebbles_fields_02` / `ground_grass08_fields13` / `ground_farmfield_01` | fields `ground_fields13_tarmac01`, `ground_tarmac05_fields`, `ground_tarmac08f_fields` | fields `road_fields07_01`, `ground_gravel_01_fields`, `ground_sand_pebbles_fields_01` | fields `road_sand_06_fields`, `road_fields_sand_01`, `road_sand_04_fields` | `macro_fields13_ns` | fields13 | `fields_vistaplane` / `bgrforest4` |
| gravel_pit | fields `ground_sand_pebbles_fields_02` / `ground_grass_dry_01` / `ground_fields08_gravelsoil` | fields `ground_fields08_tarmac01`, `…01b` | as farmland | as farmland | `macro_fields08_ns` | fields13 | `fields_vistaplane` / `bgrforest4` |
| forest | forest `ground_kaanaa_sand02` / `ground_grass_forest11` (ns `ground_forestfloor2_ns`) / `ground_forestfloor4` | forest `ground_tarmac10d`, `ground_tarmac10c`, `ground_tarmac04d` | forest `road_forest_sand_04e`, `04f`, `05b` | forest `road_forest_sand_01`, `road_sand_05` | `macro_forest11_ns` | forest11 | `landscape1` / `bgrforest3`, `bgrforest9` |
| sandpit | sandpit `ground_sand_pebbles_01` / `ground_forestfloor6` / `ground_sandbank_large_04` | sandpit `road_sandpit_tarmac01..05` | sandpit `ground_sandpit_gravel_01`, `ground_pebbles_03` | sandpit `road_sandpit3_sand_01` (ns `road_sandpit_sand_04_ns`), `road_sandpit2_sand_01` | `textures/ground_macro_soil_spec_01_ns` | sandpit3 | `landscape1` / `bgrforest9` |
| industrial | urban `ground_sand_pebbles_01` / `ground_drygrass1` / `ground_concrete_tilesdirty` (ns `ground_concrete_tiles_ns`) | urban `ground_tarmac10`, `10b`, `10c` | urban `ground_forest_sand_pebbles_01` | – | `macro_urban06_ns` | none | `landscape04_urban` / `bgr_city_01`, `bgr_building*` subscenes |

Road shoulders in stock tracks are blend materials too, e.g. `road_shoulder_forest11`, `road_shoulder_sandpit3_01/02` and `road_shoulder_fields13_c5`, which is a `#decal`.

## Props

All props are `data/art/objects/<name>.scne`. Sizes are in metres, from Toolbox imports of the props.

### Facing

The facing was measured against the stock fields13 AI route:

- **Standing spectators** (`spectator_man_standing01..08`, `spectator_woman_standing01..08`, about 1 × 2.4 m) face the road with local **+Y**. Stock tracks place them 17–50 m from the racing line, with a median of about 30 m, in groups.
- **Animated and sitting spectators, and staff** (`spectator_anim_*`, `staff_01..03`) face local **−Y**.
- **Grandstands** rise towards +Y, so their seats face **−Y**. Stock grandstands already contain animated spectators.
- **Ad fences** (`prop_adfence3`, `prop_adfence9`, and the `_small` versions) are flat panels 3.8 × 1 m along local X.

### Grandstands

| Prop | Size (m) |
|---|---|
| `prop_start_grandstand3g` | 48 × 15 × 10 |
| `prop_start_grandstand3j` | 96 × 19 × 28 |
| `prop_start_grandstand1` | 25 × 8 × 8 |
| `prop_derby_grandstand01` | 32 × 6 × 4 |

### Start and finish

| Prop | Size (m) | Notes |
|---|---|---|
| `prop_start_startlights1` | 32.7 × 8.7 × 9.5 | Gantry; legs at ±16 m along X |
| `prop_start_archway01` | 32.7 | |
| `prop_finishgate_1` | 23.3 | |
| `prop_start_gate01` / `02` | 18 | |
| `prop_gate_inflatable01..05` | | |

### Pit and paddock

| Prop | Size (m) |
|---|---|
| `prop_start_tent8` | 10 × 10 |
| `prop_start_tent4` | 21 × 22 |
| `prop_hightent_03` / `05` | 8.5 |
| `prop_tent_star_01` | 18 |
| `prop_long_tent_equipment_01` | |
| `prop_foodstand1` | |
| `prop_portable_toilet` | |
| `vehicle_t2_crewbus01` | 12 m |
| `vehicle_t2_cargotruck01` | |
| `prop_car_truck` | |
| `prop_container2` / `4` | 6 m |
| `prop_trailer2` | |

### Real pits: pit garages, buildings, bridges, start lights (2026-10-24)

Measured with Toolbox imports in the worker container (`.local/probe`: bounding boxes, the lowest geometry over the
span, workbench renders from both sides). Used by `wfbuild/pits.py` for the real circuits' pits:

| Prop | Size (m, X × Y × Z) | Use | Front / notes |
|---|---|---|---|
| `prop_tarmac2_pitstop_part2` | 28.4 × 12.4 × 10.4 | pit garages, tiled along the footprint | roller doors on **+Y** |
| `prop_tarmac2_pitstop_part1` | 28.4 × 19.3 × 11.6 | (not used) | doors on −Y |
| `prop_tarmac2_pitstop_midpart*` | 30.6 × 7.8 × 10.3 | (not used: an open canopy) | |
| `prop_startbuilding_fields14` | 21.8 × 9.3 × 10.5 | race control, media centre | windows on **+Y** |
| `prop_start_building_forest11` / `forest09` | 21.5 × 14.8 × 6.9 / 20.9 × 8.8 × 6.1 | hospitality, other buildings | −Y |
| `prop_commentatorbuilding_forest09` | 6.9 × 6.3 × 5.6 | buildings under 12 m long | −Y |
| `prop_tarmac2_tenthall` | 15 × 35 × 6 | paddock buildings | −Y |
| `prop_concrete_element3` | 6.0 × 1.41 × 2.54 | the pit wall and the closures | along X |
| `prop_bridge03_green` | 55.1 × 4.0 × 9.1 | bridges and footbridges over the road | span along X: inner pillars ±11 m, more at ±15 m, stairs to ±27.5 m; deck underside 5.97 m |
| `prop_forest5_startlight01` | 11.1 × 0.9 × 8.2 | the start lights | a pole at x = 0 and an arm 7.2 m up reaching to x = 10.5 |
| `prop_start_startlights1` | 32.7 × 8.7 × 9.5 | (generated tracks' gantry) | legs ±16.35 m; the banner's lower edge is only **4.0 m** up (5.0 m in the middle) |
| `prop_start_archway01` | 32.7 × 8.7 × 9.1 | (not used) | 5.3 m clear |
| `prop_bridge02` / `02b` / `02c` / `02d`, `prop_bridge03b` / `03c` | 22-33 m / 55 m | (not used) | 02*: stairs to the ground at ±7.5-8 m (short span); 03b: pillars at ±8 m |
| `forest13_pitbuilding_01..05` | 3-10 × 7-19 × 4-10 | (not used) | small wooden huts |

**Wreckfest does not scale `#xref` subscenes** (corrected 2026-10-27; this section used to say it did). Toolbox
writes the empty's scale into the `.scne` and bgeometry accepts it, but the game draws every subscene at its own
size. Evidence from 2026-10-26: Navarra's footbridge, written at scale 1.8, stood unscaled in game; the span banners
(our own mesh, laid out for the scaled span) floated 6.06 × (scale − 1) m above its side walls; Brands' grandstands
written at 0.55 stood at full size, overlapping, their ad boards flickering against each other's. So:

- every stock prop is placed at scale 1 and planned (clearances, footprints, sight lines) at its true size: pit
  buildings, grandstands and spans are tiled or chosen at their own size (`pits.py`), the pit wall line is trimmed
  to a whole number of 3.74 m slots so its unscaled `prop_barrier8` elements abut 4 cm apart, the start lights'
  arm reaches 10.5 m at 7.2 m whatever the pole's distance, shore rocks and gorse are drawn at their stock size,
  and a measured tree gets the stock tree nearest its height (within 25 %) instead of a scaled one;
- the only exceptions are `dressing.XREF_SCALE_JITTER`, a little random variety where a slightly different size
  breaks nothing: trees (±25 %), bushes (±15 %), saplings (±30 %) and ground props (rocks, bales; ±15 %). The game
  ignores it too; their clearances use the true size (`Planner.try_place`);
- our own meshes (road, terrain, walls, kerbs, sponsor boards and span banners) are written with their real size
  in the vertices; the objects carry no scale.

`check_scene.py` fails any `#xref` placement off scale 1 beyond that list (`try_place` refuses one too).

### Trackside

- **Flags and pennants:** `prop_flagpole12` (10 m), `prop_pennant_anim_1*` and `5a`.
- **Lights and towers:** `prop_stadium_light_02` (24 m), `prop_lightpole7`, `prop_bigspeaker01`, `prop_cameratower`, `prop_camerastand01`, `prop_scoreboard`, `prop_start_jumbotron1b`.
- **Barriers:**
  - `prop_barrier7a_small_colmod` and `prop_barrier7c_small_colmod`, with about 290 each in fields13;
  - tyre stacks `prop_tirepile_{black,red,yellow,white,blue,green}05..10`, `prop_tirepile_big01..03`;
  - bales `prop_bale1`, `prop_straw_bale1`, `prop_balefence`;
  - `prop_concrete_element3`.
- **Big boards:** `prop_ad_eu_huge01a..v` (7 × 5.6 m).
- **Braking boards:** `gameplay_distanceboard50`, `100`, `150` and `200` (see "Braking boards" below).

### Braking boards (2026-10-24)

Stock `data/art/objects/gameplay_distanceboard{50,100,150,200}.scne`, measured with `scne_read.py` (model
transforms plus batch boxes):

- **Size.** One panel 2.0 m wide (local Y) × 1.0 m tall (origin at its foot) × 0.06 m thick (local X); 636
  vertices, material `distanceboard`, texture `data/art/textures/gameplay_distanceboard_c`.
- **Dynamics.** Breakable: the root is `data/property/object/distanceboard1.dntp`, with two halves
  (`distanceboard1_bigpiece`) of four wooden pieces each (`generic_wood_small`). Hitting one shatters it,
  so it is no wall.
- **Facing.** The numbered face is local **+X**. Every one of urban03's 19 boards (and forest04's 100 and 50
  boards) has local +X pointing against the travel direction, i.e. towards oncoming cars.
- **Stock use.** forest04 has 12, forest06 3, urban03 19 (with 200 m boards) and urban04 1, in sets 50 m apart.

The planner (`dressing.brake_boards`, category `brakeBoards`) uses the 150, 100 and 50 boards; nothing is
copied. Placement rules: `worker/docs/scenery-manifest.md`.

### Sponsor boards: our own meshes and textures (2026-10-24)

**Update 2026-10-25: several sponsors.** `"sponsor"` now has a `sponsors` list (id, text, subtext, url, tagline,
logo, kinds, share, billboards cap, guarantee / lapGuarantee, colourways, designs; see its `_comment`):
@flatshiftcars on the mini billboards (3.5 × 1.75 m) and fence panels in eight designs (styles slant, stripes,
badge, split; DejaVu Sans/Serif/Mono, slanted by a shear, heavier by a stroke) with the start guarantee; the 7 × 3.5 m
billboards for TurdChecker (up to 3), Straight6 Trackdays and OpenTrack (up to 2 each, also on fence panels) and
GreyManSoftware (1, when a spot is left), each lap sponsor in clear view somewhere on every lap. The billboards
follow Chris's v3 mockups as `livery` layouts in the configuration (gradient or carbon weave, speed stripes,
fading chequer, slanted heavy type with outline and shadow, the TurdChecker logo as a sticker with an address-bar
pill for the domain, the Straight6 logo keyed off its white background); OpenTrack's small banner logo is redrawn
(`banner` style) from its colours and text. Logos supplied by Chris (his own or companies he works with) are under
`wfbuild/assets/sponsors/<name>/`. Only the textures of placed designs are written (`board` sheets shared by
billboard and mini, `fence` sheets), as `<slug>_sponsor_<id>_<board|fence><k>_c.tga`.

Stock ad props (`prop_adfence*`, `prop_ad_eu_huge*`) carry stock artwork, so the "@flatshiftcars" boards are
our own (`wfbuild/sponsor.py`, category `sponsorBoards`):

- **Meshes.** Built into the base scene as one solid mesh `sponsor_boards` (`col = true`, material
  `sponsor_<kind><k>#metal`, albedo in slot 1 and the stock `default_zero_spec_s` in slot 9), not `#xref`
  subscenes. A fence panel is 3.84 × 1.0 m (as `prop_adfence3`), 0.05 m thick, 0.08 m off the ground on two feet;
  a billboard is a 7 × 3.5 m panel from 2 m up on two posts sunk 0.6 m. The printed face is local −Y, as the stock
  ad fences.
- **Textures.** Rendered by the builder with Pillow (DejaVu Sans Bold in the worker image): the handle in bold, a
  camera glyph in the Instagram manner drawn from primitives (no logo files), a slanted accent band; the billboard
  adds the tagline. One sheet per board kind and colourway (fence 1024 × 512, billboard 1024 × 1024: printed face
  in the top half, a plain back colour below), written as `data/track/<slug>/textures/<slug>_sponsor_<kind><k>_c.tga`
  and bimage'd with the generated maps.
- **Configuration.** `environments.json` `"sponsor"`: `enabled`, `text`, `tagline`, `fenceShare` (share of the
  6-panel ad-fence blocks that become ours), `billboards` (at most this many), `colourways` (`background`, `text`,
  `accent`, `back`). The JSON file named in `TRACKGEN_SPONSOR` is merged over it (e.g. another handle for a
  different pack, or `{"enabled": false}`).
- **Placement** (`dressing.sponsor_billboards`, `ad_fences`): billboards beside the start/finish straight (ahead of
  the grid and before the line), across the road from the grandstand, then on the outside of the biggest braking
  zones at the turn-in, then mid-straight; 16–40 m beyond the road edge (behind the run-off clearance, no-go zones
  and other roads respected), one per spot, the printed face turned between square to the road and back up it
  towards the oncoming view. Measured on the samples: 2–6 billboards and 0–48 fence panels per track.
- **Guarantee** (`dressing.sponsor_guarantee`): every layout gets at least one handle board in clear view of its
  grid or start/finish straight (line of sight over the terrain and the larger props). When none of the boards
  above is, it falls back to a relaxed distance band, then anywhere along the start view, then a row of three fence
  panels beside the start straight, then a mini billboard (`sponsor_minibillboard<k>`, 3.5 × 1.75 m from 1 m up,
  the billboard's texture) on the infield facing the grid. Props placed later across that view are dropped.
- **Credit** (`"credit"` in `"sponsor"`: `enabled`, `text` "GreyManSoftware", `prefix` "track by", `style`
  `plaque` | `line`, `colourway`): style `plaque` is one small plaque (`sponsor_credit`, 1.8 × 0.45 m on two short
  posts, 0.55 m up) in muted slate with light-grey regular type, behind the clearance 40–110 m before the line on
  the paddock side, square to the road; style `line` (also the fallback when no plaque fits) prints the credit in
  small muted type at the bottom right of the billboard face. It is independent of `enabled` for the handle
  boards; `"credit": {"enabled": false}` or `"credit": false` turns it off.

### Buildings

| Prop | Size (m) |
|---|---|
| `building_barn1` | |
| `prop_house01` | |
| `prop_building_storage_b` | |
| `prop_silo8` | |
| `bgr_building00..11` | urban backdrop, 12–40 m wide, up to 58 m tall |
| `prop_storagebuilding_02` | 85 m |
| `prop_tarmac2_building02` | |

### Foliage

- **Trees:** `foliage_x_spruce2` / `7` (20–26 m), `foliage_x_alder3` / `4`, `foliage_x_birch5*`, `foliage_x_pine2`, `foliage_x_sapling_01..03`.
- **Bushes:** `foliage_bush15` / `16`, `bush_deciduous1..7`.
- **Desert:** `foliage_desert_tree_01`, `foliage_x_desert_bush_01`.
- **Rocks:** `prop_rock_large3..5`.

The x-variants are what the dense stock forests use: forest11 places about 100 of each, and sandpit03 places about 670 saplings.

### Densities

Stock bases have roughly 900–6000 subscenes:

| Base | Subscenes | Spectators | Notes |
|---|---|---|---|
| fields13 | 1613 on about 100 ha | about 400 | |
| sandpit03 | 6019 | | Mostly trees |
| urban06 | 907 | | |

Collision comes from the prop scenes themselves. The `#xref` placers carry no CustomData.

## Implemented (2026-10-19)

The whole plan is in the builder (`worker/builder/wfbuild/`; details in `worker/README.md`, "Surfaces and materials" and "Geometry"). The environment → asset table is data: `wfbuild/environments.json`.

| Step | Where | Notes |
|---|---|---|
| UV wrap and UV2 | `geometry.py` (`wrap_uv`, `Mesh.set_map_uv`) | UV1 shifted per face by whole repeats (max \|uv\| now ~5); UV2 0..1 over the map frame on road, verge and terrain |
| Generated maps | `maps.py`, pipeline `convert_textures` | blend terrain/road, colour terrain/road, clutter `_t`; 1024², bimage'd into `data/track/<slug>/textures/` |
| Blend materials | `materials.py`, `bscene.spec_material` | the stock 12-slot layout through a `#` node group; the verge shares the terrain's textures and world UV1, so there is no tarmac/grass seam beyond the road edge |
| Road edge | `geometry.build_edge_decals` | stock broken-asphalt `#decal#priority1` strip over plain tarmac edges |
| Far ground, horizon | `backdrop.py` | `#noshadow` filler ring (1000 m tiles, hills up to ~15 m), `#horizon` 900 m beyond the terrain edge, clamped to ±2000 m after the placement shift |
| Background cards | `backdrop.py` | `#xtree` strips of 8 m quads, coverage per environment (forest has 2 layers; industrial gets `bgr_city_01` skyline cards instead) |
| Crowds and dressing | `dressing.py` | crowds behind fences at corners, jumps and bridges; gantry, flags, grandstand, staff, paddock, ad fences, corner markers, lights; no-go zones; `scenery.json` ([format](scenery-manifest.md)) |
| Foliage | `dressing.py` | clustered stock x-variant trees (the same noise as the forest-floor blend), bushes at clearing edges, saplings (sandpit), rocks, sand piles and logs, farmsteads, container yards |

Measured on the sample tracks: 1,000–4,000 subscenes per base (forest hills 3,817; WinkyIsland 1,593; Cadwell 1,701; sandpit loop 1,544), against 30–160 before. Builds take ~40–60 s instead of 30–50 s.

## Visuals v2 (2026-10-19)

Review of the first comparison renders against stock fields13: flat card rectangles near the play area, no
horizon, continuous bright walls, grey ground, a hard tarmac/gravel switch and open-looking sandpit/gravel pit
tracks. Changes (all data in `environments.json` where possible):

**Walls and barriers.** Stock bases contain containment as an invisible collision mesh (`wall_col*`,
`*#col`, CustomData `vis = false | col = true`, e.g. fields14, urban06) plus visible barrier props or meshes.
The builder now does the same:
- `geometry.wall_plan()` puts the wall line 5.5 m out only where containment is needed (the inside of corners
  tighter than 60 m, the grid sector, under bridges, and wherever a set-back line would come near another road),
  12 m out elsewhere behind open run-off, ramping between the two over 25 m. The collision wall (`walls_*`,
  material `wall_col_*#concrete_barrier|#global_barrier`, ghost texture) follows it.
- `dressing.barriers()` lines it with stock props (category `barriers`), whose road face sits on the collision
  wall's face. Static props only (the probe of each prop's `.scne`: `static` parts, `concretebarrier*.dntp`):

  | Env | concrete | tyres |
  |---|---|---|
  | farmland | `prop_barrier7a/7c_small_colmod` (fields13, ~290 each) | `prop_tirebarrier02/04` |
  | gravel_pit | `prop_barrier8_dirty_side1`, `dirty2_side1` (weathered, fields14) | same |
  | forest | `prop_long_barrier_muddy01/02` (5.9 m, muddy) | same |
  | sandpit | `prop_barrier8` (sandpit03 uses 122) | same |
  | industrial | `prop_concrete_element3` | same |

  Tyre piles (`prop_tirepile_*`) are dynamic (`tirepile.dntp`), so they stay decoration (corner markers in front
  of the barrier). Prop sizes (Toolbox import): barrier7a/c 3.07 × 1.41 × 3.07 m (block plus fence),
  barrier8 3.7 × 0.82 × 1.09, long_barrier_muddy 5.9 × 0.8 × 0.8, tirebarrier02/04 5.28 × 2.83 × 1.47 (a tyre
  heap with chainlink), concrete_element3 6.0 × 1.41 × 2.54.
- Scenery clearance with walls is now 14.5 m (behind the set-back barrier); corner markers stand 4.3 m out.

**Horizon and background cards.** The ring was there but the old render camera framed the track only; the render
tool now frames the terrain and looks to the horizon (as the stock render) and has a third `horizon` view.
- `#horizon` is 127 m tall per 1100 m of distance (`HORIZON_SCALE_M`), so the vista reads like fields13's.
- The far ground rolls: low hills by the terrain edge, growing towards the horizon where they hide the vista's
  lower edge (`farHills` per environment, metres near/far).
- Cards (`cardBands`): only just in front of the ring (t = 0.8–0.93 of the way), 16–28 m tall, feet 5 m deep in
  the hills; a full-coverage band is one closed ring (forest), partial bands are staggered strips whose ends sink
  into the hill over ≥ 60 m. None stand on the terrain any more.
- A ragged tree belt (14–40 m deep) along the terrain edge hides the edge of the generated ground (not industrial).

**Ground.** Stock blend/colour stats (`blend` = mean RGB of the blend map): forest11 blend (23, 131, 37), colour
(106, 106, 103) with dark patches to ~60; sandpit3 colour (131, 128, 108); fields13 colour (133, 143, 103). Ours had
forest floor (B) under 70 % of a forest track. Now B only under canopy cores (Cadwell's mean blend is (37, 142, 75): mostly grass),
colour maps get large macro patches, a dry/lush hue drift, greener grass, shade under tree clusters and browner
wear along the road; the clutter G band is continuous (mean 154–201 at 8–35 m from the road on the test tracks, < 15 beyond 100 m; alpha 255 on forest
as forest11).

**Mixed surfaces.** Asphalt's B layer is now the environment's gravel (stock forest11 asphalt uses sand pebbles):
dusty edges, and a blend ramp over ~13–29 m before every switch so the asphalt turns into the gravel sector's own
texture. Stock `decal_asphalt_sand_transition_fields13_c5` (+ao/n/s, 2:1) lies across the road at each switch
(`track_transitions`, `#decal#priority2`, 7 m over the asphalt, 1.5 m over the gravel).

**Density.** Stock sandpit03 has ~6000 subscenes (foliage_group12 720, group10 405, spruces ~770, saplings ~1350);
fields14 (gravel-pit-like) uses foliage_group23, birch5c/5d, alder4, boulders and rock piles. Per-environment
`foliage` settings now raise sandpit (spruces, more saplings, foliage groups 10/12) and gravel pit (birch5d,
alder4, group23, boulders, rock piles).

**Still open:**
- In-game check of all of the above: the bimage'd map formats, the decal and backdrop tags, the frame rate with
  thousands of subscenes, how the invisible collision walls feel behind the barrier props, and how
  `prop_tirebarrier02/04` look in game (no stock base we imported uses them; the render shows red/white tyre heaps).
- The render tool has no clutter grass, fog or game lighting, so ground colour is judged only roughly there.
- Stock subscene groups (`foliage_bgrforest2..5.scne`) instead of our own cards.
- Sky and weather already map to stock environments (`pipeline/weather.py`); `#static_cube_map` is unchanged.

## Out of bounds (measured 2026-10-24)

Chris: on our tracks, driving off the track far enough drops the car through the world; on stock tracks the game
puts you back on the track, even with resets off. Found by decoding the compiled collision shapes of every stock
scene (`scne_read.read_collision`: per model the static collision triangles and each one's surface id; ids named as
in Toolbox Mz's importer, which match the stock `data/scene/surface/<name>.suse` files a material's `#tag` picks).

**The mechanism is a surface, not a field.** 36 of the 43 stock bases (and 33 of 157 layout and other scenes)
carry invisible collision with the surface **`resetplayer`** (id 19): objects named `resetwall`, `reset_wall`,
`theresetwall`, `great_collision_wall`, `boundcollision`, `resetplane`, `resetti1` ... A car touching it is reset
to the track. Variants: `resetplayer_delay` (fields11 containers and tents, wrecker01 pits), `crm_water_reset`
(crm01's water), `killplayer` (unused in the levels). The rest have it in their layouts (fields13: `reset1`,
`reset2`; urban01: `grandstandcollision1`) or are enclosed arenas (fields04, fields10, urban07, crm02). There is
no `.tcat` or `.envi` field for it: the `.tcat`'s per-route special zones (`tszo`) only switch resets off
(game strings "no reset zones", "resetting the 'no reset' flags here would interfere with the special zone
flags"), and the race's car-reset setting is the player's reset button. Bugbear's Technical Art Guide says of the
blue `#ai_route_*` sector borders: "outside this, you are totally lost ... should cover the whole area where a car
can physically drive"; the reset meshes sit just outside them.

Shape (heights against the ground collision below; distances in plan from the blue route border of layout `_1`):

| Stock | Reset object | Wall: outside the blue route (p10 / median) | Wall top / foot | Lid above ground (median) | Ground collision beyond the route (max) |
|---|---|---|---|---|---|
| fields05 | `resetwall` | 0.7 / 24 m | +2.4 / −4.5 m | 2.0 m, reaching ~30 m out | 171 m |
| forest01 | `resetwall` | 4.5 / 44 m | +1.9 / −3.7 m | 2.2 m | 290 m |
| forest11 | `theresetwall` | 2.1 / 7 m | +0.4 / −8.8 m | 1.9 m | 423 m |
| fields08 | `great_collision_wall` | 4.8 / 29 m | +3.6 / −6.6 m | 1.9 m | 3,483 m |
| sandpit03 | `resetwall2` (+ `resetwall`) | 1.6 / 17 m | +0.2 / −8.3 m | 4.3 m | 1,515 m |
| urban03 | `reset_wall` | 8.5 / 24 m | +3.7 / −9.1 m | 3.9 m | 451 m |
| urban05 | `resetplayer_wall` | 5.5 / 14 m | +0.5 / −9.1 m | 1.1 m | 185 m |
| fields13 (layouts) | `reset1`, `reset2` | flat sheet only, from ~2 m out | – | 0.5 m | 500 m |

So stock: a low vertical reset wall a few metres outside the blue route, its foot several metres underground, and
a flat reset lid 1-4 m above the ground reaching 10-50 m further out; mostly round the outside only (fields05's
infield has none). The collidable ground runs on hundreds of metres beyond, so nothing falls before it is reset.
Far ground (`#lofi` / `#noshadow`) mostly has no collision (902 of 1,044 such models), but some outer terrain
does (fields06 `terrain_outer`, fields14 `midrange_terrain#lofi`, urban06/08 `outerterrain#lofi`). No stock level
has a kill plane below the world.

**Ours before (all eight circuits, shipped builds):** no reset surface at all; the terrain (the only ground
collision) ends at the road's bounding box + 60 m, i.e. 48-111 m beyond the blue route at the nearest point
(median 113-301 m per layout); beyond it the far ground had no collision, so a car fell forever.

**Ours now** (`worker/builder/wfbuild/bounds.py`, checks `tests/check_bounds.py`): per layout a `bounds_reset`
wall (top +2 m, foot −4 m) with a 16 m lid at +2 m round the outside, since 2026-10-26 at least twice as far from
the road as the first walls (4 m outside the blue route) and at least 48 m, within the terrain; in the base
invisible far-ground collision (~230 m past the terrain), `#resetplayer` sheets 0.3 m under imported lakes and the
sea, `#resetplayer` boxes round every spectator crowd, and a `#resetplayer` kill floor 25 m below the lowest
collision. See worker/README.md "Out of bounds".

## Real-circuit run-off and barriers (2026-10-24)

Found for the measured run-off of imported tracks (`docs/real-tracks.md`, "Run-off, barriers and foliage"):

- **Surfaces** (`data/property/settings/surfacelist.srfl`): `gravel`, `offroadsand` and the other off-road surfaces have
  rolling resistance 0.005; `slowdown` and `slowdownsand` 0.01 (asphalt 0.0025, mud 0.05). Stock sandpit01-03 sand
  banks use `#slowdown`, so gravel traps do; `#slowdownsand` is listed but bgeometry warns "Surface Settings does not
  exists" for it. No stock base uses a tarmac-circuit gravel trap.
- **Barrier textures as stock bases use them on their own meshes:** `prop_guardrail01_c` (512 × 64, a W-beam strip;
  guardrail meshes in fields03/04/08, forest01-12, sandpit01-03), `prop_tirerubberbarrier_c` (plain black conveyor
  belting; tyre-wall meshes in fields06, forest02/03/04/06; `_white_c` exists too), `prop_chainlinkfence04_small_c5`
  (chain-link with alpha). Red/white options: `prop_tirebarrier02/04` (tyre heaps with chain-link), the red-banded
  `prop_tirebarrier4/5_c`, `metalfence_paint_01_c5`. Black/grey props also exist (`prop_barrier8_black`,
  `prop_barrier12_black_side1/2`, `prop_plasticbarrier01_black`) but meshes keep a long barrier within the vertex
  budget.
- **Gravel and old tarmac:** `sandpit:ground_pebbles_03_c` (mixed grey/brown pebbles) for traps,
  `fields:ground_tarmac08f_fields_c` (cracked tarmac) and `sandpit:road_sandpit_tarmac03_c` for run-off aprons.
