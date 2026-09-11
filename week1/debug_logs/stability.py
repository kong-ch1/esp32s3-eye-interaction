"""看板子是否周期性重启：连续采样 seq，看有没有回跳。"""
import json
import urllib.request

OUT = r"D:\aijiaohu\week1\debug_logs\stability.txt"
o = []
prev = None
import time
t0 = time.time()
resets = 0
samples = []
while time.time() - t0 < 25:
    try:
        d = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:8000/api/latest", timeout=5).read())
        r = d.get("record") or {}
        seq = r.get("seq")
        samples.append("%.0fs seq=%s age=%.1f" % (time.time() - t0, seq, r.get("age_seconds", -1)))
        if prev is not None and seq is not None and seq < prev:
            resets += 1
            samples.append("   ^^ seq 回跳，板子重启了")
        prev = seq
    except Exception as e:
        samples.append("ERR %r" % e)
    time.sleep(2)

o.append("25 秒内 seq 回跳次数: %d" % resets)
o.extend(samples)
open(OUT, "w", encoding="utf-8").write("\n".join(o))
print("\n".join(o))
