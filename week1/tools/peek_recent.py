"""看最近若干条原始记录，判断"不稳定"到底出在哪一项。

用法：
    python tools/peek_recent.py 60        # 看最近 60 条（约 1 分钟）
    python tools/peek_recent.py 1800 plot  # 看最近 1800 条并画温度/模长时间序列（文本图）

输出三块：
  1) 逐条原始读数（时间、温度、三轴、模长）
  2) 各项统计：最小值 / 最大值 / 波动幅度
  3) 诊断提示：温度是否在持续爬升、模长是否偏离 1.0
"""
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "readings.db"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()[::-1]
    if not rows:
        print("数据库里没有记录")
        return

    def hms(r):
        ms = r["server_ms"]
        return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S") if ms else "??"

    print(f"最近 {len(rows)} 条（{hms(rows[0])} ~ {hms(rows[-1])}）\n")
    print("  时间       温度    ax       ay       az       |a|")
    print("  " + "-" * 52)
    temps, norms = [], []
    for r in rows:
        a = (r["ax"] or 0, r["ay"] or 0, r["az"] or 0)
        m = math.sqrt(sum(v * v for v in a))
        t = r["temp_c"]
        temps.append(t)
        norms.append(m)
        ts = hms(r)
        tv = "  —  " if t is None else f"{t:5.1f}"
        print(f"  {ts}  {tv}  {a[0]:+.4f}  {a[1]:+.4f}  {a[2]:+.4f}   {m:.4f}")

    def stat(name, vals, unit=""):
        v = [x for x in vals if x is not None]
        if not v:
            print(f"  {name}: 无数据")
            return
        lo, hi = min(v), max(v)
        avg = sum(v) / len(v)
        sd = (sum((x - avg) ** 2 for x in v) / len(v)) ** 0.5
        print(f"  {name}: 平均 {avg:.3f}{unit}  范围 {lo:.3f}~{hi:.3f}"
              f"（波动 {hi - lo:.3f}）  标准差 {sd:.4f}")

    print("\n统计")
    stat("温度", temps, "°C")
    stat("模长", norms, " g")

    print("\n诊断")
    v = [x for x in temps if x is not None]
    if len(v) >= 10:
        half = len(v) // 2
        a1, a2 = sum(v[:half]) / half, sum(v[half:]) / (len(v) - half)
        d = a2 - a1
        if abs(d) < 0.5:
            print(f"  温度：前后半段均值差 {d:+.2f}°C —— 基本平稳，波动是读数噪声")
        else:
            print(f"  温度：前后半段均值差 {d:+.2f}°C —— 在持续{'上升' if d > 0 else '下降'}")
    nv = [x for x in norms if x is not None]
    if nv:
        avg = sum(nv) / len(nv)
        dev = abs(avg - 1.0)
        print(f"  模长：均值 {avg:.3f} g，偏离 1.0 约 {dev * 100:.1f}%"
              f" —— {'正常（静止）' if dev < 0.05 else '偏离较大，板子可能真的在动'}")


if __name__ == "__main__":
    main()
