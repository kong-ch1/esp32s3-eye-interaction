#!/usr/bin/env python3
"""第2周 · 指令通道自测（用软件模拟板子，不依赖硬件）

在不烧板子的情况下，把整条证据链跑一遍并打印每个阶段：
    issued → delivered → received → done

用法:
    python tools/cmd_selftest.py                    # 默认 127.0.0.1:8000
    python tools/cmd_selftest.py --base http://10.1.41.18:8000
    python tools/cmd_selftest.py --timeout-test     # 额外验证超时分支

它模拟的是板子在固件里的真实行为：
  1) 每 1 秒 POST 一次（常规定时上报，trigger=timer）
  2) 从**响应体**里拿到待执行指令
  3) 立刻回报 received（带 request_id）
  4) 按 params 采集 samples 条（trigger=command），最后一条带 done
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEVICE_ID = "selftest-sim"   # 刻意与真板子(team01-esp32s3eye)区分开：
# 指令是按 device_id 投递的，用独立标识才不会把发给真板子的指令抢走。


def post(base: str, path: str, obj: dict) -> dict:
    data = json.dumps(obj).encode()
    req = urllib.request.Request(base + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read().decode())


def reading(seq: int, **extra) -> dict:
    body = {"device_id": DEVICE_ID, "seq": seq, "ts": int(time.time() * 1000),
            "ax": 0.01, "ay": 0.02, "az": 1.00, "gx": 0.0, "gy": 0.0, "gz": 0.0,
            "temp_c": 45.0, "free_heap": 8000000, "min_free_heap": 7900000,
            "total_heap": 8716204, "rssi": -35}
    body.update(extra)
    return body


def show(cmd: dict, title: str):
    print(f"\n--- {title} ---")
    print(f"  request_id : {cmd['id']}")
    print(f"  status     : {cmd['status']}")
    print(f"  created_at : {cmd.get('created_at')}")
    print(f"  delivered  : {cmd.get('delivered_at')}")
    print(f"  received   : {cmd.get('received_at')}")
    print(f"  done       : {cmd.get('done_at')}")
    if cmd.get("timeout_at"):
        print(f"  timeout    : {cmd['timeout_at']}（阶段={cmd.get('timeout_stage')}）")
    if cmd.get("sample_count") is not None:
        print(f"  新采集样本 : {cmd['sample_count']} 条"
              f"（id {cmd.get('first_reading_id')}~{cmd.get('last_reading_id')}）")
    legs = cmd.get("legs_ms") or {}
    if legs:
        pretty = {k: f"{v}ms" for k, v in legs.items()}
        print(f"  各阶段耗时 : {pretty}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--device", default=DEVICE_ID)
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--interval-ms", type=int, default=120)
    ap.add_argument("--timeout-test", action="store_true",
                    help="额外验证超时分支（发指令后不执行）")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("=" * 66)
    print("第2周 指令通道自测 —— 目标:", base)
    print("=" * 66)

    # ---- 0. 先确认服务器活着 ----
    try:
        latest = get(base, "/api/latest")
    except Exception as e:
        print("服务器不可达:", e)
        return 1
    rec = latest.get("record") or {}
    print(f"\n[0] 服务器在线，最新记录 id={rec.get('id')} "
          f"age={rec.get('age_seconds')}s stale={rec.get('stale')}")

    # ---- 1. 网页侧发起「重新采集」 ----
    issued = post(base, "/api/command", {
        "device_id": args.device, "type": "recollect",
        "samples": args.samples, "interval_ms": args.interval_ms})
    cid = issued["request_id"]
    print(f"\n[1] 已受理指令 {cid}  status={issued['status']}")
    print(f"    受理超时 {issued['accept_timeout_s']}s；{issued['note']}")
    show(get(base, f"/api/command/{cid}"), "受理后")

    # ---- 2. 模拟板子常规定时上报，从响应里取指令 ----
    print("\n[2] 模拟板子 1Hz 上报（trigger=timer），等待指令搭车下发 ...")
    got = None
    for i in range(6):                       # 最多等 6 秒
        resp = post(base, "/api/data", reading(1000 + i))
        if "command" in resp:
            got = resp["command"]
            print(f"    ✓ 第 {i+1} 次上报的响应里收到指令: {got}")
            break
        print(f"    · 第 {i+1} 次上报，响应无指令")
        time.sleep(1.0)
    if not got:
        print("    ✗ 一直没收到指令，失败")
        return 1
    show(get(base, f"/api/command/{cid}"), "指令已下发（delivered）")

    if got["request_id"] != cid:
        print(f"    !! 收到的 request_id={got['request_id']} 与发起的 {cid} 不一致")
        return 1

    # ---- 3. 板子确认收到 ----
    post(base, "/api/data", reading(2000, request_id=cid,
                                    trigger="command", cmd_state="received"))
    print(f"\n[3] 已回报 received")
    show(get(base, f"/api/command/{cid}"), "设备已接收（received）")

    # ---- 4. 执行采集并逐条上报 ----
    n = int(got["params"].get("samples", args.samples))
    iv = int(got["params"].get("interval_ms", args.interval_ms))
    print(f"\n[4] 开始执行采集：{n} 条，间隔 {iv}ms")
    samples = []
    for k in range(n):
        last = (k == n - 1)
        r = reading(3000 + k, request_id=cid, trigger="command",
                    cmd_state="done" if last else None)
        post(base, "/api/data", r)
        samples.append(r["ax"])
        if not last:
            time.sleep(iv / 1000.0)
    print(f"    上报完成，共 {n} 条")

    final = get(base, f"/api/command/{cid}")
    show(final, "执行完成（done）")

    # ---- 5. 验证「新采集」与「历史记录」确实可区分 ----
    hist = get(base, "/api/history?limit=40")
    rows = hist.get("records") or []
    tagged = [r for r in rows if r.get("request_id") == cid]
    print(f"\n[5] 最近 {len(rows)} 条记录中，带 request_id={cid} 的有 {len(tagged)} 条")
    for r in tagged[:3]:
        print(f"    id={r['id']} trigger={r.get('trigger')} "
              f"state={r.get('cmd_state')} ax={r.get('ax')}")

    ok = (final["status"] == "done"
          and final.get("sample_count", 0) >= n
          and len(tagged) >= n)
    print(f"\n结论：证据链 {'完整 ✓' if ok else '不完整 ✗'}"
          f"  (status={final['status']}, sample_count={final.get('sample_count')})")

    # ---- 6. 超时分支（可选）----
    if args.timeout_test:
        print("\n[6] 验证超时分支：发一条指令但不执行，等待服务器判定超时 ...")
        t = post(base, "/api/command", {"device_id": args.device,
                                        "type": "recollect", "samples": 1})
        tid = t["request_id"]
        print(f"    已受理 {tid}，30 秒内不去取它 ...")
        for i in range(40):
            time.sleep(1.0)
            st = get(base, f"/api/command/{tid}")
            if st["status"] == "timeout":
                show(st, "超时判定（timeout）")
                print(f"    服务器在约 {i+1} 秒后判定超时，阶段={st.get('timeout_stage')}")
                ok = ok and st.get("timeout_stage") == "accept"
                break
        else:
            print("    ✗ 等了 40 秒仍未判定超时")
            ok = False

    print("\n" + "=" * 66)
    print("自测结果:", "全部通过 ✓" if ok else "存在失败项 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
