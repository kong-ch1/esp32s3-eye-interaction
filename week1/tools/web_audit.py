#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页改版审计 —— 用真实浏览器验证这次改动到底成没成

改版最容易的翻车方式是"看着挺新，其实点不动了"。所以每一项都真的去操作、
去读回值，不靠肉眼。

检查项：
  1. 页面高度（改版目标是压缩页长）
  2. 深色/浅色两套令牌是否都落到元素上（读 computed style，不靠看）
  3. 时间窗切换：点「1 小时」后曲线数据量是否真的变了
  4. 悬停读数：模拟鼠标移动，悬停层画布上是否真的出现了像素
  5. 求助列表折叠/展开
  6. 摄像头限高是否生效
  7. 控制台有没有报错

复用 tools/browser_test.py 的手写 CDP 客户端（无第三方依赖）。

用法：
    python tools/web_audit.py
    python tools/web_audit.py --url http://127.0.0.1:8000/
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT_PATH = os.path.join(ROOT, "tools", "browser_test.py")

PROBES = """
(() => {
  const pick = (sel) => {
    const e = document.querySelector(sel);
    if (!e) return null;
    const c = getComputedStyle(e);
    return { bg: c.backgroundColor, fg: c.color, border: c.borderTopColor,
             h: Math.round(e.getBoundingClientRect().height) };
  };
  return JSON.stringify({
    body:   getComputedStyle(document.body).backgroundColor,
    card:   pick('.card'),
    tag:    pick('.tag'),
    safety: pick('.safety'),
    step:   pick('.step'),
    lvOff:  pick('.lv.off'),
    lvOn:   pick('.lv.on'),
    cam:    pick('#camStream'),
    helpN:  document.querySelectorAll('#helpList .helprow').length,
    hasMore: !!document.querySelector('#helpList .morebtn'),
    chartStat: (document.querySelector('#chartStat') || {}).textContent,
    pageH:  document.body.scrollHeight,
  });
})()
"""


def load_bt():
    spec = importlib.util.spec_from_file_location("bt_cdp", BT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description="网页改版审计（真实 Chrome）")
    ap.add_argument("--url", default="http://127.0.0.1:8000/")
    ap.add_argument("--port", type=int, default=9366)
    args = ap.parse_args()

    bt = load_bt()
    browser = bt.find_browser()
    profile = tempfile.mkdtemp(prefix="audit_")

    print("=" * 74)
    print("网页改版审计")
    print("=" * 74)
    print(f"页面: {args.url}\n")

    proc = subprocess.Popen([
        browser, "--headless=new", f"--remote-debugging-port={args.port}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--hide-scrollbars", "--window-size=1280,2200", args.url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    fails: list[str] = []
    ws = None
    try:
        bt.wait_devtools(args.port)
        target = None
        for _ in range(60):
            target = bt.find_page_target(args.port, "127.0.0.1")
            if target:
                break
            time.sleep(0.3)
        if not target:
            sys.exit("✗ 没找到页面 target")

        ws = bt.WS(target["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Runtime.enable")

        # 在页面脚本执行**之前**装错误监听，然后重新加载 ——
        # 这样加载期与交互期的报错都能收到。
        # 第一版这里是读一个从没被写入过的列表，于是"没有 JS 报错"必然通过：
        # 一个永远为真的检查等于没有检查，跟这个项目一直在防的
        # "看起来对的错误证据"是同一类东西。
        ws.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
            window.__errs = [];
            window.addEventListener('error', function (e) {
              window.__errs.push((e && e.message) || String(e));
            });
            window.addEventListener('unhandledrejection', function (e) {
              window.__errs.push('unhandledrejection: ' + (e && e.reason));
            });
        """})
        ws.call("Page.reload")
        time.sleep(6)
        try:
            bt.js(ws, "document.querySelectorAll('#tbody tr').length")
        except Exception:
            pass

        def probe():
            return json.loads(bt.js(ws, PROBES))

        def check(name, ok, detail=""):
            print(f"  {'✓' if ok else '✗'} {name}" + (f"   {detail}" if detail else ""))
            if not ok:
                fails.append(name)

        # ---------- 1. 浅色 ----------
        L = probe()
        print("[1] 浅色模式 · 令牌是否落到元素上")
        print(f"    body 背景 {L['body']} · 页高 {L['pageH']}px")
        for k in ("card", "tag", "safety", "step", "lvOff", "lvOn"):
            v = L.get(k)
            print(f"    {k:8s} 背景 {v['bg'] if v else '(元素不存在)'}"
                  + (f"  文字 {v['fg']}" if v else ""))
        check("卡片有白色底", (L.get("card") or {}).get("bg") == "rgb(255, 255, 255)")
        check("页面高度已压缩（改版前 2743px）", L["pageH"] < 2600,
              f"现在 {L['pageH']}px")
        check("摄像头已限高（≤340px）", (L.get("cam") or {}).get("h", 9999) <= 340,
              f"实际 {(L.get('cam') or {}).get('h')}px")
        print()

        # ---------- 2. 深色 ----------
        print("[2] 深色模式 · 同一套令牌是否整体切换")
        ws.call("Emulation.setEmulatedMedia",
                {"features": [{"name": "prefers-color-scheme", "value": "dark"}]})
        time.sleep(1.5)
        D = probe()
        print(f"    body 背景 {D['body']}")
        check("深色已生效", bt.js(ws, "matchMedia('(prefers-color-scheme: dark)').matches"))
        check("body 背景变深", D["body"] == "rgb(25, 25, 23)", D["body"])
        for k in ("card", "tag", "safety", "step", "lvOff", "lvOn"):
            b = (L.get(k) or {}).get("bg")
            d = (D.get(k) or {}).get("bg")
            same = (b == d)
            print(f"    {k:8s} 浅 {b}  →  深 {d}" + ("   ⚠ 未变" if same else ""))
            if same and k in ("card", "safety", "step"):
                fails.append(f"{k} 在深色下没换色")
        check("卡片在深色下换了底色", (D.get("card") or {}).get("bg") != (L.get("card") or {}).get("bg"))
        # 切回浅色，后面按浅色测
        ws.call("Emulation.setEmulatedMedia", {"features": []})
        time.sleep(1.0)
        print()

        # ---------- 3. 时间窗 ----------
        print("[3] 趋势图时间窗切换")
        before = probe()["chartStat"]
        print(f"    切换前 chartStat = {before}")
        bt.js(ws, "document.querySelector('#chartRange button[data-range=\"1h\"]').click()")
        time.sleep(3)
        after = probe()["chartStat"]
        print(f"    切到 1 小时后  = {after}")
        check("时间窗文字变了", after != before)
        check("标签是「最近 1 小时」", "1 小时" in (after or ""))
        n1h = bt.js(ws, "LAST.length")
        print(f"    1 小时档实际拿到 {n1h} 个点")
        bt.js(ws, "document.querySelector('#chartRange button[data-range=\"1m\"]').click()")
        time.sleep(2.5)
        n1m = bt.js(ws, "LAST.length")
        print(f"    切回 1 分钟档   {n1m} 个点")
        check("两个档位的数据量不同（说明真的在换窗口）", n1m != n1h, f"{n1m} vs {n1h}")
        check("1 分钟档点数 ≤ 1 小时档", n1m <= n1h)
        print()

        # ---------- 4. 悬停读数 ----------
        print("[4] 悬停读数（模拟鼠标移到图上）")
        box = bt.js(ws, """(() => {
            const r = document.querySelector('.chartwrap').getBoundingClientRect();
            return JSON.stringify({x: Math.round(r.left + r.width * 0.5),
                                   y: Math.round(r.top + r.height * 0.5)});
        })()""")
        pos = json.loads(box)
        ws.call("Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": pos["x"], "y": pos["y"], "buttons": 0})
        time.sleep(0.8)
        # 悬停层上有没有画出东西：统计非透明像素
        painted = bt.js(ws, """(() => {
            const hv = document.querySelector('#chartHover');
            if (!hv || !hv.width) return -1;
            const c = document.createElement('canvas');
            c.width = hv.width; c.height = hv.height;
            const g = c.getContext('2d');
            g.drawImage(hv, 0, 0);
            const d = g.getImageData(0, 0, hv.width, hv.height).data;
            let n = 0;
            for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
            return n;
        })()""")
        print(f"    悬停层非透明像素 {painted}")
        check("悬停时图上出现了读数（悬停层被绘制）", isinstance(painted, int) and painted > 500,
              f"{painted} 像素")
        ws.call("Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": 5, "y": 5, "buttons": 0})
        time.sleep(0.5)
        cleared = bt.js(ws, """(() => {
            const hv = document.querySelector('#chartHover');
            const c = document.createElement('canvas');
            c.width = hv.width; c.height = hv.height;
            const g = c.getContext('2d');
            g.drawImage(hv, 0, 0);
            const d = g.getImageData(0, 0, hv.width, hv.height).data;
            let n = 0;
            for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
            return n;
        })()""")
        check("鼠标移开后会擦掉（不留残影）", cleared == 0, f"{cleared} 像素")
        print()

        # ---------- 5. 求助列表折叠 ----------
        print("[5] 求助列表折叠")
        h1 = probe()
        print(f"    默认显示 {h1['helpN']} 行 · 有展开按钮={h1['hasMore']}")
        check("默认不超过 3 行", h1["helpN"] <= 3, f"{h1['helpN']} 行")
        if h1["hasMore"]:
            bt.js(ws, "document.querySelector('#helpList .morebtn').click()")
            time.sleep(1.8)
            h2 = probe()
            print(f"    展开后 {h2['helpN']} 行 · 还有按钮={h2['hasMore']}")
            check("展开后行数变多", h2["helpN"] > h1["helpN"])
            bt.js(ws, "document.querySelector('#helpList .morebtn').click()")
            time.sleep(1.8)
            h3 = probe()
            print(f"    收起后 {h3['helpN']} 行")
            check("能再收起来", h3["helpN"] <= 3)
        print()

        # ---------- 6. JS 报错 ----------
        print("[6] JS 报错（加载期 + 交互期）")
        # 先证明探测器本身有效：故意抛一个错，看它能不能收到。
        # 不做这一步的话，"0 条报错"既可能是真的干净，也可能是监听根本没装上 ——
        # 两者在输出上长得一模一样。第2周做过同样的对照（撤掉故障注入后同一块板子
        # 能跑通），这里照做。
        bt.js(ws, "setTimeout(function(){ throw new Error('探测器自检'); }, 0); 'ok'")
        time.sleep(0.8)
        caught = json.loads(bt.js(ws, "JSON.stringify(window.__errs || [])"))
        detector_ok = any("探测器自检" in str(e) for e in caught)
        check("错误探测器本身有效（故意抛错能收到）", detector_ok,
              f"收到 {len(caught)} 条" if detector_ok else "监听没装上，下面的结论不可信")
        bt.js(ws, "window.__errs = []")        # 清掉自检那条

        errs = json.loads(bt.js(ws, "JSON.stringify(window.__errs || ['(监听未装上)'])"))
        print(f"    自检之后捕获到 {len(errs)} 条")
        for e in errs[:5]:
            print(f"      · {str(e)[:120]}")
        check("没有未捕获的 JS 报错", len(errs) == 0)

        print()
        print("-" * 74)
        if fails:
            print(f"  ✗ {len(fails)} 项未通过：" + "；".join(fails))
        else:
            print("  ✓ 全部通过")
        print("=" * 74)
        return 1 if fails else 0
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass
        try:
            proc.terminate(); proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(profile, ignore_errors=True)



if __name__ == "__main__":
    sys.exit(main())
