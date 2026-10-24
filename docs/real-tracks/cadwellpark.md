# Cadwell Park: accuracy audit (2026-10-24)

The circuit's build notes (layouts, sources, line, grid, kerbs) are in `docs/real-tracks.md` ("Cadwell Park: Full,
Club and Woodlands"). This page is the pre-Workshop accuracy audit; method and the shared importer change in
`docs/real-tracks.md`, "Audit notes". Plots: `.local\audit\cadwellpark\{before,after}-{main,club,woodlands}.png`
(curvature with the OSM polyline's, built and raw-DTM heights, grade over 10/50 m, grade change over 10 m, camber),
plan views over the LIDAR relief `.local\audit\cadwellpark\plan-*.png`, before/after line comparisons
`.local\renders\audit\cadwellpark-line-*.png`.

## Audit 2026-10-24

| Issue | Evidence | Fix |
|---|---|---|
| Straights wandered sideways: the line stepped 0.5-1 m over ~20 m on Park Straight and the run from the Gooseneck to Mansfield | OSM-to-built offset on Park Straight jumped −2.5 → −1.5 → −1.0 → −2.0 → −3.0 m in 0.5 m steps; a 6 m-scale bend of R 114 m on the Gooseneck-Mansfield straight; plan view `plan-parkstraight.png` | Shared importer fix (registration shift in 0.25 m steps, smoothed over 25 m). Short-wave offset on straights 0.37-0.60 → 0.16-0.42 m; the tightest bend on a straight R 114 → 173 m (the start straight, which really curves); regression test added |
| Corner radii | Built minimum radii against circle fits to the OSM nodes: Park 35/35 m, Chris Curve 53/52, Gooseneck 47/49, Mansfield 26/32, Hairpin 11/12, Barn 25/21, Charlies 55/70 | None. The Mountain reads 18 m built vs 10 m on OSM, but OSM has a cluster of nodes 1-2 m apart there (node noise); the plan view shows the built line on the paved strip |
| Corner handedness and order | Coppice L (uphill, the camber falls to the inside), Charlies R (banked 7°), Park R, Chris Curve R, Gooseneck L-R, Mansfield L, Woodland L kink, the Mountain L-R flick, Hall Bends R-L, Hairpin R, Barn R; order as the guides | None |
| Elevation: the Mountain | Dip at s 2,562 m to the crest at 2,700 m: 12.0 m rise over 138 m (mean 8.7 %, 19.1 % over 20 m, 20.7 % over 10 m), grade drops 10.5 points over the crest (sharpest grade change on the lap 7.9 % per 10 m, a vertical radius of ~125 m). No published gradient; guides say bikes get airborne and cars go light, which fits | None. Raw DTM vs built at the crest: 8 cm (σ 4 m smoothing), elsewhere RMS 1.6 cm |
| LIDAR artefacts on the road | Raw 1 m DTM along the line: no bump over 12 cm against a 15 m running median anywhere; total climb 79 m per lap, range 45.9 m | None |
| Camber | Up to 7.4° (Charlies, into the corner), Coppice 6° falling to the inside of a left, p95 6.0°; on straights within 0.9° of the 25 m mean, no flip-flop; fastest change 0.25°/m (the Gooseneck) | None |
| Width | Not published, no OSM `width`. LIDAR flush-surface bounds 8.5-10.5 m on the narrow parts (Park Straight 8.5, Chris Curve 9.0, the Mountain 10.5 m: not narrower than the rest) | None: 9.5 m kept |
| Start/finish, direction, grid | Sources recorded in the recipe (MSV map flag ~155 m from Barn, OSM node 153 m; Club line after Mansfield per racingcircuits.info) | None |
| Environment | 10 OSM woods; 2 centreline samples fall inside the wood polygon that spans the Woodland section (the builder keeps trees off the road and run-off, `check_scene` roadside checks); crowds at the Mountain, Hall Bends and the Hairpin, the main spectator banks | None |

Lap lengths (built): Full 3,496.9 → 3,496.0 m (published 3,502 m, −0.17 %); Club 2,371.4 → 2,371.8 m (2,375 m,
−0.13 %); Woodlands 1,349.9 → 1,349.4 m (1,365 m, −1.14 %; the OSM polyline is 1,372 m, and the built lap loses ~20 m
where the hairpin branch is joined to the main line; the gap was the same before the audit).

Still uncertain: real widths (no source), how the Mountain crest drives (only qualitative sources).

## Width (measured 2026-10-24)

Source: EA National LIDAR Programme point cloud, tile TF2580 (flown 2019-03-29, ~1.8 points/m²). Method and data: `docs/real-tracks.md`, "Widths (2026-10-24)". Profile `tools/real_tracks/widths/cadwellpark.json`; plots and overlays `.local\audit\cadwellpark\width-*.png`.

| | Value |
|---|---|
| Full lap | median 7.8 m, 7.0-10.1 m (p10 7.2, p90 9.3); 86 % of sections measured with confidence ≥ 0.5 |
| Narrow | Park Straight 7.1-7.3 m, Chris Curve 7.2-7.5 m, the Mountain 7.5-8.5 m (median 7.8), the exit of Hall Bends 7.0 m, Coppice and Charlies ~7.6 m |
| Wide | the run from Mansfield through Woodland 9.5-10.1 m, the start/finish straight 8.6-9.5 m, Hall Bends 8.0-9.1 m, Barn 8.7 m |
| Club / Woodlands hairpins | 7.8-8.1 m (junction zones throughout: the circuit's median) |
| Line | moved onto the tarmac's middle by up to 1.5 m (mean 0.6 m); lap 3,486.8 m (was 3,496.0; published 3,502 m, −0.4 %) |
| Spec | `options.widthM` 8.0 (the median 7.8, clamped to the schema's 8 m minimum) |

Not published (sources only call it "narrow", "about half Silverstone's width"); the recipe's 9.5 m was an estimate,
1.7 m wider than the tarmac. The pit lane beside the start straight is separated only by paint: the line rule keeps it
out. The 7 m clamp holds at Park Straight and Hall Bends (raw 6.5-7.0 m in places).

## Start straight, Coppice and the Mountain divider (2026-10-24)

Chris drove the Full layout: the road snaked left-right-left leaving the start straight towards Coppice, there was
no divider between the start straight and the run to the Mountain, and the ground showed holes where the divider
had been. Causes and fixes (shared code, `docs/real-tracks.md`, "Straights, junction ground and dividers"):

- **Snake:** OSM's nodes snake there (R 130-150 m between nodes 30 m apart) and the measured width had no confident
  edges beside the Club and Woodlands links (confidence 0-0.3), so its tarmac middle followed the old line. The
  importer's new `straighten` takes it out: s 150-260 bent at R 205-240 m alternating, now no tighter than R 600 m;
  the line moves by up to 0.6 m. Plan `.local\audit\cadwellpark\coppice-after.png` (red before, green after, blue OSM
  over the LIDAR hillshade). Lap 3,486.5 m (was 3,486.8). The width there is still the measurement's fill (8.0-8.5 m).
- **Divider:** open only at the two links themselves now (it was open 40 m either side of each, i.e. the whole
  stretch). The wall runs mid-gap (4.8 m out) along the start straight between the links.
- **Holes:** the ground between the two sections took the lower road's height up to the higher one's edge (a trench
  up to 1.06 m deep along the start straight); it now slopes across the gap (`ground_dips` 73 edge samples → 0).
- Renders: `.local\renders\cadwellpark-divider-{before,after}-driver-{startstraight,mountain}.png`.

## Run-off, barriers and trees (2026-10-24)

Measured from open data (method: `docs/real-tracks.md`, "Run-off, barriers and foliage"): EA National LIDAR Programme point cloud (intensity, classes, heights);
OSM run-off, barrier and woodland features; barrier types from `tools/real_tracks_data/runoff_evidence.json`.
Data `tools/real_tracks/runoff/cadwellpark.json`, `tools/real_tracks/vegetation/cadwellpark.json`; overlays
`.local\audit\cadwellpark\runoff-*.png`, `vegetation-overview.png`.

| | Before (builder until 2026-10-24) | Now (measured) |
|---|---|---|
| Run-off | grass everywhere (no traps or aprons); 381 stock barrier props (tyres: red/white tyre heaps prop_tirebarrier02/04) on 29 % / 23 % of the main lap's left / right sides | gravel 3 %, tarmac 0 %, grass 97 % of the main lap's sides; barriers armco 56 %, none 36 %, bank 8 %; debris fence on 16 %; built barrier line on 83 % / 41 % (left / right) |
| Trees | 1542 trees: OSM woods plus seeded park trees and an edge belt | 29 measured woods (5.0 ha in the 450 m box), 1844 single trees, canopy 3.9 % of the box; 747 trees placed (2217 candidates inside the terrain, cap 2600) |
| Look | grass verges, red/white tyre heaps on a third of each side; OSM woods plus seeded park trees | narrow grass run-off with Armco close on most of the lap (no traps but a few short gravel patches), earth banks where the LIDAR shows the ground rising first, open sides through the farmland; measured trees and woods only |

Evidence per corner (main lap; the zone that holds the corner label; offsets from the road edge):

| Corner (sM) | Side | Run-off | Barrier | Source |
|---|---|---|---|---|
| Start/finish straight (70) | right | grass | armco at 11 m + debris fence | LIDAR, circuit default |
| Coppice (387) | left | grass | armco at 5 m + debris fence | LIDAR, circuit default |
| Coppice (387) | right | grass | bank at 2 m | LIDAR |
| Charlies (646) | left | grass | armco at 24 m | LIDAR, circuit default |
| Charlies (646) | right | grass | none | LIDAR, circuit default |
| Park Straight (1097) | left | grass | armco at 18 m | LIDAR, circuit default |
| Park Straight (1097) | right | grass | armco at 4 m + debris fence | LIDAR, circuit default |
| Park (1444) | left | grass | armco at 29 m | LIDAR, circuit default |
| Park (1444) | right | grass | none | LIDAR |
| Chris Curve (1636) | left | grass | armco at 40 m | LIDAR, circuit default |
| Chris Curve (1636) | right | grass | none | LIDAR |
| The Gooseneck (1911) | left | grass | none | LIDAR, circuit default |
| The Gooseneck (1911) | right | grass | none | LIDAR |
| Mansfield (2142) | left | grass | none | LIDAR, circuit default |
| Mansfield (2142) | right | grass | none | LIDAR |
| Woodland (2390) | left | grass | armco at 3 m + debris fence | LIDAR, circuit default |
| Woodland (2390) | right | grass | none | LIDAR, circuit default |
| The Mountain (2681) | left | grass | armco at 8 m | LIDAR, circuit default |
| The Mountain (2681) | right | grass | armco at 53 m | LIDAR, circuit default |
| Hall Bends (2924) | left | grass | armco at 4 m | LIDAR, guide: classicbikehub.uk |
| Hall Bends (2924) | right | grass | armco at 18 m + debris fence | LIDAR, guide: classicbikehub.uk |
| The Hairpin (3080) | left | gravel trap 0-8 m | armco at 12 m | LIDAR, circuit default |
| The Hairpin (3080) | right | grass | armco at 8 m | LIDAR, circuit default |
| Barn (3231) | left | grass | armco at 8 m | LIDAR, circuit default |
| Barn (3231) | right | grass | armco at 7 m | LIDAR, circuit default |

Uncertain: Cadwell's barrier details (Armco vs tyre walls at the Hairpin and Barn) aren't published; the 2019 flight predates nothing major.
