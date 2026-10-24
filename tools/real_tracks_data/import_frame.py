"""Recompute a committed sample's local frame (grid centre and scale) by re-running the importer on its cached data.

    py tools/real_tracks_data/import_frame.py cadwellpark

Writes E:\\Source\\wreckfest\\.local\\realtracks\\<slug>\\frame.json and checks that the rebuilt main centreline
matches the committed sample (so the frame is the sample's).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import import_real_track as irt  # noqa: E402

LOCAL = Path(r"E:\Source\wreckfest\.local")


def main(slug: str) -> int:
    recipe = json.loads((ROOT / "tools" / "real_tracks" / f"{slug}.json").read_text(encoding="utf-8"))
    cache = LOCAL / "realtracks" / slug  # where the MSV imports cached their downloads; else the importer default
    if not (cache / "osm.json").exists():
        cache = irt.DEFAULT_CACHE_ROOT / irt.recipe_slug(recipe)
    spec, report = irt.build(recipe, cache, log=lambda m: None)
    sample = json.loads((ROOT / "geom" / "tests" / "samples" / f"{slug}.json").read_text(encoding="utf-8"))
    a = np.array([[q["x"], q["y"]] for q in spec["sketch"]["imported"]["centreline"]])
    b = np.array([[q["x"], q["y"]] for q in sample["sketch"]["imported"]["centreline"]])
    diff = float(np.abs(a - b).max()) if a.shape == b.shape else float("inf")
    out = LOCAL / "realtracks" / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "frame.json").write_text(json.dumps({"frame": report["frame"], "maxDiffVsSampleM": diff}, indent=1))
    print(slug, report["frame"], "max diff vs committed sample", diff)
    return 0 if diff < 0.05 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
