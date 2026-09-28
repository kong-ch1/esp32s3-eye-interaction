#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""断网实测取证工具 —— 任务卡第3周「当堂验证」第①句

任务卡原文：
    「断开外网后，本地仍能确认按键已触发；
      无远端接收证据时不得显示"对方已收到"」

这个脚本负责**服务器侧**的那一半证据：在这段"设备送不出消息"的窗口里，
服务器究竟收到了什么。它刻意只做取证，不做别的。

三条自我约束：
  1. **不碰串口**。2026-09-23 的教训：本板 COM5 是 ESP32-S3 自带的 USB-Serial/JTAG，
     反复开关端口会扰动芯片复位状态，把板子搞下线（当时连累网络上报一起停了 82 秒）。
     所以灯效**只能由人的眼睛确认**，脚本不替你看灯，也不假装看过。
  2. **不伪造**。如果窗口内一条记录都没有，就如实报"0 条"。这个测试的价值恰恰在于
     "什么都没到" —— 把它润色成"部分到达"就等于把结论做反了。
  3. **不数"条数"，要找"静默段"**。这是本工具第一版踩的坑，值得写下来：
     第一版只判断"窗口内有没有新增记录"，结果把**取完基线到服务器真正停掉之间那 7 秒**
     的 6 条正常记录，判成了"消息其实送到了，窗口无效"。
     基线快照与"停掉服务"之间必然有几秒空隙，那几秒里的记录是完全正常的。
     正确做法是把时间轴画出来，找**最长的一段零记录静默**：那才是真正的断网窗口。

用法：
    # 1) 服务器还活着时，先取基线
    python tools/offline_test.py snapshot --tag before --out debug_logs/_offline_before.json

    # 2) 停掉服务器 → 按键 → 重启服务器

    # 3) 取窗口后的快照，并与基线对比
    python tools/offline_test.py snapshot --tag after --out debug_logs/_offline_after.json
    python tools/offline_test.py compare debug_logs/_offline_before.json debug_logs/_offline_after.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "readings.db")

# 一段"零记录"至少要持续这么久，才被认为是有效的断网窗口。
# 1Hz 上报下正常间隙 < 1.5 秒；30 秒足以把它和任何正常抖动区分开。
SILENCE_MIN_S = 30


def now_ms() -> int:
    return int(time.time() * 1000)


def fmt(ms) -> str:
    if not ms:
        return "-"
    return time.strftime("%H:%M:%S", time.localtime(ms / 1000)) + f".{int(ms) % 1000:03d}"


def dur(ms) -> str:
    s = abs(ms) / 1000.0
    if s < 90:
        return f"{s:.1f} 秒"
    if s < 5400:
        return f"{s / 60:.1f} 分"
    return f"{s / 3600:.2f} 小时"


def connect() -> sqlite3.Connection:
    if not os.path.exists(DB_PATH):
        sys.exit(f"✗ 找不到数据库：{DB_PATH}\n  （服务器至少启动过一次才会有）")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def snapshot(tag: str) -> dict:
    """取一份服务器侧证据基线。

    用"最大时间戳"而不是"条数"做锚点：条数会被删除或回滚干扰，
    而时间戳单调递增，不会被绕过。"""
    con = connect()
    snap: dict = {"tag": tag, "taken_ms": now_ms(), "db": DB_PATH}

    if table_exists(con, "readings"):
        r = con.execute(
            "SELECT COUNT(*) n, MAX(id) max_id, MAX(server_ms) max_ms FROM readings"
        ).fetchone()
        snap["readings"] = {"count": r["n"], "max_id": r["max_id"], "max_ms": r["max_ms"]}
    else:
        snap["readings"] = {"count": 0, "max_id": None, "max_ms": None}

    if table_exists(con, "help_events"):
        r = con.execute(
            "SELECT COUNT(*) n, MAX(created_ms) max_ms FROM help_events"
        ).fetchone()
        snap["help_events"] = {"count": r["n"], "max_ms": r["max_ms"]}
    else:
        snap["help_events"] = {"count": 0, "max_ms": None}

    if table_exists(con, "commands"):
        r = con.execute(
            "SELECT COUNT(*) n, MAX(created_ms) max_ms FROM commands"
        ).fetchone()
        snap["commands"] = {"count": r["n"], "max_ms": r["max_ms"]}
    else:
        snap["commands"] = {"count": 0, "max_ms": None}

    con.close()
    return snap


def rows_since(con: sqlite3.Connection, table: str, ts_col: str, since_ms: int,
               cols: str) -> list:
    if not table_exists(con, table):
        return []
    return [dict(x) for x in con.execute(
        f"SELECT {cols} FROM {table} WHERE {ts_col} > ? ORDER BY {ts_col}", (since_ms,)
    )]


def compare(before_path: str, after_path: str) -> int:
    b = json.load(open(before_path, encoding="utf-8"))
    a = json.load(open(after_path, encoding="utf-8"))
    lo, hi = b["taken_ms"], a["taken_ms"]
    lo_read = b["readings"]["max_ms"] or 0
    lo_help = b["help_events"]["max_ms"] or 0
    lo_cmd = b["commands"]["max_ms"] or 0

    con = connect()
    new_read = rows_since(con, "readings", "server_ms", lo_read,
                          "id, server_ms, request_id, trigger, button")
    new_help = rows_since(con, "help_events", "created_ms", lo_help,
                          "id, created_ms, button, state, ack_ms, press_delay_ms")
    new_cmd = rows_since(con, "commands", "created_ms", lo_cmd,
                         "id, created_ms, type, status")
    con.close()

    print("=" * 70)
    print("断网窗口 · 服务器侧证据")
    print("=" * 70)
    print(f"  基线时刻 : {fmt(lo)}   （{os.path.basename(before_path)}）")
    print(f"  复查时刻 : {fmt(hi)}   （{os.path.basename(after_path)}）")
    print(f"  窗口总长 : {dur(hi - lo)}")
    print()

    # ---- 找出时间轴上的"零记录静默段" ----
    ts = sorted(r["server_ms"] for r in new_read)
    bounds = [lo] + ts + [hi]
    segs = [(bounds[i], bounds[i + 1], bounds[i + 1] - bounds[i])
            for i in range(len(bounds) - 1)]
    g0, g1, glen = max(segs, key=lambda x: x[2]) if segs else (lo, hi, hi - lo)

    print("[传感记录 readings]")
    print(f"  窗口内新增 : {len(new_read)} 条")
    if new_read:
        print(f"  时间分布   : {fmt(ts[0])} ~ {fmt(ts[-1])}"
              f"（前 {dur(ts[-1] - ts[0] + 1000)} 内）")
    print(f"  最长静默段 : {dur(glen)}   {fmt(g0)} → {fmt(g1)}   期间 0 条记录")
    print()

    print("[求助事件 help_events]")
    print(f"  整个窗口新增 : {len(new_help)} 条")
    for r in new_help:
        print(f"    {r['id']}  {fmt(r['created_ms'])}  按键={r['button']}  "
              f"状态={r['state']}  回应={fmt(r['ack_ms'])}")
    help_in_gap = [h for h in new_help if h["created_ms"] > g0]
    print(f"  其中落在静默段内 : {len(help_in_gap)} 条")
    print()

    print("[下行指令 commands]")
    print(f"  整个窗口新增 : {len(new_cmd)} 条")
    for r in new_cmd[:10]:
        print(f"    {r['id']}  {fmt(r['created_ms'])}  type={r['type']}  status={r['status']}")
    print()

    print("-" * 70)
    checks: list[tuple[str, bool]] = []

    # 判读 ①：窗口里必须真的出现过一段足够长的静默，否则"服务器没停成"。
    ok_silence = glen >= SILENCE_MIN_S * 1000
    if ok_silence:
        checks.append((f"✓ 窗口内存在 {dur(glen)} 的零记录静默段"
                       f"（{fmt(g0)} → {fmt(g1)}）—— 服务器确实离线过", True))
    else:
        checks.append((f"✗ 最长静默只有 {dur(glen)}，不足 {SILENCE_MIN_S} 秒 ——"
                       " 服务器多半根本没停成，本次窗口无效，不能据此下结论", False))

    # 判读 ②：整段静默里不能有任何求助事件。
    # 上一个坑的教训：只数总数，会把"停之前那几秒的正常记录"也算进来。
    ok_help = len(help_in_gap) == 0
    if ok_help:
        checks.append(("✓ 静默段内求助事件 0 条 —— 这期间任何按键都**没有**变成"
                       "服务器上的一条记录", True))
    else:
        checks.append((f"✗ 静默段内出现了 {len(help_in_gap)} 条求助事件 ——"
                       " 设备在无法上报的情况下仍有记录进库，说明静默段不是真的断网", False))

    # 判读 ③：由 ② 推出任务卡那句硬规矩在结构上成立。
    if ok_silence and ok_help:
        checks.append(('✓ 因此页面上不可能显示这次按键的 ②VPS接收 / ③查看者回应 ——'
                       ' 任务卡「无远端接收证据时不得显示对方已收到」在数据层成立', True))

    for text, _ in checks:
        print("  " + text)
    print()
    print("-" * 70)
    print("  这一半证据只覆盖**服务器侧**。任务卡要的另一半是"
          "「本地仍能确认按键已触发」，")
    print("  它只能由**人的眼睛**看灯确认 —— 本脚本不读串口，也不替你下这个结论。")
    print("=" * 70)
    return 0 if all(ok for _, ok in checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="断网实测取证（服务器侧）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("snapshot", help="取一份证据基线")
    s1.add_argument("--tag", required=True)
    s1.add_argument("--out", required=True)

    s2 = sub.add_parser("compare", help="对比两个快照，回答窗口内到底收到了什么")
    s2.add_argument("before")
    s2.add_argument("after")

    args = ap.parse_args()

    if args.cmd == "snapshot":
        snap = snapshot(args.tag)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        print(f"✓ 已存快照 [{args.tag}] → {args.out}")
        print(f"   读数 max_id={snap['readings']['max_id']}  "
              f"求助事件 {snap['help_events']['count']} 条  "
              f"指令 {snap['commands']['count']} 条")
        return 0

    return compare(args.before, args.after)


if __name__ == "__main__":
    sys.exit(main())
