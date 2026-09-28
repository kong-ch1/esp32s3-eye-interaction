#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板子会话连续性检查 —— 回答"设备这段时间重启过吗、重启了几次"

## 为什么需要这个工具

2026-09-23 我在排查断网测试时，先用"seq 变小就是重启"去猜，结果报了 118 处
"重启"——里面 `seq=99999`、`3005` 之类明显是第1周那个**数据模拟器**
（device_id=selftest-sim）写进去的记录。然后我又改用"device_ms 变大没变大"，
印出了"板子没有重启，一直同一会话在跑"——**这个结论也是错的**：
那 4.63 小时里 device_ms 只涨了 75 秒，同一会话本该涨到约 16670 秒。

两次都产出了"看起来对的错误证据"。所以把判据固定下来，别再手搓：

    **隐含开机时刻 = server_ms - device_ms**

同一会话里它基本恒定（只受网络抖动影响，实测 0.5~2.5 秒）；
一旦跳变超过容差，就是换了会话 = 板子重启过。
这比"看 seq 大小""看 device_ms 大小"都可靠，因为它是**两个时钟之差**，
只有真正的重启才能让它跳变。

## 用法

    python tools/session_check.py                    # 真机，全部历史
    python tools/session_check.py --since 2026-09-23 # 只看某天起
    python tools/session_check.py --device team01-esp32s3eye
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import statistics
import sys
import time
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "readings.db")

DEFAULT_DEVICE = "team01-esp32s3eye"
# 隐含开机时刻的容差：会话内网络抖动 0.5~2.5 秒，留 15 秒足够宽松又不至于漏判。
BOOT_JUMP_TOLERANCE_MS = 15_000


def fmt(ms) -> str:
    if ms is None:
        return "-"
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ms / 1000))


def dur(ms) -> str:
    s = abs(ms) / 1000.0
    if s < 90:
        return f"{s:.1f} 秒"
    if s < 5400:
        return f"{s / 60:.1f} 分"
    return f"{s / 3600:.2f} 小时"


def main() -> int:
    ap = argparse.ArgumentParser(description="板子会话连续性检查")
    ap.add_argument("--device", default=DEFAULT_DEVICE)
    ap.add_argument("--since", default=None,
                    help="只统计这个时间之后（YYYY-MM-DD 或 'YYYY-MM-DD HH:MM:SS'）")
    ap.add_argument("--tolerance", type=int, default=BOOT_JUMP_TOLERANCE_MS,
                    help="隐含开机时刻的跳变容差（毫秒）")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        sys.exit(f"✗ 找不到数据库：{DB_PATH}")

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    sql = ("SELECT id, server_ms, device_ms, seq, src_ip FROM readings "
           "WHERE device_id=? AND device_ms IS NOT NULL "
           "AND seq IS NOT NULL AND seq != 99999 ")
    params: list = [args.device]
    if args.since:
        s = args.since if " " in args.since else args.since + " 00:00:00"
        lo = int(time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S")) * 1000)
        sql += "AND server_ms >= ? "
        params.append(lo)
    sql += "ORDER BY id"

    rows = con.execute(sql, params).fetchall()
    con.close()

    if not rows:
        sys.exit(f"✗ 没有符合条件的记录（device={args.device}）")

    print("=" * 72)
    print(f"板子会话连续性 · device={args.device}")
    if args.since:
        print(f"统计范围 : {args.since} 起")
    print(f"记录条数 : {len(rows)}    id {rows[0]['id']} ~ {rows[-1]['id']}")
    print(f"时间范围 : {fmt(rows[0]['server_ms'])} ~ {fmt(rows[-1]['server_ms'])}")
    print("=" * 72)
    print()

    # ---- 分会话 ----
    #
    # 判据是「隐含开机时刻的**持续**偏离」，不是「某一条偏了」。
    # 这条工具连同前两版一共踩了三个坑，都是同一类错误（产出看着对的错证据）：
    #   ① 用 seq 变小判断重启 → 把第1周数据模拟器（seq=99999）的 68 条记录也算进去，
    #      报出 118 处"重启"。
    #   ② 用"上一条记录的隐含开机时刻"当基准 → 板子偶尔把**早先构造好的报文迟到送达**
    #      （实测 10:37:58 到达的 seq=1260 夹在 seq=1265 与 1267 之间，device_ms 也偏小），
    #      这种报文会让隐含开机时刻猛前跳几十秒；一次迟到就把基准永久推偏，
    #      后面所有正常记录都被判成新会话 → 8 个会话被报成 19 个。
    #   ③ 改用滚动中位数 → 只把损害从"整段"缩到"离群值自己那一条"，
    #      因为触发切分的正是那条离群记录本身，中位数管不到它。
    #
    # 真正的重启会让**连续多条**记录都稳定停在新水平上；迟到报文只有一两条。
    # 所以：偏离的记录先进 pending，攒够 PERSIST_N 条且彼此一致，才确认换会话；
    # 否则当作迟到报文，归回当前会话。PERSIST_N 取 5 —— 迟到报文实测 1~2 条，
    # 重启后至少会连续上报，5 条是很宽的余量。
    PERSIST_N = 5

    boots = [r["server_ms"] - r["device_ms"] for r in rows]
    labels = [0] * len(rows)
    sess = 1
    cur_boots: list[int] = []
    pending: list[int] = []          # 待确认的"可能新会话"记录下标

    for i, boot in enumerate(boots):
        med = statistics.median(cur_boots) if cur_boots else None
        if med is not None and abs(boot - med) > args.tolerance:
            pending.append(i)
            pb = [boots[j] for j in pending]
            if len(pending) >= PERSIST_N and (max(pb) - min(pb)) <= args.tolerance:
                sess += 1                      # 确认重启：pending 升级为新会话
                for j in pending:
                    labels[j] = sess
                cur_boots = list(pb)
                pending = []
            continue
        # 与当前会话相符（或是全表第一条）
        if pending:                            # 迟到的离群值收尾：归回当前会话
            for j in pending:
                labels[j] = sess
            cur_boots.extend(boots[j] for j in pending)
            pending = []
        labels[i] = sess
        cur_boots.append(boot)
    for j in pending:                          # 结尾的悬而未决：同样按迟到处理
        labels[j] = sess

    sessions = []
    for i, r in enumerate(rows):
        if not sessions or sessions[-1]["label"] != labels[i]:
            sessions.append({"label": labels[i], "boots": [boots[i]],
                             "start_id": r["id"], "end_id": r["id"],
                             "start_ms": r["server_ms"], "end_ms": r["server_ms"],
                             "n": 1, "ip": r["src_ip"]})
        else:
            s = sessions[-1]
            s["boots"].append(boots[i])
            s["end_id"] = r["id"]
            s["end_ms"] = r["server_ms"]
            s["n"] += 1
    for s in sessions:
        s["boot_med"] = int(statistics.median(s["boots"]))

    print(f"共识别出 {len(sessions)} 个会话（板子开机次数 ≥ {len(sessions)}）")
    print()
    print("  会话   推算开机时刻        起止（服务器时间）              记录数  板子IP")
    print("  ----   ----------------   ------------------------------   ------  --------------")
    for i, s in enumerate(sessions, 1):
        mark = "  ← 记录极少，可能是迟到报文而非真重启" if s["n"] < PERSIST_N else ""
        print(f"  #{i:<4} {fmt(s['boot_med'])}        {fmt(s['start_ms'])} → {fmt(s['end_ms'])}  "
              f"{s['n']:>6}  {s['ip'] or '-'}{mark}")

    print()
    print("-" * 72)
    if len(sessions) == 1:
        print(f"  ✓ 全程只有 1 个会话 —— 板子**没有重启过**，"
              f"持续 {dur(sessions[0]['end_ms'] - sessions[0]['start_ms'])}")
    else:
        print(f"  ⚠ 共 {len(sessions)} 个会话 —— 板子在这段时间里**重启过 "
              f"{len(sessions) - 1} 次**。")
        print("     每次重启的估算开机时刻：")
        for i in range(1, len(sessions)):
            prev, nxt = sessions[i - 1], sessions[i]
            boot = nxt["boot_med"]
            dead_gap = nxt["start_ms"] - prev["end_ms"]
            print(f"       上一次会话最后一条 : {fmt(prev['end_ms'])}")
            print(f"       新会话推算开机     : {fmt(boot)}"
                  f"（#{i + 1} 首条记录 {fmt(nxt['start_ms'])}）")
            print(f"       两次之间           : {dur(dead_gap)}"
                  f"（含服务器离线、无法记录的部分）")

    print()
    print("  说明：只在**服务器收得到**的时候才能看出会话。若服务器离线期间板子重启，")
    print("       这里只能看到'重启后'的那一次开机；离线期间重启了几次无法从库里得知。")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
