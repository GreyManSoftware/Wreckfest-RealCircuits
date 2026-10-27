# AGENTS.md — Wreckfest Real Circuits

Conventions for humans and coding agents working in this repo. Real-circuit pipeline and data:
`docs/real-tracks.md`; track pack and Workshop: `docs/workshop-publishing.md`.

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

## Layout

| Path | What |
|---|---|
| `schema/` | `track-spec.schema.json` + `track-rules.json` — **single source of truth** for the track spec and option rules; `codegen/`, `test-vectors/` |
| `geom/` | Python package `trackgen_geom` (geometry core) + FastAPI service `trackgen_geom.service` |
| `worker/` | Linux build worker (Blender 4.2 LTS + Toolbox Mz + Wine64): builder, pipeline, Dockerfile |
| `tools/` | Real-track importer and data (`real_tracks/`, `real_tracks_data/`), bundles, `wf_modlist`, Install/Build/Publish scripts |
| `docs/` | Real circuits and Workshop publishing |
| `.local/` | Scratch, research, caches (gitignored) |

## Schema and generated code

JSON Schema in `schema/` is the single source of truth. Python types are generated from it (and C# and
TypeScript ones for downstream apps, when their target folders `src/Api` and `src/web` exist);
**generated types must not be hand-edited** — change the schema and regenerate. Option rules are data
(`schema/track-rules.json`), evaluated by `geom/src/trackgen_geom/rules.py` and covered by the shared
test vectors (`schema/test-vectors/`), which other language engines can run too. Details: `schema/README.md`.

```powershell
py schema/codegen/generate.py          # regenerate geom/.../generated (and src/web, src/Api when present)
py schema/codegen/generate.py --check  # CI fails if generated files are stale
```

## Build and test

```powershell
# Geometry (venv under geom\.venv)
cd geom
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[service,test]" -e ..\worker\builder   # wfbuild for /scenery
.\.venv\Scripts\python -m pytest

# Generated code (stdlib-only; run after any schema change)
py schema\codegen\generate.py

# Builder tests (worker image, game install mounted read-only)
.\worker\builder\run-builder-tests.ps1 -Specs oval

# Local stack (geom service); worker only with --profile worker
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

The GitHub remote `GreyManSoftware/Wreckfest-RealCircuits` is **public**. Treat every commit as published: no secrets, personal data, LAN or tunnel URLs, game files, or third-party logos without permission. Run gitleaks (pinned Docker image) over new history before pushing.
