#!/usr/bin/env python3
"""第2周补齐 —— 「执行失败」这条路径的真机验收。

任务卡要求 Web 能显示「完成、失败或超时」。超时早就验过了（故意不取指令，
30 秒后判定 accept 超时）；**失败**一直没验 —— 因为它在真实环境里很难复现：
要么拔网线（拔了之后设备连"我失败了"都报不上来），要么把服务器弄挂
（整个链路一起断，分不清是谁的问题）。

所以服务器提供了一个受限的故障注入口（--fault-injection）：
让接下来 N 次「指令采集上报」返回 503，板子就会如实回报失败。
关键是它**只拦样本、不拦回执**——「设备汇报结论」那条一定送得到，
于是测试有个确定的终点，而不是一路等到超时。

这个脚本做四件事：
  1. 确认注入口可用（没开就明确告诉你，不假装通过）
  2. 注入故障 → 下发采集指令 → 看板子是否如实回报 failed（且带原因）
  3. 核对库里确实一条样本都没进来（失败是"真失败"，不是嘴上说说）
  4. 撤掉故障后再采一次 → 成功，证明**是注入的故障导致的失败，不是板子坏了**

第 4 步是故意的：没有它，就没法排除"板子本来就有病"这个解释。

用法：
    python tools/fail_acceptance.py [--samples 6] [--interval-ms 120] [--save]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE.parent / "data" / "readings.db"
LOG_DIR = HERE.parent / "debug_logs"

BASE = "http://127.0.0.1:8000"
DEVICE_ID = "team01-esp32s3eye"

_LINES: list[str] = []


def say(msg: str = ""):
    print(msg)
    _LINES.append(msg)


def post(url: str, obj: dict, timeout: float = 20.0):
    req = urllib.request.Request(
        url, data=json.dumps(obj).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:200]}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def get(url: str, timeout: float = 15.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def rows_for(req_id: str) -> list[dict]:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        rs = con.execute(
            "SELECT id, seq, server_ms, trigger, cmd_state, cmd_note"
            " FROM readings WHERE request_id = ? ORDER BY id", (req_id,)).fetchall()
        return [dict(r) for r in rs]
    finally:
        con.close()


def wait_terminal(req_id: str, timeout_s: float = 45.0) -> dict:
    """等指令走到终态，顺便把每一步的推进打印出来。"""
    seen = None
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        _, c = get(f"{BASE}/api/command/{req_id}")
        if c.get("status") != seen:
            seen = c.get("status")
            say(f"    [{time.time()-t0:5.1f}s] 状态 → {seen}")
        if seen in ("done", "timeout", "failed"):
            return c
        time.sleep(0.4)
    _, c = get(f"{BASE}/api/command/{req_id}")
    return c


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--device", default=DEVICE_ID)
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--interval-ms", type=int, default=120)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    BASE = args.base.rstrip("/")
    dev = args.device

    say("=" * 72)
    say("第2周补齐 · 「执行失败」路径 真机验收")
    say("=" * 72)
    say(f"服务器: {BASE}    设备: {dev}")
    say(f"参数  : 采集 {args.samples} 条 / 间隔 {args.interval_ms} ms")

    st, _ = get(BASE + "/api/latest")
    if st != 200:
        say("\n服务器不可达。")
        return 1

    # ---- 1. 注入口是否可用 ----
    say("\n[1] 确认故障注入口可用")
    st, d = post(BASE + "/api/debug/fault", {"mode": "off"})
    if st == 403:
        say("    ✗ 注入口没开 —— 用 --fault-injection 启动服务器：")
        say("      python week1/server/server.py --fault-injection")
        say("    （失败这条路径没法在不注入故障的情况下真机复现，")
        say("      拔网线/弄挂服务器都会把回执信道一起切断，验不出东西）")
        return 1
    if st != 200:
        say(f"    ✗ 意外返回 HTTP {st}: {d}")
        return 1
    say("    ✓ 可用（--fault-injection 已开启）")

    # ---- 2. 板子在线确认 ----
    _, latest = get(BASE + "/api/latest")
    rec = latest.get("record") or {}
    age = rec.get("age_seconds")
    say(f"\n[2] 板子状态：最新 id={rec.get('id')} 数据年龄={age}s "
        f"trigger={rec.get('trigger')}")
    if age is None or age > 5:
        say("    板子当前没在上报，先确认它在跑再验收。")
        return 1

    ok = True

    # ---- 3. 注入故障并下发指令 ----
    say(f"\n[3] 注入故障：接下来 {args.samples} 条指令采集上报一律 503")
    st, d = post(BASE + "/api/debug/fault",
                 {"mode": "command_data", "times": args.samples})
    say(f"    HTTP {st}  {d}")

    say(f"\n[4] 下发采集指令（采集 {args.samples} 条）")
    st, j = post(BASE + "/api/command",
                 {"device_id": dev, "type": "recollect",
                  "samples": args.samples, "interval_ms": args.interval_ms})
    if st != 202:
        say(f"    ✗ HTTP {st}: {j}")
        return 1
    rid = j["request_id"]
    say(f"    受理 {rid}")

    cmd = wait_terminal(rid)
    if cmd.get("status") != "failed":
        say(f"\n    ✗ 期望 failed，实际 {cmd.get('status')}")
        if cmd.get("status") == "timeout":
            say("      （是超时而不是失败：设备没能把回执送出来，"
                "可能回执信道的排除条件写错了）")
        ok = False
    else:
        say(f"\n    ✓ 板子如实回报了「失败」")
        say(f"      状态      : {cmd['status']}")
        say(f"      设备备注  : {cmd.get('note')}")
        say(f"      failed_at : {cmd.get('failed_at')}")
        say(f"      总耗时    : {(cmd.get('legs_ms') or {}).get('total_ms')} ms")
        say(f"      timeout_ms: {cmd.get('timeout_ms')}（应为 None —— 是失败不是超时）")
        if not cmd.get("note"):
            say("    ✗ 没记下原因 —— 只知道失败、不知道为什么")
            ok = False
        if cmd.get("timeout_ms"):
            say("    ✗ failed 与 timeout 混在一起了")
            ok = False

    # ---- 5. 库里核对 ----
    say("\n[5] 核对库内记录：样本应当**一条都没进来**")
    rs = rows_for(rid)
    say(f"    带 {rid} 的记录共 {len(rs)} 条：")
    for r in rs:
        say(f"      id={r['id']} seq={r['seq']} trigger={r['trigger']} "
            f"cmd_state={r['cmd_state']} note={r['cmd_note']}")
    n_sample = sum(1 for r in rs if r["cmd_state"] is None)
    n_verdict = sum(1 for r in rs if r["cmd_state"] == "failed")
    say(f"    样本（cmd_state 为空）: {n_sample} 条（期望 0）")
    say(f"    失败回执              : {n_verdict} 条（期望 1）")
    if n_sample != 0:
        say("    ✗ 居然有样本进来了 —— 故障没生效，这次不算验过")
        ok = False
    elif n_verdict != 1:
        say("    ✗ 没有失败回执 —— 那 failed 状态是怎么来的？")
        ok = False
    else:
        say("    ✓ 0 条样本 + 1 条失败回执：失败是**真的**，不是嘴上说说")

    # ---- 6. 撤掉故障，证明板子本身是好的 ----
    say("\n[6] 撤掉故障，再采一次 —— 证明是注入的故障导致的失败，不是板子坏了")
    post(BASE + "/api/debug/fault", {"mode": "off"})
    st, j = post(BASE + "/api/command",
                 {"device_id": dev, "type": "recollect",
                  "samples": 4, "interval_ms": 120})
    if st != 202:
        say(f"    ✗ HTTP {st}: {j}")
        ok = False
    else:
        rid2 = j["request_id"]
        say(f"    受理 {rid2}")
        cmd2 = wait_terminal(rid2)
        say(f"    状态 = {cmd2.get('status')}")
        if cmd2.get("status") != "done":
            say("    ✗ 撤掉故障后仍然失败 —— 那就说不清是注入的锅还是板子的锅")
            ok = False
        else:
            say(f"    ✓ 恢复正常（新采集 {cmd2.get('sample_count')} 条，"
                f"端到端 {(cmd2.get('legs_ms') or {}).get('total_ms')} ms）")
            say("      → 同一块板子、同一条链路，注入故障就失败、撤掉就成功，")
            say("        failed 这条路径因此是可信的")

    say("\n" + "=" * 72)
    say("结果: " + ("全部通过 ✓" if ok else "存在失败项 ✗"))
    say("=" * 72)

    if args.save:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        name = time.strftime("_w2_fail_acceptance_%Y%m%d_%H%M%S.txt")
        (LOG_DIR / name).write_text("\n".join(_LINES) + "\n", encoding="utf-8")
        say(f"\n原始输出已保存: debug_logs/{name}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
