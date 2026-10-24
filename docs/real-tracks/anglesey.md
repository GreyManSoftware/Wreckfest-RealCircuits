# Anglesey Circuit (Trac Môn): real-track data

Data phase for rebuilding Anglesey Circuit, Tŷ Croes, Anglesey, as a Wreckfest 1 track with two
layouts, **International** and **Coastal**, on one base scene. Collected 2026-10-19. The build
phase waits for the imported-track schema extension (Cadwell work) to land.

## Outputs

| What | Where |
|---|---|
| Raw downloads (OSM JSON, DTM cuts, COG range cache) | `E:\Source\wreckfest\.local\realtracks\anglesey\raw\` (gitignored) |
| Processed dataset | `E:\Source\wreckfest\.local\realtracks\anglesey\processed\anglesey.json` |
| 4 m terrain grid (north-up float32, NaN = sea) | `...\processed\terrain_4m.npy` (3.2 × 3.2 km) |
| OSM environment summary | `...\processed\environment.json` |
| Quick-look plan + profile per layout | `...\processed\preview_<layout>.png` |
| Scripts (committed) | `tools/real_tracks_data/` |

Rebuild from the cache (no network once cached):

```powershell
py tools\real_tracks_data\fetch_osm.py --out E:\Source\wreckfest\.local\realtracks\anglesey\raw --lat 53.1886 --lon -4.4966
cd tools\real_tracks_data
py fetch_dtm.py --out E:\Source\wreckfest\.local\realtracks\anglesey\raw\dtm_1m --bbox 231700 367100 234900 370300 --level 0
py fetch_dtm.py --out E:\Source\wreckfest\.local\realtracks\anglesey\raw\dtm_8m_12km --bbox 227300 362800 239300 374800 --level 3
py fetch_dtm.py --out E:\Source\wreckfest\.local\realtracks\anglesey\raw\dtm_32m_horizon --bbox 220000 335000 276000 385000 --level 5
py process_track.py anglesey.json
```

- `cog.py` is a stdlib + numpy reader for remote Cloud Optimised GeoTIFFs. It fetches only the 256² tiles in a bbox with HTTP range requests and caches each range. The 1 m site cut took 208 small requests from a 48 GB national file.
- `osgb.py` converts WGS84 to British National Grid with the OS 7-parameter Helmert and the Airy TM. That is accurate to about 2–5 m against OSTN15; pyproj isn't installed.
- `fetch_osm.py` makes two Overpass requests per circuit. overpass-api.de returned 502, so the data came from the kumi.systems mirror (`OVERPASS_URL`).
- `process_track.py` + `anglesey.json` build the layouts from ordered OSM way lists (see below).

## Sources and attribution

- **Track geometry, corner names, land use:** © OpenStreetMap contributors, ODbL 1.0, via Overpass API, fetched 2026-10-19. Raceway ways 892471211–892471229, 108374689–108374691, 264840722–264841296; start/finish node 11458386749.
- **Heights:** Welsh Government / Natural Resources Wales **LIDAR composite DTM, 1 m**, 2020–23. It is the 32-bit COG `wales_dtm_32bit_cog.tif` on DataMapWales, EPSG:27700, heights in m ODN. Contains Natural Resources Wales information © Natural Resources Wales and database right; licensed under the Open Government Licence v3.0. This is the DTM, not the DSM. The 8 m and 32 m grids are the COG's own overviews. No fallback (Copernicus / OS Terrain 50) was needed.
- **Published facts:** Wikipedia "Anglesey Circuit" and racingcircuits.info (lengths and history), Visordown's Anglesey track guide (corner order and character), and lapmeta.com (Coastal clockwise, about 29 m vertical gain; a low-quality aggregator). The circuit's own site (angleseycircuit.co.uk) returned an empty page to the fetcher.

## Layouts

Every OSM raceway way at Trac Môn is `oneway=yes` and drawn in the racing direction, so the layouts are plain way chains.

- **Direction:** both layouts run **clockwise**, which agrees with lapmeta's "Coastal CW" and the Visordown corner order.
- **Start/finish:** OSM node 11458386749 is on the straight between Bus Stop and Target, at local (114.8, −139.6) heading 079.5° (east).
- **Pit lane:** way 264840723 runs from the Bus Stop entry (node 2704893236) to just after Target (node 4796416668). Its entry lies on the International-only part, before the Coastal rejoin.

| | International | Coastal |
|---|---|---|
| Published length | 3.380 km / 2.100 mi, 11 turns | 2.494 km / 1.550 mi, 10 turns |
| OSM centreline (ground m) | **3295.8** (−2.5 %) | **2471.7** (−0.9 %) |
| Height min / max / range | 11.1 / 29.8 / 18.6 m | 11.1 / 29.8 / 18.6 m |
| Total climb per lap | 31.5 m | 26.3 m (lapmeta: about 29 m) |
| Max up grade (10 m / 20 m) | +7.9 % / +7.8 % (s ≈ 1504, climb to Rocket) | +7.9 % / +7.8 % (same place) |
| Max down grade (10 m / 20 m) | −8.1 % / −8.0 % (s ≈ 2076, Seamans into Corkscrew) | **−10.1 %** / −9.9 % (s ≈ 2242, Corkscrew link) |
| Max bank into turn | 5.7° (≈ 10 %) at the Banking | 5.7° at the Banking |
| Crossfall p95 | 3.9° | 5.2° |
| Max off-camber | 1.5° (Bus Stop) | 1.5° (Bus Stop) |
| Min radius | 13.8 m (Rocket) | 13.8 m (Rocket) |
| Nearest sea | 120 m (s ≈ 1814, Rocket–Peel, west side) | same |

How the numbers were derived:

- **Heights:** the 1 m DTM is Gaussian-smoothed (σ 1.5 m), sampled every 2 m along the track, then smoothed along the track (σ 4 m). The smoothed profile is within 6 mm RMS of the raw samples.
- **Crossfall / bank:** taken from the DTM at ±(width/2 − 1) m and smoothed with σ 5 m. Bank is signed so that positive means the inside of the turn is lower.
- **Elevation story:**
  - The lowest part of the lap is School (11–13 m).
  - The track climbs at up to 8 % through Rocket, then on to the highest point (29.8 m) on the crest after Peel.
  - From there it drops about 12 m through Seamans and the Corkscrew.
  - The Coastal link drops another 5.5 m to the Bus Stop (12 m). That is about 17.7 m from the Peel crest to the Bus Stop in about 450 m.
  - The International instead climbs the Tom Pryce Straight from 18 m to 22.7 m to the Hairpin, then runs back down to the Bus Stop.

Uncertainties:

- **Length:** the International is 2.5 % (84 m) short of the published 3.380 km, and the Coastal 0.9 % short.
  - The hairpin and the Tom Pryce straight match the DTM relief, so the OSM line isn't truncated.
  - The published 2.100 mi is probably a rounded figure: 3.296 km is 2.05 mi.
  - If the build must match the published length exactly, scale the curve by 1.0255 for International only, or leave it as surveyed (recommended).
- **Position:**
  - The OSM-to-DTM registration found a shift of (−1.0, −2.0) m, but the cross-section RMS only dropped from 0.011 to 0.010 m. The site is flat across the track, so registration is weak.
  - Expect ±2–3 m absolute plan error. Heights are barely affected because cross-slopes are small.
- **Corner banking:** sources call the Corkscrew "10 % banked". The DTM gives ≤ 3.5° (≈ 6 %) in the Coastal Corkscrew link and about 0° in the International Corkscrew exit. The 10 % (5.7°) shows up at **the Banking**, which fits its name. The measured value has been kept.
- **Width:** OSM tags `width=12` on School Corner, Target and Bus Stop only. The dataset uses 12 m everywhere. The DTM relief doesn't show the asphalt edges clearly enough to measure width per section.
- **Seamans:** not in OSM. From the Visordown guide it is the downhill left at the start of the Corkscrew way, at about s 2080–2120, which is where the Coastal splits off.

### Corners (s = metres from the start/finish line)

| Corner | Turn | International apex s / z | Coastal apex s / z | Notes |
|---|---|---|---|---|
| Target (T1) | L | 174 / 17.1 | 210 / 17.3 | Fast 90° left |
| Banking | R | 482 / 17.5 | 486 / 17.5 | R 23–26 m, **banked 5.7°** |
| Church | R | 834 / 16.4 | 832 / 16.4 | Long, fast; slight drop out |
| School | R | 1216 / 11.8 | 1216 / 11.8 | Long sweep; low point |
| Rocket | L then R | 1560 / 24.7 | 1562 / 24.7 | Uphill; tightest (R 13.8 m) |
| Peel | R | 1864 / 27.8 | 1860 / 27.7 | Exits over the crest (29.8 m) |
| Seamans / Corkscrew | L-R-L | 2190 / 17.5 | 2158 / 18.4 | Downhill, −8 % |
| Corkscrew link (Coastal only) | R | — | 2228 / 16.7 | −10 %, bank 3.5° |
| Tom Pryce Straight (Intl only) | — | 2218–2600 | — | Climbs 17.9 → 22.7 m |
| Hairpin (Intl only) | R | 2656 / 22.8 | — | R ≈ 21 m |
| Bus Stop | L | 3136 / 12.1 | 2354 / 12.5 | Slightly off-camber (−1.4°) |

The full list with in/out heights and the OSM way ids is in `anglesey.json → layouts.*.corners`.

### Shared sections and joins (one base scene)

The layouts share **2352 m**: S/F → Target → Banking → Church → School → Rocket → Peel → Seamans, into the start of the Corkscrew. That is 71 % of the International and 95 % of the Coastal.

| Join | OSM node | Local x, y (m) | International s | Coastal s |
|---|---|---|---|---|
| Split (Corkscrew) | 1243679987 | −79.6, −49.0 | 2190 | 2192 |
| Rejoin (Bus Stop) | 2704893227 | −22.9, −117.4 | 3134 | 2310 |

- **Coastal only:** the 118 m Corkscrew link (way 108374690).
- **International only:** about 944 m. That is the Corkscrew exit, the Tom Pryce Straight, the Hairpin, the return straight (way 108374689) and the Bus Stop entry.
- **Build approach:** build the union as one road network in one base scene, and give each layout its own route, checkpoints and AI line.
- **Other OSM raceways** are in `otherRaceways` and are not used by these layouts:
  - the National loop pieces: School Corner 108374691 and the National pit lane;
  - the Club Hairpin, 264840722;
  - two motocross tracks north of the circuit.

## Dataset format (`anglesey.json`)

- **Frame:** `frame.centreE/N` = BNG 233187, 368750. Local x = E − 233187 (east) and y = N − 368750 (north). z is m ODN. The BNG scale factor at the site is 0.99994; lengths are ground metres.
- **`layouts.<name>.samples`:** parallel arrays every 2 m:
  - `sM`, `x`, `y`, `zM`;
  - `crossfall` (z_left − z_right over the edges, as a fraction);
  - `bankDeg` (+ = into the turn);
  - `curvature` (+ = left);
  - `grade10m`, `widthM`, `osmWay`.
- **`layouts.<name>.stats` / `.corners`:** as in the tables above.
- **`startFinish`, `joins`, `namedNodes`, `otherRaceways`:** as described above.
- **`terrain`:**
  - the 4 m grid file, extent and land height stats (land −2.4 to 42.5 m; 49 % of the 3.2 km square is sea);
  - a link to the 8 m, 12 × 12 km grid in raw;
  - `seaBySector`: distance to the sea every 15° from the site centre.
- **`horizon.profile`:** the highest terrain elevation angle every 1° from a 1.5 m eye at the start line, using the 32 m grid to 45 km with curvature and refraction applied.

## Site and environment

- **Sea:** water lies on three sides.
  - From the site centre it is about 360–520 m away from SE (135°) round through S and W to NW (300°).
  - It is about 0.7–1.2 km away to the NNW, and 1.9 km to the N at Porth Trecastell (Cable Bay, sandy beach).
  - There is no sea to the E within 6 km.
  - The track is closest to the water (120 m) on the west side between Rocket and Peel.
  - The shore is mostly bare rock and scree (OSM `natural=bare_rock` 8.5 ha, `scree`) with small beaches. The DTM shows a rocky foreshore down to −2.4 m.
  - Within the ±2 km Wreckfest bounds, the far ground to the W and S should be **sea with a rocky edge**, not land.
- **Horizon from the start line:**
  - **Snowdonia** fills azimuth 90–140° (E to SE) at 1.1–1.65° elevation, 25–36 km away, with crests up to about 1000 m (Snowdon about 117°).
  - The **Llŷn hills** (Yr Eifl) are at 150–175°, about 1° elevation, 22–25 km away.
  - The **open sea horizon** is at 200–255° (Caernarfon Bay / Irish Sea).
  - To the N and W, near ground 0.4–0.5 km away (the circuit's own bunds and the headland) closes the view at 1–2.5°.
- **Land cover within 2 km (OSM):**
  - farmland 393 ha in many small fields, mostly east and south-east;
  - meadow 86 ha, north;
  - grass 19 ha inside the circuit;
  - residential 12 ha (the edge of Tŷ Croes village, about 2 km east).
  - Wood is only 0.95 ha in two small clumps 500 m north-east; forest 0.13 ha; scrub 0.6 ha.
- **Boundaries:**
  - guard rail 4.3 km (circuit Armco);
  - fences 5.2 km;
  - walls 1.9 km;
  - hedges 0.9 km.
  - Anglesey field boundaries are mostly cloddiau: stone-faced earth banks topped with gorse. OSM under-maps them.
- **Buildings:** about 110, mostly farmsteads (Plas Llangwyfan, Trecastell, Bryn Llywelyn) and the circuit's pit and paddock block.
- **Around the circuit:** a motocross track lies just north of the circuit, and the grass paddocks are inside it.
- **DTM relief:** a continuous earth bund rings the outside of the circuit, and raised banks run along both sides of the Tom Pryce straight.

### Environment brief (for the build)

Aim for an exposed, windswept coastal headland: short green-yellow grass everywhere, low gorse and bramble clumps (yellow-flowered in spring), grey rock showing near the shore, low banks and walls between small fields, a big sky, sea on three sides, and the Snowdonia skyline to the east. Use almost no trees.

Stock assets from `worker/docs/visual-assets.md`:

- **Terrain blend:** the **farmland** palette.
  - G = `ground_grass08_fields13` as the dominant cover.
  - R = `ground_sand_pebbles_fields_02` for run-offs, gravel traps and shore edges.
  - B = `ground_farmfield_01`, used sparingly for the inland fields to the E.
  - Macro: `macro_fields13_ns`.
  - For the colour map, push the grass slightly yellow and desaturated: the colour of wind-burnt pasture, not lush lowland green.
- **Road:** `ground_fields13_tarmac01` / `ground_tarmac05_fields`. Kerbs and run-off: `road_fields07_01` gravel.
- **Clutter:** `fields13` (`foliage_group20`).
  - Keep the grass band.
  - Turn R/B bushes down to small, sparse clusters standing in for gorse.
  - Use no saplings.
- **Shrubs:** `foliage_bush15/16` and `bush_deciduous1..7` in low clusters on the bunds and field banks, as gorse.
- **Rocks:** `prop_rock_large3..5` along the W/S shore band and on the headland outcrops.
- **Trees:** at most a couple of small clumps 500 m or more to the NE (`foliage_x_alder3/4`, `foliage_x_birch5*`).
- **Buildings:** a few farmsteads 1–2 km E/SE (`building_barn1`, `prop_house01`, `prop_building_storage_b`). The pit and paddock block goes on the S/F straight (`prop_storagebuilding_02`, `prop_start_tent*`).
- **Trackside:** Armco on the bunds (`prop_barrier7a/7c_small_colmod`) and tyre stacks at Rocket, the Hairpin and the Bus Stop. Use few or no grandstands: Trac Môn has small banks, not stands.
- **Far ground and horizon:**
  - The far-ground disc should be sea W/S. The stock docs have no water material, so use a flat dark blue-grey plane at z ≈ 0 with a beach or rock strip (`ground_sandbank_large_04` / `ground_sand_pebbles_01`) at the coastline from `terrain_4m.npy`.
  - The horizon ring should be open sky over the sea for 180–300°, and a mountain silhouette for 90–175°.
  - No stock horizon has Snowdonia. Generate a horizon texture from `horizon.profile` (the pipeline already plans `bimage` for generated maps), or use `fields_vistaplane_c5` to the E only, as a stop-gap.

**Avoid:**
- dense forest or background tree cards (`foliage_bgrforest*`, the `forest11` clutter, `foliage_x_spruce*` rows);
- tall hedgerows;
- the urban skyline;
- desert assets;
- lush, heavily flowered meadows.

**Gaps found:**
- no stock low dry-stone wall prop is documented;
- no water or sea material is documented.

Both should be checked in the game's `data/art/objects` and `levels/*/textures` before the build.

## Audit 2026-10-24

Plots: `.local\audit\anglesey\anglesey-{international,coastal,national,club}.png` (`python tools/audit_real_track.py`).
No change to the recipe or the sample.

| Check | Evidence | Verdict |
|---|---|---|
| Corners | International curvature: Target L (75 m), the Banking R 172° (32 m), Church R (68 m), Rocket L-R (15 m / 26 m), Peel R (33 m), the Corkscrew L (36 m), the Hairpin R 174° (24 m), the Bus Stop (`sharpWays`, 13.6 m at its tightest). Handedness as the racingcircuits.info maps, clockwise. | OK |
| Straights | Wales 1 m DTM, registered (shifts −1.5…+0.5 m): wobble ≤ 0.22 m, grade ripple RMS 0.14 %. The one camber "flip" on a straight is the steady change from Target's camber to the Banking's, not flicking. | OK |
| Elevation | 11.1-29.8 m ODN (18.8 m range), the climb to Rocket +8.1 %, Peel (28.8 m) down to the Corkscrew (18.6 m): the 10 m Corkscrew drop at up to −8.2 % (Coastal's link −9.7 %). Crests ≥ 650 m (Coastal ≥ 254 m), no steps; fit RMS 0.013 m. | OK |
| Camber | The Banking 5.7° into the right-hander (its name), Target −1.6° into the left, p95 3.9°. | OK |
| Lap lengths | International 3,286.5 vs 3,380 m (−2.8 %), Coastal −1.2 %, National −1.6 %, Club −1.2 %. OSM `width` 12 m on three ways and the registered line agree with the DTM; the International's extra gap (the Tom Pryce Straight-Hairpin-Bus Stop part) has no source to check against. | Unchanged (within 3 %) |
| Start/finish | International/Coastal: OSM "Start/Finish" node; National: ACU 2012 + map flag; Club: map flag 22 m after the Club Hairpin joins. No better source found for the Club line. | Unchanged, Club estimated |
| Environment | Sea plane below 0 m ODN to the W/S (no-data cells of the Welsh DTM), coastal rocks on the shore, crowds at the Corkscrew, Rocket, Banking. | OK |
