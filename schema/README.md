# schema

`track-spec.schema.json` (JSON Schema draft 2020-12) is the **single source of truth** for the track spec and its option rules.

## Planned codegen (phase 3)

| Target | Output | Consumer |
|---|---|---|
| C# | `src/Api/Generated/` | ASP.NET Core API (validation, persistence) |
| TypeScript | `src/web/src/generated/` | React frontend (options panel rendered from the schema) |
| Python | `geom/src/trackgen_geom/generated/` | Geometry service and Blender worker |

- Generated files carry a "do not edit" header and **must not be hand-edited**; change the schema and regenerate.
- Declarative option rules (e.g. grid size vs. first-sector length, reverse layout vs. jumps, flat crossings vs. ramps) will live alongside the schema and be enforced in all three languages.
- Shared test vectors (`schema/vectors/*.json`, valid and invalid specs) will be run by the C#, TS and Python test suites so the implementations cannot drift.

The current file is a placeholder containing only `meta.name`, `meta.slug` and `meta.description`.
