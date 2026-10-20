# tools

Local Windows helpers (phase 11).

## Start-TrackGen.ps1 / Stop-TrackGen.ps1 / New-TrackGenInvite.ps1

Run the local beta stack and its Dev Tunnel; see the runbook in the root [README](../README.md).
`TrackGen.Common.ps1` holds shared helpers. The Dev Tunnels CLI path comes from `$env:DEVTUNNEL_EXE` (default
`devtunnel.exe`, then `devtunnel` on PATH); the tunnel ID from `-TunnelId` /
`$env:TRACKGEN_TUNNEL_ID` (default `trackgen`). The tunnel host is started detached (via WMI) so it outlives the
console; `Stop-TrackGen.ps1` stops it by the PID in `.local\tunnel.pid`.

## Install-TrackMod.ps1

Installs a generated track mod zip (one top-level `<slug>\` folder with `data\`, `modinfo.modi`,
`preview.jpg`) into `<GameDir>\mods\<slug>`. Download the zip from the site first (no auth support;
`-Url` works for unauthenticated local URLs only).

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
  `data\property\event\<slug>\`, `Start-WreckfestServer.ps1` `$circuitMap` / `$expected`). It never
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

Tests (fake game dir under `.local\fake-game`, never the real D: install):

```powershell
pwsh -File tools\tests\Install-TrackMod.Tests.ps1
powershell -ExecutionPolicy Bypass -File tools\tests\Install-TrackMod.Tests.ps1
```

## Later

- Import a recorded `.tcat` back to the site.
- Workshop publishing (option C) always needs an explicit approval click.

## import_real_track.py

Turns a real circuit into a TrackSpec with `sketch.imported` (exact centreline, heights, camber, terrain, woods)
from OpenStreetMap and the Environment Agency LIDAR DTM. Recipes live in `real_tracks/`; downloads are cached in
`.local\<slug>\`. See [docs/real-tracks.md](../docs/real-tracks.md).

```powershell
geom\.venv\Scripts\python tools\import_real_track.py tools\real_tracks\cadwellpark.json --out geom\tests\samples\cadwellpark.json --verify
```
