# Donington Park (`doningtonpark`)

Grand Prix and National. Build, sources and layouts: `docs/real-tracks.md`, "Donington Park". Recipe
`tools/real_tracks/doningtonpark.json`, sample `geom/tests/samples/doningtonpark.json`.

## Audit 2026-10-24

Plots: `.local\audit\doningtonpark\doningtonpark-{grandprix,national}.png` (`python tools/audit_real_track.py`). The
recipe is unchanged; the sample was re-imported for the shared importer changes (smoothed registration, branch joins).

| Check | Evidence | Verdict |
|---|---|---|
| Corners and handedness | Curvature along the GP lap: Redgate R (R 40 m, 109°), Craner Curves L-R sweep (65 m), Old Hairpin R (45 m), Starkey's Bridge L, Schwantz Curve L-kink, McLeans R (43 m), Coppice R (43 m, 122°), Fogarty Esses L 23 m / R 34 m (OSM `sharpWays`, 17.7 m at the rule radius), Melbourne Hairpin R 176° (20 m), Goddards L 169° (24 m). Matches the MSV map and driver61's guide; nothing invented or rounded off. | OK |
| Straights | Lateral wobble ≤ 0.32 m before (registration steps after McLeans and in the Melbourne Loop), 0.27 m after the re-import with audit A's smoothed registration; grade ripple RMS 0.13 %; no camber flips. | OK (within the new artefact limits) |
| Elevation | 63.0-98.9 m (36 m range; published elevation change ~38 m, f1-fansite), Craner Curves −8.1 % over 10 m down to the Old Hairpin (the lowest point), McLeans +7.4 % up to Coppice (the highest), the Melbourne Loop dipping 3 m. No crest tighter than 573 m, no steps: EA 1 m LIDAR, fit RMS 0.013 m. | OK |
| Camber | ≤ 2.7° (Craner Curves, into the left), p95 1.6°, into the corner throughout. | OK |
| Lap lengths | Re-imported 2026-10-24 (audit A's smoothed registration): GP 3,999.4 m vs 4,020 (−0.5 %); National 3,182.5 vs MSV 3,186 (−0.1 %). | OK |
| Start/finish, direction | driver61 coordinates on the Wheatcroft Straight; clockwise per the MSV maps. | OK |
| Width | Not published (MSV, Wikipedia, guides); OSM has no `width`. | 11 m kept, unverified |
| Environment | 18 OSM woods (Donington Hall), crowds at Redgate, Craner, Old Hairpin, Melbourne. | OK |

Uncertain: the width; Schwantz Curve and Starkey's Straight are unnamed in OSM (labels from the guides).

## Width (measured 2026-10-24)

Source: EA National LIDAR Programme point cloud, tile SK4025 (flown 2022-01-04). Method and data: `docs/real-tracks.md`, "Widths (2026-10-24)". Profile `tools/real_tracks/widths/doningtonpark.json`; plots and overlays `.local\audit\doningtonpark\width-*.png`.

| | Value |
|---|---|
| Grand Prix | median 11.5 m, 10.6-17.9 m (p10 10.8, p90 14.7); 88 % confident |
| Narrow | Starkey's Straight 10.7-10.9 m (narrowest, s 2,451), Hollywood 10.7-10.9, Starkey's Bridge 10.8-11.1, Coppice 10.9-11.4 |
| Wide | the Melbourne Loop: Melbourne Hairpin 14.7-17.9 m (widest), Fogarty Esses 11.9-15.7, Goddards 11.7-16.5; Wheatcroft Straight 12.1-13.0 |
| National link (Roberts) | 11.0-13.9 m |
| Line | moved by up to 3.9 m (mean 1.9 m: the registered OSM line ran along one side of the road on Starkey's Straight and the Craner Curves); lap 3,993.3 m (published 4,020 m, −0.7 %) |
| Spec | `options.widthM` 11.5 |

The GP circuit is the widest of the English five, with the Melbourne Loop (built for the 1993 European GP) wider
still.

## Run-off, barriers and trees (2026-10-24)

Measured from open data (method: `docs/real-tracks.md`, "Run-off, barriers and foliage"): EA National LIDAR Programme point cloud (intensity, classes, heights);
OSM run-off, barrier and woodland features; barrier types from `tools/real_tracks_data/runoff_evidence.json`.
Data `tools/real_tracks/runoff/doningtonpark.json`, `tools/real_tracks/vegetation/doningtonpark.json`; overlays
`.local\audit\doningtonpark\runoff-*.png`, `vegetation-overview.png`.

| | Before (builder until 2026-10-24) | Now (measured) |
|---|---|---|
| Run-off | grass everywhere (no traps or aprons); 369 stock barrier props (tyres: red/white tyre heaps prop_tirebarrier02/04) on 26 % / 19 % of the main lap's left / right sides | gravel 22 %, tarmac 1 %, grass 77 % of the main lap's sides; barriers armco 45 %, tyres 26 %, concrete 15 %, none 15 %; debris fence on 18 %; built barrier line on 96 % / 76 % (left / right) |
| Trees | 1316 trees: OSM woods plus seeded park trees and an edge belt | 57 measured woods (31.8 ha in the 450 m box), 1257 single trees, canopy 10.0 % of the box; 614 trees placed (1943 candidates inside the terrain, cap 2600) |
| Look | green verges to red/white tyre heaps at fast corners and crowds; seeded park trees in the open infield | big gravel traps at Redgate (to 47 m), the Old Hairpin, McLeans, Coppice and Goddards with black tyre walls behind them; Armco elsewhere, concrete at Hollywood and the pit straight; grass at the Craner Curves with black tyre walls (no red/white); open infield with the Shrubbery's trees only |

Evidence per corner (main lap; the zone that holds the corner label; offsets from the road edge):

| Corner (sM) | Side | Run-off | Barrier | Source |
|---|---|---|---|---|
| Wheatcroft Straight (48) | left | grass | concrete at 4 m | OSM, LIDAR, circuit default |
| Wheatcroft Straight (48) | right | grass | concrete at 12 m | OSM, LIDAR, circuit default |
| Redgate (394) | left | gravel trap 1-47 m | tyres at 47 m | LIDAR, guide: oversteer48.com/donington-park-corners/ |
| Redgate (394) | right | grass | armco at 41 m | LIDAR, circuit default |
| Hollywood (593) | left | gravel trap 6-14 m | concrete at 15 m + debris fence | LIDAR, guide: motorsport.com BTCC 449431 |
| Hollywood (593) | right | grass | none | LIDAR |
| Craner Curves (879) | left | grass | armco at 58 m | LIDAR, circuit default |
| Craner Curves (879) | right | grass | tyres at 31 m | OSM, LIDAR, guide: paulrivett.com/?p=1842 |
| Old Hairpin (1119) | left | gravel trap 0-33 m | tyres at 34 m | OSM, LIDAR, circuit default |
| Old Hairpin (1119) | right | grass | none | LIDAR, circuit default |
| Starkey's Bridge (1346) | left | grass | tyres at 14 m | OSM, LIDAR |
| Starkey's Bridge (1346) | right | grass | armco at 18 m + debris fence | OSM, LIDAR, guide: oversteer48.com/donington-trackdays/ |
| Schwantz Curve (1550) | left | grass | armco at 14 m + debris fence | LIDAR, circuit default |
| Schwantz Curve (1550) | right | grass | armco at 31 m | LIDAR, circuit default |
| McLeans (1748) | left | gravel trap 0-30 m | tyres at 32 m | LIDAR, circuit default |
| McLeans (1748) | right | grass | none | LIDAR |
| Coppice (2160) | left | gravel trap 0-34 m | tyres at 36 m | OSM, LIDAR, guide: crash.net/f1/news/32712 |
| Coppice (2160) | right | grass | none | LIDAR, circuit default |
| Starkey's Straight (2549) | left | grass | armco at 10 m + debris fence | LIDAR, circuit default |
| Starkey's Straight (2549) | right | grass | armco at 30 m | LIDAR, circuit default |
| Fogarty Esses (2873) | left | tarmac 10 m | armco at 33 m + debris fence | LIDAR, guide: msv.com/news/2024/feb/5 |
| Fogarty Esses (2873) | right | grass | none | LIDAR, guide: msv.com/news/2024/feb/5 |
| Melbourne Hairpin (3338) | left | grass | none | LIDAR |
| Melbourne Hairpin (3338) | right | grass | armco at 20 m | LIDAR, circuit default |
| Goddards (3750) | left | grass | concrete at 20 m | OSM, LIDAR |
| Goddards (3750) | right | grass | none | LIDAR |

Uncertain: barrier materials come from guides at five corners and the circuit default (Armco, tyre walls behind traps) elsewhere; the LIDAR only sees a narrow raised object. Debris fences are OSM fences on the barrier line plus MSV's Fogarty Esses note. The 2022 flight predates MSV's later run-off works. The Melbourne Loop's inside reads as grass/none where the profiles stop half way to the National link.
