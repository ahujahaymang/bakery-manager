"""Generate solid-color placeholder PNG icons using only the Python stdlib.

Produces valid RGBA PNGs at exact dimensions (no Pillow dependency).
"""
import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png(path: str, size: int, rgba=(17, 24, 39, 255)) -> None:
    width = height = size
    # IHDR: 8-bit, color type 6 (RGBA)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    pixel = bytes(rgba)
    row = b"\x00" + pixel * width  # filter byte 0 + one row of pixels
    raw = row * height
    idat = zlib.compress(raw, 9)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )
    with open(path, "wb") as fh:
        fh.write(png)


if __name__ == "__main__":
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "public", "icons")
    os.makedirs(out, exist_ok=True)
    make_png(os.path.join(out, "icon-192.png"), 192)
    make_png(os.path.join(out, "icon-512.png"), 512)
    print("wrote icon-192.png and icon-512.png")
