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

Tests (fake game dir under `.local\fake-game`, never the real D: install):

```powershell
pwsh -File tools\tests\Install-TrackMod.Tests.ps1
powershell -ExecutionPolicy Bypass -File tools\tests\Install-TrackMod.Tests.ps1
```

## Later

- Import a recorded `.tcat` back to the site.
- Workshop publishing (option C) always needs an explicit approval click.
