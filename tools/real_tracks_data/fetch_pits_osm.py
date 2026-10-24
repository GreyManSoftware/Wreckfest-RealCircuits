"""Fetch (and cache) the OSM features around a real circuit's pits, paddock and bridges: one polite Overpass request
per circuit over the recipe's bbox (every way and multipolygon, plus tagged nodes), for pits.py.

    py tools/real_tracks_data/fetch_pits_osm.py cadwellpark [brandshatch ...] [--force]

Writes E:\\Source\\wreckfest\\.local\\realtracks\\<slug>\\osm-pits.json (gitignored, never committed). Waits 10 s between
circuits. OpenStreetMap data (c) OpenStreetMap contributors, ODbL 1.0.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(r"E:\Source\wreckfest\.local")
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
UA = {"User-Agent": "WreckfestTrackGen/0.1 (private track importer; pits survey, low volume)"}


def query(bbox: list[float]) -> str:
    s, w, n, e = bbox
    b = f"({s},{w},{n},{e})"
    return (f"[out:json][timeout:180];("
            f"way{b};"
            f'relation["type"="multipolygon"]{b};'
            f'relation["building"]{b};'
            f'node["raceway"]{b};node["man_made"]{b};node["highway"]{b};node["barrier"]{b};'
            f'node["building"]{b};node["amenity"]{b};node["leisure"]{b};node["name"]{b};'
            f");out body geom;")


def fetch(slug: str, force: bool) -> Path:
    recipe = json.loads((ROOT / "tools" / "real_tracks" / f"{slug}.json").read_text(encoding="utf-8"))
    out = LOCAL / "realtracks" / slug / "osm-pits.json"
    if out.exists() and not force:
        print("cached", out)
        return out
    data = urllib.parse.urlencode({"data": query(recipe["bbox"])}).encode()
    last = None
    for url in OVERPASS:
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read()
            json.loads(body)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(body)
            print("fetched", out, len(body), "bytes")
            return out
        except Exception as exc:  # the next mirror
            last = exc
    raise RuntimeError(f"Overpass failed for {slug}: {last}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="+")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    for k, slug in enumerate(a.slugs):
        cached = (LOCAL / "realtracks" / slug / "osm-pits.json").exists() and not a.force
        fetch(slug, a.force)
        if not cached and k < len(a.slugs) - 1:
            time.sleep(10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
