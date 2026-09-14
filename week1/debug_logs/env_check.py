import json, socket, subprocess, re, urllib.request

out = []
# 1) 串口（用 PowerShell 查，避免依赖 pyserial）
try:
    import subprocess as _sp
    r = _sp.run(["powershell", "-NoProfile", "-Command",
                 "[System.IO.Ports.SerialPort]::GetPortNames() -join ','"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20)
    ports = r.stdout.strip()
    out.append("PORTS=" + (ports if ports else "(none)"))
except Exception as e:
    out.append("PORTS ERR %r" % e)

# 2) ping 板子
p = subprocess.run(["ping", "-n", "3", "-w", "1200", "10.1.41.160"],
                   capture_output=True, text=True, encoding="gbk", errors="ignore")
m = re.search(r"(\d+%)", p.stdout)
out.append("PING_LOSS=" + (m.group(1) if m else "n/a"))

# 3) 板子 81 端口
s = socket.socket(); s.settimeout(3)
try:
    s.connect(("10.1.41.160", 81)); out.append("BOARD_81=TCP_OK")
except Exception as e:
    out.append("BOARD_81=FAIL %r" % e)
finally:
    s.close()

# 4) 本地服务器
try:
    d = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/api/latest", timeout=4).read())
    r = d.get("record") or {}
    out.append("SERVER=UP age=%.1f seq=%s ip=%s" % (r.get("age_seconds"), r.get("seq"), r.get("src_ip")))
except Exception as e:
    out.append("SERVER=DOWN %r" % e)

open(r"D:\aijiaohu\week1\debug_logs\env_check.txt", "w", encoding="utf-8").write("\n".join(out))
