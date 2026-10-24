# Oulton Park: International, Island and Fosters (2026-10-20)

One base scene, three layouts (`oultonpark_race` = International, `oultonpark_island`, `oultonpark_fosters`; events
"Oulton Park International / Island / Fosters"). Recipe `tools/real_tracks/oultonpark.json` (every source is in it),
sample `geom/tests/samples/oultonpark.json`, raw data, MSV map PDFs and report in
`E:\Source\wreckfest\.local\realtracks\oultonpark\`, zip `E:\WreckfestMods\oultonpark.zip` (8.4 MB), installed and
enabled as "Oulton Park".

## Layouts and sources

The current official layouts are exactly three: MSV's circuit-layouts page (oultonpark.co.uk/about/circuit-layouts)
lists International, Island and Fosters; Wikipedia and racingcircuits.info give the same three (2003 to date). No
"Island Historic" is on MSV's list: it is a sim-racing variant (iRacing), not an official layout, so it isn't built.
OSM also maps the old line past Hislops (way 27299256, the pre-chicane road); no current layout uses it.

| | International | Island | Fosters |
|---|---|---|---|
| Layout source | MSV map "Oulton Park International Circuit" (`OP Map_International Circuit.pdf`) | MSV map "Oulton Park Island Circuit" | MSV map "Oulton Park Fosters Circuit" |
| Route | Old Hall, The Avenue, Dentons, Cascades, Lakeside, Island Bend, Shell Oils Corner, Brittens, Hilltop, Hislops, Knickerbrook, Clay Hill, (Water Tower), Druids, Warwick Bridge, Lodge, Deer Leap | Lakeside straight into the Island Hairpin (OSM 27299255 + 820427876) straight onto Hilltop: no Island Bend, Shell Oils or Brittens | Cascades into Fosters (OSM 29187915 "Foster's") across to Hislops and Knickerbrook |
| Lap (built, 2026-10-24 audit) | 4,320.2 m (4,316.9 before the chicanes) | 3,566.6 m (was 3,564.0) | 2,679.2 m (was 2,676.4) |
| Published | 4,307 m (Wikipedia; MSV 4.30 km, 2.69 mi; racingcircuits.info 4,333 m): +0.31 % (was +0.23) | 3,616 m (MSV 2.26 mi, Wikipedia; racingcircuits.info 3,637 m): −1.37 % (was −1.44) | 2,656 m (MSV 1.66 mi, Wikipedia; racingcircuits.info 2,662 m): +0.87 % (was +0.77) |
| OSM polyline | 4,325 m | 3,573 m | 2,670 m |
| Direction | clockwise: the MSV map arrow by the flag points down the pit straight to Old Hall; coachdaveacademy.com / driver61.com guides (Old Hall turn 1, Deer Leap last) | clockwise (MSV Island map) | clockwise (MSV Fosters map; racingcircuits.info "off after Cascades to a tight right-hander leading to Knickerbrook") |
| Start/finish | pit straight just after Deer Leap: OSM node 235311718 (`raceway=start-finish`), the MSV maps' chequered flag | shares the International line and grid | shares the International line and grid |
| Grid | 24 slots, max grade 1.8 % | the International grid | the International grid |

MSV lists pole on the left for all three; the builder puts pole on the inside of Old Hall (a right-hander), i.e. on
the right. The Island branch (133 m) leaves at s 1,286 and rejoins on Hilltop at s 2,173; the Fosters branch
(182 m) leaves at Cascades (s 790) and rejoins before Hislops (s 2,613).

## Numbers (International, from the importer report)

| | Value |
|---|---|
| Height | 53.5–70.9 m above datum (range 17.5 m), climb 42.2 m per lap |
| Steepest (10 m) | +11.1 % (up from Knickerbrook to Clay Hill), −9.3 % |
| Camber | fitted up to 10.9° at the Shell Oils hairpin, clamped to the 10° imported-track rule (the importer now clamps to `importedMaxBankDeg`); p95 3.6° |
| Fit to the DTM | centre RMS 0.019 m (max 0.08 m), inner edges 0.028 m; branches 0.017–0.018 m |
| Registration | lateral shifts −3.0 … +3.0 m; no corner easing (min radius 16.2 m Shell Oils, 12.0 m on the Fosters link) |
| Rules | valid: maxGradePct 11.1, maxBankDeg 10.0, grid 1.8 %, min radius 12.0 m |
| Width | 10.0 m (not published; ~9–11 m on imagery) |

## Lake, environment, kerbs, barriers

Oulton Lake (11.7 ha, OSM "Oulton Park Lake") lies inside the circuit beside Lakeside and Island Bend. The importer
now takes OSM `natural=water` polygons (recipe `lakes`), levels each from the LIDAR water surface (the EA DTM is flat
at the water: 52.69 m ODN) and sinks the heightfield under it 1 m; the builder lays a flat generated-water plane at
that level (parkland `lake`), and the planner keeps scenery off it. Four lakes are built: Oulton Lake, Budworth Pool
(the part inside the heightfield) and two ponds. Environment `parkland`: 49 OSM woods (the mature woodland round
Druids, Lodge and the lake), crowd banks at Lodge Corner, Old Hall Corner, Knickerbrook (the three MSV grandstands)
and Druids Corner.

Kerbs from track guides (OSM has no kerb features; coachdaveacademy.com Oulton guide, driver61.com): Old Hall apex +
exit, Cascades apex + exit + green (matting), Island Bend apex, Shell Oils entry + apex + exit, Brittens apex + exit +
green, Hislops and Knickerbrook apex + exit, Clay Hill apex, Druids apex + exit, Lodge apex + exit + green; the rest
geometric. Barriers (tyre walls) on at most 24 % of a side, dividers between the branches and the main road.

Four shared fixes came out of this circuit (separate commits): the importer's way start index (the Island branch ran
alongside Hilltop as a second road), the camber clamp, the clutter band narrowed before thinning (the grass beside
the road was too sparse on a long multi-layout track), dividers running ~4 m past the end of a gap, and alternative
layouts' checkpoints kept on the built road (one sat 1.3 m off the Island link after its junction cut).

Builder tests (`run-builder-tests.ps1 -Specs oultonpark`): PASS 100 scene checks, 87 export checks (all three
layouts; lakes planes at their levels, nothing on the water). Base: 4,202 subscenes, 4.23 M vertices with every
instance, clutter 5.0 ha, all within the stock limits.

Renders: `.local\renders\oultonpark-{start,cascades,druids,oblique}.png`.

**Check in game:** the lake (colour and how its edge meets the bank; clutter grass may poke through the shallow
edge), the Shell Oils hairpin camber, the Island and Fosters links and their checkpoints, the minimap per layout.

## Chicanes (2026-10-24)

Chris drove Oulton Park and missed a "bus stop" chicane on the big straight; his reference image (from another game,
looking back up the Hill straight from Knickerbrook, against the racing direction) shows a right-left jink off a
coned-off straight: **Hislop's**. Oulton Park has two chicanes in use, both on the Hill straight from Shell Oils to
Knickerbrook:

- **Brittens** (OSM 820427208 "Britten's"; racingcircuits.info "Foulston's/Britten's"), a left-right at the start
  of the straight just after Shell Oils. MSV's maps draw it on the International only; the Island Hairpin joins the
  straight beside it (OSM 450023909 + 820427876, the straight line the Island uses), so the Island bypasses it.
- **Hislop's** (OSM 820427209), the right-left about 135 m before Knickerbrook (Wikipedia: installed after Paul
  Warwick's death in 1991 to slow cars coming down Hilltop; racingcircuits.info: the Knickerbrook chicane, enlarged
  and pulled back in winter 2002, named after Steve Hislop). The old straight beside it (OSM 27299256) is unused
  and coned off.
- **Knickerbrook** itself is now a corner, no longer a chicane.

Both were in OSM, the recipe and the TrackModel (and the compiled AI route), but the importer smoothed every lap at
σ 5 m, which turned their 10-15 m corners into 16-20 m S-bends taken flat out (render
`.local\renders\oultonpark-chicane-before.png`). The recipe now lists them in `sharpWays` (smoothed 1 m, the
registration held through them): Brittens 16.3 → 12.7 m, Hislop's 20.1 → 14.9 m tightest radius. Kerbs: the
builder finds both corners of each chicane and kerbs each (apex inside, exit outside), so both sides are red/white;
the recipe's guide data (apex + exit, green at Brittens) applies to the corner nearest the label.

**Fosters (open):** Wikipedia describes the Fosters as "half of Cascades followed by the Hislop's chicane", which we
build. MSV's current Fosters map instead draws Hislop's in grey and the Fosters link joining the old straight
(27299256) to Knickerbrook; that lap measures 2,649 m on OSM against MSV's 2,656 m (through Hislop's 2,670 m), so
the map is probably right. It crosses the International's line at the chicane entry, a flat crossing that imported
tracks can't have yet (the importer gave `crossing-needs-annotation`, 2026-10-24); left as is until imported layouts
support crossings.

## Audit 2026-10-24

Pre-Workshop accuracy audit (method and the shared importer change: `docs/real-tracks.md`, "Audit notes"). Plots:
`.local\audit\oultonpark\{before,after}-{main,island,fosters}.png`, plan views over the LIDAR relief
`.local\audit\oultonpark\plan-*.png`, before/after line `.local\renders\audit\oultonpark-line-{hill,avenue}.png`.

| Issue | Evidence | Fix |
|---|---|---|
| Straights wandered sideways (registration steps): The Avenue, the Hill straight, Hilltop | Short-wave OSM-to-built offset up to 0.54 m; spurious bends on straights (Hilltop read R 227 m, The Avenue R 140 m) | Shared importer fix (registration smoothed over 25 m): short-wave offset ≤ 0.26 m, Hilltop R 1,960 m, The Avenue R 410 m; tightest bend on a straight R 176 m (the dip after Lodge, a real curve) |
| Chicanes | Brittens 15 m and Hislop's (International) 17 m minimum radius, Fosters' Hislop's 13 m: kept by `sharpWays` (earlier fix); OSM circle fit at Brittens 14 m | None |
| Corner radii | Built vs OSM circle fit: Lodge 30/32 m, Druids 54/58, Shell Oils 29/35, Cascades 56/71, Old Hall 38 m (OSM too sparse to fit) | None |
| Corner handedness and order | Old Hall R, The Avenue kink, Dentons R, Cascades L, Island Bend L, Shell Oils R hairpin, Brittens R-L, Hislop's R-L, Knickerbrook R, Clay Hill L, Water Tower L, Druids R, Warwick Bridge L kink, Lodge R, Deer Leap L: as the MSV map and guides | None |
| Elevation | Range 17.5 m, climb 42 m per lap; Knickerbrook to Clay Hill +11.0 % over 10 m (+10.2 % over 50 m), the guides' steep climb; the dip between Lodge and Deer Leap −9.3 % / +7.8 % (Deer Leap's crest); largest grade change 5.4 % per 10 m. No published gradients | None |
| LIDAR artefacts | Raw DTM vs built 1.7 cm RMS, 9 cm max; no bump over 12 cm against a 15 m running median: no dip at Warwick Bridge, where the track crosses a stream, nor under the footbridges | None |
| Shell Oils camber 10.9° (clamped to 10°) | Cross-sections at the apex: the inside of the hairpin 1.4 m lower than the outside across the inner 8 m, a drainage dip at the inside edge; the plane fit is on the paved strip | None (real; clamped by the imported-bank rule) |
| Camber elsewhere | ≤ 4.5°; on straights within 0.6° of the 25 m mean, no flip-flop | None |
| Width | Not published, no OSM `width`. LIDAR flush-surface bounds 9.0-10.5 m on the narrow parts (Lakeside, Shell Oils, Warwick Bridge) | None: 10 m kept |
| Environment | 4 lakes (Oulton Lake etc.) and 49 woods, none on the road; crowds at the three MSV grandstands (Lodge, Old Hall, Knickerbrook) and Druids | None |
| Fosters route (open, unchanged) | MSV's current Fosters map joins the old straight to Knickerbrook (2,649 m on OSM vs MSV 2,656 m); we drive Hislop's (Wikipedia) | Needs flat crossings for imported layouts; left as documented above |

Lap lengths: International 4,320.3 → 4,320.2 m (published 4,307 m, +0.31 %); Island 3,566.1 → 3,566.6 m (3,616 m,
−1.37 %; the OSM polyline is 3,573 m, so the gap is OSM's line through the Island Hairpin); Fosters 2,679.0 → 2,679.2 m
(2,656 m, +0.87 %; see Fosters above).

## Width (measured 2026-10-24)

Source: EA National LIDAR Programme point cloud, tiles SJ5560 and SJ5565 (flown 2021-04). Method and data: `docs/real-tracks.md`, "Widths (2026-10-24)". Profile `tools/real_tracks/widths/oultonpark.json`; plots and overlays `.local\audit\oultonpark\width-*.png`.

| | Value |
|---|---|
| International | median 9.1 m, 7.0-13.8 m (p10 8.5, p90 11.9); 82 % confident |
| Narrow | Clay Hill 7.0-9.2 m (narrowest, s 3,064), Lakeside 8.4-8.9, Hilltop 8.6-8.8, Warwick Bridge 8.5-8.9, the Avenue 8.8-9.4 |
| Wide | Old Hall Corner and the start straight 11.2-13.8 m (widest at Old Hall), Deer Leap 10.4-13.0, Hislop's 9.2-10.8, Lodge 9.5-10.5 |
| Cascades / Island Bend | 9.2-9.6 m / 8.7-9.2 m: about the lap's median, not notably narrow |
| Island / Fosters links | 8.5-9.1 m / 8.6-11.0 m |
| Line | moved by up to 2.0 m (mean 0.8 m); lap 4,320.4 m (published 4,307 m, +0.3 %) |
| Spec | `options.widthM` 9.1 |

Before Deer Leap and past the line the pit exit's hatched merge is paved: the painted line is taken as the edge.
