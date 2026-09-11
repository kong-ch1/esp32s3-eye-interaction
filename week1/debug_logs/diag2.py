# -*- coding: utf-8 -*-
"""摄像头流专项诊断：直连拉流、并发占用、重启检测。结果写入 diag2_out.txt"""
import socket, json, urllib.request, threading, time, io, sys

OUT = []

def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

def http_head(url, timeout=4):
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return f.status, dict(f.headers), f.read(64)
    except Exception as e:
        return None, None, repr(e)

def grab_stream(url, seconds, stop_evt, counter, idx):
    """持续读流，统计收到的 JPEG 帧数"""
    # 主动开 socket，超时设短一点
    from urllib.parse import urlparse
    u = urlparse(url)
    frames = 0
    bytes_read = 0
    t0 = time.time()
    try:
        s = socket.create_connection((u.hostname, u.port), timeout=5)
        s.settimeout(5)
        s.sendall(f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nConnection: close\r\n\r\n".encode())
        buf = b""
        while not stop_evt.is_set() and time.time() - t0 < seconds:
            try:
                chunk = s.recv(8192)
            except socket.timeout:
                continue
            except Exception:
                break
            if not chunk:
                break
            bytes_read += len(chunk)
            buf += chunk
            frames += buf.count(b"\xff\xd8\xff")
            # 只保留尾部，避免内存涨
            buf = buf[-8:]
        s.close()
    except Exception as e:
        counter[idx] = f"连接失败: {e!r}"
        return
    dt = time.time() - t0
    counter[idx] = f"{frames} 帧 / {bytes_read} 字节 / {dt:.1f}s / {bytes_read/dt/1024:.1f} KB/s"

log("=" * 64)
log("诊断时间: " + time.strftime("%Y-%m-%d %H:%M:%S"))
log("=" * 64)

CAM = "http://10.1.41.160:81"
log("[1] 板子根路径探测 GET " + CAM + "/")
st, hd, body = http_head(CAM + "/")
log("    状态: %s" % (st,))
if hd:
    for k, v in hd.items():
        log("    %s: %s" % (k, v))
else:
    log("    失败: %s" % (body,))

log("")
log("[2] 单连接拉流 8 秒 /stream")
stop = threading.Event()
cnt = {}
t = threading.Thread(target=grab_stream, args=(CAM + "/stream", 8, stop, cnt, "single"))
t.start()
time.sleep(8.5)
stop.set()
t.join(timeout=2)
log("    结果: %s" % cnt.get("single", "无响应（没有拿到任何数据）"))

log("")
log("[3] 并发两路拉流 6 秒（检验是否只允许一个客户端）")
stop2 = threading.Event()
cnt2 = {}
ths = []
for i in ("A", "B"):
    th = threading.Thread(target=grab_stream, args=(CAM + "/stream", 6, stop2, cnt2, i))
    th.start(); ths.append(th)
    time.sleep(0.8)   # 错开 0.8s 建立，模拟"又开了一个标签页"
time.sleep(6.5)
stop2.set()
for th in ths:
    th.join(timeout=2)
log("    连接 A: %s" % cnt2.get("A", "无响应"))
log("    连接 B: %s" % cnt2.get("B", "无响应"))

log("")
log("[4] 本地服务器 /api/history —— 查 seq 是否回退（回退=板子重启过）")
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/history?limit=300", timeout=5) as f:
        hist = json.loads(f.read().decode())
    rows = hist.get("records") or hist.get("data") or hist
    log("    拿到 %d 条记录" % len(rows))
    seqs = [r.get("seq") for r in rows]
    resets = []
    for i in range(1, len(seqs)):
        if seqs[i] is not None and seqs[i-1] is not None and seqs[i] < seqs[i-1]:
            resets.append((rows[i-1].get("seq"), rows[i].get("seq"), rows[i].get("server_time")))
    log("    seq 回退次数: %d" % len(resets))
    if resets:
        log("    （每次回退 = 板子重启一次，下面列出最近 10 次）")
        for r in resets[-10:]:
            log("      %s -> %s  @ %s" % r)
    else:
        log("    最近 300 条内无重启")
    # 上报间隔
    if len(rows) > 2:
        gaps = []
        for i in range(1, len(rows)):
            try:
                g = rows[i]["server_ms"] - rows[i-1]["server_ms"]
                if 0 < g < 60000:
                    gaps.append(g)
            except Exception:
                pass
        if gaps:
            gaps.sort()
            log("    上报间隔 ms: 最小 %d / 中位 %d / 最大 %d / 样本 %d" %
                (gaps[0], gaps[len(gaps)//2], gaps[-1], len(gaps)))
    if rows:
        log("    最新一条: seq=%s 时间=%s src=%s" %
            (rows[-1].get("seq"), rows[-1].get("server_time"), rows[-1].get("src_ip")))
except Exception as e:
    log("    拉取失败: %r" % e)

log("=" * 64)

with open(r"D:\aijiaohu\week1\diag2_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(OUT))
