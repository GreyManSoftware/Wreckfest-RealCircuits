# tools

Local Windows helpers (phase 11).

## Install-TrackMod.ps1

Installs a generated track mod zip (one top-level `<slug>\` folder with `data\`, `modinfo.modi`,
`preview.jpg`) into `<GameDir>\mods\<slug>`. `-Url` downloads it first (unauthenticated local URLs only).

```powershell
.\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip                 # install
.\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip -WhatIf         # preview only
.\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip -Force          # replace an earlier install of my_oval
.\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip -Server         # also print server staging changes
.\tools\Install-TrackMod.ps1 -Url http://localhost:5000/x.zip   # download, then install
.\tools\Install-TrackMod.ps1 -Uninstall -Slug my_oval           # remove
```

Parameters: `-GameDir` (default `D:\SteamLibrary\steamapps\common\Wreckfest`), `-ServerDir` (default =
`-GameDir`; the dedicated server uses that install's `server_config.cfg`), `-TempRoot` (default
`.local\tmp`).

Safety:
- The zip is validated before extraction: exactly one top-level folder matching `[a-z0-9_]+`, containing
  `data\`; any absolute or `..` (zip-slip) entry is refused.
- Extracts to `.local\tmp` on E:, then moves into `mods\<slug>` and writes a `.trackgen` marker.
- Existing folders are never overwritten without `-Force`, and `-Force`/`-Uninstall` only touch folders
  with the `.trackgen` marker (`mods\example` and hand-made mods are safe).
- `-Server` only **prints** diff-style changes (`mods=`, `el_add=` per `.evse` in
  `data\property\event\<slug>\`, `$circuitMap` / `$expected` in a server start script, `Start-WreckfestServer.ps1`, not part of this repo). It never
  writes `server_config.cfg`, `initial_server_config.cfg` or `Start-WreckfestServer.ps1`. Ask Chris
  before applying.

### Enabling the mod without the Mods menu

**Close Wreckfest first.** The game rewrites the mod list when it exits, and the script refuses to write while
`Wreckfest_x64.exe`/`Wreckfest.exe` is running (dedicated servers started with `server_config` are ignored).

```powershell
.\tools\Install-TrackMod.ps1 -Zip .\my_oval.zip -Enable          # install, then enable in the mod list
.\tools\Install-TrackMod.ps1 -Enable -Slug my_oval -WhatIf      # preview
.\tools\Install-TrackMod.ps1 -Enable -Slug my_oval              # enable an installed mod
.\tools\Install-TrackMod.ps1 -Disable -Slug my_oval
.\tools\Install-TrackMod.ps1 -Enable -Slug my_oval -DisplayName 'My Oval'   # rename the listed entry
.\tools\Install-TrackMod.ps1 -ListMods                          # print names, order and enabled state
```

The list is `C:\Program Files (x86)\Steam\userdata\<id>\228380\local\wreckfest\modlist.mods`. It is found
automatically. If more than one Steam user has one, pass `-SteamUserId <id>`, or pass `-ModListPath <file>`.
Before each write, the script backs up the file to `modlist.mods.bak-<yyyyMMdd-HHmmss>`, next to it and in
`.local\modlist-backups\`. It then re-reads the written file and restores the backup if the file doesn't verify.
An existing `mods/<slug>/` entry is switched on. Otherwise a new entry is added, named from the mod's
`modinfo.modi` (or the slug; a modinfo name that is a file name, from older builds, becomes the title-cased slug
with a warning). `-DisplayName 'Name'` sets the name and also renames an entry that is already listed, keeping its
enabled state. Works in Windows PowerShell 5.1 and pwsh 7. Needs Python 3.11+ (`py` or `python`); the format is described in
[docs/modlist-format.md](docs/modlist-format.md).

### Bundles (`-Bundle`)

A bundle built by `Build-TrackBundle.ps1` (below) holds several track mods. The same track in two enabled mods
clashes, so `-Bundle` (game closed) reads the bundle's `bundle.json`, switches every member mod off in the mod list,
and the bundle's own Workshop copy (`publishedId`, when subscribed), then installs and enables the bundle.
`-Uninstall -Bundle` switches the bundle off, removes it and switches the installed members and the Workshop copy
back on. (2026-10-26: with both copies of the pack enabled, the AI drove off the road on 7 of 9 circuits.) Each mod-list write makes its own backup.

```powershell
.\tools\Install-TrackMod.ps1 -Zip E:\WreckfestMods\msvanglesey.zip -Bundle -Force   # -WhatIf to preview
.\tools\Install-TrackMod.ps1 -Uninstall -Slug msvanglesey -Bundle
```

Tests (fake game dir under `.local\fake-game`, never the real D: install):

```powershell
pwsh -File tools\tests\Install-TrackMod.Tests.ps1
powershell -ExecutionPolicy Bypass -File tools\tests\Install-TrackMod.Tests.ps1
```

## Build-TrackBundle.ps1

Merges per-circuit mod zips into one Workshop mod. Config: `bundles\<slug>.json` (title, Published Id,
visibility, member list, description parts, credits). Re-runnable: it rebuilds from the current zips and replaces
`<OutDir>\<slug>.zip` only when every check passes. The PASS/FAIL lines go to `.local\bundles\<slug>-checks.log`.
Python 3.11+ with numpy and Pillow (`py`); PS 5.1 and 7.

```powershell
.\tools\Build-TrackBundle.ps1                    # bundles\msvanglesey.json, zips from E:\WreckfestMods -> E:\WreckfestMods\msvanglesey.zip
.\tools\Build-TrackBundle.ps1 -KeepFolder        # also leave the unzipped E:\WreckfestMods\msvanglesey\
.\tools\Build-TrackBundle.ps1 -Slug p -Title 'P' -Zip a.zip, b.zip -OutDir E:\Source\wreckfest\.local\bundles
```

Every member's `data\` tree is kept as it is. The bundle gets its own root files:

- `modinfo.modi` in BagEdit's Mod Publish format;
- `preview.jpg`: a 16:9 collage of the members' maps;
- `CREDITS.txt`;
- `bundle.json`.

It fails, without writing the zip, on any of these:

- a file path two members share;
- a duplicate track, environment, event, layout or weather id;
- a translation key with two texts;
- a stock-file override;
- an unresolved event or weather reference;
- `scne_read` limits on any base scene;
- check_export's driving-order or `check_track_data` (.tcat) check on any layout;
- personal data in the shipped text;
- a Workshop limit.

Publishing steps: [docs/workshop-publishing.md](../docs/workshop-publishing.md).

Tests: `py -m pytest -q tools\tests\test_build_track_bundle.py` (also in CI); the scene checks are check_export's.

## Later

- Import a recorded `.tcat` back to the site.
- Workshop publishing always needs Chris's explicit approval. He publishes himself with BagEdit's Mod Publish
  (docs/workshop-publishing.md).

## import_real_track.py

Turns a real circuit into a TrackSpec with `sketch.imported` (exact centreline, heights, camber, terrain, woods)
from OpenStreetMap and the Environment Agency LIDAR DTM. Recipes live in `real_tracks/`; downloads are cached in
`.local\<slug>\`. See [docs/real-tracks.md](../docs/real-tracks.md).

```powershell
geom\.venv\Scripts\python tools\import_real_track.py tools\real_tracks\cadwellpark.json --out geom\tests\samples\cadwellpark.json --verify
```
