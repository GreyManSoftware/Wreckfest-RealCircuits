# WreckFest-TrackGen

Sketch a Wreckfest race circuit in the browser, pick game-supported options, and get a buildable mod zip.

It also builds real circuits from open survey data: the line from OpenStreetMap, heights, camber and widths from
public LIDAR. The first release is the Greyman Software Track Pack 1 on the
[Steam Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3826192302).

- Real circuits: [docs/real-tracks.md](docs/real-tracks.md)
- Plan: [docs/track-generator-plan.md](docs/track-generator-plan.md)
- Conventions, build and test: [AGENTS.md](AGENTS.md)

The web app runs as a small invite-only beta; there is no public instance.

## Game files are not included

This repository contains no Wreckfest files. The builder references stock assets by name and runs the game's own
tools (`bgeometry.exe`, `bimage.exe`) from your install, mounted read-only into the build container at runtime. You
need your own copy of Wreckfest (PC, Steam) to build a track.

## Disclaimer

Unofficial fan project. Not affiliated with or endorsed by Bugbear Entertainment, THQ Nordic, MotorSport Vision,
the Circuito de Navarra or any circuit. Circuit names are trademarks of their owners and are used only to say which real track a layout follows.

## Licence

Code and docs: MIT, see [LICENSE](LICENSE). Derived data, sponsor logos and third-party code keep their own terms;
the scope note in `LICENSE` lists them.

## Data and licences

The committed real-circuit data (`tools/real_tracks/`, `tools/real_tracks_data/*.json`, `geom/tests/samples/`) is
derived from the sources below; raw downloads are never committed. Redistributing it, or a mod built from it, needs
the same attribution. Full table: [docs/real-tracks.md](docs/real-tracks.md#data-sources-and-attribution).

| Data | Licence | Attribution |
|---|---|---|
| Circuit layouts, woodland, pits, run-off features | ODbL 1.0 (derived data stays under the ODbL) | © OpenStreetMap contributors |
| Heights and widths, England | Open Government Licence v3.0 | © Environment Agency copyright and/or database right |
| Heights, Wales (Anglesey) | Open Government Licence v3.0 | Contains Natural Resources Wales information © Natural Resources Wales and database right |
| Heights and widths, Navarre | CC BY 4.0 | Servicio proporcionado por el Gobierno de Navarra (IDENA) |

The Linux build image installs [Wreckfest Toolbox Mz](https://github.com/gmazy/wreckfest_toolbox) (MIT) and
Blender (GPL) at build time; see [worker/README.md](worker/README.md).

## Runbook (Chris): local beta over a Dev Tunnel

The whole stack runs in Docker Compose on this PC (`api` on `127.0.0.1:8080` only; `geom`, `azurite`, `worker`
stay local). Data lives under `.local\data\` (`api\` = SQLite + key ring, `azurite\` = specs, zips, logs). Testers
reach the API port through the persistent Dev Tunnel `trackgen`; their guide is
[docs/beta-testers.md](docs/beta-testers.md).

```powershell
# Dev Tunnels login (when `devtunnel user show` says expired / not logged in). Opens a browser.
& 'devtunnel.exe' user login -g     # or set $env:DEVTUNNEL_EXE

.\tools\Start-TrackGen.ps1             # local only: http://localhost:8080/
.\tools\Start-TrackGen.ps1 -Tunnel     # + host the tunnel (detached; PID in .local\tunnel.pid, log .local\tunnel.log)
.\tools\Start-TrackGen.ps1 -Rebuild    # rebuild images after pulling code
.\tools\Stop-TrackGen.ps1              # stop tunnel host + containers (data kept)
.\tools\Stop-TrackGen.ps1 -TunnelOnly  # just close the tunnel

.\tools\New-TrackGenInvite.ps1 -Note 'for a tester'           # single-use user invite (72 h) - send code + URL
.\tools\New-TrackGenInvite.ps1 -Admin -Hours 24 -Note me  # admin invite
```

- **First admin:** with no users, `Start-TrackGen.ps1` prints the bootstrap admin invite from the API log. It's
  replaced on each API restart until someone signs up; afterwards use the admin page or `New-TrackGenInvite.ps1`.
- **Tunnel:** created once with anonymous access (`devtunnel create trackgen --allow-anonymous`, port 8080, http);
  the URL (`https://<id>-8080.<region>.devtunnels.ms/`) stays the same while the tunnel exists. Tunnels expire
  after 30 days without use; the script recreates it (new URL) if it's gone. Visitors see a one-time Dev Tunnels
  warning page; the app's invite sign-up is the real gate.
- **Cookies behind the tunnel (verified):** the tunnel sends `X-Forwarded-Proto: https`, `-Host` and `-For`; the
  API sets `__Host-trackgen_session` (Secure, `Path=/`, no `Domain`, SameSite=Strict) and it works through the tunnel.
  The interstitial only adds its own `.Tunnels.Relay.*` / `tunnel_phishing_protection` cookies on the same host and
  doesn't affect the SPA or the session. The rate limiter keys on the forwarded client IP, and `/api/internal/*`
  requests arriving through the tunnel get 404. Running `-Tunnel` makes `trackgen` the CLI's default tunnel.
- **Reset:** `Stop-TrackGen.ps1`, delete `.local\data\api` (and `.local\data\azurite`), start again.
