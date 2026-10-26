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
| 7 | Circuito de Navarra (`navarra`) | Main Track 4.313 km, Medium 2.693 km, Short 1.391 km (MSV, since September 2024) | built 2026-10-20; heights from Navarre's 2024 2 m LIDAR since the 2026-10-24 audit ([details](real-tracks/navarra.md)) |

**Standing requirements (Chris, 2026-10-20), for every circuit in the pack:**

1. **All official layouts.** Check the circuit's own pages (msv.com, the circuit's site), Wikipedia and other
   reliable sources (racingcircuits.info maps, the Motorsport UK yearbook lengths) for every current official
   layout and build each one as a layout of the circuit's mod. Record each layout's source in the recipe
   (`layoutSource`).
2. **Race direction and start/finish line from official sources.** For every layout, verify the real racing
   direction and the real start/finish line position from official sources (the circuit's map, MSV track guides;
   racingcircuits.info maps and race reports second), not from OSM way direction alone. Some circuits use a
   different line per layout (Cadwell Park Club, Anglesey National and Club): give such a layout its own
   `startFinish`. In the built files the line, the grid facing and the AI route direction must match: the grid
   behind the real start line (the start lights; Chris 2026-10-26, Anglesey's race started at Target), pole row
   ~4 m behind it, facing the driving direction; the finish checkpoint (the timing line) behind the grid, since
   the game wants the grid in the first sector (stock example layout), so a lap is timed from ~96 m before the
   real line (`pipeline._line_lead`, less where a junction, a tight corner, a grade over 4 % or a road under 8 m
   comes first; the recipe's `startGridNote` says why); the compiled AI route in checkpoint order. Record the sources in the recipe (`directionSource`, `startFinishSource`); the
   importer and `geom/tests/test_import_real_track.py` fail a recipe without them, and `check_scene` /
   `check_export` check the line, the grid facing and the driving order in every layout.
3. As before: correct direction (`otherway`, route vs checkpoint order), road flush with the grass, a 24-car grid
   on tarmac per layout, dividers between close sections, the measured run-off and barrier types (since 2026-10-24,
   "Run-off, barriers and foliage"; red/white only with a source), kerbs (and green
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
  the name's last words ("Circuito de Navarra" -> `navarra`). **The IDEE WCS serves the MDT05 in whole metres**
  (1 m terraces; the report's `dtmWholeMetreFraction` is 0.2-0.45 on it, about 0.01 on a LIDAR DTM in cm): don't use
  it where a better DTM exists.
- `"dtm": "es-navarra"` (Navarre): the Gobierno de Navarra's 2 m DTM from the 2024 LIDAR flight (IDENA WCS
  `idena.navarra.es/ogc/wcs`, coverage `IDENA.WCS__ELEVAC_Ras_MDT_2M`, centimetre heights, CC BY 4.0, "Servicio
  proporcionado por el Gobierno de Navarra"), UTM 30N, 1 km tiles cached as `dtm/idena2m_*.npz` (Circuito de Navarra
  since 2026-10-24).
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
- `roads` (2026-10-24, Oulton Park's old straight past Hislop's): real tarmac that is no layout's own branch,
  `[{"id", "name", "lap", "source"}]`; `lap` is a whole lap through the road (the main lap with the road's ways in
  place of the part it bypasses), fitted and cut like a layout branch and written to `sketch.imported.roads`. It is
  built as road with collision whether or not a layout drives it; a layout that does lists it in
  `altLayouts[].roads` and its lap names the road's ways (its own branch is then found with the road counted as
  shared). geom chains the branch, the roads and the main line; a road may leave the main line up to 30 m before
  the previous piece rejoins it, and the lap hands over where the two lines come closest. `source` is required.
- `widths`: `"measured"` reads `tools/real_tracks/widths/<slug>.json` (measured tarmac widths, see "Widths"): the line
  moves onto the tarmac's middle and every centreline point gets `widthM`; `widthM` stays the nominal width for the
  registration. Every circuit except Anglesey (no open point cloud in Wales).
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
| Oulton Park | Hislop's (820427209), 135 m before Knickerbrook | 20.1 m | 14.9 m | International, Island; the Fosters takes the old straight beside it (built since 2026-10-24, see oultonpark.md) |
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

## Straights, junction ground and dividers (2026-10-24, second pass)

Chris drove the eight circuits after the audit; fixes (shared code, every circuit re-imported and rebuilt):

- **Snaking straights (importer `straighten`).** Cadwell Park's start straight towards Coppice wiggled left-right-left
  (R 205-240 m alternating over 100 m, 1.4 m off its mean). The OSM nodes themselves snake there (R 130-150 m between
  nodes 30 m apart) and the measured-width move kept it: the point cloud gave no confident edges beside the hairpin
  links (confidence 0-0.3), so the measured middle followed the line it was measured from. The fitted line, and again
  after the width move, gets bi-Laplacian smoothing on a 10 m grid (changes of curvature even out; straights and
  constant-radius arcs stay; at most 1.2 m of move; held at corners tighter than R 250 m over 20 m and at
  `sharpWays`). Cadwell's stretch now bends no tighter than R 600 m; lap lengths move by under 1 m on every layout;
  the sample tests pass unchanged. Plan view `.local\audit\cadwellpark\coppice-after.png`. The width there
  stays at the measurement's fill (8.0-8.5 m, against 9.1-9.5 m where measured on the straight); re-measuring with
  better edge detection beside the links would fix that.
- **Ground between close roads (builder `TerrainField`).** The terrain within 6 m of two roads took the lower road's
  surface right up to the higher road's edge, so a junction gore or a narrow gap between two sections at different
  heights showed a dark trench or "hole" beside the higher road (Cadwell's start straight beside the run to the
  Mountain, up to 1.06 m deep; Anglesey's Corkscrew split; Brands Hatch after Surtees and at Clearways; Oulton's
  Island link). A lower road now pulls the ground to its surface only within 1.5 m of its own edge, fading out over
  2.5 m, so the ground slopes across the gap. New `check_scene` check `ground_dips`: no ground more than 0.25 m below
  a road edge 1-3 m out beyond the natural ground; the old code failed it on Cadwell (73 edge samples), Anglesey
  (32), Brands Hatch (33) and Oulton (26), the new code passes on all eight. The verge in junction gores (shoulders
  dropped where another road is within the strip) is the kerb/verge fix's part.
- **Dividers beside short links.** Imported tracks left the divider open 40 m either side of every junction mouth and
  apron link, which removed it along the whole start straight / Mountain approach at Cadwell (the two sections 9.5 m
  apart between the Club and Woodlands links). It is now open only at the mouth or apron itself plus 4 m; the
  `check_scene` divider check probes the gaps afresh on imported tracks (the old code fails it, 34 open probes).
- **Junction mouths (builder `_stitch`).** A mouth's sides were straight chords from the branch's cut to the main
  road's edge, which paved a large triangle outside a curving branch at a fork (Donington's National split). They
  now follow the branch's own edges back to the main road (a third road beside it, as at Anglesey's Bus Stop, keeps
  the chord), so the paved fork is the union of the two roads as OSM and the LIDAR show them; no overlapping road
  faces on any circuit.

**Straight-beside-chicane survey** (every `highway=raceway` way no layout or road drives, from the cached OSM plus the
connected ways): Oulton Park's Hislop's straight was the only one; now built. Brands Hatch, Snetterton (Bomb Hole,
Coram: pit lane only) and Cadwell Park have none (Cadwell's 92 m `access=private` way inside Woodland, 396424045, is
a service loop, not on any map). Donington's Melbourne Loop (Grand Prix) and the National's straight across are both
built. Anglesey's Corkscrew link is the Coastal (built). Oulton's 37 m stub of the old straight past Brittens isn't
on MSV's maps, so it isn't built. Bedford has five unbuilt links of the North, South and East circuits MSV no longer
lists (27782783, 1558089016, 449592528, 449588233, 27811905 "North"): real tarmac that `roads` could add if wanted;
none is a straight beside a chicane.

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

**Second half (Donington Park, Bedford Autodrome, Circuito de Navarra, Anglesey).** Findings tables in
`real-tracks/<slug>.md`, "Audit 2026-10-24"; plots in `.localudit\<slug>\<slug>-<layout>.png`. Shared changes:

- **`tools/audit_real_track.py`** (system Python: numpy + Pillow): per-layout PNGs of curvature, height, 10/50 m
  grade, vertical curvature, camber, straight ripple and wobble against distance with the corner labels (`--before`
  overlays an earlier sample), plus `<slug>-artefacts.json`.
- **`profile_artefacts`** (importer): straight wobble, grade ripple on straights (10 m grade minus its 15 m
  smoothing), camber flips and rate, tightest crest and dip. In every import report (`artefacts`, per alt layout
  too) and checked on every committed sample by `test_imported_sample_has_no_profile_artefacts` (limits: ripple
  RMS ≤ 0.4 %, wobble ≤ 0.5 m, camber rate ≤ 0.35°/m, vertical radius ≥ 75 m; the worst real values when set were
  0.31 %, 0.34 m, 0.27°/m and 93 m). All eight samples pass; Navarra's MDT05 build read 0.53 %.
- **`dtmWholeMetreFraction`** in the report: a terraced (whole-metre) DTM shows 0.2-0.45.
- **`join_blend`:** an alternative layout's branch ends take the main road's height and camber, the mismatch faded
  over at least 30 m (more for a large height step, at most 1.5 % grade); geom only blends heights over 20 m.
  Samples of other circuits change only when re-imported.
- **`ease_vertical`:** crests and dips tighter than 100 m (over 10 m) get a local 6 m smoothing, main lap and
  branches (Navarra Short's hook ramp, 93 m, failed check_scene's AI-strip-on-road check by 7 mm). No current
  English or Welsh sample has one (tightest: Cadwell Park's Mountain, 130 m), so they don't change when re-imported.
- **`merge_onto_main`:** where a branch's road overlaps the main road at its ends, it takes the main road's
  cambered surface (Navarra Short's hook ran 16 cm off the T7-T8 road side by side).
- **`fillet_ends`:** a branch end meeting the main line at more than 15° is curved onto it over 30 m instead of a
  2 m kink (Navarra Short 47°, Bedford SEN 43° and 27°); the sample test checks every branch end is within 25°.
- No other height de-noising was needed on the 1 m EA / Welsh DTMs: their straights show 0.08-0.31 % ripple RMS.

**Reconciling A and B (all eight re-imported on the merged importer).** Re-importing audit A's circuits with B's
`merge_onto_main` failed the camber-rate checks: Cadwell Woodlands 0.77°/m (lap 0.76 at s ≈ 297 m), Oulton Fosters
0.40°/m. Cause: `merge_onto_main` faded the main road's camber over half a road width *sideways*, which a branch
leaving at an angle crosses in 4-10 m (a 3.5° mismatch in 10 m on Fosters), and on Woodlands' hairpin (from the start
straight onto the run to the Mountain, a few metres apart) the nearest main road switched from the one to the other
inside one merge run (−1.9° to −4.1° in 4 m). `join_blend` and `fillet_ends` were not involved. Now each end gets its
own run, which stops where the nearest main point jumps; heights keep the lateral weight (side by side within 1 mm,
as before), and the camber weight fades along the branch over at least 20 m and at most 0.15°/m of mismatch
(`MERGE_FADE_MIN_M`, `MERGE_MAX_BANK_RATE`); `join_blend` matches any rest at the end. New test
`test_branch_joins_take_the_main_surface_without_a_camber_flick` (hairpin and steep-departure cases; the old code read
0.56 and 0.75°/m). Branch camber rates after: Woodlands 0.28, Fosters 0.23, all others ≤ 0.19°/m (Brands Indy 0.31
→ 0.18, Navarra Short 0.25 → 0.16, Anglesey Coastal 0.28 → 0.19). Lap lengths unchanged on every layout (only branch
heights and camber near the joins move).

## Widths (2026-10-24)

Every circuit used one estimated width until now. Seven now have **measured, variable widths** from open LIDAR point
clouds; Anglesey keeps its published 12 m (Wales has no open point cloud). Google, Bing, Esri or other proprietary
imagery was never used.

| Circuit | Data | Median | Min-max | Narrow | Wide | Line moved (max) | Confident |
|---|---|---|---|---|---|---|---|
| Cadwell Park | EA NLP 2019 | 7.8 m | 7.0-10.1 | Park Straight 7.1-7.3, Chris Curve, the Mountain 7.5-8.5, Hall Bends exit 7.0 | Mansfield-Woodland 9.5-10.1, start straight 8.6-9.5 | 1.5 m | 86 % |
| Brands Hatch | EA NLP 2018 | 9.8 m | 7.6-14.8 | the GP loop 8.6-9.6, Clark Curve 7.6 | Paddock Hill Bend 12.1-14.8, Brabham Straight 12.3-14.0 | 2.0 m | 91 % |
| Oulton Park | EA NLP 2021 | 9.1 m | 7.0-13.8 | Clay Hill 7.0, Lakeside 8.4-8.9, Hilltop 8.6-8.8 | Old Hall / start straight 11.2-13.8, Deer Leap | 2.0 m | 82 % |
| Snetterton | EA NLP 2018/2021 | 10.7 m | 7.7-15.3 | by Williams 7.7, Nelson 9.0 | Bentley Straight 13.1-14.8, Brundle 14.9 | 2.8 m | 83 % |
| Donington Park | EA NLP 2022 | 11.5 m | 10.6-17.9 | Starkey's Straight, Hollywood 10.7-10.9 | Melbourne Hairpin 14.7-17.9, Fogarty Esses, Goddards | 3.9 m | 88 % |
| Bedford Autodrome | EA NLP 2023 | 12.5 m | 9.2-17.3 | Turns 9-10 9.9-10.7 | Turns 4-6 14.7-17.1, the Turn 10 Y junction | 4.2 m | 77 % |
| Circuito de Navarra | Navarre LiDAR 2024 (RGBI) | 12.0 m | 9.2-15.4 | Turn 4 10.4-11.4 | main straight 14.1-15.4 | 1.8 m | 74 % |
| Anglesey | none (published) | 12 m | 12 | | | | |

"Confident": main-lap sections with both edges found at confidence ≥ 0.5; the rest are filled from their neighbours
or the circuit's median. Per-circuit details: `real-tracks/<slug>.md`, "Width". **Sanity checks:** Navarra reads 12.0 m
with 14-15 m on the main straight (published: 12 m, 15 m on the main straight); Anglesey is the published 12 m;
Cadwell Park is the narrowest circuit (7.8 m median: sources call it narrow, "about half Silverstone's width"), with
the Mountain and Park Straight among its narrowest parts; Oulton's Cascades (9.2-9.6 m) and Island Bend (8.7-9.2 m)
are about its median, its narrowest parts are Clay Hill, Lakeside and Hilltop. The recipes' old estimates were 1.7 m
too wide at Cadwell Park and 0.5 m too narrow at Donington Park; Brands Hatch's Indy part is markedly wider than its
GP loop.

**Data.** England: the Environment Agency National LIDAR Programme point cloud (LAZ, 5 km OS tiles, Open Government
Licence v3.0; ~1.8 points/m²), found through the DEFRA survey catalogue API
(`environment.data.gov.uk/backend/catalog/api/tiles/collections/survey/search`). Navarre: the Gobierno de Navarra's
2024 LIDAR (LAZ, 1 km tiles, ~7 points/m², RGBI from the joint camera, CC BY 4.0, `filescartografia.navarra.es`,
`5_LIDAR/5_6_2024_NAV_cam_EPSG25830`). Only the tiles under each circuit's roads: one or two per circuit, in
`E:\Source\wreckfest\.local\realtracks\<slug>\pointcloud\` (gitignored). Wales: DataMapWales has only the 2020-22
DTM/DSM rasters (its tile catalogue lists no point clouds), and the DTM's surface roughness shows no road edge.

**Tools** (a venv on E: with numpy, scipy, laspy[lazrs], matplotlib, Pillow; at most 8 processes):

```powershell
py -m venv E:\Source\wreckfest\.local\widths-venv   # pip cache on E:
python tools\real_tracks_data\import_frame.py <slug>      # the committed sample's local frame (re-runs the importer)
python tools\real_tracks_data\fetch_pointcloud.py <slug>  # the LAZ tiles under the roads
python tools\real_tracks_data\measure_width.py <slug> --plots [--at S ...]   # profile + .local\audit\<slug>\width-*.png
pwsh -File tools\real_tracks_data\reimport_widths.ps1 [slug ...]           # re-import with the profiles
```

**Method** (`measure_width.py`): every 5 m along every route a cross-section ±16 m wide and 6 m long. Its points'
intensity (single and last returns, normalised per flight line between the circuit's road level near the line and its
verge level 13-16 m out; in Navarre averaged with the RGB brightness) is binned every 0.5 m across. From the darkest
point within 5 m of the line, each side's edge is where the surface stops being asphalt for 1 m: above a threshold
halfway between the local road and verge levels, a step over 0.25 m off the road's cross plane, or objects
(vegetation, buildings, barriers); a painted line with 2 m or more of paving beyond it (pit lanes and their hatched
merges, aprons) is the edge. Confidence falls with low contrast, few points, an edge not found within ±16 m (run-off,
junctions), objects over the road (trees), no road near the line, disagreement with the 35 m running median, a side 2 m
wider than its 155 m running median, and within 40 m of a branch end. The width and the line's offset from the
tarmac's middle are then filled where confidence is under 0.5 (from neighbours, relaxing to the circuit's median width
and zero offset in long gaps), smoothed (width σ 12 m, offset σ 40 m), clamped (7-18 m, offset ±5 m) and rate-limited
(the width changes at most 0.4 m per 5 m, the offset 0.1 m). `UNMEASURED` lists stretches checked on the overlays that
the survey can't see (Navarra's 2024 extension, unpaved when flown). Overlays (`width-overlay.png`, `width-zoom-*.png`)
show the edges on the intensity raster; `width-<route>.png` plots the width, both halves and the confidence.

**Importer** (recipe `"widths": "measured"`, `widthsNote`): the profile (`tools/real_tracks/widths/<slug>.json`,
section centres, normals and edges in the recipe's grid, derived data) is matched to the fitted line by position (each
sample takes the two nearest sections of its own route running the same way within 8 m). The line **moves onto the
tarmac's middle** (OSM and the DTM registration left it up to 4 m off: Donington's Starkey's Straight, Bedford), is
resampled evenly, and every centreline point gets `widthM`; a branch's move fades out over 30 m towards its ends (they
lie on the main line) and its width blends to the main road's there. Before a layout's branch is cut, its whole lap follows the main line's move where it runs on the main road (within 3 m, fading out by 6 m, smoothed over 15 m), so the shared road stays shared (without it Donington's National and Bedford's loops overlapped the main road). Heights and camber are fitted across the inner
80 % of the measured median width. `options.widthM` becomes the main lap's median width (clamped to the schema's
8-20 m), the fallback; `sketch.imported.source` gains the width attribution, and the report a `widths` summary (median,
min/max, p10/p90, how far the line moved). `geom/tests/test_import_real_track.py` checks every measured sample: a width
at every point, 7-18 m, at most 0.5 m change per 5 m outside 40 m of a junction, `options.widthM` the median.

**Geometry and builder:** `trackgen_geom` builds each route with its per-point width (edges, samples, the TrackModel's
`width`), checkpoints span the road's width there (+2 × 2 m), the grid uses `importedGridSlotWidthM` (4 m) slots on real
tracks, and reports `gridWidthM`, the narrowest road along any grid, which the `grid-width-two-abreast` rule now checks
(8 m for real tracks). The builder already worked per sample (road mesh, kerbs, green strips, shoulders and run-off, AI
route/safe/race strips, dividers between close sections, the flush terrain, the minimap route); its road UVs, edge
decals, apron links and checkpoint-on-road test now use the local width too.

## Run-off, barriers and foliage (2026-10-24)

Chris: run-off and foliage must match reality (Donington has far fewer trees in the circuit area and almost all
gravel run-off), and barriers must be the real types: Armco, plain black tyre walls, concrete, debris fencing or
open banks; **red/white only where a source confirms it** (none does yet, so no real circuit shows any). Until now
every circuit had grass run-off, generic barriers (stock red/white tyre heaps `prop_tirebarrier02/04`, or grey
concrete blocks) on about a quarter of each side at fast corners and crowds, and OSM woods plus seeded park trees and
an edge belt. Per-circuit findings and evidence tables: `real-tracks/<slug>.md`, "Run-off, barriers and trees".

**Data** (open only; no Google, Bing or Esri). Tools in `tools/real_tracks_data/` (widths venv on E:, at most 8
processes):

```powershell
python tools\real_tracks_data\fetch_pointcloud.py <slug> --corridor 450   # the LAZ tiles under the whole terrain box
py tools\real_tracks_data\fetch_osm_runoff.py <slug>                      # OSM woods, scrub, trees, sand/shingle, barriers
python tools\real_tracks_data\lidar_raster.py <slug>                      # 1 m rasters: DTM, DSM, canopy, objects, intensity
python tools\real_tracks_data\measure_runoff.py <slug> --plots            # runoff/<slug>.json, vegetation/<slug>.json, overlays
py tools\real_tracks_data\apply_runoff.py <slug>                          # into the committed sample (the importer does it too)
```

- **Rasters** (`lidar_raster.py`, `.local\realtracks\<slug>\runoff\raster.npz`): the EA point clouds (Navarre: the
  2024 flight with RGBI) over the routes' box ± 450 m at 1 m: ground (class 2; Navarre also 11, its road class),
  surface, canopy (DSM − DTM), the share of returns in the vegetation classes over 2 m and of non-ground returns
  0.3-2.5 m up (barriers, tyre walls, fences), and the ground's last-return intensity divided by each flight line's
  median. Wales: the DataMapWales DTM and DSM COGs (range reads); no intensity.
- **Surfaces**: the intensity has three clear modes on every English circuit: asphalt dark (0.2 of the grass
  level), gravel mid-grey (0.5-0.7), grass bright (1.0-1.4). Thresholds per circuit from its road level and the
  80th percentile 12-45 m out (`calibration` in the run-off file): asphalt below road + 22 % of the range, gravel
  below road + 62 %. Bedford's mid-grey is the airfield's concrete (no gravel traps there: evo, racingcircuits.info).
  Navarre: the 2024 colour (run-off asphalt is paler than the track's: threshold 130 of 255) and OSM's
  `natural=sand raceway=gravel trap` polygons. OSM `natural=shingle|sand`, `surface=gravel|sand` areas are gravel
  everywhere (Donington maps its traps as shingle), `area:highway` and paved areas are tarmac.
- **Profiles**: every 4 m along every route, each side, 0.5 m bins from the measured road edge out to 60 m, cut
  half way to another road. Per profile: tarmac contiguous from the edge (an apron), the first gravel run of 3 m or
  more starting within 15 m (a trap), a barrier candidate, an earth bank where the ground rises 1.2 m within 12 m
  first. Sections are mode-filtered (7) and merged into zones of at least 16 m.
- **Barriers need corroboration (2026-10-25).** Chris drove Cadwell, Donington and Anglesey: long Armco and fence
  runs round the outer edges that aren't there. Cause: every raised LIDAR object 0.35-3 m high up to 60 m out
  (hedges, field and service-road fences, banks) was a barrier, typed Armco by the circuit default, and any OSM fence
  within 25 m made it a debris fence. Now a LIDAR candidate needs a barrier's build (top 0.45-1.6 m above the ground,
  at most 2.5 m deep) within 15 m of the edge or 6 m behind the trap/apron, and an object on an OSM hedge or fence
  line is that hedge or fence. A barrier is kept only with a confidence of at least 0.6 from what corroborates it:
  OSM `guard_rail|tyres|wall|jersey_barrier` 0.9 (walls only near the run-off's end; rails and tyre walls beyond 30 m
  only where the LIDAR sees them too: further out OSM's rails are boundary fencing, Anglesey), a sourced corner
  override 0.8, the back of a measured gravel trap 0.7, a separate part of the same lap (at least 400 m along it)
  within 30 m 0.7; a lone LIDAR line (0.4) is open run-off. A debris fence needs an OSM fence on the barrier line
  (1.5 m) or the evidence. The zone's `confidence` is the barrier's corroboration, its `source` names it.
- **Barrier types** the LIDAR can't tell (it sees a raised line, not its material): `runoff_evidence.json`, per
  circuit a default (`barrier`, `trapBarrier` behind a gravel trap) and corner overrides, each with its source
  (oversteer48, driver61, MSV and circuit news, Geobrugg's Navarra project, Wikipedia, crash reports). A research
  pass found no published barrier colour for any circuit.
- **Vegetation**: canopy over 3 m (and vegetation classes over 2 m where the cloud has them), opened once to drop
  wires and fences; woods = 5 m cells at least half covered, traced (simplified 3 m, at least 400 m²) with their
  mean cover; single trees = canopy peaks over 4 m outside the woods, at least 6 m apart, with their height. OSM
  `natural=tree` nodes are counted against them (Donington: 120 of 385 within 8 m).
- **Spec**: `sketch.imported.runoff` (zones: route, side, fromS/toS, surface, tarmacM, startM/depthM, barrier,
  offsetM, fence, colour, confidence, source), `sketch.imported.trees` and `woodland[].cover` (schema
  `RunoffZone`, `ImportedTree`). `apply_runoff.py` re-projects every zone by its ends' grid positions, so a
  re-imported sample keeps its zones; recipes opt in with `"runoff": "measured"` (all eight).

Hard barriers on the main lap, left / right side, before → after the corroboration rule (2026-10-25; fence = debris
fence): Cadwell Armco 74 / 35 % → 3 / 0 % (fence 16 / 16 → 0); Donington 97 / 74 % → 27 / 18 % (tyre walls behind the
traps, OSM walls; fence 32 / 5 → 0 / 1); Brands Hatch 95 / 76 % → 3 / 8 % (fence 62 / 41 → 0); Oulton Park 86 / 59 % →
1 / 6 %; Snetterton 28 / 67 % → 0 / 20 % (OSM walls along the pit straight and after Riches); Navarra 91 / 78 % →
84 / 66 % (63 OSM guard rails); Anglesey 97 / 13 % → 88 / 10 % (OSM guard rails within 30 m; fence 15 → 5 %); Bedford
none. The table below is the first measurement (2026-10-24).

| Circuit | Data | Gravel / tarmac / grass (main lap, both sides) | Barriers (share of the sides) | Woods / single trees (450 m box) |
|---|---|---|---|---|
| Cadwell Park | EA 2019 | 3 / 0 / 97 % | Armco 56, none 36, bank 8 | 5.0 ha / 1,844 |
| Brands Hatch | EA 2018 | 16 / 1 / 84 % | Armco 72, tyres 15, none 9, bank 3, concrete 1; debris fence 53 % (OSM) | 64.8 ha / 3,203 |
| Oulton Park | EA 2021 | 5 / 1 / 94 % | Armco 70, none 26, concrete 2, bank 2 | 83.0 ha / 1,145 |
| Snetterton | EA 2018/2021 | 9 / 0 / 91 % | none 51, Armco 29, concrete 11, tyres 8 | 19.3 ha / 729 |
| Donington Park | EA 2022 | 22 / 1 / 77 % | Armco 45, tyres 26, concrete 15, none 15; fence 18 % | 31.8 ha / 1,257 |
| Bedford Autodrome | EA 2023 | 0 / 39 / 61 % (concrete) | none 100 (dividers keep black tyre walls) | 27.2 ha / 1,297 |
| Circuito de Navarra | Navarre 2024 RGBI | 12 / 21 / 67 % | Armco 84, none 15; fence 7 % | 0.2 ha / 458 |
| Anglesey | Wales DSM/DTM + OSM | 0 / 0 / 100 % (unmeasured) | Armco 52, none 45, concrete 3 | 0.9 ha / 91 |

**Builder** (`worker/builder/wfbuild/runoff.py`, used by geometry, maps and dressing):

- **Gravel traps**: terrain faces whose centre lies in a trap take `terrain_trap_<env>#slowdown#blend`, the
  terrain's own maps with the R layer swapped for stock gravel (`sandpit:ground_pebbles_03_c`) and no clutter group;
  the blend map is red there (softened one pixel into the grass), the clutter mask and colour tint are cleared. The
  surface is the stock `#slowdown` the sandpit sand banks use (surfacelist: the slowdown tyre model, twice gravel's
  rolling resistance); `#slowdownsand` is in the surface list but bgeometry has no settings for it. The ground dips
  15 cm inside a trap (eased over 2 m). A trap never starts under a kerb or green strip (0.3 m beyond them).
- **Tarmac run-off**: `terrain_apron_<env>#asphalt#blend` with an older, cracked tarmac (`ground_tarmac08f_fields`;
  arid `road_sandpit_tarmac03`) from the edge out to the measured depth; the shoulder takes it too. The AI safe and
  race strips stay on the road; the AI route covers the run-off up to the barrier as before.
- **Barriers**: the wall plan takes each zone's barrier at its measured offset (behind every trap and apron, at least
  2 m beyond the kerb and green strip, steps between zones eased over 24 m and joined at 45°), plus dividers and the
  grid sector's pit wall (concrete); the generic fast-corner and drop-off barriers are off. Dividers on real circuits
  only between separate parts of the lap (the same route at least 400 m along it, `geometry.separate_part`: Cadwell's
  start straight beside the run to the Mountain), never between a chicane's, an esses' or a hairpin's legs or beside
  a branch (2026-10-25: concrete walls in Donington's Fogarty Esses and between Anglesey's Coastal and International
  branches). No spectator barrier or crowd fence of our own: crowds stand behind the real barrier, else at least
  20 m out, past the reset wall. The visible line is smoothed over 12 m in plan and height (within -0.2/+0.15 m of
  the ground), so Armco doesn't kink over terrain steps and zone joins.
  Collision walls (invisible, as before) take the type's surface (`#metal_barrier` Armco, `#global_barrier` tyres,
  `#concrete_barrier`). The visible barrier is our own mesh with stock textures, as stock bases build theirs
  (fields08/forest05 guardrail, forest04 tyre walls), no collision of its own: **Armco** two W-beams
  (`prop_guardrail01_c`) on posts every 2 m; **tyre wall** 1.05 m faced with plain black conveyor belting
  (`prop_tirerubberbarrier_c`); **concrete** (`a_concrete2_c`); **debris fence** 3 m of chain-link
  (`prop_chainlinkfence04_small_c5`, alpha) on the barrier. Split into objects of at most 100 line samples. No
  barrier props and no corner-marker tyre stacks on measured circuits.
- **Placement**: nothing but the grid-sector dressing (gantry, flags, staff, start grandstand) stands in a trap,
  on an apron or between a road edge and its barrier; crowds stand 3 m behind the real barrier (at most 75 m out).
- **Trees**: only the measured ones: the woods' tree spots kept with their cover, every single tree at its place
  scaled to its height, nearest the road first up to the environment's cap; no seeded clusters, park trees or edge
  belt (bushes at wood edges, foliage groups, saplings and rocks stay). The terrain reaches 60 m beyond the roads'
  box, so trees further out aren't placed (the backdrop cards stand there).

**Checks**: `check_scene` (`check_runoff`): the built ground under every zone has its surface (ray cast on the
terrain and shoulders), traps use `#slowdown`, the barrier along every zone is its type and stands behind its trap,
every built type has its visible mesh (no collision), no red/white barrier without a source, no kerb in a trap,
nothing in the run-off, trees only on measured canopy (≥ 98 %) and density near the track (15-150 m: ≥ 60 % of the
wooded 20 m cells have a tree, ≤ 3 % of the open ones); `check_export`: the traps exported with `#slowdown`, the
barrier materials with their stock textures, and the stock scene limits; `geom/tests/test_import_real_track.py`:
every recipe's zones on their routes, both sides covered, traps ≥ 3 m deep and in front of their barrier, every
hard barrier corroborated (confidence ≥ 0.6, source OSM / trap / divider / evidence), debris fences only from OSM
or the evidence; `check_scene`: real circuits divide only separate parts of the lap.

## Pits, paddocks, bridges and gantries (2026-10-24)

Every circuit now has its real pit lane and pit wall, pit buildings, paddock, grandstands (where mapped), start
lights, bridges and gantries, at their real positions, from open data only (OSM, LIDAR, the circuits' own maps; no
Google, Bing or Esri).

**Data** (`tools/real_tracks/pits/<slug>.json`, hand-written: which OSM ways, the lane's width and mode, gantries
with sources; `tools/real_tracks/pits/derived/<slug>.json`, generated, recipe grid coordinates):

```powershell
py tools\real_tracks_data\fetch_pits_osm.py <slug> ...          # one polite Overpass query per circuit (cached osm-pits.json)
E:\Source\wreckfest\.local\widths-venv\Scripts\python tools\real_tracks_data\pits.py <slug> --plots   # derived + .local\audit\<slug>\pits-*.png
E:\Source\wreckfest\.local\widths-venv\Scripts\python tools\real_tracks_data\pits_overhead.py <slug>  # structures over the roads (LIDAR)
py tools\real_tracks_data\apply_pits.py <slug> ...              # into the committed sample (a re-import does the same)
```

- **Heights over the ground** (`pits_lidar.py`): nDSM = the highest LIDAR return per 1 m cell minus the importer's
  DTM, from the cached point clouds (EA NLP; Navarre 2024, whose ellipsoidal heights are levelled on the median
  ground) or, for Anglesey, the Welsh DSM COG (DataMapWales, range requests). Building heights are the 85th percentile
  inside the footprint (OSM `height` / levels where the LIDAR sees nothing). `pits_overhead.py` finds anything that
  spans the road's width more than 3-4 m up: every bridge and footbridge below came from it or from OSM `bridge=*`
  ways crossing the raceway, and both agree where both exist. Start-light gantries are too thin for 1.8 points/m²
  (none shows in the LIDAR); they stand on the start line from the recipes' sources.
- **Pit lane:** OSM's `Pit Lane` raceway way, oriented in the driving direction, smoothed; OSM usually maps it near the
  track-side edge, so its middle part moves sideways to `centreFromTrackM` (between the track's edge and the garage
  fronts), fading in over `taperM` from the merges. Widths are estimates from the gap between the track's edge and
  the garages (no published or measured figures). **Pit wall:** 1.5 m off the lane's edge towards the track (midway
  where the gap is under 3 m), wherever the edges are at least 1.2 m apart: continuous from the entry merge to the
  exit merge. OSM barriers lie within 3 m of it over 57-87 % of its length where OSM maps them.
- `shortCutM` (how much shorter the lane is than the track between its ends) decides whether a lane could be
  drivable.

**Build** (`worker/builder/wfbuild/pits.py`; the importer writes `sketch.imported.pits`, schema `ImportedPits`):

- the pit lane: a tarmac strip draped on the terrain (6 cm up, edges sloping down) where the pit wall runs; its
  collision wall (`walls_pit_<id>`, invisible, as the track's walls) plus a row of stock `prop_concrete_element3` on
  the real wall line; **every lane is closed**: the same concrete row and collision across the lane at both ends of
  the wall. On that stretch the track's own barrier on the pit side is off (the pit wall is the containment) and the
  AI route's run-off margin stops 0.8 m in front of the pit wall. The lane is no route of the TrackModel, so it's in
  no AI strip, checkpoint or minimap; no clutter grass grows through it (`paved` zones).
- buildings: stock pit garages (`prop_tarmac2_pitstop_part2`, roller doors towards the lane), race control / media
  (`prop_startbuilding_fields14`), hospitality and others (`prop_start_building_forest11/09`), paddock halls
  (`prop_tarmac2_tenthall`, its 35 m side along the row), tiled along each footprint's long side, 3 % apart, and
  scaled uniformly to the real height (0.55-1.4), front on the footprint's front edge (moved back where OSM's
  footprint reaches the lane); grandstands (`prop_start_grandstand1`; not `3g`, whose front boards are two strips of
  adverts 13 mm apart that flicker) the same; paddock: trucks, tents and crew buses spread over the real paddock (at
  most 45 per area). These replace the generic grandstand and paddock beside the line.
- no shared faces (2026-10-26): the pit wall's `prop_barrier8` elements stand end to end 4 cm apart (they overlapped
  by 6-12 cm, flickering along every pit wall), ad fence panels the same along the line, none within 8 m of a
  grandstand or building (their own boards); `check_scene` fails on any two props with a vertical face on one plane
  (`footprint.coplanar`, measured stock extents in `footprint.EXTENTS`).
- start lights: a stock cantilever (`prop_forest5_startlight01`: a pole and an arm 7.2 m up) on the pit wall at the
  real line, the arm over the track (the generated tracks' banner gantry hangs only 4 m over the road at scale 1);
  bridges, footbridges and the Navarra gantry: `prop_bridge03_green` across the road at the real sM, scaled so its
  inner pillars stand outside the road, its walls and any pit lane, and its deck at least 5 m over the road.
- the dressing planner reserves all of it first (occupancy, and no-go discs over the buildings so the start camera
  keeps out); `info.pits` in `scenery.json` reports it.
- checks (`worker/builder/tests/check_pits.py`, from `check_scene`; `check_export`): lane and wall exist, the wall's
  collision and concrete row continuous along the wall, closed at both ends; no AI strip of any layout on the lane;
  the AI run-off stops in front of it; buildings, grandstands and paddock props off every road and off the lane; each
  gantry within 3 m of its real sM (start lights 15 m) and at least 5 m over the road with its legs off the pits;
  each layout's start camera neither inside nor looking through a pit building; every pit prop exported as a
  resolving stock subscene; scene limits as before.

| Circuit | Pit lane (OSM) | Mode, why | Pit wall | Buildings | Paddock | Start lights, bridges, gantries |
|---|---|---|---|---|---|---|
| Cadwell Park | 117976650, right of the start straight, 7 m, s 3355 → 88 | closed: narrow, squeezed between the start straight and the run to the Mountain | s 3363-76, 201 m | 2 small pit buildings (OSM, 4-8.5 m) | hand-drawn: beyond the run to the Mountain, reached by OSM's Paddock Entry/Exit roads (10,300 m²) | lights at the line; footbridges at s 260 (before Coppice) and 2466 (Woodland), LIDAR only (8.1-8.2 m tops); Club Hairpin Grandstand |
| Brands Hatch | 4906930 "Pit Lane", inside Brabham Straight, 9.5 m, s 3559 → 140 | closed: entry on the exit of Clark Curve; 6 m shorter than the track | s 3571-82, 406 m (OSM barrier within 3 m: 87 %) | Pit Garages (223 m), Media Center, Race Control, Brabham Stewart Hospitality Suites, Directors' Terrace | GP Paddock, Lower Paddock (OSM) | lights at the **start** line 19 m past the finish line (driver61.com; BARC: lines ~20 m apart); Hailwood Hill covered footbridge (OSM 842276417, s 532, top 7.7 m); service bridges at s 1725 and 3316 (OSM, LIDAR tops 5.4 / 4.4-5.8 m); 5 grandstands (OSM: Pit Straight, Desiré Wilson, Paddock Hill, Hailwoods Hill, one unnamed) |
| Oulton Park | 721714385 "Pit Lane", 10 m, s 4200 → 366 | closed: 15 m shorter than the track (inside the kink after Deer Leap); the entry under a footbridge on a fast curve | s 4208-358, 456 m | pit garages (147 m), 3 small buildings, paddock building | OSM "Paddock" area (38,900 m²) | lights at the line; footbridges at Clay Hill (OSM 108784542, s 3071) and before Deer Leap (LIDAR, s 4184, over the track and the pit entry); Warwick Bridge (OSM 108809456 + man_made=bridge, s 3686, deck top 4.4-6.5 m) |
| Snetterton | 145755934 "Pit Lane", beside Senna Straight, 10 m, s 4492 → 274 | closed for now; **could be drivable** (straight, parallel, no short cut) but a car in it would miss the finish checkpoint and the barrier plan has no merge gaps yet | s 4612-264, 438 m | pit garages (150 m), 2 small buildings, Tyrells | 3 OSM parking areas behind the pits | lights at the line; service bridge at Brundle (OSM 98718314, s 3324, deck 5.2-7.0 m) |
| Donington Park | 90102737 "Pit Lane", inside the Wheatcroft Straight, 10 m, s 3850 → 246 | closed for now; **could be drivable** (as Snetterton; 4 m longer than the track) | s 3858-234, 370 m (OSM barrier 79 %) | Pit Garages 1-38 (181 m), Race Control / Media Centre, its entrance, 750 Motor Club, Goddards Hospitality, Redgate Suites, Scrutineering Bay, a garage block | hand-drawn: behind the pit garages, inside the Melbourne loop | lights at the line; footbridge 80 m before the line (LIDAR, s 3914, top 8.8 m); Hollywood Grandstand (OSM bleachers) |
| Bedford Autodrome | 27782783 "pit lane", 10 m, s 5577 → 116 | closed: it runs inside the corner before the line, 19 m shorter than the track | s 5583-98, 383 m | pit building (76 m) and 2 outbuildings | hand-drawn: the apron behind the pit building | lights at the line; nothing over the roads (LIDAR) |
| Circuito de Navarra | 195101700 "Pit Lane", 12 m, from Turn 15 to past the line | closed: it bypasses the Turn 17 hairpin (225 m shorter) | s 3442-471, 1,123 m | pit building (281 m), race control and 2 buildings across the straight | OSM parking behind the pits (32,600 m²) | lights at the line; a gantry over the main straight at s 156 (LIDAR, top 6.8 m) |
| Anglesey | 264840723 "Pit Lane", 10 m, s 3101 → 272 | closed: it runs inside the last corners, 43 m shorter than the track | s 3129-262, 390 m | 2 garage blocks, Race Control & Administration, Scrutineering | OSM Paddock, Parc Ferme, 2 Grass Paddocks | lights at the line (OSM "Start/Finish"); no bridge (Welsh DSM) |

Overlays: `.local\audit\<slug>\pits-overview.png`, `pits-pit-entry.png`, `pits-pit-exit.png`, `pits-gantry-<k>.png`,
`pits-grandstands.png` (nDSM under the roads, OSM, the derived lane, wall, buildings, paddocks, gantries).

**Uncertain:** pit lane widths (estimates); Brands Hatch's start line (driver61.com's coordinates; whether the lights
are on that line or the finish line); the footbridges seen only in the LIDAR (Cadwell Park, Oulton before Deer Leap,
Donington) are real structures over the road but unnamed; Navarra's gantry may be an advertising gantry or a
footbridge; heights of buildings LIDAR can't see under trees; the stock props are generic (no circuit's own
architecture).

## Data sources and attribution

| Data | Source | Licence | Attribution (in the spec's `sketch.imported.source`) |
|---|---|---|---|
| Layout (raceway ways), woodland polygons | OpenStreetMap via the Overpass API | ODbL 1.0 | © OpenStreetMap contributors |
| Heights, camber, terrain (Wales) | Welsh Government / NRW LIDAR composite DTM, 1 m (COG on DataMapWales) | Open Government Licence v3.0 | Contains Natural Resources Wales information © Natural Resources Wales and database right |
| Heights, camber, terrain | Environment Agency LIDAR Composite DTM, 1 m (bare earth; WCS `environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs`) | Open Government Licence v3.0 | © Environment Agency copyright and/or database right 2022 |
| Heights, camber, terrain (Spain) | IGN MDT05, 5 m, from the PNOA LIDAR (WCS `servicios.idee.es/wcs-inspire/mdt`, coverage `Elevacion25830_5`, ETRS89 / UTM 30N); no track uses it since 2026-10-24 | CC BY 4.0 | MDT05 © Instituto Geográfico Nacional de España (IGN), derived from PNOA LIDAR |
| Heights, camber, terrain (Navarre) | Gobierno de Navarra MDT 2 m, 2024 LIDAR (IDENA WCS, coverage `IDENA.WCS__ELEVAC_Ras_MDT_2M`, ETRS89 / UTM 30N) | CC BY 4.0 | Servicio proporcionado por el Gobierno de Navarra (IDENA) |
| Widths (England) | Environment Agency National LIDAR Programme point cloud (LAZ, 5 km tiles; DEFRA survey catalogue) | Open Government Licence v3.0 | Contains Environment Agency information (c) Environment Agency and/or database right |
| Widths (Navarre) | Gobierno de Navarra LiDAR 2024 point cloud with RGBI (LAZ, 1 km tiles, filescartografia.navarra.es) | CC BY 4.0 | Servicio proporcionado por el Gobierno de Navarra (IDENA), LiDAR 2024 |
| Run-off, barriers, trees | The same EA / Navarre point clouds; Wales: Welsh Government / NRW LIDAR DSM and DTM 1 m (DataMapWales COGs); OSM run-off, barrier and woodland features; barrier types from published guides (`runoff_evidence.json`) | OGL v3.0 / CC BY 4.0 / ODbL 1.0 | appended to `sketch.imported.source` by `apply_runoff.py` |
| Pits: lanes, buildings, paddocks, grandstands, bridges | OpenStreetMap via the Overpass API (`fetch_pits_osm.py`) | ODbL 1.0 | © OpenStreetMap contributors |
| Pits: heights of buildings and structures over the road | the point clouds above (England, Navarre); Welsh Government / NRW LIDAR composite DSM 1 m (DataMapWales COG) for Anglesey | OGL v3 / CC BY 4.0 | as above; Contains Natural Resources Wales information © Natural Resources Wales and database right |

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
| Width | 9.5 m (recipe; measured 7.8 m median since 2026-10-24, "Widths") | narrow for cars |
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
| Lap (built) / published | 3,286.8 / 3,380 m (−2.8 %; Bus Stop sharpened 2026-10-24) | 2,463.4 / 2,494 m (−1.2 %) | 1,898.9 / 1,931 m (−1.7 %) | 1,272.4 / 1,287 m (−1.1 %) |
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
| Lap (built) / published | 3,999.4 / 4,020 m (−0.5 %; Fogarty Esses sharpened, re-imported 2026-10-24) | 3,182.5 / 3,186 m (−0.1 %; Wikipedia 3,149 m: +1.1 %) |
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
- **Width:** not published; 11 m (`widthNote`) until 2026-10-24, now measured ("Widths": median 11.5 m).
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
| Lap (built) / published | 5,880.8 / 6,110 m (−3.8 %) | 4,585.3 / 4,500 m (+1.9 %) | 2,798.4 / 3,000 m (−6.7 %) | 3,595.3 / 3,700 m (−2.8 %; junctions filleted 2026-10-24) |
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
- **Width:** not published; 12 m until 2026-10-24, now measured ("Widths": median 12.5 m).
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
| Lap (built) / published | 4,280.0 / 4,313 m (−0.8 %) | 2,637.4 / 2,693 m (−2.1 %) | 1,349.3 / 1,391 m (−3.0 %; OSM's hook is coarse, `lapLengthNote`) |
| Direction | clockwise (racingcircuits.info arrows; MSV's 11 right / 6 left fit only clockwise) | clockwise | clockwise |
| Start/finish | main straight opposite the middle of the pit building, 263 m from T17 (MSV map line; OSM pit building 121-402 m along the straight; racingcircuits.info draws its flag ~160 m further on) | shares the Main Track line | own: the T10-T11 straight 70 m after T10 (racingcircuits.info Short flag on that straight; placed where the 24-car grid stays under 4 %) |

Corner labels are racingcircuits.info's turn numbers ("Turn 1" … "Turn 17", `cornerLabels` at OSM nodes); Navarra's
corners have no published names. The OSM lap has exactly MSV's 11 right and 6 left corners.

- **Heights (since the 2026-10-24 audit, `real-tracks/navarra.md`):** the Gobierno de Navarra's 2 m DTM from the
  2024 LIDAR (IDENA WCS, CC BY 4.0, `"dtm": "es-navarra"`), in UTM 30N. Road heights 427-448 m (range 20.3 m), climb
  42 m per lap, +6.8 % / −9.6 % over 10 m, camber up to 3.0° (p95 1.4°; T11 2.4° into the hairpin); fit to the DTM
  0.019 m. The line is used as mapped (the orthophoto puts it mid-road; registration only added wobble). Until then
  the IGN MDT05, which the IDEE WCS serves in whole metres: its 1 m terraces made every straight wavy, gave the
  main straight a false 4° camber, and its older flight showed the 2024 extension (T11-T14) as the ground before it
  was built (the "3 m hump", 7.4° camber and −13.5 % grade were there, not on the back straight).
- **Width:** published 12 m, 15 m on the main straight; measured since 2026-10-24 ("Widths": 12.0 m, 14-15 m on the main straight).
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
  2024 extension (T11-T14) on the 2024 heights, how the arid ground and sandpit textures read on tarmac.
