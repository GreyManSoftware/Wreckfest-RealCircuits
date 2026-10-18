# WreckFest-TrackGen

Sketch a Wreckfest race circuit in the browser, pick game-supported options, and get a buildable mod zip.

- Plan: [docs/track-generator-plan.md](docs/track-generator-plan.md)
- Conventions, build and test: [AGENTS.md](AGENTS.md)

Private project for a few invited users. Game files are licensed and are never committed or baked into images.

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
