# AGENTS.md — Wreckfest Track Generator

Conventions for humans and coding agents working in this repo. The plan lives in
`docs/track-generator-plan.md`; background research in `docs/custom-track-handover.md`.

## Hard constraints

- **E: only.** All downloads, caches and toolchains live on `E:`, never `C:`. Use the gitignored
  `E:\Source\wreckfest\.local\` for caches, e.g.:
  ```powershell
  $env:NUGET_PACKAGES   = 'E:\Source\wreckfest\.local\nuget'
  $env:npm_config_cache = 'E:\Source\wreckfest\.local\npm-cache'
  $env:PIP_CACHE_DIR    = 'E:\Source\wreckfest\.local\pip-cache'
  ```
- **Python:** call `py` or `python` (Python ≥ 3.11). Never `python3` on Windows.
- **CPU ≤ 75%.** Don't generate artificial load. Every Docker Compose service must have `cpus:` and
  `mem_limit:` set.
- **Never publish to Steam Workshop** without Chris's explicit approval (and never automatically).
- **Never commit secrets:** server passwords, `password=` values, admin Steam IDs (`7656119…`), IP
  addresses, tokens, player names/IDs. Use `.env` files (gitignored) for local secrets.
- **Game files:** don't modify anything under `D:\SteamLibrary\…`. Game tools (`bgeometry.exe`,
  `bimage.exe`) and stock assets are **mounted at runtime, never baked into images or committed**.
  Game formats (`*.bmap`, `*.scne`, `*.bgo3`, `*.weat`, `*.tcat`, …) are gitignored.
- **Server:** don't start/stop the Wreckfest dedicated server, and don't change voting, laps, bots or
  existing tracks without Chris's approval. Stop it only by PID (`Stop-WreckfestServer.ps1`).
  `AGENT-HANDOVER.md` (server runbook) stays local and is gitignored because it has host/LAN details.

## Layout

| Path | What |
|---|---|
| `schema/` | `track-spec.schema.json` + `track-rules.json` — **single source of truth** for the track spec and option rules; `codegen/`, `test-vectors/` |
| `src/Api/` | ASP.NET Core (.NET 10) minimal API `TrackGen.Api` (auth, tracks, jobs) |
| `tests/Api.Tests/` | xUnit tests for the API (`WebApplicationFactory`) |
| `src/web/` | React + TypeScript (Vite) frontend, vitest |
| `geom/` | Python package `trackgen_geom` (geometry core) + FastAPI service `trackgen_geom.service` |
| `worker/` | Linux build worker (Blender 4.2 LTS + Toolbox Mz + Wine64) — placeholder |
| `infra/` | Terraform for Azure Container Apps — placeholder |
| `tools/` | Local PowerShell helpers (`Install-TrackMod.ps1` later) — placeholder |
| `docs/` | Plan and research |
| `.local/` | Scratch, research, caches (gitignored) |
| `Start-/Stop-WreckfestServer.ps1`, `rotation.txt` | Existing server scripts |

## Schema and generated code

JSON Schema in `schema/` is the single source of truth. C#, TypeScript and Python types are generated
from it; **generated types must not be hand-edited** — change the schema and regenerate. Option rules
are data (`schema/track-rules.json`), evaluated by small engines in each language
(`geom/src/trackgen_geom/rules.py`, `src/web/src/rules/`, `src/Api/Rules/`) and covered by shared test
vectors (`schema/test-vectors/`) run in all three test suites. Details: `schema/README.md`.

```powershell
py schema/codegen/generate.py          # regenerate src/web/src/generated, src/Api/Generated, geom/.../generated
py schema/codegen/generate.py --check  # CI fails if generated files are stale
```

## Build and test

```powershell
# .NET API
dotnet build TrackGen.slnx
dotnet test TrackGen.slnx

# Web
cd src\web; npm ci; npm run build; npx vitest run; npm run lint

# Geometry (venv under geom\.venv)
cd geom
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[service,test]"
.\.venv\Scripts\python -m pytest

# Generated code (stdlib-only; run after any schema change)
py schema\codegen\generate.py

# Local stack (api, geom, azurite); worker only with --profile worker
docker compose config
docker compose up --build
```

CI (`.github/workflows/ci.yml`) runs the same steps on Ubuntu.

## Commits

Small, logical commits. Every commit message ends with these trailers:

```
Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>
Copilot-Session: <session id>
```

The GitHub remote `GreyManSoftware/WreckFest-TrackGen` is **private** and must stay private.
