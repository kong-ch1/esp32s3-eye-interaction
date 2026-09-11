#!/usr/bin/env python3
"""AI 交互课 第1周 —— 最小接收与存储服务

只用 Python 标准库实现，不需要 pip install 任何东西，换台电脑拷过去就能跑。

接口:
    POST /api/data          板端上传一条传感记录
    GET  /api/latest        最新一条 + 距今年龄(秒) + 是否过期
    GET  /api/history       最近 N 条记录
    GET  /api/devices       出现过的设备列表
    GET  /api/camera        摄像头 MJPEG 视频流（服务器中转，详见 camera_relay.py）
    GET  /api/camera/status 中继状态（有几个观看者、帧龄、错误信息）
    GET  /                  Web 页面

运行:
    python server/server.py --port 8000
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from camera_relay import CameraRelay, CLIENT_BOUNDARY, FRAME_BOUNDARY

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "readings.db")

# 超过这个秒数没收到新数据，就判定为"未更新"
STALE_SECONDS = 5.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT    NOT NULL,
    seq       INTEGER DEFAULT 0,
    ax REAL, ay REAL, az REAL,
    gx REAL, gy REAL, gz REAL,
    device_ms INTEGER DEFAULT 0,
    server_ms INTEGER NOT NULL,
    src_ip    TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_device ON readings(device_id, id DESC);
"""


def get_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


CONN = get_conn()
LOCK = __import__("threading").Lock()


def latest_device_ip() -> str:
    """最近一条上报来自哪个 IP —— 摄像头中继靠它自动找到板子，不必写死 IP。"""
    with LOCK:
        row = CONN.execute(
            "SELECT src_ip FROM readings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row["src_ip"] if row and row["src_ip"] else ""


RELAY = CameraRelay(latest_ip_fn=latest_device_ip)


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["server_time"] = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(d["server_ms"] / 1000)
    )
    d["age_seconds"] = round((time.time() * 1000 - d["server_ms"]) / 1000, 2)
    d["stale"] = d["age_seconds"] > STALE_SECONDS
    return d


class Handler(BaseHTTPRequestHandler):
    server_version = "Week1Server/1.0"

    def log_message(self, fmt, *args):
        # 只打印 API 请求，静态资源太吵
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)

    # ---------- 工具 ----------
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ---------- GET ----------
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        qs = parse_qs(url.query)

        if path == "/api/latest":
            return self.api_latest(qs)
        if path == "/api/history":
            return self.api_history(qs)
        if path == "/api/devices":
            return self.api_devices()
        if path == "/api/camera":
            return self.api_camera()
        if path == "/api/camera/status":
            return self._send_json(200, RELAY.get_status())
        if path in ("/", "/index.html"):
            return self.serve_file(os.path.join(WEB_DIR, "index.html"))
        return self._send_json(404, {"error": "not found", "path": path})

    def api_latest(self, qs):
        device = (qs.get("device_id") or [None])[0]
        sql = "SELECT * FROM readings"
        args: list = []
        if device:
            sql += " WHERE device_id = ?"
            args.append(device)
        sql += " ORDER BY id DESC LIMIT 1"
        with LOCK:
            row = CONN.execute(sql, args).fetchone()
        if row is None:
            return self._send_json(200, {"has_data": False,
                                         "stale_seconds": STALE_SECONDS})
        return self._send_json(200, {"has_data": True,
                                     "stale_seconds": STALE_SECONDS,
                                     "record": row_to_dict(row)})

    def api_history(self, qs):
        device = (qs.get("device_id") or [None])[0]
        try:
            limit = int((qs.get("limit") or [20])[0])
        except ValueError:
            limit = 20
        limit = max(1, min(limit, 500))
        sql = "SELECT * FROM readings"
        args: list = []
        if device:
            sql += " WHERE device_id = ?"
            args.append(device)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with LOCK:
            rows = CONN.execute(sql, args).fetchall()
        return self._send_json(200, {"count": len(rows),
                                     "records": [row_to_dict(r) for r in rows]})

    def api_devices(self):
        with LOCK:
            rows = CONN.execute(
                "SELECT device_id, COUNT(*) AS n, MAX(server_ms) AS last_ms"
                " FROM readings GROUP BY device_id ORDER BY last_ms DESC"
            ).fetchall()
        now = time.time() * 1000
        return self._send_json(200, {"devices": [
            {"device_id": r["device_id"], "count": r["n"],
             "age_seconds": round((now - r["last_ms"]) / 1000, 2)}
            for r in rows]})

    # ---------- 摄像头视频流（服务器中转） ----------
    def _write_frame(self, frame: bytes):
        self.wfile.write(CLIENT_BOUNDARY + b"\r\n")
        self.wfile.write(b"Content-Type: image/jpeg\r\n")
        self.wfile.write(("Content-Length: %d\r\n\r\n" % len(frame)).encode("ascii"))
        self.wfile.write(frame)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def api_camera(self):
        """把板子的 MJPEG 流转发给浏览器。
        注意：服务器与板子之间始终只有 1 路连接，浏览器再多也不会压垮板子。"""
        q = RELAY.subscribe()
        first = queue.Empty
        try:
            first = q.get(timeout=8.0)
        except queue.Empty:
            pass
        except Exception:
            RELAY.unsubscribe(q)
            return
        if first is None or first is queue.Empty:
            RELAY.unsubscribe(q)
            st = RELAY.get_status()
            return self._send_json(503, {"error": "摄像头画面暂不可用", "status": st})
        try:
            self.send_response(200)
            self.send_header(
                "Content-Type",
                f"multipart/x-mixed-replace;boundary={FRAME_BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self._write_frame(first)
            while True:
                try:
                    frame = q.get(timeout=3.0)
                except queue.Empty:
                    continue          # 上游还没帧，继续等，别断开
                if frame is None:     # 哨兵：上游结束，收摊让前端重连
                    break
                self._write_frame(frame)
        except (BrokenPipeError, ConnectionResetError):
            pass                      # 浏览器关页面了，正常情况
        except Exception:
            pass
        finally:
            RELAY.unsubscribe(q)

    def serve_file(self, filepath):
        try:
            with open(filepath, "rb") as f:
                body = f.read()
        except OSError:
            return self._send_json(404, {"error": "index.html not found"})
        self._send(200, body, "text/html; charset=utf-8")

    # ---------- POST ----------
    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/api/data":
            return self._send_json(404, {"error": "not found"})

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return self._send_json(400, {"error": "empty body"})

        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return self._send_json(400, {"error": "bad json", "detail": str(e)})

        device_id = str(data.get("device_id") or "").strip()
        if not device_id:
            return self._send_json(400, {"error": "device_id required"})

        def f(key):
            try:
                return float(data.get(key, 0.0))
            except (TypeError, ValueError):
                return 0.0

        try:
            seq = int(data.get("seq", 0))
        except (TypeError, ValueError):
            seq = 0
        try:
            device_ms = int(data.get("ts", 0))
        except (TypeError, ValueError):
            device_ms = 0

        server_ms = int(time.time() * 1000)
        with LOCK:
            cur = CONN.execute(
                "INSERT INTO readings"
                " (device_id, seq, ax, ay, az, gx, gy, gz, device_ms, server_ms, src_ip)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (device_id, seq, f("ax"), f("ay"), f("az"),
                 f("gx"), f("gy"), f("gz"), device_ms, server_ms,
                 self.client_address[0]),
            )
            CONN.commit()
            new_id = cur.lastrowid
        return self._send_json(200, {"ok": True, "id": new_id,
                                     "server_ms": server_ms})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()


def main():
    global STALE_SECONDS
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--stale", type=float, default=STALE_SECONDS,
                    help="判定未更新的秒数阈值")
    ap.add_argument("--camera-url", default="",
                    help="板子视频流地址，留空则自动从上报表里找最近的设备 IP")
    args = ap.parse_args()

    STALE_SECONDS = args.stale
    if args.camera_url:
        RELAY.set_board_url(args.camera_url)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[week1] 服务已启动: http://127.0.0.1:{args.port}")
    print(f"[week1] 数据库: {DB_PATH}")
    print(f"[week1] 未更新阈值: {STALE_SECONDS} 秒")
    print(f"[week1] 摄像头中转: http://127.0.0.1:{args.port}/api/camera"
          f"（无需知道板子 IP）")
    print("[week1] Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[week1] 已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
