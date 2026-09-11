#!/usr/bin/env python3
"""摄像头 MJPEG 中继 —— 让视频流走服务器，而不是每个网页自己去连板子。

为什么必须这样做，而不是简单地用 requests 转发一下：

 1) 板子扛不住多路。实测 ESP32-S3-EYE 单路 QVGA 4~5 fps，两路并发直接崩到 0.1 fps。
    如果每个浏览器各自申请一路 /stream，两个人同时看网页就没画面了。
    这里改成：服务器跟板子始终保持【一路】连接，解析出 JPEG 帧，再分发给任意多个浏览器。
    板端永远只有 1 个客户端，压力恒定。

 2) 网页不再需要知道板子的 IP。板子 IP 由服务器从最近一条上报记录里自动取，
    换路由器导致 IP 变了也不用改代码。以后做远程访问更是只需要暴露服务器这一个端口。

 3) 浏览器<supported>只认 multipart/x-mixed-replace，所以中继出来的格式必须和板端一致，
    否则 <img src> 只会显示第一帧然后卡住。

用法（由 server.py 调用，不单独运行）:
    relay = CameraRelay()
    relay.subscribe() -> Queue      # 每个观看者一个队列
    relay.unsubscribe(q)
    relay.get_status() -> dict
"""

from __future__ import annotations

import queue
import re
import threading
import time
import urllib.request

# 中继给浏览器的分隔串（与板端那个不一样，避免混淆）
FRAME_BOUNDARY = "week1relayframe"
CLIENT_BOUNDARY = ("--" + FRAME_BOUNDARY).encode("ascii")


class CameraRelay:
    """一个上游连接 + 多个下游订阅者"""

    def __init__(self, board_url: str = "", poll_seconds: float = 2.0,
                 latest_ip_fn=None):
        """latest_ip_fn: 返回最近上报的设备 IP 的函数，由 server.py 注入。
        传进来是为了避免反向 import server（会形成循环依赖）。"""
        self._latest_ip_fn = latest_ip_fn
        self._lock = threading.Lock()
        self._subs: "set[queue.Queue]" = set()
        self._pump = None              # 上游读取线程
        self._stop = threading.Event()
        # 没人观看后多久断开上游（留一点余量，避免刷新页面就重连）
        self._idle_before_stop = 5.0
        # 统计
        self.last_frame_at = 0.0       # 最近一帧到达时间
        self.frames_total = 0
        self.state = "idle"            # idle / connecting / streaming / error
        self.last_error = ""
        self.board_url_resolved = ""
        # board_url 为空时，由 resolve_url() 动态从数据库里的最新 src_ip 推导
        self._board_url_fixed = board_url
        self._poll = poll_seconds

    # ---------- 板子地址 ----------
    def set_board_url(self, url: str):
        """命令行 --camera-url 指定后写死；不指定则自动从上报记录里找。"""
        self._board_url_fixed = url

    def resolve_url(self) -> str:
        """优先用命令行指定的固定地址；否则从最近一条上报记录取板子 IP。
        这样板子换 IP（DHCP 重新分配）时不需要改任何代码。"""
        if self._board_url_fixed:
            return self._board_url_fixed
        ip = self._latest_ip()
        if ip:
            self.board_url_resolved = f"http://{ip}:81/stream"
            return self.board_url_resolved
        return ""

    def _latest_ip(self) -> str:
        if self._latest_ip_fn:
            try:
                return self._latest_ip_fn() or ""
            except Exception:
                return ""
        return ""

    # ---------- 订阅管理 ----------
    def subscribe(self) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue(maxsize=2)
        with self._lock:
            self._subs.add(q)
            self._maybe_start_pump_locked()
        return q

    def unsubscribe(self, q: "queue.Queue"):
        with self._lock:
            self._subs.discard(q)

    def _maybe_start_pump_locked(self):
        if self._pump and self._pump.is_alive():
            return
        self._stop.clear()
        self._pump = threading.Thread(target=self._run, daemon=True)
        self._pump.start()

    # ---------- 上游读取 ----------
    def _run(self):
        url = self.resolve_url()
        if not url:
            self.state = "error"
            self.last_error = "还没收到过板子的上报，无法确定板子 IP"
            time.sleep(2)
            self._subs.clear()
            return

        self.state = "connecting"
        self.last_error = ""
        try:
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            self.state = "error"
            self.last_error = f"连不上板子 {url}: {e}"
            return

        ctype = resp.headers.get("Content-Type", "")
        m = re.search(r'boundary=(?:"([^"]+)"|([^\s;]+))', ctype)
        if not m:
            self.state = "error"
            self.last_error = f"板端流格式不对: {ctype}"
            resp.close()
            return
        boundary = (m.group(1) or m.group(2)).encode("latin-1")
        marker = b"--" + boundary

        self.state = "streaming"
        self.last_error = ""
        buf = b""
        last_activity = time.time()

        try:
            while not self._stop.is_set():
                # 没人看了：超过宽限时间就断开上游，别白占板子的名额和带宽
                with self._lock:
                    nsub = len(self._subs)
                if nsub == 0:
                    if time.time() - last_activity > self._idle_before_stop:
                        self.state = "idle"
                        break
                    time.sleep(0.2)
                    continue
                last_activity = time.time()

                try:
                    chunk = resp.read(16384)
                except Exception as e:
                    self.last_error = f"读取中断: {e}"
                    break
                if not chunk:
                    self.last_error = "板端关闭了连接"
                    break

                buf += chunk
                buf, got = self._extract_frames(buf, marker)
                if got:
                    last_activity = time.time()
                # 缓冲区保护：解不出来就别让它无限涨
                if len(buf) > 1_000_000:
                    buf = b""
        finally:
            try:
                resp.close()
            except Exception:
                pass
            if self.state != "idle":
                self.state = "error"
            # 通知所有订阅者收摊，让页面的 <img> 触发 onerror 自动重连
            self._wake_all()

    def _extract_frames(self, buf: bytes, marker: bytes):
        """从 MJPEG 流里切出 JPEG 帧。返回 (剩余未处理数据, 是否解出帧)"""
        got = False
        while True:
            i = buf.find(marker)
            if i < 0:
                # 还没走到帧边界，保留尾部一小段防止边界被截断
                keep = len(marker) + 4
                return (buf[-keep:] if len(buf) > keep else buf), got
            rest = i + len(marker)
            hdr_end = buf.find(b"\r\n\r\n", rest)
            if hdr_end < 0:
                return buf[i:], got
            header = buf[rest:hdr_end].decode("latin-1", "ignore")
            body = hdr_end + 4
            mlen = re.search(r"Content-Length:\s*(\d+)", header, re.I)
            if not mlen:
                buf = buf[body:]
                continue
            n = int(mlen.group(1))
            if len(buf) < body + n:
                # 数据还没收全，等下一轮
                return buf[i:], got
            frame = buf[body:body + n]
            buf = buf[body + n:]
            if frame[:3] == b"\xff\xd8\xff":
                got = True
                self.frames_total += 1
                self.last_frame_at = time.time()
                self._emit(frame)

    def _emit(self, frame: bytes):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(frame)
            except queue.Full:
                # 队列满 = 这个观看者网络慢。丢掉旧帧保最新，绝不能让帧堆积成延迟
                try:
                    q.get_nowait()
                    q.put_nowait(frame)
                except queue.Empty:
                    pass
            except Exception:
                pass

    def _wake_all(self):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(None)   # None = 哨兵，表示上游结束
            except Exception:
                pass

    # ---------- 对外状态 ----------
    def get_status(self) -> dict:
        with self._lock:
            n = len(self._subs)
        age = round(time.time() - self.last_frame_at, 2) if self.last_frame_at else None
        url = self._board_url_fixed or self.board_url_resolved or self._latest_ip()
        pumping = bool(self._pump and self._pump.is_alive())
        return {
            "state": self.state,
            "viewers": n,
            "frames_total": self.frames_total,
            "last_frame_age": age,
            "board_stream": f"http://{url}:81/stream" if url and not url.startswith("http") else url,
            "error": self.last_error,
            "upstream_running": pumping,
        }
