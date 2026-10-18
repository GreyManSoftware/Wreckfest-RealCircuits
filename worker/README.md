# worker

The Linux build worker turns a track spec into a Wreckfest mod. It will run as a Container Apps Job, started by a Storage Queue message. This folder holds the **toolchain image and smoke tests** (todo `toolchain-gate`); the queue-driven build script comes later.

The image contains:

- Blender **4.2 LTS**, which runs headless with `blender -b`;
- the **Toolbox Mz** add-on, pinned to one commit;
- **Wine64**, which runs the game's own `bgeometry.exe` (`.bgo3` → `.scne`) and `bimage.exe` (`.tga`/`.png` → `.bmap`).

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
| `scripts/entrypoint.sh` | `/opt/worker/scripts` | Entrypoint. `info` (default) prints versions and checks the mount. `smoke [a b c d]` runs the smoke tests. Anything else is run as a command. |
| `scripts/wf-tool` | on `PATH` | `wf-tool bgeometry\|bimage <args>` runs a game tool under Wine. |
| `scripts/wf-project` | on `PATH` | `wf-project ROOT [MOD_ROOT…]` overlays the game's `data/` onto a writable project root. |
| `blender/wf_toolbox_headless.py` | `/opt/worker/blender` | Enables Toolbox Mz for headless use and wraps its BGO exporter. |
| `smoke/` | `/opt/worker/smoke` | Smoke tests: `run-smoke.sh` runs inside the container, `run-smoke.ps1` runs on the Windows host. |

The image runs as the non-root user `worker` (uid 1000). It has a pre-initialised 64-bit Wine prefix at `/home/worker/.wine`, and `WINEDEBUG=-all` is set.

## Build and run

The build uses roughly one or two cores; it is mostly downloads and single-threaded `xz` decompression. Docker data lives on `E:\DockerImages`.

```powershell
# Build
docker build -f worker/Dockerfile -t trackgen-worker:dev .
# or: docker compose --profile worker build worker

# Versions and mount check
docker compose --profile worker run --rm --no-deps worker

# Smoke tests via compose (uses the scripts baked into the image)
docker compose --profile worker run --rm --no-deps worker smoke

# Smoke tests from the host (mounts worker\scripts, blender and smoke from the repo, so no rebuild is needed)
.\worker\smoke\run-smoke.ps1                # all tests, --cpus 4
.\worker\smoke\run-smoke.ps1 -Tests b,c -Build
```

- **Game path:** the game root comes from `WRECKFEST_DIR` (default `D:/SteamLibrary/steamapps/common/Wreckfest`). You can set it in `.env`.
- **CPU and memory:** compose limits the worker to 4 CPUs and 4 GB. `run-smoke.ps1` uses `--cpus 4 --memory 4g` by default; change them with `-Cpus` and `-Memory`.
- **Dependencies:** `--no-deps` stops compose from also starting Azurite.

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

The image is **3.36 GB**:

| Layer | Size |
|---|---|
| Wine prefix | 807 MB |
| Blender | 1.31 GB |
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
6. **Not installed yet:** `trackgen_geom` (from `../geom`). It goes into Blender's Python when the Blender builder is written. The Toolbox itself needs no extra Python packages.

**Fallback (not needed):** if Wine stops working, a Windows worker on the dev PC can pull from the same queue.
