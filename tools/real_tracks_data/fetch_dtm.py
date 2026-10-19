"""Cut a bounding box out of a remote LIDAR COG and save it as .npy + .json.

    py tools/real_tracks_data/fetch_dtm.py --out E:/.../anglesey/raw/dtm_1m \
        --bbox 231700 367300 234900 370300 --level 0

Default source: Welsh Government / NRW LIDAR composite DTM (1 m, 32-bit COG,
EPSG:27700, Open Government Licence v3) on DataMapWales.
Range responses are cached under <out>/../cog_cache.
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

from cog import RemoteCog

WALES_DTM = "https://dmwproductionblob.blob.core.windows.net/cogs/lidar/wales_dtm_32bit_cog.tif"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output path stem (no extension)")
    ap.add_argument("--bbox", type=float, nargs=4, required=True, metavar=("E0", "N0", "E1", "N1"))
    ap.add_argument("--level", type=int, default=0, help="0 = full res, k = overview k")
    ap.add_argument("--url", default=WALES_DTM)
    a = ap.parse_args()
    if os.path.exists(a.out + ".npy"):
        print("cached", a.out)
        return
    cache = os.path.join(os.path.dirname(os.path.abspath(a.out)), "cog_cache")
    cog = RemoteCog(a.url, cache)
    cog.use_level(a.level)
    arr, xc, yc, res = cog.read_bbox(*a.bbox)
    np.save(a.out + ".npy", arr.astype(np.float32))
    meta = {"source": a.url, "crs": "EPSG:27700", "level": a.level, "res": res,
            "x0_centre": xc, "y0_centre": yc, "rows": arr.shape[0], "cols": arr.shape[1],
            "row_order": "north-to-south", "nodata": "NaN",
            "nan_fraction": float(np.isnan(arr).mean()),
            "min": float(np.nanmin(arr)) if np.isfinite(arr).any() else None,
            "max": float(np.nanmax(arr)) if np.isfinite(arr).any() else None,
            "http_requests": cog.requests}
    with open(a.out + ".json", "w") as f:
        json.dump(meta, f, indent=1)
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
