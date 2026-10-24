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
