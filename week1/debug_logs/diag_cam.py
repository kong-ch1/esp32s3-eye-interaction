# -*- coding: utf-8 -*-
"""临时诊断：摄像头 81 端口 + 本地服务器 8000 端口 + 数据新鲜度"""
import socket, json, urllib.request, subprocess, re, time

def tcp(host, port, timeout=2.0):
    s = socket.socket(); s.settimeout(timeout)
    try:
        s.connect((host, port)); return True
    except Exception as e:
        return False, str(e)
    finally:
        s.close()

print("=" * 60)
# 本机 IP
try:
    out = subprocess.run(["ipconfig"], capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    ips = re.findall(r"IPv4[^\d]*(\d+\.\d+\.\d+\.\d+)", out)
    print("本机 IPv4:", ips)
except Exception as e:
    print("ipconfig 失败:", e)

print("=" * 60)
cam_ip = "10.1.41.160"
r = tcp(cam_ip, 81)
print(f"板子 {cam_ip}:81 ->", "通" if r is True else f"不通 {r[1]}")

# 扫一下常见候选 IP（DHCP 可能换地址）
print("\n扫描同网段 81 端口...")
same_net = None
try:
    out = subprocess.run(["ipconfig"], capture_output=True, text=True, encoding="gbk", errors="ignore").stdout
    m = re.search(r"IPv4[^\d]*(\d+\.\d+\.\d+\.\d+)", out)
    if m:
        same_net = ".".join(m.group(1).split(".")[:3])
except Exception:
    pass
if same_net:
    print("网段:", same_net + ".x")
    found = []
    for i in [160, 100, 101, 102, 103, 150, 161, 159, 200, 2]:
        ip = f"{same_net}.{i}"
        if tcp(ip, 81, 0.6) is True:
            found.append(ip)
    print("开放 81 端口的 IP:", found if found else "无")

print("=" * 60)
r8000 = tcp("127.0.0.1", 8000)
print(f"本地服务器 127.0.0.1:8000 ->", "通" if r8000 is True else f"不通 {r8000[1]}")
if r8000 is True:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/latest", timeout=3) as f:
            d = json.loads(f.read().decode())
        print("/api/latest:")
        print(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as e:
        print("拉 latest 失败:", e)
print("=" * 60)
