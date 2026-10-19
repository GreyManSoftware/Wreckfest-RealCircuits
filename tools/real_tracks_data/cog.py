"""Minimal stdlib reader for a remote tiled (Big)TIFF / Cloud Optimised GeoTIFF.

Reads only the tiles that cover a bounding box using HTTP range requests, so a
48 GB national DTM can be sampled for one circuit with a few MB of traffic.
Every range response is cached on disk (``cache_dir``), so re-runs make no
requests.

Supports: little-endian TIFF/BigTIFF, tiled, one band, float32/int16/uint16,
compression none or deflate (8, 32946), predictor 1/2/3, and the overview levels.
LZW/ZSTD/LERC raise NotImplementedError. That covers the DataMapWales LIDAR COGs
(deflate, no predictor). numpy is the only dependency.
"""
from __future__ import annotations

import hashlib
import os
import struct
import urllib.request
import zlib

import numpy as np

UA = "WreckfestTrackGen-research/0.1 (personal, low volume)"
_TSZ = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8, 17: 8, 18: 8}
_TFMT = {1: "B", 3: "H", 4: "I", 8: "h", 9: "i", 11: "f", 12: "d", 16: "Q", 17: "q"}


class RemoteCog:
    def __init__(self, url: str, cache_dir: str):
        self.url = url
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.requests = 0
        head = self._range(0, 65535)
        if head[:4] == b"II+\x00":
            self.big = True
            ifd = struct.unpack_from("<Q", head, 8)[0]
        elif head[:4] == b"II*\x00":
            self.big = False
            ifd = struct.unpack_from("<I", head, 4)[0]
        else:
            raise ValueError("not a little-endian TIFF")
        self._head = head
        self.ifds = []
        while ifd and len(self.ifds) < 32:
            tags, ifd = self._read_ifd(ifd)
            self.ifds.append(tags)
        t = self.ifds[0]
        self.tags = t
        self.width = t[256][0]
        self.height = t[257][0]
        self.tile_w = t[322][0]
        self.tile_h = t[323][0]
        self.compression = t.get(259, (1,))[0]
        self.predictor = t.get(317, (1,))[0]
        self.sample_format = t.get(339, (1,))[0]
        self.bits = t.get(258, (32,))[0]
        scale = t[33550]
        tie = t[33922]
        self.res_x, self.res_y = scale[0], scale[1]
        self.origin_x = tie[3] - tie[0] * self.res_x
        self.origin_y = tie[4] + tie[1] * self.res_y
        nodata = t.get(42113)
        self.nodata = float(nodata.rstrip(b"\x00")) if nodata else None
        self._full = (self.width, self.height, self.res_x, self.res_y)
        self.use_level(0)
        dt = {(3, 32): "<f4", (1, 16): "<u2", (2, 16): "<i2", (3, 64): "<f8", (2, 32): "<i4"}
        self.dtype = np.dtype(dt[(self.sample_format, self.bits)])

    def use_level(self, k: int) -> None:
        """Select full resolution (0) or overview k (1..n); resolution scales with width."""
        t = self.ifds[k]
        w0, h0, rx0, ry0 = self._full
        self.width, self.height = t[256][0], t[257][0]
        self.tile_w, self.tile_h = t[322][0], t[323][0]
        self.res_x, self.res_y = rx0 * w0 / self.width, ry0 * h0 / self.height
        self.tiles_across = (self.width + self.tile_w - 1) // self.tile_w
        self._offs_tag = t.get("_ptr_324")
        self._cnts_tag = t.get("_ptr_325")
        self.tags_level = t

    # -- http with cache ------------------------------------------------
    def _range(self, start: int, end: int) -> bytes:
        key = hashlib.sha1(f"{self.url}|{start}|{end}".encode()).hexdigest()[:20]
        path = os.path.join(self.cache_dir, key + ".bin")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        self.requests += 1
        with open(path, "wb") as f:
            f.write(data)
        return data

    def _bytes(self, off: int, n: int) -> bytes:
        if off + n <= len(self._head):
            return self._head[off:off + n]
        return self._range(off, off + n - 1)

    def _read_ifd(self, off: int):
        big = self.big
        ent = 20 if big else 12
        cnt = struct.unpack("<Q" if big else "<H", self._bytes(off, 8 if big else 2))[0]
        base = off + (8 if big else 2)
        raw = self._bytes(base, cnt * ent + (8 if big else 4))
        tags = {}
        for i in range(cnt):
            e = raw[i * ent:(i + 1) * ent]
            if big:
                tag, typ, c = struct.unpack_from("<HHQ", e)
                inline = e[12:20]
                lim = 8
            else:
                tag, typ, c = struct.unpack_from("<HHI", e)
                inline = e[8:12]
                lim = 4
            size = _TSZ.get(typ, 1) * c
            if size <= lim:
                data = inline[:size]
                ptr = None
            else:
                ptr = struct.unpack("<Q" if big else "<I", inline)[0]
                data = None
            if tag in (324, 325) and ptr is not None:
                tags[f"_ptr_{tag}"] = (ptr, typ, c)
                continue
            if data is None:
                data = self._bytes(ptr, size)
            if typ == 2 or typ == 7:
                tags[tag] = data
            else:
                tags[tag] = struct.unpack("<" + _TFMT[typ] * c, data)
        nxt = struct.unpack_from("<Q" if big else "<I", raw, cnt * ent)[0]
        return tags, nxt

    # -- tiles ----------------------------------------------------------
    def _tile_loc(self, idx: int):
        if self._offs_tag is None:  # inline (single tile)
            return self.tags_level[324][idx], self.tags_level[325][idx]
        p, typ, _ = self._offs_tag
        q, typ2, _ = self._cnts_tag
        s, s2 = _TSZ[typ], _TSZ[typ2]
        o = struct.unpack("<" + _TFMT[typ], self._range(p + idx * s, p + idx * s + s - 1))[0]
        n = struct.unpack("<" + _TFMT[typ2], self._range(q + idx * s2, q + idx * s2 + s2 - 1))[0]
        return o, n

    def _tile_locs_row(self, row: int, c0: int, c1: int):
        """Offsets/counts for tiles (row, c0..c1) in two range requests."""
        i0 = row * self.tiles_across + c0
        n = c1 - c0 + 1
        if self._offs_tag is None:
            return list(zip(self.tags_level[324][i0:i0 + n], self.tags_level[325][i0:i0 + n]))
        p, typ, _ = self._offs_tag
        q, typ2, _ = self._cnts_tag
        s, s2 = _TSZ[typ], _TSZ[typ2]
        i0 = row * self.tiles_across + c0
        n = c1 - c0 + 1
        offs = struct.unpack("<" + _TFMT[typ] * n, self._range(p + i0 * s, p + (i0 + n) * s - 1))
        cnts = struct.unpack("<" + _TFMT[typ2] * n, self._range(q + i0 * s2, q + (i0 + n) * s2 - 1))
        return list(zip(offs, cnts))

    def _decode(self, data: bytes) -> np.ndarray:
        if self.compression == 1:
            raw = data
        elif self.compression in (8, 32946):
            raw = zlib.decompress(data)
        else:
            raise NotImplementedError(f"compression {self.compression}")
        tw, th = self.tile_w, self.tile_h
        bps = self.dtype.itemsize
        if self.predictor == 3:
            a = np.frombuffer(raw, np.uint8).reshape(th, tw * bps).astype(np.uint8)
            a = np.cumsum(a, axis=1, dtype=np.uint8)
            a = a.reshape(th, bps, tw).transpose(0, 2, 1)[:, :, ::-1]  # big-endian byte planes
            return np.ascontiguousarray(a).view(">f4" if bps == 4 else ">f8").reshape(th, tw).astype(np.float64)
        arr = np.frombuffer(raw, self.dtype).reshape(th, tw).astype(np.float64)
        if self.predictor == 2:
            arr = np.cumsum(arr, axis=1)
        return arr

    def read_bbox(self, x0: float, y0: float, x1: float, y1: float):
        """Return (array[rows, cols] north-up, x of col 0 centre, y of row 0 centre, res)."""
        c0 = int((x0 - self.origin_x) // self.res_x)
        c1 = int((x1 - self.origin_x) // self.res_x)
        r0 = int((self.origin_y - y1) // self.res_y)
        r1 = int((self.origin_y - y0) // self.res_y)
        c0, r0 = max(c0, 0), max(r0, 0)
        c1, r1 = min(c1, self.width - 1), min(r1, self.height - 1)
        tc0, tc1 = c0 // self.tile_w, c1 // self.tile_w
        tr0, tr1 = r0 // self.tile_h, r1 // self.tile_h
        out = np.full(((tr1 - tr0 + 1) * self.tile_h, (tc1 - tc0 + 1) * self.tile_w), np.nan)
        for tr in range(tr0, tr1 + 1):
            locs = self._tile_locs_row(tr, tc0, tc1)
            for k, (off, n) in enumerate(locs):
                if n == 0:
                    continue
                tile = self._decode(self._range(off, off + n - 1))
                rr = (tr - tr0) * self.tile_h
                cc = k * self.tile_w
                out[rr:rr + self.tile_h, cc:cc + self.tile_w] = tile
        if self.nodata is not None:
            out[out == self.nodata] = np.nan
        out[out < -1000] = np.nan
        rs, cs = r0 - tr0 * self.tile_h, c0 - tc0 * self.tile_w
        out = out[rs:rs + (r1 - r0 + 1), cs:cs + (c1 - c0 + 1)]
        xc = self.origin_x + (c0 + 0.5) * self.res_x
        yc = self.origin_y - (r0 + 0.5) * self.res_y
        return out, xc, yc, self.res_x


if __name__ == "__main__":
    import sys
    cog = RemoteCog(sys.argv[1], sys.argv[2])
    for k, v in cog.tags.items():
        print(k, v if not isinstance(v, (bytes, tuple)) or len(v) < 40 else (v[:40], "..."))
    for k in range(1, len(cog.ifds)):
        cog.use_level(k)
        print("level", k, cog.width, cog.height, "res", cog.res_x)
    cog.use_level(0)
    print("size", cog.width, cog.height, "tile", cog.tile_w, cog.tile_h, "comp", cog.compression,
          "pred", cog.predictor, "origin", cog.origin_x, cog.origin_y, "res", cog.res_x, cog.res_y,
          "nodata", cog.nodata, "overviews", len(cog.ifds) - 1, "requests", cog.requests)
