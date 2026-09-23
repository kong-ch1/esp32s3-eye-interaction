#!/usr/bin/env python3
"""第3周 —— 教学求助闭环的**服务器级**自测（不需要硬件）。

不用硬件是因为这里要验的是状态机与那几条硬规矩，它们全在服务器侧：
  · 三级反馈必须是三个独立事实，不能合并
  · **没有第 ③ 级（查看者回应）的证据时，不得出现"已收到"**
  · 一条求助重复上报不能变成三条（幂等）
  · 回应/取消只能按规则迁移状态（取消后不能再回应）
  · 回应/取消要生成**回传指令**，把结果送回设备（任务卡：迁移确认/取消机制）

脚本自己扮演设备（按真实协议 POST /api/help），也扮演"另一个远端的人"
（POST /api/help/<id>/reply）。跑完会把自己造的数据删干净。

用法：
    python tools/help_selftest.py [--base http://127.0.0.1:8000]
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

DEV = "help-selftest-sim"
BASE = "http://127.0.0.1:8000"


def req(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=20) as x:
            return x.status, json.loads(x.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:200]}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def post(path, body):
    return req("POST", path, body)


def get(path):
    return req("GET", path)


def db_rows():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM help_events WHERE device_id=? ORDER BY created_ms",
            (DEV,)).fetchall()]
    finally:
        con.close()


def cleanup():
    con = sqlite3.connect(DB)
    try:
        c1 = con.execute("DELETE FROM help_events WHERE device_id=?", (DEV,)).rowcount
        c2 = con.execute("DELETE FROM commands WHERE device_id=?", (DEV,)).rowcount
        c3 = con.execute("DELETE FROM readings WHERE device_id=?", (DEV,)).rowcount
        con.commit()
        return c1, c2, c3
    finally:
        con.close()


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base.rstrip("/")

    print("=" * 70)
    print("第3周 · 教学求助闭环 —— 服务器级自测（无硬件）")
    print("=" * 70)
    print(f"服务器: {BASE}")
    print(f"测试设备: {DEV}（与真板子隔离，跑完自动清理）")

    st, _ = get("/api/latest")
    if st != 200:
        print("\n服务器不可达。先启动：python week1/server/server.py")
        return 1

    cleanup()
    ok = True
    EID = "BTN-abc123-MENU-0001"

    def check(name: str, good: bool, detail: str = ""):
        nonlocal ok
        print(f"    {'✓' if good else '✗'} {name}" + (f"  {detail}" if detail else ""))
        if not good:
            ok = False

    # ------------------------------------------------ 1. 设备发起求助
    print("\n[1] 设备发起一条教学求助测试消息（含环境快照）")
    st, d = post("/api/help", {
        "device_id": DEV, "event_id": EID, "button": "MENU",
        "source": "button", "local_feedback": "led", "press_delay_ms": 430,
        "snapshot": {"imu_ok": True, "ax": 0.01, "ay": -0.02, "az": 1.0,
                     "norm_g": 1.003, "press_delay_ms": 430},
    })
    print(f"    HTTP {st}  id={d.get('id')}  state={d.get('state')}  "
          f"duplicate={d.get('duplicate')}")
    check("受理成功（201）", st == 201)
    check("初始状态为 open", d.get("state") == "open")
    check("安全声明随响应下发", "真实紧急服务" in (d.get("safety") or ""),
          f"“{(d.get('safety') or '')[:24]}…”")

    # ------------------------------------------------ 2. 三级反馈必须分开
    print("\n[2] 三级反馈是三个独立事实")
    lv = d.get("levels") or {}
    for k, label in (("local", "① 本地确认"), ("server", "② VPS 接收"),
                     ("viewer", "③ 查看者回应")):
        one = lv.get(k) or {}
        print(f"    {label}: done={one.get('done')}  证据={one.get('evidence')}")
        print(f"        说明: {one.get('note')}")
    check("① 本地确认已成立（设备自报做了本地反馈）",
          bool((lv.get("local") or {}).get("done")))
    check("① 标注了证据来源是设备自报（不是服务器观测）",
          "设备自报" in ((lv.get("local") or {}).get("evidence") or ""))
    check("② VPS 接收已成立且带服务器时间戳",
          bool((lv.get("server") or {}).get("done"))
          and bool((lv.get("server") or {}).get("at")))
    check("③ 查看者回应**尚未**成立", not (lv.get("viewer") or {}).get("done"))

    # ------------------------------------------------ 3. 那条硬规矩
    print("\n[3] 硬规矩：没有 ③ 的证据，不得出现「对方已收到」")
    blob = json.dumps(d, ensure_ascii=False)
    # "已收到"出现在"不显示「对方已收到」"这句说明里是允许的（那是在声明没发生），
    # 不允许的是任何把回应当作已发生的字段。
    check("viewer 级没有 ack 时刻（ack_ms 为空）", d.get("ack_ms") in (None, ""))
    check("viewer 级的说明明确写着「界面不显示」",
          "不显示" in ((lv.get("viewer") or {}).get("note") or ""))
    check("viewer 级的 by 为空", not (lv.get("viewer") or {}).get("by"))
    check("接口里没有把状态说成已回应", d.get("state") != "acknowledged")

    # ------------------------------------------------ 4. 幂等
    print("\n[4] 同一条求助重复上报，不能变成两条")
    st, d2 = post("/api/help", {"device_id": DEV, "event_id": EID, "button": "MENU"})
    print(f"    HTTP {st}  duplicate={d2.get('duplicate')}")
    check("重复上报被识别（duplicate=True）", bool(d2.get("duplicate")))
    check("库里仍然只有 1 条", len(db_rows()) == 1, f"实际 {len(db_rows())} 条")

    # ------------------------------------------------ 5. 查看者回应
    print("\n[5] 查看者「回应」——第三级反馈此刻才成立")
    st, d3 = post(f"/api/help/{EID}/reply", {"action": "ack", "by": "同学B"})
    print(f"    HTTP {st}  state={d3.get('state')}  result={d3.get('result')}")
    print(f"    回传指令: {d3.get('reply_command_id')}")
    lv3 = d3.get("levels") or {}
    check("状态迁移到 acknowledged", d3.get("state") == "acknowledged")
    check("③ 现在成立了", bool((lv3.get("viewer") or {}).get("done")))
    check("③ 记录了回应人", (lv3.get("viewer") or {}).get("by") == "同学B")
    check("③ 带上了回应时刻", bool(d3.get("ack_at")))
    check("生成了回传设备的指令", bool(d3.get("reply_command_id")))

    print("\n[6] 回传指令的内容（它要能把「有人回应了」送回设备）")
    cid = d3.get("reply_command_id")
    st, cmd = get(f"/api/command/{cid}")
    print(f"    {cid}: type={cmd.get('type')} status={cmd.get('status')} "
          f"params={cmd.get('params')}")
    check("类型是 help_reply", cmd.get("type") == "help_reply")
    check("带上事件 id", (cmd.get("params") or {}).get("event_id") == EID)
    check("带上动作 ack", (cmd.get("params") or {}).get("action") == "ack")
    check("状态为 issued（等设备来取）", cmd.get("status") == "issued")

    print("\n[7] 重复回应：幂等，且不再生成第二条回传指令")
    st, d4 = post(f"/api/help/{EID}/reply", {"action": "ack", "by": "同学B"})
    print(f"    HTTP {st}  result={d4.get('result')}  "
          f"reply_command_created={d4.get('reply_command_created')}  "
          f"reply_command_id={d4.get('reply_command_id')}")
    check("识别为重复（result=duplicate）", d4.get("result") == "duplicate")
    check("本次没有新建回传指令",
          d4.get("reply_command_created") is False)
    # 语义要精确："没新建"不等于"没有回传指令" —— 上一次那条还在，
    # 而且应该照样报出来。否则界面上会显示"没有回传指令"，与事实相反。
    check("仍然报出上一次那条回传指令的 id",
          d4.get("reply_command_id") == cid)
    con = sqlite3.connect(DB)
    n_cmd = con.execute("SELECT COUNT(*) FROM commands WHERE id LIKE 'CMD-%' "
                        "AND device_id=?", (DEV,)).fetchone()[0]
    con.close()
    check("库里该设备的指令仍是 1 条", n_cmd == 1, f"实际 {n_cmd} 条")

    # ------------------------------------------------ 6. 取消
    print("\n[8] 「取消」——任务卡要求明确取消，并把该机制迁移到设备")
    st, d5 = post(f"/api/help/{EID}/reply",
                  {"action": "cancel", "reason": "本人到场，不需要了"})
    print(f"    HTTP {st}  state={d5.get('state')}  "
          f"cancel_at={d5.get('cancel_at')}  回传指令={d5.get('reply_command_id')}")
    check("状态迁移到 cancelled", d5.get("state") == "cancelled")
    check("记录了取消时刻", bool(d5.get("cancel_at")))
    check("生成了取消回传指令", bool(d5.get("reply_command_id")))
    cid2 = d5.get("reply_command_id")
    st, cmd2 = get(f"/api/command/{cid2}")
    check("取消回传的动作是 cancel",
          (cmd2.get("params") or {}).get("action") == "cancel",
          f"params={cmd2.get('params')}")

    # ------------------------------------------------ 7. 终态不可逆
    print("\n[9] 已取消的求助不能再被回应")
    st, d6 = post(f"/api/help/{EID}/reply", {"action": "ack", "by": "吃瓜群众"})
    print(f"    HTTP {st}  error={d6.get('error')}  hint={d6.get('hint')}")
    check("返回 409", st == 409)
    check("给出了可读的提示", bool(d6.get("hint")))

    print("\n[10] 非法动作被拒绝")
    st, d7 = post(f"/api/help/{EID}/reply", {"action": "telephone"})
    print(f"    HTTP {st}  error={d7.get('error')}  allowed={d7.get('allowed')}")
    check("返回 400 并列出可用动作",
          st == 400 and set(d7.get("allowed") or []) == {"ack", "cancel"})

    # ------------------------------------------------ 8. 模拟必须可识别
    print("\n[11] 模拟/演练数据必须带标识（备用路径要求：模拟操作保留标识）")
    st, d8 = post("/api/help", {
        "device_id": DEV, "event_id": "SIM-DEMO-0001", "button": "PLAY",
        "source": "web", "simulated": True, "local_feedback": None,
    })
    print(f"    HTTP {st}  simulated={d8.get('simulated')}  "
          f"① local={((d8.get('levels') or {}).get('local') or {}).get('done')}")
    check("simulated 被如实记录", bool(d8.get("simulated")))
    check("这条没有本地反馈（网页模拟的按不出物理灯）",
          not ((d8.get("levels") or {}).get("local") or {}).get("done"))

    # ------------------------------------------------ 9. 列表
    print("\n[12] 列表与计数")
    st, dl = get(f"/api/help?limit=10&device_id={DEV}")
    print(f"    HTTP {st}  count={dl.get('count')}  counts={dl.get('counts')}")
    check("两条都在", dl.get("count") == 2)
    check("计数正确（1 条已取消 + 1 条待回应）",
          (dl.get("counts") or {}).get("cancelled") == 1
          and (dl.get("counts") or {}).get("open") == 1)
    check("列表响应也带安全声明", "真实紧急服务" in (dl.get("safety") or ""))

    # ------------------------------------------------ 收尾
    print("\n[13] 清理测试数据")
    a, b, c = cleanup()
    print(f"    已删：求助 {a} 条、指令 {b} 条、采样 {c} 条（只删 {DEV}）")

    print("\n" + "=" * 70)
    print("结果:", "全部通过 ✓" if ok else "存在失败项 ✗")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
