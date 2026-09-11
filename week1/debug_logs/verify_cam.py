# -*- coding: utf-8 -*-
"""验证异步改造是否真的解决了「第二次连接就卡死」"""
import socket, time, threading, urllib.request

CAM = "10.1.41.160:81"
res = []
def log(s=""):
    res.append(str(s)); print(s, flush=True)

def get(url, timeout=5):
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as f:
            return True, f.status, f.read(), time.time() - t0
    except Exception as e:
        return False, None, repr(e), time.time() - t0

log("=" * 62)
log("验证时间: " + time.strftime("%Y-%m-%d %H:%M:%S"))
log("=" * 62)

# 1. 心跳是否秒回
log("[1] /ping 心跳 x3")
for i in range(3):
    ok, st, body, dt = get("http://%s/ping" % CAM)
    txt = body.decode("utf-8", "ignore").replace("\n", "  ") if isinstance(body, bytes) else body
    log("    第%d次: %s %.3fs -> %s" % (i + 1, "OK" if ok else "FAIL", dt, txt[:90]))

# 2. 板子自带首页
log("")
log("[2] / 板端首页")
ok, st, body, dt = get("http://%s/" % CAM)
log("    %s status=%s %.3fs 长度=%s" % ("OK" if ok else "FAIL", st, dt, len(body) if isinstance(body, bytes) else "-"))

# 3. 连续 5 次单帧抓拍（之前第二次必超时）
log("")
log("[3] /capture 连续 5 次")
for i in range(5):
    ok, st, body, dt = get("http://%s/capture" % CAM)
    ln = len(body) if isinstance(body, bytes) else 0
    log("    第%d次: %s status=%s %.3fs %d字节 %s" %
        (i + 1, "OK" if ok else "FAIL", st, dt, ln, "JPEG" if isinstance(body, bytes) and body[:3] == b"\xff\xd8\xff" else ""))

# 4. ★关键回归★ 反复建立/断开视频流，看服务器会不会被堵死
log("")
log("[4] 反复重连视频流 4 轮（每轮拉 3 秒后主动断开）")
for rnd in range(4):
    from urllib.parse import urlparse
    s = None
    frames = 0
    buf = b""
    try:
        s = socket.create_connection(("10.1.41.160", 81), timeout=5)
        s.settimeout(3)
        s.sendall(b"GET /stream HTTP/1.1\r\nHost: 10.1.41.160\r\nConnection: close\r\n\r\n")
        t0 = time.time()
        while time.time() - t0 < 3:
            try:
                c = s.recv(8192)
            except socket.timeout:
                continue
            if not c:
                break
            buf += c
            frames += buf.count(b"\xff\xd8\xff")
            buf = buf[-8:]
        dt = time.time() - t0
    except Exception as e:
        log("    第%d轮: 连接失败 %r" % (rnd + 1, e))
        if s: s.close()
        continue
    finally:
        if s:
            try: s.close()
            except Exception: pass
    log("    第%d轮: %d 帧 / %.1fs = %.1f fps" % (rnd + 1, frames, dt, frames / dt if dt else 0))
    # 断开后立刻探测服务器有没有被卡住 —— 这是最关键的判据
    ok, st, body, pdt = get("http://%s/ping" % CAM, timeout=3)
    log("           断开后立刻 /ping: %s %.3fs %s" %
        ("OK(服务器清醒)" if ok else "FAIL(服务器被堵死!)", pdt, ""))
    time.sleep(0.5)

# 5. 超限拒接：同时开 4 路云町看510是否快速失败而不是挂起
log("")
log("[5] 同时开 4 路视频流（上限 2），检查多余的会不会被快速拒绝")
socks = []
for i in range(4):
    try:
        s = socket.create_connection(("10.1.41.160", 81), timeout=4)
        s.settimeout(4)
        s.sendall(b"GET /stream HTTP/1.1\r\nHost: 10.1.41.160\r\n\r\n")
        socks.append(s)
    except Exception as e:
        log("    第%d路: 连接失败 %r" % (i + 1, e))
time.sleep(2.0)
for i, s in enumerate(socks):
    got = b""
    try:
        s.settimeout(1.0)
        got = s.recv(300)
    except Exception:
        got = b"<no response / timeout>"
    head = got.split(b"\r\n")[0][:60]
    log("    第%d路回应: %r" % (i + 1, head))
    try: s.close()
    except Exception: pass

log("")
log("=" * 62)
with open(r"D:\aijiaohu\week1\verify_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(res))
