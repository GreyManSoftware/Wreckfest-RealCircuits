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
    'tcam' v11 [n]  cameras (BagEdit's bbTrackCamera v11 field table; all 120 stock files decode): u32 type
                    (enum: 0 rotating, 1 fixed, 2 moving), u32 flags (bit enum: 2 "start camera", 4 "sector
                    activation"), f default fov, f zoom start distance, i32 activation route, i32 x2 active
                    between sectors, i32 x2 move between sectors (indices into the compiled AI route's
                    sectors), f tracking start / end distance, a dof reference ('fxdf' text, or an empty
                    reference: two zero dwords), 'tcde' v1 [k] data entries: f position x, y (up), z (world,
                    Bugbear axes: Blender x, z, y; checked against the stock base terrain with each model's
                    matrix), f rotation pitch, yaw, roll (degrees; yaw from +z towards +x, pitch < 0 looks down,
                    stored 0..360), f field of view, f screen position x, y, f smoothing (vel, pos, pred). We
                    write a start camera and trackside cameras per layout (``Camera``, wfbuild/cameras.py).
    'trou' v7 [n]   routes, one per AI route in index order (main, alt1, ...): u32 flags (0; a flag bit is
                    "Do not render in minimap"), f Minimap Race To Safe Bias, f Minimap Track Width Extend,
                    f ai drive probability, i32 Alt Start Route Start Sector, 'tszo' v1 [0] special zones,
                    'tsec' v0 [0] sectors
    u32 flags, f track length (m)  (the game updates length and surfaces unless the data is locked)
    'trsi' v1 [0]   surface infos
    9 x i32         lap reference times (Class A/B/C), checkpoint race start and first span times
    'ttgi' v1 [0]   time gate infos;  'tobs' v1 [0] obstacles

Stock layouts carry a start camera and trackside cameras (wfbuild/cameras.py); the game's trackside camera mode
(cinematic camera button, replays) uses them. The race intro view comes from the scene objects resultcamera /
resultcamera_target instead (build_scene.result_camera). Values are the ones stock and Workshop files carry by default (highlight 2.0, zoom 1.0, bias 0.3, extend 0,
drive probability 0.9 on the main route and 0.1 on alternatives as in stock fields04_2).
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

VERSION = 20
DOF = "data/property/effects/dof/default.fxdf"
HIGHLIGHT, ZOOM = 2.0, 1.0
RACE_TO_SAFE_BIAS, WIDTH_EXTEND = 0.3, 0.0
DRIVE_PROB_MAIN, DRIVE_PROB_ALT = 0.9, 0.1
GRID_GENERATION_OFF = 2
GRID_DEFAULTS = (5.0, 3, 0, 0.2, 1.5, 2.0, 3.0)  # what the game's track editor writes (Workshop tracks)
CAM_ROTATING, CAM_FIXED, CAM_MOVING = 0, 1, 2
CAM_START, CAM_SECTOR = 2, 4  # flag bits: "start camera", "sector activation" (BagEdit's enum table)
CAM_TRACKING = 1000.0  # tracking start/end distance of most stock cameras


@dataclass
class Camera:
    """A track camera in Blender axes (x, y, z up, metres; the layout scene's coordinates). ``yaw_deg`` is the
    bearing it looks along, from +y towards +x; ``pitch_deg`` < 0 looks down. Stock start cameras (the view the
    game shows while the race loads in, before the menu): fixed, 2-10 m above the ground beside the road, some
    10-50 m from the pole, looking back at the grid, field of view ~58."""
    x: float
    y: float
    z: float
    pitch_deg: float
    yaw_deg: float
    fov_deg: float = 58.0
    kind: int = CAM_FIXED
    flags: int = CAM_START
    sectors: tuple[int, int] = (0, 0)  # active between these compiled main-route sectors (sector activation)

    @staticmethod
    def looking_at(eye, target, fov_deg: float = 58.0, kind: int = CAM_FIXED, flags: int = CAM_START,
                   sectors: tuple[int, int] = (0, 0)) -> "Camera":
        dx, dy, dz = (float(target[k]) - float(eye[k]) for k in range(3))
        yaw = math.degrees(math.atan2(dx, dy)) % 360.0
        pitch = math.degrees(math.atan2(dz, math.hypot(dx, dy)))
        return Camera(float(eye[0]), float(eye[1]), float(eye[2]), pitch, yaw, fov_deg, kind, flags,
                      (int(sectors[0]), int(sectors[1])))

    def direction(self) -> tuple[float, float, float]:
        """Unit view direction (Blender axes)."""
        p, y = math.radians(self.pitch_deg), math.radians(self.yaw_deg)
        return (math.cos(p) * math.sin(y), math.cos(p) * math.cos(y), math.sin(p))


def _tag(t: str) -> bytes:
    return t[::-1].encode("ascii")


def _f(*v: float) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def _i(*v: int) -> bytes:
    return struct.pack(f"<{len(v)}i", *v)


def _arr(tag: str, version: int, n: int) -> bytes:
    return _tag(tag) + _i(version, n)


def _camera(c: Camera) -> bytes:
    a, z = c.sectors
    b = struct.pack("<IIff5i2f", c.kind, c.flags, c.fov_deg, 7.0, 0, a, z, 0, 0, CAM_TRACKING, CAM_TRACKING)
    b += _tag("fxdf") + _i(len(DOF)) + DOF.encode("ascii")
    b += _arr("tcde", 1, 1)
    b += _f(c.x, c.z, c.y, c.pitch_deg % 360.0, c.yaw_deg % 360.0, 0.0, c.fov_deg, 0.5, 0.5, 0.9, 0.9, 1.0)
    return b


def build(n_routes: int, lap_length_m: float, cameras: list[Camera] | tuple = ()) -> bytes:
    """A plain (format 1) v20 .tcat for a layout with ``n_routes`` AI routes (1 + alternative routes) and the
    given cameras (scene coordinates)."""
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
    b += _arr("tcam", 11, len(cameras))
    for c in cameras:
        b += _camera(c)
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


def _dof_ref(r: _R) -> str | None:
    """A dof reference: 'fxdf' and its path, or an empty reference (two zero dwords)."""
    if r.d[r.p:r.p + 4] == bytes(4):
        r.p += 4
        r.text()
        return None
    r.tag("fxdf")
    return r.text()


def read(payload: bytes) -> dict:
    """Parse a v20 .tcat payload (after the 12-byte header; scne_read.unbag gives it for plain and lz4 files).
    Raises ValueError when the layout differs. Cameras come back as ``Camera`` (Blender axes, first position)
    with the raw header and every position entry. The cameras of all 120 stock files parse; so does the rest
    (to the last byte, or up to their time gates, which aren't decoded) except forest02_1's special zones."""
    r = _R(payload)
    out = {"highlightRange": r.f(), "zoomFactor": r.f(), "derbyTagFactor": r.f(),
           "pit": (r.f(), r.f(), r.f())}
    v, n = r.arr("grgi")
    out["grid"] = [struct.unpack_from("<IfiIffff", payload, r.p + 32 * k) for k in range(n)]
    r.p += 32 * n
    out["aiGrip"] = (r.f(), r.f())
    out["dof"] = _dof_ref(r)
    out["freeCam"] = (r.f(), r.f(), r.f())
    out["prerace_fov"] = r.f()
    v, n = r.arr("dofs")
    r.p += 20 * n
    v, n = r.arr("tcam")
    cams = []
    for _ in range(n):
        h = struct.unpack_from("<IIff5I2f", payload, r.p)
        r.p += 44
        dof = _dof_ref(r)
        ev, en = r.arr("tcde")
        ents = [struct.unpack_from("<12f", payload, r.p + 48 * k) for k in range(en)]
        r.p += 48 * en
        if not ents:
            raise ValueError("camera without a position")
        e = ents[0]
        pitch = (e[3] + 180.0) % 360.0 - 180.0
        cams.append({"camera": Camera(e[0], e[2], e[1], pitch, e[4] % 360.0, e[6], h[0], h[1], (h[5], h[6])),
                     "header": h, "dof": dof, "positions": ents})
    out["cameras"] = cams
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
        return out  # time gates not decoded
    r.arr("tobs")
    if r.p != len(payload):
        raise ValueError(f"{len(payload) - r.p} trailing bytes")
    return out


def header(data: bytes) -> tuple[int, str, int]:
    """(format dword, tag, version) of a .tcat file."""
    fmt, tag, ver = struct.unpack_from("<I4sI", data, 0)
    return fmt, tag[::-1].decode("latin-1"), ver
