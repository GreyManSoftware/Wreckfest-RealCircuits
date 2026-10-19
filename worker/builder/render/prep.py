"""Host-side helper for the comparison renders (reads the game install read-only; writes only under OUT).

Needs a Python with ``lz4`` and ``Pillow`` (the worker image's Blender Python has Pillow but not lz4).

    python prep.py scenes OUT data/art/levels/fields/fields13_1/fields13_base.scne ...   # -> OUT/raw/<name>.raw
    python prep.py props OUT scenery.json [more.json|names.txt ...]                       # -> OUT/props/<prop>.scne.raw
    python prep.py textures OUT needed.json                                               # -> OUT/tex/<data path>.png

Stock .scne/.bmap files are lz4-compressed bags (worker/docs/visual-assets.md): a format dword, the tag, a dword,
then blocks of u32 size + lz4 block; a block that fails alone decodes with the previous 64 KB as dictionary.
"""
from __future__ import annotations

import io
import json
import os
import struct
import sys

import lz4.block
from PIL import Image

GAME = os.environ.get("WRECKFEST_DIR", r"D:\SteamLibrary\steamapps\common\Wreckfest")


def unbag(d: bytes) -> bytes:
    fmt, _tag, _x = struct.unpack_from("<I4sI", d, 0)
    if fmt != 4:
        return d[12:] if fmt == 1 else d
    pos, out = 12, bytearray()
    while pos < len(d):
        (cs,) = struct.unpack_from("<I", d, pos)
        pos += 4
        blk = d[pos:pos + cs]
        pos += cs
        try:
            out += lz4.block.decompress(blk, uncompressed_size=1 << 22)
        except Exception:  # noqa: BLE001
            out += lz4.block.decompress(blk, uncompressed_size=1 << 22, dict=bytes(out[-65536:]))
    return bytes(out)


def game(rel: str) -> str:
    return os.path.join(GAME, *rel.replace("\\", "/").split("/"))


def decompress(rel: str, dst: str) -> bool:
    src = game(rel)
    if not os.path.isfile(src):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    data = unbag(open(src, "rb").read())
    with open(dst, "wb") as f:  # Toolbox's importer reads a *.raw as the bare payload
        f.write(data)
    return True


def load_image(rel: str) -> Image.Image | None:
    stem = rel[:-4] if rel.lower().endswith((".tga", ".png")) else rel
    src = game(stem + ".bmap")
    if not os.path.isfile(src):
        return None
    raw = unbag(open(src, "rb").read())
    i = raw.find(b"DDS ")
    if i >= 0:
        im = Image.open(io.BytesIO(raw[i:]))
        im.load()
        return im
    for k in range(0, 512):
        try:
            im = Image.open(io.BytesIO(raw[k:]), formats=["TGA"])
            im.load()
            return im
        except Exception:  # noqa: BLE001
            continue
    return None


def main() -> None:
    cmd, out = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    if cmd == "scenes":
        for rel in args:
            name = os.path.basename(rel) + ".raw"
            print(rel, decompress(rel, os.path.join(out, "raw", name)))
    elif cmd == "props":
        names: set[str] = set()
        for a in args:
            if a.endswith(".json"):
                d = json.load(open(a, encoding="utf-8"))
                if "placements" in d:
                    names |= {p["asset"] for p in d["placements"]}
                else:
                    names |= set(d.get("props", []))
            else:
                names |= {x.strip() for x in open(a, encoding="utf-8") if x.strip()}
        ok = 0
        for n in sorted(names):
            dst = os.path.join(out, "props", n + ".scne.raw")
            if os.path.isfile(dst) or decompress(f"data/art/objects/{n}.scne", dst):
                ok += 1
            else:
                print("missing prop", n)
        print(f"props: {ok}/{len(names)}")
    elif cmd == "textures":
        need = json.load(open(args[0], encoding="utf-8"))
        done = 0
        for rel in sorted(set(need)):
            rel = rel.replace("\\", "/")
            if rel.startswith("data/track/"):
                continue  # generated maps: the renderer reads the builder's TGA
            stem = rel.rsplit(".", 1)[0]
            dst = os.path.join(out, "tex", *(stem + ".png").split("/"))
            if os.path.isfile(dst):
                done += 1
                continue
            im = load_image(rel)
            if im is None:
                print("missing texture", rel)
                continue
            im.thumbnail((1024, 1024))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            im.save(dst)
            done += 1
        print(f"textures: {done}/{len(set(need))}")


if __name__ == "__main__":
    main()
