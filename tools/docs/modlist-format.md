# Wreckfest 1 client mod list (`modlist.mods`)

Location: `C:\Program Files (x86)\Steam\userdata\<steamUserId>\228380\local\wreckfest\modlist.mods`.
Read/written by `tools/wf_modlist.py` (stdlib-only Python) for `Install-TrackMod.ps1 -Enable/-Disable/-ListMods`.
The game rewrites this file when it exits, so only edit it while Wreckfest is closed.

## Bag container (format 7)

All integers little-endian u32.

| Offset | Value | Meaning |
|---|---|---|
| 0 | `7` | bag format: 1 = raw, 4 = LZ4 blocks, 7 = LZ4 blocks + CRC, 8 = encrypted |
| 4 | `sdom` | file tag (`mods` reversed) |
| 8 | `0` | file type version |
| 12… | blocks | repeated until EOF: `compressedSize`, `crc32c(uncompressed block)`, LZ4 block |

Format 4 is the same without the CRC word. Format 1 is the 12-byte header followed by the raw payload.
Each block is a plain LZ4 *block* (no frame). The game writes it with LZ4HC (level 9, lz4 1.9.x);
`wf_modlist.py` contains a port of that compressor and re-encodes the real file byte-identically.
The checksum is CRC32C (Castagnoli) of the block's decompressed bytes.

## Payload

```
'domb' u32 version=5  u32 count
count × entry:
  u32 len, name           display name (from the mod's modinfo.modi, e.g. "Cadwell")
  'idom' u32 len, info    modinfo reference: "mods/<slug>/modinfo.modi" or "<workshop dir>/modinfo.modi"
  u32 enabled             1 = enabled, 0 = disabled
  u32 order               load order; 0x7FFFFFFF = not placed yet (fresh Workshop subscription)
  u32 len, path           mod folder: "mods/<slug>/" (local) or "d:/steamlibrary/.../228380/<id>" (Workshop)
```

Field names come from BagEdit's schema (`bbMod` v5: `name`, `info`, `enabled`, `order`, `path`); Chris
confirmed `enabled = 1` is "on" in the Mods menu. New entries added by the helper copy the local layout,
with `order = max(order) + 1`, the way the game appended `Cadwell`.

## Sources

- gmazy/bag-decompress `main.c` (CC0): format 4/7 block layout (the extra word in 7 is skipped there).
- MaxxWyndham/Breckfest: format 4 writer.
- The CRC32C was identified by matching it against two real files.
