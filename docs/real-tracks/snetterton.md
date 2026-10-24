# Snetterton: 300, 200 and 100 (2026-10-20)

One base scene, three layouts (`snetterton_race` = 300, `snetterton_c200`, `snetterton_c100`; events "Snetterton
300 / 200 / 100"; layout ids need a leading letter, hence `c200`/`c100`). Recipe `tools/real_tracks/snetterton.json`
(every source is in it), sample `geom/tests/samples/snetterton.json`, raw data, MSV map PDFs and report in
`E:\Source\wreckfest\.local\realtracks\snetterton\`, zip `E:\WreckfestMods\snetterton.zip`, installed and enabled as
"Snetterton".

## Layouts and sources

The current official layouts are exactly three: MSV's circuit-layouts page (snetterton.co.uk/about/circuit-layouts)
lists the 100, 200 and 300 Circuits; Wikipedia and racingcircuits.info give the same three (2011 to date).

| | 300 | 200 | 100 |
|---|---|---|---|
| Layout source | MSV map "Snetterton 300 Circuit" | MSV map "Snetterton 200 Circuit" | MSV map "Snetterton 100 Circuit" |
| Route | Senna Straight, Riches, Wilson, Palmer, Agostini, Hamilton, Oggies, Williams, Bentley Straight, Brundle, Nelson, Bomb Hole, Coram, Murrays | Riches, Wilson, then right at Chapman (OSM 161975517) onto Bentley Straight: no infield | the infield loop: Palmer, Agostini, Hamilton, Oggies, the foot of Williams, Firmans (OSM 161975518, mapped the other way) back onto the road to Palmer |
| Lap (built) | 4,788.7 m | 3,194.5 m | 1,556.2 m |
| Published | 4,779 m (2.969 mi; Wikipedia, racingcircuits.info; MSV "3 miles"): +0.20 % | 3,219 m (2.000 mi; MSV "2 miles"): −0.76 % | 1,577 m (0.980 mi; MSV "1 mile"): −1.32 % |
| OSM polyline | 4,801 m | 3,202 m | 1,566 m |
| Direction | clockwise: the MSV map arrow by the flag on Senna Straight points west to Riches; driver61.com turn order (1 Riches … 12 Murrays) | clockwise (MSV 200 map) | the 300's infield driven the same way (Palmer → Agostini → Hamilton → Oggies); anticlockwise as a loop on its own (recipe `"clockwise": false`) |
| Start/finish | Senna Straight, OSM node 1717542072 "Start/finish line", 459 m from Murrays: the MSV flag in front of race control; BARC: "start line and finish line is located on the Senna Straight"; driver61 GPS | shares the 300 line and grid | **estimated**: no official source places a 100 line (the MSV 100 map shows only the grey Senna Straight flag, off the loop). The 100 runs while the 200 uses Senna Straight, so it can't share that line; it is put on the infield's only long straight, Palmer to Agostini beside Tyrrells, 112 m after Palmer, with its own 24-car grid. **Chris to confirm.** |
| Grid | 24 slots, max grade 1.5 % | the 300 grid | its own 24 slots after its line |

MSV lists pole on the left for all three; the builder puts pole on the inside of the first corner (Riches, a
right-hander, for the 300/200).

## Numbers (300)

| | Value |
|---|---|
| Height | 40.1–45.9 m above datum (range 5.8 m), climb 14.4 m per lap: flat |
| Steepest (10 m) | +1.6 %, −1.9 % (the 100: −2.3 %) |
| Camber | up to 2.5°, p95 1.4° |
| Fit to the DTM | centre RMS 0.015 m (max 0.06 m), inner edges 0.019 m |
| Registration | lateral shifts −1.5 … 0.0 m; no corner easing (min radius 13.3 m; the Firmans hairpin 11.9 m) |
| Rules | valid: maxGradePct 2.3, maxBankDeg 2.5, grid 1.5 %, min radius 11.9 m |
| Width | 12.0 m (not published; ~11–14 m on imagery) |

## Environment, kerbs, barriers

New environment `airfield` (data-driven, `environments.json`; schema enum, weather families fields14/13/08, web
palette): open short dry grass, only the real OSM woods (4 here) and a light shelter belt at the terrain edge, no
single park trees, ploughed arable patches from ~220 m out, a low horizon with thin distant tree lines. Crowd banks at
Murrays (Jim Russell Stand), Bomb Hole (Oliver Sear Stand), Palmer (Tyrrells) and Riches.

Kerbs from the driver61.com Snetterton 300 guide (OSM has none): Riches exit only (its apex kerbs are steep, "I
wouldn't advise using any of the kerbs"), Wilson, Agostini, Hamilton, Williams, Nelson, Bomb Hole and Murrays apex +
exit (Murrays and Oggies with green), Palmer entry + apex + exit, Brundle and Coram apex; Chapman and Firmans
geometric. Barriers (tyre walls) on at most 29 % of a side; dividers between the infield roads.

Builder tests (`run-builder-tests.ps1 -Specs snetterton`): PASS 98 scene checks, 86 export checks (three layouts,
the 100 with its own line and grid). Base: 1,891 subscenes, 2.76 M vertices with every instance, clutter 5.0 ha.

Renders: `.local\renders\snetterton-{start,riches,bombhole,firmans,oblique}.png`.

**Check in game:** the 100's start line and grid (estimated), the Firmans hairpin (11.9 m) and the AI round it, the
200's Chapman link, the minimap per layout, how open the airfield looks.

## Audit 2026-10-24

Pre-Workshop accuracy audit (method and the shared importer change: `docs/real-tracks.md`, "Audit notes"). Plots:
`.local\audit\snetterton\{before,after}-{main,c200,c100}.png`, plan views over the LIDAR relief
`.local\audit\snetterton\plan-*.png`, before/after line `.local\renders\audit\snetterton-line-bentley.png`.

| Issue | Evidence | Fix |
|---|---|---|
| Bentley Straight read as a gentle bend | A spurious R 229 m bend from a registration step (the flat airfield gives the registration little to hold) | Shared importer fix (registration smoothed over 25 m): R 2,500 m |
| Senna Straight kink | OSM bends the straight 9° over two nodes 20 m apart (s 4,555-4,575 m), so the built line has a short R ~170 m bend (0.7 m off a 100 m quadratic); the LIDAR relief (the barrier line beside it) shows the real road curving gently over ~150 m. The same turn, just more spread out | None: below the new test's R 130 m limit and invisible at 12 m wide. Spreading the turn at sparse nodes was tried and didn't help here (the two nodes are 20 m apart) |
| Corner radii | Built vs OSM circle fit: Wilson 18/22 m, Agostini 26/32, Oggies 21/22, Hamilton 46/46, Bomb Hole 54/68, Murrays 14/12; Brundle-Nelson 21 m vs a 3-node fit of 15.5 m (plan view: the line follows OSM, rounding its apex node by ~0.5 m) | None |
| Corner handedness and order | Riches R, Wilson R hairpin, Palmer L, Agostini L, Hamilton L, Oggies R, Williams R, Brundle R, Nelson R, Bomb Hole R, Coram R, Murrays L: driver61's turn order | None |
| Elevation (flat airfield) | Range 5.8 m, climb 14.4 m per lap, ±1.9 % over 10 m, largest grade change 1.3 % per 10 m (Bomb Hole's shallow dip); raw DTM vs built 1.2 cm RMS, no bump over 12 cm | None |
| Camber | ≤ 2.5° (Bomb Hole 2.3° into the corner); the crossfall swaps sides between some corners (±1°, e.g. Riches to Wilson), smoothly; on straights within 0.4° of the 25 m mean | None |
| Width | Not published, no OSM `width`; the airfield is flat round the road, so the LIDAR bounds it only loosely (13-18 m flush surface) | None: 12 m kept |
| 100 start line (open, unchanged) | No official source places it | Estimated as documented above; Chris to confirm |

Lap lengths unchanged to 0.1 m: 300 4,788.7 m (published 4,779 m, +0.20 %), 200 3,194.5 m (3,219 m, −0.76 %), 100
1,556.2 m (1,577 m, −1.32 %); the OSM polylines are 4,801 / 3,202 / 1,566 m.

## Width (measured 2026-10-24)

Source: EA National LIDAR Programme point cloud, tiles TL9585 (2018-11) and TM0085 (2021-11). Method and data: `docs/real-tracks.md`, "Widths (2026-10-24)". Profile `tools/real_tracks/widths/snetterton.json`; plots and overlays `.local\audit\snetterton\width-*.png`.

| | Value |
|---|---|
| 300 | median 10.7 m, 7.7-15.3 m (p10 10.0, p90 13.3); 83 % confident |
| Wide | Bentley Straight 13.1-14.8 m, Brundle 10.7-14.9 (widest, s 3,309), Agostini 10.6-12.8, Murrays 9.9-12.6, Oggies 10.7-12.4 |
| Narrow | 7.7 m near Williams (s 2,404), Nelson 9.0-10.8, Riches 9.2-10.9 |
| 200 / 100 links | 9.7-10.9 m / 10.1-10.6 m |
| Line | moved by up to 2.8 m (mean 0.9 m); lap 4,785.8 m (published 4,779 m, +0.1 %) |
| Spec | `options.widthM` 10.7 |

## Run-off, barriers and trees (2026-10-24)

Measured from open data (method: `docs/real-tracks.md`, "Run-off, barriers and foliage"): EA National LIDAR Programme point cloud (intensity, classes, heights);
OSM run-off, barrier and woodland features; barrier types from `tools/real_tracks_data/runoff_evidence.json`.
Data `tools/real_tracks/runoff/snetterton.json`, `tools/real_tracks/vegetation/snetterton.json`; overlays
`.local\audit\snetterton\runoff-*.png`, `vegetation-overview.png`.

| | Before (builder until 2026-10-24) | Now (measured) |
|---|---|---|
| Run-off | grass everywhere (no traps or aprons); 495 stock barrier props (tyres: red/white tyre heaps prop_tirebarrier02/04) on 30 % / 19 % of the main lap's left / right sides | gravel 9 %, tarmac 0 %, grass 91 % of the main lap's sides; barriers none 51 %, armco 29 %, concrete 11 %, tyres 8 %, bank 2 %; debris fence on 0 %; built barrier line on 45 % / 73 % (left / right) |
| Trees | 260 trees: OSM woods plus seeded park trees and an edge belt | 14 measured woods (19.3 ha in the 450 m box), 729 single trees, canopy 7.2 % of the box; 62 trees placed (729 candidates inside the terrain, cap 1200) |
| Look | grass verges, red/white tyre heaps at fast corners and crowds | open grass run-off on the airfield with Armco where the LIDAR finds a barrier (half the lap has none within 60 m), black tyre walls at Coram, a few gravel patches |

Evidence per corner (main lap; the zone that holds the corner label; offsets from the road edge):

| Corner (sM) | Side | Run-off | Barrier | Source |
|---|---|---|---|---|
| Riches (436) | left | grass | armco at 14 m | LIDAR, circuit default |
| Riches (436) | right | grass | armco at 13 m | OSM, guide: driver61.com/circuit-guide/snetterton-300/ |
| Wilson (821) | left | gravel trap 3-9 m | none | LIDAR |
| Wilson (821) | right | grass | none | OSM, LIDAR, circuit default |
| Palmer (1165) | left | grass | none | LIDAR |
| Palmer (1165) | right | grass | none | LIDAR |
| Agostini (1714) | left | grass | none | LIDAR |
| Agostini (1714) | right | grass | armco at 6 m | LIDAR, circuit default |
| Hamilton (2028) | left | grass | none | LIDAR |
| Hamilton (2028) | right | grass | none | LIDAR |
| Oggies (2290) | left | gravel trap 0-6 m | none | LIDAR |
| Oggies (2290) | right | grass | none | LIDAR |
| Williams (2504) | left | grass | none | LIDAR, circuit default |
| Williams (2504) | right | grass | none | LIDAR |
| Bentley Straight (2951) | left | grass | none | LIDAR, circuit default |
| Bentley Straight (2951) | right | grass | armco at 5 m | LIDAR, circuit default |
| Brundle (3383) | left | grass | bank at 7 m | LIDAR, circuit default |
| Brundle (3383) | right | grass | armco at 12 m | LIDAR, circuit default |
| Nelson (3487) | left | grass | bank at 8 m | LIDAR, circuit default |
| Nelson (3487) | right | grass | none | LIDAR |
| Bomb Hole (3727) | left | grass | armco at 26 m | LIDAR, circuit default |
| Bomb Hole (3727) | right | grass | armco at 54 m | LIDAR, circuit default |
| Coram (4060) | left | grass | tyres at 34 m | LIDAR, guide: snetterton.co.uk/news/2026/mar/5 |
| Coram (4060) | right | grass | none | LIDAR |
| Murrays (4308) | left | grass | none | LIDAR, guide: snetterton.co.uk/news/2026/mar/5 |
| Murrays (4308) | right | gravel trap 0-4 m | tyres at 22 m | LIDAR, circuit default |
| Senna Straight (4734) | left | grass | none | LIDAR, circuit default |
| Senna Straight (4734) | right | grass | concrete at 1 m | OSM, LIDAR, circuit default |

Uncertain: the 2018/2021 flights predate the 2026 works (new tyre walls at Coram, tarmac behind the kerbs at Nelson and Murrays); Agostini's tarmac run-off (Autosport guide) isn't contiguous with the edge in the LIDAR and reads as grass.
