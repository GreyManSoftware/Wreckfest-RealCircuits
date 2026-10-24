"""Put a circuit's measured run-off, barriers and trees into its TrackSpec (``sketch.imported``).

    py tools/real_tracks_data/apply_runoff.py doningtonpark [...]     # patch the committed sample(s) in place

Reads tools/real_tracks/runoff/<slug>.json and tools/real_tracks/vegetation/<slug>.json (measure_runoff.py; in
the recipe's grid) and writes, in the sample's local frame:

- ``runoff``: every zone, its ends re-projected onto the sample's routes by position (the zone's ``e``/``e2``
  grid points), so a re-imported sample whose line moved a little keeps its zones where they were measured;
- ``woodland``: the measured woods (with ``cover``) instead of OSM's, and ``trees``: single trees, tallest
  first up to the schema's 6000;
- the attribution in ``source``.

The importer calls ``apply`` itself for recipes with ``"runoff": "measured"`` (so a re-import keeps it); the CLI
patches committed samples without re-importing them (the frame comes from import_frame.py's frame.json).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RUNOFF_DIR = ROOT / "tools" / "real_tracks" / "runoff"
VEG_DIR = ROOT / "tools" / "real_tracks" / "vegetation"
MAX_TREES = 6000
MAX_WOODS = 500
MAX_ZONES = 3000
ATTRIBUTION = ("Run-off, barriers and trees: measured from {src} and OpenStreetMap (ODbL), barrier types from "
               "published track guides (tools/real_tracks_data/runoff_evidence.json).")
SHORT_SRC = {"england": "Environment Agency LIDAR point clouds (OGL v3.0)",
             "navarra": "Gobierno de Navarra LiDAR 2024 (CC BY 4.0)",
             "wales": "NRW LIDAR DSM/DTM (OGL v3.0)"}


def _routes(imp: dict) -> dict[str, tuple[np.ndarray, np.ndarray, bool]]:
    out = {}
    for rid, c, closed in ([("main", imp["centreline"], True)] +
                           [(a["id"], a["centreline"], False) for a in imp.get("altLayouts", [])]):
        xy = np.array([[q["x"], q["y"]] for q in c], dtype=np.float64)
        q = np.vstack([xy, xy[:1]]) if closed else xy
        s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(q, axis=0).T))])
        out[rid] = (q, s, closed)
    return out


def _project(q: np.ndarray, s: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    """(s, distance) of point p on the polyline q."""
    a, b = q[:-1], q[1:]
    d = b - a
    L2 = np.maximum((d * d).sum(1), 1e-12)
    t = np.clip(((p - a) * d).sum(1) / L2, 0, 1)
    c = a + d * t[:, None]
    dist = np.hypot(*(c - p).T)
    k = int(np.argmin(dist))
    return float(s[k] + t[k] * np.sqrt(L2[k])), float(dist[k])


def apply(spec: dict, slug: str, frame: dict, region: str = "england", log=print) -> dict:
    """Patch ``spec`` (in place) with the measured run-off and vegetation; returns a small report."""
    imp = spec["sketch"]["imported"]
    centre = np.array([frame["centreE"], frame["centreN"]])
    scale = float(frame["scale"])
    to_local = lambda e: (np.asarray(e, dtype=np.float64) - centre) / scale  # noqa: E731
    rep = {}
    rf = RUNOFF_DIR / f"{slug}.json"
    if rf.exists():
        data = json.loads(rf.read_text(encoding="utf-8"))
        routes = _routes(imp)
        zones, moved = [], []
        for r in data["routes"]:
            if r["id"] not in routes:
                log(f"  runoff: route {r['id']} not in the sample, skipped")
                continue
            q, s, closed = routes[r["id"]]
            L = float(s[-1])
            for z in r["zones"]:
                z = dict(z)
                a, da = _project(q, s, to_local(z.pop("e")))
                b, db = _project(q, s, to_local(z.pop("e2")))
                if da < 15.0 and db < 15.0:
                    step = 4.0 / scale  # e2 is the last section's centre: the zone runs one section past it
                    moved.append(max(abs(a - z["fromS"]), abs(b + step - z["toS"])) if not closed else 0.0)
                    z["fromS"], z["toS"] = round(a, 1), round(b + step, 1)
                if closed:
                    z["fromS"], z["toS"] = round(z["fromS"] % L, 1), round(z["toS"] % L, 1)
                else:
                    z["fromS"], z["toS"] = round(min(max(z["fromS"], 0.0), L), 1), round(min(max(z["toS"], 0.0), L), 1)
                if z.get("offsetM") is None:
                    z.pop("offsetM", None)
                for k in ("tarmacM", "startM", "depthM", "offsetM"):
                    if k in z:
                        z[k] = round(min(max(float(z[k]), 0.0), 60.0), 1)
                z["source"] = z.get("source", "")[:400]
                zones.append({"route": r["id"], **z})
        imp["runoff"] = zones[:MAX_ZONES]
        rep["zones"] = len(imp["runoff"])
        rep["zoneShiftMaxM"] = round(max(moved), 1) if moved else 0.0
    vf = VEG_DIR / f"{slug}.json"
    if vf.exists():
        v = json.loads(vf.read_text(encoding="utf-8"))
        woods = sorted(v["woods"], key=lambda w: -w["areaM2"])[:MAX_WOODS]
        imp["woodland"] = [{"points": [{"x": round(float(x), 1), "y": round(float(y), 1)}
                                       for x, y in to_local(np.array(w["points"]))[:2000]],
                            "cover": round(float(w["cover"]), 2)} for w in woods if len(w["points"]) >= 3]
        trees = sorted(v["trees"], key=lambda t: -t[2])[:MAX_TREES]
        loc = to_local(np.array([t[:2] for t in trees]).reshape(-1, 2))
        imp["trees"] = [{"x": round(float(x), 1), "y": round(float(y), 1), "h": round(min(float(t[2]), 60.0), 1)}
                        for (x, y), t in zip(loc, trees)]
        rep["woods"], rep["trees"] = len(imp["woodland"]), len(imp["trees"])
    if "runoff" in imp or "trees" in imp:
        extra = ATTRIBUTION.format(src=SHORT_SRC.get(region, SHORT_SRC["england"]))
        src = imp["source"]
        if "Run-off, barriers and trees" not in src:
            src = (src + " " + extra)
        imp["source"] = src[:1000]
    return rep


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fetch_pointcloud import REGION, report_path  # noqa: PLC0415

    for slug in (argv if argv is not None else sys.argv[1:]):
        sample = ROOT / "geom" / "tests" / "samples" / f"{slug}.json"
        spec = json.loads(sample.read_text(encoding="utf-8"))
        frame = json.loads(report_path(slug).read_text(encoding="utf-8"))["frame"]
        rep = apply(spec, slug, frame, REGION.get(slug, "england"))
        sample.write_text(json.dumps(spec, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
        print(slug, rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
