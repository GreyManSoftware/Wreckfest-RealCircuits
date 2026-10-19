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
- **Verified (2026-10-18).** A 12-slot material from a node group named with `#`, plus #xtree, #horizon, a ghost material and a scaled `#xref`, went through Toolbox and bgeometry with no errors. The rebuilt `.scne` holds the same slots, UV channels and scales as the stock files.

### Clutter

- **Stock textures.** Stock clutter masks are 2048² `_t` maps. They are mostly black, with **G = grass** in a band roughly 5–80 m from the road, R and B sparse (bushes and flowers), and alpha 128 (forest11 uses 255).
- **Clutter groups.**
  - `fields13`: `foliage_group20` grass, bushes and saplings.
  - `forest11`: `foliage_group_forest11`.
  - `sandpit3`: `foliage_group12`.
  - `fields05`: `fg_fields05`.

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

### Trackside

- **Flags and pennants:** `prop_flagpole12` (10 m), `prop_pennant_anim_1*` and `5a`.
- **Lights and towers:** `prop_stadium_light_02` (24 m), `prop_lightpole7`, `prop_bigspeaker01`, `prop_cameratower`, `prop_camerastand01`, `prop_scoreboard`, `prop_start_jumbotron1b`.
- **Barriers:**
  - `prop_barrier7a_small_colmod` and `prop_barrier7c_small_colmod`, with about 290 each in fields13;
  - tyre stacks `prop_tirepile_{black,red,yellow,white,blue,green}05..10`, `prop_tirepile_big01..03`;
  - bales `prop_bale1`, `prop_straw_bale1`, `prop_balefence`;
  - `prop_concrete_element3`.
- **Big boards:** `prop_ad_eu_huge01a..v` (7 × 5.6 m).

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

**Still open:**
- In-game check of all of the above: the bimage'd map formats, the decal and backdrop tags, and the frame rate with thousands of subscenes.
- The hard surface change between tarmac and gravel sectors on `mixed` tracks. A transition decal like stock `sand_transition#decal` would soften it.
- Stock subscene groups (`foliage_bgrforest2..5.scne`) instead of our own cards.
- Sky and weather already map to stock environments (`pipeline/weather.py`); `#static_cube_map` is unchanged.
