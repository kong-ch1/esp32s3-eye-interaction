# -*- coding: utf-8 -*-
"""串口日志 + 网络压测 同步进行，便于把「网络现象」和「板端状态」对齐。
   用法: <idf python> combined_diag.py COM5
   注意：pyserial 打开串口时会拉一下 DTR/RTS，等于顺手给板子复位，正好做干净基线。"""
import serial, threading, time, socket, urllib.request, sys

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM5"
CAM = "10.1.41.160:81"
T0 = time.time()
lines = []
lock = threading.Lock()


def log(s):
    with lock:
        el = time.time() - T0
        line = "[%6.1fs] %s" % (el, s)
        lines.append(line)
        print(line, flush=True)


def serial_reader(ev_stop):
    try:
        ser = serial.Serial(PORT, 115200, timeout=1)
        ser.setDTR(False)
        ser.setRTS(False)
        ser.reset_input_buffer()
    except Exception as e:
        log("串口打开失败: %r" % e)
        return
    while not ev_stop.is_set():
        try:
            b = ser.readline()
        except Exception:
            break
        if b:
            try:
                log("  SER | " + b.decode("utf-8", "replace").rstrip("\r\n"))
            except Exception:
                pass
    ser.close()


def get(path, timeout=5):
    t = time.time()
    try:
        with urllib.request.urlopen("http://" + CAM + path, timeout=timeout) as f:
            return True, f.read(), time.time() - t
    except Exception as e:
        return False, repr(e), time.time() - t


def ping(tag):
    ok, body, dt = get("/ping")
    info = ""
    if ok and isinstance(body, bytes):
        import re
        m = re.search(r"clients=(\d+)/(\d+)", body.decode("utf-8", "ignore"))
        if m:
            info = "clients=" + m.group(1)
    log("  NET | %s /ping -> %s %.3fs %s" % (tag, "OK" if ok else "FAIL", dt, info))
    return ok


def one_stream_round(rnd, secs=4):
    log("  NET | --- 第 %d 轮视频流开始 ---" % rnd)
    frames = 0
    buf = b""
    s = None
    try:
        s = socket.create_connection(("10.1.41.160", 81), timeout=5)
        s.settimeout(3)
        s.sendall(b"GET /stream HTTP/1.1\r\nHost: 10.1.41.160\r\nConnection: close\r\n\r\n")
        t = time.time()
        while time.time() - t < secs:
            try:
                c = s.recv(8192)
            except socket.timeout:
                continue
            if not c:
                log("  NET |   连接被服务端关闭")
                break
            buf += c
            frames += buf.count(b"\xff\xd8\xff")
            buf = buf[-8:]
        log("  NET |   收得 %d 帧 / %.1fs" % (frames, time.time() - t))
    except Exception as e:
        log("  NET |   失败: %r" % e)
    finally:
        if s:
            try:
                s.close()
            except Exception:
                pass
    ping("断开后")


stop = threading.Event()
th = threading.Thread(target=serial_reader, args=(stop,), daemon=True)
th.start()

log("=== 等待板子启动 15 秒 ===")
time.sleep(15)

log("=== 阶段 A：基础服务可用性 ===")
for i in range(3):
    ping("A%d" % (i + 1))
    time.sleep(0.5)
ok, body, dt = get("/capture")
log("  NET | /capture -> %s %.3fs %d 字节" % (ok, dt, len(body) if isinstance(body, bytes) else 0))

log("=== 阶段 B：连续 5 轮 建立->拉流->断开->立刻探测 ===")
for r in range(1, 6):
    one_stream_round(r)
    time.sleep(1.0)

log("=== 阶段 C：再来一次 /capture，确认服务器没被堵死 ===")
ok, body, dt = get("/capture")
log("  NET | /capture -> %s %.3fs %d 字节" % (ok, dt, len(body) if isinstance(body, bytes) else 0))
ping("收尾")

time.sleep(2)
stop.set()
th.join(timeout=3)

with open(r"D:\aijiaohu\week1\combined_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
