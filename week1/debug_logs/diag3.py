# -*- coding: utf-8 -*-
"""确认两件事：1) 板子最近有没有重启过  2) 现在还能不能正常拉到视频帧"""
import json, urllib.request, socket, time

lines = []
def log(s=""):
    lines.append(str(s)); print(s, flush=True)

# ---- 1. 重启检测：history 按时间倒序，seq 反向跳变即为重启点 ----
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/history?limit=800", timeout=8) as f:
        hist = json.loads(f.read().decode())
    rows = hist.get("records") or hist.get("data") or hist
    log("history 返回 %d 条" % len(rows))
    if rows:
        log("最新: seq=%s @ %s" % (rows[0].get("seq"), rows[0].get("server_time")))
        log("最旧: seq=%s @ %s" % (rows[-1].get("seq"), rows[-1].get("server_time")))
    boots = []
    prev = None
    for r in rows:                      # rows[0] 是最新
        s = r.get("seq")
        if prev is not None and s is not None and s > prev:
            boots.append((prev, s, r.get("server_time")))
        prev = s
    log("在这段历史里检测到 %d 次疑似重启(seq 归零)" % len(boots))
    for b in boots[-5:]:
        log("    seq %s -> %s @ %s" % b)
except Exception as e:
    log("history 拉取失败: %r" % e)

log("")
log("---- 2. 现在连续探测 6 次 /capture 单帧 ----")
for i in range(6):
    t0 = time.time()
    try:
        req = urllib.request.Request("http://10.1.41.160:81/capture")
        with urllib.request.urlopen(req, timeout=6) as f:
            data = f.read()
        log("  第%d次: 成功 %d 字节, 耗时 %.2fs" % (i + 1, len(data), time.time() - t0))
    except Exception as e:
        log("  第%d次: 失败 %r, 耗时 %.2fs" % (i + 1, e, time.time() - t0))

with open(r"D:\aijiaohu\week1\diag3_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
