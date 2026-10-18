# geom

`trackgen_geom`: the pure-Python geometry core. It turns a track spec's sketch into the authoritative
**TrackModel**, computes the **geometry facts** the rules engine needs, and draws SVG previews. It is shared by the
internal FastAPI geometry service and the Blender worker, and runs without Blender (Python ≥ 3.11, `numpy` and
`jsonschema` only; Blender 4.2's bundled Python 3.11 and numpy are enough).

```powershell
py -m venv .venv
$env:PIP_CACHE_DIR = 'E:\Source\wreckfest\.local\pip-cache'
.\.venv\Scripts\python -m pip install -e ".[service,test]"
.\.venv\Scripts\python -m pytest            # writes previews to tests\output\ (gitignored)
.\.venv\Scripts\python -m uvicorn trackgen_geom.service:app --port 8000   # see "Service" below
```

Python types for the track spec are generated from `../schema/` into `trackgen_geom/generated/` (never hand-edit).

## API

```python
from trackgen_geom import build_track_model

result = build_track_model(spec)       # spec: dict or generated TrackSpec
result.model                           # TrackModel | None (None if schema-invalid, no main loop or rejected)
result.model.to_json()                 # deterministic JSON (floats rounded to 4 dp)
result.facts                           # GeometryFacts dict (schema $defs/GeometryFacts)
result.option_result                   # rules without facts: option-level rules, geometry rules deferred
result.rule_result                     # rules with the facts: the authoritative verdict
result.preview_svg, result.elevation_svg
result.warnings                        # [GeometryIssue(code, message)]
result.errors                          # [GeometryIssue] why the sketch was rejected (model is None)
result.valid                           # model built, no errors and no rule errors
```

Steps: reject oversized sketch arrays (schema `maxItems`, checked before the slow full validation) → validate the spec and evaluate the option-level rules → build the geometry → compute facts →
evaluate all rules with the facts. The same spec always gives byte-identical model JSON and SVGs.

**Rejections** (`result.errors`, cheap checks before any smoothing):

| Code | When |
|---|---|
| `main-loop-too-small` | the main loop is shorter than 1% of the larger canvas side (e.g. a stray click) |
| `main-loop-degenerate` | fewer than three distinct points |
| `alt-route-too-long` | an alternative-route stroke, scaled like the main loop, is longer than 3× the lap |
| `alt-route-out-of-bounds` | an alternative-route stroke reaches beyond `maxAbsCoordinateM` from the main loop's centre |

Resampling is also capped at 100,000 points per call, as a backstop.

| Module | What |
|---|---|
| `pipeline.py` | `build_track_model`, `Result`, the build steps |
| `centreline.py` | stroke → centreline (smoothing, B-spline, radius relaxation, scaling, orientation) |
| `polyline.py` | numpy polyline toolkit: resampling, smoothing, subdivision, curvature, projection, intersections |
| `elevation.py` | seeded terrain noise, bridge ramps, jump profiles |
| `model.py` | `TrackModel` dataclasses and JSON serialisation |
| `preview.py` | top-down and elevation SVGs |
| `constants.py` | modelling constants; rule thresholds are read from `track-rules.json` (`rule_constants()`) |

## Algorithms

- **Centreline.** Canvas points are flipped to y-up and pre-scaled to the lap length. A Gaussian low-pass
  (σ 15 m) removes hand-drawn jitter; the result is resampled into control points every 25 m and refined by
  4 rounds of cubic B-spline (Lane-Riesenfeld) subdivision, then resampled every ~2 m and scaled so the lap
  length is exact. The loop is reversed if needed to match `startFinish.headingDeg`, and re-parameterised so
  sample 0 (s = 0) is the start/finish (default: first point, heading to the second).
- **Minimum radius.** The radius at each sample is the circumradius of samples i−3, i, i+3. Control points
  near samples tighter than `widthM / 2 + minInnerEdgeRadiusM` (+3%) get Laplacian relaxation, repeated until
  none are left. If the next step would move any control point further than max(8 m, 1.5% of the length), it
  stops, and the measured minimum is reported as `minCornerRadiusM` (warning `corner-radius-unresolved`).
- **Bounds.** The layout is centred on the bounding-box centre of all centrelines. `maxAbsCoordinateM` is the
  largest |x| or |y| of any road edge **plus `TERRAIN_MARGIN_M` (60 m)** of terrain/run-off.
- **Alternative routes.** Attachments resolve to a main-loop arc length (`param` wins over `point`; a missing
  attachment snaps the stroke end). Those points are inserted into the main loop as vertices (or an existing
  vertex within 5 cm is reused), and the alt route starts and ends on exactly those coordinates. Its first and
  last spans lean towards the main loop's direction. `startGapM`/`endGapM` are the distances from the raw stroke
  ends to the snapped points.
- **Crossings.** Segment intersections (spatial hash) within and between routes; hits within 25 m of an alt
  route's ends are the junction itself, and hits closer than 15 m are merged. Annotations are matched greedily
  by distance (≤ 40 m). Unmatched intersections become `unannotated` crossings, reported in the
  `unannotatedCrossings` fact (canvas coordinates; rule `crossing-annotation-required` is an error). Annotations
  that match nothing are listed in `unmatchedCrossingAnnotations` (warning rule `crossing-annotation-matches`),
  get no per-crossing facts and aren't built. `nearestJumpM` is left out when neither branch has a jump or bridge
  ramp. For a bridge,
  `over.route` picks the branch and `over.param` disambiguates two passes of the same route.
- **Elevation.** Ground height is a seeded 2D field (6 plane waves, seed = SHA-256 of the slug) so equal (x, y)
  always gives equal height. `flat` is 0; `gentle` ±3 m, ≤ 4% grade; `hilly` ±10 m, ≤ 9% grade (the field is
  scaled down globally if any route would be steeper). Bridges add 6.5 m on the over branch over a deck that
  clears the under road, with ramps of max(`bridgeRampMinLengthM`, length for an 18% peak grade); the ramps
  stop at other crossings, jumps, junctions and the grid. With the `symmetricBridgeRamps` flag both ramps get the
  shorter length. Alt-route heights blend into the main loop's over 20 m and are equal at the joins.
- **Jumps.** Small/medium/large: 1.0/1.8/2.8 m high, 8/12/16 m takeoff. One-way: concave takeoff to a 2 m lip,
  then a smooth landing 2.5× the takeoff. Bidirectional: two takeoffs either side of a 6 m table (symmetric).
- **Grid and sectors.** The grid sits **ahead of** the start/finish line, inside the first sector (to verify in
  game). `firstSectorLengthM` is the clear road after the line until a corner tighter than 50 m, a turn of 45°
  from the start heading, a crossing, jump, bridge ramp or junction, so `grid-first-sector-length` guarantees the
  slots sit on clear road (never on a jump ramp). The back row is 4 m past the line, then every
  `gridRowSpacingM` towards the pole row; pairs are `gridSlotWidthM` apart and the pole is on the inside of the
  first sector's turn. Bridge ramps keep clear of the grid. Main checkpoints: 6–10 (every ~250 m); the first is
  10 m past the pole row, the last is the start/finish line; alt routes get evenly spaced ones too.

## TrackModel (format version 1)

Coordinates are **metres, right-handed, Z up** (Blender's convention): **x = east (canvas right), y = north
(canvas up), z = up**. Headings are **radians counter-clockwise from +x about +z** (a Blender object with
`rotation_euler.z = heading` faces along the road). Canvas → world: `x = scale·cx + offsetX`,
`y = −scale·cy + offsetY` (`canvasTransform`). Keys are camelCase; floats are rounded to 4 decimals.

| Field | Content |
|---|---|
| `formatVersion`, `generator`, `slug`, `name`, `coordinateSystem` | identity and conventions |
| `reverse`, `lapLengthM`, `widthM`, `elevation`, `firstSectorLengthM` | layout summary |
| `canvasTransform` | `{scale, offsetX, offsetY}` |
| `bounds` | `minX…maxZ` of the road edges, `maxAbsRoadM`, `terrainMarginM`, `maxAbsCoordinateM`, `limitM` (2000) |
| `routes[]` | `{id, aiIndex (0 main, −1, −2… alts), closed, lengthM, widthM, samples[], leftEdge[], rightEdge[], startJoin?, endJoin?}` |
| `routes[].samples[]` | `{x, y, z, s, heading, banking (0), width}` every ~2 m; the main loop is closed (no repeated point), s = 0 at the start/finish |
| `routes[].leftEdge/rightEdge` | `[x, y, z]` per sample, ±width/2 along the left normal (left = driver's left) |
| `startJoin/endJoin` | `{route: "main", sampleIndex, s}`: the main-loop vertex the alt route shares exactly |
| `crossings[]` | `{id, annotated, detected, type (bridge/flat/unannotated), position [x, y], angleDeg, branches[{route, s, param, heading, z}], overBranch, deckHalfLengthM, rampUpM, rampDownM, deckHeightM}` |
| `jumps[]` | `{id, route, size, bidirectional, s, position, heading, heightM, startS, endS, layouts, inBase, profile[[offset, dz]]}` |
| `grid` | `{size, rowSpacingM, slotWidthM, slots[{name "#startpoint01", index, row, side, s, position, heading}], carChange{name "#startpointcarchange", position, heading}}` |
| `checkpoints[]` | `{route, index, s, position, heading, widthM (road + 2×2 m), isFinish}` |
| `aiLines[]` | `{route, aiIndex, closed, points[[x, y, z]]}`: the racing line (the centreline in v1) |
| `warnings[]` | `"code: message"` strings |

Sample `z` is the final road surface: terrain + bridge decks/ramps + the jumps with `inBase: true`. In a
reverse layout a one-directional jump has `inBase: false` and `layouts: ["forward"]`: it is left out of the
shared road surface (as the `reverse-jumps-bidirectional` rule says) and its `profile` lets the builder add it
as a forward-only object. The reverse layout itself is the same geometry driven backwards (`reverse: true` →
`otherway = true` on the main AI route).

## Tests

`tests/test_geometry.py` (low-level maths) and `tests/test_pipeline.py` (samples in `tests/samples/`: oval,
figure-8 with a bridge, loop with an alt route and jumps, hilly circuit; plus every schema-valid vector in
`schema/test-vectors/`, which must build with no geometry rule deferred). Previews and model JSON go to
`tests/output/`. `tests/test_service.py` covers the HTTP service and keeps the contract fixtures in
`tests/contract/` current.

## Service

`trackgen_geom.service:app` is the internal FastAPI service the API calls (`GEOM_URL`, `http://geom:8000` in
Compose). It runs `build_track_model` and reports measurements; pass or fail is the rules' job. The Blender worker
doesn't call it: it installs this package and calls `build_track_model` directly.

| Endpoint | Request | 200 response |
|---|---|---|
| `GET /healthz` | – | `{"status": "ok", "version"}` |
| `POST /validate` | `{"spec": TrackSpec}` | `{version, valid, facts, ruleResult, warnings, errors}` |
| `POST /preview` | `{"spec": TrackSpec}` | `/validate` plus `svg` (top-down), `elevationSvg` and `modelSummary` |
| `POST /model` | `{"spec": TrackSpec}` | `/validate` plus `model` (the full TrackModel). Internal; nothing calls it yet. |

- **`facts`:** `$defs/GeometryFacts`, always with `crossings`, `altRoutes`, `unannotatedCrossings` and
  `unmatchedCrossingAnnotations`, even when they are empty.
- **`ruleResult`:** `rule_result` with the facts, in the API's `EvaluationResult` shape: `schemaValid`,
  `schemaErrors[{path, message}]`, `violations[{ruleId, severity, message, path}]`,
  `disabled[{ruleId, option, values, hidden, reason}]`, `flags[{ruleId, flag, message}]`, `deferred[]`, plus `valid`.
- **`warnings` / `errors`:** `[{code, message}]`. `errors` is always empty in a 200; `valid` is `result.valid`.
- **`modelSummary`:** `{formatVersion, slug, lapLengthM, widthM, bounds, routes[{id, closed, lengthM}]}`.
- **Determinism:** the same request gives a byte-identical body.

Errors are `{"detail": {"code", "message", …}}`. The API maps any 4xx to its own 422 and anything else to 503.

| Status | `code` | When |
|---|---|---|
| 400 | `bad-request` | The body isn't JSON (`NaN`/`Infinity` included) or isn't `{"spec": …}`. |
| 413 | `body-too-large` | The body is over `GEOM_MAX_BODY_BYTES` (default 8 MiB, which fits every schema-valid spec). Also has `limit`. |
| 422 | `schema-invalid` | The spec fails the schema, or a sketch array is over its `maxItems`. Has `schemaErrors[{path, message}]`. |
| 422 | `geometry-rejected` | No geometry: a rejection in `errors[{code, message}]` (`main-loop-too-small`, …) or `main-loop-missing`. Also has `warnings`. |
| 500 | `internal-error` | Unexpected exception. The message is generic; the traceback is logged. |
| 503 | `compute-timeout` | The build took longer than `GEOM_COMPUTE_TIMEOUT_S` (default 15 s, below the API's 20 s client timeout). |

**Runtime:** a single uvicorn worker. Builds run in a thread pool of `GEOM_MAX_CONCURRENCY` (default 2) threads; a
request waiting for a free thread counts against its timeout. Python can't stop a thread, so **a timed-out build
keeps running** (and holds its thread) until it finishes; the sketch size limits keep that bounded. Logs are JSON
lines on stderr: one `request` entry per request (method, path, status, `durationMs`, `X-Request-Id`) and one
`build` entry per build (slug, valid, warning count, error codes, `computeMs`). Set the level with `GEOM_LOG_LEVEL`.

**Contract fixtures:** `tests/contract/validate-<case>.json` are real `/validate` responses for `figure8_bridge`,
`loop_alt_jump` and `rule_failures` (`rule_failures.spec.json`: warnings, a disabled option, a flag and rule
errors). `test_service.py` checks they still match the service (numbers within 0.01), and
`tests/Api.Tests/GeometryContractTests.cs` deserialises them with the API's types and checks the C# rules agree.
After a deliberate change to the response, regenerate them:

```powershell
$env:GEOM_UPDATE_CONTRACT = '1'; .\.venv\Scripts\python -m pytest tests\test_service.py -k contract; Remove-Item Env:GEOM_UPDATE_CONTRACT
```

**Docker:** `geom/Dockerfile` builds a slim, non-root image (`python:3.12-slim`, uid 10001) that runs
`uvicorn trackgen_geom.service:app` on port 8000. In Compose, `geom` has a 1-CPU / 512 MB limit, a healthcheck on
`/healthz`, and only a localhost port (`127.0.0.1:8000`, for `dotnet run`).
