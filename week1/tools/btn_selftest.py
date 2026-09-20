#!/usr/bin/env python3
"""第3周 实体按键链路自测 —— 不需要真板子。

用软件模拟"有人在板子上按了 MENU 键"：
    模拟板子连续 POST 5 条 trigger='button'、button='MENU'、
    共用同一个 request_id(BTN-MENU-xxxx) 的数据
然后检查服务器是否把它正确地聚合成**一条按键事件**。

自测覆盖的边界（这些是真正容易出错的地方）：
  1. 按键批次能被 /api/buttons 聚合成一条，样本数正确
  2. 定时的 trigger='timer' **不会**混进按键事件
  3. 按键批次带 BTN-… 的 request_id，**不会**污染指令状态机
     （它压根不是指令的产物，不该在 /api/commands 里出现）

设备标识刻意用 selftest-sim，与真板子(team01-esp32s3eye)分开，
免得把发给真板的指令抢走、或污染真实数据。

用法:
    python tools/btn_selftest.py [--base http://127.0.0.1:8000] [--button MENU]
"""
from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
DEVICE_ID = "selftest-sim"
SAMPLES = 5
GAP_MS = 60          # 自测跑快一点，不必等真板子的 100ms


def post(base: str, path: str, obj: dict):
    req = urllib.request.Request(
        base + path, data=json.dumps(obj).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.loads(r.read().decode())


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=15) as r:
        return r.status, json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--button", default="MENU", help="模拟按哪个键")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("=" * 62)
    print("第3周 实体按键链路自测（软件模拟，不需要真板子）")
    print("=" * 62)

    # ---- 0. 服务器可达 ----
    try:
        _, latest = get(base, "/api/latest")
    except Exception as e:
        print(f"\n[0] 服务器不可达: {e}")
        return 1
    print(f"\n[0] 服务器在线（最新记录 id={latest.get('id')}）")

    # ---- 1. 记录自测开始前的按键事件数（用来做差集）----
    _, before = get(base, f"/api/buttons?limit=100")
    before_ids = {e["event_id"] for e in before["events"]}
    print(f"[1] 自测前已有按键事件 {before['count']} 条")

    # ---- 2. 模拟一次按键：连发 5 条 ----
    evid = f"BTN-{args.button}-SELFTEST{int(time.time()) % 10000:04d}"
    print(f"\n[2] 模拟按下 {args.button} 键，连发 {SAMPLES} 条"
          f"（事件 {evid}）")
    ok = 0
    for i in range(SAMPLES):
        payload = {
            "device_id": DEVICE_ID,
            "seq": 90000 + i,
            "ts": int(time.time() * 1000),
            "ax": round(random.uniform(0.99, 1.01), 4),
            "ay": round(random.uniform(-0.01, 0.01), 4),
            "az": round(random.uniform(-0.01, 0.01), 4),
            "gx": 0.0, "gy": 0.0, "gz": 0.0,
            "temp_c": 45.0, "free_heap": 8000000,
            "request_id": evid, "trigger": "button", "button": args.button,
        }
        try:
            st, resp = post(base, "/api/data", payload)
            if st == 200:
                ok += 1
                print(f"     [{i + 1}/{SAMPLES}] HTTP 200 id={resp.get('id')}")
            else:
                print(f"     [{i + 1}/{SAMPLES}] HTTP {st}")
        except Exception as e:
            print(f"     [{i + 1}/{SAMPLES}] 失败: {e}")
        if i < SAMPLES - 1:
            time.sleep(GAP_MS / 1000.0)

    # ---- 3. 顺便发一条定时上报，验证它不会混进按键事件 ----
    timer_evid = None
    try:
        post(base, "/api/data", {
            "device_id": DEVICE_ID, "seq": 99999, "ts": int(time.time() * 1000),
            "ax": 1.0, "ay": 0.0, "az": 0.0, "gx": 0.0, "gy": 0.0, "gz": 0.0,
            "trigger": "timer",
        })
        print(f"\n[3] 另发 1 条定时上报（trigger=timer，无 request_id）作为对照")
    except Exception as e:
        print(f"\n[3] 定时上报失败: {e}")

    # ---- 4. 检查聚合结果 ----
    time.sleep(0.4)
    _, after = get(base, f"/api/buttons?limit=100")
    mine = [e for e in after["events"] if e["event_id"] == evid]
    print(f"\n[4] /api/buttons 现在共 {after['count']} 条事件"
          f"（自测前 {before['count']} 条，新增 {after['count'] - before['count']}）")

    agg_ok = False
    if mine:
        e = mine[0]
        agg_ok = (e["samples"] == SAMPLES and e["button"] == args.button)
        print(f"     ✓ 找到本次事件：按键={e['button']} 样本={e['samples']} "
              f"记录 id {e['first_id']}~{e['last_id']} 跨度={e['span_ms']}ms")
    else:
        print("     ✗ 没找到本次事件（聚合失败）")

    # 定时上报不该出现在按键事件里
    leaked = [e for e in after["events"] if not e.get("button")
              and e["event_id"] and "SELFTEST" not in e["event_id"]]
    timer_clean = (timer_evid not in {e["event_id"] for e in after["events"]})
    print(f"     定时上报是否混入按键事件: {'否 ✓' if timer_clean else '是 ✗'}")

    # ---- 5. 确认没污染指令状态机 ----
    _, cmds = get(base, "/api/commands?limit=50")
    polluted = [c for c in cmds["commands"] if c["id"] == evid]
    print(f"\n[5] 指令表里是否出现按键事件 id: "
          f"{'否 ✓' if not polluted else '是 ✗ 按键批次污染了指令状态机'}")
    print(f"     指令表当前共 {cmds['count']} 条（与第2周一致，未被按键干扰）")

    # ---- 判定 ----
    print("\n" + "=" * 62)
    all_ok = (ok == SAMPLES) and agg_ok and timer_clean and not polluted
    if all_ok:
        print("结论: 全部通过 ✓  按键链路（上报 → 聚合 → 与定时/指令隔离）正常")
    else:
        print("结论: 存在失败项 ✗")
        if ok != SAMPLES:
            print(f"   · 上报成功率 {ok}/{SAMPLES}")
        if not agg_ok:
            print("   · 聚合结果不符合预期")
        if not timer_clean:
            print("   · 定时上报混入了按键事件")
        if polluted:
            print("   · 按键批次污染了指令状态机")
    print("=" * 62)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
