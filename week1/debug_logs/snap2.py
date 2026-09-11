"""实测服务器中转：拉 5 秒视频流，数 JPEG 帧并存最后一帧。"""
import http.client
import time

HOST, PORT = "127.0.0.1", 8000
OUT_TXT = r"D:\aijiaohu\week1\debug_logs\relay_frame2.txt"
OUT_JPG = r"D:\aijiaohu\week1\debug_logs\relay_frame2.jpg"

conn = http.client.HTTPConnection(HOST, PORT, timeout=20)
conn.request("GET", "/api/camera")
resp = conn.getresponse()
ctype = resp.getheader("Content-Type")
o = ["HTTP %d, Content-Type=%s" % (resp.status, ctype)]

if resp.status != 200:
    body = resp.read()[:400]
    o.append("非 200，响应: %r" % body)
else:
    buf = b""
    t0 = time.time()
    # 边界随 Content-Type 动态取，避免写死
    bnd = ctype.split("boundary=")[-1].encode() if "boundary=" in ctype else b"--frame"
    frames = 0
    last = None
    while time.time() - t0 < 5:
        chunk = resp.read(8192)
        if not chunk:
            break
        buf += chunk
        if b"\xff\xd8\xff" in chunk:
            # 每次出现 SOI 都算一帧新图
            idxs = []
            start = 0
            while True:
                i = chunk.find(b"\xff\xd8\xff", start)
                if i < 0:
                    break
                idxs.append(i)
                start = i + 1
            frames += len(idxs)
        bnd_count = chunk.count(bnd)
        del bnd_count
    elapsed = time.time() - t0
    o.append("%.1f 秒内收到 %d 字节，JPEG SOI 出现 %d 次 -> 约 %.1f fps"
             % (elapsed, len(buf), frames, frames / elapsed if elapsed else 0))

    # 从缓冲里抠出最后一帧完整 JPEG
    parts = buf.split(b"\xff\xd8\xff")
    if len(parts) > 1:
        tail = parts[-1]
        e = tail.find(b"\xff\xd9")
        if e > 0:
            last = b"\xff\xd8\xff" + tail[: e + 2]
            open(OUT_JPG, "wb").write(last)
            o.append("最后一帧已保存: %d 字节 -> %s" % (len(last), OUT_JPG))
        else:
            o.append("缓冲内未找到完整 EOI（正常，帧被截断）")
    else:
        o.append("缓冲内没有 JPEG 起始标记")

conn.close()
open(OUT_TXT, "w", encoding="utf-8").write("\n".join(o))
print("\n".join(o))
