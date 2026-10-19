# Real-world tracks (imported centrelines)

A real circuit becomes a TrackSpec with `sketch.imported` (schema: `schema/README.md`, "Imported tracks"): the exact
centreline with per-point heights and camber, the surrounding ground, woodland and named places. The geometry core
uses it as given, the builder cambers the road and plants the real woods, and the web editor shows it read-only.

## Importer

```powershell
# downloads are cached in E:\Source\wreckfest\.local\<slug>\ (gitignored); never commit them
geom\.venv\Scripts\python tools\import_real_track.py tools\real_tracks\cadwell.json `
    --out geom\tests\samples\realcadwell.json --report E:\Source\wreckfest\.local\realcadwell\report.json --verify
```

A recipe (`tools/real_tracks/<name>.json`) names the OSM `highway=raceway` ways of one lap in driving order, the
start/finish point, the width, the viewing spots and the build options. The importer:

1. stitches the ways (each must start where the previous one ends) and checks the direction;
2. projects WGS84 → OSGB36 (OS 7-parameter Helmert, a few metres) → British National Grid, then into a local
   frame in ground metres (divided by the grid's scale factor) centred on the line;
3. smooths the OSM polyline (σ 5 m), then **registers it to the DTM**: per sample, the lateral shift (±6 m) that
   puts a plane across the whole road width on the flattest strip, kept smooth along the road by a dynamic
   programme (OSM traces and the Helmert step are each a few metres out; the road is the flat strip between its
   verges and banks);
4. eases corners tighter than the `min-corner-radius` rule (Laplacian, reports the largest move);
5. starts the lap at the recipe's start/finish point and resamples every 2 m;
6. fits a plane across the inner 80 % of the road every 2 m: its height is the road (smoothed σ 4 m), its slope
   the camber (σ 10 m);
7. writes a 6 m heightfield of the DTM (block means) over the line ± 400 m, the OSM woods, corner names and the
   viewing spots;
8. `--verify` builds the TrackModel and compares it with the DTM (lap length, RMS of the centre and of points
   10 % inside both cambered edges, camber, the rule verdict).

Other circuits: write a recipe (find the way ids with Overpass, e.g.
`way["highway"="raceway"](bbox); out geom;`), run the importer, check the report, then build. Heights: England
(EA DTM, the default) or Wales (`"dtm": "wales"`: the Welsh Government / NRW 1 m DTM COG read by range requests);
elsewhere add another open DTM to `load_dtm`.

Recipe extras (RealAnglesey, `tools/real_tracks/anglesey.json`):

- `altLayouts`: further layouts sharing the base, each with its own `lap` of ways (`[way, from_node, to_node]`
  takes part of a way), `measuredLapLengthM` and `publishedLapLengthM`. Each lap is fitted like the main one and
  the part that leaves the main line becomes `sketch.imported.altLayouts[]` (ends moved onto the main line);
  `mainLayoutName` names the main layout. `--verify` reports each layout's lap and every route's fit to the DTM.
- `startFinish: {"node": id}`: an OSM node on the lap.
- `lapNames`: `null` drops an unnamed way's label.
- `seaLevelODN`: no-data (sea) cells become seabed 3 m below it and the spec gets `seaLevelM`; the `coastal`
  environment floods below it.
- `horizon`: the distant skyline (3-45 km, 32 m DTM overview) for a painted `#horizon` texture (Wales only).
- `terrainCellM`: heightfield cell (default 6 m; 10 m for a wide coastal margin).

## Data sources and attribution

| Data | Source | Licence | Attribution (in the spec's `sketch.imported.source`) |
|---|---|---|---|
| Layout (raceway ways), woodland polygons | OpenStreetMap via the Overpass API | ODbL 1.0 | © OpenStreetMap contributors |
| Heights, camber, terrain (Wales) | Welsh Government / NRW LIDAR composite DTM, 1 m (COG on DataMapWales) | Open Government Licence v3.0 | Contains Natural Resources Wales information © Natural Resources Wales and database right |
| Heights, camber, terrain | Environment Agency LIDAR Composite DTM, 1 m (bare earth; WCS `environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs`) | Open Government Licence v3.0 | © Environment Agency copyright and/or database right 2022 |

The committed sample (`geom/tests/samples/realcadwell.json`) is derived data (a 2 m centreline, a 6 m grid and
simplified woods), not the raw downloads. A published mod that uses it should carry the same attribution.

## RealCadwell (Cadwell Park full circuit, 2026-10-19)

| | Value | Real world |
|---|---|---|
| Lap (built TrackModel) | 3,495.5 m (OSM polyline 3,496.6 m) | 3,477 m published (2.173 mi): +0.53 % |
| Corners identified (OSM names) | Coppice, Charlies, Park Straight, Park, Chris Curve, The Gooseneck, Mansfield, Woodland, The Mountain, Hall Bends, The Hairpin, Barn | full circuit (the club circuit's Club Hairpin link is not used) |
| Direction | clockwise | clockwise |
| Start/finish | middle of the pit lane on the pit straight (estimated), grid on a 2.0 % grade | |
| Width | 9.5 m (recipe) | narrow for cars; about 9-10 m |
| Height | 93.8-139.8 m above datum (range 45.9 m), total climb 79 m per lap | |
| Steepest | +20.7 % over 10 m (+19.2 % over 20 m) on the Mountain; −11.6 % over 10 m into Mansfield | the Mountain: famously steep, with a crest where bikes take off |
| The Mountain | dip at 2,581 m, crest at 2,719 m: 12.0 m rise over 138 m (8.7 % mean, 19.2 % peak), grade drops ~10 points over the crest | |
| Camber | up to 7.5° (Charlies, banked into the corner), 95th percentile 6.0°, mean 2.5° | |
| Fit to the DTM | centre RMS 0.016 m (max 0.08 m), inner edges RMS 0.029 m; cross-road plane residual 1 cm mean | |
| Registration | lateral shifts −3.0 … +3.5 m (mean 1.1 m); corner easing moved the line at most 1.1 m (Hairpin) | |
| Rules | valid: maxGradePct 20.7 (≤ 25), maxBankDeg 7.5 (≤ 10), grid 2.0 % (≤ 4), min radius 11.4 m (≥ 10.75) | |

Woods: 10 OSM wood polygons around the circuit (Coppice, the Woodland section, Barn and the Hairpin); the
parkland environment plants deciduous woods with a few conifers there and single park trees in the open grass.
Crowd banks at the Mountain, the Hairpin and Hall Bends. Renders: `.local\renders\realcadwell-*.png`.

Unverified: the start/finish position (estimated from the pit lane), the width (not published; one value for the
whole lap), and how the camber and the Mountain's crest drive in game.

## RealAnglesey (Anglesey Circuit / Trac Môn, International + Coastal, 2026-10-19)

One base scene with two layouts (`realanglesey_race` = International, `realanglesey_coastal` = Coastal). Data
phase, sources and the environment brief: `docs/real-tracks/anglesey.md`. Recipe `tools/real_tracks/anglesey.json`,
sample `geom/tests/samples/realanglesey.json`, report `.local\realanglesey\report.json`.

| | International | Coastal |
|---|---|---|
| Lap (built TrackModel) | 3,286.8 m (−0.28 % vs 3,296 m measured; −2.8 % vs the published 3,380 m) | 2,463.8 m (−0.33 % vs 2,472 m; −1.2 % vs 2,494 m) |
| Shared with the other layout | start/finish to the Corkscrew (s 0–2,171 m) and the Bus Stop to the line | branch 137.2 m from the Corkscrew (s 2,170.6) to the Bus Stop (s 3,130.8) |
| Height | 11.1–29.8 m ODN (range 18.8 m), climb 31.7 m per lap | same range, climb 26.5 m |
| Steepest (10 m) | +8.1 % (to Rocket), −8.2 % (into the Corkscrew) | −9.7 % (Corkscrew link) |
| Camber | up to 5.7° (the Banking, into the corner), p95 3.9° | branch up to 3.4° |
| Fit to the DTM | centre RMS 0.013 m (max 0.06 m), inner edges 0.015 m | branch centre RMS 0.025 m, edges 0.037 m |
| Rules | valid: maxGradePct 9.8, maxBankDeg 5.7, grid 3.0 % (≤ 4), min radius 13.6 m | |

Width 12 m (OSM `width` on three ways), clockwise, start/finish at OSM node "Start/Finish". Environment
`coastal`: sea plane at 0 m ODN (`seaLevelM` −11 relative to the 11 m datum) over the ground below it, rocks and a
cliff face on the shore, stone field walls, gorse clumps, three farmsteads, concrete barrier rows (prop_barrier7a/7c, which stock fields13 uses by the hundred; not the static tyre walls), crowds at
the Corkscrew, the Rocket and the Banking, and a `#horizon` painted from the DTM skyline (Snowdonia 90–140°, the
Llŷn hills to the south, open sea to the south-west). The terrain heightfield is 10 m cells over the circuit
± 1 km (coarse, so the far ground carries the real coast).

Game data checked for the sea: Wreckfest 1 has no sea material. The only water is the animated
`water2#saw33#texture_animation` (levels/crm `water2_c5`, a cartoon pool caustic) and fields13's mud-puddle
`#blend` (`ground_water_fields13_c`); neither reads as sea, so the sea is a generated blue-grey texture on a flat
`#noshadow` plane. Stone walls: stock `prop_stone_wall_01/02` (3.4 × 1.6 × 1.6 m) exist and are used.

Unverified in game: the sea plane and the painted horizon, the second layout's checkpoints and AI, how 12 m
and the Banking's camber drive. Not installed yet (waiting for the vertex-buffer fix).
