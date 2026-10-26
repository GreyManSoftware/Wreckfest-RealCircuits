#!/usr/bin/env python3
"""Read and edit the Wreckfest 1 client mod list (modlist.mods). Stdlib only.

Format notes: tools/docs/modlist-format.md. Used by tools/Install-TrackMod.ps1; all output is JSON.

  py tools/wf_modlist.py info PATH
  py tools/wf_modlist.py set PATH --slug SLUG --state enabled|disabled [--name NAME [--rename]] [--out OUT]
  py tools/wf_modlist.py set PATH --workshop PUBLISHED_ID --state enabled|disabled [--out OUT]
  py tools/wf_modlist.py modinfo-name PATH
  py tools/wf_modlist.py roundtrip PATH

The LZ4HC compressor is a port of LZ4HC_compress_hashChain from lz4 v1.9.4 (lib/lz4hc.c):
  LZ4 HC - High Compression Mode of LZ4
  Copyright (C) 2011-2020, Yann Collet.
  BSD 2-Clause License (http://www.opensource.org/licenses/bsd-license.php)
  Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
  following conditions are met: * Redistributions of source code must retain the above copyright notice, this list of
  conditions and the following disclaimer. * Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation and/or other materials provided with the
  distribution.
  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
  INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
  DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
  SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
  SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
  WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
Bag container layout follows gmazy/bag-decompress (CC0) and Breckfest (MaxxWyndham).
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass, field

M32 = 0xFFFFFFFF
ORDER_UNSET = 0x7FFFFFFF
BLOCK_SIZE = 0x10000


class BagError(Exception):
    pass


# --- CRC32C (Castagnoli), as stored per block in format-7 bags ---------------------------------------------------

def _crc32c_table() -> list[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (0x82F63B78 if c & 1 else 0)
        table.append(c)
    return table


_CRC32C = _crc32c_table()


def crc32c(data: bytes) -> int:
    c = M32
    for b in data:
        c = _CRC32C[(c ^ b) & 0xFF] ^ (c >> 8)
    return c ^ M32


# --- LZ4 block format ---------------------------------------------------------------------------------------------

def lz4_decompress(src: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                if i >= n:
                    raise BagError('LZ4: truncated literal length')
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        if i + lit > n:
            raise BagError('LZ4: literals past end of block')
        out += src[i:i + lit]
        i += lit
        if i == n:
            break
        if i + 2 > n:
            raise BagError('LZ4: truncated offset')
        off = src[i] | (src[i + 1] << 8)
        i += 2
        if off == 0 or off > len(out):
            raise BagError('LZ4: invalid match offset')
        ml = token & 15
        if ml == 15:
            while True:
                if i >= n:
                    raise BagError('LZ4: truncated match length')
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        if off >= ml:
            out += out[start:start + ml]
        else:
            for k in range(ml):
                out.append(out[start + k])
    return bytes(out)


_HC_ATTEMPTS = {1: 2, 2: 2, 3: 4, 4: 8, 5: 16, 6: 32, 7: 64, 8: 128, 9: 256}


def lz4hc_compress(src: bytes, level: int = 9) -> bytes:
    """LZ4HC hash-chain compressor (levels 1-9), byte-compatible with lz4 1.9.4 LZ4_compress_HC (single block, no dict)."""
    attempts = _HC_ATTEMPTS[max(1, min(9, level))]
    pattern_analysis = attempts > 128
    n = len(src)
    base = 0x10000  # prefixIdx == lowLimit == 64 KB for a fresh context
    hash_table = [0] * (1 << 15)
    chain = [0xFFFF] * 0x10000
    state = {'next': base}
    out = bytearray()

    def r32(p: int) -> int:
        return src[p] | (src[p + 1] << 8) | (src[p + 2] << 16) | (src[p + 3] << 24)

    def r16(p: int) -> int:
        return src[p] | (src[p + 1] << 8)

    def hptr(p: int) -> int:
        return ((r32(p) * 2654435761) & M32) >> 17

    def insert(ip: int) -> None:
        target = ip + base
        idx = state['next']
        while idx < target:
            h = hptr(idx - base)
            delta = idx - hash_table[h]
            if delta > 0xFFFF:
                delta = 0xFFFF
            chain[idx & 0xFFFF] = delta
            hash_table[h] = idx
            idx += 1
        state['next'] = target

    def count(p_in: int, p_match: int, limit: int) -> int:
        k = 0
        while p_in + k < limit and src[p_in + k] == src[p_match + k]:
            k += 1
        return k

    def count_back(ip: int, match: int, i_min: int, m_min: int) -> int:
        back = 0
        lo = max(i_min - ip, m_min - match)
        while back > lo and src[ip + back - 1] == src[match + back - 1]:
            back -= 1
        return back

    def count_pattern(ip: int, i_end: int, pattern: int) -> int:
        pb = pattern.to_bytes(4, 'little')
        k = 0
        while ip + k < i_end and src[ip + k] == pb[k & 3]:
            k += 1
        return k

    def reverse_count_pattern(ip: int, i_low: int, pattern: int) -> int:
        pb = pattern.to_bytes(4, 'little')
        p = ip
        while p >= i_low + 4 and r32(p - 4) == pattern:
            p -= 4
        j = 3
        while p > i_low and j >= 0 and src[p - 1] == pb[j]:
            p -= 1
            j -= 1
        return ip - p

    def protect_dict_end(match_index: int) -> bool:
        return ((base - 1 - match_index) & M32) >= 3

    def wider(ip: int, i_low: int, i_high: int, longest: int):
        ip_index = ip + base
        lowest = base if base + 0x10000 > ip_index else ip_index - 0xFFFF
        look_back = ip - i_low
        nb = attempts
        pattern = r32(ip)
        repeat = 0  # 0 untested, 1 not, 2 confirmed
        src_pattern_len = 0
        mpos = spos = None
        insert(ip)
        match_index = hash_table[hptr(ip)]
        while match_index >= lowest and nb > 0:
            nb -= 1
            mp = match_index - base
            if r16(i_low + longest - 1) == r16(mp - look_back + longest - 1) and r32(mp) == pattern:
                back = count_back(ip, mp, i_low, 0) if look_back else 0
                ml = 4 + count(ip + 4, mp + 4, i_high) - back
                if ml > longest:
                    longest, mpos, spos = ml, mp + back, ip + back
            dist = chain[match_index & 0xFFFF]
            if pattern_analysis and dist == 1:
                cand = match_index - 1
                if repeat == 0:
                    if (pattern & 0xFFFF) == (pattern >> 16) and (pattern & 0xFF) == (pattern >> 24):
                        repeat = 2
                        src_pattern_len = count_pattern(ip + 4, i_high, pattern) + 4
                    else:
                        repeat = 1
                if repeat == 2 and cand >= lowest and protect_dict_end(cand):
                    cp = cand - base
                    if r32(cp) == pattern:
                        fwd = count_pattern(cp + 4, i_high, pattern) + 4
                        back_len = reverse_count_pattern(cp, 0, pattern)
                        back_len = cand - max(cand - back_len, lowest)
                        seg = back_len + fwd
                        if seg >= src_pattern_len and fwd <= src_pattern_len:
                            new_index = cand + fwd - src_pattern_len
                            match_index = new_index if protect_dict_end(new_index) else base
                        else:
                            new_index = cand - back_len
                            if not protect_dict_end(new_index):
                                match_index = base
                            else:
                                match_index = new_index
                                if look_back == 0:
                                    max_ml = min(seg, src_pattern_len)
                                    if longest < max_ml:
                                        if ip + base - match_index > 0xFFFF:
                                            break
                                        longest, mpos, spos = max_ml, match_index - base, ip
                                    d2 = chain[match_index & 0xFFFF]
                                    if d2 > match_index:
                                        break
                                    match_index -= d2
                        continue
            match_index -= chain[match_index & 0xFFFF]
        return longest, mpos, spos

    def emit(ip: int, anchor: int, ml: int, ref: int) -> int:
        lit = ip - anchor
        token = len(out)
        out.append(0)
        if lit >= 15:
            out[token] = 15 << 4
            rest = lit - 15
            while rest >= 255:
                out.append(255)
                rest -= 255
            out.append(rest)
        else:
            out[token] = lit << 4
        out.extend(src[anchor:ip])
        out.extend(struct.pack('<H', ip - ref))
        rest = ml - 4
        if rest >= 15:
            out[token] += 15
            rest -= 15
            while rest >= 510:
                out.extend(b'\xff\xff')
                rest -= 510
            if rest >= 255:
                rest -= 255
                out.append(255)
            out.append(rest)
        else:
            out[token] += rest
        return ip + ml

    ip = anchor = 0
    mflimit = n - 12
    matchlimit = n - 5
    optimal_ml = 18
    if n >= 13:
        while ip <= mflimit:
            ml, ref, _ = wider(ip, ip, matchlimit, 3)
            if ml < 4:
                ip += 1
                continue
            start0, ref0, ml0 = ip, ref, ml
            start2 = ref2 = start3 = ref3 = None
            stage = 2
            while True:
                if stage == 2:
                    if ip + ml <= mflimit:
                        ml2, r2, s2 = wider(ip + ml - 2, ip, matchlimit, ml)
                        if r2 is not None:
                            ref2, start2 = r2, s2
                    else:
                        ml2 = ml
                    if ml2 == ml:
                        ip = anchor = emit(ip, anchor, ml, ref)
                        break
                    if start0 < ip and start2 < ip + ml0:
                        ip, ref, ml = start0, ref0, ml0
                    if start2 - ip < 3:
                        ml, ip, ref = ml2, start2, ref2
                        continue
                    stage = 3
                # stage 3
                if start2 - ip < optimal_ml:
                    new_ml = min(ml, optimal_ml)
                    if ip + new_ml > start2 + ml2 - 4:
                        new_ml = start2 - ip + ml2 - 4
                    correction = new_ml - (start2 - ip)
                    if correction > 0:
                        start2 += correction
                        ref2 += correction
                        ml2 -= correction
                if start2 + ml2 <= mflimit:
                    ml3, r3, s3 = wider(start2 + ml2 - 3, start2, matchlimit, ml2)
                    if r3 is not None:
                        ref3, start3 = r3, s3
                else:
                    ml3 = ml2
                if ml3 == ml2:
                    if start2 < ip + ml:
                        ml = start2 - ip
                    ip = anchor = emit(ip, anchor, ml, ref)
                    ip = start2
                    ip = anchor = emit(ip, anchor, ml2, ref2)
                    break
                if start3 < ip + ml + 3:
                    if start3 >= ip + ml:
                        if start2 < ip + ml:
                            correction = ip + ml - start2
                            start2 += correction
                            ref2 += correction
                            ml2 -= correction
                            if ml2 < 4:
                                start2, ref2, ml2 = start3, ref3, ml3
                        ip = anchor = emit(ip, anchor, ml, ref)
                        ip, ref, ml = start3, ref3, ml3
                        start0, ref0, ml0 = start2, ref2, ml2
                        stage = 2
                        continue
                    start2, ref2, ml2 = start3, ref3, ml3
                    stage = 3
                    continue
                if start2 < ip + ml:
                    if start2 - ip < optimal_ml:
                        if ml > optimal_ml:
                            ml = optimal_ml
                        if ip + ml > start2 + ml2 - 4:
                            ml = start2 - ip + ml2 - 4
                        correction = ml - (start2 - ip)
                        if correction > 0:
                            start2 += correction
                            ref2 += correction
                            ml2 -= correction
                    else:
                        ml = start2 - ip
                ip = anchor = emit(ip, anchor, ml, ref)
                ip, ref, ml = start2, ref2, ml2
                start2, ref2, ml2 = start3, ref3, ml3
                stage = 3

    last = n - anchor
    if last >= 15:
        out.append(15 << 4)
        rest = last - 15
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)
    else:
        out.append(last << 4)
    out += src[anchor:]
    return bytes(out)


# --- Bag container ------------------------------------------------------------------------------------------------

@dataclass
class Bag:
    fmt: int
    tag: bytes
    version: int
    payload: bytes
    blocks: int = 0


def bag_decode(data: bytes) -> Bag:
    if len(data) < 12:
        raise BagError('file too short for a bag header')
    fmt, tag, version = struct.unpack_from('<I4sI', data, 0)
    if fmt == 1:
        return Bag(fmt, tag, version, data[12:])
    if fmt not in (4, 7):
        raise BagError(f'unsupported bag format {fmt}' + (' (encrypted)' if fmt == 8 else ''))
    pos, out, blocks = 12, bytearray(), 0
    while pos < len(data):
        if pos + 4 > len(data):
            raise BagError('truncated block header')
        (size,) = struct.unpack_from('<I', data, pos)
        pos += 4
        crc = None
        if fmt == 7:
            if pos + 4 > len(data):
                raise BagError('truncated block checksum')
            (crc,) = struct.unpack_from('<I', data, pos)
            pos += 4
        if size == 0 or pos + size > len(data):
            raise BagError(f'block {blocks}: size {size} runs past end of file')
        chunk = lz4_decompress(data[pos:pos + size])
        if crc is not None and crc32c(chunk) != crc:
            raise BagError(f'block {blocks}: CRC32C mismatch')
        out += chunk
        pos += size
        blocks += 1
    return Bag(fmt, tag, version, bytes(out), blocks)


def bag_encode(bag: Bag) -> bytes:
    head = struct.pack('<I4sI', bag.fmt, bag.tag, bag.version)
    if bag.fmt == 1:
        return head + bag.payload
    if bag.fmt not in (4, 7):
        raise BagError(f'cannot encode bag format {bag.fmt}')
    out = bytearray(head)
    for i in range(0, max(len(bag.payload), 1), BLOCK_SIZE):
        chunk = bag.payload[i:i + BLOCK_SIZE]
        comp = lz4hc_compress(chunk)
        out += struct.pack('<I', len(comp))
        if bag.fmt == 7:
            out += struct.pack('<I', crc32c(chunk))
        out += comp
    return bytes(out)


# --- Mod list ------------------------------------------------------------------------------------------------------

LIST_TAG = b'domb'
LIST_VERSION = 5
INFO_TAG = b'idom'


@dataclass
class ModEntry:
    name: str
    info_tag: bytes
    info: str
    enabled: int
    order: int
    path: str

    def to_json(self, index: int) -> dict:
        return {'index': index, 'name': self.name, 'info': self.info, 'enabled': bool(self.enabled),
                'enabledRaw': self.enabled, 'order': self.order, 'path': self.path,
                'local': self.path.lower().startswith('mods/')}


@dataclass
class ModList:
    bag: Bag
    entries: list[ModEntry] = field(default_factory=list)
    tail: bytes = b''


def _enc(s: str) -> bytes:
    return s.encode('utf-8', 'surrogateescape')


def _dec(b: bytes) -> str:
    return b.decode('utf-8', 'surrogateescape')


def modlist_decode(data: bytes) -> ModList:
    bag = bag_decode(data)
    if bag.tag != b'sdom':
        raise BagError(f"not a mod list (bag tag {bag.tag!r}, expected b'sdom')")
    p = bag.payload
    if len(p) < 12:
        raise BagError('mod list payload too short')
    tag, version, n = struct.unpack_from('<4sII', p, 0)
    if tag != LIST_TAG or version != LIST_VERSION:
        raise BagError(f'unsupported mod list {tag!r} v{version} (expected {LIST_TAG!r} v{LIST_VERSION})')
    pos = 12

    def u32() -> int:
        nonlocal pos
        if pos + 4 > len(p):
            raise BagError('mod list truncated')
        (v,) = struct.unpack_from('<I', p, pos)
        pos += 4
        return v

    def text() -> str:
        nonlocal pos
        size = u32()
        if pos + size > len(p):
            raise BagError('mod list string runs past end')
        s = _dec(p[pos:pos + size])
        pos += size
        return s

    entries = []
    for _ in range(n):
        name = text()
        if pos + 4 > len(p):
            raise BagError('mod list truncated')
        info_tag = p[pos:pos + 4]
        pos += 4
        info = text()
        enabled = u32()
        order = u32()
        path = text()
        entries.append(ModEntry(name, info_tag, info, enabled, order, path))
    return ModList(bag, entries, p[pos:])


def modlist_payload(ml: ModList) -> bytes:
    out = bytearray(struct.pack('<4sII', LIST_TAG, LIST_VERSION, len(ml.entries)))
    for e in ml.entries:
        for part in (_enc(e.name),):
            out += struct.pack('<I', len(part)) + part
        info = _enc(e.info)
        out += e.info_tag + struct.pack('<I', len(info)) + info
        out += struct.pack('<II', e.enabled & M32, e.order & M32)
        path = _enc(e.path)
        out += struct.pack('<I', len(path)) + path
    out += ml.tail
    return bytes(out)


def modlist_encode(ml: ModList) -> bytes:
    bag = Bag(ml.bag.fmt, ml.bag.tag, ml.bag.version, modlist_payload(ml))
    return bag_encode(bag)


SLUG_RE = re.compile(r'^[a-z0-9_]+$')


def find_entry(ml: ModList, slug: str) -> int | None:
    folder = f'mods/{slug}/'
    for i, e in enumerate(ml.entries):
        path = e.path.replace('\\', '/').lower()
        if path == folder or path == folder.rstrip('/') or e.info.replace('\\', '/').lower() == folder + 'modinfo.modi':
            return i
    return None


def find_workshop_entry(ml: ModList, published_id: str) -> int | None:
    """The Steam Workshop copy of a published mod: an entry outside mods/ whose folder is the Published Id
    (...\\workshop\\content\\228380\\<id>)."""
    for i, e in enumerate(ml.entries):
        path = e.path.replace('\\', '/').lower().rstrip('/')
        if not path.startswith('mods/') and path.rsplit('/', 1)[-1] == published_id:
            return i
    return None


def set_workshop_state(ml: ModList, published_id: str, enable: bool) -> dict:
    """Enable/disable the Workshop copy of mod ``published_id`` (never adds one: Steam does when you subscribe).
    A local install of a published bundle and its Workshop copy hold the same tracks and must not both be on."""
    if not re.match(r'^\d+$', published_id):
        raise BagError(f'invalid Workshop id {published_id!r} (expected digits)')
    i = find_workshop_entry(ml, published_id)
    if i is None:
        return {'action': 'absent', 'changed': False, 'entry': None}
    e = ml.entries[i]
    want = 1 if enable else 0
    if e.enabled == want:
        return {'action': 'noop', 'changed': False, 'entry': e.to_json(i)}
    before = e.enabled
    e.enabled = want
    return {'action': 'enabled' if enable else 'disabled', 'changed': True, 'before': before, 'entry': e.to_json(i)}


def set_state(ml: ModList, slug: str, enable: bool, name: str | None = None, rename: bool = False) -> dict:
    """Enable/disable (or add) mods/<slug>. `name` names a new entry; with `rename` it also renames an existing one."""
    if not SLUG_RE.match(slug):
        raise BagError(f'invalid slug {slug!r} (expected [a-z0-9_]+)')
    if rename and not (name and name.strip()):
        raise BagError('--rename needs a non-empty --name')
    i = find_entry(ml, slug)
    want = 1 if enable else 0
    if i is None:
        if not enable:
            return {'action': 'absent', 'changed': False, 'entry': None}
        orders = [e.order for e in ml.entries if e.order != ORDER_UNSET]
        entry = ModEntry(name or slug, INFO_TAG, f'mods/{slug}/modinfo.modi', 1,
                         (max(orders) + 1) if orders else len(ml.entries), f'mods/{slug}/')
        ml.entries.append(entry)
        return {'action': 'added', 'changed': True, 'entry': entry.to_json(len(ml.entries) - 1)}
    e = ml.entries[i]
    old_name = e.name
    renamed = rename and e.name != name
    if renamed:
        e.name = name
    if e.enabled == want:
        if renamed:
            return {'action': 'renamed', 'changed': True, 'renamedFrom': old_name, 'before': e.enabled,
                    'entry': e.to_json(i)}
        return {'action': 'noop', 'changed': False, 'entry': e.to_json(i)}
    before = e.enabled
    e.enabled = want
    result = {'action': 'enabled' if enable else 'disabled', 'changed': True, 'before': before, 'entry': e.to_json(i)}
    if renamed:
        result['renamedFrom'] = old_name
    return result


def modinfo_name(data: bytes) -> str:
    bag = bag_decode(data)
    if bag.tag != b'idom':
        raise BagError(f'not a modinfo.modi (tag {bag.tag!r})')
    p = bag.payload
    if len(p) < 4:
        raise BagError('modinfo payload too short')
    (size,) = struct.unpack_from('<I', p, 0)
    if 4 + size > len(p):
        raise BagError('modinfo name runs past end')
    return _dec(p[4:4 + size])


def describe(data: bytes) -> dict:
    ml = modlist_decode(data)
    return {'format': ml.bag.fmt, 'tag': _dec(ml.bag.tag), 'version': ml.bag.version, 'blocks': ml.bag.blocks,
            'payloadSize': len(ml.bag.payload), 'listVersion': LIST_VERSION, 'tailSize': len(ml.tail),
            'entries': [e.to_json(i) for i, e in enumerate(ml.entries)]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('info', help='decode and print the list as JSON')
    p.add_argument('path')
    p = sub.add_parser('set', help='enable/disable (or add) mods/<slug>; writes --out only when something changes')
    p.add_argument('path')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--slug')
    g.add_argument('--workshop', help="a Workshop copy's Published Id (switch it on or off; never added)")
    p.add_argument('--state', required=True, choices=['enabled', 'disabled'])
    p.add_argument('--name', help='display name for a new entry')
    p.add_argument('--rename', action='store_true', help='also rename an existing entry to --name')
    p.add_argument('--out')
    p = sub.add_parser('modinfo-name', help='print the display name from a modinfo.modi')
    p.add_argument('path')
    p = sub.add_parser('roundtrip', help='decode + re-encode and compare')
    p.add_argument('path')
    args = ap.parse_args(argv)

    try:
        if args.cmd == 'info':
            with open(args.path, 'rb') as f:
                result = describe(f.read())
        elif args.cmd == 'set':
            with open(args.path, 'rb') as f:
                data = f.read()
            ml = modlist_decode(data)
            if args.workshop:
                result = set_workshop_state(ml, args.workshop, args.state == 'enabled')
            else:
                result = set_state(ml, args.slug, args.state == 'enabled', args.name, args.rename)
            result['count'] = len(ml.entries)
            if result['changed']:
                encoded = modlist_encode(ml)
                check = modlist_decode(encoded)
                if modlist_payload(check) != modlist_payload(ml):
                    raise BagError('re-encoded list does not decode to the edited list')
                result['size'] = len(encoded)
                if args.out:
                    with open(args.out, 'wb') as f:
                        f.write(encoded)
                    result['out'] = args.out
        elif args.cmd == 'modinfo-name':
            with open(args.path, 'rb') as f:
                result = {'name': modinfo_name(f.read())}
        else:
            with open(args.path, 'rb') as f:
                data = f.read()
            ml = modlist_decode(data)
            encoded = modlist_encode(ml)
            again = modlist_decode(encoded)
            result = {'byteIdentical': encoded == data,
                      'decodeIdentical': again.bag.payload == ml.bag.payload and len(again.entries) == len(ml.entries),
                      'size': len(data), 'reencodedSize': len(encoded)}
    except (BagError, OSError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
