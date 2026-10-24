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
| 1 | Cadwell Park (`cadwellpark`) | Full 3.502 km, Club 2.375 km, Woodlands 1.365 km (the bike chicane variant isn't used by cars) | built 2026-10-20, audited 2026-10-24: 3,496 m (−0.2 %), 2,372 m (−0.1 %), 1,349 m (−1.1 %); [audit](real-tracks/cadwellpark.md) |
| – | Anglesey (`anglesey`, not MSV) | International 3.380 km, Coastal 2.494 km, National 1.931 km, Club 1.287 km | built 2026-10-20 |
| 2 | Brands Hatch (`brandshatch`) | Grand Prix 3.916 km, Indy 1.944 km (MSV) | built 2026-10-20, audited 2026-10-24: GP 3,904 m (−0.3 %), Indy 1,931 m (−0.7 %); [details](real-tracks/brandshatch.md) |
| 3 | Oulton Park (`oultonpark`) | International 4.307 km, Island 3.616 km, Fosters 2.656 km (MSV) | built 2026-10-20, chicanes sharpened and audited 2026-10-24: 4,320 m (+0.3 %), 3,567 m (−1.4 %), 2,679 m (+0.9 %); [details](real-tracks/oultonpark.md) |
| 4 | Snetterton (`snetterton`) | 300 4.779 km, 200 3.219 km, 100 1.577 km (MSV) | built 2026-10-20, audited 2026-10-24: 4,789 m (+0.2 %), 3,195 m (−0.8 %), 1,556 m (−1.3 %); the 100's own line is estimated; [details](real-tracks/snetterton.md) |
| 5 | Donington Park (`doningtonpark`) | Grand Prix 4.020 km, National 3.186 km (MSV; Wikipedia 3.149 km) | built 2026-10-20 |
| 6 | Bedford Autodrome (`bedfordautodrome`) | GT 6.11 km, SW 4.50 km, West 3.0 km, SEN 3.70 km (MSV's circuit-layouts page; North, South and East no longer listed) | built 2026-10-20 |
| 7 | Circuito de Navarra (`navarra`) | Main Track 4.313 km, Medium 2.693 km, Short 1.391 km (MSV, since September 2024) | built 2026-10-20 (IGN MDT05 heights, UTM 30N) |

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
- Closed ways (2026-10-20, Bedford, where OSM maps each circuit as one loop): `[way, from_node, to_node, "fwd"|"rev"]`
  takes the part of a closed way from one node to the other along or against the way, through its closing node when
  needed; a bare way id still takes the whole way. Alternative layouts are cut where they leave the main lap by OSM
  *segment* (node to node, in the driving direction), so a layout may use another part of a way the main lap uses.
- `extraWays`: ids of non-raceway ways a layout drives (Bedford's airfield service road between two loops), fetched
  by id into `osm_extra.json`.
- `cornerLabels`: `[{"name", "node"}]`, names placed at OSM nodes on the main lap, for corners on a long way (a loop
  way, or numbered turns); they join the way labels in `corners` and `expectedCornerOrder`.
- `registrationMaxShiftM` (default 6): 0 keeps the OSM line as mapped (flat ground such as an airfield has no road
  signal in the DTM, so the registration only drifts, differently per layout). `branchOffM` (default 1): how far a
  layout's fitted line must be from the main one to count as its own branch (unregistered lines on shared ways
  match exactly but are sampled out of phase, up to 1 m apart).
- `nodeJoins`: `[[node_a, node_b]]`, OSM nodes under 5 m apart that are one junction on the ground but not joined in
  OSM (Navarra's Short hook); a way may start at the partner of the previous way's end.
- `"dtm": "es-mdt05"` (Spain): the IGN MDT05 (5 m, from the PNOA LIDAR, CC BY 4.0) from the IDEE INSPIRE WCS
  (`servicios.idee.es/wcs-inspire/mdt`, coverage `Elevacion25830_5`, ASCII grids in 1 km tiles) in ETRS89 / UTM
  30N: the importer then projects WGS84 (taken as ETRS89, under 1 m apart) to UTM 30N with the same transverse
  Mercator formulae on GRS80, and the local frame uses the UTM scale factor. On a coarser DTM the heights and camber
  are smoothed more (sigma max(4 m, 2 cells) and max(10 m, 3 cells)). An explicit `slug` may also be the default of
  the name's last words ("Circuito de Navarra" -> `navarra`).
- `altLayouts[].joinWithinM`: a branch that runs beside the main road before it merges (Bedford SEN) joins where it
  first comes within this distance of the main line, instead of overlapping it.
- `lapLengthTolerancePct` + `lapLengthNote` (main or per layout): a larger gap to the published length than the
  test's 3 %, only with a note of at least 40 characters explaining it (`test_import_real_track.py`).
- Lap length: `options.lapLengthKm` is informational for imported tracks and may be up to 7 km (schema); drawn
  tracks stay at 5 km or less (rule `drawn-lap-length-max`).
- `seaLevelODN`: no-data (sea) cells become seabed 3 m below it and the spec gets `seaLevelM`; the `coastal`
  environment floods below it.
- `horizon`: the distant skyline (3-45 km, 32 m DTM overview) for a painted `#horizon` texture (Wales only).
- `terrainCellM`: heightfield cell (default 6 m; 10 m for a wide coastal margin).
- `lakes` (`{"minAreaM2", "depthM"}`, Oulton Park): OSM `natural=water` polygons in the terrain box, clear of the
  road, become `sketch.imported.lakes` (level from the LIDAR water surface; the heightfield under them sunk `depthM`);
  environments with `lake` (parkland, airfield) lay a flat water plane there and keep scenery off it.
- `altLayouts[].clockwise`: a layout that winds the other way as a loop while driving the shared roads the same way
  (Snetterton 100, the 300's infield loop): `false`. Alt layout ids start with a letter (`c200`, `c100`).
- `sharpWays` (+ `sharpWaysNote`): ids of chicane ways whose corners the 5 m smoothing would round off (2026-10-24,
  Oulton Park's Hislop's read as a kink in game). Over them the line is smoothed only 1 m (feathered over 8 m) and the
  registration shift is held from their ends (it could pull the line towards a disused straight beside them); the
  rule radius still applies. Used for Oulton Park (Brittens, Hislop's), Donington Park (Fogarty Esses) and Anglesey
  (Bus Stop).
- Heights are written in mm (cm rounding moved a 2 m step's grade by 0.5 %), and the fitted camber is clamped to
  the 10° imported-bank rule (the report keeps `bankFittedMaxDeg`; Oulton Park's Shell Oils hairpin fits 10.9°).

Environments for real tracks: `parkland` (Cadwell Park, Brands Hatch, Oulton Park, Donington Park), `coastal` (Anglesey), `airfield`
(Snetterton, Bedford Autodrome: flat, open, few trees, arable land beyond), `arid` (Circuito de Navarra).

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

## Chicanes (2026-10-24)

Chris drove Oulton Park and missed the "bus stop" chicane on the big straight. It was in OSM, the recipe and the
model, but the importer's 5 m smoothing had made every chicane a gentle S taken flat out. Recipes now list their
chicanes in `sharpWays`; tightest radius before / after:

| Circuit | Chicane (OSM way) | Before | After | Layouts using it |
|---|---|---|---|---|
| Oulton Park | Brittens (820427208), start of the Hill straight | 16.3 m | 12.7 m | International (Island bypasses it via the Island Hairpin) |
| Oulton Park | Hislop's (820427209), 135 m before Knickerbrook | 20.1 m | 14.9 m | International, Island; Fosters too in our build (see oultonpark.md) |
| Donington Park | Fogarty Esses (28381201) | 21.3 m | 17.7 m | Grand Prix, National |
| Anglesey | Bus Stop (892471229) | 28.8 m | 13.6 m | all four |

Lap lengths barely move (Donington GP 4,000.9 m, National 3,184.0 m; Anglesey 3,286.5 / 2,463.2 / 1,899.7 / 1,272.1 m).

Survey of chicanes OSM might map outside the recipes (cached `osm.json` plus one Overpass query per circuit for every
way sharing a node with its laps, `osm-connected.json`): no circuit has an unused chicane way. Unused `raceway` ways
are pit lanes, karting/rally stages, other configurations (Bedford's links, Navarra's "Connection") and Oulton's old
straight past Hislop's (27299256) and the Island line through Brittens (450023909 + 820427876); Cadwell's 92 m
private raceway beside Woodland (396424045) is a service bypass, not a car chicane. Connected non-raceway ways are
service, emergency and paddock roads. Brands Hatch (no chicane), Snetterton (Bomb Hole, Coram: corners, no chicane on
the 300/200/100), Cadwell Park (none for cars), Bedford (none on GT/SW/West/SEN) and Navarra (numbered turns, no
named chicane; their tightest S-bends follow OSM) need no change. Donington's old Esses were replaced by the Fogarty
Esses; the Melbourne Hairpin and Goddards are hairpins (15.9 m, 22.2 m), not chicanes.

## Audit notes (2026-10-24)

Accuracy audit before the Workshop release, in two halves: Cadwell Park, Brands Hatch, Oulton Park and Snetterton
here (findings in `docs/real-tracks/<slug>.md`, "Audit 2026-10-24"); Donington Park, Bedford, Navarra and Anglesey by
a second agent. Plots per layout (curvature with the OSM polyline's, built and raw-DTM heights, grade over 10 and
50 m, grade change over 10 m, camber; straights shaded) and plan views over a LIDAR relief map are in
`.local\audit\<slug>\` (`after-<layout>.png`, `plan-*.png`).

**Shared importer change (commit "importer: smooth the registration shift"):** the registration's lateral shift was
found in 0.5 m steps and smoothed over only 5 m, so a straight's line stepped 0.5-1 m sideways over about 20 m
wherever the best shift changed (R 115 m jinks on Cadwell's straights; Brands Hatch's Cooper Straight, Oulton's Hill
straight). It is now found in 0.25 m steps and smoothed over 25 m (`REGISTRATION_SMOOTH_M`); the fit to the DTM is
unchanged (centre and inner-edge RMS, cross residual). **Every registered recipe's sample changes slightly when
re-imported** (lap lengths by under 1 m; corner radii by a few per cent): Donington Park and Anglesey need a re-import
to pick it up (Bedford and Navarra use `registrationMaxShiftM` 0 and are unaffected). Tried and dropped: wider
smoothing at sparse OSM nodes (a gentle curve drawn as a few nodes reads as kinks): it barely moved any metric.

**New regression test** (`test_imported_sample_has_no_wobble_height_steps_or_camber_flips`, every recipe's sample,
every layout): on straights no 6 m-scale bend tighter than R 130 m; grade change at most 12 % over 10 m; camber on
straights within 1.5° of its 25 m mean and nowhere changing faster than 0.5°/m. All eight current samples pass
(Donington National's committed sample reads R 133 m on a straight, close to the limit; 188 m after a re-import).
The importer test also checks that the registration shift follows a skewed road without steps.

**What the audit found to be sound (no change):** heights. The raw 1 m DTM sampled along every built line (median
over the inner 60 % of the road) matches the built heights to 1.2-2.0 cm RMS, 10 cm at most (the σ 4 m smoothing at
Cadwell's Mountain crest), and has no local bump over 12 cm against a 15 m running median anywhere on the four
circuits: no bridge decks, tunnels, vehicles or DTM gaps under the road. Corner radii follow the OSM geometry (plan
views); circle fits to OSM nodes that read tighter were node noise. Camber is the LIDAR's (cross-sections checked
at Brabham Straight, Paddock Hill Bend and Shell Oils). Widths: no published figures and no OSM `width` tags on any
of the four; the LIDAR gives only upper bounds (flush verges and run-off count as road), consistent with the recipe
values, so they stay.

## Data sources and attribution

| Data | Source | Licence | Attribution (in the spec's `sketch.imported.source`) |
|---|---|---|---|
| Layout (raceway ways), woodland polygons | OpenStreetMap via the Overpass API | ODbL 1.0 | © OpenStreetMap contributors |
| Heights, camber, terrain (Wales) | Welsh Government / NRW LIDAR composite DTM, 1 m (COG on DataMapWales) | Open Government Licence v3.0 | Contains Natural Resources Wales information © Natural Resources Wales and database right |
| Heights, camber, terrain | Environment Agency LIDAR Composite DTM, 1 m (bare earth; WCS `environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs`) | Open Government Licence v3.0 | © Environment Agency copyright and/or database right 2022 |
| Heights, camber, terrain (Spain) | IGN MDT05, 5 m, from the PNOA LIDAR (WCS `servicios.idee.es/wcs-inspire/mdt`, coverage `Elevacion25830_5`, ETRS89 / UTM 30N) | CC BY 4.0 | MDT05 © Instituto Geográfico Nacional de España (IGN), derived from PNOA LIDAR |

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
| Lap (built) | 3,496.0 m (2026-10-24 audit; was 3,496.9) | 2,371.8 m (was 2,371.4) | 1,349.4 m (was 1,349.6) |
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
| Lap (built) / published | 3,286.5 / 3,380 m (−2.8 %; Bus Stop sharpened 2026-10-24) | 2,463.2 / 2,494 m (−1.2 %) | 1,899.7 / 1,931 m (−1.6 %) | 1,272.1 / 1,287 m (−1.2 %) |
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
`geom/tests/samples/doningtonpark.json`, raw data and report in `.local
ealtracks\doningtonpark\`, zip
`E:\WreckfestMods\doningtonpark.zip` (6.7 MB), installed and enabled as "Donington Park". MSV's own
circuit-layouts page lists exactly these two layouts (Wikipedia and racingcircuits.info: the same pair, 2010 to
date); the 2010 Tilke GP extension was never built.

| | Grand Prix | National |
|---|---|---|
| Source of the layout | MSV GP Circuit map (donington-park.co.uk/about/circuit-layouts): 2.49 mi / 4.02 km, 12 corners | MSV National Circuit map: 1.98 mi / 3.186 km, 10 corners; the Roberts chicane (OSM 27131003, "short cut used on National circuit") in place of the Melbourne Loop |
| Lap (built) / published | 4,000.9 / 4,020 m (−0.5 %; Fogarty Esses sharpened 2026-10-24) | 3,184.0 / 3,186 m (−0.1 %; Wikipedia 3,149 m: +1.1 %) |
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
- **Renders:** `.local
enders\doningtonpark-{oblique,horizon,driver,driver-craner,driver-melbourne}.png`.
- **Check in game:** the Craner Curves descent and Old Hairpin, the Melbourne Hairpin divider, the 11 m width.

## Bedford Autodrome: GT, SW, West and SEN (2026-10-20)

One base scene, four layouts (`bedfordautodrome_race` = GT, `_sw`, `_west`, `_sen`; events "Bedford Autodrome GT /
SW / West / SEN"). Recipe `tools/real_tracks/bedfordautodrome.json`, sample `geom/tests/samples/bedfordautodrome.json`,
raw data and report in `.local\realtracks\bedfordautodrome\` (MSV and racingcircuits.info maps in `maps\`), zip
`E:\WreckfestMods\bedfordautodrome.zip` (10.7 MB), installed and enabled as "Bedford Autodrome".

**Layouts.** MSV's own circuit-layouts page (bedfordautodrome.co.uk) lists four: GT, SEN, SW, West; all four are
built. racingcircuits.info also has the North (1.61 km), South (2.41 km) and East (1.21 km) circuits, which MSV no
longer lists (PalmerSport driving experiences), so they aren't built (their loops are in the scene where the four
layouts use them). OSM maps the West, North, South and East circuits as four closed loop ways plus links; each
layout takes parts of them, and the GT and SEN cross from the North circuit's S-bend to the South circuit's top
corner over an airfield service road (OSM 449592525, paved between the mapped grass areas).

| | GT | SW | West | SEN |
|---|---|---|---|---|
| Source of the layout | MSV GT map (3.8 mi / 6.11 km, 10 left + 8 right corners); racingcircuits.info GT map T1–T18 | MSV SW map (2.8 mi / 4.50 km); racingcircuits.info South West map | MSV West map (1.87 mi / 3 km); OSM's West circuit way, matching the maps turn for turn | MSV SEN map (2.3 mi / 3.70 km); racingcircuits.info SEN map |
| Lap (built) / published | 5,880.8 / 6,110 m (−3.8 %) | 4,585.3 / 4,500 m (+1.9 %) | 2,798.4 / 3,000 m (−6.7 %) | 3,593.7 / 3,700 m (−2.9 %) |
| OSM polyline | 5,899 m | 4,592 m | 2,806 m | 3,592 m |
| Direction | anticlockwise (racingcircuits.info turn numbering; evo.co.uk: the Hangar Hairpin first) | anticlockwise | anticlockwise (lapmeta: counter-clockwise) | anticlockwise |
| Start/finish | inner (pit) straight before T1, OSM node 305063847, 159 m before the pit lane merges (racingcircuits.info flag, mapped by an affine fit of the West loop) | shares the GT line | shares the GT line | own: the south straight where the South pit lane rejoins, after the small west loop (racingcircuits.info SEN flag) |

**Length gaps.** The OSM routes follow the maps corner for corner (the GT has exactly MSV's 10 left and 8 right
corners) on the mapped tarmac. The SW, built from the same ways, is 1.9 % over MSV's figure, so OSM isn't
uniformly short: MSV rounds to 0.1 mi (±80 m), and its West figure (3.0 km) looks like an older layout's
(racingcircuits.info: 2.98–3.14 km before 2015; lapmeta 2.89 km). The recipe allows 4 % (GT) and 7 % (West) with
`lapLengthNote`s.

- **Ground:** the flat Thurleigh airfield: road heights 82.8–89.5 m (6.7 m over 2.5 km), grades under 2.1 %, camber
  under 1.6°. With no road signal in the DTM the OSM line is used as mapped (`registrationMaxShiftM` 0); fit to the
  DTM: centre RMS 0.015 m.
- **Width:** not published; 12 m.
- **Corner names:** Bedford's corners are numbered on MSV's and racingcircuits.info's maps; the labels are the GT's
  turn numbers ("Turn 2" … "Turn 18", `cornerLabels` at OSM nodes) with Turn 1 named the Hangar Hairpin (evo.co.uk
  corner guide). The other layouts number their corners differently.
- **Kerbs:** OSM has none; the Hangar Hairpin's apex kerb from evo.co.uk, the rest geometric.
- **Environment:** `airfield` (shared with Snetterton): short dry grass round the circuits, few trees (the 8 OSM
  woods and a light shelter belt), arable land from about 220 m out, a low horizon; tyre barrier rows only where
  needed (dividers between the parallel straights, the grid, crowds). Crowds at the Hangar Hairpin, Turn 11 (the
  East tip) and Turn 15 (the North hairpin).
- **Checks:** builder `check_scene` + `check_export` pass for all four layouts (dividers: 60 probes across gaps
  under 30 m, one open at 28.5 m; no overlapping road faces; grids on the road; finish checkpoints on the lines;
  routes in checkpoint order; a minimap `.tcat` per layout; base 2,393 subscenes, 3.8 M vertices with every
  instance, clutter 4.9 ha, within the stock limits).
- **Renders:** `.local\renders\bedfordautodrome-{oblique,horizon,driver,driver-hangar,driver-t11,driver-t15}.png`.
- **Check in game:** the SEN's merge across the paved area after its small west loop, the GT/SEN crossing from the
  North S-bend to the South top corner, how open the run-off feels (AI resets), the 12 m width.

## Circuito de Navarra: Main Track, Medium and Short (2026-10-20)

One base scene, three layouts (`navarra_race` = Main Track, `navarra_medium`, `navarra_short`; events "Circuito de
Navarra Main Track / Medium / Short"). Recipe `tools/real_tracks/navarra.json`, sample `geom/tests/samples/navarra.json`,
raw data and report in `.local\realtracks\navarra\` (MSV and racingcircuits.info maps in `maps\`), zip
`E:\WreckfestMods\navarra.zip` (7.3 MB), installed and enabled as "Circuito de Navarra". Slug `navarra` (the name's
last word, now allowed by the importer).

**Layouts.** MSV's own circuit-layouts page (circuitodenavarra.com) lists the 2024 Main Track, Medium and Short;
Wikipedia and racingcircuits.info have the same three for September 2024 to date (the 650 m extension with the new
Turns 11-14). racingcircuits.info's "Bike" Long and Medium are bike-only and not built. OSM's
"Circuito de Velocidad" way is the 2024 lap.

| | Main Track | Medium | Short |
|---|---|---|---|
| Source of the layout | MSV Main Track map (2.679 mi / 4.313 km, 6 left + 11 right); racingcircuits.info Speed Circuit Long 2024 (T1-T17) | MSV Medium map (1.673 mi / 2.693 km); the diagonal link (OSM 103746068) from T5 to T14 | MSV Short map (0.864 mi / 1.391 km); the Main Track's T8-T11 and the hook from the T11 hairpin's exit back to the T7-T8 road (OSM 1539678089, joined to the lap by `nodeJoins`: it ends 2.3 m off it) |
| Lap (built) / published | 4,280.0 / 4,313 m (−0.8 %) | 2,637.4 / 2,693 m (−2.1 %) | 1,347.7 / 1,391 m (−3.1 %; OSM's hook is coarse, `lapLengthNote`) |
| Direction | clockwise (racingcircuits.info arrows; MSV's 11 right / 6 left fit only clockwise) | clockwise | clockwise |
| Start/finish | main straight opposite the middle of the pit building, 263 m from T17 (MSV map line; OSM pit building 121-402 m along the straight; racingcircuits.info draws its flag ~160 m further on) | shares the Main Track line | own: the T10-T11 straight 70 m after T10 (racingcircuits.info Short flag on that straight; placed where the 24-car grid stays under 4 %) |

Corner labels are racingcircuits.info's turn numbers ("Turn 1" … "Turn 17", `cornerLabels` at OSM nodes); Navarra's
corners have no published names. The OSM lap has exactly MSV's 11 right and 6 left corners.

- **Heights:** Spain's IGN MDT05 (5 m, from the PNOA LIDAR, CC BY 4.0, "© Instituto Geográfico Nacional"), from the
  IDEE INSPIRE WCS in ETRS89 / UTM 30N; the importer now projects to UTM 30N for it (`"dtm": "es-mdt05"`). Road
  heights 427-447 m (range 20.3 m), climb 45 m per lap, +9.3 % / −13.5 % over 10 m (the back straight between T8
  and T9 crosses a 3 m hump), camber up to 7.4° (T11 is banked). The 5 m grid is too coarse to register the OSM
  line to (with 3 m allowed the three layouts' fits drifted apart), so the line is used as mapped; heights and
  camber are smoothed over 10 m and 15 m. Fit to the DTM: centre RMS 0.13 m (max 0.7 m at the hump).
- **Width:** not published; 12 m.
- **Kerbs:** OSM has none and no track guide with kerb detail for the 2024 layout was found: geometric kerbs.
- **Environment:** the new `arid` preset: the stock sandpit03 palette (sand, pebbles, dry ground, sandpit3 dry-grass
  clutter, the landscape1 vista), low scrub, rocks and a few pines, bare rolling hills on the far ground; light
  concrete barrier rows (stock barrier8) only where needed, metal crowd fences; crowds at Turns 3, 11 and 17.
  No OSM woods inside the terrain box.
- **Checks:** builder `check_scene` + `check_export` pass for all three layouts (dividers: 43 probes, none open;
  grids on the road under 4 %; lines and route order; a minimap `.tcat` per layout; base 3,822 subscenes, 8.5 M
  vertices with every instance (the first build was 10.7 M with barrier7a crowd fences, now metal fences), clutter
  5.0 ha). The Short first failed in bgeometry ("safe area did not overlap all sectors"): its start line runs
  nearly east-west, so the route and the narrower safe/race strips disagreed on bgeometry's canonical orientation;
  the builder now runs the strips on past the line until they agree, and `check_scene` checks it.
- **Renders:** `.local\renders\navarra-{oblique,horizon,driver,driver-t3,driver-t11}.png`.
- **Check in game:** the Short's hook inside the T11 hairpin (its road and divider run inside the hairpin), the
  back-straight hump, the banked T11, how the arid ground and sandpit textures read on tarmac.
