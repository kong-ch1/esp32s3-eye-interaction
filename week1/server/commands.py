#!/usr/bin/env python3
"""第2周 —— 下行指令与回执状态机。

背景：第1周只有「板子 → 服务器」这一条**上行**通道，板子是纯发布者：
它自己决定什么时候采、采什么。网页刷新只能看历史，**无法让设备再采一次**。

本模块补上反方向的第二条通道，并把它做成一条**可核查的证据链**：

    网页点击         服务器受理        板子取走          板子执行完
      │                 │                │                 │
      ▼                 ▼                ▼                 ▼
   POST /api/command → issued → delivered → received → done
                        │         │           │
                        └─────────┴───────────┴──► timeout（附超时发生在哪一阶段）

关键设计
--------
1. **指令不是"立即送达"，而是"搭车"送达。**
   板子的 HTTP 客户端只做 POST、不监听端口，所以不可能被"推"。
   服务器把待执行指令**缓存**起来，等板子下次上报（1Hz）时，
   在 `/api/data` 的**响应体**里捎带回去。板子拿到后立刻执行并回报。
   → 不需要给板子开监听端口，也不需要引入 MQTT/WebSocket，改动量最小。

2. **状态只能单向推进，重复回报不倒退。**
   delivered 与 received 都由板子上报驱动，网络抖动可能导致重复，
   所以每次推进都带条件（只在当前状态更靠前时才更新）。

3. **超时分阶段记录，而不是只记一个 timeout。**
   "板子一直没来取"和"取了但没执行完"是完全不同的故障，
   所以额外记录 `timeout_stage`，验收时能一眼看出问题出在哪一段。

4. **每条指令都留服务器侧时间戳**，与板子发来的数据用同一个 `request_id` 关联，
   这样"这一次采集确实是被这次请求触发的"就是可证的，而不是靠嘴说。
"""

from __future__ import annotations

import json
import sqlite3
import time

# 状态机：只允许按这个顺序前进
ORDER = ["issued", "delivered", "received", "done"]

# 默认超时（秒）。分了两个阶段：
#   ACCEPT_TIMEOUT —— 从受理到板子把指令取走（含板子重启/断网的时间）
#   EXEC_TIMEOUT   —— 板子取走之后到执行完成
ACCEPT_TIMEOUT_S = 30.0
EXEC_TIMEOUT_S = 30.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    id               TEXT PRIMARY KEY,
    device_id        TEXT NOT NULL,
    type             TEXT NOT NULL,
    params           TEXT,
    status           TEXT NOT NULL,
    -- 证据链上的四个时间点（毫秒）
    created_ms       INTEGER NOT NULL,
    delivered_ms     INTEGER,
    received_ms      INTEGER,
    done_ms          INTEGER,
    timeout_ms       INTEGER,
    timeout_stage    TEXT,
    -- 截止时间
    accept_deadline_ms INTEGER NOT NULL,
    exec_deadline_ms   INTEGER,
    exec_timeout_ms    INTEGER,
    -- 执行结果
    sample_count     INTEGER,
    first_reading_id INTEGER,
    last_reading_id  INTEGER,
    note             TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_dev ON commands(device_id, created_ms DESC);
CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


EXTRA_COLUMNS = {
    "exec_timeout_ms": "INTEGER",
    "failed_ms": "INTEGER",       # 板子回报「执行失败」的时刻（与 timeout 分开记）
}


class CommandStore:
    """下行指令的存储与状态机。所有方法都在外部传入的锁内执行。"""

    def __init__(self, conn: sqlite3.Connection, lock, clock=_now_ms):
        self._conn = conn
        self._lock = lock
        self._clock = clock
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self):
        """幂等补列：老库里的 commands 表不会自动长出新字段。

        与 readings 表同样的做法 —— 加字段不能让历史指令丢失。
        """
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(commands)")}
        for name, typ in EXTRA_COLUMNS.items():
            if name not in cols:
                self._conn.execute(
                    f"ALTER TABLE commands ADD COLUMN {name} {typ}")

    # ---------------------------------------------------------------- 内部
    def _row(self, cid: str):
        cur = self._conn.execute("SELECT * FROM commands WHERE id = ?", (cid,))
        return cur.fetchone()

    def _next_id(self, day: str) -> str:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM commands WHERE id LIKE ?", (f"CMD-{day}-%",))
        n = cur.fetchone()[0] + 1
        return f"CMD-{day}-{n:04d}"

    # ---------------------------------------------------------------- 写入
    def issue(self, device_id: str, ctype: str = "recollect",
              params: dict | None = None,
              accept_timeout_s: float = ACCEPT_TIMEOUT_S,
              exec_timeout_s: float = EXEC_TIMEOUT_S) -> dict:
        """受理一条指令 —— 这就是「服务器受理」这一步。"""
        now = self._clock()
        day = time.strftime("%Y%m%d", time.localtime(now / 1000))
        with self._lock:
            cid = self._next_id(day)
            self._conn.execute(
                "INSERT INTO commands"
                " (id, device_id, type, params, status, created_ms,"
                "  accept_deadline_ms, exec_timeout_ms) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (cid, device_id, ctype, json.dumps(params or {}, ensure_ascii=False),
                 "issued", now, now + int(accept_timeout_s * 1000),
                 int(exec_timeout_s * 1000)))
            self._conn.commit()
        return self.get(cid)

    def active_for(self, device_id: str, ctype: str | None = None) -> dict | None:
        """该设备是否还有**未走完**的同类指令（issued/delivered/received）。

        用途：拦住重复点击。没有这道闸门时，连点 5 次会排队 5 条，
        板子 1 秒只能取走 1 条，于是最后一条要在几十秒后才执行 ——
        那时用户早就走开了，而前面几条还可能撞上 accept 超时，
        页面上呈现成一片「完成 / 超时」混杂，看起来像 bug。

        真正拦不住的场景是**两个浏览器 / 两台手机同时点**：界面上的
        disabled 只作用于自己那个标签页，所以闸门必须放在服务器。
        """
        with self._lock:
            sql = ("SELECT * FROM commands WHERE device_id = ?"
                   " AND status IN ('issued','delivered','received')")
            args: list = [device_id]
            if ctype:
                sql += " AND type = ?"
                args.append(ctype)
            sql += " ORDER BY created_ms ASC LIMIT 1"
            row = self._conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def pending_for(self, device_id: str) -> dict | None:
        """取出该设备最老的一条「尚未送达」的指令，并标记为 delivered。

        标记动作就是「服务器已把它下发出去」的证据；重复调用不会重复取同一条。
        """
        now = self._clock()
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM commands WHERE device_id = ? AND status = 'issued'"
                " ORDER BY created_ms ASC LIMIT 1", (device_id,))
            row = cur.fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE commands SET status='delivered', delivered_ms=?,"
                " exec_deadline_ms=? WHERE id=? AND status='issued'",
                (now, now + (row["exec_timeout_ms"] or int(EXEC_TIMEOUT_S * 1000)),
                 row["id"]))
            self._conn.commit()
        return self.get(row["id"])

    def _advance(self, cid: str, target: str, **fields) -> dict | None:
        """把状态推进到 target，但绝不倒退（网络抖动会带来重复上报）。"""
        with self._lock:
            row = self._row(cid)
            if row is None:
                return None
            cur = row["status"]
            if cur in ("done", "timeout", "failed"):
                return dict(row)            # 终态不再改动
            if ORDER.index(target) <= ORDER.index(cur):
                # 已经到过这个状态了：只补齐可能缺的字段，不改状态
                if fields:
                    sets = ", ".join(f"{k}=?" for k in fields)
                    self._conn.execute(
                        f"UPDATE commands SET {sets} WHERE id=?",
                        (*fields.values(), cid))
                    self._conn.commit()
                return dict(self._row(cid))
            sets = ", ".join(f"{k}=?" for k in fields)
            sql = f"UPDATE commands SET status=?, {sets} WHERE id=?" if fields \
                else "UPDATE commands SET status=? WHERE id=?"
            args = (target, *fields.values(), cid) if fields else (target, cid)
            self._conn.execute(sql, args)
            self._conn.commit()
        return self.get(cid)

    def mark_received(self, cid: str, now_ms: int | None = None) -> dict | None:
        """「设备接收」—— 板子明确回报它拿到了这条指令。"""
        return self._advance(cid, "received",
                             received_ms=now_ms or self._clock())

    def mark_done(self, cid: str, sample_count: int | None = None,
                  first_id: int | None = None, last_id: int | None = None,
                  note: str | None = None,
                  now_ms: int | None = None) -> dict | None:
        """「完成」—— 板子回报执行完毕，且新采集的数据已入库。

        `note` 用来记「完成了但不完全干净」的情况（例如 8 条里只送达 6 条）。
        完成就是完成，不降级成失败；但代价必须写下来，不能悄悄吞掉。
        """
        return self._advance(cid, "done", done_ms=now_ms or self._clock(),
                             sample_count=sample_count,
                             first_reading_id=first_id,
                             last_reading_id=last_id,
                             note=note)

    def mark_failed(self, cid: str, reason: str) -> dict | None:
        """「失败」—— 板子**收到了**指令，但没能执行成功。

        与 timeout 的区别是这张表里最要紧的一条：
            timeout —— 服务器什么回音都没听到（板子掉线 / 卡死 / 网络断）
            failed  —— 板子明确回报"我试了，没干成"（并带上原因）
        把两者混成一个"没成功"，排查时就分不清该去看网络还是看设备。
        """
        with self._lock:
            self._conn.execute(
                "UPDATE commands SET status='failed', note=?, failed_ms=? "
                "WHERE id=? AND status NOT IN ('done','timeout','failed')",
                (reason, self._clock(), cid))
            self._conn.commit()
        return self.get(cid)

    # ---------------------------------------------------------------- 超时
    def sweep_timeouts(self) -> list[dict]:
        """扫描超时指令。由后台线程周期调用。

        `timeout_stage` 明确记录卡在哪一步：
          accept —— 指令发出去后，板子一直没来取（板子掉线/重启/没上报）
          exec   —— 板子取走了，但没执行完（执行中崩了/网络断了）
        """
        now = self._clock()
        fired = []
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM commands WHERE status IN ('issued','delivered','received')")
            rows = cur.fetchall()
            for row in rows:
                stage = None
                if row["status"] == "issued" and now > row["accept_deadline_ms"]:
                    stage = "accept"
                elif row["status"] in ("delivered", "received") and \
                        row["exec_deadline_ms"] and now > row["exec_deadline_ms"]:
                    stage = "exec"
                if stage:
                    self._conn.execute(
                        "UPDATE commands SET status='timeout', timeout_ms=?,"
                        " timeout_stage=? WHERE id=?", (now, stage, row["id"]))
                    fired.append({"id": row["id"], "stage": stage})
            if fired:
                self._conn.commit()
        return fired

    # ---------------------------------------------------------------- 读取
    def get(self, cid: str) -> dict | None:
        with self._lock:
            row = self._row(cid)
        return dict(row) if row else None

    def recent(self, device_id: str | None = None, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        sql = "SELECT * FROM commands"
        args: list = []
        if device_id:
            sql += " WHERE device_id = ?"
            args.append(device_id)
        sql += " ORDER BY created_ms DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def to_public(cmd: dict) -> dict:
        """给网页用的视图：把毫秒时间戳换算成可读的阶段耗时。"""
        if not cmd:
            return {}
        out = dict(cmd)
        try:
            out["params"] = json.loads(cmd.get("params") or "{}")
        except (TypeError, ValueError):
            out["params"] = {}

        def ms_to_iso(ms):
            if not ms:
                return None
            return time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(ms / 1000)) + \
                f".{ms % 1000:03d}"

        out["created_at"] = ms_to_iso(cmd.get("created_ms"))
        out["delivered_at"] = ms_to_iso(cmd.get("delivered_ms"))
        out["received_at"] = ms_to_iso(cmd.get("received_ms"))
        out["done_at"] = ms_to_iso(cmd.get("done_ms"))
        out["timeout_at"] = ms_to_iso(cmd.get("timeout_ms"))
        out["failed_at"] = ms_to_iso(cmd.get("failed_ms"))

        # 阶段耗时，单位毫秒 —— 验收时直接看这几个数
        leg = {}
        c, d = cmd.get("created_ms"), cmd.get("delivered_ms")
        r, dn = cmd.get("received_ms"), cmd.get("done_ms")
        if c and d:
            leg["issue_to_deliver_ms"] = d - c
        if d and r:
            leg["deliver_to_receive_ms"] = r - d
        if r and dn:
            leg["receive_to_done_ms"] = dn - r
        if c:
            # 终态各有各的结束时刻：成功看 done，失败看 failed，超时看 timeout。
            # 漏掉 failed 的话，"失败"的指令会显示没有总耗时，看着像半条记录。
            end = dn or cmd.get("failed_ms") or cmd.get("timeout_ms")
            if end:
                leg["total_ms"] = end - c
        out["legs_ms"] = leg
        return out
