#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页截图工具 —— 改版前后的对比证据

优化网页这种事，最容易犯的错是"凭印象说变好了"。所以先固定一个能反复出图的
工具：同一个视口、同一台真实 Chrome、同一套等待逻辑，改版前后各截一张，
放在一起看。

它复用 tools/browser_test.py 里手写的 CDP 客户端（不引入第三方依赖），
只做一件事：打开页面 → 等到画面稳定 → 截图。

用法：
    python tools/shoot.py --out debug_logs/_web_v1_desktop.png
    python tools/shoot.py --out debug_logs/_web_v1_mobile.png --viewport 390x900 --mobile
    python tools/shoot.py --out a.png b.png      # 一次出多张（同一会话，省启动开销）
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT_PATH = os.path.join(ROOT, "tools", "browser_test.py")


def load_bt():
    """复用 browser_test.py 里的 CDP 客户端，避免抄一遍 WebSocket 实现。"""
    if not os.path.exists(BT_PATH):
        sys.exit(f"✗ 找不到 {BT_PATH}")
    spec = importlib.util.spec_from_file_location("bt_cdp", BT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def capture(bt, ws, out: str, full: bool) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    r = ws.call("Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": bool(full)},
                timeout=60.0)
    with open(out, "wb") as fh:
        fh.write(base64.b64decode(r["data"]))
    size = os.path.getsize(out)
    print(f"  ✓ {out}  ({size // 1024} KB)")


def main() -> int:
    ap = argparse.ArgumentParser(description="网页截图（真实 Chrome + CDP）")
    ap.add_argument("--out", nargs="+", required=True, help="输出 PNG 路径（可多张）")
    ap.add_argument("--url", default="http://127.0.0.1:8000/")
    ap.add_argument("--viewport", default="1280x2200", help="宽x高，如 1280x2200 / 390x1400")
    ap.add_argument("--mobile", action="store_true", help="按移动端设备模拟（含触摸与缩放）")
    ap.add_argument("--dark", action="store_true",
                    help="强制 prefers-color-scheme: dark（用 CDP 媒体模拟，不是 Chrome 的自动变暗）")
    ap.add_argument("--range", default=None,
                    help="先点一下趋势图的时间窗按钮，如 1m / 10m / 1h")
    ap.add_argument("--wait", type=float, default=6.0, help="打开后等多久再截（秒）")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--no-full", action="store_true", help="只截当前视口，不截整页")
    args = ap.parse_args()

    bt = load_bt()
    browser = bt.find_browser()

    try:
        vw, vh = (int(x) for x in args.viewport.lower().split("x"))
    except Exception:
        sys.exit(f"✗ --viewport 格式不对：{args.viewport}（应为 1280x2200）")

    profile = tempfile.mkdtemp(prefix="shoot_")
    print("=" * 66)
    print(f"截图：{args.url}")
    print(f"浏览器：{browser}")
    print(f"视口：{vw}x{vh}{'（移动端模拟）' if args.mobile else ''}")
    print("=" * 66)

    proc = subprocess.Popen([
        browser, "--headless=new", f"--remote-debugging-port={args.port}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--hide-scrollbars", f"--window-size={vw},{vh}", args.url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ws = None
    try:
        bt.wait_devtools(args.port)
        target = None
        deadline = time.time() + 20
        while time.time() < deadline and target is None:
            target = bt.find_page_target(args.port, "127.0.0.1") or \
                     bt.find_page_target(args.port, "localhost")
            if target is None:
                time.sleep(0.3)
        if target is None:
            sys.exit("✗ 没找到页面 target")

        ws = bt.WS(target["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Runtime.enable")
        metrics = {"width": vw, "height": vh, "deviceScaleFactor": 1, "mobile": bool(args.mobile)}
        if args.mobile:
            metrics["deviceScaleFactor"] = 2
        ws.call("Emulation.setDeviceMetricsOverride", metrics)
        if args.dark:
            ws.call("Emulation.setEmulatedMedia",
                    {"features": [{"name": "prefers-color-scheme", "value": "dark"}]})
            # 回读确认，不要假设它生效了 —— 上一次就是"以为设了深色、截出来还是白的"，
            # 白花了半小时找 CSS 的问题。凡是能便宜验证的，都验一下。
            ok = bt.js(ws, "matchMedia('(prefers-color-scheme: dark)').matches")
            bg = bt.js(ws, "getComputedStyle(document.body).backgroundColor")
            print(f"  深色模拟: matchMedia={ok}  body 背景={bg}")
            if not ok:
                sys.exit("✗ 深色媒体模拟没生效，截出来的会是浅色图 —— 先修这个再截图")

        # 等页面自己把数据渲染出来：等 #tbody 里出现真实行，或超时
        deadline = time.time() + args.wait
        while time.time() < deadline:
            try:
                n = bt.js(ws, "document.querySelectorAll('#tbody tr').length")
                if n and n > 0 and bt.js(ws, "document.querySelector('#tbody td')?.textContent !== '暂无数据'"):
                    break
            except Exception:
                pass
            time.sleep(0.4)
        time.sleep(1.2)          # 再给图表/摄像头一点时间

        # 可选：先切到某个时间窗，再截图（否则截到的永远是默认的 1 分钟档）
        if args.range:
            bt.js(ws, f"document.querySelector('#chartRange button[data-range=\"{args.range}\"]')"
                      f"?.click()")
            time.sleep(2.5)

        for i, out in enumerate(args.out):
            if i:
                time.sleep(0.5)
            capture(bt, ws, out, full=not args.no_full)
        return 0
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
