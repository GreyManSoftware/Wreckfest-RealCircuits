# worker

The Linux build worker turns a track spec into a Wreckfest mod. Locally it runs as the Compose `worker` service, which consumes the Storage Queue `builds` (later a Container Apps Job). This folder holds the **toolchain image and smoke tests** (todo `toolchain-gate`), the **Blender builder** (todo `blender-builder`, see [Blender builder](#blender-builder)) and the **pipeline and queue consumer** (todos `export-build` and `packaging`, see [Pipeline](#pipeline)).

The image contains:

- Blender **4.2 LTS**, which runs headless with `blender -b`;
- the **Toolbox Mz** add-on, pinned to one commit;
- **Wine64**, which runs the game's own `bgeometry.exe` (`.bgo3` → `.scne`) and `bimage.exe` (`.tga`/`.png` → `.bmap`);
- the geometry core **`trackgen_geom`** (from `../geom`), installed into Blender's bundled Python 3.11 with `jsonschema` (Blender already ships numpy 1.24.3);
- the pipeline's packages in the same Python: **Pillow**, **cairosvg** (with `libcairo2` and `fonts-dejavu-core`), **azure-storage-queue** and **azure-storage-blob**. The image has no system Python; `wf-python` runs Blender's.

Everything runs on the CPU. Blender never renders, and nothing uses `--gpus`.

**The Wreckfest install is mounted read-only at runtime at `/wreckfest`.** It is never baked into the image or committed, and nothing is ever written to it.

## Pinned versions

| Component | Version | Source |
|---|---|---|
| Base image | `debian:trixie-slim` | Docker Hub |
| Blender | **4.2.23 LTS**, linux-x64, SHA-256 `bea0eb31…c49fab40` (checked during the build) | download.blender.org |
| Toolbox Mz | **v1.1.8**, commit `e03a57aa739c860a7721224bf417c454b8c755df` | github.com/gmazy/wreckfest_toolbox |
| Wine | **10.0** (Debian `wine`/`wine64` `10.0~repack-6`), 64-bit only, without `wine32` | Debian trixie |

Version pins are build args in the `Dockerfile` (`BLENDER_VERSION`, `BLENDER_SHA256`, `TOOLBOX_COMMIT`).

### Toolbox Mz licence

- The add-on is **MIT** (© 2021 Guillemot Thomas; credits in `CREDITS.md`).
- `wf_mod_gen/bagwrite.py` and the SCNE importer (`addons/io_import_wreckfest`) are **CC0**.

Both licences allow redistribution, so the add-on is **installed into the image at build time**. The licence texts are copied to `/usr/share/doc/wreckfest_toolbox/`, and the pinned commit is recorded in `/opt/blender-scripts/addons/wreckfest_toolbox/PINNED_COMMIT`.

## Layout

| Path | In image | What |
|---|---|---|
| `Dockerfile` | | Image definition. The build context is the repo root. |
| `scripts/entrypoint.sh` | `/opt/worker/scripts` | Entrypoint. `worker` (default) runs the queue consumer. `build-mod …` runs the pipeline without the queue. `info` prints versions and checks the mount. `smoke [a b c d]` runs the smoke tests. `build …` runs the Blender builder; `builder-tests […]` runs its tests. Anything else is run as a command. |
| `scripts/wf-tool` | on `PATH` | `wf-tool bgeometry\|bimage <args>` runs a game tool under Wine. |
| `scripts/wf-project` | on `PATH` | `wf-project ROOT [MOD_ROOT…]` overlays the game's `data/` onto a writable project root. |
| `scripts/wf-python` | on `PATH` | Runs Blender's bundled Python 3.11 (the pipeline's interpreter). |
| `pipeline/` | `/opt/worker/pipeline` | Pipeline (`pipeline.py`, `package.py`, `images.py`, `weather.py`, `common.py`) and queue consumer (`consumer.py`). See [Pipeline](#pipeline). |
| `blender/wf_toolbox_headless.py` | `/opt/worker/blender` | Enables Toolbox Mz for headless use and wraps its BGO exporter. |
| `smoke/` | `/opt/worker/smoke` | Smoke tests: `run-smoke.sh` runs inside the container, `run-smoke.ps1` runs on the Windows host. |
| `builder/build_scene.py` | `/opt/worker/builder` | Blender builder entry point: spec (+ TrackModel) → base and layout `.blend` files + `manifest.json`. |
| `builder/wfbuild/` | `/opt/worker/builder` | Builder package: `geometry` (road, junctions, kerbs, walls, terrain, bridges, jumps), `layout` (AI routes, checkpoints, start points), `scatter` (props), `config` (dimensions, materials, presets), `tcat` (per-layout track data, which the minimap needs), `bscene` (the only `bpy` module). |
| `builder/bgo_read.py` | `/opt/worker/builder` | Stdlib reader for `.bgo3` files: used by the tests and to inspect the example mod. |
| `builder/scne_read.py` | `/opt/worker/builder` | Stdlib reader for built `.scne` files (pure-Python lz4): draw batches, vertices, subscenes, clutter. `--limits BASE.scne ROOT...` checks a base scene against the stock-observed limits (`check_export.py`, `tools/Diagnose-TrackCrash.ps1`). |
| `builder/tests/`, `builder/run-builder-tests.ps1` | `/opt/worker/builder/tests` | Builder tests: build, check, Toolbox export and bgeometry for every sample. |

The image runs as the non-root user `worker` (uid 1000). It has a pre-initialised 64-bit Wine prefix at `/home/worker/.wine`, and `WINEDEBUG=-all` is set.

## Build and run

The build uses roughly one or two cores; it is mostly downloads and single-threaded `xz` decompression. Docker data lives on `E:\DockerImages`.

```powershell
# Build
docker build -f worker/Dockerfile -t trackgen-worker:dev .
# or: docker compose --profile worker build worker

# Versions and mount check
docker compose --profile worker run --rm --no-deps worker info

# Smoke tests via compose (uses the scripts baked into the image)
docker compose --profile worker run --rm --no-deps worker smoke

# Smoke tests from the host (mounts worker\scripts, blender and smoke from the repo, so no rebuild is needed)
.\worker\smoke\run-smoke.ps1                # all tests, --cpus 4
.\worker\smoke\run-smoke.ps1 -Tests b,c -Build
```

- **Game path:** the game root comes from `WRECKFEST_DIR` (default `D:/SteamLibrary/steamapps/common/Wreckfest`). You can set it in `.env`.
- **CPU and memory:** compose limits the worker to 4 CPUs and 6 GB. `run-smoke.ps1` uses `--cpus 4 --memory 4g` by default; change them with `-Cpus` and `-Memory`.
- **Dependencies:** `--no-deps` stops compose from also starting Azurite.

## Blender builder

`builder/build_scene.py` turns a track spec and its TrackModel (`trackgen_geom`, see `../geom/README.md`) into Wreckfest-ready Blender scenes for the Toolbox Mz BGO exporter. It is pure numpy plus `bpy`, deterministic (same spec → byte-identical `.bgo3` exports), and takes 1–3 s per track on the CPU.

```powershell
# In the container (the entrypoint's "build" runs blender -b ... build_scene.py -- ARGS)
docker compose --profile worker run --rm --no-deps -v E:\tracks:/in:ro -v E:\Source\wreckfest\.local\builder-out:/out worker `
    build --spec /in/spec.json --out /out/my_track
# Directly
blender -b --factory-startup --python-exit-code 1 --python /opt/worker/builder/build_scene.py -- `
    --spec spec.json [--model model.json] --out DIR
```

Without `--model` the spec goes through `build_track_model` and `DIR/model.json` is written. The spec is always needed: the TrackModel has no options (surface, kerbs, walls, environment). Exit codes: 0 built, 2 no model (rejected sketch), 3 out of bounds.

| Output | Contents |
|---|---|
| `<slug>_base.blend` | Road, kerbs, shoulders, road-edge decal, walls, bridges, terrain tiles, backdrop (far ground, `#horizon`, tree cards), `#static_cube_map`, prop subscenes |
| `<slug>_race.blend` | Forward layout: AI routes, checkpoints, start points, the base subscene, layout-only jumps |
| `<slug>_race_reverse.blend` | Reverse layout (only when the model has `reverse: true`) |
| `manifest.json` | Files, layouts (line position, sectors, checkpoints with lap positions, relocated checkpoints), junction stitches, bridges, placement transform, per-scene objects/materials, warnings |
| `model.json` | The TrackModel (when built from the spec) |
| `scenery.json` | The scenery placement manifest (format: [docs/scenery-manifest.md](docs/scenery-manifest.md)) |
| `mod/data/track/<slug>/textures/*.tga` | Generated blend, colour and clutter maps (the pipeline converts them with bimage) |
| `mod/data/track/<slug>/<layout>.tcat` | Track data per layout; the game sets up the in-race minimap from it (see [Mod layout](#mod-layout-zip-extracts-as-slug)) |

The pipeline ([Pipeline](#pipeline)) exports each `.blend` with Toolbox (`wf_toolbox_headless.export_bgo`) to `data/track/<slug>/<scene>.bgo3` and runs bgeometry, **base first** (the layouts reference it). `builder/tests/run_builder_tests.sh` does the same for the tests.

### Scene structure

This mirrors the example mod: `exampletrack_base` holds the world, and each layout (`example_race_track`, `…_variant1`) holds only gameplay objects plus an `exampletrack_base#xref` subscene with `vis = true`, `col = true` (read from the example `.bgo3` files with `builder/bgo_read.py`). An event's `.evse` names the layout `.scne`, so the layout must pull in the base.

| Scene | Collection | Objects |
|---|---|---|
| base | `road` | `track_surface` (all routes, junction and crossing stitches, one mesh so junction vertices are shared), `track_kerbs`, `track_shoulders`, `track_edges` (#decal, no collision) |
| base | `walls` | `walls_<route>_l`, `walls_<route>_r` |
| base | `bridges` | `bridge_<id>_deck` (slab under the deck road), `bridge_<id>_barriers`, `bridge_<id>_pillars` |
| base | `terrain` | `terrain_XX_YY`: 128 m tiles of 4 m cells |
| base | `backdrop` | `backdrop_ground#lofi`, `backdrop_horizon#lofi`, `backdrop_cards_NN` (no collision) |
| base | `helpers` | `#static_cube_map` |
| base | `bounds` | `bounds_far_ground`, `bounds_kill_floor`, `bounds_water` (imported lakes / sea): invisible, see "Out of bounds" |
| base | `props` | `#xref ob/<prop>.scne` empties |
| layout | `base` | `#xref <slug>_base.scne` with `xdir = data/track/<slug>` |
| layout | `airoutes` | `#ai_route_main`, `#ai_safe_main`, `#ai_race_main`, `#ai_*_altN` |
| layout | `checkpoints` | `#checkpointNN[_split][_altN]` |
| layout | `startpoints` | `#startpoint01…NN`, `#startpointcarchange` |
| layout | `jumps` | `jump_<id>` (layout-only jumps) |
| layout | `bounds` | `bounds_reset`: the layout's `#resetplayer` reset wall and lid (invisible) |
| layout | `editing#exclude` | `ai_path`, `ai_path_altN` curves (Toolbox skips `#exclude` collections and curves) |

Every base mesh except `#static_cube_map`, `track_edges` and the `backdrop_*` meshes has the String custom property `CustomData = "col = true"`. Coordinates are the TrackModel's (metres, Z up), except for the placement transform below.

### Naming

| Object | Type | Purpose | Source |
|---|---|---|---|
| `#ai_route_main` | mesh, open quad strip | Blue drivable area (no wrong-way warning): road + 6 m (10 m without walls) each side | Handover; example layout; Toolbox AI Route Generator |
| `#ai_safe_main` | mesh, same topology | Red area AI keeps to: road edge − 0.5 m | Same; Bugbear `BugAiGeomGenerator.ms` (safe = road outline) |
| `#ai_race_main` | mesh, same topology | Green racing area: road edge − 1.5 m | Same (race sits 1 m inside safe) |
| `#ai_route_altN` etc. | meshes | Alternative route for TrackModel `aiIndex = −N`; first/last sector lines are the main route's lines at the junction vertices | Toolbox importer and the unofficial wiki (`_altN`); handover (negative indices, exact shared coordinates) |
| `otherway = true` | CustomData on `#ai_route_main` | Any layout whose driving-order strip isn't bgeometry's canonical orientation (`layout.needs_otherway`) | Measured on our builds, 2026-10-19 |
| `crossstart = true` | CustomData on `#ai_route_altN` | Alt route that bypasses the start line | Toolbox |
| `#checkpointNN` | mesh, one vertical quad | Numbered in driving order from 01 (first after the grid) to the start/finish line (last) | Handover; example layout |
| `_split` | checkpoint suffix | On `#checkpoint01` and the main checkpoint nearest half a lap | Example (`#checkpoint01_split`, `#checkpoint03_split` of 4) |
| `_altN` | checkpoint suffix | TrackModel checkpoint on an alternative route, numbered in sequence by its lap position | Wiki / Toolbox importer |
| `#startpoint01…NN` | empty, local +Y = forward | Grid slots in pairs | Handover; example (dummies; the example grid is 3 abreast, ours is 2 as the TrackModel lays it out) |
| `#startpointcarchange` | empty | Where the car is shown before the race | Handover, tutorial, wiki. The example has `#startpointpit01` instead (pit lane, used only when the `.tcat` enables it), so we follow the handover. |
| `#xref <slug>_base.scne` + `xdir` | empty | Base scene subscene → OBJX `<slug>_base#xref`, path `data/track/<slug>/<slug>_base.scn` | Example layouts; Toolbox `find_xref_path` (`xdir` keeps names under Blender's 63-byte limit) |
| `#xref ob/<prop>.scne` | empty | Stock prop subscene → `data/art/objects/<prop>.scn` | Toolbox; the example derby's `prop_trafficcone01#xref` |
| `#static_cube_map` | 0.3 m cube, `dyn = "data/property/object/ghost"`, material `ghost` with stock `tmp_ghost_c1` in slots 1 (colour) and 6 (opacity), 3.5 m above the start line | Reflection and ambient probe; invisible in game. An untextured material shows as a pink square (seen in game before 2026-10-18). | Example base (`ghost` material, 0.3 m cube, ~3 m up); every stock base (`ghost` + `tmp_ghost_c1`) |
| `ai_path` | open curve (not exported) | AI line for re-running Toolbox's AI Route Generator by hand | Handover / tutorial (not closed) |

### Surfaces and materials

Every texture is a **stock texture referenced by its game path** in the mounted install (`/wreckfest/data/...`; Toolbox writes it from `data/` on with a `.tga` extension and the game uses the `.bmap`), except five per-track maps generated by the builder. Per-environment choices live in `wfbuild/environments.json` (loaded by `wfbuild/envs.py`); `docs/visual-assets.md` has the palette.

- **Road, verge, terrain: stock-style 12-slot `#blend` materials** (`wfbuild/materials.py`), written through a node group named `trackgen#slots` (Toolbox exports its input *k* as texture slot *k*): albedo + `_ns` for R/G/B in slots 0-5 on UV1, with a Mapping scale for the tile size; the blend map (6), `detailblend_white` (7), the environment's macro `_ns` (8), the colour map (9, soft light), `default_clutter_colorgray_t` (10) and the clutter mask (11), all maps on **UV2**.
  - `road_<surface>_<env>#asphalt|gravel#blend`: three stock road textures per surface and environment (e.g. forest tarmac `ground_tarmac10d/10c/04d`), mixed by the road blend map (variant patches, worn edges).
  - `terrain_<env>#<surface>#<clutter group>#blend`: dirt (R) / grass (G) / third ground (B: farm field, forest floor, sandbank, gravel soil, concrete) mixed by the terrain blend map. The clutter group tag (`fields13`, `forest11`, `sandpit3`, `urban01`) turns on the stock grass clutter where the clutter mask's G is set.
  - `verge_<env>#gravel#blend`: the shoulders and run-off use the **terrain's textures and world-space UV1**, so they blend into the terrain without a seam; only the physics surface differs.
- **Road edge:** `track_edges`, a stock broken-asphalt `#decal#priority1` strip (fields `road_shoulder_fields13_c5`, forest and sandpit `road_*_asphalt_edge_01_c5`) bent over each plain tarmac edge (not under kerbs, junction mouths or bridges), its broken edge 0.3 m outside the road. Gravel and sand roads fade into the verge through their blend map instead.
- **Generated maps** (`wfbuild/maps.py`, from the distance-to-road field): `<slug>_blend_terrain_c`, `_blend_road_c`, `_color_terrain_c`, `_color_road_c` and `_clutter_t`, 1024² over the terrain extent, written as TGA under `mod/data/track/<slug>/textures/` and referenced as `data/track/<slug>/textures/...`; the pipeline converts them with `bimage -auto` into the mod. The clutter grass (G) is a band 5-80 m from the road, as in stock fields13, thinned evenly to the stock clutter budget (at most 5 ha at full density and a 0.18 mean within any 400 m square; denser masks crashed the game, see `docs/visual-assets.md` "Scene budgets"); the verge dirt fades out 2-12 m from the edge; crowds, the paddock and yards trample the ground.
- **UVs:** the game stores them as int16 / 2048, so they must stay within ±16. UV1 is wrapped per face by whole repeats (invisible for tiling textures); UV2 is 0..1 over the map frame.
- **Backdrop:** `far_ground_<env>#noshadow` (stock filler texture, `default_zero_spec_s` in slot 9), `horizon_<env>#horizon` (vista `_c5` in slots 1 and 6, `_fog` in 7) and `backdrop_card<k>_<env>#xtree` (stock `foliage_bgrforest*_c5`, or `bgr_city_01_c5` for industrial, plus `default_foliage_s`).
- Unchanged single-texture materials: `kerb#curb`, `bridge_concrete#concrete`, `ghost`; the invisible walls use `wall_col_concrete#concrete_barrier` / `wall_col_tyres#global_barrier` / `wall_col_armco#metal_barrier` with the stock ghost texture; measured barriers' visible meshes use `barrier_armco#metal_barrier` (`prop_guardrail01_c`), `barrier_tyres#global_barrier` (`prop_tirerubberbarrier_c`), `barrier_concrete#concrete_barrier` (`a_concrete2_c`) and `barrier_fence#metal_barrier` (`prop_chainlinkfence04_small_c5`, alpha in slot 6).

| `options.surface` | Road material |
|---|---|
| tarmac | `road_tarmac_<env>#asphalt#blend` |
| gravel | `road_gravel_<env>#gravel#blend` |
| sand | `road_sand_<env>#gravel#blend`. There is no plain sand road surface; `offroadsand` is for off-track |
| mixed | Tarmac and gravel alternate by checkpoint sector (the grid sector is tarmac); alternative routes are gravel |

bgeometry logs `Surface Settings does not exists: decal ()` and `... noshadow ()` for those render tags. Stock materials use the same tags, so the tests and the pipeline treat both as known notices.

### Geometry

- **Road:** one cross-section per TrackModel sample (~2 m) split into `round(width / 2)` lanes, so the faces are ~2 × 2 m quads, facing up.
- **Junctions:** an alternative route starts at the first cross-section that lies fully outside the road it leaves (+0.5 m). Ear-clipped triangles then stitch that cross-section to the main road's edge vertices between where the alt route's edge lines meet the main edge. These are the main road's own vertices, so there are no gaps, overlaps or T-junctions. The main road's kerbs, shoulders and walls are removed along the mouth.
- **Flat crossings** (and unannotated ones): the branch with the larger `|aiIndex|`, or the later pass of the same route, gets a hole that is stitched to the kept road on both sides.
- **Kerbs and track limits** (`geometry.kerb_plan`): only at corners (smoothed radius under 110 m, turning at least 15°): an apex kerb on the inside and an exit kerb on the outside from past the apex to beyond the exit, 1.2 m wide, rising 5 cm, stock red/white `prop_curb1_c` with its blocks along the road (2 m per red + white), `#curb`. Corners with an apex radius of 40 m or more get a 2.5 m green track-limit strip beyond the exit kerb (our own generated green paint `<slug>_limits_c`, surface `slowdown`), sloping from the kerb to 3 cm above the grass. An imported track's `kerbs` overrides (real data per corner, from the recipe) replace the geometric choice; the manifest's `kerbs` lists every corner with its source.
- **Terrain at the road:** the nearest road's surface (along its grade, across its camber, continuing the camber 1.5 m past the low edge), 15 cm down under the road (plus half the grade, so a 4 m terrain cell never pokes through) and 4 cm from 1.5 m out, blending to the ground over 30 m without dipping below the line from the edge to the ground further out (`TerrainField`). The old "lowest road within ~10 m" sank it 0.5-1 m on any grade.
- **Shoulders:** 3 m verges sloping 0.6 m down into the terrain (8 m and 0.4 m gravel run-off without walls), from the kerb or green strip where there is one.
- **Barriers** (`geometry.wall_plan`): not round the whole lap; grass run-off first and a barrier only where needed: a divider along the middle of the gap between close sections of track (another road within 30 m, at least 1.5 m from either edge, built by one of the two; drawn tracks also get a 0.8 m grass bank under it where the gap is 9 m or more, imported ones have the real ground), the grid/start/paddock stretch and under bridges (5.5 m out), in front of every crowd (`geometry.crowd_spots`, 12 m out), outside fast corners (apex radius 60-250 m, 14 m out) and where the ground drops more than 2.5 m within 8 m (5.5 m out); where they meet, the line moves 0.25 m per metre. Each barrier is an invisible collision wall (CustomData `vis = false | col = true`, like stock `wall_col` meshes, sunk 0.5 m) plus a row of the environment's stock barrier props (`dressing.barriers`). The blue AI route reaches 12 m over the run-off (less at a wall or half way to another road), so cars on the grass aren't reset at once.
- **Measured run-off of imported tracks** (`sketch.imported.runoff`, `wfbuild/runoff.py`; data and method in `docs/real-tracks.md`, "Run-off, barriers and foliage"): terrain faces in a gravel trap take `terrain_trap_<env>#slowdown#blend` (stock gravel as the R layer, no clutter, the ground 15 cm lower), faces on a paved apron `terrain_apron_<env>#asphalt#blend`; the wall plan puts each zone's real barrier (Armco, tyre wall, concrete) at its measured offset behind the trap instead of the generic fast-corner and drop-off barriers (dividers, the grid sector and crowds stay), the collision wall takes the type's surface (`#metal_barrier`, `#global_barrier`, `#concrete_barrier`), and the visible barrier is a mesh with stock textures (`barrier_*`, no collision: Armco W-beams on posts, black belted tyre wall, concrete, chain-link debris fence) instead of props. Red/white only where the data cites a source. Nothing stands in the run-off but the grid-sector dressing; `sketch.imported.trees` and measured woods (`cover`) replace the seeded trees.
- **Terrain:** a 4 m grid over the model bounds plus the 60 m margin, with a 5 m berm at the edge. It follows the lowest nearby road surface − 0.15 m and blends to trackgen_geom's seeded ground over 30 m. Bridge structures don't pull it up. The road surface is taken across its camber (the height at the point's offset from the centreline, clamped to the edges), so the ground meets the low edge of a cambered road.
- **Imported (real-world) tracks** (`sketch.imported`, e.g. `cadwellpark`): the road, kerbs, shoulders and edge decals follow the TrackModel's cambered edges; the AI strips (`#ai_*`) and the checkpoint planes' feet are tilted with the road (`Route.slope`); the grid slots come from geom already on the cambered surface. The ground is the import's real heightfield (`trackgen_geom.heightfield`) with no edge berm, and the backdrop's far ground continues it as far as the heightfield reaches (fading to the usual hills over 120 m). The import's OSM woods (`woodland`) place the trees and the forest floor and canopy shade in the maps (`wfbuild/woods.py`, `WoodMask`), and its `viewingSpots` get crowd banks on both sides first.
- **Bridges:** the over branch's deck is its own road surface. Over it, wherever the road is above ground within the under road's width + 15 m, there's an 0.8 m slab with 1 m barriers. Pillars stand outside the under road's walls. Clearance is ≥ 5.7 m (deck 6.5 m − slab).
- **Jumps:** `inBase` jumps are already in the road heights. One-way jumps in a reverse build (`inBase: false`) become `jump_<id>` objects in the forward layout scene only: the profile on top of the road, plus side faces.
- **Scenery** (`wfbuild/dressing.py`, pure numpy, seeded by the slug; manifest format in [docs/scenery-manifest.md](docs/scenery-manifest.md)):
  - start/finish: a gantry (`prop_start_startlights1`) over the line, flags, staff and a start grandstand on the roomier side; a paddock (tents, trucks, crew bus, toilets, food stand) on the other side;
  - crowds of standing spectators behind fences on the outside of the sharpest corners, after jump landings and beside bridges;
  - tyre stacks or straw bales on corner apexes; ad fences and big boards on straights; stadium lights by the start;
  - clustered foliage (shared noise, coverage per environment; an imported track's real woods instead), single park trees in parkland, saplings for sandpit, rocks, sand piles and logs; farmsteads (farmland), container yards and a city-building skyline (industrial);
  - coastal: gorse clumps (stock bushes at their own size: the game ignores `#xref` scale), stone field walls along a seeded parcel grid, a few farmsteads, rocks and cliff faces along the shore, nothing on ground under the sea, no tree cards;
  - only assets listed for the environment; no-go zones: the road and run-off (nothing but the gantry within wall offset + 1 m of an edge), the grid sector, bridge decks and ramps, jump landings;
  - about 1,000-4,000 subscenes per track (stock bases have 900-6,000).
- **Backdrop** (`wfbuild/backdrop.py`): a far-ground ring from the terrain edge to past the horizon with gentle hills (at most ~15 m, like stock), a `#horizon` cylinder 900 m beyond the terrain edge (closer where the ±2000 m limit leaves less room; computed after the placement shift) and `#xtree` card strips (two layers for forest). No collision (the invisible `bounds_far_ground` gives the near part of the far ground collision; see "Out of bounds"). With an imported `seaLevelM` (coastal) a flat `backdrop_sea#lofi` plane with a generated sea texture covers the terrain and far ground at sea level and the far ground stays seabed beyond the real ground; with an imported `horizon` profile the `#horizon` ring is centred on the drivers' eye and carries a generated skyline texture (`<slug>_horizon_c5`, one repeat, u = azimuth).
- **Out of bounds** (`wfbuild/bounds.py`; stock findings in `docs/visual-assets.md` "Out of bounds"). Stock tracks
  reset a car that leaves the track with invisible collision whose surface is **`#resetplayer`** (stock
  `resetwall` meshes: a low wall just outside the blue AI route, foot underground, and a lid 1-4 m above the
  ground reaching further out), whatever the race's reset setting, and their ground collision runs on for hundreds
  of metres. Until 2026-10-24 ours had no reset and the ground collision ended at the terrain edge (48 m past the
  blue route at the nearest), so a car that drove on fell through the world. Now, all invisible
  (`vis = false | col = true`, stock ghost texture):
  - `bounds_reset` per layout (`bounds_reset#resetplayer`, the same mesh on every layout): on the 4 m terrain
    grid, the cells within 4 m (`RESET_GAP`) of any layout's `#ai_route_*` strips, of any base mesh of kind `pit`
    (`ALLOWED_KINDS`), of imported extra roads (`imported.roads`) and of the run-off beside a road out to its
    barrier or the end of a measured gravel trap / apron are allowed, so no reset touches any layout's road,
    run-off or AI route. Beyond that (2026-10-26, Chris: the first walls were "too extreme") the allowed area
    reaches out from every road edge to twice the first walls' distance (`RESET_FACTOR` x the blue route's margin
    or the run-off reach, whichever is further, plus `RESET_GAP`) or 48 m (`RESET_MIN_M`), whichever is further,
    but stays 8 m (`RESET_EDGE_KEEP`) inside the terrain's edge. Round the outside (the cells reachable from the
    terrain edge; the infield isn't reset, as stock fields05) a wall from 4 m under to 2 m over the terrain,
    facing the track, and a 16 m lid 2 m above the terrain (narrower by the terrain's edge). ~5-11k faces.
    A pit lane or any other drivable area off the AI routes must be a base mesh of kind `pit` (or an AI route).
  - `bounds_crowd` (`#resetplayer`): the game can't detect a car hitting a person, so every spectator crowd (the
    dressing's `spectators`) is boxed in: walls 1.5 m (`CROWD_PAD`) round the people on a 0.5 m raster, from 1 m
    under the ground to a lid 3 m over it, kept 0.5 m (`PROTECT_PAD`) off every layout's road, run-off and blue
    route. A car that gets past the barrier into the crowd is reset.
  - `bounds_far_ground` (`#offroadfoliage`): collision on the backdrop's far ground, from under the terrain
    edge to ~230 m beyond it (`FAR_COLLISION_T`), the heights the far ground draws.
  - `bounds_water` (`#resetplayer`): flat sheets 0.3 m under an imported lake's level (within the lake planes'
    reach) or the sea, over terrain cells below that and off every layout's road, run-off and blue route;
    shallow edges stay drivable, deeper water resets. (Water in the data: `imported.lakes` with `levelM`, and
    `imported.seaLevelM` on coastal tracks; drawn tracks have none.)
  - `bounds_kill_floor` (`#resetplayer`): one plane 25 m below the lowest collision, 600 m beyond the terrain
    (within ±2 km after the placement), so anything that still falls is reset.
  The manifest has `bounds` (kill floor z, water cells, crowd cells, far-collision reach, the reset distances)
  and per layout `bounds` (allowed area, lid cells, faces).
- **Imported multi-layout tracks:** the TrackModel's `layouts` (e.g. Anglesey International and Coastal) each become a layout scene (`<slug>_race` for the main one, `<slug>_<id>`) sharing the base: the main one drives the main route only, an alternative one a closed route made of its TrackModel `sections` (branch and main, from its start/finish line: `layout.composite_route`), with its own evenly spaced checkpoints, plus one inside every stretch of its lap that a shortcut skips (`layout.shortcut_spans`: another road joining the lap, alone or chained, an untaken branch or link road, the main road past the layout's coned-off branch, the open pit lane, at most 10 % longer than what it skips; Oulton Trackday's bus stop, Cadwell Club past the Woodlands link), so cutting through the cones misses a checkpoint; `check_scene` fails a layout where a shortcut skips more than 30 m without passing one. Every layout (the main one too) gets its start points from its own `grid` and runs from its own timing line (`startFinish`, the finish checkpoint), with the grid between that and the real start line (`startLine`); layouts sharing the main line get the same timing line and grid. The events are titled with the track and layout names ("Cadwell Park Club"). `check_scene` and `check_export` check per layout that the finish checkpoint sits on the layout's line and the start points sit ahead of it facing the first checkpoint.
- **Imported U-turn links (aprons):** a branch that turns back between two adjacent sections of road a few metres apart (Cadwell Park's Club Hairpin from the run to the Mountain onto the start straight, the Woodlands hairpin at the Coppice end) has no cross-section clear of the other road, so no junction mouth. The builder paves the gap between the two sections where the turn crosses it instead (`TrackGeometry._apron`: one polygon of the two facing edges' vertices, shared with their road quads, listed under `junctions` with `kind: apron`); the branch keeps its AI route and checkpoints but no road, kerbs or walls of its own, and the dividers stop there. On imported tracks a branch still merging into its road within 60 m of its junction (`DIVIDER_MERGE_M`) needs no divider either, and two junctions on the same edge never share an edge segment (Anglesey's Bus Stop, where the Coastal joins one node before the National leaves).
- **Real pits (imported tracks, `wfbuild/pits.py`, 2026-10-24):** with `sketch.imported.pits` the base gets the real
  pit lane (`pitlane_<id>`, tarmac draped on the terrain) and its pit wall (`walls_pit_<id>`, invisible collision,
  plus stock concrete elements), closed at both ends; the track's barrier on the pit side is off along the pit wall
  (`wall_plan` hook) and the AI run-off margin stops in front of it. The planner reserves the pits first and places
  stock pit garages and buildings on the real footprints, real grandstands, paddock props on the real paddock (instead
  of the generic grandstand and paddock), the start lights (a cantilever on the pit wall) and stock footbridges over
  the road at real bridges and gantries (≥ 5 m clear). Checks: `tests/check_pits.py`. Details: `docs/real-tracks.md`,
  "Pits, paddocks, bridges and gantries".
- **Reverse layout:** the same geometry driven backwards. The grid uses the forward first-sector stretch facing the other way: the forward back row becomes the pole row, the line moves 4 m behind the new back row, and checkpoint 01 is 10 m past the pole. The other checkpoints run in reverse order.
- **Driving direction and `otherway`:** bgeometry ignores the face order of `#ai_route_main`. It stores the route in a canonical orientation (the first sector's right end south of, Blender −y, the last sector's left end) unless the mesh has `otherway = true`, and then in the other; the game drives the stored order (every stock layout's checkpoints follow it; Toolbox's importer reads `otherway` back with the same test). The builder makes every AI strip in driving order and sets `otherway` exactly when that strip isn't canonical (`layout.needs_otherway`), in forward and reverse layouts alike. Until 2026-10-19 forward strips never had it, so tracks whose start heading made the strip non-canonical ran backwards (Cadwell Park, figure8, the shortcut loops). `check_export` reads the built `.scne` (`scne_read.read_layout`) and fails when the checkpoints don't follow the stored route.
- **Checkpoints and alternative routes:** a main checkpoint inside an alternative route's bypassed section moves to 20 m after the rejoin, so both ways pass every main checkpoint and no proxy checkpoints are needed.
- **AI sectors:** ~16 m (8 samples), with forced lines at the junction vertices. Lines are shortened on the inside of tight corners. Any line whose sector wouldn't be a convex, upward-facing quad is dropped. The strip starts on the start line and runs 12 m past it, like the example.

### Findings from bgeometry (not in any doc)

1. **AI routes must start at Bugbear Z ≤ 0** (`createaidata.cpp(285): "startZ <= 0.f"`).
   - Bugbear Z is Blender y. The check uses the AI meshes' absolute coordinates: moving only the checkpoints or start points changes nothing, and moving only the AI meshes by +200 m makes it fail.
   - The builder therefore moves the whole track (base and layouts) along y, and rotates it by a multiple of 90° only if it would otherwise leave ±2000 m, so that both ends of every AI strip are at y ≤ −2.
   - `manifest.json` records the transform: `scene = rotate(model) + shift`.
   - **The layout's `#xref <slug>_base.scne` stays at the origin.** The base scene's vertices already carry the transform, so moving the reference as well shifts the whole world a second time in game. Until 2026-10-18 the builder did exactly that: on "Cadwell" (shift −48.6 m) the grid, checkpoints and AI routes ended up ~45 m off the road. `check_export.py` now places the exported base road through the exported layout's base reference and requires every start point, checkpoint centre and AI race-route start to lie on it.
2. **Sectors must be convex and face up** (`createaidata.cpp(258): "c.y > 0.f"`). This happened at alternative-route junctions with wide (10 m) route margins; the sector validation above prevents it.
3. **`otherway = true` is applied, but bgeometry logs it as an error.** It writes `custom data has illegal key otherway` to `Error.log`, for every spelling (`OtherWay`, `Otherway`), and still builds. The `.scne` differs from one built without the key, and `otherway = false` gives the same file as no key. The tests and the pipeline treat that line (and the same for `crossstart`) as a known notice.
4. **bgeometry can't read files on a Docker Desktop bind mount** (`Fatal disk error … FileCodecFile::ReadCacheBlock`). The tests build in the container's home and copy the results out.
5. **`wf-project` is not idempotent in speed.** A second run on the same root turns linked directories into real ones, link by link, which is slow. Call it once per project root.

### Tests

```powershell
.\worker\builder\run-builder-tests.ps1                    # all specs + determinism (--cpus 8, read-only mounts)
.\worker\builder\run-builder-tests.ps1 -Specs oval,figure8_bridge -Determinism ''
.\worker\builder\run-builder-tests.ps1 -Build             # rebuild the image first (needed after geom changes)
```

- **Specs:** the five geom samples, plus two variants from `tests/make_variants.py`:
  - `oval_concrete`: tarmac, concrete walls, no kerbs, industrial;
  - `shortcut_reverse`: the alt-route loop, reversed, no walls, sand, sandpit, with layout-only jumps.
- **`check_scene.py`** (in Blender) checks:
  - object names and counts;
  - `col = true` as a String on every solid base mesh, and none on the edge decal and the backdrop;
  - materials `name#tags` with a valid surface on solid meshes and only known tags (surfaces, `blend`, `decal`, `priorityN`, `horizon`, `xtree`, `noshadow`, `lofi`, existing clutter groups);
  - every texture resolves (a stock `.bmap`, or a generated map in the build's `mod/`);
  - the road, verge and terrain `#blend` materials have slots 0-9, with the blend, macro and colour maps on UV2;
  - every UV within ±16, and UV2 within 0..1 on `#blend` meshes;
  - the clutter mask: a grass band by the road, within the stock clutter budget;
  - far ground, `#horizon` and background cards per environment;
  - props are stock subscenes allowed for the environment and exist in the game;
  - `scenery.json` matches the props; only allowed assets; nothing but the gantry within the road/run-off clearance; the grid sector, bridge and jump-landing no-go zones are clear (recomputed from the TrackModel); every placement on the terrain; standing spectators face the road;
  - all vertices within ±2000 m;
  - ~2 m road squares;
  - unique road vertices, no non-manifold edges, no cracks (T-junctions) and no overlapping road faces;
  - junction stitches on the main road's edge vertices;
  - bridge clearance ≥ 4.5 m;
  - terrain below the road;
  - AI strips: open, identical topology, alt ends on main sector lines, ends at y ≤ 0;
  - checkpoints numbered 1..N, spanning the route, 01 after the grid;
  - start points facing the driving direction;
  - `otherway` exactly when the driving-order strip isn't canonical, the strip in driving order;
  - the base subscene reference;
  - `ai_path` open and not exported;
  - layout-only jumps.
- **Export and build:** the generated maps are converted with `bimage -auto`, Toolbox exports every scene, and bgeometry builds base first, then the layouts. Any non-empty `.scne` with an error, warning, assert or `Error.log` entry fails, except the known `otherway`/`crossstart` and `decal`/`noshadow` notices.
- **`check_export.py`** reads the `.bgo3` back with `bgo_read`. It checks that every mesh and empty was exported with its CustomData, that the xref paths are right (`data/track/<slug>/<slug>_base.scn` and `data/art/objects/*.scn`), that the start points are dummies and the AI routes are meshes, that the materials keep their `data/…` texture paths, that **every texture resolves** (a stock `.bmap` under `/wreckfest/data` or a texture in the mod, otherwise it would render pink), that the exported UVs are within ±16 (UV2 within 0..1), that the `#blend` materials keep the 12-slot layout with albedos on UV1 and maps on UV2, that the generated maps are in the mod as `.bmap`, that `#static_cube_map` uses the invisible `ghost` material, that **every start point, checkpoint centre and AI race-route start lies on the exported base road** as the layout's base reference places it, that **every layout has its minimap track data** (`<scene>.tcat` beside the `.scne`, v20, non-zero zoom, one entry per compiled AI route, none hidden from the minimap) and that the compiled main AI route the minimap draws matches the layout (bounds within 15 m of its centreline, same direction of travel, lap length within 5 %), and that the **built base `.scne` stays within the stock scene limits** (`scne_read.LIMITS`: largest draw batch, scene vertices, vertices with every prop instance, subscenes, unresolved subscenes and textures, clutter at full density and its 400 m maximum).
- **Out of bounds** (`tests/check_bounds.py`, run by both `check_scene.py` on the `.blend` files and
  `check_export.py` on the built `.scne` files, including `--track-data` and the bundle builder): every layout
  has its `#resetplayer` reset, at least 8 m outside every layout's blue route (none on any layout's road), and
  (scene check) every reset vertex at least twice as far from the road as the first walls stood and at least
  48 m (except by the terrain's edge); every spectator under the crowd reset's lid, no crowd or water reset over
  a layout's blue route; flooding a 2 m grid from the layout's road
  through every cell not under a reset face never reaches the edge of the collision ground (a car can't leave
  without being reset) and finds ground collision under every reached cell (no holes; finer cracks are the
  junction checks' job); the kill floor is 5+ m below every other collision face and under all of the ground;
  and (scene check, imported tracks) every terrain vertex more than 0.35 m under a lake or the sea has a water
  sheet over it. The shipped pre-fix Cadwell Park fails them (no reset; 118 ha reachable, off the edge of the
  terrain).
- **Determinism:** `oval` is built twice; the exported `.bgo3` files, `model.json`, `scenery.json` and the generated maps must be identical.

Results (2026-10-19, `--cpus 8`, 135 s for everything):

| Spec | Base | Result |
|---|---|---|
| oval | 576 objects (33 meshes, 543 props), 26.5k faces → 1.8 MB `.scne` | PASS (46 + 29 checks) |
| oval_concrete | 393 objects (349 props), 26.2k faces → 1.7 MB | PASS (46 + 29) |
| figure8_bridge | 1,138 objects (1,086 props), 44.1k faces → 3.7 MB | PASS (63 + 42) |
| loop_alt_jump | 1,079 objects (1,031 props), 48.0k faces → 3.8 MB | PASS (50 + 29) |
| hilly_circuit | 3,928 objects (3,817 props, forest), 89.5k faces → 7.6 MB | PASS (46 + 29) |
| hill_markers | 3,902 objects (3,787 props), 89.5k faces → 6.5 MB | PASS (46 + 29) |
| shortcut_reverse | 1,585 objects (1,544 props, sandpit), 35.3k faces → 2.7 MB | PASS (67 + 43) |
| determinism (oval) | identical `.bgo3`, `model.json`, `scenery.json` and maps | PASS |

Builder time per track: geometry 0.5-2.2 s, scenery plan 0.1-0.8 s, maps 1.0-1.6 s (`manifest.json` `timings`).

### Comparison renders

`builder/render/` renders a built base scene (and optionally the stock fields13 base) with Cycles on the CPU, using stock textures and stock props instanced at every subscene, from an oblique camera and at driver height on the pole slot (`-DriverAt 'name=metres,…'` adds driver views further round the lap). It only approximates the game's shaders (no clutter grass, no game lighting), so use it to judge layout and texture choice, not exact looks. See `builder/render/README.md`.

### Unverified in game

- The layout → base subscene reference (`<slug>_base#xref` with a `data/track/...scn` path). bgeometry stores it, and the example does the same with a `.max` path.
- Stock props as `ob/` subscenes in the base scene: they are stored and the paths exist, but they haven't been seen loaded. Now ~1,000-4,000 per track; frame rate unmeasured.
- The generated maps (`data/track/<slug>/textures/*.bmap`, bimage `-auto` formats), the `#blend` materials, the `#decal` road edge, the `#horizon`/`#noshadow`/`#xtree` backdrop and the clutter grass.
- The reverse layout:
  - driving-ordered meshes with `otherway` from the canonical test (forward layouts now verified in the built `.scne`);
  - the moved start line and grid;
  - reversed checkpoints.
- Alternative routes:
  - `_altN` names for TrackModel `aiIndex = −N`;
  - the alt checkpoint numbered by lap position;
  - relocating main checkpoints out of bypassed sections;
  - `crossstart`, with no proxy checkpoint generated.
- Layout-only jump objects (`inBase: false`) sitting on the shared road.
- Whether the lap is timed at the last checkpoint (the TrackModel's start/finish) or at the AI route start. Both are on the same line here.
- Surfaces and looks: the tyre wall approximation and kerb texture tiling.
- The whole-track move along y (needed by bgeometry) and the ±2000 m margin it uses.
- `#static_cube_map` placement (3.5 m above the start line).

## Pipeline

`pipeline/pipeline.py` turns a spec into an installable mod zip; `pipeline/consumer.py` runs it for queued builds. Both run in **Blender's bundled Python 3.11** (`wf-python`), which already has `trackgen_geom`; the image has no system Python, and one interpreter keeps the dependencies in one place.

### Steps

1. **Geometry:** `trackgen_geom.build_track_model(spec)`. A schema-invalid spec, a rejected sketch or any rule error fails the build with the reasons (e.g. `invalid spec: schema /options/lapLengthKm: 99 is greater than the maximum of 5.0`). The model goes to `model.json` and is passed to the builder.
2. **Scenes:** `blender -b build_scene.py --spec --model` → base and layout `.blend` + `manifest.json`. Builder exit codes 2/3 become "sketch rejected" / "doesn't fit within ±2000 m".
3. **Textures:** the builder's generated maps (`scenes/mod/data/track/<slug>/textures/*.tga`) → `bimage -auto` → `project/data/track/<slug>/textures/*.bmap` (three at a time), later copied into the mod.
3. **Export:** `blender -b builder/tests/export_scene.py` (Toolbox Mz) per scene → `project/data/track/<slug>/<scene>.bgo3`, base first.
4. **Build:** `wf-project project`, then `wf-tool bgeometry -v` per scene. stdout/stderr and any `Error.log` go into the build log. Known notices (bgeometry's `illegal key otherway|crossstart`, Wine's `XDG_RUNTIME_DIR`, Blender's `BKE_icon`) are counted under **info**. Anything else matching error/failed/warning/fatal/assert, a non-zero exit or an empty `.scne` fails the build.
5. **Package:** the mod tree (below), bag files with Toolbox's `wf_mod_gen/bagwrite.py`, menu images, `preview.jpg`, any `.tcat` uploads, `build-report.json`, then `<slug>.zip`.

Everything runs in a work dir on the container's own filesystem (bgeometry can't read Docker Desktop bind mounts); only the results are copied out. A build takes about 40 s (WinkyIsland, Cadwell) to 60 s (a reverse layout) with `--cpus 8`, up from 30-50 s before the scenery work; about half is `bimage` (≈2.6 s per image, mostly Wine start-up; 6 menu images plus 5 generated maps).

**Outputs:** `<slug>.zip`, `scenery.json` (the placement manifest), `build.log` (sections per step, then *info*, *warnings*, *errors*) and `build-report.json` (also inside the zip): versions (pipeline, trackgen_geom, builder, Blender, Wine, Toolbox commit), layouts and events, weathers, the placement transform, `.tcat`s included, warnings, the unverified-in-game list, every file with its size, and (in the copy beside the zip) the zip's size and SHA-256.

### CLI mode (no queue)

```powershell
docker run --rm --cpus 8 --memory 6g `
    -v "D:\SteamLibrary\steamapps\common\Wreckfest:/wreckfest:ro" `
    -v "E:\Source\wreckfest\geom\tests\samples:/in:ro" -v "E:\Source\wreckfest\.local\pipeline-out\cli\oval:/out" `
    trackgen-worker:dev build-mod --spec /in/oval.json --out /out [--tcat main=/in/main.tcat] [--keep-work]
```

Exit code 0 on success, 1 on failure (the log and report are written either way).

### Queue consumer

`entrypoint worker` (the image's default command; the Compose `worker` service):

| Env var | Default | |
|---|---|---|
| `STORAGE_CONNECTION_STRING` | required | Any Azure Storage connection string. The API's Azurite form `UseDevelopmentStorage=true;DevelopmentStorageProxyUri=http://azurite` is expanded to Azurite's documented dev account (blob `:10000`, queue `:10001`). |
| `API_BASE_URL` | required | `http://api:8080` in Compose (the API's container port). |
| `WORKER_SECRET` | required | Bearer secret for `PATCH /api/internal/builds/{id}`; must equal the API's `Worker:Secret`. Compose sets both from `TRACKGEN_WORKER_SECRET` in `.env`. |
| `QUEUE_NAME`, `VISIBILITY_TIMEOUT_S`, `HEARTBEAT_S`, `POLL_S`, `MAX_ATTEMPTS` | `builds`, 1800, 300, 5, 3 | |

Loop, one build at a time:

1. Receive one message with a 30 min visibility timeout; poll every 5 s when idle. Malformed messages are deleted.
2. `PATCH running`. A **404 or 409** (unknown or already finished/timed-out build) deletes the message and moves on. If the API is unreachable, the message becomes visible again after 30 s.
3. Download `specs/{specBlobName}` and every `tcats/{trackId}/*.tcat`; run the pipeline in `~/work/build-*` (removed afterwards).
4. Every 5 min while it runs: `PATCH running` (heartbeat, the API times builds out after 2 h) and extend the message's visibility by 30 min.
5. Upload `artifacts/{buildId}/build.log` and `build-report.json`, and on success `artifacts/{buildId}/{slug}.zip`. Then `PATCH succeeded` with `logBlobName` + `artifactBlobName`, or `PATCH failed` with `logBlobName` and a short `errorMessage`.
6. Delete the message after the final PATCH (also on 404/409). If the final PATCH can't be delivered, the message is kept so the build is retried.
7. After `MAX_ATTEMPTS` deliveries a message is reported `failed` ("worker gave up") and deleted.

**SIGTERM/SIGINT:** when idle the consumer exits at once. During a build it kills the running tool, makes the message visible again immediately (the next worker retries it; re-sending `running` is allowed) and exits within a few seconds. Compose gives it `stop_grace_period: 30s`.

**Compose:** the service stays under the `worker` **profile**, because it needs the Wreckfest install and Chris's machine; `docker compose up` alone still starts api + geom + azurite. Start everything with `docker compose --profile worker up -d`. Limits: `cpus: 4`, `mem_limit: 6g`; the game is mounted read-only at `/wreckfest`.

### Mod layout (zip extracts as `<slug>/…`)

Mirrors `mods/example` (track folder `exampletrack`, event group `example`; we use the slug for both and one event per layout, id = layout scene name):

| Ours (`gravel_oval`) | Example mod |
|---|---|
| `modinfo.modi` (`bagwrite.modi_file`, tag 8 = track, visibility private) | `modinfo.modi` |
| `preview.jpg` (1280×720) | `preview.jpg` |
| `build-report.json` | – |
| `data/property/event/<slug>/<slug>.envi` | `data/property/event/example/example.envi` |
| `data/property/event/<slug>/<slug>_race.evse`, `<slug>_race_reverse.evse` | `…/example/example_race_inner_oval.evse` etc. |
| `data/property/weather/<slug>/<slug>.weli` (stock `.weat` paths) | `data/property/weather/example/example.weli` (+ its own `example_afternoon1.weat`) |
| `data/track/<slug>/<slug>_base.scne` | `data/track/exampletrack/exampletrack_base.scne` |
| `data/track/<slug>/<slug>_race.scne` + `<slug>_race.tcat` (generated, or the recorded upload) | `data/track/exampletrack/example_race_track.scne` + `.tcat` |
| `data/track/<slug>/menu/event_<layout>_{loading_1920x1200,results_812x162,small_250x116}_raw.bmap` | `data/track/exampletrack/menu/event_example_race_track_…_raw.bmap` |
| `data/menu/textures/env_<slug>_{large_832x386,small_250x116}_raw.bmap` | `data/menu/textures/env_example_…_raw.bmap` |
| `data/menu/textures/event_<layout>_small_250x116_raw.bmap` | `data/menu/textures/event_example_race_inner_oval_small_250x116_raw.bmap` |

Shipped besides the scenes: the five generated maps as `data/track/<slug>/textures/*.bmap`. Not shipped: `.bgo3`/`.blend` sources (the example ships `.bgo3`/`.max`), `.tga` sources, stock textures and props (we reference stock ones). Each `.evse` is state Final (2), gamemode Racing (1), scene `data/track/<slug>/<layout>.scne`, weather list `data/property/weather/<slug>/<slug>.weli`; titles are the track name (+ " Reverse").

**Track data and the minimap.** Wreckfest 1 has no minimap image per track: it draws the HUD minimap at run time from the layout's compiled AI routes (the road between the race and safe lines, per-route `Minimap Race To Safe Bias` / `Minimap Track Width Extend`, world x/z seen from above; colours and scale from the global `data/property/rendering/minimap_settings.mise`). It sets the minimap up while loading the layout's track data, which it looks for as `<scene dir>/<scene>.tcat` or `data/property/track/<scene>.tcat` (`track.cpp`: `%s%s.tcat`, `data/property/track/%s.tcat`, then `%s_minimap`). Every stock layout and Workshop track ships one, the official guide lists it in the simplest track, and our mods shipped none: Cadwell Park and Anglesey had no minimap (2026-10-19). The builder now writes one per layout (`wfbuild/tcat.py`, written to `scenes/mod/data/track/<slug>/<layout>.tcat`, copied beside the layout `.scne`): v20 like stock (field order from BagEdit's `bbTrackData` schema; `tcat.read` parses all 120 stock v20 files, except the special zones of forest02_1), minimap highlight 2.0 / zoom 1.0, one route entry per AI route (bias 0.3, width extend 0, drive probability 0.9 main / 0.1 alternatives, as stock fields04_2), grid generation off (flag 2, as Workshop tracks: our start points are the grid), a start camera and trackside cameras (below), the lap length.

**Intro view: `resultcamera` (2026-10-25).** The race intro ("WELCOME TO ...") does not use the `.tcat` cameras: decoded from `Wreckfest_x64.exe`, the prerace state calls the camera manager's result-camera setup (the string `resultcamera`, camera mode 3, `IngameAnimatedCamera`), which looks up the scene objects `resultcamera` and `resultcamera_target` by name and views from the first towards the second, each at its world position (stock animates them with keyframes). With either missing its update puts the camera at the world origin looking along -z: the level, under-the-ground view on every generated track (Cadwell's terrain is 31.7 m above the origin, Oulton's 16 m). The `.tcat` start camera (flag 2) is read only by `IngameTrackSideCamera` (camera mode 5: the cinematic camera button and replays). Every stock base carries both objects (small boxes with `dyn = "data/property/object/ghost"`); ours and the example's had none. `build_scene.result_camera` now puts the pair in every layout scene at the start camera's eye and target (`BUILDER_RESULT_CAMERA=base|static|none` builds in-game A/B variants), and `check_export.check_result_camera` requires one of each, the camera 2-150 m above the terrain and the target near an AI route. **Not yet seen in game.**

**Cameras (2026-10-24).** Chris saw every generated circuit load in from under the ground, the "WELCOME TO ..." race intro looking level from near the world origin, first with no cameras and again with a start camera in the `.tcat`. Stock layouts carry, besides one fixed start camera, trackside cameras with "sector activation" covering the lap (crm01_1: 17, sandpit01_1: 15); ours had none, so the game fell back to its default camera. Camera records (`tcam` v11, field order from BagEdit's bbTrackCamera table): u32 type (enum 0 rotating, 1 fixed, 2 moving), u32 flags (bit enum from BagEdit: 2 "start camera", 4 "sector activation"), default fov, zoom start distance, activation route, active-between and move-between sectors (indices into the compiled main AI route's sectors), tracking start/end distance, a dof reference, then `tcde` entries: world position x, y up, z (Blender x, z, y), rotation pitch, yaw, roll in degrees (yaw from +z towards +x), fov, screen position, smoothing. The convention is pinned on stock: `scne_read` applies each model's matrix, and `check_export.check_stock_cameras` requires sandpit01_1's and forest01_1's start cameras (decoded by our own `raw_cameras`) within 60 m of their pole and 1-30 m above their terrain (measured: 4.2 and 10.5 m). `wfbuild/cameras.py` writes per layout the start camera (28-80 m ahead of the pole, 6-24 m beyond the local road edge, 6 m above the terrain, looking at the front of the grid) and a rotating trackside camera per ~160 m of the lap (sector ranges from the `#ai_route_main` faces, which bgeometry compiles one sector each; 7-28 m beyond the edge, 5 m above the terrain, clear of roads, water, props and no-go zones, with a clear view of its sectors; fallback 15 m above the road). `check_export.check_track_data` (also run by the bundle tool and `check_export.py --track-data <dir>`) decodes the cameras from the raw `.tcat` bytes itself and requires one start camera with the pole in view, trackside cameras whose sector ranges cover every compiled sector, each within 250 m of its sectors, and every camera 2-150 m above the exported terrain. `render/Render-Scenes.ps1 -Intro` renders the start camera and the first trackside cameras from the shipped `.tcat`. The manifest records each layout's minimap outline (`layouts[].minimap`: bounds, signed area, length of the driving-order centreline in scene coordinates) and `check_export` compares it with the compiled main AI route.

**`.tcat` uploads** (`tcats/{trackId}/{layout}.tcat`) go beside their layout `.scne` and replace the generated one: `main`/`race`/`forward` → `<slug>_race.tcat`, `main_reverse`/`reverse`/`race_reverse` → `<slug>_race_reverse.tcat`; the layout scene name itself also works. Unknown names are skipped with a warning.

**Menu images (2026-10-24):** drawn per layout from the TrackModel with Pillow (`pipeline/images.py`; Chris: the old ones, the geom preview SVG scaled down and the same for every layout, were unreadable at thumbnail size). Like stock event thumbnails (`data/menu/textures/event_*_small_250x116_raw`: a thick white outline with an arrowhead on a transparent background), each layout's images are tight-cropped to its own lap with a fixed margin: a white stroke with a dark outline (about 4 px at 250x116, scaled with the image), a chequered start/finish bar across it and a direction arrow; the circuit's other roads only as a faint thin line, and only on the larger images. The small event images (track menu folder and `data/menu/textures`) are the outline alone on a transparent background; the results (812x162) and loading (1920x1200) images, the environment's large (832x386, all layouts) and `preview.jpg` are dark cards with the title; the environment's small image is the outline on dark, no text. `images.check_menu` fails the build when a layout misses an image, a size is wrong or two layouts share a picture. PNG copies go to `<out>/thumbs/`. Each image is drawn at its named size in the top-left of a power-of-two canvas (250×116 in 256×128, 812×162 in 1024×256, 832×386 in 1024×512, 1920×1200 in 2048×2048), saved as an uncompressed `.tga` under a staging `data/…` path and converted with `bimage -auto`.

### Weather mapping

The schema's weathers are time-of-day slots. `pipeline/weather.py` maps them per environment preset to stock `.weat` files (listed from the mounted `data/property/weather/`), checking that each exists; a slot falls back to the nearest time of day (morning → sunrise, evening → sunset) or to another family. Paths below are under `data/property/weather/`:

`parkland` uses fields13, then forest11, then fields14 (its slots match farmland's where fields13 has them). `coastal` uses fields14, then fields13, then gravel1.
`airfield` uses fields14, then fields13, then fields08; `arid` sandpit3, then sandpit1, then gravel1.

| Slot | gravel_pit | farmland | forest | industrial | sandpit |
|---|---|---|---|---|---|
| sunrise | fields/gravel1_sunrise1 | fields/fields13_sunrise1 | forest/forest11_sunrise1 | urban/urban06_sunrise1 | sandpit/sandpit3_sunrise1 |
| morning | fields/fields13_morning1 | fields/fields13_morning1 | forest/forest11_morning1 | urban/urban06_morning1 | sandpit/sandpit3_sunrise1 |
| midday | fields/gravel1_midday1 | fields/fields13_midday1 | forest/forest11_midday1 | urban/urban06_midday1 | sandpit/sandpit3_midday1 |
| noon | fields/fields14_noon1 | fields/fields13_noon1 | forest/forest11_noon1 | urban/urban06_noon1 | sandpit/sandpit3_midday1 |
| afternoon | fields/gravel1_afternoon1 | fields/fields13_afternoon1 | forest/forest11_afternoon1 | urban/urban06_afternoon1 | sandpit/sandpit3_afternoon1 |
| evening | fields/fields13_sunset2 | fields/fields13_sunset2 | forest/forest11_evening1 | urban/urban09_sunset2 | sandpit/sandpit3_sunset1 |
| sunset | fields/gravel1_sunset1 | fields/fields14_sunset1 | forest/forest11_sunset1 | urban/urban07_sunset1 | sandpit/sandpit3_sunset1 |

Duplicates are removed, so a sandpit spec with `morning` and `sunrise` lists one file. Inexact matches are build warnings. Print the table against an install with `wf-python /opt/worker/pipeline/weather.py /wreckfest`.

### Unverified in game

Also listed in every `build-report.json`, plus the builder's own list ([Unverified in game](#unverified-in-game)):

- Bag files (`.envi`, `.evse`, `.weli`, `modinfo.modi`) have format dword `01 00 00 00` (bagwrite, uncompressed); stock files use `04 00 00 00` (lz4).
- **`defaultLaps` is not written:** bagwrite's `.evse` (v17) has no laps field, so the game's default lap count applies. It is recorded in the report only.
- Event state Final and gamemode Racing; one event per layout with the layout scene name as event id.
- Weathers referencing other tracks' stock `.weat` files.
- Menu image names, power-of-two canvases and `bimage -auto` formats.
- The generated `.tcat` (minimap, no trackside cameras, grid generation off). Record one in game for cameras (`*`, then `K`, `J`, `T`) and upload it; it replaces the generated one.

### End-to-end results (2026-10-18)

- CLI, `--cpus 8`: `oval` → `gravel_oval.zip` 1.15 MB (14 files) in 30 s; `figure8_bridge` → `figure_eight.zip` 2.56 MB (20 files, two events) in 50 s. The only info notices were Wine's XDG line and one `illegal key otherway` (reverse layout).
- Queued (`docker compose up -d azurite geom api` + worker): admin bootstrap invite → signup → login → `POST /api/tracks` (oval.json) → `POST /api/tracks/{id}/builds` → `queued` → `running` → `succeeded` in 36 s → artifact and log downloaded through the API. A SIGTERM during a figure-8 build (with `HEARTBEAT_S=10`: heartbeats at 10 s and 20 s) cancelled it and requeued the message; the restarted worker built it on attempt 2.
- An invalid spec fails with the schema/rule reason in `errorMessage` and the log.

## Smoke test results (2026-10-18)

Run on Docker Desktop 29.5 (WSL2, Windows host) with `--cpus 8`. All four tests pass, and each takes about a second.

| Test | What it proves | Result |
|---|---|---|
| a | `bimage -auto` under Wine converts a 256×256 `.tga` generated in the container | **PASS**: 827-byte `.bmap`, header `04 00 00 00 "pamb"` |
| b | `bgeometry -v` under Wine converts the example mod's `example_race_track.bgo3` | **PASS**: 2,699-byte `.scne`, **byte-identical to the stock file** |
| c | In `blender -b`: enable Toolbox Mz, build a road plane (material `road#asphalt`, `CustomData = "col = true"`), export with `wftb.export_bgo_with_dialog`, then run `bgeometry` | **PASS**: 788-byte `.bgo3` → 468-byte `.scne`. bgeometry reports model `road`, material `road#asphalt`, 4 vertices. |
| d | `bagwrite.BagWrite` writes `.envi`, `.evse`, `.weli` and `modinfo.modi` | **PASS**: every file starts with the format dword plus the reversed tag (`ivne`, `esve`, `ilew`, `idom`) |

Extra checks, run by hand:

- `SMOKE_BGO=exampletrack_base` (10 MB, 18,522 vertices) builds in about 1 s and is **byte-identical** to the stock `exampletrack_base.scne`.
- `bimage` on the example mod's `grass_small_01_c5.tga` and `grass_small_01_n.tga` gives `.bmap` files **byte-identical** to the stock ones.

### Image size

The image is **3.37 GB** (3.36 GB before the builder):

| Layer | Size |
|---|---|
| Wine prefix | 807 MB |
| Blender | 1.31 GB |
| trackgen_geom + jsonschema in Blender's Python | 10.7 MB |
| builder scripts | 125 KB |
| apt layer (Wine, X and GL libraries) | the rest |

Two ways to make it smaller later:

- create the Wine prefix at container start instead (about 8 s);
- leave out the bundled Blender pieces we don't use.

## Quirks and findings

1. **Wine can't execute PE files straight from the Docker Desktop bind mount.**
   - **Symptom:** Run directly from `/wreckfest/tools`, both tools fault at their entry point (`page fault on write access to 0x0` in `EntryPoint`, where the code reads as zeros).
   - **Cause:** The 9p/drvfs share doesn't map PE images correctly.
   - **Fix:** `wf-tool` copies the `.exe` into the container's own filesystem (`~/.cache/wftools`) on first use. That copy exists only in the running container. Expect the same on Azure Files, so keep this staging step.
2. **bgeometry needs the game's `data/` under the *project root*.**
   - bgeometry treats the folder containing the input's `data` folder as the project root.
   - From there it reads files such as `data/property/settings/surfacelist.srfl` (otherwise: `could not find surface physics file`).
   - A bare file gives `invalid project path`.
   - `wf-project` builds the overlay as symlinks:
     - the project's own files win;
     - then any mod roots you pass;
     - then `/wreckfest/data`.
   - Always pass **absolute Linux paths** to `-input`/`-output`; Wine maps `/` to `Z:`.
   - bimage doesn't need a project root.
3. **Toolbox Mz in background mode.**
   - **Crash:** Its operators call `WindowManager.popup_menu()`, which **segfaults Blender 4.2 under `-b`**. The `.bgo3` has already been written by then, but Blender dies.
   - **Popup fix:** `wf_toolbox_headless.enable()` swaps `popup_menu` for a print in background mode.
   - **Settings:** It also sets the preferences in code: `wf_path=/wreckfest`, `username`, `build_after_export=False` and `build_bmap=False`.
     - The add-on's own build step runs `wine bgeometry` in a detached thread that `blender -b` doesn't wait for.
     - Its bimage path uses a Windows `\`.
     - So the worker runs the tools itself with `wf-tool`.
   - **Breckfest.exe isn't needed** for exporting, bag writing or building. Only the add-on's SCNE/BMAP *importers* use it.
4. **Bag file format dword.** `bagwrite` writes `01 00 00 00`, marked "uncompressed" in its source. Stock game files start with `04 00 00 00`. The tag layout is otherwise the same. Toolbox's Mod Generator ships files written this way, so the game is assumed to accept both; check this in game when the first generated mod is installed.
5. **Harmless noise.** Wine prints `error: XDG_RUNTIME_DIR is invalid…`. The tools are console apps: no Xvfb is needed, and none is started unless `WORKER_XVFB=1` is set. Blender prints a few `BKE_icon_get` errors when the add-on registers.
6. **`trackgen_geom`** (from `../geom`) is installed into Blender's Python by the Dockerfile, after the Wine prefix layer so a geom change doesn't rebuild the big layers. Rebuild the image after changing `geom/src`. The Toolbox itself needs no extra Python packages.

**Fallback (not needed):** if Wine stops working, a Windows worker on the dev PC can pull from the same queue.
