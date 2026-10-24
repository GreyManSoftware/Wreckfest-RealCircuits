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
