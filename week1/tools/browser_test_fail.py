#!/usr/bin/env python3
"""第2周补齐 —— 网页能不能真的把「失败」显示出来。

任务卡要求 Web 显示「完成、失败或超时」。前两版浏览器实测只验了「完成」
（四阶段全绿），失败态从来没在真实浏览器里看过 —— 而这一版才刚把 failed
接入界面。没在浏览器里点过就宣称"能显示失败"，就是又一次"照着自己写的
摘要验收"。

做法：
  1. 让服务器注入故障（接下来 N 条指令采集上报 503）
  2. 用真实 Chrome 打开页面，用 JS **真的点一下**「重新采集」
  3. 轮询 DOM，看时间线是否变红、文案是否显示「设备回报执行失败」和原因
  4. 再模拟「两个标签页同时点」：在页面上下文里并发发两个请求，
     确认一个受理、一个 409
  5. 截图留证

需要服务器以 --fault-injection 启动。

用法：
    python tools/browser_test_fail.py
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
PORT = 9334


def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:8000" + path, data=data,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
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
    print("第2周补齐 · 网页「失败」显示 真实浏览器实测")
    print("=" * 72)
    print(f"浏览器: {browser}")
    print(f"页面  : {URL}")

    st, _ = api("GET", "/api/latest")
    if st != 200:
        print("服务器不可达。")
        return 1
    st, d = api("POST", "/api/debug/fault", {"mode": "off"})
    if st != 200:
        print("故障注入口没开 —— 用 --fault-injection 启动服务器。")
        print(f"  HTTP {st} {d}")
        return 1
    print("注入口: ✓ 可用")

    # 让页面认到设备
    _, latest = api("GET", "/api/latest")
    dev = (latest.get("record") or {}).get("device_id")
    print(f"设备  : {dev}")

    # 注入故障的条数必须和**页面下拉框里选的那一档**一致。
    # 第一版写死了 5，而页面默认是 8 条 → 只挡住了 5 条，剩下 3 条送进去了，
    # 于是结果是「部分送达」的 done 而不是 failed。
    # （那次跑歪也验证了另一件事：备注链路是通的，页面确实显示了
    #   "部分送达：3/8 条成功"。）所以这里改成先读页面再注入。

    profile = tempfile.mkdtemp(prefix="wbfail_")
    proc = subprocess.Popen([
        browser, "--headless=new", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--window-size=1280,2400", URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok = True
    try:
        print(f"\n内核: {wait_devtools(PORT).get('Browser')}")
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

        # ---- 1. 等页面认到设备，并读出下拉框里选的采集条数 ----
        print("\n[1] 等页面拿到设备号，并读取采集参数 ...")
        dev_plan = None
        for _ in range(30):
            dev_plan = js(ws, """(()=>{
              const d=document.getElementById('dev');
              const p=document.getElementById('cmdPlan');
              return {dev: d?d.textContent.trim():'', plan: p?p.value:''};
            })()""")
            if dev_plan and dev_plan.get("dev") and "—" not in dev_plan["dev"] \
                    and dev_plan.get("plan"):
                break
            time.sleep(0.5)
        print(f"    页面显示: {dev_plan['dev']}")
        plan_samples = int(dev_plan["plan"].split(",")[0])
        print(f"    下拉框选定: {dev_plan['plan']}  → 会采 {plan_samples} 条")

        # 按页面的实际条数注入，保证整批都被挡住
        st, _ = api("POST", "/api/debug/fault",
                    {"mode": "command_data", "times": plan_samples})
        print(f"    已注入故障：接下来 {plan_samples} 条指令采集上报返回 503")

        # ---- 2. 真的点一下「重新采集」----
        print("\n[2] 用 JS 真实点击「重新采集」")
        js(ws, "(()=>{const b=document.getElementById('btnCmd');"
               "if(!b) throw new Error('找不到按钮');"
               "if(b.disabled) throw new Error('按钮是禁用状态');"
               "b.click(); return true})()")
        print("    已点击")

        # ---- 3. 轮询 DOM 直到终态 ----
        print("\n[3] 观察时间线与提示文案 ...")
        seen = None
        final = {}
        for _ in range(200):                     # 最多 100 秒
            st_dom = js(ws, """(()=>{
              const steps=[...document.querySelectorAll('#cmdTimeline .step')].map(s=>({
                cls:s.className.replace('step','').trim(),
                n:s.querySelector('.n').textContent.trim(),
                t:s.querySelector('.t').textContent.trim()}));
              const hint=document.getElementById('cmdHint').innerText.trim();
              const cmp=document.getElementById('cmdCmp').innerText.trim();
              return {steps, hint, cmp};
            })()""")
            sig = [s["cls"] + s["t"] for s in st_dom["steps"]]
            if sig != seen:
                seen = sig
                for s in st_dom["steps"]:
                    mark = {"done": "完成", "cur": "进行中", "bad": "异常",
                            "": "等待"}.get(s["cls"], s["cls"])
                    print(f"      {s['n']:<14} [{mark:<4}] {s['t']}")
                print("      " + "-" * 56)
            final = st_dom
            if ("执行失败" in st_dom["hint"]) and ("进行中" not in st_dom["hint"]):
                break
            time.sleep(0.5)

        print("\n[4] 页面最终显示")
        print("    cmdHint:")
        for ln in (final.get("hint") or "").splitlines():
            if ln.strip():
                print("      " + ln.strip())
        print("    cmdCmp:")
        for ln in (final.get("cmp") or "").splitlines():
            if ln.strip():
                print("      " + ln.strip())

        steps = final.get("steps") or []
        bad_steps = [s for s in steps if s["cls"] == "bad"]
        hint = final.get("hint") or ""
        cmp_txt = final.get("cmp") or ""

        print("\n[5] 判定")
        checks = [
            ("时间线出现红色（bad）阶段", len(bad_steps) >= 1),
            ("红色阶段文案含「执行失败」",
             any("执行失败" in s["t"] for s in bad_steps)),
            ("提示区说明是**设备**回报的失败", "设备回报执行失败" in hint),
            ("提示区带上了失败原因", "上报失败" in hint or "全部" in hint),
            ("对比区显示「设备备注」", "设备备注" in cmp_txt),
            ("没有把它说成超时", "超时" not in hint),
        ]
        for name, good in checks:
            print(f"    {'✓' if good else '✗'} {name}")
            if not good:
                ok = False

        png = shot(ws, "_w2b_browser_failed.png")
        print(f"\n    截图: {png}")

        # ---- 6. 两个标签页同时点（并发）----
        print("\n[6] 模拟「两个标签页同时点」：在页面里并发发两个请求")
        res = js(ws, """(async()=>{
          const dev=document.getElementById('dev').textContent.replace('device: ','').trim();
          const one=()=>fetch('/api/command',{method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({device_id:dev,type:'recollect',samples:4,interval_ms:120})})
            .then(async r=>({status:r.status, body:await r.json()}));
          const rs=await Promise.all([one(), one()]);
          return rs.map(r=>({status:r.status,
            rid:r.body.request_id||r.body.existing_request_id||null,
            hint:r.body.hint||null}));
        })()""", timeout=30)
        for r in res:
            print(f"    HTTP {r['status']}  {r['rid']}  {r.get('hint') or ''}")
        codes = sorted(r["status"] for r in res)
        if codes == [202, 409]:
            print("    ✓ 一个受理、一个被挡（闸门在服务器侧生效）")
        else:
            print(f"    ✗ 期望 [202, 409]，实际 {codes}")
            ok = False

        ws.close()
    finally:
        api("POST", "/api/debug/fault", {"mode": "off"})   # 一定要撤掉
        print("\n    已撤销故障注入")
        proc.terminate()

    print("\n" + "=" * 72)
    print("结果:", "全部通过 ✓" if ok else "存在失败项 ✗")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
