#!/usr/bin/env python3
"""第3周 · 网页面板的真实浏览器实测

只验证一件事：**第3周新增的「实体交互」面板在真实浏览器里真的渲染出来了**，
而且面板上的数字与 /api/buttons 返回的一致。

为什么不并进 browser_test.py：那个脚本验证的是「点按钮 → 指令四阶段推进」，
是第2周的功能；这个是纯只读的渲染校验，两者的失败含义完全不同，
混在一起以后不好定位。

复用 browser_test.py 里已经写好的 Chrome 启动 / DevTools 连接 / 截图工具
（包括那个手写的极简 WebSocket 客户端），不重复造。

用法:
    python tools/browser_test_btn.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from browser_test import (WS, find_browser, find_page_target, js,  # noqa: E402
                          shot, wait_devtools)


def main() -> int:
    url = "http://127.0.0.1:8000/"
    port = 9333
    browser = find_browser()

    print("=" * 70)
    print("第3周 · 网页「实体交互」面板 真实浏览器实测")
    print("=" * 70)
    print(f"浏览器: {browser}")
    print(f"页面  : {url}")

    # 服务端真值（用来和页面上显示的对照）。
    # ⚠️ 必须带上 device_id —— 页面只显示"当前上报设备"的按键事件，
    #    如果这里不带过滤，会取到自测脚本(selftest-sim)模拟出来的事件，
    #    于是拿模拟数据去和真机页面对账，必然对不上。
    with urllib.request.urlopen("http://127.0.0.1:8000/api/latest", timeout=15) as r:
        latest = json.loads(r.read().decode())
    dev = (latest.get("record") or {}).get("device_id") or ""
    q = ("&device_id=" + urllib.parse.quote(dev)) if dev else ""
    with urllib.request.urlopen(
            "http://127.0.0.1:8000/api/buttons?limit=6" + q, timeout=15) as r:
        api = json.loads(r.read().decode())
    print(f"\n[0] /api/buttons 返回 {api['count']} 条事件"
          f"（设备 {dev or '不限'}，作为对照基准）")

    profile = tempfile.mkdtemp(prefix="wbbtn_")
    proc = subprocess.Popen([
        browser, "--headless=new", f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--window-size=1280,2600", url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok = True
    try:
        ver = wait_devtools(port)
        print(f"    内核: {ver.get('Browser')}")

        tgt = None
        for _ in range(30):
            tgt = find_page_target(port, url.rstrip("/"))
            if tgt:
                break
            time.sleep(0.4)
        if not tgt:
            raise SystemExit("没找到页面标签页")

        ws = WS(tgt["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Runtime.enable")

        # ---- 1. 等实时数据渲染 + 面板填充 ----
        print("\n[1] 等待页面渲染 ...")
        for _ in range(30):
            v = js(ws, "(()=>{const e=document.getElementById('btnCount');"
                       "return e?e.textContent.trim():''})()")
            if v and "等待按键" not in v:
                break
            time.sleep(0.5)

        data = js(ws, """(()=>{
          const txt = (id)=>{const e=document.getElementById(id);return e?e.textContent.trim():null};
          const rows = [...document.querySelectorAll('#btnList .btnrow')].map(r=>r.innerText.replace(/\\s+/g,' ').trim());
          const tags = [...document.querySelectorAll('#tbody .tag')].map(t=>t.textContent.trim());
          const heading = [...document.querySelectorAll('h2')].map(h=>h.textContent.trim());
          return {count:txt('btnCount'), rows:rows, tags:tags,
                  hasBtnPanel: !!document.getElementById('btnBox'),
                  heading, freshness: document.querySelectorAll('#btnList .btnrow.fresh').length};
        })()""")

        print(f"\n[2] 「实体交互」面板")
        print(f"    面板存在      : {'✓' if data['hasBtnPanel'] else '✗'}")
        print(f"    标题          : {'✓ 找到' if any('实体交互' in h for h in data['heading']) else '✗ 没找到'}")
        print(f"    右上角计数    : {data['count']}")
        print(f"    事件行数      : {len(data['rows'])}")
        for r in data["rows"][:6]:
            print(f"       · {r[:110]}")

        print(f"\n[3] 表格「触发」列的标记分布")
        from collections import Counter
        c = Counter(data["tags"])
        for k, v in c.items():
            print(f"    {k:<12} {v} 行")
        has_btn_tag = any(t.startswith("按键") for t in data["tags"])
        print(f"    含「按键」标记: {'✓' if has_btn_tag else '（本次窗口内没有按键行，正常）'}")

        p = shot(ws, "_w3_browser_btn.png")
        print(f"\n    截图: {p}")

        # ---- 4. 与接口对账 ----
        print(f"\n[4] 与 /api/buttons 对账")
        api_n = api["count"]
        page_n = len(data["rows"])
        match = (page_n == min(api_n, 6))
        print(f"    接口 {api_n} 条（取前 6）vs 页面 {page_n} 行  "
              f"{'✓ 一致' if match else '✗ 不一致'}")
        ok &= match
        if api["events"]:
            e0 = api["events"][0]
            shown = data["rows"][0] if data["rows"] else ""
            in_page = (e0["event_id"] in shown) and (str(e0["samples"]) in shown)
            print(f"    最新事件 {e0['event_id']} 是否出现在页面首行: "
                  f"{'✓' if in_page else '✗'}")
            ok &= in_page
        ok &= data["hasBtnPanel"]

        ws.close()
    finally:
        if proc.poll() is None:
            proc.terminate()

    print("\n" + "=" * 70)
    print("结论: " + ("✓ 面板在真实浏览器中渲染正确，且与接口数据一致"
                      if ok else "✗ 存在不一致，需要排查"))
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
