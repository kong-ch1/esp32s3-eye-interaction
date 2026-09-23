#!/usr/bin/env python3
"""第2周补齐 · 「暂停周期上报」验收（任务卡硬要求）

任务卡原文两处都写了这件事：
  · 后2学时："暂停周期上报验证按钮触发"
  · 当堂验证："暂停周期上报、保持命令通道，改变设备状态后点击采集，核对新观测及 request_id"

它要证明的东西比页面上比数字硬得多：
  **停掉 1Hz 周期上报之后，数据库里任何新增记录都只可能来自指令。**

这个脚本把整条链验一遍，全程只观察真实服务器与真实板子，不伪造数据：

  ① 基线         —— 正常上报时，读数确实在涨（每秒 +1 左右）
  ② 发暂停指令    —— 走与采集指令完全相同的那条下行通道
  ③ 暂停生效      —— 读数**停止增长**，但板子仍在线（心跳 /api/poll 还在）
  ④ 暂停期间采集  —— 点「重新采集」，新记录全部带该指令的 request_id
  ⑤ 关键断言      —— 暂停期间**一条 timer 记录都没有**；新记录 100% 来自指令
  ⑥ 发恢复指令    —— 读数重新开始增长

用法:
    python tools/pause_acceptance.py
    python tools/pause_acceptance.py --quiet-seconds 8
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=15) as r:
        return json.loads(r.read().decode())


def post(base, path, obj):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(obj).encode("utf-8"),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def total(base):
    return get(base, "/api/devices").get("total", 0)


def wait_terminal(base, cid, timeout=45):
    """等一条指令走到终态，返回它的状态对象。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        c = get(base, "/api/command/" + urllib.parse.quote(cid))
        if c["status"] in ("done", "timeout", "failed"):
            return c
        time.sleep(0.4)
    return get(base, "/api/command/" + urllib.parse.quote(cid))


def readings_since(base, device, since_id):
    """取 id > since_id 的记录（只看这台设备的）。"""
    h = get(base, f"/api/history?limit=500&device_id={urllib.parse.quote(device)}")
    return [r for r in h["records"] if r["id"] > since_id]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--device", default="team01-esp32s3eye")
    ap.add_argument("--quiet-seconds", type=float, default=6.0,
                    help="暂停后观察多久，确认读数真的不涨")
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--interval-ms", type=int, default=100)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    dev = args.device

    print("=" * 70)
    print("第2周补齐 · 「暂停周期上报」验收")
    print("=" * 70)

    try:
        latest = get(base, "/api/latest")
    except Exception as e:
        print(f"\n服务器不可达: {e}")
        return 1
    if not latest.get("has_data"):
        print("\n服务器里还没有数据，先让板子上报一会儿。")
        return 1

    ok = True

    # ---------------- ① 基线：读数在涨 ----------------
    print("\n[1] 基线：正常上报时读数应该在涨")
    t0_total, last_id = total(base), latest["record"]["id"]
    time.sleep(3.0)
    t1_total = total(base)
    grew = t1_total - t0_total
    print(f"    3 秒内库内 {t0_total} → {t1_total}（+{grew}）")
    if grew < 1:
        print("    ✗ 基线就不涨，后面的对比没有意义 —— 先确认板子在正常上报")
        return 1
    print("    ✓ 周期上报正常（这是后面「停了」的对照）")

    # ---------------- ② 发暂停指令 ----------------
    print("\n[2] 下发 pause 指令（走与采集指令完全相同的那条下行通道）")
    st, j = post(base, "/api/command", {"device_id": dev, "type": "pause"})
    if st != 202:
        print(f"    ✗ HTTP {st} → {j}")
        return 1
    cid_pause = j["request_id"]
    print(f"    已受理 {cid_pause}")
    c = wait_terminal(base, cid_pause)
    print(f"    最终状态: {c['status']}   各段耗时: {c.get('legs_ms')}")
    if c["status"] != "done":
        print("    ✗ 暂停指令没走完 —— 板子在线吗？")
        return 1
    print("    ✓ 暂停指令四段走完（受理 → 下发 → 设备接收 → 执行结果）")

    # ---------------- ③ 暂停生效：读数停涨，但板子仍在线 ----------------
    print(f"\n[3] 暂停生效验证：观察 {args.quiet_seconds:.0f} 秒")
    base_id = get(base, "/api/latest")["record"]["id"]
    base_total = total(base)
    time.sleep(args.quiet_seconds)
    now_total = total(base)
    after_latest = get(base, "/api/latest")
    delta = now_total - base_total
    print(f"    库内 {base_total} → {now_total}（+{delta}）")
    print(f"    板子自报 paused = {after_latest.get('paused')}，"
          f"最近被看到 = {after_latest.get('device_seen_seconds')} 秒前，"
          f"最近一次请求类型 = {after_latest.get('last_request_kind')}")
    if delta != 0:
        print(f"    ✗ 暂停期间还在写入 {delta} 条 —— 暂停没生效")
        ok = False
    else:
        print("    ✓ 传感上报已停止：这段时间一条都没写")
    seen = after_latest.get("device_seen_seconds")
    if seen is None or seen > 4:
        print(f"    ✗ 心跳不见了（{seen} 秒没被看到）—— 命令通道断了，不是「保持通道」")
        ok = False
    else:
        print(f"    ✓ 但板子仍在线（{seen:.1f} 秒前刚发过心跳）—— 命令通道保持住了")

    # ---------------- ④ 暂停期间点采集 ----------------
    print(f"\n[4] 暂停状态下点「重新采集」（{args.samples} 条 · 间隔 {args.interval_ms}ms）")
    mark_id = get(base, "/api/latest")["record"]["id"]
    st, j = post(base, "/api/command",
                 {"device_id": dev, "type": "recollect",
                  "samples": args.samples, "interval_ms": args.interval_ms})
    if st != 202:
        print(f"    ✗ HTTP {st} → {j}")
        return 1
    cid_get = j["request_id"]
    c = wait_terminal(base, cid_get)
    print(f"    {cid_get} 最终状态: {c['status']}  "
          f"新采集 {c.get('sample_count')} 条，端到端 {c.get('legs_ms', {}).get('total_ms')} ms")
    if c["status"] != "done":
        print("    ✗ 采集指令没走完 —— 暂停把命令通道也停掉了？")
        ok = False

    # ---------------- ⑤ 关键断言 ----------------
    print("\n[5] 关键断言：暂停期间的新记录，来源必须 100% 是指令")
    rows = readings_since(base, dev, mark_id)
    by_trigger: dict[str, int] = {}
    mine = 0
    for r in rows:
        by_trigger[r.get("trigger")] = by_trigger.get(r.get("trigger"), 0) + 1
        if r.get("request_id") == cid_get:
            mine += 1
    print(f"    标记点之后共新增 {len(rows)} 条，按 trigger 分布: {by_trigger}")
    print(f"    其中属于本次指令 {cid_get} 的: {mine} 条")
    if by_trigger.get("timer"):
        print(f"    ✗ 出现了 {by_trigger['timer']} 条 timer 记录 —— 周期上报没真停")
        ok = False
    else:
        print("    ✓ 一条 timer 记录都没有 —— 这些数据**不可能是**页面翻出来的旧值")
    if mine < 1:
        print("    ✗ 没有带该 request_id 的新记录")
        ok = False

    # ---------------- ⑥ 恢复 ----------------
    print("\n[6] 下发 resume 指令，确认能恢复")
    st, j = post(base, "/api/command", {"device_id": dev, "type": "resume"})
    cid_res = j.get("request_id") if isinstance(j, dict) else "?"
    c = wait_terminal(base, cid_res) if cid_res != "?" else {"status": "?"}
    print(f"    {cid_res} 最终状态: {c.get('status')}")
    t_before = total(base)
    time.sleep(3.0)
    t_after = total(base)
    print(f"    3 秒内库内 {t_before} → {t_after}（+{t_after - t_before}）")
    if t_after - t_before >= 1:
        print("    ✓ 周期上报已恢复")
    else:
        print("    ✗ 没有恢复上报")
        ok = False

    print("\n" + "=" * 70)
    print("结论: " + ("✓ 「暂停周期上报 + 保持命令通道」成立，"
                      "且暂停期间的新记录可证明全部来自指令"
                      if ok else "✗ 存在失败项，见上面标 ✗ 的行"))
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
