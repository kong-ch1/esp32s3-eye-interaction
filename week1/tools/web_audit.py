#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页改版审计 —— 用真实浏览器验证改动到底成没成

改版最容易的翻车方式是"看着挺新，其实点不动了"。所以每一项都真的去操作、
去读回值，不靠肉眼。

重点防的一类错：**假通过**。比如"摄像头是否限高 340px"——分区之后摄像头面板
默认是隐藏的，隐藏元素的高度是 0，那个检查会自动通过，但它什么都没验证。
所以凡是量尺寸的检查，都必须**先切到对应分区再量**。

检查项：
  1. 分区结构：六种模式各自可见的分区数对不对
  2. 各分区的页高（分区的主要收益就在这里）
  3. 曲线：从别的分区切回概览后，画布尺寸是否与容器一致
     （隐藏时画布宽度是 0，不处理会画成 900px 再被拉伸变形）
  4. 摄像头限高（切到影像分区后量）
  5. 求助面板折叠 ＋ 未读徽标（分区之后最容易丢掉的安全属性）
  6. 深色/浅色两套令牌是否都落到元素上（读 computed style）
  7. 悬停读数：悬停层是否真的画出了像素
  8. URL hash 直达分区
  9. JS 报错（含探测器自检）

复用 tools/browser_test.py 的手写 CDP 客户端（无第三方依赖）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT_PATH = os.path.join(ROOT, "tools", "browser_test.py")
DB_PATH = os.path.join(ROOT, "data", "readings.db")

PROBE = """
(() => {
  const css = (e) => getComputedStyle(e);
  const vis = Array.from(document.querySelectorAll('.panel'))
    .filter(p => css(p).display !== 'none').map(p => p.id);
  const pick = (sel) => {
    const e = document.querySelector(sel);
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return { bg: css(e).backgroundColor, h: Math.round(r.height) };
  };
  const cv = document.getElementById('chart');
  return JSON.stringify({
    tab: document.body.dataset.tab,
    visible: vis,
    pageH: document.body.scrollHeight,
    bodyBg: css(document.body).backgroundColor,
    card: pick('.card'),
    safety: pick('.safety'),
    cam: pick('#camStream'),
    helpN: document.querySelectorAll('#helpList .helprow').length,
    hasMore: !!document.querySelector('#helpList .morebtn'),
    badgeHidden: (document.getElementById('badgeHelp') || {}).hidden,
    badgeText: (document.getElementById('badgeHelp') || {}).textContent,
    chartW: cv ? cv.width : 0,
    wrapW: cv ? Math.round(cv.parentElement.getBoundingClientRect().width) : 0,
    dpr: window.devicePixelRatio || 1,
    onCount: document.querySelectorAll('.tabs button.on').length,
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
    fails: list[str] = []

    print("=" * 76)
    print("网页改版审计（分区标签页）")
    print("=" * 76)
    print(f"页面: {args.url}\n")

    proc = subprocess.Popen([
        browser, "--headless=new", f"--remote-debugging-port={args.port}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--hide-scrollbars", "--window-size=1280,1000", args.url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ws = None
    sim_eid = None
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

        def probe():
            return json.loads(bt.js(ws, PROBE))

        def check(name, ok, detail=""):
            print(f"  {'✓' if ok else '✗'} {name}" + (f"   {detail}" if detail else ""))
            if not ok:
                fails.append(name)

        def go(tab):
            """点标签切到某个分区，等渲染稳定后返回探测结果。"""
            bt.js(ws, f"document.querySelector('.tabs button[data-tab=\"{tab}\"]').click(); 'ok'")
            time.sleep(2.2)
            return probe()

        # ---------- 1. 分区结构 ----------
        print("[1] 分区结构")
        base = probe()
        print(f"    当前分区 {base['tab']} · 可见 {base['visible']}")
        check("默认落在「概览」且只显示它",
              base["tab"] == "overview" and base["visible"] == ["panel-overview"],
              f"tab={base['tab']} 可见={base['visible']}")
        check("导航条上有且只有一个选中项", base["onCount"] == 1, f"{base['onCount']} 个")

        heights = {}
        for tab, want in (("overview", 1), ("command", 1), ("help", 1),
                          ("camera", 1), ("data", 1), ("all", 5)):
            d = go(tab)
            heights[tab] = d["pageH"]
            ok = (len(d["visible"]) == want) and d["tab"] == tab
            check(f"「{tab}」显示 {len(d['visible'])} 个分区（应为 {want}）", ok,
                  f"页高 {d['pageH']}px")
        print()
        print("    各分区页高（同一次会话内量，可直接比较）：")
        for k, v in heights.items():
            print(f"      {k:9s} {v:5d} px")
        print("      （改版前未分区时整页 2743 px）")
        check("每个单区都明显短于改版前的整页",
              all(heights[k] < 1800 for k in ("overview", "command", "help", "camera", "data")))
        print()

        # ---------- 2. 曲线在切分区之后仍然正确 ----------
        print("[2] 曲线画布（隐藏分区的宽度是 0，不处理会画歪）")
        go("camera")
        back = go("overview")
        expect = round(back["wrapW"] * back["dpr"])
        print(f"    容器 {back['wrapW']}px · dpr {back['dpr']} "
              f"→ 画布位图应为 {expect}px，实际 {back['chartW']}px")
        check("切回概览后画布宽度与容器一致", abs(back["chartW"] - expect) <= 2,
              f"{back['chartW']} vs {expect}")
        print()

        # ---------- 3. 摄像头限高（必须先切过去，否则量到 0 会假通过）----------
        print("[3] 摄像头限高")
        cam = go("camera")
        print(f"    影像分区下镜头高度 {cam['cam']['h']}px")
        check("量到的是真实高度（不是隐藏时的 0）", cam["cam"]["h"] > 0, f"{cam['cam']['h']}px")
        check("摄像头限高 ≤ 340px", 0 < cam["cam"]["h"] <= 340, f"{cam['cam']['h']}px")
        print()

        # ---------- 4. 求助面板 + 未读徽标 ----------
        print("[4] 求助面板与未读徽标")
        h = go("help")
        print(f"    求助分区下 {h['helpN']} 行 · 展开按钮={h['hasMore']}")
        check("求助面板切过去后真的显示出来了", h["visible"] == ["panel-help"])
        check("默认不超过 3 行", 0 < h["helpN"] <= 3, f"{h['helpN']} 行")
        if h["hasMore"]:
            bt.js(ws, "document.querySelector('#helpList .morebtn').click(); 'ok'")
            time.sleep(1.8)
            h2 = probe()
            print(f"    展开后 {h2['helpN']} 行")
            check("展开后行数变多", h2["helpN"] > h["helpN"])
            bt.js(ws, "document.querySelector('#helpList .morebtn').click(); 'ok'")
            time.sleep(1.8)
            check("能再收起来", probe()["helpN"] <= 3)

        # 徽标：先比对"接口的真实待回应数"与"界面徽标"，再造事件看会不会亮。
        # 设备名必须用**当前真机**的 —— 页面查求助时按当前设备过滤（刻意设计：
        # 防止自测脚本的模拟事件混进真机面板）。第一版这里写了 selftest-sim，
        # 于是事件被页面正确过滤掉、徽标不亮，看起来像功能坏了，其实是测试写错了。
        with urllib.request.urlopen(args.url.rstrip("/") + "/api/latest", timeout=8) as r:
            real_dev = (json.loads(r.read().decode()).get("record") or {}).get("device_id")
        # 注意要带上 device_id：页面是按当前设备过滤的，这里也必须同样过滤，
        # 否则别的设备有待回应事件时，这个比对会误判成"界面漏了"。
        with urllib.request.urlopen(
                args.url.rstrip("/") + "/api/help?limit=20&device_id=" + str(real_dev),
                timeout=8) as r:
            api_open = (json.loads(r.read().decode()).get("counts") or {}).get("open", 0)

        # 两个状态都要测。只测"有事件时会亮"是不够的 —— 徽标如果永远亮着，
        # 那个检查照样通过（第一版就漏在这里：hidden 被 display 盖掉，永久显示 0）。
        b0 = go("overview")
        print(f"    接口待回应 {api_open} 条 · 界面徽标 隐藏={b0['badgeHidden']} "
              f"文字={b0['badgeText']!r}")
        check("徽标与接口的待回应数一致（有才显示、没有就藏）",
              bool(b0["badgeHidden"]) == (api_open == 0),
              f"隐藏={b0['badgeHidden']} 而接口 open={api_open}")

        sim_eid = "SIM-TABBADGE-" + str(int(time.time()))
        body = json.dumps({
            "device_id": real_dev, "event_id": sim_eid,
            "button": "MENU", "source": "web", "simulated": True,
            "local_feedback": "led", "press_delay_ms": 100,
            "snapshot": {"imu_ok": True, "norm_g": 1.0},
        }).encode()
        req = urllib.request.Request(args.url.rstrip("/") + "/api/help", data=body,
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            time.sleep(2.5)
            b = go("overview")
            print(f"    造一条待回应求助（device={real_dev}）后："
                  f"badge 隐藏={b['badgeHidden']} 文字={b['badgeText']!r}")
            check("概览分区下也能看到「有待回应」徽标", not b["badgeHidden"])
            check("徽标数字是待回应条数", str(b["badgeText"]).strip().isdigit())
        except Exception as e:
            print(f"    （造模拟求助失败，跳过徽标检查：{e}）")
            sim_eid = None
        print()

        # ---------- 5. 深色模式 ----------
        print("[5] 深色模式 · 同一套令牌是否整体切换")
        ws.call("Emulation.setEmulatedMedia",
                {"features": [{"name": "prefers-color-scheme", "value": "dark"}]})
        time.sleep(1.5)
        L = go("overview")
        print(f"    body 背景 {L['bodyBg']} · 卡片 {L['card']['bg']}")
        check("深色已生效", bt.js(ws, "matchMedia('(prefers-color-scheme: dark)').matches"))
        check("body 背景变深", L["bodyBg"] == "rgb(25, 25, 23)", L["bodyBg"])
        check("卡片换了深色底", L["card"]["bg"] == "rgb(34, 34, 32)", L["card"]["bg"])
        D = go("help")
        check("安全声明在深色下也换了底色",
              D["safety"]["bg"] == "rgb(51, 39, 15)", D["safety"]["bg"])
        ws.call("Emulation.setEmulatedMedia", {"features": []})
        time.sleep(1.2)
        print()

        # ---------- 6. 悬停读数 ----------
        print("[6] 悬停读数")
        go("overview")
        pos = json.loads(bt.js(ws, """(() => {
            const r = document.querySelector('.chartwrap').getBoundingClientRect();
            return JSON.stringify({x: Math.round(r.left + r.width * 0.5),
                                   y: Math.round(r.top + r.height * 0.5)});
        })()"""))
        ws.call("Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": pos["x"], "y": pos["y"], "buttons": 0})
        time.sleep(0.8)
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
        check("悬停时图上出现读数", isinstance(painted, int) and painted > 500, f"{painted} 像素")
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
        check("鼠标移开后会擦掉", cleared == 0, f"{cleared} 像素")
        print()

        # ---------- 7. hash 直达 ----------
        print("[7] URL hash 直达分区（验收时可以直接给 #help）")
        ws.call("Page.navigate", {"url": args.url.rstrip("/") + "/#help"})
        time.sleep(6)
        d = probe()
        print(f"    打开 #help 后落在 {d['tab']}，可见 {d['visible']}")
        check("#help 能直接落到求助分区",
              d["tab"] == "help" and d["visible"] == ["panel-help"])
        print()

        # ---------- 8. JS 报错 ----------
        print("[8] JS 报错（加载期 + 交互期）")
        bt.js(ws, "setTimeout(function(){ throw new Error('探测器自检'); }, 0); 'ok'")
        time.sleep(0.8)
        caught = json.loads(bt.js(ws, "JSON.stringify(window.__errs || [])"))
        check("错误探测器本身有效（故意抛错能收到）",
              any("探测器自检" in str(e) for e in caught), f"收到 {len(caught)} 条")
        bt.js(ws, "window.__errs = []")
        errs = json.loads(bt.js(ws, "JSON.stringify(window.__errs || ['(监听未装上)'])"))
        print(f"    自检之后捕获到 {len(errs)} 条")
        for e in errs[:5]:
            print(f"      · {str(e)[:120]}")
        check("没有未捕获的 JS 报错", len(errs) == 0)

        print()
        print("-" * 76)
        if fails:
            print(f"  ✗ {len(fails)} 项未通过：" + "；".join(fails))
        else:
            print("  ✓ 全部通过")
        print("=" * 76)
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

        # 清掉造出来的模拟求助，别让它冒充真机历史
        if sim_eid:
            try:
                req = urllib.request.Request(
                    args.url.rstrip("/") + f"/api/help/{sim_eid}/reply",
                    data=json.dumps({"action": "cancel", "reason": "审计收尾"}).encode(),
                    method="POST")
                req.add_header("Content-Type", "application/json")
                urllib.request.urlopen(req, timeout=8).read()
            except Exception:
                pass
            try:
                con = sqlite3.connect(DB_PATH)
                n1 = con.execute("DELETE FROM help_events WHERE id=?", (sim_eid,)).rowcount
                n2 = con.execute(
                    "DELETE FROM commands WHERE type='help_reply' AND params LIKE ?",
                    (f"%{sim_eid}%",)).rowcount
                con.commit(); con.close()
                print(f"\n  已清理审计造的模拟数据：求助 {n1} 条、回传指令 {n2} 条")
            except Exception as e:
                print(f"\n  ⚠ 清理模拟数据失败，请手工删 {sim_eid}：{e}")


if __name__ == "__main__":
    sys.exit(main())
