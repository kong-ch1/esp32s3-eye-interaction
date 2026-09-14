"""检查数据上报的连续性：间隔分布 + 找出所有"断流"缺口。

用法：
    python tools/gap_check.py            # 最近 1800 条（约 30 分钟）
    python tools/gap_check.py 600

看三件事：
  1) 间隔分布：大部分是不是 1 秒，长尾有多严重
  2) 缺口清单：超过 3 秒的空白都发生在什么时刻、断了多久
  3) 断流占比：掉线时间 / 总时间
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "readings.db"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, seq, server_ms, device_ms FROM readings"
        " ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()[::-1]
    if len(rows) < 3:
        print("数据太少")
        return

    ts = [r["server_ms"] for r in rows]
    gaps = [(ts[i + 1] - ts[i]) / 1000.0 for i in range(len(ts) - 1)]
    gaps_ok = [g for g in gaps if g < 100]  # 过滤跨重启的超长间隔

    def pct(p):
        s = sorted(gaps_ok)
        return s[min(len(s) - 1, int(len(s) * p))]

    print(f"样本 {len(rows)} 条，{datetime.fromtimestamp(ts[0]/1000).strftime('%H:%M:%S')}"
          f" ~ {datetime.fromtimestamp(ts[-1]/1000).strftime('%H:%M:%S')}"
          f"（跨度 {(ts[-1]-ts[0])/1000/60:.1f} 分钟）\n")

    print("间隔分布（秒）")
    print(f"  中位数 {pct(.5):.2f}   75% {pct(.75):.2f}   90% {pct(.90):.2f}"
          f"   99% {pct(.99):.2f}   最长 {max(gaps_ok):.1f}")
    for lo, hi, name in [(0, 1.5, "≈1秒（正常）"), (1.5, 3, "1.5~3秒（略卡）"),
                         (3, 10, "3~10秒（明显卡）"), (10, 1e9, "10秒以上（断流）")]:
        c = sum(1 for g in gaps if lo <= g < hi)
        print(f"  {name:<16} {c:>5} 次  {c / len(gaps) * 100:5.1f}%")

    big = [(i, g) for i, g in enumerate(gaps) if g >= 3]
    print(f"\n缺口清单（≥3 秒，共 {len(big)} 处）")
    for i, g in sorted(big, key=lambda x: -x[1])[:15]:
        t = datetime.fromtimestamp(ts[i] / 1000).strftime("%H:%M:%S")
        lost = max(0, round(g) - 1)
        print(f"  {t} 之后断了 {g:5.1f} 秒（约丢失 {lost} 条）")

    total = (ts[-1] - ts[0]) / 1000
    lost_time = sum(g - 1 for g in gaps if 1 < g < 100)
    print(f"\n总时长 {total:.0f} 秒，其中断流累计 {lost_time:.0f} 秒"
          f"（{lost_time / total * 100:.1f}%）")

    seqs = [r["seq"] for r in rows if r["seq"] is not None]
    if len(seqs) > 2:
        jumps = [(rows[i + 1]["seq"] - rows[i]["seq"]) for i in range(len(rows) - 1)]
        missed = sum(j - 1 for j in jumps if 1 < j < 10000)
        print(f"板端 seq 累计跳过 {missed} 条"
              f" —— {'板子自己没发（板端问题）' if missed > len(big) * 0.5 else '发了但没到（网络/服务器问题）'}")


if __name__ == "__main__":
    main()
