#!/usr/bin/env python3
"""第2周 · 真实浏览器实测（Chrome 无头 + DevTools Protocol）

为什么要有这个：网页部分此前只做过**静态**检查（JS 语法、元素 id 一致性、
接口连通性）和**离线**逻辑验证。那些都证明不了"在真实浏览器里点一下按钮，
界面真的会动"。这个脚本补上这一环。

它做的是：
  1. 启动真实 Chrome（无头模式），打开 http://127.0.0.1:8000
  2. 等页面把实时数据渲染出来，截一张图
  3. 用 JS 真的去 **点击「重新采集」按钮**
  4. 轮询页面上的时间线 DOM，等待四个阶段依次点亮（或超时变红）
  5. 再截一张图，并把页面上的文字结果抓回来

不依赖任何第三方库：WebSocket 部分是手写的（Chrome DevTools 用 WS 通信，
而标准库没有 WS 客户端）。

用法:
    python tools/browser_test.py
    python tools/browser_test.py --samples 12 --interval-ms 100 --keep-open
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "debug_logs")


# ------------------------------------------------------------ 极简 WebSocket 客户端
class WS:
    """只实现 CDP 需要的部分：文本帧、客户端掩码、分片长度。"""

    def __init__(self, url: str, timeout: float = 30.0):
        # ws://127.0.0.1:9222/devtools/page/XXXX
        rest = url.split("://", 1)[1]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        self._id = 0
        key = base64.b64encode(bytes(random.getrandbits(8) for _ in range(16))).decode()
        req = (f"GET /{path} HTTP/1.1\r\n"
               f"Host: {hostport}\r\n"
               "Upgrade: websocket\r\n"
               "Connection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败：连接被关闭")
            buf += chunk
        if b"101" not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError("WebSocket 握手失败：" + buf.split(b"\r\n", 1)[0].decode("latin1"))

    def send(self, obj: dict):
        data = json.dumps(obj).encode()
        header = bytearray([0x81])                      # FIN + text
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = bytes(random.getrandbits(8) for _ in range(4))
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("WebSocket 连接已关闭")
            buf += chunk
        return buf

    def recv(self) -> dict:
        while True:
            h = self._read_exact(2)
            opcode = h[0] & 0x0F
            length = h[1] & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            payload = self._read_exact(length) if length else b""
            if opcode == 0x8:
                raise RuntimeError("WebSocket 被对端关闭")
            if opcode == 0x9:                            # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + b"\x00\x00\x00\x00")
                continue
            if opcode in (0x1, 0x2):
                return json.loads(payload.decode("utf-8", "replace"))

    def call(self, method: str, params: dict | None = None, timeout=30.0):
        self._id += 1
        mid = self._id
        self.send({"id": mid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self.recv()
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} 失败: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ------------------------------------------------------------ 工具
def find_browser() -> str:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("没找到 Chrome / Edge")


def http_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode())


def wait_devtools(port: int, timeout=25.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return http_json(f"http://127.0.0.1:{port}/json/version")
        except Exception:
            time.sleep(0.3)
    raise SystemExit("Chrome 的调试端口没起来")


def find_page_target(port: int, want: str):
    for t in http_json(f"http://127.0.0.1:{port}/json/list"):
        if t.get("type") == "page" and want in (t.get("url") or ""):
            return t
    return None


def shot(ws: WS, name: str) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    r = ws.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(r["data"]))
    return path


def js(ws: WS, expr: str, timeout=20.0):
    r = ws.call("Runtime.evaluate",
                {"expression": expr, "returnByValue": True, "awaitPromise": True},
                timeout=timeout)
    res = r.get("result", {})
    if r.get("exceptionDetails"):
        raise RuntimeError("页面 JS 抛异常: " + json.dumps(r["exceptionDetails"])[:300])
    return res.get("value")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000/")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--samples", type=int, default=None,
                    help="不传就用页面下拉框当前选中的档位")
    ap.add_argument("--keep-open", action="store_true")
    args = ap.parse_args()

    browser = find_browser()
    profile = tempfile.mkdtemp(prefix="wbtest_")
    print("=" * 70)
    print("第2周 · 真实浏览器实测")
    print("=" * 70)
    print(f"浏览器: {browser}")
    print(f"页面  : {args.url}")

    proc = subprocess.Popen([
        browser, "--headless=new", f"--remote-debugging-port={args.port}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--window-size=1280,2400", args.url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok = True
    try:
        ver = wait_devtools(args.port)
        print(f"内核  : {ver.get('Browser')}")

        tgt = None
        for _ in range(30):
            tgt = find_page_target(args.port, args.url.rstrip("/"))
            if tgt:
                break
            time.sleep(0.4)
        if not tgt:
            raise SystemExit("没找到页面标签页")

        ws = WS(tgt["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Runtime.enable")

        # ---- 1. 等页面把实时数据渲染出来 ----
        print("\n[1] 等待页面渲染实时数据 ...")
        for i in range(30):
            v = js(ws, "(()=>{const e=document.getElementById('mtime');return e?e.textContent:''})()")
            if v and v != '—':
                break
            time.sleep(0.5)
        info = js(ws, """(()=>{
          const t=(id)=>{const e=document.getElementById(id);return e?e.textContent.trim():null};
          return {device:t('dev'), pill:t('pill'), ax:t('ax'), ay:t('ay'), az:t('az'),
                  am:t('am'), temp:t('tp'), mtime:t('mtime'), mage:t('mage'),
                  mip:t('mip'), rows:document.querySelectorAll('#tbody tr').length};
        })()""")
        print("    设备:", info["device"])
        print("    状态:", info["pill"])
        print(f"    加速度: ax={info['ax']} ay={info['ay']} az={info['az']}  |a|={info['am']}")
        print("    服务器时间:", info["mtime"], " 数据年龄:", info["mage"], " 来源IP:", info["mip"])
        print("    表格行数:", info["rows"])
        p1 = shot(ws, "_w2_browser_1_before.png")
        print("    截图:", p1)

        # ---- 2. 记录点击前的库内条数 ----
        before_n = js(ws, "(()=>{const m=document.getElementById('csvInfo').textContent.match(/[\\d,]+/);return m?m[0]:null})()")
        print(f"\n[2] 点击前：页面显示库内 {before_n} 条")

        # ---- 3. 真的去点那个按钮 ----
        if args.samples:
            js(ws, f"""(()=>{{
              const sel=document.getElementById('cmdPlan');
              for (const o of sel.options) if (o.value.startsWith('{args.samples},')) {{ sel.value=o.value; }}
              return sel.value;
            }})()""")
        picked = js(ws, "document.getElementById('cmdPlan').value")
        print(f"    采集参数: {picked}（条数,间隔ms）")

        js(ws, "document.getElementById('btnCmd').click(); 'clicked'")
        print("    ✓ 已通过 JS 真实点击「重新采集」按钮")

        # ---- 4. 轮询时间线 DOM ----
        # 判定依据是**DOM 上的 class**（done / cur / bad），不是去匹配界面文案 ——
        # 文案会变，class 是渲染逻辑真正吐出来的东西。
        print("\n[3] 观察页面上时间线的推进 ...")
        seen = []
        steps = []
        final_text = ""
        for _ in range(170):                 # 最多 85 秒
            st = js(ws, """(()=>{
              const steps=[...document.querySelectorAll('#cmdTimeline .step')].map(s=>({
                cls:s.className.replace('step','').trim(),
                n:s.querySelector('.n').textContent.trim(),
                t:s.querySelector('.t').textContent.trim()
              }));
              return {steps, hint:document.getElementById('cmdHint').textContent.trim(),
                      cmp:document.getElementById('cmdCmp').innerText.trim()};
            })()""")
            steps = st["steps"]
            cur = [s["cls"] + s["t"] for s in steps]
            if cur != seen:
                seen = cur
                for s in steps:
                    mark = {"done": "已完成", "cur": "进行中", "bad": "失败", "": "等待"}.get(s["cls"], s["cls"])
                    print(f"      {s['n']:<14} [{mark:<4}] {s['t']}")
                print("      " + "-" * 52)
            final_text = st["hint"] + "\n" + st["cmp"]
            if any(s["cls"] == "bad" for s in steps):
                break                        # 出现失败/超时，终止
            if steps and steps[-1]["cls"] == "done":
                break                        # 最后一步完成，终止
            time.sleep(0.5)

        print("\n[4] 页面最终呈现的文字结果：")
        for line in final_text.splitlines():
            if line.strip():
                print("    " + line.strip())

        p2 = shot(ws, "_w2_browser_2_after.png")
        print("\n    截图:", p2)

        # ---- 5. 判定 ----
        print("\n[5] 判定")
        all_done = bool(steps) and all(s["cls"] == "done" for s in steps)
        any_bad = any(s["cls"] == "bad" for s in steps)
        if all_done and not any_bad:
            last = steps[-1]["t"]
            print("    ✓ 真实浏览器里点击按钮 → 四阶段依次点亮 → 全部完成")
            print(f"      ④ 执行完成 停在 {last}")
            ok = True
        elif any_bad:
            print("    ✗ 页面上出现超时/失败 —— 这是真实结果，不是脚本问题")
            ok = False
        else:
            print("    ✗ 85 秒内时间线未走到终态")
            for s in steps:
                print(f"      {s['n']} = {s['cls'] or '等待'}")

        # 「下发前 N 条 → 下发后 M 条(+k)」直接从面板文字里取，
        # 这两个数是页面自己算出来的，不是脚本硬编的。
        import re as _re
        m = _re.search(r"下发前库里\s*([\d,]+)\s*条", final_text)
        n1 = _re.search(r"下发后库里\s*([\d,]+)\s*条", final_text)
        d1 = _re.search(r"新采集入库\s*(\d+)\s*条", final_text)
        if m and n1:
            a, b = int(m.group(1).replace(",", "")), int(n1.group(1).replace(",", ""))
            print(f"\n    面板上的数字：下发前 {a} 条 → 下发后 {b} 条（{b - a:+d}）")
            if d1:
                print(f"    其中带本次 request_id 的新采集：{d1.group(1)} 条")
            if b <= a:
                print("    ✗ 条数没有增加 —— 说明点按钮没有真的触发新采集")
                ok = False
        else:
            print("\n    ? 没能从面板文字里取到条数对比")

        ws.close()
    finally:
        if not args.keep_open:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:
                proc.kill()
        shutil.rmtree(profile, ignore_errors=True)

    print("\n结果:", "通过 ✓" if ok else "未通过 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
