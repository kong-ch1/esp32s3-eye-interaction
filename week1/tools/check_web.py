#!/usr/bin/env python3
"""网页静态检查 —— 本项目没有构建步骤，所以这些只能靠脚本兜住。

检查三件事：
  1) 内联 <script> 的 JS 语法（用 node --check）
  2) JS 里 $('xxx') 引用的元素 id 在 HTML 中是否都存在（拼错 id 是静默失败）
  3) 页面调用的每个 API 端点是否都能通（服务器在跑时才有意义）

用法:
    python tools/check_web.py
    python tools/check_web.py --base http://127.0.0.1:8000

退出码非 0 表示有不通过项，可以直接串进别的检查流程。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "web", "index.html")
NODE_CANDIDATES = [
    os.path.expanduser(r"~\AppData\Local\Programs\node\node.exe"),
    r"D:\gongju\nodejs\node.exe",
]


def find_node():
    for p in NODE_CANDIDATES:
        if os.path.exists(p):
            return p
    return shutil.which("node")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--no-api", action="store_true", help="跳过接口连通性检查")
    args = ap.parse_args()

    ok = True
    html = open(INDEX, encoding="utf-8").read()
    print(f"页面: {INDEX}  ({len(html)} 字节)")

    # ---- 1. JS 语法 ----
    m = re.search(r"<script>(.*)</script>", html, re.S)
    if not m:
        print("✗ 没找到内联 <script>")
        return 1
    js = m.group(1)
    tmp = os.path.join(ROOT, "debug_logs", "_web_js_check.js")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(js)

    node = find_node()
    print(f"\n[1] JS 语法检查（node: {node or '未找到'}）")
    if node:
        r = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"    ✓ 通过（{len(js.splitlines())} 行）")
        else:
            ok = False
            print("    ✗ 失败:")
            print(r.stderr[:1500])
    else:
        print("    - 跳过（环境里没有 node）")

    # ---- 2. 元素 id 引用一致性 ----
    print("\n[2] 元素 id 引用一致性")
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r"\$\('([^']+)'\)", js))
    missing = sorted(used - ids)
    print(f"    HTML 定义 {len(ids)} 个 id，JS 引用 {len(used)} 个")
    if missing:
        ok = False
        print("    ✗ JS 引用了不存在的 id（拼错会静默失效）:", missing)
    else:
        print("    ✓ 全部能对上")

    # ---- 3. 接口连通性 ----
    if not args.no_api:
        print(f"\n[3] 接口连通性（{args.base}）")
        endpoints = ["/api/latest", "/api/history?limit=1", "/api/devices",
                     "/api/commands?limit=1", "/api/camera/status"]
        for ep in endpoints:
            try:
                with urllib.request.urlopen(args.base + ep, timeout=8) as r:
                    n = len(r.read())
                print(f"    ✓ {ep:<28} HTTP 200 ({n} 字节)")
            except Exception as e:
                ok = False
                print(f"    ✗ {ep:<28} {e}")

    print("\n结果:", "全部通过 ✓" if ok else "有不通过项 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
