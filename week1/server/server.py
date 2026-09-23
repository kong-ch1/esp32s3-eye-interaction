#!/usr/bin/env python3
"""AI 交互课 第1周 —— 最小接收与存储服务

只用 Python 标准库实现，不需要 pip install 任何东西，换台电脑拷过去就能跑。

接口:
    POST /api/data          板端上传一条传感记录
    GET  /api/latest        最新一条 + 距今年龄(秒) + 是否过期
    GET  /api/history       最近 N 条记录
    GET  /api/devices       出现过的设备列表
    GET  /api/export.csv    导出历史记录为 CSV 文件（浏览器下载）
                            可选参数：limit=条数 / hours=最近几小时 / device_id=设备
    GET  /api/camera        摄像头 MJPEG 视频流（服务器中转，详见 camera_relay.py）
    GET  /api/camera/status 中继状态（有几个观看者、帧龄、错误信息）
    GET  /                  Web 页面

第2周新增（下行指令与回执，详见 commands.py）:
    POST /api/command       下发一条「重新采集」指令，返回 request_id
    GET  /api/command/<id>  查这条指令的状态与各阶段耗时（证据链）
    GET  /api/commands      最近的指令列表
    —— 板子侧不需要新增端口：待执行指令随 /api/data 的**响应体**捎带下发

运行:
    python server/server.py --port 8000
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import queue
import socket
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from camera_relay import CameraRelay, CLIENT_BOUNDARY, FRAME_BOUNDARY
from commands import CommandStore

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "readings.db")

# 超过这个秒数没收到新数据，就判定为"未更新"
STALE_SECONDS = 5.0

# 单次 CSV 导出最多多少条（板子 1 秒 1 条，20 万条约等于跑满 2.3 天，够用）
EXPORT_LIMIT = 200000

# ---- 数据库容量上限 ----
# 板子一秒一条，一天就是 8.6 万条，几天下来库会一直涨。
# 超过 MAX_ROWS 就自动删掉**最早**的那批，只保留最近的记录，
# 这样长时间挂机也不会把磁盘写满。想保留更久就调大这个数。
MAX_ROWS = 200000

# 裁剪检查的节流：每插入这么多条、或每过这么多秒才去 COUNT 一次。
# 每条上报都数一遍总数太浪费，板子 1 秒 1 条时不值得。
TRIM_CHECK_EVERY = 500
TRIM_CHECK_SECONDS = 60.0

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

# 后来新增的列：板子自身状态（芯片温度 / 剩余堆内存 / 历史最低堆）
# 老数据库是用 CREATE TABLE IF NOT EXISTS 建的，不会自动补列，
# 所以这里做一次幂等的 ALTER TABLE 迁移，老数据一行不丢。
EXTRA_COLUMNS = {
    "temp_c": "REAL",
    "free_heap": "INTEGER",
    "min_free_heap": "INTEGER",
    "total_heap": "INTEGER",   # 堆总大小，用于算"已用多少"
    "rssi": "INTEGER",         # WiFi 信号强度 dBm，负值，越接近 0 越好
    # ---- 第2周新增：把「这一条是被哪次请求触发的」也存下来 ----
    "request_id": "TEXT",      # 触发本次采集的批次 id（常规定时上报为 NULL）
                               #   CMD-… = 网页下发指令；BTN-… = 板载实体按键
    "trigger": "TEXT",         # 'timer' 定时上报 / 'command' 指令触发 / 'button' 实体按键
    "cmd_state": "TEXT",       # 板子回报的回执阶段：'received' / 'done'（仅指令）
    # ---- 第3周新增：实体按键 ----
    "button": "TEXT",          # 按的是哪个键：MENU / PLAY / UP+ / DN-（非按键触发为 NULL）
    "press_delay_ms": "INTEGER",  # 按下 → 本条采样组装完成 的毫秒数（仅按键批次的首条）
}


def migrate(conn):
    """给已存在的老库补上新列。幂等：重复执行无副作用。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(readings)")}
    for name, typ in EXTRA_COLUMNS.items():
        if name not in cols:
            conn.execute("ALTER TABLE readings ADD COLUMN %s %s" % (name, typ))
    conn.commit()


_trim_counter = 0
_trim_last_ts = 0.0


def maybe_trim(conn: sqlite3.Connection, force: bool = False):
    """记录数超过 MAX_ROWS 时，删掉最早的几条，把库压回上限以内。

    平时靠计数器/时间节流，不是每来一条就统计一次总数；
    force=True 用于启动时立刻检查一次（上次运行可能已经超了）。
    """
    global _trim_counter, _trim_last_ts
    _trim_counter += 1
    now = time.time()
    if not force and (_trim_counter < TRIM_CHECK_EVERY
                      and now - _trim_last_ts < TRIM_CHECK_SECONDS):
        return
    _trim_counter = 0
    _trim_last_ts = now

    total = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    if total <= MAX_ROWS:
        return
    over = total - MAX_ROWS
    # 按 id 升序删最前面的 over 条（id 是自增主键，升序即最早写入的）
    conn.execute(
        "DELETE FROM readings WHERE id IN"
        " (SELECT id FROM readings ORDER BY id ASC LIMIT ?)", (over,))
    conn.commit()
    print("[trim] 记录 %d 条，超过上限 %d，已删除最早的 %d 条"
          % (total, MAX_ROWS, over))


def get_conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


CONN = get_conn()
LOCK = threading.Lock()

# ---- 每个设备的运行时状态（内存，不落库；板子重启后自然复位为"未暂停"）----
# 为什么需要它：任务卡要求"暂停周期上报、保持命令通道"，用来证明点采集后
# 产生的新数据**必然来自指令**，而不是页面翻出的旧值。
# 板子暂停后不再上报传感数据，但仍每 1 秒发一次 /api/poll 心跳 ——
# 心跳本身不写 readings，却继续把待执行指令捎带回去，所以通道不断。
DEVICE_STATE: dict[str, dict] = {}

# 允许下发的指令类型。
#   recollect —— 让设备立刻采一批新数据（第2周主任务）
#   pause / resume —— 暂停/恢复**周期上报**（任务卡要求：暂停周期上报、保持命令通道）
COMMAND_TYPES = {"recollect", "pause", "resume"}


def device_state(device_id: str) -> dict:
    st = DEVICE_STATE.get(device_id)
    if st is None:
        st = {"paused": False, "last_seen_ms": 0, "last_poll_ms": 0,
              "last_reading_ms": 0, "last_kind": None}
        DEVICE_STATE[device_id] = st
    return st

# 第2周：下行指令 + 回执状态机（实现见 commands.py）
COMMANDS = CommandStore(CONN, LOCK)


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
        if path == "/api/export.csv":
            return self.api_export(qs)
        if path == "/api/camera":
            return self.api_camera()
        if path == "/api/camera/status":
            return self._send_json(200, RELAY.get_status())
        if path == "/api/commands":
            return self.api_commands(qs)
        if path == "/api/buttons":
            return self.api_buttons(qs)
        if path.startswith("/api/command/"):
            cid = path[len("/api/command/"):]
            cmd = COMMANDS.get(cid)
            if not cmd:
                return self._send_json(404, {"error": "no such command", "id": cid})
            return self._send_json(200, CommandStore.to_public(cmd))
        if path in ("/", "/index.html"):
            return self.serve_file(os.path.join(WEB_DIR, "index.html"))
        return self._send_json(404, {"error": "not found", "path": path})

    def api_commands(self, qs):
        """最近的指令列表 —— 验收时用来看整条证据链。"""
        device = (qs.get("device_id") or [None])[0]
        try:
            limit = int((qs.get("limit") or [20])[0])
        except ValueError:
            limit = 20
        rows = COMMANDS.recent(device_id=device, limit=limit)
        return self._send_json(200, {
            "count": len(rows),
            "commands": [CommandStore.to_public(r) for r in rows],
        })

    def api_buttons(self, qs):
        """最近的**实体按键事件** —— 按事件 id 聚合成一条，一次按键算一条。

        为什么单开一个接口，而不是塞进 /api/commands：
        这是两条方向完全不同的路径 ——
          · 网页下发指令：服务器受理 → 下发 → 设备接收 → 完成（四段，都有服务器侧证据）
          · 板载实体按键：**没有下行通道**，所以没有前两段；起点就是设备自己
        硬塞进同一张表会让"阶段"字段一半为空，反而看不清。
        """
        device = (qs.get("device_id") or [None])[0]
        try:
            limit = int((qs.get("limit") or [10])[0])
        except ValueError:
            limit = 10
        limit = max(1, min(limit, 100))

        sql = ("SELECT request_id, button,"
               "       COUNT(*)       AS samples,"
               "       MIN(id)        AS first_id,"
               "       MAX(id)        AS last_id,"
               "       MIN(server_ms) AS first_ms,"
               "       MAX(server_ms) AS last_ms,"
               "       AVG(press_delay_ms) AS avg_press,"
               "       MAX(press_delay_ms) AS max_press"
               "  FROM readings"
               " WHERE trigger = 'button' AND request_id IS NOT NULL")
        args: list = []
        if device:
            sql += " AND device_id = ?"
            args.append(device)
        sql += " GROUP BY request_id ORDER BY last_ms DESC LIMIT ?"
        args.append(limit)

        with LOCK:
            rows = CONN.execute(sql, args).fetchall()

        now = time.time() * 1000
        events = [{
            "event_id":   r["request_id"],
            "button":     r["button"],
            "samples":    r["samples"],
            "first_id":   r["first_id"],
            "last_id":    r["last_id"],
            "first_time": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(r["first_ms"] / 1000)),
            "span_ms":    r["last_ms"] - r["first_ms"],   # 首条入库→末条入库的跨度
            "press_delay_ms": (round(r["avg_press"]) if r["avg_press"] is not None
                               else None),                 # 按下 → 首条采样组装完成
            "age_seconds": round((now - r["last_ms"]) / 1000, 2),
        } for r in rows]
        return self._send_json(200, {"count": len(events), "events": events})

    def api_issue_command(self):
        """POST /api/command —— 网页点「重新采集」走这里。

        注意与「刷新页面」的区别：刷新只是 GET 历史记录，一条新数据都不会产生；
        这里会在服务器侧**受理**一条指令，并等板子真的执行完。
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = {}
        if length > 0:
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8")) or {}
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                return self._send_json(400, {"error": "bad json", "detail": str(e)})

        device_id = str(body.get("device_id") or "").strip()
        if not device_id:
            return self._send_json(400, {"error": "device_id required"})
        ctype = str(body.get("type") or "recollect").strip()
        # 白名单：拼错了要当场 400，而不是受理一条板子永远不认识的指令、
        # 白等 30 秒超时。宁可在这里失败，也不要在超时后才发现是打错字。
        if ctype not in COMMAND_TYPES:
            return self._send_json(400, {
                "error": "unknown command type",
                "type": ctype,
                "allowed": sorted(COMMAND_TYPES),
            })

        # 采集类指令才需要采集参数；暂停/恢复是控制类，带参数没有意义，
        # 给了反而会让验收时误以为它也会产生新观测。
        if ctype == "recollect":
            params = {}
            for k in ("samples", "interval_ms"):
                if body.get(k) is not None:
                    try:
                        params[k] = int(body[k])
                    except (TypeError, ValueError):
                        pass
            params.setdefault("samples", 10)
            params.setdefault("interval_ms", 100)
            params["samples"] = max(1, min(params["samples"], 100))
            params["interval_ms"] = max(20, min(params["interval_ms"], 1000))
        else:
            params = {}

        cmd = COMMANDS.issue(device_id, ctype, params)
        print(f"[cmd] 受理 {cmd['id']} device={device_id} type={ctype} "
              f"params={params}")
        return self._send_json(202, {
            "ok": True,
            "request_id": cmd["id"],
            "status": cmd["status"],
            "params": params,
            "accept_timeout_s": round(
                (cmd["accept_deadline_ms"] - cmd["created_ms"]) / 1000, 1),
            "note": "已受理。板子每 1 秒上报一次，指令会随下一次上报的响应下发。",
            "detail_url": f"/api/command/{cmd['id']}",
        })

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

        # 把设备的运行模式一起带出来。少了它网页只有一个"数据年龄"可用，
        # 会把"我们主动让设备别上报"错判成"设备掉线了" —— 那是冤假错案。
        rec = row_to_dict(row)
        ds = DEVICE_STATE.get(rec["device_id"]) or {}
        now = time.time() * 1000
        return self._send_json(200, {
            "has_data": True,
            "stale_seconds": STALE_SECONDS,
            "record": rec,
            "paused": bool(ds.get("paused")),
            "device_seen_seconds": (round((now - ds["last_seen_ms"]) / 1000, 2)
                                    if ds.get("last_seen_ms") else None),
            "last_request_kind": ds.get("last_kind"),
        })

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
        with LOCK:
            total = CONN.execute("SELECT COUNT(*) FROM readings").fetchone()[0]

        devices = []
        for r in rows:
            ds = DEVICE_STATE.get(r["device_id"]) or {}
            devices.append({
                "device_id": r["device_id"],
                "count": r["n"],
                "age_seconds": round((now - r["last_ms"]) / 1000, 2),
                # 暂停期间 readings 不再增长，所以 age_seconds 会一直变大。
                # 若不把 paused 带出来，网页会把它显示成「未更新」——
                # 那是**冤假错案**：设备其实活得好好的，只是被我们要求别上报了。
                "paused": bool(ds.get("paused")),
                "seen_seconds": (round((now - ds["last_seen_ms"]) / 1000, 2)
                                 if ds.get("last_seen_ms") else None),
            })
        return self._send_json(200, {
            "devices": devices,
            "total": total,        # 当前库里总条数（网页用来显示"累计 N 条"）
            "max_rows": MAX_ROWS,  # 上限，超出后自动删除最早的记录
        })

    def api_export(self, qs):
        """把历史记录导出成 CSV 文件，交给浏览器下载。

        几个刻意的选择：
        - 顺序改成**时间正序**（id 升序）：曲线/表格看最新在前更方便，
          但导出的数据一般要拿去做分析，按时间从早到晚更顺。
        - 开头写一个 UTF-8 BOM：不然 Excel 双击打开中文表头会是乱码。
        - 表头用 Content-Disposition: attachment，浏览器直接下载而不是在页面里打开。
        """
        device = (qs.get("device_id") or [None])[0]
        try:
            limit = int((qs.get("limit") or [EXPORT_LIMIT])[0])
        except ValueError:
            limit = EXPORT_LIMIT
        limit = max(1, min(limit, EXPORT_LIMIT))

        # hours=24 表示"只要最近 24 小时的"，不传则不限时间
        hours = None
        try:
            if qs.get("hours"):
                hours = float((qs.get("hours") or [0])[0])
        except ValueError:
            hours = None

        sql = "SELECT * FROM readings WHERE 1=1"
        args: list = []
        if device:
            sql += " AND device_id = ?"
            args.append(device)
        if hours and hours > 0:
            sql += " AND server_ms >= ?"
            args.append(int((time.time() - hours * 3600) * 1000))
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with LOCK:
            rows = CONN.execute(sql, args).fetchall()
        rows = list(reversed(rows))

        cols = ["id", "device_id", "seq", "server_time", "device_ms",
                "ax", "ay", "az", "gx", "gy", "gz",
                "temp_c", "free_heap", "min_free_heap", "total_heap", "rssi",
                "trigger", "request_id", "cmd_state", "button",
                "press_delay_ms", "src_ip"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            d = row_to_dict(r)
            w.writerow(["" if d.get(c) is None else d.get(c) for c in cols])

        body = ("\ufeff" + buf.getvalue()).encode("utf-8")
        fname = "readings_%s.csv" % time.strftime("%Y%m%d_%H%M%S")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"' % fname)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
            # 首帧等久一点：板子刚重启时要先连 WiFi 再起摄像头，8 秒常常不够，
            # 以前会直接 503，页面就陷入「重连→又没帧→再重连」的循环
            first = q.get(timeout=15.0)
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
        if path == "/api/command":
            return self.api_issue_command()
        if path == "/api/poll":
            return self.api_poll()
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

        # 设备状态字段允许为 NULL：板子没上报/读数失败时存 NULL 而不是 0，
        # 这样网页能显示"—"，不会把"没测到"伪装成"温度 0 度"。
        def fnum(key):
            v = data.get(key)
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def fint(key):
            v = data.get(key)
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        try:
            seq = int(data.get("seq", 0))
        except (TypeError, ValueError):
            seq = 0
        try:
            device_ms = int(data.get("ts", 0))
        except (TypeError, ValueError):
            device_ms = 0

        server_ms = int(time.time() * 1000)

        # —— 第2/3周：这一条数据是被谁触发的 ——
        # trigger 把三种来源分开：'timer' 板子自己按时上报 / 'command' 网页点按钮
        # 下发的指令 / 'button' 现场的人按板子上的实体键。
        # 这是把「新采集」与「历史记录」区分开的**数据层证据**（不只是界面话术）。
        request_id = data.get("request_id")
        request_id = str(request_id).strip() if request_id else None
        trigger = str(data.get("trigger") or "timer").strip() or "timer"
        cmd_state = data.get("cmd_state")
        cmd_state = str(cmd_state).strip() if cmd_state else None
        button = data.get("button")
        button = str(button).strip() if button else None

        with LOCK:
            cur = CONN.execute(
                "INSERT INTO readings"
                " (device_id, seq, ax, ay, az, gx, gy, gz, device_ms, server_ms, src_ip,"
                "  temp_c, free_heap, min_free_heap, total_heap, rssi,"
                "  request_id, trigger, cmd_state, button, press_delay_ms)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (device_id, seq, f("ax"), f("ay"), f("az"),
                 f("gx"), f("gy"), f("gz"), device_ms, server_ms,
                 self.client_address[0],
                 fnum("temp_c"), fint("free_heap"), fint("min_free_heap"),
                 fint("total_heap"), fint("rssi"),
                 request_id, trigger, cmd_state, button, fint("press_delay_ms")),
            )
            CONN.commit()
            new_id = cur.lastrowid
            maybe_trim(CONN)      # 超上限就删最早的，控制在锁内避免并发问题

        ds = device_state(device_id)
        ds["last_seen_ms"] = server_ms
        ds["last_reading_ms"] = server_ms
        ds["last_kind"] = "data"

        self._apply_cmd_receipt(trigger, request_id, cmd_state)

        # —— 第2周：把待执行指令捎带回去 ——
        # 板子只做 POST、不监听端口，无法被"推"。这里在响应体里带上指令，
        # 板子拿到后立刻执行。响应体为空时板子本来也不解析，天然向后兼容。
        resp = {"ok": True, "id": new_id, "server_ms": server_ms}
        # 只在**定时上报**这条路径上捎带指令，理由有两条：
        #   · 板子执行指令期间不该再叠加新指令（避免交叉执行）
        #   · 板子只在定时上报那条路径里解析响应体。第3周的实体按键批次也会
        #     POST，但它不解析响应 —— 若把指令搭在它的响应里，板子收不到而
        #     服务器已标记 delivered，这条指令会一直卡到超时。
        if trigger == "timer":
            self._attach_pending(device_id, resp)
        return self._send_json(200, resp)

    def api_poll(self):
        """POST /api/poll —— 暂停周期上报期间的**心跳**，用来保持命令通道。

        任务卡第2周明确要求「暂停周期上报、保持命令通道」，目的很清楚：
        停掉 1Hz 的传感上报之后，库里任何新记录都只可能来自指令 ——
        这才是"证明开发板进行了新采集"最硬的证据，比页面上比数字强得多。

        所以这个端点**不写 readings**，只做三件事：
          1) 记录设备在线，以及它自报的「当前是否暂停」
          2) 收板子对指令的 received / done 回执（pause/resume 也走这条）
          3) 把待执行指令继续搭车捎回去 —— 通道因此不断
        """
        st, code = self._read_json_body()
        if code:
            return code
        device_id = str(st.get("device_id") or "").strip()
        if not device_id:
            return self._send_json(400, {"error": "device_id required"})

        paused = bool(st.get("paused"))
        now = int(time.time() * 1000)

        ds = device_state(device_id)
        ds["paused"] = paused
        ds["last_seen_ms"] = now
        ds["last_poll_ms"] = now
        ds["last_kind"] = "poll"

        request_id = st.get("request_id")
        request_id = str(request_id).strip() if request_id else None
        cmd_state = st.get("cmd_state")
        cmd_state = str(cmd_state).strip() if cmd_state else None

        if not paused:
            # 板子自己恢复了上报（例如重启后状态复位）—— 如实记录，不假装它还在暂停
            print(f"[poll] {device_id} 上报已恢复（paused=false）")
        self._apply_cmd_receipt("command", request_id, cmd_state)

        resp = {"ok": True, "paused": paused, "server_ms": now}
        self._attach_pending(device_id, resp)
        return self._send_json(200, resp)

    # ---------- 第2 / 3 周共用的几段逻辑 ----------
    def _read_json_body(self):
        """读并解析请求体。返回 (body, 错误响应或 None)。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}, None
        raw = self.rfile.read(length)
        try:
            return (json.loads(raw.decode("utf-8")) or {}), None
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return {}, self._send_json(400, {"error": "bad json", "detail": str(e)})

    def _apply_cmd_receipt(self, trigger, request_id, cmd_state):
        """处理板子回报的回执，推进指令状态机。

        只认 trigger=="command"：实体按键批次也会带 request_id（BTN-…），
        但它压根不是指令的产物，不该进指令状态机（否则日志里会凭空多出
        一堆"处理回执失败：没有这条指令"的噪音）。
        """
        if not (trigger == "command" and request_id
                and cmd_state in ("received", "done")):
            return
        try:
            if cmd_state == "received":
                COMMANDS.mark_received(request_id)
                print(f"[cmd] {request_id} 板子已接收（设备侧确认）")
            else:                     # done：顺便把统计结果补全
                with LOCK:
                    row = CONN.execute(
                        "SELECT COUNT(*) n, MIN(id) a, MAX(id) b"
                        " FROM readings WHERE request_id = ?",
                        (request_id,)).fetchone()
                COMMANDS.mark_done(request_id, sample_count=row["n"],
                                   first_id=row["a"], last_id=row["b"])
                print(f"[cmd] {request_id} 执行完成，新采集 {row['n']} 条")
        except Exception as e:        # 回执处理失败不能影响数据入库
            print(f"[cmd] 处理回执失败 {request_id}: {e}")

    def _attach_pending(self, device_id, resp):
        """把待执行指令塞进响应体 —— 板子只 POST 不监听，推不了它，只能搭车。"""
        pend = COMMANDS.pending_for(device_id)
        if not pend:
            return False
        resp["command"] = {
            "request_id": pend["id"],
            "type": pend["type"],
            "params": json.loads(pend["params"] or "{}"),
            "issued_at_ms": pend["created_ms"],
        }
        print(f"[cmd] {pend['id']} 已随响应下发（delivered）")
        return True

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()


def lan_ip() -> str:
    """取本机在局域网里的 IP。

    板子和别的设备（手机、别人的电脑）要靠这个地址访问，只打印 127.0.0.1 没用 ——
    那就是板子自己。这里用 UDP socket"连"一下外部地址，让内核告诉我们
    "这个目的地你会从哪块网卡出去"，**不会真的发包，也不需要联网**。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def main():
    # 必须在用到 MAX_ROWS/STALE_SECONDS 之前声明（它们要给 argparse 当默认值）；
    # 不声明 global 的话，下面只是改了局部变量，命令行参数不会生效。
    global MAX_ROWS, STALE_SECONDS
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--stale", type=float, default=STALE_SECONDS,
                    help="判定未更新的秒数阈值")
    ap.add_argument("--camera-url", default="",
                    help="板子视频流地址，留空则自动从上报表里找最近的设备 IP")
    ap.add_argument("--max-rows", type=int, default=MAX_ROWS,
                    help="数据库最多保留多少条记录，超出后自动删除最早的")
    args = ap.parse_args()

    MAX_ROWS = max(1, args.max_rows)
    STALE_SECONDS = args.stale
    if args.camera_url:
        RELAY.set_board_url(args.camera_url)

    maybe_trim(CONN, force=True)   # 启动时先检查一次：上次跑可能已经超上限了

    # —— 第2周：超时看门狗 ——
    # 指令超时不能只靠"查的时候顺手算"，必须由服务器**主动记下**超时这件事，
    # 否则"超时"就没有服务器侧的证据。这里每秒扫一次并落库。
    def sweep_loop():
        while True:
            try:
                for item in COMMANDS.sweep_timeouts():
                    print(f"[cmd] {item['id']} 超时（阶段={item['stage']}）")
            except Exception as e:
                print(f"[cmd] 超时扫描异常: {e}")
            time.sleep(1.0)

    threading.Thread(target=sweep_loop, daemon=True, name="cmd-timeout").start()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    lan = lan_ip()
    print("[week1] 服务已启动")
    print(f"[week1]   本机访问  : http://127.0.0.1:{args.port}")
    if lan:
        print(f"[week1]   局域网访问: http://{lan}:{args.port}"
              f"   ← 板子靠这个地址上报；同一 WiFi 下的手机/电脑也能用它打开页面")
    else:
        print("[week1]   局域网访问: （没探测到局域网 IP，可能没接网线/没连 WiFi）")
    print(f"[week1] 数据库: {DB_PATH}")
    print(f"[week1] 未更新阈值: {STALE_SECONDS} 秒")
    print(f"[week1] 记录上限: {MAX_ROWS} 条（超出自动删除最早的）")
    print(f"[week1] 摄像头中转: http://127.0.0.1:{args.port}/api/camera"
          f"（无需知道板子 IP）")
    print(f"[week1] 下行指令: POST http://127.0.0.1:{args.port}/api/command"
          f"（状态查询 /api/command/<id>）")
    print("[week1] Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[week1] 已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
