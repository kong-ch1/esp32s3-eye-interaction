#!/usr/bin/env python3
"""第3周 —— 教学求助闭环的**真机**验收。

任务卡第3周要求：
  · 按键发起「教学求助测试消息」
  · 实现 触发—本地反馈—远端显示—回应/取消
  · 当堂验证：断开外网后本地仍能确认按键已触发；
    **无远端接收证据时不得显示「对方已收到」**

这个脚本跑完整条闭环，并在**每一个环节都停下来核对证据**：
  ①  等你按板子上的键（真的按，不是模拟）
  ②  事件出现 → 断言 ① 本地确认 ✓、② VPS 接收 ✓、③ 查看者回应 **必须还是 ✗**
  ③  脚本扮演远端查看者「回应」→ 断言 ③ 此刻才成立
  ④  等回传指令走到 done → 证明**板子确实收到了回应**（闭环回到发起者身边）
  ⑤  再「取消」一次，同样等它回到 done
  ⑥  最后核对：回应/取消都留下了服务端时间戳与操作人

关于 ③ 的"真人"这一点要说清楚：本脚本通过**与人相同的接口**回应，
所以链路是真的；但"有没有人真的点过按钮"这件事，由
`tools/browser_test_help.py` 在真实浏览器里点一次来证明。
两者都做，才算把 ③ 验干净。

用法：
    python tools/help_acceptance.py [--press-timeout 180] [--save]
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
    print(msg, flush=True)
    _LINES.append(msg)


def req(method: str, path: str, body: dict | None = None, timeout: float = 20.0):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status, json.loads(x.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:200]}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def wait_terminal(cid: str, timeout_s: float = 45.0) -> dict:
    seen, t0 = None, time.time()
    while time.time() - t0 < timeout_s:
        _, c = req("GET", f"/api/command/{cid}")
        if c.get("status") != seen:
            seen = c.get("status")
            say(f"      [{time.time() - t0:5.1f}s] 回传指令 → {seen}")
        if seen in ("done", "timeout", "failed"):
            return c
        time.sleep(0.4)
    return req("GET", f"/api/command/{cid}")[1]


def context_samples(eid: str) -> list[dict]:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT id, seq, trigger, button, cmd_state FROM readings"
            " WHERE request_id = ? ORDER BY id", (eid,)).fetchall()]
    finally:
        con.close()


def show_levels(ev: dict, title: str):
    say(f"    {title}")
    lv = ev.get("levels") or {}
    for k, label in (("local", "① 本地确认  "), ("server", "② VPS 接收  "),
                     ("viewer", "③ 查看者回应")):
        one = lv.get(k) or {}
        mark = "✓ 成立" if one.get("done") else "✗ 未成立"
        say(f"      {label} {mark}   {one.get('note')}")


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--device", default=DEVICE_ID)
    ap.add_argument("--press-timeout", type=int, default=180)
    ap.add_argument("--event", default="",
                    help="直接对一个已存在的真实按键事件验收（不再等新的按键）")
    ap.add_argument("--press-by", default="验收脚本（扮演远端查看者）")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    BASE = args.base.rstrip("/")
    dev = args.device

    say("=" * 74)
    say("第3周 · 教学求助闭环 真机验收")
    say("=" * 74)
    say(f"服务器: {BASE}    设备: {dev}")
    say("⚠️ 本功能只连本组教学接收端：不打电话、不发短信、不推第三方服务。")

    st, _ = req("GET", "/api/latest")
    if st != 200:
        say("\n服务器不可达。")
        return 1

    _, latest = req("GET", "/api/latest")
    rec = latest.get("record") or {}
    age = rec.get("age_seconds")
    say(f"\n[0] 板子状态：最新 id={rec.get('id')} 数据年龄={age}s "
        f"trigger={rec.get('trigger')}")
    if age is None or age > 5:
        say("    板子当前没在上报，先确认它在跑再验收。")
        return 1

    # 记下起点，稍后只认「这之后新出现」的求助
    _, before = req("GET", f"/api/help?limit=1&device_id={dev}")
    before_id = ((before.get("events") or [{}])[0] or {}).get("id")
    say(f"    当前最新求助: {before_id or '(还没有)'}")

    # ------------------------------------------------ ① 等一次真实按键
    ev = None
    t0 = time.time()
    if args.event:
        # 对着**已经按过**的一次真实按键验收（比如上一次脚本跑完才发现要改时序）。
        # 事件本身仍然是人按出来的，只是这次不等新的按键 —— 验收内容完全一样。
        st, d = req("GET", f"/api/help/{args.event}")
        if st != 200:
            say(f"\n    找不到事件 {args.event}: HTTP {st}")
            return 1
        ev = d
        say(f"\n[1] 使用已存在的真实按键事件 {ev['id']}（按键 {ev.get('button')}）")
        say("    —— 事件由真人按下产生，本次不再等新的按键")
    else:
        say(f"\n[1] 👆 请在 {args.press_timeout} 秒内按一下板子上的任意一个键"
            f"（MENU / PLAY / UP+ / DN-）")
        say("    按下的瞬间板上绿灯会**短亮一下** —— 那就是①本地确认，"
            "它不依赖网络。")
        while time.time() - t0 < args.press_timeout:
            _, d = req("GET", f"/api/help?limit=3&device_id={dev}")
            for e in (d.get("events") or []):
                if e.get("id") and e.get("id") != before_id:
                    ev = e
                    break
            if ev:
                break
            time.sleep(0.5)

    if not ev:
        say(f"\n    ✗ {args.press_timeout} 秒内没有收到求助 —— 没有按键吗？")
        say("      （不会伪造一条出来：这条路径的意义就在于它是真的按了）")
        return 1

    say(f"    事件 {ev['id']}（按键 {ev.get('button')}）"
        f"  等待 {(time.time() - t0):.1f} 秒")

    ok = True

    def check(name: str, good: bool, detail: str = ""):
        nonlocal ok
        say(f"    {'✓' if good else '✗'} {name}" + (f"  {detail}" if detail else ""))
        if not good:
            ok = False

    # ------------------------------------------------ ② 三级反馈的分界
    say("\n[2] 三级反馈此刻应该只有前两级成立")
    show_levels(ev, "事件当前状态：")

    check("① 本地确认成立（设备自报做了本地反馈）",
          bool((ev.get("levels") or {}).get("local", {}).get("done")))
    check("① 的证据来源标注为「设备自报」，不是服务器观测",
          "设备自报" in ((ev.get("levels") or {}).get("local", {}).get("evidence") or ""))
    check("② VPS 接收成立且带服务器时间戳",
          bool((ev.get("levels") or {}).get("server", {}).get("done"))
          and bool((ev.get("levels") or {}).get("server", {}).get("at")))

    say("\n[3] ⚠️ 硬规矩：此刻**没有任何人回应**，所以③必须还没成立")
    check("③ 查看者回应仍然是 ✗", not (ev.get("levels") or {}).get("viewer", {}).get("done"))
    check("没有 ack 时刻", ev.get("ack_ms") in (None, ""))
    check("状态还不是「已回应」", ev.get("state") != "acknowledged")
    check("③ 的说明明确写着界面不显示「对方已收到」",
          "不显示" in ((ev.get("levels") or {}).get("viewer", {}).get("note") or ""))

    say("\n[4] 事件内容核对（它必须能证明是**实体按键**发起的）")
    say(f"    source={ev.get('source')}  local_feedback={ev.get('local_feedback')}"
        f"  simulated={ev.get('simulated')}")
    snap = ev.get("snapshot") or {}
    say(f"    快照: imu_ok={snap.get('imu_ok')} |a|≈{snap.get('norm_g')} "
        f"按下→快照={snap.get('press_delay_ms')} ms")
    check("来源是实体按键（source=button）", ev.get("source") == "button")
    check("设备自报了本地反馈方式（led）", ev.get("local_feedback") == "led")
    check("不是模拟数据（simulated 为假）", not ev.get("simulated"))
    check("带上了环境快照", bool(snap))

    rows = context_samples(ev["id"])
    if not rows:
        # ⚠️ 这里必须等一下再判定。
        # 板子侧的顺序是"先发求助消息、再补上下文采样"，而每条 HTTP 往返
        # 实测要 0.5~2.5 秒 —— 求助消息一到就立刻查采样，会查到 0 条，
        # 然后误判成"采样没上传"。这是**测试自己的时序问题**，不是产品缺陷：
        # 第一版就是这么栽的（库里明明有 2 条，脚本却说 0 条）。
        say("\n    （求助消息先到、上下文采样后到；等最多 10 秒再判定）")
        t1 = time.time()
        while time.time() - t1 < 10:
            time.sleep(0.5)
            rows = context_samples(ev["id"])
            if rows:
                break
    say(f"\n    同一次按键的上下文采样：{len(rows)} 条"
        f"（trigger={sorted({r['trigger'] for r in rows})}，"
        f"等待 {time.time() - t0:.1f} 秒）")
    check("上下文采样都带同一个事件 id 且 trigger=button",
          bool(rows) and all(r["trigger"] == "button" for r in rows))

    # ------------------------------------------------ ③ 查看者回应
    say(f"\n[5] 由「{args.press_by}」做出回应（走与人相同的接口）")
    say("    👀 现在盯一下板子的绿灯：稍后它应该**慢闪 4 次**")
    st, d = req("POST", f"/api/help/{ev['id']}/reply",
                {"action": "ack", "by": args.press_by})
    say(f"    HTTP {st}  state={d.get('state')}  回传指令={d.get('reply_command_id')}")
    show_levels(d, "回应之后：")
    check("状态迁移到 acknowledged", d.get("state") == "acknowledged")
    check("③ 此刻才成立", bool((d.get("levels") or {}).get("viewer", {}).get("done")))
    check("记下了回应人", (d.get("levels") or {}).get("viewer", {}).get("by") == args.press_by)
    check("生成了回传设备的指令", bool(d.get("reply_command_id")))

    # ------------------------------------------------ ④ 闭环回到设备
    say("\n[6] 等回传指令走到 done —— 这一步才证明板子真的收到了「有人回应」")
    cid = d.get("reply_command_id")
    if cid:
        cmd = wait_terminal(cid)
        say(f"    回传指令 {cid}: 状态={cmd.get('status')}  "
            f"端到端={(cmd.get('legs_ms') or {}).get('total_ms')} ms")
        check("回传指令 done（设备已取走并给出本地提示）", cmd.get("status") == "done")
    else:
        check("回传指令 done", False, "没有生成回传指令")

    # ------------------------------------------------ ⑤ 取消
    say("\n[7] 「取消」也要能回到设备（任务卡：迁移确认/取消机制）")
    say("    👀 盯一下绿灯：这次应该**长亮 1 秒**（与「有人回应」区分开）")
    st, d2 = req("POST", f"/api/help/{ev['id']}/reply",
                 {"action": "cancel", "reason": "验收演练结束"})
    say(f"    HTTP {st}  state={d2.get('state')}  回传指令={d2.get('reply_command_id')}")
    check("状态迁移到 cancelled", d2.get("state") == "cancelled")
    check("记录了取消时刻", bool(d2.get("cancel_at")))
    cid2 = d2.get("reply_command_id")
    if cid2:
        cmd2 = wait_terminal(cid2)
        say(f"    回传指令 {cid2}: 状态={cmd2.get('status')}")
        check("取消也回到了设备（done）", cmd2.get("status") == "done")
    else:
        check("取消也回到了设备", False, "没有生成取消回传指令")

    # ------------------------------------------------ ⑥ 终态
    say("\n[8] 已取消的求助不能再被回应（终态不可逆）")
    st, d3 = req("POST", f"/api/help/{ev['id']}/reply", {"action": "ack", "by": "路人"})
    say(f"    HTTP {st}  {d3.get('error')}  {d3.get('hint')}")
    check("返回 409 且给出可读提示", st == 409 and bool(d3.get("hint")))

    say("\n" + "=" * 74)
    say("结果: " + ("全部通过 ✓" if ok else "存在失败项 ✗"))
    say("=" * 74)
    if ok:
        say("\n完整闭环：按键 → ① 本地灯亮 → ② 服务器落库 → ③ 有人回应")
        say("        → 回传设备 → 灯再亮一次（慢闪4）→ 取消 → 灯长亮1秒")
        say("全程 ③ 在有人回应之前一直是 ✗ —— 界面不会谎报「对方已收到」。")

    if args.save:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        name = time.strftime("_w3_help_acceptance_%Y%m%d_%H%M%S.txt")
        (LOG_DIR / name).write_text("\n".join(_LINES) + "\n", encoding="utf-8")
        say(f"\n原始输出已保存: debug_logs/{name}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
