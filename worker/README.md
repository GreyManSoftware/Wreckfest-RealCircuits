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
| `builder/wfbuild/` | `/opt/worker/builder` | Builder package: `geometry` (road, junctions, kerbs, walls, terrain, bridges, jumps), `layout` (AI routes, checkpoints, start points), `scatter` (props), `config` (dimensions, materials, presets), `bscene` (the only `bpy` module). |
| `builder/bgo_read.py` | `/opt/worker/builder` | Stdlib reader for `.bgo3` files: used by the tests and to inspect the example mod. |
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
| `<slug>_base.blend` | Road, kerbs, shoulders, walls, bridges, terrain tiles, `#static_cube_map`, prop subscenes |
| `<slug>_race.blend` | Forward layout: AI routes, checkpoints, start points, the base subscene, layout-only jumps |
| `<slug>_race_reverse.blend` | Reverse layout (only when the model has `reverse: true`) |
| `manifest.json` | Files, layouts (line position, sectors, checkpoints with lap positions, relocated checkpoints), junction stitches, bridges, placement transform, per-scene objects/materials, warnings |
| `model.json` | The TrackModel (when built from the spec) |

The pipeline ([Pipeline](#pipeline)) exports each `.blend` with Toolbox (`wf_toolbox_headless.export_bgo`) to `data/track/<slug>/<scene>.bgo3` and runs bgeometry, **base first** (the layouts reference it). `builder/tests/run_builder_tests.sh` does the same for the tests.

### Scene structure

This mirrors the example mod: `exampletrack_base` holds the world, and each layout (`example_race_track`, `…_variant1`) holds only gameplay objects plus an `exampletrack_base#xref` subscene with `vis = true`, `col = true` (read from the example `.bgo3` files with `builder/bgo_read.py`). An event's `.evse` names the layout `.scne`, so the layout must pull in the base.

| Scene | Collection | Objects |
|---|---|---|
| base | `road` | `track_surface` (all routes, junction and crossing stitches, one mesh so junction vertices are shared), `track_kerbs`, `track_shoulders` |
| base | `walls` | `walls_<route>_l`, `walls_<route>_r` |
| base | `bridges` | `bridge_<id>_deck` (slab under the deck road), `bridge_<id>_barriers`, `bridge_<id>_pillars` |
| base | `terrain` | `terrain_XX_YY`: 128 m tiles of 4 m cells |
| base | `helpers` | `#static_cube_map` |
| base | `props` | `#xref ob/<prop>.scne` empties |
| layout | `base` | `#xref <slug>_base.scne` with `xdir = data/track/<slug>` |
| layout | `airoutes` | `#ai_route_main`, `#ai_safe_main`, `#ai_race_main`, `#ai_*_altN` |
| layout | `checkpoints` | `#checkpointNN[_split][_altN]` |
| layout | `startpoints` | `#startpoint01…NN`, `#startpointcarchange` |
| layout | `jumps` | `jump_<id>` (layout-only jumps) |
| layout | `editing#exclude` | `ai_path`, `ai_path_altN` curves (Toolbox skips `#exclude` collections and curves) |

Every base mesh except `#static_cube_map` has the String custom property `CustomData = "col = true"`. Coordinates are the TrackModel's (metres, Z up), except for the placement transform below.

### Naming

| Object | Type | Purpose | Source |
|---|---|---|---|
| `#ai_route_main` | mesh, open quad strip | Blue drivable area (no wrong-way warning): road + 6 m (10 m without walls) each side | Handover; example layout; Toolbox AI Route Generator |
| `#ai_safe_main` | mesh, same topology | Red area AI keeps to: road edge − 0.5 m | Same; Bugbear `BugAiGeomGenerator.ms` (safe = road outline) |
| `#ai_race_main` | mesh, same topology | Green racing area: road edge − 1.5 m | Same (race sits 1 m inside safe) |
| `#ai_route_altN` etc. | meshes | Alternative route for TrackModel `aiIndex = −N`; first/last sector lines are the main route's lines at the junction vertices | Toolbox importer and the unofficial wiki (`_altN`); handover (negative indices, exact shared coordinates) |
| `otherway = true` | CustomData on `#ai_route_main` | Reverse layout | Handover; Toolbox |
| `crossstart = true` | CustomData on `#ai_route_altN` | Alt route that bypasses the start line | Toolbox |
| `#checkpointNN` | mesh, one vertical quad | Numbered in driving order from 01 (first after the grid) to the start/finish line (last) | Handover; example layout |
| `_split` | checkpoint suffix | On `#checkpoint01` and the main checkpoint nearest half a lap | Example (`#checkpoint01_split`, `#checkpoint03_split` of 4) |
| `_altN` | checkpoint suffix | TrackModel checkpoint on an alternative route, numbered in sequence by its lap position | Wiki / Toolbox importer |
| `#startpoint01…NN` | empty, local +Y = forward | Grid slots in pairs | Handover; example (dummies; the example grid is 3 abreast, ours is 2 as the TrackModel lays it out) |
| `#startpointcarchange` | empty | Where the car is shown before the race | Handover, tutorial, wiki. The example has `#startpointpit01` instead (pit lane, used only when the `.tcat` enables it), so we follow the handover. |
| `#xref <slug>_base.scne` + `xdir` | empty | Base scene subscene → OBJX `<slug>_base#xref`, path `data/track/<slug>/<slug>_base.scn` | Example layouts; Toolbox `find_xref_path` (`xdir` keeps names under Blender's 63-byte limit) |
| `#xref ob/<prop>.scne` | empty | Stock prop subscene → `data/art/objects/<prop>.scn` | Toolbox; the example derby's `prop_trafficcone01#xref` |
| `#static_cube_map` | 1 m cube, `dyn = "data/property/object/ghost"` | Reflection and ambient probe | Example base; wiki |
| `ai_path` | open curve (not exported) | AI line for re-running Toolbox's AI Route Generator by hand | Handover / tutorial (not closed) |

### Surfaces and materials

Materials are `name#surface`, with surfaces from the game's `surfacelist.srfl` (the wiki's list is in `wfbuild/config.py`). Each has one Principled BSDF with a Base Color image whose file path points at the **stock texture in the mounted install** (`/wreckfest/data/...`). Toolbox writes it from `data/` on with a `.tga` extension (`data/art/textures/x.tga`), the same way it handles any texture; bgeometry and the game use the stock `.bmap`. Nothing is copied. UVs are in metres / tile size.

| `options.surface` | Road material |
|---|---|
| tarmac | `road_asphalt#asphalt` (`ground_race_tarmack_01_c`) |
| gravel | `road_gravel#gravel` (forest `ground_gravel_01_c`) |
| sand | `road_sand#gravel` (fields `road_sand_05_fields_c`). There is no plain sand road surface; `offroadsand` is for off-track |
| mixed | Asphalt and gravel alternate by checkpoint sector (the grid sector is asphalt); alternative routes are gravel |

Also `kerb#curb`, `shoulder#gravel` (`runoff#gravel` without walls), `wall_concrete#concrete` (as the example's `wall#concrete`), `wall_tyres#default` (tyre barrier texture), `bridge_concrete#concrete`, and terrain per preset: gravel_pit `#offroadsand`, farmland and forest `#offroadfoliage`, industrial `#concrete`, sandpit `#offroadsand`. The tests check that every texture exists as a `.bmap` in the install.

### Geometry

- **Road:** one cross-section per TrackModel sample (~2 m) split into `round(width / 2)` lanes, so the faces are ~2 × 2 m quads, facing up.
- **Junctions:** an alternative route starts at the first cross-section that lies fully outside the road it leaves (+0.5 m). Ear-clipped triangles then stitch that cross-section to the main road's edge vertices between where the alt route's edge lines meet the main edge. These are the main road's own vertices, so there are no gaps, overlaps or T-junctions. The main road's kerbs, shoulders and walls are removed along the mouth.
- **Flat crossings** (and unannotated ones): the branch with the larger `|aiIndex|`, or the later pass of the same route, gets a hole that is stitched to the kept road on both sides.
- **Kerbs:** 1.2 m `#curb` strips outside both edges where the radius is under 150 m, dilated and tapered at their ends.
- **Shoulders:** 3 m verges sloping 0.6 m down into the terrain (8 m and 0.4 m gravel run-off without walls).
- **Walls:** a concrete (jersey-like) or tyre (rounded) profile 5.5 m outside the road edge, sunk 0.5 m into the terrain. Walls stop where they would enter another road's corridor and on folds inside tight corners.
- **Terrain:** a 4 m grid over the model bounds plus the 60 m margin, with a 5 m berm at the edge. It follows the lowest nearby road height − 0.15 m and blends to trackgen_geom's seeded ground over 30 m. Bridge structures don't pull it up.
- **Bridges:** the over branch's deck is its own road surface. Over it, wherever the road is above ground within the under road's width + 15 m, there's an 0.8 m slab with 1 m barriers. Pillars stand outside the under road's walls. Clearance is ≥ 5.7 m (deck 6.5 m − slab).
- **Jumps:** `inBase` jumps are already in the road heights. One-way jumps in a reverse build (`inBase: false`) become `jump_<id>` objects in the forward layout scene only: the profile on top of the road, plus side faces.
- **Props:** a deterministic scatter (seed = SHA-256 of the slug) of stock props per environment, 1.5 per hectare (max 160), at least 16 m outside every road edge and 8 m apart.
- **Reverse layout:** the same geometry driven backwards. The grid uses the forward first-sector stretch facing the other way: the forward back row becomes the pole row, the line moves 4 m behind the new back row, and checkpoint 01 is 10 m past the pole. The other checkpoints run in reverse order. The AI meshes keep forward order with `otherway = true`.
- **Checkpoints and alternative routes:** a main checkpoint inside an alternative route's bypassed section moves to 20 m after the rejoin, so both ways pass every main checkpoint and no proxy checkpoints are needed.
- **AI sectors:** ~16 m (8 samples), with forced lines at the junction vertices. Lines are shortened on the inside of tight corners. Any line whose sector wouldn't be a convex, upward-facing quad is dropped. The strip starts on the start line and runs 12 m past it, like the example.

### Findings from bgeometry (not in any doc)

1. **AI routes must start at Bugbear Z ≤ 0** (`createaidata.cpp(285): "startZ <= 0.f"`).
   - Bugbear Z is Blender y. The check uses the AI meshes' absolute coordinates: moving only the checkpoints or start points changes nothing, and moving only the AI meshes by +200 m makes it fail.
   - The builder therefore moves the whole track (base and layouts) along y, and rotates it by a multiple of 90° only if it would otherwise leave ±2000 m, so that both ends of every AI strip are at y ≤ −2.
   - `manifest.json` records the transform: `scene = rotate(model) + shift`.
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

- **Specs:** the four geom samples, plus two variants from `tests/make_variants.py`:
  - `oval_concrete`: tarmac, concrete walls, no kerbs, industrial;
  - `shortcut_reverse`: the alt-route loop, reversed, no walls, sand, sandpit, with layout-only jumps.
- **`check_scene.py`** (in Blender) checks:
  - object names and counts;
  - `col = true` as a String on every base mesh;
  - materials `name#surface` with valid surfaces and existing stock textures;
  - props from the preset list that exist in the game;
  - all vertices within ±2000 m;
  - ~2 m road squares;
  - unique road vertices, no non-manifold edges, no cracks (T-junctions) and no overlapping road faces;
  - junction stitches on the main road's edge vertices;
  - bridge clearance ≥ 4.5 m;
  - terrain below the road;
  - AI strips: open, identical topology, alt ends on main sector lines, ends at y ≤ 0;
  - checkpoints numbered 1..N, spanning the route, 01 after the grid;
  - start points facing the driving direction;
  - `otherway` only in reverse layouts;
  - the base subscene reference;
  - `ai_path` open and not exported;
  - layout-only jumps.
- **Export and build:** Toolbox exports every scene. bgeometry builds base first, then the layouts. Any non-empty `.scne` with an error, warning, assert or `Error.log` entry fails, except the known `otherway`/`crossstart` notice.
- **`check_export.py`** reads the `.bgo3` back with `bgo_read`. It checks that every mesh and empty was exported with its CustomData, that the xref paths are right (`data/track/<slug>/<slug>_base.scn` and `data/art/objects/*.scn`), that the start points are dummies and the AI routes are meshes, and that the materials keep their `data/…` texture paths.
- **Determinism:** `oval` is built twice, and the exported `.bgo3` files and `model.json` must be identical.

Results (2026-10-18, `--cpus 8`, under a minute for everything):

| Spec | Base | Layouts | Result |
|---|---|---|---|
| oval | 51 objects (21 meshes, 15 terrain tiles, 30 props), 24.8k faces → 1.5 MB `.scne` | race: 3 AI, 6 checkpoints, 16+1 start points → 2.5 KB | PASS (33 + 17 checks) |
| oval_concrete | 50 objects, 21.8k faces → 1.3 MB | race → 2.5 KB | PASS |
| figure8_bridge | 95 objects (bridge deck, barriers, 4 pillars; 54 props), 41.3k faces → 3.1 MB | race, race_reverse (`otherway`) → 5.0 KB each | PASS (50 + 26) |
| loop_alt_jump | 95 objects (59 props), 44.9k faces → 3.2 MB; 2 junction stitches | race with `#ai_*_alt1`, `#checkpoint04_alt1`, one relocated checkpoint → 4.6 KB; track moved −200 m in y | PASS (37 + 17) |
| hilly_circuit | 204 objects (60 terrain tiles, 138 props), 86.9k faces → 6.4 MB | race → 7.2 KB; moved −77 m | PASS (33 + 17) |
| shortcut_reverse | 90 objects, 33.5k faces → 2.2 MB | race with `jump_kicker`, `jump_hop` → 24.5 KB; race_reverse → 4.5 KB | PASS (54 + 27) |
| determinism (oval) | identical `.bgo3` and `model.json` | | PASS |

### Unverified in game

- The layout → base subscene reference (`<slug>_base#xref` with a `data/track/...scn` path). bgeometry stores it, and the example does the same with a `.max` path.
- Stock props as `ob/` subscenes in the base scene: they are stored and the paths exist, but they haven't been seen loaded.
- The reverse layout:
  - `otherway = true` on forward-ordered meshes;
  - the moved start line and grid;
  - reversed checkpoints.
- Alternative routes:
  - `_altN` names for TrackModel `aiIndex = −N`;
  - the alt checkpoint numbered by lap position;
  - relocating main checkpoints out of bypassed sections;
  - `crossstart`, with no proxy checkpoint generated.
- Layout-only jump objects (`inBase: false`) sitting on the shared road.
- Whether the lap is timed at the last checkpoint (the TrackModel's start/finish) or at the AI route start. Both are on the same line here.
- Surfaces and looks: `road_sand#gravel`, the tyre wall approximation, terrain textures, kerb texture tiling, and the single-texture materials (no `#blend`, no normal or spec maps).
- The whole-track move along y (needed by bgeometry) and the ±2000 m margin it uses.
- `#static_cube_map` placement (centre of the terrain, 15 m up).

## Pipeline

`pipeline/pipeline.py` turns a spec into an installable mod zip; `pipeline/consumer.py` runs it for queued builds. Both run in **Blender's bundled Python 3.11** (`wf-python`), which already has `trackgen_geom`; the image has no system Python, and one interpreter keeps the dependencies in one place.

### Steps

1. **Geometry:** `trackgen_geom.build_track_model(spec)`. A schema-invalid spec, a rejected sketch or any rule error fails the build with the reasons (e.g. `invalid spec: schema /options/lapLengthKm: 99 is greater than the maximum of 5.0`). The model goes to `model.json` and is passed to the builder.
2. **Scenes:** `blender -b build_scene.py --spec --model` → base and layout `.blend` + `manifest.json`. Builder exit codes 2/3 become "sketch rejected" / "doesn't fit within ±2000 m".
3. **Export:** `blender -b builder/tests/export_scene.py` (Toolbox Mz) per scene → `project/data/track/<slug>/<scene>.bgo3`, base first.
4. **Build:** `wf-project project`, then `wf-tool bgeometry -v` per scene. stdout/stderr and any `Error.log` go into the build log. Known notices (bgeometry's `illegal key otherway|crossstart`, Wine's `XDG_RUNTIME_DIR`, Blender's `BKE_icon`) are counted under **info**. Anything else matching error/failed/warning/fatal/assert, a non-zero exit or an empty `.scne` fails the build.
5. **Package:** the mod tree (below), bag files with Toolbox's `wf_mod_gen/bagwrite.py`, menu images, `preview.jpg`, any `.tcat` uploads, `build-report.json`, then `<slug>.zip`.

Everything runs in a work dir on the container's own filesystem (bgeometry can't read Docker Desktop bind mounts); only the results are copied out. A build takes about 30 s (oval) to 50 s (figure 8 with a reverse layout) with `--cpus 8`; about half is `bimage` (≈2.6 s per image, mostly Wine start-up).

**Outputs:** `<slug>.zip`, `build.log` (sections per step, then *info*, *warnings*, *errors*) and `build-report.json` (also inside the zip): versions (pipeline, trackgen_geom, builder, Blender, Wine, Toolbox commit), layouts and events, weathers, the placement transform, `.tcat`s included, warnings, the unverified-in-game list, every file with its size, and (in the copy beside the zip) the zip's size and SHA-256.

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
| `data/track/<slug>/<slug>_race.scne` (+ `<slug>_race.tcat` when uploaded) | `data/track/exampletrack/example_race_track.scne` + `.tcat` |
| `data/track/<slug>/menu/event_<layout>_{loading_1920x1200,results_812x162,small_250x116}_raw.bmap` | `data/track/exampletrack/menu/event_example_race_track_…_raw.bmap` |
| `data/menu/textures/env_<slug>_{large_832x386,small_250x116}_raw.bmap` | `data/menu/textures/env_example_…_raw.bmap` |
| `data/menu/textures/event_<layout>_small_250x116_raw.bmap` | `data/menu/textures/event_example_race_inner_oval_small_250x116_raw.bmap` |

Not shipped: `.bgo3`/`.blend` sources (the example ships `.bgo3`/`.max`), `.tga` sources, textures and props (we reference stock ones). Each `.evse` is state Final (2), gamemode Racing (1), scene `data/track/<slug>/<layout>.scne`, weather list `data/property/weather/<slug>/<slug>.weli`; titles are the track name (+ " Reverse").

**`.tcat` uploads** (`tcats/{trackId}/{layout}.tcat`) go beside their layout `.scne`: `main`/`race`/`forward` → `<slug>_race.tcat`, `main_reverse`/`reverse`/`race_reverse` → `<slug>_race_reverse.tcat`; the layout scene name itself also works. Unknown names are skipped with a warning.

**Menu images:** the geom preview SVG (labels stripped) is rendered with cairosvg and laid out with Pillow on a dark card with the track name and lap length / environment / surface. Like the example's sources, each image is drawn at its named size in the top-left of a power-of-two canvas (250×116 in 256×128, 812×162 in 1024×256, 832×386 in 1024×512, 1920×1200 in 2048×2048), saved as an uncompressed `.tga` (bottom-left origin, as the example) under a staging `data/…` path and converted with `bimage -auto` (which records the `data/…` path, as in the example's `.bmap`s).

### Weather mapping

The schema's weathers are time-of-day slots. `pipeline/weather.py` maps them per environment preset to stock `.weat` files (listed from the mounted `data/property/weather/`), checking that each exists; a slot falls back to the nearest time of day (morning → sunrise, evening → sunset) or to another family. Paths below are under `data/property/weather/`:

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
- Without a recorded `.tcat` the game makes its own grid and trackside cameras (record one in game: `*`, then `K`, `J`, `T`).

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
