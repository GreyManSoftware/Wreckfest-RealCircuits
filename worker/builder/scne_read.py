"""Geometry statistics of built Wreckfest .scne files (stdlib only; lz4 decoded in pure Python).

What bgeometry wrote, as the game will load it: per model mesh the draw batches (one per material, each with
its own vertex and 16-bit index buffer), their vertex and triangle counts and vertex format, plus the number
of subscenes. Used by check_export.py (vertex limits), tools/Diagnose-TrackCrash.ps1 and for comparing our
scenes with stock ones.

    python scne_read.py FILE.scne [...] [--json] [--top N]

Format (little endian; Toolbox Mz's SCNE importer, io_import_wreckfest, CC0): a bag file is a format dword
(1 = plain, 4 = lz4 blocks of u32 size + block, linked: matches may reach into the previous block), the tag
'scne' reversed, a dword, then the payload. Section headers are 4 reversed characters followed by a version.
Models ('modl') hold a base and a damage 'pmsh'; each pmsh entry has a mesh and (pmsh version > 0) a shadow
mesh; a mesh holds named sub-meshes of 'btch' batches: 40 bytes bias/bbox, 'mtrl', ..., 'vert' (version 4: 32
bytes per vertex: int16 position, normal, UV1, second normal, UV2, bone ids/weights), 'tria' (3 x u16), and
'edgm' when the material version is below 18.
"""
from __future__ import annotations

import json
import os
import struct
import sys
from dataclasses import asdict, dataclass, field


def _lz4_blocks(d: bytes, pos: int) -> bytes:
    out = bytearray()
    n = len(d)
    while pos + 4 <= n:
        (cs,) = struct.unpack_from("<I", d, pos)
        pos += 4
        end = pos + cs
        i = pos
        while i < end:
            tok = d[i]
            i += 1
            lit = tok >> 4
            if lit == 15:
                while True:
                    b = d[i]
                    i += 1
                    lit += b
                    if b != 255:
                        break
            out += d[i:i + lit]
            i += lit
            if i >= end:
                break
            off = d[i] | (d[i + 1] << 8)
            i += 2
            ml = tok & 15
            if ml == 15:
                while True:
                    b = d[i]
                    i += 1
                    ml += b
                    if b != 255:
                        break
            ml += 4
            start = len(out) - off
            if off >= ml:
                out += out[start:start + ml]
            else:  # overlapping copy: repeat the period
                chunk = out[start:]
                reps = ml // off + 1
                out += (chunk * reps)[:ml]
        pos = end
    return bytes(out)


def unbag(d: bytes) -> bytes:
    """Payload of a bag file (format 1 plain or 4 lz4)."""
    fmt = struct.unpack_from("<I", d, 0)[0] if len(d) >= 12 else -1
    if fmt == 4:
        try:
            import lz4.block  # optional, much faster
            pos, out = 12, bytearray()
            while pos < len(d):
                (cs,) = struct.unpack_from("<I", d, pos)
                pos += 4
                blk = d[pos:pos + cs]
                pos += cs
                try:
                    out += lz4.block.decompress(blk, uncompressed_size=1 << 22)
                except Exception:  # noqa: BLE001 - linked block
                    out += lz4.block.decompress(blk, uncompressed_size=1 << 22, dict=bytes(out[-65536:]))
            return bytes(out)
        except ImportError:
            return _lz4_blocks(d, 12)
    if fmt == 1:
        return d[12:]
    return d


@dataclass
class Batch:
    material: str
    verts: int
    tris: int
    vert_version: int
    stride: int
    clutter: int = 0  # clutter layers of the material (grass the game scatters over the batch)
    plan_area: float = 0.0  # bounding box area in plan (m2)
    box: tuple = ()  # bounding box min x, y (up), z, max x, y, z (Bugbear Y-up)
    # with read(..., geometry=prefix), for meshes whose name starts with the prefix: vertex positions (n x 3,
    # Blender axes x, y, z up, in world space: the model's matrix applied) and triangles (m x 3 vertex indices)
    pos: object = field(default=None, repr=False, compare=False)
    idx: object = field(default=None, repr=False, compare=False)


@dataclass
class Mesh:
    name: str
    role: str  # main | shadow | damage | damage_shadow
    batches: list[Batch] = field(default_factory=list)
    # the model's 4x4 matrix as stored (Bugbear Y-up, row vectors: rows x, y, z axes, then the translation);
    # stock bases place most models with it, ours are all identity
    matrix: object = field(default=None, repr=False, compare=False)

    @property
    def verts(self) -> int:
        return sum(b.verts for b in self.batches)

    @property
    def tris(self) -> int:
        return sum(b.tris for b in self.batches)


@dataclass
class Scene:
    path: str
    payload_bytes: int
    models: int
    meshes: list[Mesh]
    subscenes: int
    refs: list[str] = field(default_factory=list)  # subscene paths as stored (data/....scn)

    def batches(self):
        for m in self.meshes:
            for b in m.batches:
                yield m, b

    def totals(self) -> dict:
        bs = list(self.batches())
        return {
            "payloadMB": round(self.payload_bytes / 1e6, 2),
            "models": self.models,
            "meshes": len(self.meshes),
            "batches": len(bs),
            "subscenes": self.subscenes,
            "verts": sum(b.verts for _, b in bs),
            "tris": sum(b.tris for _, b in bs),
            "vertexMB": round(sum(b.verts * b.stride for _, b in bs) / 1e6, 2),
            "indexMB": round(sum(b.tris * 6 for _, b in bs) / 1e6, 2),
            "maxBatchVerts": max((b.verts for _, b in bs), default=0),
            "maxBatchTris": max((b.tris for _, b in bs), default=0),
            "maxMeshVerts": max((m.verts for m in self.meshes), default=0),
            "clutterBatches": sum(1 for _, b in bs if b.clutter),
            "clutterBoxHa": round(sum(b.plan_area for _, b in bs if b.clutter) / 1e4, 1),
        }


class _P:
    def __init__(self, d: bytes):
        self.d, self.p = d, 0

    def i(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def text(self) -> str:
        n = self.i()
        v = self.d[self.p:self.p + n].decode("utf-8", "replace")
        self.p += n
        return v

    def header(self, want: str) -> bool:
        """Reads a 4-character header. False for an empty (zero) section, which is followed by 4 bytes."""
        h = self.d[self.p:self.p + 4]
        self.p += 4
        if h == b"\0\0\0\0":
            self.p += 4
            return False
        if h[::-1].decode("latin-1") != want:
            raise ValueError(f"expected '{want}' at {self.p - 4}, got {h[::-1]!r}")
        return True

    def find(self, tag: str, start: int | None = None) -> int:
        """Position of the next header ``tag`` followed by a plausible version, or -1."""
        t = tag[::-1].encode()
        p = self.p if start is None else start
        while True:
            p = self.d.find(t, p)
            if p < 0:
                return -1
            v = struct.unpack_from("<i", self.d, p + 4)[0] if p + 8 <= len(self.d) else -1
            if 0 <= v < 70:
                return p
            p += 1


def _mesh_section(r: _P, role: str, geometry: str | None = None) -> list[Mesh]:
    r.i()  # version
    out = []
    for _ in range(r.i()):
        m = Mesh(r.text(), role)
        want = geometry is not None and m.name.startswith(geometry)
        r.header("btch")
        r.i()
        for _b in range(r.i()):
            bias = struct.unpack_from("<4f", r.d, r.p)  # x, y_up, z offsets and the scale of the int16 positions
            box = struct.unpack_from("<6f", r.d, r.p + 16)  # after the bias (4f): min x, y_up, z, max x, y_up, z
            r.p += 40
            r.header("mtrl")
            mver = r.i()
            nm = r.i()
            mat = r.text() if nm else ""
            v = r.find("vert")
            if v < 0:
                raise ValueError("vert section not found")
            c = r.d.find(b"rtlc", r.p, v)  # clutter section: version, number of clutter layers
            clutter = struct.unpack_from("<i", r.d, c + 8)[0] if c >= 0 else 0
            r.p = v + 4
            vver = r.i()
            nv = r.i()
            stride = 32 if vver > 3 else 16
            pos = idx = None
            if want:  # Toolbox's importer: int16 x, y_up, z, w at the start of each vertex, times the bias scale
                import numpy as np
                raw = np.frombuffer(r.d, "<i2", count=nv * stride // 2, offset=r.p).reshape(nv, stride // 2)
                pos = np.stack([raw[:, 0] * bias[3] + bias[0], raw[:, 2] * bias[3] + bias[2],
                                raw[:, 1] * bias[3] + bias[1]], axis=1).astype(np.float64)
            r.p += nv * stride
            r.header("tria")
            r.i()
            nt = r.i()
            if want:
                idx = np.frombuffer(r.d, "<u2", count=nt * 3, offset=r.p).reshape(nt, 3).astype(np.int64)
            r.p += nt * 6
            if mver < 18:
                r.header("edgm")
                r.i()
                for _e in range(r.i()):
                    for _k in range(4):
                        r.p += r.i()
            m.batches.append(Batch(mat, nv, nt, vver, stride, clutter, (box[3] - box[0]) * (box[5] - box[2]), box,
                                   pos, idx))
        out.append(m)
    return out


def _pmsh(r: _P, damage: bool, geometry: str | None = None) -> list[Mesh]:
    r.header("pmsh")
    ver = r.i()
    out = []
    for _ in range(r.i()):
        r.p += 24  # aabb
        if r.header("mesh"):
            out += _mesh_section(r, "damage" if damage else "main", None if damage else geometry)
        if ver > 0 and r.header("mesh"):
            out += _mesh_section(r, "damage_shadow" if damage else "shadow")
    return out


def to_world(pos, mx):
    """Model-space positions (n x 3, Blender axes) to world space with a model matrix as stored (Bugbear Y-up,
    row vectors, translation in the last row)."""
    bug = pos[:, [0, 2, 1]] @ mx[:3, :3] + mx[3, :3]
    return bug[:, [0, 2, 1]]


def read(path: str, geometry: str | None = None) -> Scene:
    """Statistics of a .scne; ``geometry`` (a mesh name prefix, e.g. "terrain_") also decodes the positions (world
    space) and triangles of the main meshes with that prefix (``Batch.pos``, ``Batch.idx``)."""
    with open(path, "rb") as f:
        raw = f.read()
    d = unbag(raw)
    r = _P(d)
    p = r.find("modl", 0)
    if p < 0:
        raise ValueError(f"{path}: no models section")
    r.p = p + 4
    mver = r.i()
    nmodels = r.i()
    meshes: list[Mesh] = []
    if mver < 6:
        import numpy as np
        for k in range(nmodels):
            if k:  # the rest of the previous model (shapes, animation, dummies) is skipped by searching
                q = r.find("pmsh")
                if q < 0:
                    break
                r.p = q
            else:
                r.text()
                r.text()
                if mver > 1:
                    r.p += 4
                    r.text()
                r.p += 64 + 24
            # the model's matrix and bounding box sit right before its 'pmsh' (Toolbox Mz's importer)
            mx = np.array(struct.unpack_from("<16f", d, r.p - 88), dtype=np.float64).reshape(4, 4)
            got = _pmsh(r, False, geometry) + _pmsh(r, True)
            for m in got:
                m.matrix = mx
                for b in m.batches:
                    if b.pos is not None:
                        b.pos = to_world(b.pos, mx)
            meshes += got
    refs: list[str] = []
    q = r.find("ssce")
    if q >= 0:
        r.p = q + 4
        sver = r.i()
        for _ in range(r.i()):
            r.p += 64 + (12 if sver >= 3 else 0) + 8  # matrix, scale, heading, flags
            r.text()
            refs.append(r.text() if r.header("scne") else "")
    return Scene(path, len(d), nmodels, meshes, len(refs), refs)


@dataclass
class CompiledLayout:
    """What bgeometry compiled into a layout scene: per AI route the sector lines (left, right) as stored,
    Blender axes (x, y, z up); the start points (name, x, y, z); the checkpoints (number, split, route index,
    centre x, y)."""
    routes: list[list[tuple[tuple[float, float, float], tuple[float, float, float]]]]
    startpoints: list[tuple[str, float, float, float]]
    checkpoints: list[tuple[int, bool, int, float, float]]

    def main_otherway(self) -> bool:
        """Toolbox Mz's importer rule for a compiled main route: stored the other way when the first sector's
        right end lies north (Blender +y) of the last sector's left end. bgeometry stores a route in this
        orientation exactly when its source mesh carried ``otherway = true`` (measured on our builds)."""
        r = self.routes[0]
        return r[0][1][1] > r[-1][0][1]

    def checkpoint_sectors(self) -> list[int]:
        """The main-route sector nearest each main-route checkpoint, in checkpoint order."""
        r = self.routes[0]
        mids = [((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0) for a, b in r]
        out = []
        for _n, _split, ri, x, y in self.checkpoints:
            if ri == 0:
                out.append(min(range(len(mids)), key=lambda k: (mids[k][0] - x) ** 2 + (mids[k][1] - y) ** 2))
        return out


def read_layout(path: str) -> CompiledLayout:
    """AI routes, start points and checkpoints of a built layout .scne (the sections Toolbox Mz's SCNE importer
    reads after the models: 'airt', 'trsp', 'trcp'; Wreckfest 1 versions)."""
    with open(path, "rb") as f:
        d = unbag(f.read())
    r = _P(d)
    for tag in ("modl", "ltpd", "ssce", "aprl", "airt"):  # in file order, so a tag inside mesh data isn't hit
        q = r.find(tag)
        if q < 0:
            raise ValueError(f"{path}: no '{tag}' section")
        r.p = q + 4

    def f() -> float:
        v = struct.unpack_from("<f", d, r.p)[0]
        r.p += 4
        return v

    def v3() -> tuple[float, float, float]:
        x, up, z = f(), f(), f()
        return (x, z, up)  # Bugbear Y-up -> Blender Z-up

    wf2 = r.i() > 0
    routes = []
    for _ in range(r.i()):
        r.header("aisc")
        r.i()
        lines = []
        for _s in range(r.i()):
            left, right = v3(), v3()
            r.p += 4 * (8 if wf2 else 4) + (4 if wf2 else 0)  # safe and race positions (fractions)
            lines.append((left, right))
        r.i(), r.i()  # main-route start / end sector of an alternative route
        r.text()
        if wf2:
            r.p += 4 * 11
        routes.append(lines)
    q = r.find("trsp")
    r.p = q + 8
    sps = []
    for _ in range(r.i()):
        name = r.text()
        m = struct.unpack_from("<16f", d, r.p)
        r.p += 64
        sps.append((name, m[12], m[14], m[13]))
    q = r.find("trcp")
    r.p = q + 4
    ver = r.i()
    cps = []
    for k in range(r.i()):
        split, ri = d[r.p], struct.unpack_from("<b", d, r.p + 1)[0]
        r.p += 3
        cx, _cy, cz = f(), f(), f()
        r.p += 24 + (4 if ver >= 5 else 0)
        cps.append((k + 1, split > 0, ri, cx, cz))
    return CompiledLayout(routes, sps, cps)


# Limits for a base scene, with a margin below what stock tracks use (all 43 stock *_base.scne measured with
# this reader, 2026-10-19; worker/docs/visual-assets.md "Scene budgets"). Stock maxima in the comments.
LIMITS = {
    "batchVerts": 32768,          # largest stock draw batch: 50,506 vertices (urban07); ours ~2-5k
    "sceneVerts": 800_000,        # base scene's own vertices: stock 25k-1.07M (urban04)
    "instanceVerts": 10_000_000,  # own + every subscene instance (props, recursively): stock 0.03-9.8M, urban03 21M
    "subscenes": 9000,            # stock up to 8,440 (wrecker01)
    "clutterHa": 6.0,             # clutter mask (R+G+B) x area at full density: stock 3.6-7.2 ha
    "clutterLocal": 0.22,         # highest mask mean within a 400 m square: stock 0.13-0.24; v2 crashes 0.44-0.60
}


def resolve(ref: str, roots: list[str]) -> str | None:
    rel = ref.replace("\\", "/")
    if rel.endswith(".scn"):
        rel += "e"
    for r in roots:
        p = os.path.join(r, *rel.split("/"))
        if os.path.isfile(p):
            return p
    return None


def instance_verts(path: str, roots: list[str], _cache: dict | None = None) -> tuple[int, int]:
    """(vertices of the scene and every subscene instance, recursively; unresolved references)."""
    cache = {} if _cache is None else _cache
    if path in cache:
        return cache[path]
    s = read(path)
    total, missing = sum(m.verts for m in s.meshes), 0
    for ref in s.refs:
        p = resolve(ref, roots)
        if p is None:
            missing += 1
            continue
        v, mi = instance_verts(p, roots, cache)
        total, missing = total + v, missing + mi
    cache[path] = (total, missing)
    return cache[path]


def bmap_rgba(path: str):
    """An uncompressed bmap (what bimage -auto writes for our TGA masks, and stock clutter masks) as an
    n x n x 4 uint8 array (channel order as stored), or None for a compressed (DDS) one."""
    import math

    import numpy as np
    d = unbag(open(path, "rb").read())
    if b"DDS " in d[:512]:
        return None
    n = 1 << int(math.log2((len(d) - 64) / 4))
    side = int(math.isqrt(n))
    if side * side != n:
        return None
    return np.frombuffer(d[len(d) - 4 * n:], np.uint8).reshape(side, side, 4)


def clutter_stats(s: Scene, roots: list[str]) -> dict | None:
    """Clutter of a scene: its mask(s) referenced by the scene, over the extent of the clutter batches."""
    import re

    import numpy as np
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from wfbuild.maps import clutter_density
    bs = [b for _, b in s.batches() if b.clutter]
    if not bs:
        return None
    size = max(max(b.box[3] for b in bs) - min(b.box[0] for b in bs), max(b.box[5] for b in bs) - min(b.box[2] for b in bs))
    d = unbag(open(s.path, "rb").read())
    names = sorted({m.decode() for m in re.findall(rb"data/[a-z0-9_/]*clutter[a-z0-9_]*_t\b", d)}
                   - {"data/art/textures/default_clutter_colorgray_t"})
    best = None
    for t in names:
        if "colo" in t.split("/")[-1]:
            continue
        p = resolve(t + ".bmap", roots)
        a = bmap_rgba(p) if p else None
        if a is None:
            continue
        ha, local = clutter_density(a[:, :, :3].astype(np.float64) / 255.0, size)
        if best is None or ha > best["ha"]:
            best = {"mask": t, "ha": round(ha, 2), "local": round(local, 3), "frameM": round(size, 1)}
    return best


def missing_textures(s: Scene, roots: list[str]) -> list[str]:
    """Texture paths of the scene's materials ('bmap' entries: u32 length + data/....bmap) that no root has."""
    d = unbag(open(s.path, "rb").read())
    paths, p = set(), 0
    while (p := d.find(b"pamb", p)) >= 0:
        n = struct.unpack_from("<I", d, p + 4)[0] if p + 8 <= len(d) else 0
        t = d[p + 8:p + 8 + n] if 0 < n < 260 else b""
        if t.startswith(b"data/") and t.endswith(b".bmap"):
            paths.add(t.decode("utf-8", "replace"))
        p += 4
    return sorted(x for x in paths if resolve(x, roots) is None)


def limits(path: str, roots: list[str]) -> list[tuple[str, float, float]]:
    """[(what, value, limit)] for a base scene; roots resolve subscenes and textures (mod root, then game)."""
    s = read(path)
    t = s.totals()
    iv, missing = instance_verts(path, roots)
    out = [("largest draw batch (vertices)", t["maxBatchVerts"], LIMITS["batchVerts"]),
           ("scene vertices", t["verts"], LIMITS["sceneVerts"]),
           ("vertices with every subscene instance", iv, LIMITS["instanceVerts"]),
           ("subscenes", t["subscenes"], LIMITS["subscenes"]),
           ("unresolved subscenes", missing, 0)]
    out.append(("textures that don't resolve (pink in game)", len(missing_textures(s, roots)), 0))
    c = clutter_stats(s, roots)
    if c:
        out += [(f"clutter at full density, ha ({c['mask'].split('/')[-1]})", c["ha"], LIMITS["clutterHa"]),
                ("clutter, highest mean within 400 m", c["local"], LIMITS["clutterLocal"])]
    return out


def main() -> None:
    if "--limits" in sys.argv:  # scne_read.py --limits BASE.scne ROOT [ROOT...]: exit 1 when over a limit
        a = [x for x in sys.argv[1:] if x != "--limits"]
        bad = 0
        for what, v, lim in limits(a[0], a[1:]):
            ok = v <= lim
            bad += not ok
            print(f"{'PASS' if ok else 'FAIL'}: {what} {v:,} (limit {lim:,})")
        sys.exit(1 if bad else 0)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    top = 8
    if "--top" in sys.argv:
        top = int(sys.argv[sys.argv.index("--top") + 1])
        args = [a for a in args if a != str(top)]
    res = []
    for p in args:
        s = read(p)
        t = s.totals()
        big = sorted(s.batches(), key=lambda mb: -mb[1].verts)[:top]
        res.append({"path": p, **t, "vertFormats": sorted({(b.vert_version, b.stride) for _, b in s.batches()}),
                    "largest": [{"mesh": m.name, "role": m.role, **asdict(b)} for m, b in big]})
    if "--json" in sys.argv:
        print(json.dumps(res, indent=1))
        return
    for x in res:
        print(f"{x['path']}\n  " + ", ".join(f"{k} {x[k]}" for k in x if k not in ("path", "largest")))
        for b in x["largest"]:
            print(f"    {b['verts']:>7} v {b['tris']:>7} t  {b['role']:<6} {b['mesh']}  [{b['material']}]")


if __name__ == "__main__":
    main()
