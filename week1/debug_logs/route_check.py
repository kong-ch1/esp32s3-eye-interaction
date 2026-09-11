"""确认摄像头当前走的是哪条路：服务器中转 还是 板子直连。"""
import json
import re
import socket
import urllib.request

OUT = r"D:\aijiaohu\week1\debug_logs\route_check.txt"
o = []

# 1) 服务器是否活着、端口谁在监听
try:
    r = urllib.request.urlopen("http://127.0.0.1:8000/api/camera/status", timeout=8)
    st = json.loads(r.read())
    o.append("[服务器] /api/camera/status -> " + json.dumps(st, ensure_ascii=False))
except Exception as e:
    o.append("[服务器] 不可达: %r" % e)
    st = {}

# 2) 网页源码里的路由配置
try:
    h = urllib.request.urlopen("http://127.0.0.1:8000/", timeout=6).read().decode("utf-8", "ignore")
    m = re.search(r"CAM_MODE\s*=\s*'(\w+)'", h)
    o.append("[网页] CAM_MODE = %s" % (m.group(1) if m else "未找到"))
    o.append("[网页] 含 /api/camera : %s" % ("/api/camera" in h))
    o.append("[网页] 残留板子IP %s : %s" % ("10.1.41.160", "10.1.41.160" in h))
except Exception as e:
    o.append("[网页] 读取失败: %r" % e)

# 3) 实际走一趟中转：拉 2 秒，数帧
frames = 0
bytes_got = 0
try:
    req = urllib.request.Request("http://127.0.0.1:8000/api/camera")
    with urllib.request.urlopen(req, timeout=15) as f:
        deadline = __import__("time").time() + 5
        buf = b""
        f.fp._sock.settimeout(6)
        while __import__("time").time() < deadline:
            try:
                chunk = f.read(4096)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            bytes_got += len(chunk)
        frames = buf.count(b"\xff\xd8\xff")
    o.append("[中转实测] 5 秒收到 %d 字节，含 JPEG 帧起始标记 %d 个" % (bytes_got, frames))
except Exception as e:
    o.append("[中转实测] 失败: %r" % e)

# 4) 顺带看板子是否在线（仅用于说明，网页现在不该直连它）
s = socket.socket()
s.settimeout(3)
try:
    s.connect(("10.1.41.160", 81))
    o.append("[板子] 10.1.41.160:81 TCP 可达")
except Exception as e:
    o.append("[板子] 不可达: %r" % e)
finally:
    s.close()

open(OUT, "w", encoding="utf-8").write("\n".join(o))
print("\n".join(o))
