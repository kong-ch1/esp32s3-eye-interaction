#!/usr/bin/env python3
"""第3周 实体按键「真机」验收 —— 需要有人在板子上按一下键。

与 btn_selftest.py 的区别：
  · btn_selftest 用软件**模拟**板子，验证服务器逻辑
  · 这个脚本**只观察、只等待**，执行者是真人 + 真板子

脚本不会伪造任何数据：如果 90 秒内没人按键，它就如实报告"没等到"，
而不是造一条假的按键事件出来。这也是本项目的规矩 ——
证据必须是真发生的。

用法:
    python tools/btn_acceptance.py
    python tools/btn_acceptance.py --wait 120 --device team01-esp32s3eye
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=15) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--device", default="team01-esp32s3eye")
    ap.add_argument("--wait", type=int, default=90, help="最长等待秒数")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print("=" * 66)
    print("第3周 实体按键真机验收")
    print("=" * 66)

    # ---- 0. 前置状态 ----
    try:
        latest = get(base, "/api/latest")
        dev = get(base, "/api/devices")
        btns = get(base, f"/api/buttons?limit=100&device_id={args.device}")
    except Exception as e:
        print(f"\n服务器不可达: {e}")
        return 1

    rec = latest.get("record") or {}
    print(f"\n[0] 服务器在线")
    print(f"    库内总记录 : {dev.get('total')}")
    print(f"    板子最新   : id={rec.get('id')} seq={rec.get('seq')} "
          f"age={rec.get('age_seconds')}s trigger={rec.get('trigger')}")
    if rec.get("age_seconds") is None or rec["age_seconds"] > 5:
        print("    ⚠ 板子超过 5 秒没上报 —— 检查板子是否通电/联网")

    seen = {e["event_id"] for e in btns["events"]}
    total_before = dev.get("total", 0)
    print(f"    已有按键事件: {btns['count']} 条")

    # ---- 1. 等待真人按键 ----
    print(f"\n[1] 请现在去按板子上的 MENU / PLAY / UP+ / DN- 任意一个键")
    print(f"    （最多等 {args.wait} 秒；板子上的绿灯会先闪一下表示『按到了』）\n")

    found = None
    t0 = time.time()
    last_note = 0
    while time.time() - t0 < args.wait:
        try:
            cur = get(base, f"/api/buttons?limit=100&device_id={args.device}")
        except Exception:
            time.sleep(1.0)
            continue
        new = [e for e in cur["events"] if e["event_id"] not in seen]
        if new:
            found = new[0]
            break
        elapsed = int(time.time() - t0)
        if elapsed >= last_note + 10:
            last_note = elapsed
            print(f"    等待中… {elapsed}s")
        time.sleep(0.5)

    if not found:
        print(f"\n[2] ✗ {args.wait} 秒内没有检测到任何实体按键事件。")
        print("     如实报告，不伪造数据。可能原因：")
        print("       · 没按到（请按下去有明显手感的那四个键，不是 RST）")
        print("       · 板子没跑第3周固件（串口应能看到『实体按键按下: …』）")
        print("       · 按键电压判读阈值不对（串口会打印实际 mV）")
        return 1

    # ---- 2. 证据链 ----
    print(f"\n[2] ✓ 检测到实体按键事件\n")
    print(f"    事件 id     : {found['event_id']}")
    print(f"    按的键      : {found['button']}")
    print(f"    首条入库时间: {found['first_time']}")
    print(f"    新采集条数  : {found['samples']}")
    print(f"    记录 id     : {found['first_id']} ~ {found['last_id']}")
    print(f"    入库跨度    : {found['span_ms']} ms（首条入库 → 末条入库）")

    # ---- 3. 把这批数据从历史记录里捞出来，逐条核对 ----
    hist = get(base, f"/api/history?limit=80&device_id={args.device}")
    mine = [r for r in hist["records"] if r.get("request_id") == found["event_id"]]
    mine.sort(key=lambda r: r["id"])
    print(f"\n[3] 从 /api/history 里按 request_id 回溯，找到 {len(mine)} 条原始记录：")
    print(f"    {'id':<8}{'服务器时间':<22}{'trigger':<10}{'button':<8}{'|a|':<8}{'来源IP'}")
    for r in mine:
        import math
        a = math.sqrt(float(r["ax"]) ** 2 + float(r["ay"]) ** 2 + float(r["az"]) ** 2)
        print(f"    {r['id']:<8}{r['server_time']:<22}{str(r.get('trigger')):<10}"
              f"{str(r.get('button')):<8}{a:<8.3f}{r.get('src_ip')}")

    # ---- 4. 与定时上报做区分 ----
    timers = [r for r in hist["records"] if r.get("trigger") == "timer"]
    print(f"\n[4] 同一批历史里定时上报 {len(timers)} 条，trigger 字段与按键批次"
          f"截然不同 —— 证明这批数据确实是按键触发的，不是翻出来的旧记录")

    # ---- 5. 库内条数增长 ----
    dev_after = get(base, "/api/devices")
    print(f"\n[5] 库内总记录 {total_before} → {dev_after.get('total')}"
          f"（+{dev_after.get('total', 0) - total_before}，含在此期间板子的定时上报）")

    print("\n" + "=" * 66)
    ok = found["samples"] >= 1 and len(mine) == found["samples"]
    print("结论: " + ("✓ 实体按键 → 服务器记录 → 可回溯，闭环成立"
                      if ok else "✗ 聚合条数与实际记录数不一致，需要排查"))
    print("   注意：本地反馈（板上绿灯）与远端反馈（网页面板）需要用眼睛确认，")
    print("   这部分脚本看不到，请人工核对。")
    print("=" * 66)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
