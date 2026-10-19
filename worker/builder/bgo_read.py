"""Minimal reader for Bugbear .bgo3 files (stdlib only, no Blender).

Reads what Toolbox Mz's exporter (utils/export_bgo.py) and Bugbear's 3ds Max exporter write: materials with
their texture paths, and the object hierarchy (type, parent, name, CustomData, matrix, triangles). Used by the
builder tests to check exported files and to inspect the example mod's layouts.

    py worker/builder/bgo_read.py path/to/file.bgo3 [--tris]

Layout (little endian):
    u32 filelen, "MAIN"
    INFO  u32 size (incl. header), "INFO", u32 777, cstr "BGO", cstr user
    MLST  u32 size (incl. header), "MLST", u32 n, MATC * n
    MATC  u32 size (incl. header), "MATC", 32 bytes, 3f, 3f, 3f, f, f, f, u32, 4 bytes, u32, 4 bytes, cstr name,
          u32 ntex, TEXC * ntex  (TEXC: u32 size, "TEXC", 3 u32 slot/uv/0, 16 f, cstr path)
    HIER  u32 size (excl. header), "HIER", u32 n, objects
    OBJM/OBJD/OBJX  u32 size (excl. header), tag, u32 parent id (0 = none, else 1-based), u32 0, 16 f matrix
          (Y/Z swapped), 16 f pivot matrix, u32 0, u32 3, cstr name, cstr CustomData,
          OBJM: u32 mesh id, GMSH (u32 size excl. header, "GMSH", u32 ntris, u32 nuv, per corner 4 f
          (material id, x, z, y) + nuv * 11 f); OBJX: cstr scene path
"""
from __future__ import annotations

import json
import math
import struct
import sys
from dataclasses import dataclass, field


@dataclass
class Texture:
    slot: int
    uv: int
    path: str


@dataclass
class Material:
    name: str
    textures: list[Texture] = field(default_factory=list)


@dataclass
class BgoObject:
    index: int  # 1-based, as referenced by parent ids
    kind: str  # OBJM (mesh), OBJD (dummy/empty), OBJX (xref subscene)
    parent: int
    name: str
    custom_data: str
    matrix: list[float]  # 16 floats as written (row-major, Y/Z swapped to Bugbear Y-up)
    xref: str | None = None
    tri_count: int = 0
    uv_count: int = 0
    # Blender-space (x, y, z) triangles and material ids, only when read with tris=True
    tris: list[tuple[tuple[float, float, float], ...]] | None = None
    tri_materials: list[int] | None = None
    # per UV layer [u, v] minimum / maximum as stored (v already flipped by the exporter), with tris=True
    uv_lo: list[list[float]] | None = None
    uv_hi: list[list[float]] | None = None

    @property
    def location(self) -> tuple[float, float, float]:
        # Bugbear is Y-up; Blender is Z-up. The 4th row holds the translation (x, y_up, z).
        m = self.matrix
        return (m[12], m[14], m[13])


@dataclass
class Bgo:
    user: str
    materials: list[Material]
    objects: list[BgoObject]

    def by_name(self) -> dict[str, BgoObject]:
        return {o.name: o for o in self.objects}


class _Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.p = pos

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def f32(self, n: int = 1):
        v = struct.unpack_from(f"<{n}f", self.d, self.p)
        self.p += 4 * n
        return v

    def tag(self) -> str:
        v = self.d[self.p:self.p + 4].decode("ascii", "replace")
        self.p += 4
        return v

    def cstr(self) -> str:
        end = self.d.index(b"\x00", self.p)
        v = self.d[self.p:end].decode("utf-8", "replace")
        self.p = end + 1
        return v


def _read_material(r: _Reader, end: int) -> Material:
    r.p += 32 + 9 * 4 + 3 * 4 + 4 + 4 + 4 + 4
    name = r.cstr()
    mat = Material(name)
    if r.p >= end:
        return mat
    ntex = r.u32()
    for _ in range(ntex):
        start = r.p
        size = r.u32()
        tag = r.tag()
        if tag != "TEXC":
            break
        slot, uv, _zero = struct.unpack_from("<3I", r.d, r.p)
        r.p += 12 + 64
        mat.textures.append(Texture(slot, uv, r.cstr()))
        r.p = start + size
    return mat


def read(path: str, tris: bool = False) -> Bgo:
    with open(path, "rb") as f:
        data = f.read()
    r = _Reader(data)
    r.u32()
    if r.tag() != "MAIN":
        raise ValueError(f"{path}: not a BGO file")
    user = ""
    materials: list[Material] = []
    objects: list[BgoObject] = []
    while r.p + 8 <= len(data):
        start = r.p
        size = r.u32()
        tag = r.tag()
        if tag == "INFO":
            r.u32()
            r.cstr()
            user = r.cstr()
            r.p = start + size
        elif tag == "MLST":
            n = r.u32()
            for _ in range(n):
                ms = r.p
                msize = r.u32()
                if r.tag() != "MATC":
                    raise ValueError(f"{path}: bad material chunk at {ms}")
                materials.append(_read_material(r, ms + msize))
                r.p = ms + msize
            r.p = start + size
        elif tag == "HIER":
            n = r.u32()
            for i in range(n):
                r.u32()  # object size: inconsistent between exporters, so parse sequentially
                kind = r.tag()
                parent = r.u32()
                r.u32()
                matrix = list(r.f32(16))
                r.f32(16)
                r.u32()
                r.u32()
                name = r.cstr()
                custom = r.cstr()
                ob = BgoObject(i + 1, kind, parent, name, custom, matrix)
                if kind == "OBJM":
                    r.u32()
                    gsize = r.u32()  # excludes its 8-byte header
                    if r.tag() != "GMSH":
                        raise ValueError(f"{path}: {name}: missing GMSH")
                    gend = r.p + gsize
                    ob.tri_count = r.u32()
                    ob.uv_count = r.u32()
                    if tris:
                        stride = 4 + 11 * ob.uv_count
                        ob.tris, ob.tri_materials = [], []
                        lo = [[math.inf, math.inf] for _ in range(ob.uv_count)]
                        hi = [[-math.inf, -math.inf] for _ in range(ob.uv_count)]
                        for _ in range(ob.tri_count):
                            corners = []
                            mid = 0
                            for _c in range(3):
                                vals = r.f32(stride)
                                mid = int(vals[0])
                                corners.append((vals[1], vals[3], vals[2]))
                                for k in range(ob.uv_count):
                                    for a in (0, 1):
                                        v = vals[4 + 11 * k + a]
                                        lo[k][a] = min(lo[k][a], v)
                                        hi[k][a] = max(hi[k][a], v)
                            ob.tris.append(tuple(corners))
                            ob.tri_materials.append(mid)
                        ob.uv_lo, ob.uv_hi = lo, hi
                    r.p = gend
                elif kind == "OBJX":
                    ob.xref = r.cstr()
                objects.append(ob)
            break
        else:
            break
    return Bgo(user, materials, objects)


def summary(bgo: Bgo) -> dict:
    return {
        "user": bgo.user,
        "materials": [{"name": m.name, "textures": [[t.slot, t.uv, t.path] for t in m.textures]}
                      for m in bgo.materials],
        "objects": [{"i": o.index, "kind": o.kind, "parent": o.parent, "name": o.name,
                     "customData": o.custom_data, "xref": o.xref, "tris": o.tri_count, "uvs": o.uv_count,
                     "location": [round(v, 3) for v in o.location]} for o in bgo.objects],
    }


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for p in args:
        print(json.dumps(summary(read(p, tris="--tris" in sys.argv)), indent=1))
