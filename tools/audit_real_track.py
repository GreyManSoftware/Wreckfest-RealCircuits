"""Accuracy-audit plots of an imported real track: curvature, height, grade, camber and the artefact series along
every layout's lap, with the corner labels (2026-10-24 audit).

    python tools/audit_real_track.py geom/tests/samples/navarra.json --out E:\\Source\\wreckfest\\.local\\audit\\navarra
    python tools/audit_real_track.py <sample> --before <older sample> --out <dir>   # overlay an earlier import

Writes ``<slug>-<layout>.png`` per layout and ``<slug>-artefacts.json`` (``profile_artefacts`` per layout). Needs
numpy and Pillow (the system Python has both; geom's venv has no Pillow).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_real_track as irt  # noqa: E402


def layout_laps(spec: dict) -> dict[str, tuple[np.ndarray, list[dict]]]:
    """Each layout's lap as (n x 4 [x, y, z, bank], corners with their s on that lap). An alternative layout is its
    branch plus the main line from where the branch rejoins round to where it leaves (the model's sections do the
    same; this needs no trackgen_geom)."""
    im = spec["sketch"]["imported"]
    main = np.array([[p["x"], p["y"], p["z"], p["bankDeg"]] for p in im["centreline"]])
    n = len(main)
    s_main = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(main[:, :2], axis=0).T))])
    out = {im.get("mainLayoutName", "Main"): (main, list(im["corners"]))}
    for a in im.get("altLayouts", []):
        b = np.array([[p["x"], p["y"], p["z"], p["bankDeg"]] for p in a["centreline"]])
        i0 = int(np.argmin(np.hypot(*(main[:, :2] - b[0, :2]).T)))
        i1 = int(np.argmin(np.hypot(*(main[:, :2] - b[-1, :2]).T)))
        back = [(i1 + k) % n for k in range((i0 - i1) % n + 1)]
        lap = np.vstack([b[1:-1], main[back]])
        sb = float(np.sum(np.hypot(*np.diff(b[:, :2], axis=0).T)))
        corners = []
        for c in im["corners"]:  # main corners on the shared part, shifted onto this lap's s
            i = int(round(c["sM"] / (s_main[-1] / (n - 1)))) % n
            if i in back:
                corners.append({"name": c["name"], "sM": sb + back.index(i) * (s_main[-1] / (n - 1))})
        sf = a.get("startFinish")
        if sf:  # the layout's own line: start the lap there
            k = int(np.argmin(np.hypot(*(lap[:, :2] - [sf["x"], sf["y"]]).T)))
            ds = float(np.mean(np.hypot(*np.diff(lap[:, :2], axis=0).T)))
            lap = np.roll(lap, -k, axis=0)
            total = len(lap) * ds
            corners = [{"name": c["name"], "sM": (c["sM"] - k * ds) % total} for c in corners]
        out[a["name"]] = (lap, sorted(corners, key=lambda c: c["sM"]))
    return out


def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


PANELS = [  # key, title, unit scale, fixed range or None
    ("k", "curvature 1/R (1/m; + left)", 1.0, (-0.08, 0.08)),
    ("z", "height (m above datum)", 1.0, None),
    ("g", "grade over 10 m (line) and 50 m (grey), %", 100.0, None),
    ("kv", "vertical curvature (1/m; - crest), over 10 m", 1.0, (-0.012, 0.012)),
    ("bank", "camber (deg; + left side higher)", 1.0, (-10.0, 10.0)),
    ("ripple", "straights: grade ripple (%) and lateral wobble (m x 10, grey)", 100.0, (-2.0, 2.0)),
]


def plot(lap: np.ndarray, corners: list[dict], title: str, path: Path, before: np.ndarray | None = None) -> dict:
    ser = irt.profile_series(lap[:, :2], lap[:, 2], lap[:, 3])
    old = irt.profile_series(before[:, :2], before[:, 2], before[:, 3]) if before is not None else None
    W, ph, left, top = 2400, 230, 90, 60
    H = top + ph * len(PANELS) + 40
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f, fs = _font(18), _font(14)
    d.text((left, 12), title, fill="black", font=_font(24))
    L = float(ser["s"][-1] + ser["ds"])
    X = lambda s: left + (W - left - 20) * np.asarray(s) / L  # noqa: E731
    for k, (key, name, sc, rng) in enumerate(PANELS):
        y0 = top + k * ph
        y1 = y0 + ph - 30
        series = {"k": [ser["k"]], "z": [ser["z"]], "g": [ser["g10"] * sc, ser["g50"] * sc], "kv": [ser["kv"]],
                  "bank": [ser["bank"]], "ripple": [ser["ripple"] * sc, ser["wobble"] * 10.0]}[key]
        olds = None
        if old is not None:
            olds = {"k": old["k"], "z": old["z"], "g": old["g10"] * sc, "kv": old["kv"], "bank": old["bank"],
                    "ripple": old["ripple"] * sc}[key]
        if rng is None:
            vals = np.concatenate(series + ([olds] if olds is not None else []))
            lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
            pad = 0.05 * (hi - lo + 1e-6)
            rng = (lo - pad, hi + pad)
        Y = lambda v: y1 - (y1 - y0) * (np.clip(np.asarray(v), rng[0], rng[1]) - rng[0]) / (rng[1] - rng[0])  # noqa: E731
        # straights shaded
        st = ser["straight"]
        i = 0
        while i < len(st):
            if st[i]:
                j = i
                while j < len(st) and st[j]:
                    j += 1
                d.rectangle([float(X(ser["s"][i])), y0, float(X(ser["s"][j - 1])), y1], fill=(236, 244, 255))
                i = j
            else:
                i += 1
        d.rectangle([left, y0, W - 20, y1], outline=(160, 160, 160))
        if rng[0] < 0 < rng[1]:
            d.line([left, float(Y(0)), W - 20, float(Y(0))], fill=(200, 200, 200))
        for c in corners:
            x = float(X(c["sM"]))
            d.line([x, y0, x, y1], fill=(225, 225, 225))
            if k == 0:
                d.text((x + 2, y0 + 2), c["name"], fill=(90, 90, 90), font=fs)
        if olds is not None:
            d.line(list(zip(X(old["s"]).tolist(), Y(olds).tolist())), fill=(240, 150, 150), width=2)
        colours = [(20, 60, 160), (150, 150, 150)]
        for m, v in enumerate(series):
            d.line(list(zip(X(ser["s"]).tolist(), Y(v).tolist())), fill=colours[m], width=2 if m == 0 else 1)
        d.text((left + 4, y1 + 4), name + (" (red: before)" if olds is not None else ""), fill="black", font=f)
        d.text((4, y0), f"{rng[1]:.3g}", fill=(90, 90, 90), font=fs)
        d.text((4, y1 - 16), f"{rng[0]:.3g}", fill=(90, 90, 90), font=fs)
    for sm in range(0, int(L) + 1, 250):
        d.text((float(X(sm)) - 10, H - 30), f"{sm}", fill=(90, 90, 90), font=fs)
    img.save(path)
    return irt.profile_artefacts(lap[:, :2], lap[:, 2], lap[:, 3])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sample", help="imported TrackSpec (geom/tests/samples/<slug>.json)")
    ap.add_argument("--before", help="an earlier import of the same track to overlay (red)")
    ap.add_argument("--out", required=True, help="output directory")
    args = ap.parse_args(argv)
    spec = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    old = layout_laps(json.loads(Path(args.before).read_text(encoding="utf-8"))) if args.before else {}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    slug = spec["meta"]["slug"]
    summary = {}
    for name, (lap, corners) in layout_laps(spec).items():
        tag = "".join(ch for ch in name.lower() if ch.isalnum())
        L = float(np.sum(np.hypot(*np.diff(np.vstack([lap[:, :2], lap[:1, :2]]), axis=0).T)))
        summary[name] = plot(lap, corners, f"{spec['meta']['name']} {name}: {L:,.0f} m", out / f"{slug}-{tag}.png",
                             old.get(name, (None,))[0])
        summary[name]["lapM"] = round(L, 1)
    (out / f"{slug}-artefacts.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
