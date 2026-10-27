# Circuito de Navarra (`navarra`)

Main Track, Medium and Short. Build, sources and layouts: `docs/real-tracks.md`, "Circuito de Navarra". Recipe
`tools/real_tracks/navarra.json`, sample `geom/tests/samples/navarra.json`, raw data and report in
`.local\realtracks\navarra\`.

## Lap length against AiM (2026-10-26, issue 20)

AiM's "Los Arcos" track (3,850 m) is the pre-2024 lap. Its official length was 3.933 km until the 2024 extension,
and our Main Track (4,287 m against MSV's 4,313 m) is the 2024 lap. From the T10-T11 approach AiM cuts straight
across to the old T11, where the extension (Turns 11-14) now runs, then takes the old road to Turn 15. The 2024
LIDAR shows the extension under construction and the old arc still there. Nothing in our lap is wrong. Details:
`docs/real-tracks.md`, "AiM outlines".

## Audit 2026-10-24

Plots (curvature, height, 10/50 m grade, vertical curvature, camber, straight ripple and wobble against distance,
with the turn labels; red is the build before the audit): `.local\audit\navarra\navarra-{maintrack,medium,short}.png`
(`python tools/audit_real_track.py`). Orthophoto and hillshade checks: `.local\audit\navarra\ortho-*.png`,
`shade-*.png`. Renders: `.local\renders\audit\navarra-{before,after}-*.png`.

| Issue | Evidence | Fix |
|---|---|---|
| **Wavy straights (Chris)**: vertical, not lateral | The IDEE WCS serves the IGN MDT05 as whole metres (its GeoTIFF is 16-bit integers; 37 % of the cells we cached are exact integers, against 1 % for a LIDAR DTM in cm). Gentle slopes became 1 m terraces 40-50 m apart: on the T8-T9 back straight the road sat on plateaus at 15.000, 14.000, 13.000 m … with 1 m risers over ~15 m, so even after the 10 m smoothing the grade swung 0.3 ↔ 3.4 % every ~55 m (grade ripple RMS on straights 0.53 %). The lines themselves are straight (lateral wobble on straights ≤ 0.15 m; the OSM line is used as mapped). | Heights from the Gobierno de Navarra's own DTM: 2 m, from the 2024 LIDAR flight (5 points/m², centimetre heights, IDENA WCS, CC BY 4.0; `"dtm": "es-navarra"`). Ripple RMS 0.11 %; the back straight is one steady −1.7 % descent. |
| The "3 m hump on the back straight" and the 9-13 % grades | Not on the T8-T9 straight: the steepest 10 m grades (+9.3 / −13.5 %) and a 98 m-radius crest were between T12 and T13, on the 2024 extension, which the MDT05's older flight shows as the ground before it was built (up to 2.4 m off the 2024 DTM). | 2024 DTM: +6.8 / −9.6 % (McLeans-like climb out of T5, the drop from T11 to T12), tightest crest 318 m. The hillshade of the 2024 DTM shows the extension's road where OSM maps it. |
| False 4° camber on the main straight, 7.4° "banking" between T12 and T13 | The terraces beside the road tilted the cross-road plane; the extension's slope was the hillside before construction. | 2024 DTM: the main straight 1.1-1.3° (≈ 2 % crossfall), camber ≤ 3.0° anywhere (p95 1.4°), into the corner; T11 banked 2.4° into the left hairpin. |
| Fit to the ground | centre RMS 0.13 m (max 0.7 m) against the MDT05 | 0.019 m (inner edges 0.028 m) against the 2 m DTM; a plane across the inner 80 % of the road fits within 7 mm on average. |
| Registration | With 3-6 m allowed on the 2 m DTM the line moves at most 2 m (Medium's link up to 3.9 m) and gains 0.1 m of wobble for 7 mm better fit. | Kept as mapped (`registrationMaxShiftM` 0, note updated). The PNOA orthophoto puts the OSM line in the middle of the asphalt on the main, back and T15-T16 straights. |
| Width | Published at the 2010 opening (autofacil.es, motorpasion.com): 12 m, 15 m on the main straight; the orthophoto shows ~13.5 m between the edge lines on the back straight. | 12 m kept (one width per track); the main straight is 3 m narrower than real. `widthNote` updated. |
| Short's hook ramp | The hook climbs 1.6 m in ~15 m onto the T7-T8 road (13 % over 10 m, a 93 m dip) in the 2024 DTM; the builder's check that the AI racing strip lies on the road failed by 7 mm there (strip sectors can't follow so tight a dip). | Eased: crests and dips tighter than 100 m get a local 6 m smoothing (`ease_vertical`, all imports); the hook is now 9.6 % at most, dip 161 m (0.4 m off the DTM at worst). Also new: a branch's ends take the main road's height and camber, faded over ≥ 30 m (`join_blend`). |
| Short's hook junction | The hook met the T7-T8 road at 47° within 2 m (a kink, after `joinWithinM` trimmed the merge), and its own fitted surface ran up to 16 cm off the main road's where they overlap; check_scene's AI-strip-on-road check failed (5.7 cm). | `fillet_ends` (curved onto the main road over 30 m) and `merge_onto_main` (the main road's surface where they overlap): check passes (0.9 cm). |
| Lap lengths | Main 4,280 m (−0.8 % vs 4,313), Medium 2,637 m (−2.1 % vs 2,693), Short 1,349 m (−3.0 % vs 1,391). OSM tags the lap "approximate drawing"; the 2010 lap was 3,933 m and its Short 1,332 m. | Unchanged (the DTM doesn't change the plan). Short keeps its 3.5 % `lapLengthNote`. |
| Start/finish | Main: MSV map, opposite the middle of the pit building; Short: racingcircuits.info's flag, placed where the grid stays under 4 %. No better source found. | Unchanged. |
| Environment | No OSM woods in the box; the orthophoto shows bare dry ground, scrub and the reservoir south-east of T16 (outside the road, not modelled as a lake). | `arid` unchanged. |

Remaining uncertainty: OSM's lap is an approximate drawing; the 2 m DTM can't show its few-metre errors on flat
asphalt (the orthophoto spot checks agree with it), the hook's ramp (in the DTM, maybe still under construction in
the 2024 flight) and the T5-T6 curvature ripples (OSM nodes; no orthophoto check there).

## Width (measured 2026-10-24)

Source: Gobierno de Navarra LiDAR 2024 point cloud with RGBI, tiles 568-4712 and 568-4713 (~7 points/m², CC BY 4.0). Method and data: `docs/real-tracks.md`, "Widths (2026-10-24)". Profile `tools/real_tracks/widths/navarra.json`; plots and overlays `.local\audit\navarra\width-*.png`.

| | Value |
|---|---|
| Main Track | median 12.0 m, 9.2-15.4 m (p10 11.3, p90 14.5); 74 % confident |
| Main straight (Turn 17 to Turn 3) | 14.1-15.4 m (Turn 17 14.7, Turn 1 14.9, Turn 2 14.6, Turn 3 14.0) |
| Rest of the lap | 11.0-12.4 m (Turns 5-10, 15, 16) |
| Narrow | Turn 4 10.4-11.4 m (a paler asphalt there; narrowest 9.2 m just after it) |
| 2024 extension (Turns 11-14) | unpaved or half-paved in the 2024 flight: not measured, 12.0 m (`UNMEASURED`) |
| Medium / Short links | 9.3-12.1 m / 11.7-12.0 m |
| Line | moved by up to 1.8 m (mean 0.6 m); lap 4,287.4 m (published 4,313 m, −0.6 %) |
| Spec | `options.widthM` 12.0 |

**Published:** 12 m, 15 m on the main straight (2010 opening). Measured: 12.0 m median, 14-15 m on the main straight:
both agree.

## Run-off, barriers and trees (2026-10-24)

Measured from open data (method: `docs/real-tracks.md`, "Run-off, barriers and foliage"): Gobierno de Navarra LiDAR 2024 (RGBI colour, classes, heights);
OSM run-off, barrier and woodland features; barrier types from `tools/real_tracks_data/runoff_evidence.json`.
Data `tools/real_tracks/runoff/navarra.json`, `tools/real_tracks/vegetation/navarra.json`; overlays
`.local\audit\navarra\runoff-*.png`, `vegetation-overview.png`.

| | Before (builder until 2026-10-24) | Now (measured) |
|---|---|---|
| Run-off | grass everywhere (no traps or aprons); 612 stock barrier props (concrete: grey concrete blocks) on 28 % / 17 % of the main lap's left / right sides | gravel 12 %, tarmac 21 %, grass 67 % of the main lap's sides; barriers armco 84 %, none 15 %, bank 1 %, tyres 1 %; debris fence on 7 %; built barrier line on 95 % / 81 % (left / right) |
| Trees | 542 trees: OSM woods plus seeded park trees and an edge belt | 2 measured woods (0.2 ha in the 450 m box), 458 single trees, canopy 0.6 % of the box; 12 trees placed (458 candidates inside the terrain, cap 400) |
| Look | grey concrete barrier blocks (barrier8) at fast corners and crowds | Armco round almost the whole lap (OSM maps 63 guard-rail ways), tarmac run-off at the big corners (pale older asphalt in the 2024 colour), gravel beds from OSM's gravel-trap polygons, debris fences at Turns 6, 11-13 and 15 (Geobrugg project notes) |

Evidence per corner (main lap; the zone that holds the corner label; offsets from the road edge):

| Corner (sM) | Side | Run-off | Barrier | Source |
|---|---|---|---|---|
| Turn 1 (563) | left | gravel trap 11-20 m | armco at 22 m | OSM, LIDAR, circuit default |
| Turn 1 (563) | right | grass | armco at 12 m | OSM, LIDAR, circuit default |
| Turn 2 (750) | left | grass | none | LIDAR |
| Turn 2 (750) | right | grass | armco at 31 m | OSM |
| Turn 3 (854) | left | gravel trap 16-32 m | armco at 32 m | OSM, LIDAR |
| Turn 3 (854) | right | tarmac 21 m | none | LIDAR |
| Turn 4 (1034) | left | grass | armco at 22 m | OSM, LIDAR, circuit default |
| Turn 4 (1034) | right | gravel trap 6-14 m | armco at 17 m | OSM, LIDAR |
| Turn 5 (1355) | left | grass | armco at 5 m | OSM, LIDAR, circuit default |
| Turn 5 (1355) | right | grass | armco at 18 m | OSM, LIDAR |
| Turn 6 (1559) | left | grass | armco at 7 m | OSM, LIDAR, circuit default |
| Turn 6 (1559) | right | gravel trap 15-60 m | none | LIDAR, guide: circuitodenavarra.com 2024 works |
| Turn 7 (1702) | left | gravel trap 7-22 m | armco at 28 m | OSM, LIDAR, circuit default |
| Turn 7 (1702) | right | grass | none | LIDAR |
| Turn 8 (1886) | left | grass | armco at 39 m | OSM, LIDAR |
| Turn 8 (1886) | right | grass | armco at 50 m | OSM, LIDAR, circuit default |
| Turn 9 (2433) | left | grass | armco at 6 m | OSM, LIDAR, circuit default |
| Turn 9 (2433) | right | grass | armco at 43 m | OSM |
| Turn 10 (2592) | left | gravel trap 10-22 m | armco at 23 m | OSM, LIDAR, circuit default |
| Turn 10 (2592) | right | grass | armco at 34 m | OSM |
| Turn 11 (2964) | left | grass | armco at 16 m | LIDAR, circuit default |
| Turn 11 (2964) | right | tarmac 21 m | armco at 22 m + debris fence | LIDAR, guide: geobrugg.com/project_ja,,145638.html |
| Turn 12 (3085) | left | tarmac 7 m | armco at 7 m | LIDAR, circuit default |
| Turn 12 (3085) | right | tarmac 4 m | armco at 4 m + debris fence | OSM, LIDAR, guide: geobrugg.com/project_ja,,145638.html |
| Turn 13 (3207) | left | tarmac 3 m | armco at 3 m | LIDAR, circuit default |
| Turn 13 (3207) | right | grass | armco at 31 m + debris fence | LIDAR, guide: geobrugg.com/project_ja,,145638.html |
| Turn 14 (3287) | left | grass | armco at 24 m | OSM, LIDAR, circuit default |
| Turn 14 (3287) | right | grass | armco at 35 m + debris fence | OSM, LIDAR, guide: geobrugg.com/project_ja,,145638.html |
| Turn 15 (3546) | left | grass | armco at 10 m + debris fence | OSM, LIDAR, guide: geobrugg.com project 145638 |
| Turn 15 (3546) | right | grass | none | OSM, LIDAR |
| Turn 16 (3814) | left | grass | armco at 22 m | OSM, LIDAR, circuit default |
| Turn 16 (3814) | right | grass | armco at 18 m | OSM, LIDAR, circuit default |
| Turn 17 (3997) | left | grass | armco at 49 m | OSM, LIDAR |
| Turn 17 (3997) | right | grass | none | LIDAR, circuit default |

Uncertain: tyre walls in front of the Armco at corners (Geobrugg: 'guardrail plus tyre barrier') aren't located in any source, so the Armco is shown bare; the T1 run-off reads as tarmac and gravel.
