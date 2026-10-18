# worker

Linux build worker (Container Apps Job, started by a Storage Queue message). Not built yet: the image is the next todo, **toolchain-gate**.

Planned image:

- Blender **4.2 LTS** headless (`blender -b`), with **Toolbox Mz** pinned to a specific commit.
- **Wine64** to run the game's `bgeometry.exe` (`.bgo3` → `.scne`) and `bimage.exe` (texture conversion).
- `trackgen_geom` installed from `../geom`.
- Bag files (`.evse`, `.envi`, `.weli`, `modinfo`) written by Toolbox Mz `wf_mod_gen/bagwrite.py`.

**Game tools and stock assets are mounted at runtime** (local bind mount or Azure Files share), **never baked into the image or committed**.

Smoke tests for the toolchain gate:

1. `bimage` converts a `.tga` under Wine.
2. `bgeometry` converts the example `.bgo3` to `.scne`.
3. The Toolbox Mz BGO exporter runs in `blender -b`.

Fallback if Wine fails: a Windows worker on the dev PC pulling from the same queue.
