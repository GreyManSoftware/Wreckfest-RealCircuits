"""Write a circuit's pits (tools/real_tracks/pits/derived/<slug>.json) into its committed sample without re-importing
it: ``sketch.imported.pits`` in the sample's local frame (the frame from import_frame.py). A re-import does the same
(import_real_track.load_pits), so this is only a shortcut that keeps the rest of the sample byte for byte.

    py tools/real_tracks_data/apply_pits.py cadwellpark [brandshatch ...]
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


def main(slugs: list[str]) -> int:
    for slug in slugs:
        recipe = json.loads((ROOT / "tools" / "real_tracks" / f"{slug}.json").read_text(encoding="utf-8"))
        fr = json.loads((LOCAL / "realtracks" / slug / "frame.json").read_text(encoding="utf-8"))
        if fr.get("maxDiffVsSampleM", 1.0) > 0.05:
            raise SystemExit(f"{slug}: frame.json doesn't match the committed sample (re-run import_frame.py)")
        f = fr["frame"]
        pits = irt.load_pits(recipe, np.array([f["centreE"], f["centreN"]]), f["scale"])
        path = ROOT / "geom" / "tests" / "samples" / f"{slug}.json"
        spec = json.loads(path.read_text(encoding="utf-8"))
        if pits:
            spec["sketch"]["imported"]["pits"] = pits
        else:
            spec["sketch"]["imported"].pop("pits", None)
        path.write_text(json.dumps(spec, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
        print(slug, "pits" if pits else "no pits", path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
