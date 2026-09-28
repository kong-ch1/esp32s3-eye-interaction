#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图取样器 —— 用像素说话，不靠眼睛判断

来由：2026-09-28 改网页时，用 CDP 模拟深色模式后，页面里 JS 回读
`getComputedStyle(document.body).backgroundColor` 明确是 rgb(25,25,23)（深色），
但截出来的 PNG 看着还是浅色。两边矛盾，说明**矛盾本身就是线索**：
问题出在截图这一环，不在 CSS。这类事不该靠"我看着像浅色"来定论，
所以写个取样器，直接读像素。

只依赖标准库（zlib）。PNG 是 zlib 压缩 + 逐行滤波，解出来不难，
比在沙箱里折腾装 Pillow 稳。

用法：
    python tools/img_sample.py 图片.png                 # 平均色 + 角落取样
    python tools/img_sample.py a.png b.png              # 多张对比
    python tools/img_sample.py 图.png --at 20,20,640,1200
"""
from __future__ import annotations

import argparse
import struct
import sys
import zlib


def load_png(path: str):
    """返回 (宽, 高, 逐像素 RGB 的 bytearray)。只支持 8 位 RGB/RGBA/灰度。"""
    raw = open(path, "rb").read()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit(f"✗ {path} 不是 PNG")

    pos = 8
    width = height = bitdepth = colortype = None
    idat = bytearray()
    while pos < len(raw):
        (ln,) = struct.unpack(">I", raw[pos:pos + 4])
        ctype = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if ctype == b"IHDR":
            width, height, bitdepth, colortype = struct.unpack(">IIBB", data[:10])
        elif ctype == b"IDAT":
            idat += data
        elif ctype == b"IEND":
            break

    if bitdepth != 8:
        sys.exit(f"✗ 只支持 8 位色深，这张是 {bitdepth} 位")
    ch = {0: 1, 2: 3, 4: 2, 6: 4}.get(colortype)
    if ch is None:
        sys.exit(f"✗ 不支持的色彩类型 {colortype}")

    buf = zlib.decompress(bytes(idat))
    stride = width * ch
    out = bytearray(width * height * ch)
    prev = bytearray(stride)

    p = 0
    for y in range(height):
        f = buf[p]; p += 1
        line = bytearray(buf[p:p + stride]); p += stride
        # 逐行反滤波（PNG 规范里的 5 种滤波器）
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line

    return width, height, ch, out


def rgb_at(w, ch, buf, x, y):
    o = (y * w + x) * ch
    if ch >= 3:
        return buf[o], buf[o + 1], buf[o + 2]
    return buf[o], buf[o], buf[o]


def luma(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def describe(path: str, points=None) -> dict:
    w, h, ch, buf = load_png(path)
    total = sum(luma(rgb_at(w, ch, buf, x, y))
                for y in range(0, h, max(1, h // 120))
                for x in range(0, w, max(1, w // 120)))
    n = len(range(0, h, max(1, h // 120))) * len(range(0, w, max(1, w // 120)))
    avg = total / n

    print(f"{path}")
    print(f"  尺寸 {w}x{h}   通道 {ch}   平均亮度 {avg:6.1f}/255  "
          f"→ 整体偏{'亮（浅色底）' if avg > 140 else '暗（深色底）'}")
    pts = points or [(5, 5), (w // 2, 40), (w - 6, 5), (w // 2, h - 6)]
    for (x, y) in pts:
        x = max(0, min(w - 1, x)); y = max(0, min(h - 1, y))
        c = rgb_at(w, ch, buf, x, y)
        print(f"    ({x:5d},{y:5d})  rgb{tuple(c)}  亮度 {luma(c):6.1f}")
    return {"w": w, "h": h, "avg": avg}


def main() -> int:
    ap = argparse.ArgumentParser(description="PNG 像素取样（标准库实现）")
    ap.add_argument("images", nargs="+")
    ap.add_argument("--at", default=None,
                    help="自定义取样点，形如 20,20,640,1200（依次为 x,y 对）")
    args = ap.parse_args()

    pts = None
    if args.at:
        v = [int(t) for t in args.at.split(",")]
        if len(v) % 2:
            sys.exit("✗ --at 要给偶数个数字")
        pts = list(zip(v[0::2], v[1::2]))

    res = []
    for p in args.images:
        res.append(describe(p, pts))
        print()
    if len(res) == 2:
        d = res[1]["avg"] - res[0]["avg"]
        print(f"两张图平均亮度差 {d:+.1f} —— "
              f"{'明显不同，说明配色确实变了' if abs(d) > 25 else '几乎一样，配色其实没变'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
