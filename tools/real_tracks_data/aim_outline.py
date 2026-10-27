"""Write a circuit's AiM track outlines to tools/real_tracks/aim/<slug>.json, for the importer's and the tests' route
check (import_real_track.py, "AiM outlines").

    py tools/real_tracks_data/aim_outline.py <slug> <file.ztracks>... --track "<AiM name>" <layout id|none> ["note"] ...

An AiM RaceStudio ``.ztracks`` (from Chris's AiM dash software) is a zip of ``.tkk`` files, one per track, each a run
of tagged chunks: ``<h`` + 4-character tag + uint32 length + 2 bytes, the payload, then a closing tag. ``pts `` holds
12-byte records (latitude and longitude x 1e7 as int32, height in mm; heights are whole metres and the first point,
the start/finish, has none) and ``Vnfo`` the track's name. Each ``--track`` names one track and the layout of the
recipe it traces (``main`` or an ``altLayouts`` id; ``none`` for a layout we don't build, kept for the record with a
note). The frame is the committed sample's (``.local\\realtracks\\<slug>\\frame.json`` from import_frame.py); the
importer keeps it current when it re-imports the sample.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import import_real_track as irt  # noqa: E402

LOCAL = Path(r"E:\Source\wreckfest\.local")


def chunks(b: bytes) -> dict[str, bytes]:
    i, out = 0, {}
    while i + 12 <= len(b) and b[i:i + 2] == b"<h":
        tag = b[i + 2:i + 6].decode("latin1").strip("\x00 ")
        n = struct.unpack_from("<I", b, i + 6)[0]
        out.setdefault(tag, b[i + 12:i + 12 + n])
        i += 12 + n + 8
    return out


def read_ztracks(path: Path) -> dict[str, dict]:
    """Every track in a .ztracks: name -> {file, points [[lat, lon, altM]]}."""
    out = {}
    with zipfile.ZipFile(path) as z:
        for zi in z.infolist():
            c = chunks(z.read(zi))
            if "pts" not in c or "Vnfo" not in c:
                continue
            name = c["Vnfo"][:64].split(b"\x00")[0].decode("latin1")
            p = c["pts"]
            rec = [struct.unpack_from("<iii", p, k * 12) for k in range(len(p) // 12)]
            out[name] = {"file": zi.filename,
                         "points": [[r[0] / 1e7, r[1] / 1e7, round(r[2] / 1000.0, 1)] for r in rec]}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug")
    ap.add_argument("ztracks", nargs="+", type=Path)
    ap.add_argument("--track", nargs="+", action="append", required=True, metavar="ARG",
                    help='"<AiM name>" <layout id|none> ["note"]')
    args = ap.parse_args(argv)
    recipe = json.loads((ROOT / "tools" / "real_tracks" / f"{args.slug}.json").read_text(encoding="utf-8"))
    layouts = {"main"} | {a["id"] for a in recipe.get("altLayouts", [])}
    tracks = {}
    for p in args.ztracks:
        tracks.update(read_ztracks(p))
    frame = json.loads((LOCAL / "realtracks" / args.slug / "frame.json").read_text(encoding="utf-8"))["frame"]
    outlines = []
    for t in args.track:
        name, lay = t[0], (None if t[1] == "none" else t[1])
        if name not in tracks:
            raise SystemExit(f"no track {name!r} in {[str(p) for p in args.ztracks]}: {sorted(tracks)}")
        if lay is not None and lay not in layouts:
            raise SystemExit(f"{args.slug} has no layout {lay!r} ({sorted(layouts)})")
        if lay is None and len(t) < 3:
            raise SystemExit(f"{name}: a track with no layout needs a note saying why")
        pts = tracks[name]["points"]
        o = {"name": name, "file": tracks[name]["file"], "layout": lay}
        if len(t) > 2:
            o["note"] = t[2]
        o["points"] = pts
        outlines.append(o)
        print(f"{name}: {len(pts)} points -> {lay}", file=sys.stderr)
    d = {"description": ("AiM track outlines of " + recipe["name"] + " (tools/real_tracks_data/aim_outline.py; derived "
                         "data): per outline the AiM track name and file, the layout it traces (null: one we don't "
                         "build) and its points [lat, lon, height m] (WGS84; heights whole metres, the first point "
                         "has none). frame: the committed sample's local frame, kept current by the importer."),
         "source": "AiM RaceStudio track database (.ztracks from Chris's AiM dash software)",
         "frame": frame, "outlines": outlines}
    out = irt.AIM_DIR / f"{args.slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(irt.aim_json(d), encoding="utf-8", newline="\n")
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
