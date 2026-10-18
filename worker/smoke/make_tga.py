"""Write a small uncompressed 32-bit BGRA .tga test texture (checker grid). No third-party deps.

usage: python make_tga.py OUT.tga [SIZE]
"""
import struct
import sys


def make_tga(path: str, size: int = 256) -> None:
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, size, size, 32, 8)
    rows = bytearray()
    for y in range(size):
        for x in range(size):
            on = ((x // 32) + (y // 32)) % 2 == 0
            rows += bytes((40, 40, 40, 255)) if on else bytes((200, 200, 200, 255))
    with open(path, "wb") as f:
        f.write(header)
        f.write(rows)
    print(f"wrote {path} ({size}x{size}, {len(header) + len(rows)} bytes)")


if __name__ == "__main__":
    make_tga(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 256)
