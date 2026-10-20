"""Track data (.tcat) for a layout: what Wreckfest 1 needs to draw the in-race minimap (stdlib only).

Wreckfest 1 has no minimap image per track. The game draws the HUD minimap at run time from the layout's
compiled AI routes (the road between the race and safe lines, ``Minimap Race To Safe Bias`` and ``Minimap
Track Width Extend`` per route) in world coordinates, with colours and scale from the global
``data/property/rendering/minimap_settings.mise``. It sets the minimap up while loading the layout's track data:
``track.cpp`` looks for ``<scene dir>/<scene>.tcat`` and ``data/property/track/<scene>.tcat`` and only then
creates ``<name>_minimap``. Every stock layout and every Workshop track ships a ``.tcat`` named after its
layout scene, and the official guide lists it in the simplest possible track; our mods shipped none, and
Cadwell Park and Anglesey had no minimap (2026-10-19). So every layout now gets a generated one.

Format (bag file, little endian; field names and order from BagEdit's ``bbTrackData`` schema, version 20, and
checked against all 120 stock v20 ``.tcat`` files, which parse to the last byte with ``read``): format dword (1 =
plain), tag 'tcat' reversed, version 20, then

    f  Minimap Highlight Range, Minimap Zoom Factor, Derby: player tag draw distance factor
    f  pit lane speed limit (km/h), pit point detection distance, pit point stopping distance
    'grgi' v2 [1]   grid generation info: u32 flags (2: off; our start points are the grid), f space between
                    the first point and the line, i32 max points in a row, u32 force first point side,
                    f first point lateral fraction, f lateral / longitudinal spacing, f row spacing
    f  use ai Grip Handicap, ai Grip Handicap Offset
    'fxdf' text     dof (data/property/effects/dof/default.fxdf)
    f  free cam max distance (X), (Z), max elevation; prerace cam fov
    'dofs' v0 [1]   prerace cam dof (5 dwords)
    'tcam' v11 [0]  cameras (none: stock fields04_2 and urban02_6 have none either)
    'trou' v7 [n]   routes, one per AI route in index order (main, alt1, ...): u32 flags (0; a flag bit is
                    "Do not render in minimap"), f Minimap Race To Safe Bias, f Minimap Track Width Extend,
                    f ai drive probability, i32 Alt Start Route Start Sector, 'tszo' v1 [0] special zones,
                    'tsec' v0 [0] sectors
    u32 flags, f track length (m)  (the game updates length and surfaces unless the data is locked)
    'trsi' v1 [0]   surface infos
    9 x i32         lap reference times (Class A/B/C), checkpoint race start and first span times
    'ttgi' v1 [0]   time gate infos;  'tobs' v1 [0] obstacles

Values are the ones stock and Workshop files carry by default (highlight 2.0, zoom 1.0, bias 0.3, extend 0,
drive probability 0.9 on the main route and 0.1 on alternatives as in stock fields04_2).
"""
from __future__ import annotations

import struct

VERSION = 20
DOF = "data/property/effects/dof/default.fxdf"
HIGHLIGHT, ZOOM = 2.0, 1.0
RACE_TO_SAFE_BIAS, WIDTH_EXTEND = 0.3, 0.0
DRIVE_PROB_MAIN, DRIVE_PROB_ALT = 0.9, 0.1
GRID_GENERATION_OFF = 2
GRID_DEFAULTS = (5.0, 3, 0, 0.2, 1.5, 2.0, 3.0)  # what the game's track editor writes (Workshop tracks)


def _tag(t: str) -> bytes:
    return t[::-1].encode("ascii")


def _f(*v: float) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def _i(*v: int) -> bytes:
    return struct.pack(f"<{len(v)}i", *v)


def _arr(tag: str, version: int, n: int) -> bytes:
    return _tag(tag) + _i(version, n)


def build(n_routes: int, lap_length_m: float) -> bytes:
    """A plain (format 1) v20 .tcat for a layout with ``n_routes`` AI routes (1 + alternative routes)."""
    if n_routes < 1:
        raise ValueError("a layout needs its main AI route")
    b = bytearray(_i(1) + _tag("tcat") + _i(VERSION))
    b += _f(HIGHLIGHT, ZOOM, 0.0, 30.0, 15.0, 3.0)
    sp, mx, side, lat, sl, slo, sr = GRID_DEFAULTS
    b += _arr("grgi", 2, 1) + struct.pack("<IfiIffff", GRID_GENERATION_OFF, sp, mx, side, lat, sl, slo, sr)
    b += _f(1.0, 0.0)
    b += _tag("fxdf") + _i(len(DOF)) + DOF.encode("ascii")
    b += _f(50.0, 50.0, 50.0, 58.0)
    b += _arr("dofs", 0, 1) + _i(0, 0, 0, 0, 0)
    b += _arr("tcam", 11, 0)
    b += _arr("trou", 7, n_routes)
    for k in range(n_routes):
        b += struct.pack("<Ifff", 0, RACE_TO_SAFE_BIAS, WIDTH_EXTEND, DRIVE_PROB_MAIN if k == 0 else DRIVE_PROB_ALT)
        b += _i(0) + _arr("tszo", 1, 0) + _arr("tsec", 0, 0)
    b += struct.pack("<If", 0, float(lap_length_m))
    b += _arr("trsi", 1, 0)
    b += _i(*([0] * 9))
    b += _arr("ttgi", 1, 0) + _arr("tobs", 1, 0)
    return bytes(b)


class _R:
    def __init__(self, d: bytes):
        self.d, self.p = d, 0

    def f(self) -> float:
        v = struct.unpack_from("<f", self.d, self.p)[0]
        self.p += 4
        return v

    def i(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def tag(self, want: str) -> None:
        got = self.d[self.p:self.p + 4]
        self.p += 4
        if got != _tag(want):
            raise ValueError(f"expected '{want}' at {self.p - 4}, got {got[::-1]!r}")

    def arr(self, want: str) -> tuple[int, int]:
        self.tag(want)
        return self.i(), self.i()

    def text(self) -> str:
        n = self.i()
        v = self.d[self.p:self.p + n].decode("utf-8")
        self.p += n
        return v


def read(payload: bytes) -> dict:
    """Parse a v20 .tcat payload (after the 12-byte header; scne_read.unbag gives it for plain and lz4 files).
    Raises ValueError when the layout differs. Cameras are skipped by their count only when there are none
    (camera records aren't decoded); stock files with cameras are read up to them."""
    r = _R(payload)
    out = {"highlightRange": r.f(), "zoomFactor": r.f(), "derbyTagFactor": r.f(),
           "pit": (r.f(), r.f(), r.f())}
    v, n = r.arr("grgi")
    out["grid"] = [struct.unpack_from("<IfiIffff", payload, r.p + 32 * k) for k in range(n)]
    r.p += 32 * n
    out["aiGrip"] = (r.f(), r.f())
    r.tag("fxdf")
    out["dof"] = r.text()
    out["freeCam"] = (r.f(), r.f(), r.f())
    out["prerace_fov"] = r.f()
    v, n = r.arr("dofs")
    r.p += 20 * n
    v, n = r.arr("tcam")
    out["cameras"] = n
    if n:
        return out  # camera records not decoded
    v, n = r.arr("trou")
    routes = []
    for _ in range(n):
        fl = struct.unpack_from("<I", payload, r.p)[0]
        r.p += 4
        rt = {"flags": fl, "raceToSafeBias": r.f(), "widthExtend": r.f(), "driveProbability": r.f(),
              "altStartSector": r.i()}
        zv, zn = r.arr("tszo")
        r.p += 12 * zn
        sv, sn = r.arr("tsec")
        r.p += 8 * sn
        rt["zones"], rt["sectors"] = zn, sn
        routes.append(rt)
    out["routes"] = routes
    out["flags"] = struct.unpack_from("<I", payload, r.p)[0]
    r.p += 4
    out["trackLengthM"] = r.f()
    v, n = r.arr("trsi")
    out["surfaces"] = [(r.text(), r.f()) for _ in range(n)]
    out["times"] = [r.i() for _ in range(9)]
    v, n = r.arr("ttgi")
    if n:
        raise ValueError("time gates not decoded")
    r.arr("tobs")
    if r.p != len(payload):
        raise ValueError(f"{len(payload) - r.p} trailing bytes")
    return out


def header(data: bytes) -> tuple[int, str, int]:
    """(format dword, tag, version) of a .tcat file."""
    fmt, tag, ver = struct.unpack_from("<I4sI", data, 0)
    return fmt, tag[::-1].decode("latin-1"), ver
