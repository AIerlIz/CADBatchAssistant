"""生成占位 logo(纯标准库,零依赖):assets/logo.png + assets/logo.ico。

图案:深蓝圆角方块 + 居中白色点阵 "CAD" 文字(第一版设计)。

- logo.png : 256x256 RGBA,供欢迎窗口/窗口图标显示
- logo.ico : 多尺寸(16/32/48/256)ICO(各尺寸直接渲染),供 exe 文件
  图标与 iconbitmap 使用(PyInstaller EXE(icon=...) 直接引用)。
  多尺寸保证 Windows 资源管理器各视图(小图标/详细信息)均正常显示。

用法:uv run python scripts/gen_logo.py
之后替换正式 logo 时,直接覆盖 assets/logo.png / logo.ico 即可。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 256
RADIUS = 56                      # 圆角半径(256 尺寸)
BG = (31, 78, 121, 255)          # 深蓝背景
FG = (255, 255, 255, 255)        # 白色前景

# 5x7 点阵字库(C/A/D)
GLYPHS = {
    "C": [0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110],
    "A": [0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
    "D": [0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110],
}
GLYPH_W, GLYPH_H = 5, 7
LETTER_SPACING = 2               # 字母间距(格)

# ICO 内包含的尺寸
ICO_SIZES = (16, 32, 48, 256)


def _in_rounded_rect(x: int, y: int, x0: int, y0: int,
                     x1: int, y1: int, r: int) -> bool:
    """点 (x,y) 是否在圆角矩形 [x0,x1]x[y0,y1] 内(圆角半径 r)。"""
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _render_pixels(size: int = SIZE) -> list[list[tuple[int, int, int, int]]]:
    """逐像素绘制:深蓝圆角方块 + 居中白色 CAD 点阵文字。

    scale 随尺寸自适应(小尺寸 16/32/48 也直接渲染,保持清晰)。
    """
    text = "CAD"
    grid_w = len(text) * GLYPH_W + (len(text) - 1) * LETTER_SPACING
    scale = max(1, round(size / 21))       # 21 = grid 19 格 + 边距
    px_w, px_h = grid_w * scale, GLYPH_H * scale
    off_x = (size - px_w) // 2
    off_y = (size - px_h) // 2
    r = max(1, round(size * RADIUS / SIZE))

    px = [[(0, 0, 0, 0)] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            if not _in_rounded_rect(x, y, 0, 0, size - 1, size - 1, r):
                continue
            px[y][x] = BG
            # 文字像素判定(居中)
            gx = (x - off_x) // scale
            gy = (y - off_y) // scale
            if not (0 <= gy < GLYPH_H and 0 <= gx < grid_w):
                continue
            li = gx // (GLYPH_W + LETTER_SPACING)
            col = gx % (GLYPH_W + LETTER_SPACING)
            if li < len(text) and col < GLYPH_W:
                glyph = GLYPHS[text[li]]
                if glyph[gy] >> (GLYPH_W - 1 - col) & 1:
                    px[y][x] = FG
    return px


def make_png(pixels, w: int, h: int) -> bytes:
    """RGBA 像素 → PNG 字节(标准库 zlib)。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(
        b"\x00" + b"".join(struct.pack("4B", *pixels[y][x]) for x in range(w))
        for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def make_ico(images: list[tuple[int, int, bytes]]) -> bytes:
    """多尺寸 PNG 内嵌的 ICO 字节。images = [(w, h, png), ...]。"""
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    payload = b""
    offset = 6 + 16 * len(images)
    for w, h, png in images:
        entries += struct.pack("<BBBBHHII",
                               w % 256, h % 256, 0, 0, 1, 32,
                               len(png), offset)
        payload += png
        offset += len(png)
    return header + entries + payload


def main() -> int:
    ico_images = []
    for s in ICO_SIZES:
        px = _render_pixels(s)
        ico_images.append((s, s, make_png(px, s, s)))
    ico_images.sort(key=lambda t: t[0])
    ico = make_ico(ico_images)

    assets = Path(__file__).resolve().parent.parent / "assets"
    assets.mkdir(exist_ok=True)
    png_256 = ico_images[-1][2]
    (assets / "logo.png").write_bytes(png_256)
    (assets / "logo.ico").write_bytes(ico)
    print(f"已生成: {assets / 'logo.png'} ({len(png_256)} B)")
    print(f"已生成: {assets / 'logo.ico'} ({len(ico)} B, "
          f"尺寸 {[s for s, _, _ in ico_images]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
