"""Fetch (and cache) the OSM features that describe a real circuit's run-off, barriers and vegetation.

    py tools/real_tracks_data/fetch_osm_runoff.py doningtonpark [--force]

One Overpass request per circuit over the recipe's ``bbox`` grown by 600 m: woods, scrub, tree rows and single
trees; sand, gravel and other surface areas (``natural=sand``, ``surface=*`` areas, ``area:highway``,
``golf=bunker``, ``landuse``); barriers (``barrier=guard_rail|tyres|wall|fence|jersey_barrier|kerb|...``) and
raceway areas. Cached as E:\\Source\\wreckfest\\.local\\realtracks\\<slug>\\osm-runoff.json (raw OSM, gitignored,
never committed); measure_runoff.py reads it.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(r"E:\Source\wreckfest\.local")
ENDPOINT = "https://overpass-api.de/api/interpreter"
UA = "WreckfestTrackGen-research/0.1 (personal, low volume)"


def query(s: float, w: float, n: float, e: float) -> str:
    b = f"({s},{w},{n},{e})"
    return f"""[out:json][timeout:180];
(
  way["natural"~"^(wood|scrub|tree_row|sand|bare_rock|scree|shingle|heath|grassland|wetland|water)$"]{b};
  relation["natural"~"^(wood|scrub|sand|heath|grassland|water)$"]{b};
  node["natural"="tree"]{b};
  way["landuse"]{b};
  relation["landuse"]{b};
  way["surface"]["area"="yes"]{b};
  way["surface"~"gravel|sand|fine_gravel|pebblestone|grass|asphalt|paved|concrete"]["highway"!~"."]{b};
  way["area:highway"]{b};
  way["golf"]{b};
  way["barrier"]{b};
  node["barrier"]{b};
  way["highway"="raceway"]{b};
  way["leisure"~"track|sports_centre"]{b};
  way["man_made"~"embankment|dyke"]{b};
);
out body geom;"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    recipe = json.loads((ROOT / "tools" / "real_tracks" / f"{a.slug}.json").read_text(encoding="utf-8"))
    s, w, n, e = recipe["bbox"]
    dlat, dlon = 600 / 111_000.0, 600 / (111_000.0 * 0.62)
    out = LOCAL / "realtracks" / a.slug / "osm-runoff.json"
    if out.exists() and not a.force:
        print("cached", out)
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    data = urllib.parse.urlencode({"data": query(s - dlat, w - dlon, n + dlat, e + dlon)}).encode()
    req = urllib.request.Request(ENDPOINT, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read()
    out.write_bytes(body)
    print("fetched", out, len(body), "bytes", len(json.loads(body).get("elements", [])), "elements", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
