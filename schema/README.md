# schema

The **single source of truth** for the track spec and its option rules.

| File | What |
|---|---|
| `track-spec.schema.json` | JSON Schema (draft 2020-12) for the track spec, plus `GeometryFacts` (measurements from the geometry core). |
| `track-rules.json` | Declarative rules: mutual exclusions, dependencies, warnings and build flags. |
| `track-rules.schema.json` | Schema for `track-rules.json`, including the expression language. |
| `codegen/generate.py` | Generates C#, TypeScript and Python types and copies the two JSON files to each target. |
| `test-vectors/*.json` | Shared test vectors, run by pytest, vitest and xUnit. |

## Track spec

```
TrackSpec
├─ meta      name, slug (^[a-z][a-z0-9_]{2,23}$, the track/mod id), description?
├─ sketch    canvas coordinates (x right, y down)
│  ├─ canvas       { width, height }
│  ├─ mainLoop?    { points[≥3] }  (implicitly closed)
│  ├─ startFinish? { position, headingDeg }  (default: first point, heading to the second)
│  ├─ altRoutes[]  { id, points[≥2], start?, end? }; attachment = { point? , param? } (at least one)
│  ├─ crossings[]  { id, position, type: bridge|flat, over?: { route, param? } }
│  └─ jumps[]      { id, position, size: small|medium|large, route? = "main", bidirectional? = false }
└─ options   all required (the schema has the UI defaults)
   lapLengthKm 0.8–5 (1.5) · widthM 8–20 (12) · surface tarmac|gravel|mixed|sand (mixed) · kerbs (true)
   walls concrete|tyres|none (tyres) · elevation flat|gentle|hilly (gentle)
   environment gravel_pit|farmland|forest|industrial|sandpit (gravel_pit) · gridSize 8–24 (16)
   weathers ≥1 unique of sunrise|morning|midday|noon|afternoon|evening|sunset (["midday"])
   reverseLayout (false) · defaultLaps 1–20 (5)
```

Every property has a `title`/`description` for rendering the options panel. A route reference is `"main"` or an
alternative route's `id`.

**Weather ids are provisional.** Stock `.weat` files are per track and time of day (e.g.
`data/property/weather/fields/fields08_midday1.weat`), so the spec stores a time-of-day slot and each environment
preset will map slots to stock files. The slot list comes from the stock file names; confirm it per preset when the
builder is written.

`mainLoop`, `startFinish` and the alternative-route attachments are optional in the schema so that a sketch can be
saved while it is being drawn. The rules report what is missing.

### Geometry facts

`GeometryFacts` (also in `$defs`) holds measurements that only the geometry core (`trackgen_geom`) can compute:
`firstSectorLengthM`, `maxAbsCoordinateM`, `minCornerRadiusM`, `crossings[id].nearestJumpM`,
`crossings[id].availableRampLengthM`, and `altRoutes[id].startGapM` and `endGapM`.

## Rules

### Boundary

- **The rules engine** (one small evaluator per language) validates the spec against the schema. It then evaluates
  every rule in `track-rules.json` using only spec values, optional geometry facts and constants. It never computes
  geometry.
- **The geometry core** computes facts: lengths, radii, bounds and distances. It does not decide pass or fail.
  Every threshold lives in `track-rules.json` as a named constant.
- Rules with `"evaluatedBy": "geometry"` list the facts they need in `requires`. If a fact is missing (for example
  in the browser before the geometry service has responded), the rule is reported as **deferred**. It is not a pass.

### Rule format

```jsonc
{
  "id": "grid-first-sector-length",         // kebab-case, stable; used by tests and the UI
  "title": "First sector holds the grid",
  "description": "…",
  "evaluatedBy": "engine" | "geometry",
  "requires": ["facts.firstSectorLengthM"],  // optional: deferred if any path is null
  "forEach": { "path": "sketch.jumps", "as": "jump" },  // optional: binds $jump and $index
  "let": { "needM": <expr> },                // optional: evaluated in order, bound as $needM
  "when": <expr>,                             // optional (default true)
  "effect": <effect>
}
```

Effects:

| `type` | Fields | Result |
|---|---|---|
| `disable` / `hide` | `option`, `values[]`, `reason` | Adds a disabled (or hidden) option value with the reason. If the spec currently selects one of those values (or any of them, for multi-select options), it is also an **error** violation. Can't be combined with `forEach`. |
| `require` | `assert`, `severity` (`error`/`warning`), `message`, `path?` | A violation if `assert` is not `true`. |
| `error` / `warning` | `message`, `path?` | A violation whenever `when` holds. |
| `flag` | `flag`, `message` | A build flag for the geometry core or builder (e.g. `symmetricBridgeRamps`). |

The violation `path` is a JSON pointer: the effect's `path`, otherwise the `forEach` item (e.g. `/sketch/jumps/0`).
Messages are templates: `{options.gridSize}`, `{$needM}` and `{constants.x}` use `var` path syntax. Numbers are
printed as integers when they are whole, otherwise rounded to 2 decimal places. A missing value prints as `?`.

### Expressions

Expressions are JSON. Literals (`null`, booleans, numbers, strings) evaluate to themselves, and arrays evaluate each
element. An operator is an object with exactly one key:

| Operator | Meaning |
|---|---|
| `{"var": "a.b.0"}` | Path lookup. Roots: `meta`, `sketch`, `options`, `facts`, `constants`, or a `$binding`. Array indices are numeric segments. Missing gives `null`. |
| `{"get": [obj, key]}` | Dynamic key or index lookup (e.g. facts keyed by a crossing id). |
| `{"exists": x}` | `x` is not null. |
| `{"len": x}` | Length of an array, string or object; otherwise 0. |
| `==`, `!=` | Deep equality. Numbers compare by value, and a number is never equal to a boolean. |
| `<`, `<=`, `>`, `>=` | `false` unless both sides are numbers. |
| `{"in": [x, list]}` | `list` contains `x`. `false` if `list` is not an array. |
| `and`, `or` (lists), `not` | Only the boolean `true` is truthy. |
| `{"if": [c, a, b]}` | Conditional. |
| `+`, `*` (lists), `-`, `/` (pairs), `ceil`, `floor` | Arithmetic. A non-number operand, or division by zero, gives `null`. |
| `{"any"\|"all"\|"count": {"over": list, "as": "x", "where": pred}}` | Quantifiers. `$x` is bound inside `where`. A non-array is treated as empty. |

### Evaluation

1. Validate the spec, and the facts if given, against the schema. On any error, return `schemaValid: false` with
   the errors (as JSON pointers) and skip the rules.
2. For each rule, in file order: report it as deferred if a `requires` path is null. Otherwise evaluate each
   `forEach` item (or once), then `let`, then `when`, then apply the effect.
3. `valid` = schema valid and no `error` violations. Warnings, deferred rules and flags don't affect validity.

Result shape (field case differs per language): `schemaValid`, `valid`, `schemaErrors[{path, message}]`,
`violations[{ruleId, severity, message, path}]`, `disabled[{ruleId, option, values, hidden, reason}]`,
`flags[{ruleId, flag, message}]` and `deferred[ruleId]`.

### Rule ids

| Id | By | Effect |
|---|---|---|
| `main-loop-required` | engine | error when there is no main loop |
| `alt-route-requires-main-loop` | engine | error when there are alternative routes but no main loop |
| `alt-route-attachments-required` | engine | each alternative route needs a `start` and an `end` |
| `alt-route-joins-main-loop` | geometry | route ends within `attachmentToleranceM` (2 m) of the main loop |
| `jump-route-exists` | engine | a jump's `route` is `main` or an existing alternative route |
| `bridge-over-branch-required` | engine | a bridge says which branch goes `over` |
| `grid-width-two-abreast` | engine | `widthM ≥ 2 × gridSlotWidthM` (9 m) |
| `grid-first-sector-length` | geometry | `firstSectorLengthM ≥ ceil(gridSize / 2) × gridRowSpacingM` (8 m) |
| `reverse-jumps-bidirectional` | engine | warning: one-way jumps are left out of the reverse layout |
| `reverse-bridge-symmetric-ramps` | engine | flag `symmetricBridgeRamps` (reverse layout with a bridge) |
| `flat-crossing-jump-clearance` | geometry | flat crossing ≥ `flatCrossingJumpClearanceM` (30 m) from a jump on either branch |
| `bridge-ramp-length` | geometry | bridge has ≥ `bridgeRampMinLengthM` (50 m) for its ramps |
| `layout-within-bounds` | geometry | `maxAbsCoordinateM ≤ 2000` |
| `min-corner-radius` | geometry | `minCornerRadiusM ≥ widthM / 2 + minInnerEdgeRadiusM` (6 m) |
| `sandpit-no-tarmac` | engine | disables `surface=tarmac` in the sandpit (`mixed` is still offered) |
| `sand-surface-environments` | engine | disables `surface=sand` in farmland, forest and industrial (provisional) |
| `sand-no-kerbs` | engine | hides `kerbs=true` on sand |

Flat elevation with jumps is deliberately **not** restricted, because jumps are local ramps. The
`flat-elevation-with-jumps` vector pins this down.

The constants are first guesses and are expected to be tuned once real tracks are driven.

## Codegen

```powershell
py schema/codegen/generate.py          # regenerate
py schema/codegen/generate.py --check  # exit 1 if anything is stale
```

A stdlib-only Python emitter. It avoids three different generator toolchains and keeps the output deterministic.

| Target | Output |
|---|---|
| TypeScript | `src/web/src/generated/trackSpec.ts`: interfaces, string-union enums, `<enum>Values` arrays and `optionsDefaults` |
| C# | `src/Api/Generated/TrackSpec.g.cs`: `TrackGen.Api.Generated` records with `required` members, and enums using `JsonStringEnumMemberName` and `Options.Defaults` |
| Python | `geom/src/trackgen_geom/generated/track_spec.py`: `kw_only` dataclasses with `from_dict`/`to_dict`, `StrEnum`s and `Options.defaults()` |

`track-spec.schema.json` and `track-rules.json` are copied into each output directory. They are imported by the web
bundle, embedded in the API assembly, and shipped as package data. The emitter supports the subset of the schema
that is used: named `$defs` objects, string enums and string aliases, primitives, arrays and `additionalProperties`
maps. Add new object or enum types as `$defs` entries. Generated files start with a DO NOT EDIT header and are
committed. CI (`codegen` job) regenerates them and fails on any diff.

## Evaluators

| Language | Code | Schema validator |
|---|---|---|
| Python | `geom/src/trackgen_geom/rules.py`, `evaluate(spec, facts=None)` | `jsonschema` |
| TypeScript | `src/web/src/rules/`, `evaluate(spec, facts?)` | `ajv` (2020) |
| C# | `src/Api/Rules/RulesEngine.cs`, `RulesEngine.Evaluate(spec, facts)` | JsonSchema.Net 7.4 (pinned: the last MIT-only release; 8+ ship with an extra maintenance-fee EULA) |

Each evaluator accepts raw JSON or the generated types.

## Test vectors

Each file in `test-vectors/` has the form `{ description, spec, facts?, expect }`. `expect` contains `schemaValid`,
`valid`, and sorted arrays: `errors` and `warnings` (rule ids), `disabled` and `hidden` (`option=value`), `flags`
and `deferred` (rule ids). It can also contain `schemaErrorPaths`: JSON pointers, each of which must be among the
reported schema errors. All three suites assert the same summary, check that every rule id is triggered by at least
one vector, and round-trip the schema-valid specs through the generated types.
