#!/usr/bin/env python3
"""第2周 · 真机验收 —— 对真实板子下发一次「重新采集」并把证据链完整打出来

与 cmd_selftest.py 的区别：
  · cmd_selftest.py 用软件**模拟**板子，用来在没有硬件时验证服务器逻辑
  · 本脚本**只发起指令、只观察**，执行者是真板子。它不会伪造任何数据，
    板子不回就是不回，超时就如实显示超时。

用法:
    python tools/cmd_acceptance.py
    python tools/cmd_acceptance.py --samples 20 --interval-ms 80
    python tools/cmd_acceptance.py --save       # 结果另存到 debug_logs/

它回答的正是评分表上那几个问题：
  1) 数据值 / 来源 / 采集时间 是否对得上
  2) 「刷新存储记录」与「触发新采集」怎么区分
  3) 服务器受理 / 设备接收 / 完成 / 超时 四段证据是否齐全
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.request

DEVICE_ID = "team01-esp32s3eye"
BASE = "http://127.0.0.1:8000"

OUT = io.StringIO()


def say(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    OUT.write(line + "\n")


def post(path, obj):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode())


def total_rows() -> int | None:
    try:
        d = get("/api/devices")
        if isinstance(d.get("total"), int):
            return d["total"]
        return sum(x.get("count", 0) for x in d.get("devices", []))
    except Exception:
        return None


def main():
    # 注意：global 必须写在**任何对该名字的使用之前** —— 下面 argparse 的
    # default=BASE 也算"使用"，写在后面会直接 SyntaxError。
    # （第1周在 server.py 的 MAX_ROWS 上踩过一模一样的坑，见排错记录第 13 条）
    global BASE

    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--device", default=DEVICE_ID)
    ap.add_argument("--samples", type=int, default=10)
    ap.add_argument("--interval-ms", type=int, default=100)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    BASE = args.base.rstrip("/")

    say("=" * 70)
    say(f"第2周 真机验收 —— {time.strftime('%Y-%m-%d %H:%M:%S')}")
    say(f"服务端: {BASE}   设备: {args.device}")
    say("=" * 70)

    # ---------- 0. 先确认板子在线、且在正常上报 ----------
    before = get("/api/latest")
    rec = before.get("record") or {}
    if not rec:
        say("\n[0] 失败：服务器里没有任何数据")
        return 1
    say(f"\n[0] 板子当前状态")
    say(f"    device_id   : {rec.get('device_id')}")
    say(f"    来源 IP     : {rec.get('src_ip')}（服务器侧记录的来源，不是页面常量）")
    say(f"    最后一条    : id={rec.get('id')} seq={rec.get('seq')} "
        f"采集于 {rec.get('server_time')}")
    say(f"    数据年龄    : {rec.get('age_seconds')} 秒  stale={rec.get('stale')}")
    if rec.get("stale"):
        say("    !! 板子当前未在实时上报，指令很可能超时。请先确认板子通电联网。")

    n_before = total_rows()
    say(f"    库里总条数  : {n_before}")
    seq_before = rec.get("seq")

    # ---------- 1. 明确"刷新"与"重新采集"的区别 ----------
    say("\n[1] 区分「刷新存储记录」与「触发新采集」")
    t0 = time.time()
    again = get("/api/latest")
    say(f"    先做一次『刷新』：再次 GET /api/latest")
    say(f"    → 还是同一条记录 id={again.get('record', {}).get('id')}，"
        f"seq={again.get('record', {}).get('seq')}，库里仍是 {total_rows()} 条")
    say(f"    → 刷新只是读取已存储的数据，**没有产生任何新数据**（耗时 "
        f"{int((time.time()-t0)*1000)}ms）")

    # ---------- 2. 下发指令 ----------
    say(f"\n[2] 下发「重新采集」指令（{args.samples} 条 · 间隔 {args.interval_ms}ms）")
    issued = post("/api/command", {
        "device_id": args.device, "type": "recollect",
        "samples": args.samples, "interval_ms": args.interval_ms})
    cid = issued["request_id"]
    say(f"    服务器已受理 → request_id = {cid}")
    say(f"    返回: status={issued['status']} "
        f"受理超时={issued['accept_timeout_s']}s")

    # ---------- 3. 轮询证据链 ----------
    say("\n[3] 等待板子取走并执行（每 0.3 秒查一次状态）")
    last_status = None
    deadline = time.time() + 70
    st = {}
    while time.time() < deadline:
        st = get(f"/api/command/{cid}")
        if st["status"] != last_status:
            mark = {
                "issued":    "① 已受理，等待板子来取",
                "delivered": "② 已随上报响应下发到板子",
                "received":  "③ 板子确认收到，开始采集",
                "done":      "④ 执行结果，新数据已入库",
                "timeout":   f"✗ 超时（阶段={st.get('timeout_stage')}）",
            }.get(st["status"], st["status"])
            say(f"    {st['status']:<10} {mark}")
            last_status = st["status"]
        if st["status"] in ("done", "timeout", "failed"):
            break
        time.sleep(0.3)

    # ---------- 4. 打印完整证据链 ----------
    say("\n[4] 证据链（服务器侧记录的每个时间点）")
    rows = [
        ("① 服务器受理 issued",    st.get("created_at")),
        ("② 指令下发 delivered",   st.get("delivered_at")),
        ("③ 设备接收 received",    st.get("received_at")),
        ("④ 执行结果 done",        st.get("done_at")),
        ("  超时 timeout",         st.get("timeout_at")),
    ]
    for name, ts in rows:
        say(f"    {name:<24} {ts or '—'}")
    legs = st.get("legs_ms") or {}
    say(f"    各阶段耗时：")
    for k, label in [("issue_to_deliver_ms", "受理 → 下发"),
                     ("deliver_to_receive_ms", "下发 → 设备接收"),
                     ("receive_to_done_ms", "接收 → 完成"),
                     ("total_ms", "端到端")]:
        if k in legs:
            say(f"        {label:<14} {legs[k]:>6} ms")

    # ---------- 5. 新采集的数据 ----------
    say("\n[5] 本次新采集的数据（按 request_id 精确关联，不是靠时间猜）")
    hist = get("/api/history?limit=200")
    tagged = [r for r in hist.get("records", [])
              if r.get("request_id") == cid]
    say(f"    带 request_id={cid} 的记录共 {len(tagged)} 条")
    if tagged:
        say(f"    id 范围 {tagged[-1]['id']} ~ {tagged[0]['id']}")
        say(f"    这些记录的触发方式均为 trigger='command'（常规定时上报是 'timer'）")
        say(f"\n    {'id':>6} {'触发':<9} {'回执阶段':<9} {'ax':>9} {'ay':>9} {'az':>9}  服务器时间")
        for r in sorted(tagged, key=lambda x: x["id"]):
            say(f"    {r['id']:>6} {str(r.get('trigger')):<9} "
                f"{str(r.get('cmd_state') or '-'):<9} "
                f"{r['ax']:>9.4f} {r['ay']:>9.4f} {r['az']:>9.4f}  {r['server_time']}")
        say(f"\n    数据来源 IP：{tagged[0].get('src_ip')}")
    else:
        say("    （没有找到带该 request_id 的记录）")

    # ---------- 6. 结论 ----------
    n_after = total_rows()
    say("\n[6] 结论")
    say(f"    下发前 {n_before} 条 → 下发后 {n_after} 条"
        f"（增加 {None if n_after is None or n_before is None else n_after - n_before}）")
    say(f"    seq {seq_before} → 板子最新 seq "
        f"{(get('/api/latest').get('record') or {}).get('seq')}")
    ok = st.get("status") == "done" and len(tagged) >= args.samples
    if ok:
        say("    ✓ 证据链完整：受理 → 下发 → 设备接收 → 完成，且新数据可精确关联")
    elif st.get("status") == "timeout":
        say(f"    ✗ 指令超时（阶段={st.get('timeout_stage')}）——"
            f" 服务器与板子之间的通道有问题，这是**真实故障**，不是脚本问题")
    else:
        say(f"    ✗ 未完成，最终状态 {st.get('status')}")

    if args.save:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "debug_logs")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"_w2_acceptance_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(OUT.getvalue())
        print(f"\n结果已保存: {p}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
