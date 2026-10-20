# Real-world tracks (imported centrelines)

A real circuit becomes a TrackSpec with `sketch.imported` (schema: `schema/README.md`, "Imported tracks"): the exact
centreline with per-point heights and camber, the surrounding ground, woodland and named places. The geometry core
uses it as given, the builder cambers the road and plants the real woods, and the web editor shows it read-only.

**Naming (2026-10-20):** a real track is named after the circuit alone, no "Real" prefix: display name "Cadwell
Park", slug `cadwellpark` (the importer's default: the name in lower case, letters and digits only; it refuses a
"Real…" name or a slug that isn't the name's default). Events read "<track> <layout>", e.g. "Cadwell Park Club".
Cadwell Park and Anglesey were RealCadwell / `realcadwell` and RealAnglesey / `realanglesey` until then; zips,
renders and caches made before keep the old names.

## MSV pack

Chris is building every MotorSport Vision circuit as a real-world track, one mod per circuit (all its layouts in
one base scene), one circuit at a time, plus Anglesey (not MSV). Build order and layouts (current official
layouts; lengths from Wikipedia unless noted, to verify per circuit under the standing rules below):

| # | Circuit (mod, slug) | Layouts (main first) | Status |
|---|---|---|---|
| 1 | Cadwell Park (`cadwellpark`) | Full 3.502 km, Club 2.375 km, Woodlands 1.365 km (the bike chicane variant isn't used by cars) | built 2026-10-20 |
| – | Anglesey (`anglesey`, not MSV) | International 3.380 km, Coastal 2.494 km, National 1.931 km, Club 1.287 km | built 2026-10-20 |
| 2 | Brands Hatch | Grand Prix 3.916 km, Indy 1.944 km (1.928 km in the text) | next |
| 3 | Oulton Park | International 4.307 km, Island 3.616 km, Fosters 2.656 km | |
| 4 | Snetterton | 300 4.779 km, 200 3.219 km, 100 1.577 km | |
| 5 | Donington Park (`doningtonpark`) | Grand Prix 4.020 km, National 3.186 km (MSV; Wikipedia 3.149 km) | built 2026-10-20 |
| 6 | Bedford Autodrome | GT, South West, SEN, West, South, North, East (MSV / PalmerSport list; lengths conflict between sources: check MSV's own Bedford page) | |
| 7 | Circuito de Navarra | Full 4.313 km, Medium 2.693 km, Short 1.391 km (since 2024) | outside the UK: needs a Spanish open DTM in `load_dtm` |

**Standing requirements (Chris, 2026-10-20), for every circuit in the pack:**

1. **All official layouts.** Check the circuit's own pages (msv.com, the circuit's site), Wikipedia and other
   reliable sources (racingcircuits.info maps, the Motorsport UK yearbook lengths) for every current official
   layout and build each one as a layout of the circuit's mod. Record each layout's source in the recipe
   (`layoutSource`).
2. **Race direction and start/finish line from official sources.** For every layout, verify the real racing
   direction and the real start/finish line position from official sources (the circuit's map, MSV track guides;
   racingcircuits.info maps and race reports second), not from OSM way direction alone. Some circuits use a
   different line per layout (Cadwell Park Club, Anglesey National and Club): give such a layout its own
   `startFinish`. In the built files the line, the grid facing and the AI route direction must match: checkpoint
   0 / the finish checkpoint on the real line, the grid ahead of it facing the driving direction, the compiled AI
   route in checkpoint order. Record the sources in the recipe (`directionSource`, `startFinishSource`); the
   importer and `geom/tests/test_import_real_track.py` fail a recipe without them, and `check_scene` /
   `check_export` check the line, the grid facing and the driving order in every layout.
3. As before: correct direction (`otherway`, route vs checkpoint order), road flush with the grass, a 24-car grid
   on tarmac per layout, dividers between close sections, selective barriers with grass run-off, kerbs (and green
   strips on fast exits), a minimap `.tcat` per layout, within the stock scene limits.

## Importer

```powershell
# downloads are cached in E:\Source\wreckfest\.local\<slug>\ (gitignored); never commit them
geom\.venv\Scripts\python tools\import_real_track.py tools\real_tracks\cadwellpark.json `
    --out geom\tests\samples\cadwellpark.json --report E:\Source\wreckfest\.local\cadwellpark\report.json --verify
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

Recipe extras (Anglesey, `tools/real_tracks/anglesey.json`):

- `altLayouts`: further layouts sharing the base, each with its own `lap` of ways (`[way, from_node, to_node]`
  takes part of a way, backwards when `from_node` comes after `to_node`: a link mapped against the driving
  direction), `measuredLapLengthM` (the OSM polyline) and `publishedLapLengthM`. Each lap is fitted like the main
  one; the part that leaves the main line (around the ways the main lap doesn't use, grown while it stays more
  than 1 m off the main line, plus 10 m) becomes `sketch.imported.altLayouts[]` (ends moved onto the main line);
  `mainLayoutName` names the main layout. `--verify` reports each layout's lap, sections, grid and every route's
  fit to the DTM.
- `altLayouts[].startFinish` (optional, same forms as the main one): the layout's own start/finish line, for
  circuits that time a layout elsewhere (Cadwell Park Club, Anglesey National and Club). Without it the layout
  shares the main line and grid, and its lap must pass the main line.
- `startFinish`: `{"node": id}` (an OSM node on a fetched way, projected onto the lap), `{"alongWay": id,
  "fraction": f}` or `{"lat", "lon"}`.
- `layoutSource`, `directionSource`, `startFinishSource` (required, top level and per alternative layout): where
  the layout's route, racing direction and line come from (see "MSV pack", standing rules). The importer and
  `test_import_real_track.py` fail without them.
- `lapNames`: `null` drops an unnamed way's label.
- `seaLevelODN`: no-data (sea) cells become seabed 3 m below it and the spec gets `seaLevelM`; the `coastal`
  environment floods below it.
- `horizon`: the distant skyline (3-45 km, 32 m DTM overview) for a painted `#horizon` texture (Wales only).
- `terrainCellM`: heightfield cell (default 6 m; 10 m for a wide coastal margin).

Every recipe (2026-10-19, after Cadwell Park drove backwards):

- `expectedCornerOrder`: the published lap order of the named corners (track guides). The importer fails when
  the OSM labels, in driving order from the start line, don't follow it (a rotation is fine, reversed isn't);
  `geom/tests/test_import_real_track.py` drives each recipe's committed sample through it in every layout and
  checks the recipe's `clockwise`. The builder's AI routes, start points and checkpoints follow increasing s, and
  `check_export` reads the built `.scne` to confirm the game drives that way (worker README, "Driving direction
  and `otherway`"). Cadwell Park's spec was always right; the game drove it backwards because bgeometry flips an
  AI strip it considers non-canonical unless `otherway` is set.
- `gridSize` 24: the game races up to 24 cars and parks any without a start point beside the grid, on the grass.
- `kerbs` (optional): per-corner kerb data from real sources, `{"corner": <label>, "entry"|"apex"|"exit"|"green":
  bool, "source": <where it comes from>}`, written to `sketch.imported.kerbs` at the label's sM. The builder applies
  it to the corner nearest that sM; other corners get the geometric placement (apex kerb inside, exit kerb outside,
  a green strip beyond the exit kerb when the apex radius is 40 m or more). Use OSM kerb/run-off features, a clear
  LIDAR signal or a track guide, never proprietary imagery. The build's `manifest.json` lists every kerbed corner
  with its source (`kerbs`).

## Data sources and attribution

| Data | Source | Licence | Attribution (in the spec's `sketch.imported.source`) |
|---|---|---|---|
| Layout (raceway ways), woodland polygons | OpenStreetMap via the Overpass API | ODbL 1.0 | © OpenStreetMap contributors |
| Heights, camber, terrain (Wales) | Welsh Government / NRW LIDAR composite DTM, 1 m (COG on DataMapWales) | Open Government Licence v3.0 | Contains Natural Resources Wales information © Natural Resources Wales and database right |
| Heights, camber, terrain | Environment Agency LIDAR Composite DTM, 1 m (bare earth; WCS `environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs`) | Open Government Licence v3.0 | © Environment Agency copyright and/or database right 2022 |

The committed sample (`geom/tests/samples/cadwellpark.json`) is derived data (a 2 m centreline, a 6 m grid and
simplified woods), not the raw downloads. A published mod that uses it should carry the same attribution.

## Cadwell Park: Full, Club and Woodlands (2026-10-20)

One base scene, three layouts (`cadwellpark_race` = Full, `cadwellpark_club`, `cadwellpark_woodlands`; events
"Cadwell Park Full / Club / Woodlands"). Recipe `tools/real_tracks/cadwellpark.json` (every source is in it),
sample `geom/tests/samples/cadwellpark.json`, report `.local\cadwellpark\report.json`, zip
`E:\WreckfestMods\cadwellpark.zip` (7.0 MB), installed and enabled as "Cadwell Park".

| | Full | Club | Woodlands |
|---|---|---|---|
| Source of the layout | MSV car circuit map (cadwellpark.co.uk) | racingcircuits.info Club map + history; MSV map "Club Hairpin Grandstand" | racingcircuits.info Woodlands map; MSV: "the rarely used Woodlands circuit" |
| Route | the whole lap | Full minus the Mountain loop: the Club Hairpin (OSM 117987847) from the end of the run to the Mountain back onto the start straight | the Mountain loop: start straight, a hairpin at its Coppice end (OSM 396424044) onto the run to the Mountain, Mountain, Hall Bends, Hairpin, Barn |
| Lap (built) | 3,496.9 m | 2,371.4 m | 1,349.6 m |
| Published | 3,502 m (Wikipedia; MSV 3.54 km): −0.15 % | 2,375 m (1.476 mi; motorsport-timing 2.37 km): −0.15 % | 1,365 m (0.848 mi; motorsport-timing 1.36 km): −1.1 % |
| OSM polyline | 3,494 m | 2,383 m | 1,372 m |
| Direction | clockwise (MSV map arrow from Barn towards Coppice) | clockwise | clockwise |
| Start/finish | on the start straight where the pit garage lane rejoins the pit lane, 153 m from Barn (MSV map flag ≈155 m; ukCircuits: lights opposite the start of the pit exit lane) | own line just after Mansfield, 44 m along the run to the Mountain (racingcircuits.info: "a separate timing loop was installed just after Mansfield"; its Club map's "Finish" flag) | shares the Full line (racingcircuits.info Woodlands map) |
| Grid | 24 slots on the start straight | 24 slots after its own line (grade ≤ 3.0 %) | the Full grid |
| Hairpin | | radius 7.3 m (the real tight hairpin; was eased to 11 m before) | radius 7.1 m |

The Club and Woodlands hairpins turn back between the start straight and the run to the Mountain, which lie only
a few metres apart there (the DTM shows one paved V where they meet). No junction mouth fits, so the builder
paves the gap where the turn crosses it (an apron, worker README "Imported U-turn links"), with an apex kerb and
a green strip on its inside; the dividers between the two roads stop there. Real Club races grid on the start
straight before the hairpin joins it and finish after Mansfield; that first part of the straight isn't on the
Club lap, so in the game the Club grid sits just past its own (finish) line.

Renders: `.local\renders\cadwellpark-club-{start,grid,hairpin-approach,hairpin,oblique}.png`.

## Cadwell Park full circuit (as built 2026-10-19)

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

**v3 (2026-10-19, after Chris's first drive):** it ran backwards (bgeometry flipped the AI route; now `otherway`),
the road sat on a 0.5-1 m lip (the terrain took the lowest road within ~10 m; now flush, 4 cm down), eight of
24 cars started on the grass (16 start points; now 24), the start straight and the run to the Mountain ran
15-17 m apart with nothing between them (now a divider along the middle of the gap; the bank between them is in
the DTM), the kerbs were grey (UVs across the texture) and continuous with a barrier row all round (now kerbs at
corners only, green strips on fast exits, barriers only where needed). Kerb sources: OSM has no kerb or run-off
features here (Overpass: `barrier=kerb`, `kerb=*`, `area:highway=*`, `racing:kerb` none) and the 1 m DTM can't
resolve 5 cm kerbs, so five corners use track-guide overrides (Coppice exit; Charlies exit + green; Park apex;
the Gooseneck entry, apex, exit + green; Hall Bends entry + apex; sources in the recipe) and the rest geometry.
Built `E:\WreckfestMods\realcadwell.zip` (6.7 MB; barriers on 27-29 % of each side, 414 barrier props instead of
1,312; built route checked to run in checkpoint order) and installed; renders
`.local\renders\realcadwell-v3-{driver,driver-parallel,driver-mountain,driver-gooseneck,oblique,horizon}.png`.
Anglesey got the same rebuild (geometric kerbs: no OSM, LIDAR or guide data on kerbs there):
`E:\WreckfestMods\realanglesey.zip`, renders `.local\renders\realanglesey-v2-*.png`, installed.

**Minimap (2026-10-20):** both circuits had no in-race minimap: the mods shipped no `.tcat`, which the game needs
to set the minimap up (it draws the compiled AI route; there is no minimap image). Every layout now gets a
generated `.tcat` (worker README, "Track data and the minimap"); both zips rebuilt.

## Anglesey: National and Club added (2026-10-20)

All four current layouts now (Wikipedia, racingcircuits.info maps): `anglesey_race` International,
`anglesey_coastal`, `anglesey_national`, `anglesey_club`; zip `E:\WreckfestMods\anglesey.zip` (6.0 MB), installed
and enabled as "Anglesey".

| | International | Coastal | National | Club |
|---|---|---|---|---|
| Lap (built) / published | 3,286.8 / 3,380 m (−2.8 %) | 2,463.8 / 2,494 m (−1.2 %) | 1,898.9 / 1,931 m (−1.7 %) | 1,272.1 / 1,287 m (−1.2 %) |
| Route | full lap | Corkscrew link (OSM 108374690) | School Corner (OSM 108374691) from the end of the return straight, Rocket, Peel, Seamans, Tom Pryce Straight, Hairpin, return straight | start straight, Target, Banking, Church, Club Hairpin (OSM 264840722) |
| Direction | clockwise (racingcircuits.info arrows; BRSCC notes) | clockwise | clockwise (ACU 2012: first corner the uphill right School Corner) | clockwise |
| Start/finish | OSM "Start/Finish" node by the 2007 pits | shares International | own: the return straight beside the National pit lane, 62 % along it (ACU 2012: the original pre-2007 line and pit lane; map flag) | own: 22 m after the Club Hairpin joins the start straight (map flag; the International line is 34 m before that junction) |

The International's grid now passes the Club Hairpin's mouth (junctions with other layouts' branches no longer end
an imported track's first sector). At the Bus Stop the Coastal joins one OSM node before the National leaves; the
builder keeps their two stitches on separate edge segments.

## Anglesey International + Coastal (as built 2026-10-19)

One base scene with two layouts (`anglesey_race` = International, `anglesey_coastal` = Coastal). Data
phase, sources and the environment brief: `docs/real-tracks/anglesey.md`. Recipe `tools/real_tracks/anglesey.json`,
sample `geom/tests/samples/anglesey.json`, report `.local\anglesey\report.json`.

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
cliff face on the shore, stone field walls, gorse clumps, three farmsteads, light concrete barrier rows (stock prop_barrier8_dirty, about 2,500 vertices each, so the base stays within the stock vertex budget: 6.97 M of 10 M with every subscene), crowds at
the Corkscrew, the Rocket and the Banking, and a `#horizon` painted from the DTM skyline (Snowdonia 90–140°, the
Llŷn hills to the south, open sea to the south-west). The terrain heightfield is 10 m cells over the circuit
± 1 km (coarse, so the far ground carries the real coast).

Game data checked for the sea: Wreckfest 1 has no sea material. The only water is the animated
`water2#saw33#texture_animation` (levels/crm `water2_c5`, a cartoon pool caustic) and fields13's mud-puddle
`#blend` (`ground_water_fields13_c`); neither reads as sea, so the sea is a generated blue-grey texture on a flat
`#noshadow` plane. Stone walls: stock `prop_stone_wall_01/02` (3.4 × 1.6 × 1.6 m) exist and are used.

Unverified in game: the sea plane and the painted horizon, the second layout's checkpoints and AI, how 12 m
and the Banking's camber drive. Not installed yet (waiting for confirmation that Cadwell Park loads and for the
imported-track direction fix; rebuild after that lands).

Zip `E:\WreckfestMods\realanglesey.zip` (two events, Anglesey International and Anglesey Coastal; 3,896
subscenes, 6.97 M vertices with every subscene, within the stock limits). Renders:
`.local\renders\realanglesey-{oblique,horizon,driver,driver-corkscrew,driver-banking,driver-rocket,driver-school}.png`.

## Donington Park: Grand Prix and National (2026-10-20)

One base scene, two layouts (`doningtonpark_race` = Grand Prix, `doningtonpark_national`; events "Donington Park
Grand Prix / National"). Recipe `tools/real_tracks/doningtonpark.json` (every source is in it), sample
`geom/tests/samples/doningtonpark.json`, raw data and report in `.localealtracks\doningtonpark\`, zip
`E:\WreckfestMods\doningtonpark.zip` (6.7 MB), installed and enabled as "Donington Park". MSV's own
circuit-layouts page lists exactly these two layouts (Wikipedia and racingcircuits.info: the same pair, 2010 to
date); the 2010 Tilke GP extension was never built.

| | Grand Prix | National |
|---|---|---|
| Source of the layout | MSV GP Circuit map (donington-park.co.uk/about/circuit-layouts): 2.49 mi / 4.02 km, 12 corners | MSV National Circuit map: 1.98 mi / 3.186 km, 10 corners; the Roberts chicane (OSM 27131003, "short cut used on National circuit") in place of the Melbourne Loop |
| Lap (built) / published | 3,999.7 / 4,020 m (−0.5 %) | 3,183.8 / 3,186 m (−0.1 %; Wikipedia 3,149 m: +1.1 %) |
| OSM polyline | 4,006 m | 3,180 m |
| Direction | clockwise: the MSV map's start/finish chevrons point down the Wheatcroft Straight towards Redgate; guides (driver61.com, oversteer48.com) start the lap at Redgate, a right-hander | clockwise (same map and guides) |
| Start/finish | on the Wheatcroft Straight 197 m from Goddards, beside the middle of the pit lane (driver61.com coordinates 52°49′47.3″N 1°22′46.4″W; MSV maps) | shares the GP line and grid (same chevrons on the MSV National map) |
| Grid | 24 slots towards Redgate, grade ≤ 2.5 % | the GP grid |

Corners (OSM names, driving order): Wheatcroft Straight, Redgate, Hollywood, Craner Curves, Old Hairpin,
Starkey's Bridge, Schwantz Curve (unnamed in OSM), McLeans, Coppice, Starkey's Straight (unnamed in OSM), Fogarty
Esses, Melbourne Hairpin, Goddards; the National leaves after Starkey's Straight through Roberts.

- **Ground:** EA 1 m DTM, heights 63.0–98.9 m (range 36 m), climb 48 m per lap; the Craner Curves drop at −8.1 %
  over 10 m, McLeans climbs +7.4 %. Camber up to 2.7° (p95 1.6°). Fit: centre RMS 0.014 m, inner edges 0.025 m;
  registration shifts −2.0…+0.5 m, no corner easing (min radius 15.9 m at the Melbourne Hairpin).
- **Width:** not published; 11 m (`widthNote`).
- **Kerbs:** OSM has none; seven corners from the driver61.com guide (Redgate, Craner Curves, Old Hairpin apex +
  exit, Schwantz Curve, McLeans apex + exit + green concrete, Coppice, Goddards' sausage kerb as an apex kerb), the
  rest geometric.
- **Environment:** `parkland` with the 18 OSM woods (Donington Hall's woods along the Craner Curves and Coppice);
  crowds at Redgate, the Craner Curves, the Old Hairpin and the Melbourne Hairpin; tyre barrier rows on 22 % of a
  side at most. East Midlands Airport lies about 1 km south, outside the 400 m terrain margin, so it isn't
  modelled (the horizon is the stock fields vista).
- **Checks:** builder `check_scene` + `check_export` pass (a divider between the two legs of the Melbourne Loop;
  24 start points and slot footprints on the road; finish checkpoint on the line in both layouts; route in
  checkpoint order with `otherway`; minimap `.tcat` per layout matching the compiled route; base 2,705 subscenes,
  3.4 M vertices with every instance, clutter 5.0 ha, all within the stock limits).
- **Renders:** `.localenders\doningtonpark-{oblique,horizon,driver,driver-craner,driver-melbourne}.png`.
- **Check in game:** the Craner Curves descent and Old Hairpin, the Melbourne Hairpin divider, the 11 m width.

