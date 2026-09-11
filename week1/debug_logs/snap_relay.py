# -*- coding: utf-8 -*-
"""从服务器中转流抓一帧存证 + 打印中继健康状态"""
import json, time, urllib.request

BASE = "http://127.0.0.1:8000"
out = []

status = {}
try:
    with urllib.request.urlopen(BASE + "/api/camera/status", timeout=6) as f:
        status = json.loads(f.read())
except Exception as e:
    status = {"error": repr(e)}
out.append("中继状态: " + json.dumps(status, ensure_ascii=False, indent=1))
try:
    with urllib.request.urlopen(BASE + "/api/latest", timeout=6) as f:
        d = json.loads(f.read())
    r = d.get("record") or {}
    out.append("数据最新: age=%s 秒, seq=%s, 来源=%s" %
               (r.get("age_seconds"), r.get("seq"), r.get("src_ip")))
except Exception as e:
    out.append("数据接口失败: %r" % e)

# 抓一帧：读两个 boundary 之间的 JPEG
try:
    req = urllib.request.Request(BASE + "/api/camera?nocache=%d" % time.time())
    with urllib.request.urlopen(req, timeout=15) as f:
        buf = b""
        deadline = time.time() + 12
        while time.time() < deadline:
            c = f.read(8192)
            if not c:
                break
            buf += c
            s = buf.find(b"\xff\xd8\xff")
            e = buf.find(b"\xff\xd9", s + 3)
            if s >= 0 and e > s:
                frame = buf[s:e + 2]
                p = r"D:\aijiaohu\week1\debug_logs\relay_frame.jpg"
                with open(p, "wb") as g:
                    g.write(frame)
                out.append("抓帧成功: %d 字节 -> %s" % (len(frame), p))
                break
        else:
            out.append("抓帧超时")
except Exception as e:
    out.append("抓帧失败: %r" % e)

text = "\n".join(out)
print(text)
open(r"D:\aijiaohu\week1\debug_logs\relay_final.txt", "w", encoding="utf-8").write(text)
