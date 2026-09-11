"""自愈中继验证：
1) 第一轮拉 12 秒看帧率与连接数
2) 断开后看中继能否自己收摊
3) 第二轮再拉，验证「从零恢复」不需要重启服务器
"""
import http.client
import json
import time
import urllib.request

OUT = r"D:\aijiaohu\week1\debug_logs\selfheal.txt"
o = []


def status():
    return json.loads(urllib.request.urlopen(
        "http://127.0.0.1:8000/api/camera/status", timeout=8).read())


def pull(seconds, tag):
    conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=30)
    conn.request("GET", "/api/camera")
    r = conn.getresponse()
    if r.status != 200:
        o.append("[%s] 非 200: %d %r" % (tag, r.status, r.read()[:200]))
        conn.close()
        return 0, 0
    n = 0
    gbytes = 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            c = r.read(16384)
        except Exception:
            break
        if not c:
            break
        gbytes += len(c)
        n += c.count(b"\xff\xd8\xff")
    el = time.time() - t0
    conn.close()
    o.append("[%s] %.1fs 内 %d 帧 -> %.2f fps（%d 字节）" % (tag, el, n, n / el, gbytes))
    return n, el


st0 = status()
o.append("起始状态: " + json.dumps(st0, ensure_ascii=False))

pull(12, "第1轮")

st1 = status()
o.append("第1轮后: " + json.dumps(st1, ensure_ascii=False))

time.sleep(8)
st2 = status()
o.append("断开 8 秒后: " + json.dumps(st2, ensure_ascii=False))

pull(12, "第2轮")

st3 = status()
o.append("第2轮后: " + json.dumps(st3, ensure_ascii=False))

open(OUT, "w", encoding="utf-8").write("\n".join(o))
print("\n".join(o))
