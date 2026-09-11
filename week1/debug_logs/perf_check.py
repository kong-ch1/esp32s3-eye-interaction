"""区分：板义慢 还是 中转慢。
做法一：只读服务器自己的帧计数器（不增加任何连接），算稳定 fps。
做法二：看 IMU 上报是否也跟着卡（同一条 WiFi）。
"""
import json
import socket
import subprocess
import time
import urllib.request

OUT = r"D:\aijiaohu\week1\debug_logs\perf_check.txt"
o = []

def status():
    return json.loads(urllib.request.urlopen(
        "http://127.0.0.1:8000/api/camera/status", timeout=8).read())

s1 = status()
time.sleep(10)
s2 = status()
d = s2.get("frames_total", 0) - s1.get("frames_total", 0)
o.append("[中继] 10 秒内新增 %d 帧 -> %.1f fps" % (d, d / 10.0))
o.append("[中继] state=%s viewers=%s upstream_running=%s last_frame_age=%s"
         % (s2.get("state"), s2.get("viewers"),
            s2.get("upstream_running"), s2.get("last_frame_age")))
o.append("[中继] board_stream=%s error=%r" % (s2.get("board_stream"), s2.get("error")))

try:
    lat = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:8000/api/latest", timeout=6).read())
    r = lat.get("record") or {}
    o.append("[IMU] 距上次上报 %.1f 秒, seq=%s, src_ip=%s"
             % (r.get("age_seconds", -1), r.get("seq"), r.get("src_ip")))
except Exception as e:
    o.append("[IMU] ERR %r" % e)

sb = socket.socket()
sb.settimeout(4)
try:
    sb.connect(("10.1.41.160", 81))
    o.append("[板子] 81 端口 TCP 可达")
except Exception as e:
    o.append("[板子] 81 端口 FAIL %r" % e)
finally:
    sb.close()

p = subprocess.run(["ping", "-n", "6", "-w", "1500", "10.1.41.160"],
                   capture_output=True, text=True, encoding="gbk", errors="ignore")
line = [x for x in p.stdout.splitlines() if "%" in x or "平均" in x or "Average" in x]
o.append("[WiFi] " + (" | ".join(line[:3]) if line else p.stdout.strip()[-160:]))

open(OUT, "w", encoding="utf-8").write("\n".join(o))
print("\n".join(o))
