# Snetterton: 300, 200 and 100 (2026-10-20)

One base scene, three layouts (`snetterton_race` = 300, `snetterton_c200`, `snetterton_c100`; events "Snetterton
300 / 200 / 100"; layout ids need a leading letter, hence `c200`/`c100`). Recipe `tools/real_tracks/snetterton.json`
(every source is in it), sample `geom/tests/samples/snetterton.json`, raw data, MSV map PDFs and report in
`E:\Source\wreckfest\.local\realtracks\snetterton\`, zip `E:\WreckfestMods\snetterton.zip`, installed and enabled as
"Snetterton".

## Layouts and sources

The current official layouts are exactly three: MSV's circuit-layouts page (snetterton.co.uk/about/circuit-layouts)
lists the 100, 200 and 300 Circuits; Wikipedia and racingcircuits.info give the same three (2011 to date).

| | 300 | 200 | 100 |
|---|---|---|---|
| Layout source | MSV map "Snetterton 300 Circuit" | MSV map "Snetterton 200 Circuit" | MSV map "Snetterton 100 Circuit" |
| Route | Senna Straight, Riches, Wilson, Palmer, Agostini, Hamilton, Oggies, Williams, Bentley Straight, Brundle, Nelson, Bomb Hole, Coram, Murrays | Riches, Wilson, then right at Chapman (OSM 161975517) onto Bentley Straight: no infield | the infield loop: Palmer, Agostini, Hamilton, Oggies, the foot of Williams, Firmans (OSM 161975518, mapped the other way) back onto the road to Palmer |
| Lap (built) | 4,788.7 m | 3,194.5 m | 1,556.2 m |
| Published | 4,779 m (2.969 mi; Wikipedia, racingcircuits.info; MSV "3 miles"): +0.20 % | 3,219 m (2.000 mi; MSV "2 miles"): −0.76 % | 1,577 m (0.980 mi; MSV "1 mile"): −1.32 % |
| OSM polyline | 4,801 m | 3,202 m | 1,566 m |
| Direction | clockwise: the MSV map arrow by the flag on Senna Straight points west to Riches; driver61.com turn order (1 Riches … 12 Murrays) | clockwise (MSV 200 map) | the 300's infield driven the same way (Palmer → Agostini → Hamilton → Oggies); anticlockwise as a loop on its own (recipe `"clockwise": false`) |
| Start/finish | Senna Straight, OSM node 1717542072 "Start/finish line", 459 m from Murrays: the MSV flag in front of race control; BARC: "start line and finish line is located on the Senna Straight"; driver61 GPS | shares the 300 line and grid | **estimated**: no official source places a 100 line (the MSV 100 map shows only the grey Senna Straight flag, off the loop). The 100 runs while the 200 uses Senna Straight, so it can't share that line; it is put on the infield's only long straight, Palmer to Agostini beside Tyrrells, 112 m after Palmer, with its own 24-car grid. **Chris to confirm.** |
| Grid | 24 slots, max grade 1.5 % | the 300 grid | its own 24 slots after its line |

MSV lists pole on the left for all three; the builder puts pole on the inside of the first corner (Riches, a
right-hander, for the 300/200).

## Numbers (300)

| | Value |
|---|---|
| Height | 40.1–45.9 m above datum (range 5.8 m), climb 14.4 m per lap: flat |
| Steepest (10 m) | +1.6 %, −1.9 % (the 100: −2.3 %) |
| Camber | up to 2.5°, p95 1.4° |
| Fit to the DTM | centre RMS 0.015 m (max 0.06 m), inner edges 0.019 m |
| Registration | lateral shifts −1.5 … 0.0 m; no corner easing (min radius 13.3 m; the Firmans hairpin 11.9 m) |
| Rules | valid: maxGradePct 2.3, maxBankDeg 2.5, grid 1.5 %, min radius 11.9 m |
| Width | 12.0 m (not published; ~11–14 m on imagery) |

## Environment, kerbs, barriers

New environment `airfield` (data-driven, `environments.json`; schema enum, weather families fields14/13/08, web
palette): open short dry grass, only the real OSM woods (4 here) and a light shelter belt at the terrain edge, no
single park trees, ploughed arable patches from ~220 m out, a low horizon with thin distant tree lines. Crowd banks at
Murrays (Jim Russell Stand), Bomb Hole (Oliver Sear Stand), Palmer (Tyrrells) and Riches.

Kerbs from the driver61.com Snetterton 300 guide (OSM has none): Riches exit only (its apex kerbs are steep, "I
wouldn't advise using any of the kerbs"), Wilson, Agostini, Hamilton, Williams, Nelson, Bomb Hole and Murrays apex +
exit (Murrays and Oggies with green), Palmer entry + apex + exit, Brundle and Coram apex; Chapman and Firmans
geometric. Barriers (tyre walls) on at most 29 % of a side; dividers between the infield roads.

Builder tests (`run-builder-tests.ps1 -Specs snetterton`): PASS 98 scene checks, 86 export checks (three layouts,
the 100 with its own line and grid). Base: 1,891 subscenes, 2.76 M vertices with every instance, clutter 5.0 ha.

Renders: `.local\renders\snetterton-{start,riches,bombhole,firmans,oblique}.png`.

**Check in game:** the 100's start line and grid (estimated), the Firmans hairpin (11.9 m) and the AI round it, the
200's Chapman link, the minimap per layout, how open the airfield looks.
