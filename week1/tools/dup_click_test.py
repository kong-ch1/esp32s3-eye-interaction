#!/usr/bin/env python3
"""第2周补齐 —— 「重复点击」与「失败态」的服务器级测试。

背景（任务卡要求）：Web 要能显示「完成 / 失败 / 超时」，并测试重复点击。

这个脚本不用真板子，因为它要验证的是**服务器的策略**，不是硬件行为：
界面上的按钮 disabled 只能管住自己那个标签页；真正拦不住的是
「两个浏览器 / 两台手机同时点」——所以闸门必须在服务器，也必须在这里测。

它自己扮演板子（用真实协议回报 received / done / failed），
所以整条回执链路都是真的，没有任何 mock。跑完会把自己造的测试数据删掉，
不留痕迹污染交付用的数据库。

用法：
    python tools/dup_click_test.py [--base http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE.parent / "data" / "readings.db"

# 刻意用一个明显是测试设备的 id：既不会抢走发给真板子的指令，
# 也便于把这批测试数据从库里摘干净。
DEV = "dupclick-sim"
BASE = "http://127.0.0.1:8000"


def post(url: str, obj: dict, timeout: float = 15.0):
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
    except Exception as e:                       # 连不上
        return 0, {"error": f"{type(e).__name__}: {e}"}


def get(url: str, timeout: float = 15.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def issue(ctype: str, extra: dict | None = None):
    body = {"device_id": DEV, "type": ctype}
    body.update(extra or {})
    return post(BASE + "/api/command", body)


def report_as_board(request_id: str, cmd_state: str, note: str | None = None):
    """假装自己是板子，按真实协议回报一条回执。

    trigger 必须是 'command' —— 服务器只认这个来源推进指令状态机。
    """
    body = {
        "device_id": DEV, "seq": 1, "ts": 1,
        "ax": 0.0, "ay": 0.0, "az": 1.0,
        "trigger": "command", "request_id": request_id, "cmd_state": cmd_state,
    }
    if note:
        body["cmd_note"] = note
    return post(BASE + "/api/data", body)


def cmd_of(request_id: str) -> dict:
    _, d = get(f"{BASE}/api/command/{request_id}")
    return d


def active_count(ctype: str) -> int:
    """直接从库里数——不信接口的自我陈述，看落盘的真相。"""
    con = sqlite3.connect(DB)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM commands WHERE device_id = ? AND type = ?"
            " AND status IN ('issued','delivered','received')",
            (DEV, ctype)).fetchone()[0]
    finally:
        con.close()
    return n


def cleanup():
    """把本次测试造的指令删掉（只删自己这个 device_id，别的一个不动）。"""
    con = sqlite3.connect(DB)
    try:
        c1 = con.execute("DELETE FROM commands WHERE device_id = ?", (DEV,))
        c2 = con.execute("DELETE FROM readings WHERE device_id = ?", (DEV,))
        con.commit()
        return c1.rowcount, c2.rowcount
    finally:
        con.close()


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--clicks", type=int, default=5,
                    help="模拟连点几次（并发发出）")
    args = ap.parse_args()
    BASE = args.base.rstrip("/")

    print("=" * 68)
    print("第2周补齐 · 重复点击（并发下发）与失败态")
    print("=" * 68)
    print(f"服务器: {BASE}")
    print(f"测试设备: {DEV}（与真板子隔离，跑完自动清理）")

    st, _ = get(BASE + "/api/latest")
    if st != 200:
        print("\n服务器不可达，先启动它：python week1/server/server.py")
        return 1

    # 先清一次，保证从干净状态开始（上次跑到一半中断也不影响）
    cleanup()

    ok = True
    K = max(2, args.clicks)

    # ---------------------------------------------------------- 1. 并发连点
    print(f"\n[1] {K} 个请求**同时**发出去（模拟两个标签页/多台手机一起点）")
    results: list[tuple[int, dict]] = [None] * K   # type: ignore

    def worker(i: int):
        results[i] = issue("recollect", {"samples": 5})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(K)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    codes = [c for c, _ in results]
    n202 = codes.count(202)
    n409 = codes.count(409)
    for c, d in results:
        tag = "受理" if c == 202 else ("拒绝" if c == 409 else f"?!{c}")
        ident = d.get("request_id") or d.get("existing_request_id") or "-"
        print(f"    {c} {tag}  {ident}  {d.get('hint', '')}")

    accepted = [d["request_id"] for c, d in results if c == 202]
    rejected = [d.get("existing_request_id") for c, d in results if c == 409]
    print(f"\n    受理 {n202} 条 / 拒绝 {n409} 条（期望恰好 1 / {K-1}）")
    if n202 != 1 or n409 != K - 1:
        print("    ✗ 不满足「只受理一条」——闸门没起作用")
        ok = False
    else:
        print("    ✓ 只受理一条，其余全部被挡住")

    if rejected and accepted and set(rejected) != set(accepted):
        print(f"    ✗ 拒绝时指的指令和真正受理的不是同一条：{set(rejected)} vs {accepted}")
        ok = False
    elif rejected:
        print("    ✓ 每条拒绝都指向**同一条**在途指令（不是各说各话）")

    # 落盘核对：库里确实只有一条未走完的
    n_active = active_count("recollect")
    print(f"    库里在途的 recollect：{n_active} 条（期望 1）")
    if n_active != 1:
        print("    ✗ 库里排了不止一条 —— 接口和落盘不一致")
        ok = False
    else:
        print("    ✓ 落盘一致：没有偷偷排队")

    # ------------------------------------------------- 2. 异类指令不受牵连
    print("\n[2] 采集还在途中，此时点「暂停」——应当放行（不同类互不阻塞）")
    st, d = issue("pause")
    print(f"    HTTP {st}  {d.get('request_id') or d.get('hint')}")
    if st != 202:
        print("    ✗ 暂停被误伤：采集在途不该阻塞维护类操作")
        ok = False
    else:
        print("    ✓ 放行（把暂停一起堵住会很难解释，也会挡住该做的事）")
    pause_id = d.get("request_id")

    # 收尾：把 pause 那条走完，免得它占着同类闸门
    if pause_id:
        report_as_board(pause_id, "received")
        report_as_board(pause_id, "done")

    # --------------------------------------------------- 3. 闸门会释放
    print("\n[3] 让在途那条走完后，再点一次 —— 应当被受理（闸门要能释放）")
    rid = accepted[0] if accepted else None
    if rid:
        report_as_board(rid, "received")
        report_as_board(rid, "done")
        c = cmd_of(rid)
        print(f"    {rid} 现在状态 = {c.get('status')}")
    st, d = issue("recollect", {"samples": 4})
    print(f"    再次下发 recollect: HTTP {st}  {d.get('request_id') or d.get('hint')}")
    if st != 202:
        print("    ✗ 闸门没释放：指令走完了却还是不让发新指令")
        ok = False
    else:
        print("    ✓ 释放正常")
    rid2 = d.get("request_id")

    # ------------------------------------------ 4. 失败态：设备回报 failed
    print("\n[4] 设备回报「执行失败」——状态应变成 failed，并带上原因")
    if rid2:
        report_as_board(rid2, "received")
        report_as_board(rid2, "failed", "5 条全部上报失败（网络或服务器不可达）")
        c = cmd_of(rid2)
        print(f"    状态      : {c.get('status')}")
        print(f"    备注      : {c.get('note')}")
        print(f"    failed_at : {c.get('failed_at')}")
        print(f"    总耗时    : {(c.get('legs_ms') or {}).get('total_ms')} ms")
        # failed 与 timeout 必须区分开：这两个字段落在不同的列上
        print(f"    timeout_ms: {c.get('timeout_ms')}（应为 None —— 这不是超时）")
        if c.get("status") != "failed":
            print("    ✗ 没有变成 failed")
            ok = False
        elif not c.get("note"):
            print("    ✗ 变成 failed 了，但没记下原因 —— 只知道失败、不知道为什么")
            ok = False
        elif c.get("timeout_ms"):
            print("    ✗ 同时带上了 timeout 标记 —— failed 和 timeout 混了")
            ok = False
        else:
            print("    ✓ failed 真实生效，带原因、与 timeout 分开记录")

    # ------------------------------------------------------------- 收尾
    print("\n[5] 清理本次测试数据")
    nc, nr = cleanup()
    print(f"    已删：commands {nc} 条、readings {nr} 条（只删 {DEV}）")

    print("\n" + "=" * 68)
    print("结果:", "全部通过 ✓" if ok else "存在失败项 ✗")
    print("=" * 68)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
