#!/usr/bin/env python3
"""第3周 —— 教学求助面板的真实浏览器实测。

为什么非要在真浏览器里点一次：任务卡的第三条硬规矩是
**「无远端接收证据时不得显示『对方已收到』」** ——
这句话只有在"有人真的点了按钮"之后才谈得上被违反。
用接口调用来验，只能证明接口逻辑对，证明不了**界面**没在骗人。

本脚本做四件事：
  1. 造一条**带 simulated 标记**的求助事件（备用路径要求：模拟操作保留标识）
  2. 真实 Chrome 打开页面 → 断言 ③ 查看者回应**还没点亮**，
     并且页面上有安全声明（只连教学接收端）
  3. 在页面里**真的点**「我来处理」→ 断言 ③ 此刻才点亮、状态变已回应
  4. 再点「取消求助」→ 断言状态变已取消，且回传指令已生成
  5. 截图留证，并把自己造的模拟数据删掉

用模拟事件而不是等你按键，是因为"界面有没有说谎"这件事与数据来源无关；
真机按键的验收在 tools/help_acceptance.py，两边都做才算验干净。

用法：
    python tools/browser_test_help.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from browser_test import (WS, find_browser, find_page_target, js,  # noqa: E402
                          shot, wait_devtools)

URL = "http://127.0.0.1:8000/"
PORT = 9335


def req(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request("http://127.0.0.1:8000" + path, data=data,
                               method=method)
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


def main() -> int:
    browser = find_browser()
    print("=" * 72)
    print("第3周 · 教学求助面板 真实浏览器实测")
    print("=" * 72)
    print(f"浏览器: {browser}")
    print(f"页面  : {URL}")

    st, _ = req("GET", "/api/latest")
    if st != 200:
        print("服务器不可达。")
        return 1
    _, latest = req("GET", "/api/latest")
    dev = (latest.get("record") or {}).get("device_id") or ""
    if not dev:
        print("还没有任何设备上报，页面认不到设备，先让板子上线。")
        return 1
    print(f"设备  : {dev}")

    # ---- 1. 造一条模拟求助（带 simulated 标记）----
    eid = f"SIM-BROWSER-{int(time.time())}"
    st, ev = req("POST", "/api/help", {
        "device_id": dev, "event_id": eid, "button": "MENU",
        "source": "web", "simulated": True,
        # 模拟的按键给不出真灯效，所以 local_feedback 留空 ——
        # 这样界面上 ① 会是"未成立"，正好演示三级反馈确实各自独立
        "snapshot": {"imu_ok": True, "norm_g": 1.002, "press_delay_ms": None},
    })
    print(f"\n[1] 已造一条模拟求助: HTTP {st} id={eid} "
          f"simulated={ev.get('simulated')}")
    if st != 201:
        print(f"    ✗ 造数据失败: {ev}")
        return 1

    profile = tempfile.mkdtemp(prefix="wbhelp_")
    proc = subprocess.Popen([
        browser, "--headless=new", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--window-size=1300,2800", URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok = True

    def check(name: str, good: bool, detail: str = ""):
        nonlocal ok
        print(f"    {'✓' if good else '✗'} {name}" + (f"  {detail}" if detail else ""))
        if not good:
            ok = False

    try:
        print(f"    内核: {wait_devtools(PORT).get('Browser')}")
        tgt = None
        for _ in range(30):
            tgt = find_page_target(PORT, URL.rstrip("/"))
            if tgt:
                break
            time.sleep(0.4)
        if not tgt:
            raise SystemExit("没找到页面标签页")

        ws = WS(tgt["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Runtime.enable")

        # ---- 2. 等面板把这条渲染出来 ----
        print("\n[2] 等面板渲染这条求助 ...")
        row = None
        for _ in range(40):
            row = js(ws, f"""(()=>{{
              const rows=[...document.querySelectorAll('#helpList .helprow')];
              const r=rows.find(x=>x.innerText.includes('{eid}'));
              if(!r) return null;
              const lv=[...r.querySelectorAll('.lv')].map(b=>({{
                 on:b.className.includes('on'), txt:b.textContent.trim(),
                 tip:b.getAttribute('title')||''}}));
              return {{cls:r.className, text:r.innerText.replace(/\\s+/g,' ').trim(),
                       lv:lv,
                       hasAck:[...r.querySelectorAll('button[data-act=ack]')].length,
                       hasCancel:[...r.querySelectorAll('button[data-act=cancel]')].length}};
            }})()""")
            if row:
                break
            time.sleep(0.5)
        if not row:
            print("    ✗ 面板里没找到这条求助")
            return 1

        print(f"    行内容: {row['text'][:150]}")
        for b in row["lv"]:
            print(f"      {b['txt']}  →  title: {b['tip'][:70]}")
        check("三级反馈拆成三个徽标", len(row["lv"]) == 3, f"实际 {len(row['lv'])} 个")
        check("① 本地确认未成立（模拟操作给不出真灯效）",
              len(row["lv"]) >= 1 and not row["lv"][0]["on"])
        check("① 的说明标明了证据来源",
              len(row["lv"]) >= 1 and "设备自报" in row["lv"][0]["tip"])
        check("② VPS 接收成立", len(row["lv"]) >= 2 and row["lv"][1]["on"])

        print("\n[3] ⚠️ 关键：此刻**没人回应**，③ 必须还没亮")
        check("③ 查看者回应未点亮", len(row["lv"]) >= 3 and not row["lv"][2]["on"])
        check("③ 的说明写着界面不显示「对方已收到」",
              len(row["lv"]) >= 3 and "不显示" in row["lv"][2]["tip"])
        check("行里出现了「模拟」标记（模拟数据必须可识别）",
              "模拟" in row["text"])
        check("状态显示为「待回应」", "待回应" in row["text"])

        page = js(ws, """(()=>{
          const s=document.getElementById('helpSafety');
          return {safety: s?s.innerText.replace(/\\s+/g,' ').trim():'',
                  hasWhoami: !!document.getElementById('whoami')};
        })()""")
        print(f"\n[4] 安全声明: {page['safety'][:90]}...")
        check("页面上有安全声明", "真实紧急服务" in page["safety"])
        check("回应人输入框存在", bool(page["hasWhoami"]))

        png1 = shot(ws, "_w3_help_browser_1_before.png")
        print(f"    截图(点之前): {png1}")

        # ---- 5. 真的点「我来处理」 ----
        print("\n[5] 在页面里真实点击「我来处理（回应）」")
        js(ws, """(()=>{
          const w=document.getElementById('whoami');
          if(w){w.value='同学B（浏览器实测）';w.dispatchEvent(new Event('input'));}
          const rows=[...document.querySelectorAll('#helpList .helprow')];
          const r=rows.find(x=>x.innerText.includes(%s));
          if(!r) throw new Error('找不到那一行');
          const b=r.querySelector('button[data-act=ack]');
          if(!b) throw new Error('找不到回应按钮');
          b.click(); return true;
        })()""" % json.dumps(eid))

        after = None
        for _ in range(40):
            after = js(ws, f"""(()=>{{
              const rows=[...document.querySelectorAll('#helpList .helprow')];
              const r=rows.find(x=>x.innerText.includes('{eid}'));
              if(!r) return null;
              const lv=[...r.querySelectorAll('.lv')].map(b=>({{
                 on:b.className.includes('on'), txt:b.textContent.trim()}}));
              return {{text:r.innerText.replace(/\\s+/g,' ').trim(), lv:lv, cls:r.className}};
            }})()""")
            if after and len(after["lv"]) >= 3 and after["lv"][2]["on"]:
                break
            time.sleep(0.5)

        print(f"    行内容: {(after or {}).get('text','')[:170]}")
        check("③ 查看者回应此刻点亮",
              bool(after) and len(after["lv"]) >= 3 and after["lv"][2]["on"])
        check("状态变成「已回应」", bool(after) and "已回应" in after["text"])
        check("显示出了回应人", bool(after) and "同学B" in after["text"])

        _, d = req("GET", f"/api/help/{eid}")
        print(f"    接口侧: state={d.get('state')} by="
              f"{((d.get('levels') or {}).get('viewer') or {}).get('by')} "
              f"回传指令={d.get('reply_command_id')}")
        check("服务端也记录了回应人", ((d.get("levels") or {}).get("viewer") or {}).get("by")
              == "同学B（浏览器实测）")
        check("生成回传设备的指令", bool(d.get("reply_command_id")))

        png2 = shot(ws, "_w3_help_browser_2_acked.png")
        print(f"    截图(回应后): {png2}")

        # ---- 6. 取消 ----
        print("\n[6] 在页面里真实点击「取消求助」")
        js(ws, """(()=>{
          const rows=[...document.querySelectorAll('#helpList .helprow')];
          const r=rows.find(x=>x.innerText.includes(%s));
          if(!r) throw new Error('找不到那一行');
          const b=r.querySelector('button[data-act=cancel]');
          if(!b) throw new Error('找不到取消按钮');
          b.click(); return true;
        })()""" % json.dumps(eid))

        fin = None
        for _ in range(40):
            fin = js(ws, f"""(()=>{{
              const rows=[...document.querySelectorAll('#helpList .helprow')];
              const r=rows.find(x=>x.innerText.includes('{eid}'));
              if(!r) return null;
              const lv=[...r.querySelectorAll('.lv')].map(b=>({{
                 on:b.className.includes('on'), txt:b.textContent.trim()}}));
              return {{text:r.innerText.replace(/\\s+/g,' ').trim(), lv:lv, cls:r.className}};
            }})()""")
            if fin and "已取消" in fin["text"]:
                break
            time.sleep(0.5)

        print(f"    行内容: {(fin or {}).get('text','')[:190]}")
        check("状态变成「已取消」", bool(fin) and "已取消" in (fin or {}).get("text", ""))
        check("取消后 ③ 仍然保持已成立（那是真发生过的事，不该被抹掉）",
              bool(fin) and len(fin["lv"]) >= 3 and fin["lv"][2]["on"])
        check("取消后不再提供操作按钮（终态不可逆）",
              bool(fin) and "我来处理" not in fin["text"])

        png3 = shot(ws, "_w3_help_browser_3_cancelled.png")
        print(f"    截图(取消后): {png3}")

        ws.close()
    finally:
        proc.terminate()
        # 清掉这条模拟数据，别让它冒充真机历史
        req("POST", f"/api/help/{eid}/reply", {"action": "cancel", "reason": "测试收尾"})
        import sqlite3
        con = sqlite3.connect(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", "readings.db"))
        try:
            n1 = con.execute("DELETE FROM help_events WHERE id=?", (eid,)).rowcount
            n2 = con.execute(
                "DELETE FROM commands WHERE device_id=? AND type='help_reply'"
                " AND params LIKE ?", (dev, f"%{eid}%")).rowcount
            con.commit()
            print(f"\n    已清理模拟数据：求助 {n1} 条、回传指令 {n2} 条")
        finally:
            con.close()

    print("\n" + "=" * 72)
    print("结果:", "全部通过 ✓" if ok else "存在失败项 ✗")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
