# Circuito de Navarra (`navarra`)

Main Track, Medium and Short. Build, sources and layouts: `docs/real-tracks.md`, "Circuito de Navarra". Recipe
`tools/real_tracks/navarra.json`, sample `geom/tests/samples/navarra.json`, raw data and report in
`.local\realtracks\navarra\`.

## Audit 2026-10-24

Plots (curvature, height, 10/50 m grade, vertical curvature, camber, straight ripple and wobble against distance,
with the turn labels; red is the build before the audit): `.local\audit\navarra\navarra-{maintrack,medium,short}.png`
(`python tools/audit_real_track.py`). Orthophoto and hillshade checks: `.local\audit\navarra\ortho-*.png`,
`shade-*.png`. Renders: `.local\renders\audit\navarra-{before,after}-*.png`.

| Issue | Evidence | Fix |
|---|---|---|
| **Wavy straights (Chris)**: vertical, not lateral | The IDEE WCS serves the IGN MDT05 as whole metres (its GeoTIFF is 16-bit integers; 37 % of the cells we cached are exact integers, against 1 % for a LIDAR DTM in cm). Gentle slopes became 1 m terraces 40-50 m apart: on the T8-T9 back straight the road sat on plateaus at 15.000, 14.000, 13.000 m … with 1 m risers over ~15 m, so even after the 10 m smoothing the grade swung 0.3 ↔ 3.4 % every ~55 m (grade ripple RMS on straights 0.53 %). The lines themselves are straight (lateral wobble on straights ≤ 0.15 m; the OSM line is used as mapped). | Heights from the Gobierno de Navarra's own DTM: 2 m, from the 2024 LIDAR flight (5 points/m², centimetre heights, IDENA WCS, CC BY 4.0; `"dtm": "es-navarra"`). Ripple RMS 0.11 %; the back straight is one steady −1.7 % descent. |
| The "3 m hump on the back straight" and the 9-13 % grades | Not on the T8-T9 straight: the steepest 10 m grades (+9.3 / −13.5 %) and a 98 m-radius crest were between T12 and T13, on the 2024 extension, which the MDT05's older flight shows as the ground before it was built (up to 2.4 m off the 2024 DTM). | 2024 DTM: +6.8 / −9.6 % (McLeans-like climb out of T5, the drop from T11 to T12), tightest crest 318 m. The hillshade of the 2024 DTM shows the extension's road where OSM maps it. |
| False 4° camber on the main straight, 7.4° "banking" between T12 and T13 | The terraces beside the road tilted the cross-road plane; the extension's slope was the hillside before construction. | 2024 DTM: the main straight 1.1-1.3° (≈ 2 % crossfall), camber ≤ 3.0° anywhere (p95 1.4°), always into the corner; T11 banked 2.4° into the left hairpin. |
| Fit to the ground | centre RMS 0.13 m (max 0.7 m) against the MDT05 | 0.019 m (inner edges 0.028 m) against the 2 m DTM; a plane across the inner 80 % of the road fits within 7 mm on average. |
| Registration | With 3-6 m allowed on the 2 m DTM the line moves at most 2 m (Medium's link up to 3.9 m) and gains 0.1 m of wobble for 7 mm better fit. | Kept as mapped (`registrationMaxShiftM` 0, note updated). The PNOA orthophoto puts the OSM line in the middle of the asphalt on the main, back and T15-T16 straights. |
| Width | Published at the 2010 opening (autofacil.es, motorpasion.com): 12 m, 15 m on the main straight; the orthophoto shows ~13.5 m between the edge lines on the back straight. | 12 m kept (one width per track); the main straight is 3 m narrower than real. `widthNote` updated. |
| Short's hook ramp | The hook climbs 1.6 m in ~15 m onto the T7-T8 road (13 % over 10 m, a 93 m dip): the 2024 DTM shows it across the whole road width. | Kept (real per the DTM). Generic fix found on the way: a branch's ends now take the main road's height and camber, faded over ≥ 30 m (`join_blend`). |
| Lap lengths | Main 4,280 m (−0.8 % vs 4,313), Medium 2,637 m (−2.1 % vs 2,693), Short 1,348 m (−3.1 % vs 1,391). OSM tags the lap "approximate drawing"; the 2010 lap was 3,933 m and its Short 1,332 m. | Unchanged (the DTM doesn't change the plan). Short keeps its 3.5 % `lapLengthNote`. |
| Start/finish | Main: MSV map, opposite the middle of the pit building; Short: racingcircuits.info's flag, placed where the grid stays under 4 %. No better source found. | Unchanged. |
| Environment | No OSM woods in the box; the orthophoto shows bare dry ground, scrub and the reservoir south-east of T16 (outside the road, not modelled as a lake). | `arid` unchanged. |

Remaining uncertainty: OSM's lap is an approximate drawing; the 2 m DTM can't show its few-metre errors on flat
asphalt (the orthophoto spot checks agree with it), the hook's ramp (in the DTM, maybe still under construction in
the 2024 flight) and the T5-T6 curvature ripples (OSM nodes; no orthophoto check there).
