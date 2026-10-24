# Bedford Autodrome (`bedfordautodrome`)

GT, SW, West and SEN. Build, sources and layouts: `docs/real-tracks.md`, "Bedford Autodrome". Recipe
`tools/real_tracks/bedfordautodrome.json`, sample `geom/tests/samples/bedfordautodrome.json`.

## Audit 2026-10-24

Plots: `.local\audit\bedfordautodrome\bedfordautodrome-{gt,sw,west,sen}.png`; hillshade of the EA DTM with the
lines: `shade-all.png`. No change to the recipe or the sample.

| Check | Evidence | Verdict |
|---|---|---|
| Corners | GT curvature: Hangar Hairpin L 128° (17 m), T15 L 142° (16 m), T11 L 165° (30 m), the rest 27-95 m; MSV counts 10 L + 8 R and the curvature shows those plus one 28° and one 23° left kink on the straights. The hillshade shows the lines on the paved loops. | OK |
| Straights | OSM used as mapped (no road signal on the airfield): wobble ≤ 0.22 m at two OSM node kinks, grade ripple RMS 0.08 %. | OK |
| Elevation | 82.8-89.5 m (6.7 m over 2.5 km), grades under 2.0 %, crests and dips ≥ 850 m: Bedford is flat, as published (Thurleigh airfield, 83 m). Fit RMS 0.015 m. | OK |
| Camber | ≤ 1.6° (p95 0.9°): airfield crossfall. | OK |
| Lap lengths | GT 5,881 m vs MSV 3.8 mi / 6.11 km (−3.8 %), SW 4,585 vs 4,500 (+1.9 %), West 2,798 vs MSV 1.87 mi / 3.0 km (−6.7 %; lapmeta 2.89 km: −3.2 %), SEN 3,594 vs 3,700 (−2.9 %). MSV's own page (re-read 2026-10-24) still gives these figures and 12 corners (7 L, 5 R) for the West, lapmeta 10. The SW, from the same OSM ways, is over MSV's figure, so OSM isn't uniformly short; the GT/West gaps stay unexplained (MSV's figures may predate the 2015 changes). | Unchanged; the `lapLengthNote`s stand |
| Start/finish, direction | racingcircuits.info flags fitted to OSM; anticlockwise. | OK |
| Width | Not published; the 1 m DTM shows no edge on the flat asphalt. | 12 m kept, unverified |

Uncertain: the GT and West lap-length gaps, the width.
