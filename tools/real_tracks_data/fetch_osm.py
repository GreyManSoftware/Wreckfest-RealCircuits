"""Fetch (and cache) the OSM data for a real circuit through Overpass.

Two polite requests per circuit: the raceway ways/relations, and the landuse /
natural / building / wall features in a ~1.5 km radius. Responses are cached;
re-running never re-downloads unless --force is given.

    py tools/real_tracks_data/fetch_osm.py --out E:/Source/wreckfest/.local/realtracks/anglesey/raw \
        --lat 53.1886 --lon -4.4966 --race-r 1500 --env-r 2000
"""
from __future__ import annotations

import argparse
import os
import time
import urllib.parse
import urllib.request

ENDPOINT = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
UA = "WreckfestTrackGen-research/0.1 (personal, low volume)"


def raceway_query(lat, lon, r):
    return f"""[out:json][timeout:120];
(
  way["highway"="raceway"](around:{r},{lat},{lon});
  relation["highway"="raceway"](around:{r},{lat},{lon});
  relation["type"="route"]["route"~"raceway|racetrack|motorsport"](around:{r},{lat},{lon});
  way["leisure"~"track|sports_centre"](around:{r},{lat},{lon});
  relation["leisure"~"track|sports_centre"](around:{r},{lat},{lon});
  way["landuse"="raceway"](around:{r},{lat},{lon});
  node["name"](around:{r},{lat},{lon});
);
out body geom;"""


def env_query(lat, lon, r):
    return f"""[out:json][timeout:180];
(
  way["landuse"](around:{r},{lat},{lon});
  relation["landuse"](around:{r},{lat},{lon});
  way["natural"](around:{r},{lat},{lon});
  relation["natural"](around:{r},{lat},{lon});
  way["building"](around:{r},{lat},{lon});
  way["barrier"](around:{r},{lat},{lon});
  way["waterway"](around:{r},{lat},{lon});
  way["leisure"](around:{r},{lat},{lon});
  way["amenity"="parking"](around:{r},{lat},{lon});
  way["highway"](around:{r},{lat},{lon});
  way["power"="line"](around:{r},{lat},{lon});
  node["natural"~"peak|tree|beach|rock"](around:{r},{lat},{lon});
);
out body geom;"""


def fetch(query: str, path: str, force: bool):
    if os.path.exists(path) and not force:
        print("cached", path)
        return
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(ENDPOINT, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        body = r.read()
    with open(path, "wb") as f:
        f.write(body)
    print("fetched", path, len(body), "bytes")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--race-r", type=int, default=1500)
    ap.add_argument("--env-r", type=int, default=2000)
    ap.add_argument("--only", choices=["race", "env"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.only in (None, "race"):
        fetch(raceway_query(a.lat, a.lon, a.race_r), os.path.join(a.out, "osm_raceway.json"), a.force)
    if a.only is None:
        time.sleep(5)
    if a.only in (None, "env"):
        fetch(env_query(a.lat, a.lon, a.env_r), os.path.join(a.out, "osm_env.json"), a.force)


if __name__ == "__main__":
    main()
