#!/usr/bin/env python3
"""静止状态下，加速度读数到底抖多少？抖动和芯片温度有关系吗？

用法：
    python tools/analyze_noise.py [样本条数] [设备名]

默认取最近 600 条（约 10 分钟，板子 1 秒 1 条）。
输出：每轴的均值/标准差/峰峰值（单位 g 和 mg），以及温度与读数的相关系数。

"标准差"就是抖动的典型幅度；"峰峰值"是这段时间内最大最小之差。
1 mg = 0.001 g，即千分之一倍重力。
"""
from __future__ import annotations

import math
import os
import sqlite3
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(os.path.dirname(HERE), "data", "readings.db")

N = int(sys.argv[1]) if len(sys.argv) > 1 else 600
DEVICE = sys.argv[2] if len(sys.argv) > 2 else "team01-esp32s3eye"


def stats(vals):
    return {
        "mean": st.fmean(vals),
        "sd": st.pstdev(vals),
        "min": min(vals),
        "max": max(vals),
        "p2p": max(vals) - min(vals),
    }


def corr(xs, ys):
    """皮尔逊相关系数：+1 完全同向，-1 完全反向，0 没关系。"""
    if len(xs) < 3:
        return float("nan")
    try:
        return st.correlation(xs, ys)
    except Exception:
        return float("nan")


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ax, ay, az, temp_c, server_ms FROM readings"
        " WHERE device_id = ? AND ax IS NOT NULL AND temp_c IS NOT NULL"
        " ORDER BY id DESC LIMIT ?",
        (DEVICE, N),
    ).fetchall()
    rows = list(reversed(rows))          # 改成时间正序

    if len(rows) < 10:
        print("有效样本不足（需要同时有加速度和温度的至少 10 条）：%d 条" % len(rows))
        return

    t0, t1 = rows[0]["server_ms"], rows[-1]["server_ms"]
    print("设备: %s" % DEVICE)
    print("样本: %d 条，跨度 %.1f 分钟" % (len(rows), (t1 - t0) / 60000.0))
    print()

    axes = {
        "ax": [r["ax"] for r in rows],
        "ay": [r["ay"] for r in rows],
        "az": [r["az"] for r in rows],
    }
    norms = [math.sqrt(r["ax"] ** 2 + r["ay"] ** 2 + r["az"] ** 2) for r in rows]
    temps = [r["temp_c"] for r in rows]

    print("%-6s %10s %10s %10s %10s" % ("", "均值(g)", "标准差(mg)", "峰峰值(mg)", "最小~最大(g)"))
    for k in ("ax", "ay", "az"):
        s = stats(axes[k])
        print("%-6s %10.4f %10.2f %10.2f   %.4f ~ %.4f"
              % (k, s["mean"], s["sd"] * 1000, s["p2p"] * 1000, s["min"], s["max"]))
    s = stats(norms)
    print("%-6s %10.4f %10.2f %10.2f   %.4f ~ %.4f"
          % ("|a|", s["mean"], s["sd"] * 1000, s["p2p"] * 1000, s["min"], s["max"]))
    print()

    ts = stats(temps)
    print("芯片温度: 均值 %.1f C，标准差 %.2f C，范围 %.1f ~ %.1f C（波动 %.1f C）"
          % (ts["mean"], ts["sd"], ts["min"], ts["max"], ts["p2p"]))
    print()
    print("相关系数（越接近 ±1 说明两者关系越强，0 附近=无关）：")
    print("  温度 vs |a|   : %+.3f" % corr(temps, norms))
    for k in ("ax", "ay", "az"):
        print("  温度 vs %-4s  : %+.3f" % (k, corr(temps, axes[k])))


if __name__ == "__main__":
    main()
