# -*- coding: utf-8 -*-
"""验证服务器端 MJPEG 中转是否可用"""
import json, socket, time, urllib.request, threading

BASE = "http://127.0.0.1:8000"
out = []
def log(s=""):
    out.append(str(s)); print(s, flush=True)

log("=" * 60)
log("时间: " + time.strftime("%Y-%m-%d %H:%M:%S"))

log("")
log("[1] 中继状态 /api/camera/status")
try:
    with urllib.request.urlopen(BASE + "/api/camera/status", timeout=5) as f:
        log("    " + json.dumps(json.loads(f.read()), ensure_ascii=False))
except Exception as e:
    log("    失败: %r" % e)

def pull(tag, secs, idx, res):
    """直读 multipart，统计帧数"""
    import http.client
    try:
        conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=10)
        conn.request("GET", "/api/camera?nocache=%d" % time.time())
        r = conn.getresponse()
        res[idx] = "HTTP %d %s" % (r.status, r.getheader("Content-Type"))
        if r.status != 200:
            return
        buf = b""
        frames = 0
        t0 = time.time()
        while time.time() - t0 < secs:
            c = r.read(8192)
            if not c:
                break
            buf += c
            frames += buf.count(b"\xff\xd8\xff")
            buf = buf[-8:]
        dt = time.time() - t0
        res[idx] = "%s -> %d 帧 / %.1fs = %.1f fps" % (res[idx], frames, dt, frames/dt if dt else 0)
        conn.close()
    except Exception as e:
        res[idx] = "失败: %r" % e

log("")
log("[2] 单个客户端拉 6 秒")
res = {}
pull("A", 6, "A", res)
log("    " + str(res.get("A")))

log("")
log("[3] ★ 同时 3 个客户端拉 6 秒（板端应始终只有 1 路）")
res2 = {}
ths = []
for i, k in enumerate(("A", "B", "C")):
    t = threading.Thread(target=pull, args=(k, 6, k, res2))
    t.start(); ths.append(t); time.sleep(0.4)
for t in ths: t.join(timeout=12)
for k in ("A", "B", "C"):
    log("    %s: %s" % (k, res2.get(k)))

log("")
log("[4] 再看一次状态（看 viewers 是否归零、上游是否被回收）")
time.sleep(6)
try:
    with urllib.request.urlopen(BASE + "/api/camera/status", timeout=5) as f:
        log("    " + json.dumps(json.loads(f.read()), ensure_ascii=False))
except Exception as e:
    log("    失败: %r" % e)

log("")
log("[5] 数据接口仍然正常吗")
try:
    with urllib.request.urlopen(BASE + "/api/latest", timeout=5) as f:
        d = json.loads(f.read())
    rec = d.get("record") or {}
    log("    age=%s seq=%s ip=%s" % (rec.get("age_seconds"), rec.get("seq"), rec.get("src_ip")))
except Exception as e:
    log("    失败: %r" % e)

log("=" * 60)
open(r"D:\aijiaohu\week1\relay_out.txt", "w", encoding="utf-8").write("\n".join(out))
