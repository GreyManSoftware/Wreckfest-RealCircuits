# Donington Park (`doningtonpark`)

Grand Prix and National. Build, sources and layouts: `docs/real-tracks.md`, "Donington Park". Recipe
`tools/real_tracks/doningtonpark.json`, sample `geom/tests/samples/doningtonpark.json`.

## Audit 2026-10-24

Plots: `.local\audit\doningtonpark\doningtonpark-{grandprix,national}.png` (`python tools/audit_real_track.py`). No
change to the recipe or the sample: nothing below needed one.

| Check | Evidence | Verdict |
|---|---|---|
| Corners and handedness | Curvature along the GP lap: Redgate R (R 40 m, 109°), Craner Curves L-R sweep (65 m), Old Hairpin R (45 m), Starkey's Bridge L, Schwantz Curve L-kink, McLeans R (43 m), Coppice R (43 m, 122°), Fogarty Esses L 23 m / R 34 m (OSM `sharpWays`, 17.7 m at the rule radius), Melbourne Hairpin R 176° (20 m), Goddards L 169° (24 m). Matches the MSV map and driver61's guide; nothing invented or rounded off. | OK |
| Straights | Lateral wobble ≤ 0.32 m (registration steps after McLeans and in the Melbourne Loop), grade ripple RMS 0.13 %; no camber flips. | OK (within the new artefact limits) |
| Elevation | 63.0-98.9 m (36 m range; published elevation change ~38 m, f1-fansite), Craner Curves −8.1 % over 10 m down to the Old Hairpin (the lowest point), McLeans +7.4 % up to Coppice (the highest), the Melbourne Loop dipping 3 m. No crest tighter than 573 m, no steps: EA 1 m LIDAR, fit RMS 0.013 m. | OK |
| Camber | ≤ 2.7° (Craner Curves, into the left), p95 1.6°, into the corner throughout. | OK |
| Lap lengths | GP 4,000.9 m vs 4,020 (−0.5 %); National 3,184.0 vs MSV 3,186 (−0.1 %). | OK |
| Start/finish, direction | driver61 coordinates on the Wheatcroft Straight; clockwise per the MSV maps. | OK |
| Width | Not published (MSV, Wikipedia, guides); OSM has no `width`. | 11 m kept, unverified |
| Environment | 18 OSM woods (Donington Hall), crowds at Redgate, Craner, Old Hairpin, Melbourne. | OK |

Uncertain: the width; Schwantz Curve and Starkey's Straight are unnamed in OSM (labels from the guides).
