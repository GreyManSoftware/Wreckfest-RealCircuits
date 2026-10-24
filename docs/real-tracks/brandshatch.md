# Brands Hatch: Grand Prix and Indy (2026-10-20)

One base scene, two layouts (`brandshatch_race` = Grand Prix, `brandshatch_indy`; events "Brands Hatch Grand Prix"
and "Brands Hatch Indy"). Recipe `tools/real_tracks/brandshatch.json` (every source is in it), sample
`geom/tests/samples/brandshatch.json`, raw data and report in `E:\Source\wreckfest\.local\realtracks\brandshatch\`
(OSM, `osm-extras.json`, EA DTM tiles, `report.json`, the MSV map PDFs' content summarised below), zip
`E:\WreckfestMods\brandshatch.zip` (7.3 MB), installed and enabled as "Brands Hatch".

## Layouts and sources

The current official layouts are exactly two: MSV's circuit-layouts page (brandshatch.co.uk/about/circuit-layouts)
lists the GP Circuit and the Indy Circuit; Wikipedia and racingcircuits.info give the same two (2003 to date). No
bike-only variant is in use.

| | Grand Prix | Indy |
|---|---|---|
| Layout source | MSV map "Brands Hatch Grand Prix Circuit" (docs.msv.com `BH Map_GP Circuit.pdf`) | MSV map "Brands Hatch Indy Circuit" (`BH Map_Indy Circuit.pdf`) |
| Route | Brabham Straight, Paddock Hill Bend, Hailwoods Hill, Druids Bend, Graham Hill Bend, Cooper Straight, Surtees, Pilgrims Drop, Hawthorn Hill, Hawthorn Bend, Derek Minter Straight, Westfield Bend, Dingle Dell, Sheene Curve, Stirling's Bend, Clearways, Clark Curve | as the GP to Surtees, then McLaren (OSM way 4928103) to Clearways and Clark Curve |
| Lap (built) | 3,903.5 m (2026-10-24 audit; was 3,903.2) | 1,930.5 m (was 1,930.3) |
| Published | 3,916 m (2.433 mi; MSV, Wikipedia, racingcircuits.info): −0.32 % | 1,944 m (1.208 mi; MSV, BARC, Wikipedia infobox; Wikipedia text 1.928 km): −0.69 % |
| OSM polyline | 3,903 m | 1,930 m |
| Direction | clockwise: MSV map arrow by the flag on Brabham Straight points to Paddock Hill Bend; Wikipedia: clockwise since 1954; track guides | clockwise: MSV Indy map; BARC Indy briefing notes "Clockwise" |
| Start/finish | Brabham Straight, OSM node 1825805766 (`raceway=start-finish`), 176 m from Clark Curve: the MSV maps' chequered flag in front of the pit garages; driver61's GPS for the line is 19 m further on (BARC: start and finish lines ~20 m apart) | shares the GP line (same flag on the MSV Indy map) and grid |
| Grid | 24 slots ahead of the line, max grade 3.90 % (the straight climbs ~3.7 % to the crest before Paddock Hill) | the GP grid |

The GP and the Indy share everything but McLaren: the Indy branch (207.8 m) leaves the GP lap at Surtees (s 1,248 m)
and rejoins at Clearways (s 3,429 m). MSV lists pole on the left for the GP and on the right for the Indy; the
builder puts pole on the inside of the first corner (Paddock Hill Bend, a right-hander), i.e. on the right for both.

## Numbers (GP, from the importer report)

| | Value |
|---|---|
| Height | 114.2–149.2 m above datum (range 35.1 m), climb 79.9 m per lap |
| Steepest (10 m) | −17.0 % down Paddock Hill Bend; +11.6 % up Hailwoods Hill to Druids |
| Camber | up to 7.4°, p95 6.5° (Druids 6.0°, Hawthorn 5.6°, Stirlings 6.0° the other way) |
| Fit to the DTM | centre RMS 0.014 m (max 0.07 m), inner edges 0.022 m; Indy branch 0.018 / 0.022 m |
| Registration | lateral shifts −2.5 … +1.5 m; no corner easing needed (min radius 28.3 m, Druids) |
| Rules | valid: maxGradePct 17.0 (≤ 25), maxBankDeg 7.4 (≤ 10), grid 3.90 % (≤ 4), min radius 28.5 m |
| Width | 11.0 m (not published; ~10–12 m on imagery), one value for the whole lap |

Two shared changes came out of this circuit: the importer writes centreline heights in mm (cm rounding at 2 m
spacing made the grid read 4.01 %), and `check_export`'s flush check allows the 1.5 m camber run-out beside a low
road edge (the 6–7° banking read p98 0.202 m against 0.20 m; it is the designed run-out, now subtracted).

## Kerbs, barriers, scenery

OSM has no kerb features on the circuit (the two `barrier=kerb` ways are karting islands; no `area:highway`), so the
kerbs come from track guides (driver61.com Indy guide, coachdaveacademy.com GP guide; per corner in the recipe):
Paddock Hill Bend apex + exit, Druids apex + exit, Graham Hill Bend entry + apex + exit + green (the "matting"),
Surtees, Hawthorn, Stirlings apex + exit, Westfield apex + exit + green, Sheene Curve apex only ("no exit kerb, just
grass"), Clearways apex, Clark Curve apex (no exit kerb). Pilgrims Drop, Dingle Dell and McLaren get the geometric
placement. Barriers (tyre walls) on 26 % / 18 % of the GP's sides, none on the Indy branch; the rest grass run-off.

Environment `parkland` (the Kent Downs bowl: lush grass banks, 24 OSM woods: Westfield Wood and the woods inside
the GP loop and round Druids), crowd banks at Paddock Hill Bend, Druids Bend and Clearways (the Paddock Hill and
Hailwoods grandstands are on the outside of the first two). Base: 4,581 subscenes, 4.14 M vertices with every
instance (limit 10 M), clutter 5.0 ha (limit 6), all within the stock limits.

Builder tests (`run-builder-tests.ps1 -Specs brandshatch`): PASS 80 scene checks, 65 export checks (both layouts:
route in checkpoint order, finish checkpoint on the line, 24 grid footprints on the road facing the driving
direction, minimap `.tcat` matching the layout, road flush, scene limits).

Renders: `.local\renders\brandshatch-{start,paddockhill,druids,oblique}.png`.

**Check in game:** the drop into Paddock Hill Bend (−17 %) and the climb to Druids, the camber at Druids and
Hawthorn, the grid on the climbing Brabham Straight, the Indy's McLaren link and its checkpoints, the minimap for
both layouts, the 11 m width.

## Audit 2026-10-24

Pre-Workshop accuracy audit (method and the shared importer change: `docs/real-tracks.md`, "Audit notes"). Plots:
`.local\audit\brandshatch\{before,after}-{main,indy}.png`, plan views over the LIDAR relief
`.local\audit\brandshatch\plan-*.png`, before/after line `.local\renders\audit\brandshatch-line-cooper.png`.

| Issue | Evidence | Fix |
|---|---|---|
| Cooper Straight wandered sideways (registration steps) | Short-wave part of the OSM-to-built offset 0.66 m on Cooper Straight, 0.4 m after Sheene Curve | Shared importer fix (registration smoothed over 25 m): 0.22 / 0.21 m; tightest bend on any straight R 188 m (the Paddock Hill descent, a real curve) |
| Corner radii | Built minimum radius vs circle fits to OSM nodes: Druids 31/33 m, Hailwoods 34/33, Graham Hill 33/34, Westfield 35/34, Stirlings 36/41, Paddock Hill 56/69 (its tightest part is at turn-in), Hawthorn 76/97, Clark Curve 75/90 | None (plan view of Druids: the line on the paved strip) |
| Corner handedness and order | Paddock Hill R, Druids R hairpin, Graham Hill L, Surtees L, Pilgrims Drop R kink, Hawthorn R, Westfield R, Dingle Dell R, Sheene R, Stirlings L, Clearways R, Clark Curve R: as the MSV map and guides | None |
| Paddock Hill drop steeper than a published figure | LIDAR: 131.6 m ODN at the crest on Brabham Straight (s 150 m) to 114.3 m at the bottom (s 370 m): 17.3 m in 220 m, −17.0 % over 10 m, −15.4 % over 50 m. oversteer48.com gives 140 → 129 m (11 m) and −8 % steepest, +11 % up to Druids, from an unsourced elevation map. The raw 1 m DTM matches the built line to 1.3 cm RMS and the cross-sections show the paved strip; the published +11 % to Druids matches ours (+11.6 %), the −8 % looks like a coarse DEM's | None: the LIDAR is kept |
| Brabham Straight camber 6-7° | Cross-sections at s 50-200 m: a uniform 11 cm/m fall from left to right across 20+ m (the straight runs along the side of the bowl); inner-edge fit RMS 2.2 cm | None (real) |
| Paddock Hill camber | Described as off-camber in guides; the LIDAR has the outside about 0.5 m higher across the road at turn-in (+3.3°) and nearly level (+1.5°) in the compression | None; noted as uncertain (the "adverse camber" may mean the falling road) |
| LIDAR artefacts | No bump over 12 cm against a 15 m running median; largest grade change 6.2 % per 10 m (the compression at the bottom of Paddock Hill) | None |
| Camber elsewhere | Druids 6.0°, Hawthorn 5.6°, Stirlings 6.0° into the corner; on straights within 0.6° of the 25 m mean | None |
| Width | Not published, no OSM `width`. LIDAR flush-surface bounds 11.5-12.5 m on the cleanest sections (Surtees to Pilgrims Drop), wider where run-off is flush | None: 11 m kept |

Lap lengths: GP 3,903.2 → 3,903.5 m (published 3,916 m, −0.32 %), Indy 1,930.3 → 1,930.5 m (1,944 m, −0.69 %); the
OSM polylines are 3,903 / 1,930 m, so the gaps are OSM's.
