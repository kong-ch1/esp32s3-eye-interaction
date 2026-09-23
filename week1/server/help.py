"""第3周 —— 教学求助事件与回应/取消闭环。

任务卡原文（第3周）要求：
  · 增加「**按键发起教学求助测试消息**」
  · 讲解**本地确认、VPS 接收和查看者回应的区别**
  · 实现**触发—本地反馈—远端显示—回应/取消**
  · **只连接教学接收端，不拨打真实紧急电话**
  · 当堂验证：断开外网后本地仍能确认按键已触发；
    **无远端接收证据时不得显示「对方已收到」**

⚠️ 安全边界（写进代码，不只写在文档里）：
    本模块只服务于**本组教学接收端**。它没有任何对外呼叫能力 ——
    既不打电话、不发短信、不推送任何第三方服务，唯一动作是往本组服务器
    写一条记录、以及在网页上显示出来。界面上必须把这句话显示给使用者看，
    免得有人误以为这是个真实的求助系统。

为什么求助事件要单独一张表、而不是塞进 readings：
  一次按键其实产生**两样东西** ——
    ① 一条「求助」语义的事件（要有人回应，有 open/ack/cancelled 状态）
    ② 几条环境快照（给回应的人一点上下文：板子当时是什么姿态/温度）
  这两样东西的生命周期完全不同：快照写完就完了，事件则有状态、要被操作。
  塞进同一张表会让一半字段永远为空（第2周把实体按键硬塞进指令表时已经吃过
  一次这个亏：实体按键没有下行通道，压根不存在「服务器受理」「指令下发」两段）。

三级反馈是**三件独立的事**，必须各自有各自的证据：
  ① 本地确认  —— 板子上的灯亮了。发生在设备端，断网也有效。
                 服务器能拿到的证据是「设备自报它做了本地反馈」
                 （`local_feedback` 字段）。这是设备的话，不是服务器的观测，
                 所以界面上要标明出处。
  ② VPS 接收  —— 服务器真的收到并落库了。证据是 `created_ms`（服务器时钟）。
  ③ 查看者回应 —— 网页上**有个真人点了按钮**。证据是 `ack_ms` + `ack_by`。
                 **没有这一条，界面就绝不能显示「对方已收到」。**
"""

from __future__ import annotations

import json
import sqlite3
import time

# 事件状态。只有这三种，且 cancelled 是终态。
STATES = ("open", "acknowledged", "cancelled")

# 允许的回应动作
ACTIONS = {"ack", "cancel"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS help_events (
    id              TEXT PRIMARY KEY,      -- 设备生成的事件 id（BTN-<hex>-<KEY>-NNNN）
    device_id       TEXT NOT NULL,
    button          TEXT,                  -- 按的是哪个键
    source          TEXT NOT NULL,         -- 'button' 实体按键 / 'web' 网页（模拟演练）
    simulated       INTEGER NOT NULL,      -- 1 = 模拟/演练数据，界面必须标出来
    local_feedback  TEXT,                  -- 设备自报的本地反馈方式，如 'led'
    snapshot        TEXT,                  -- JSON：当时的环境快照
    press_delay_ms  INTEGER,               -- 按下 → 首条上下文采样组装完成
    created_ms      INTEGER NOT NULL,      -- 服务器收到时刻 = 「VPS 接收」的证据
    state           TEXT NOT NULL,
    ack_ms          INTEGER,               -- 查看者回应时刻 = 「查看者回应」的证据
    ack_by          TEXT,
    cancel_ms       INTEGER,
    cancel_reason   TEXT,
    reply_cmd_id    TEXT                   -- 回应/取消搭车下发到设备所用的指令 id
);
CREATE INDEX IF NOT EXISTS idx_help_dev ON help_events(device_id, created_ms DESC);
CREATE INDEX IF NOT EXISTS idx_help_state ON help_events(state);
"""

EXTRA_COLUMNS = {
    "press_delay_ms": "INTEGER",
    "reply_cmd_id": "TEXT",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


class HelpStore:
    """教学求助事件的存储与状态迁移。所有方法都在外部传入的锁内执行。"""

    def __init__(self, conn: sqlite3.Connection, lock, clock=_now_ms):
        self._conn = conn
        self._lock = lock
        self._clock = clock
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self):
        cols = {r["name"] for r in self._conn.execute(
            "PRAGMA table_info(help_events)")}
        for name, typ in EXTRA_COLUMNS.items():
            if name not in cols:
                self._conn.execute(
                    f"ALTER TABLE help_events ADD COLUMN {name} {typ}")

    def _row(self, eid: str):
        return self._conn.execute(
            "SELECT * FROM help_events WHERE id = ?", (eid,)).fetchone()

    # ---------------------------------------------------------------- 写入
    def record(self, device_id: str, event_id: str | None = None,
               button: str | None = None, local_feedback: str | None = None,
               snapshot: dict | None = None, source: str = "button",
               simulated: bool = False,
               press_delay_ms: int | None = None) -> tuple[dict, bool]:
        """记一条教学求助事件。返回 (事件, 是否是重复上报)。

        幂等 —— 同 event_id 重复上报不再入库。
        理由与第2周终态回执完全相同：设备把消息发出去之后，如果回程断了，
        它分不清服务器到底收到没有（两将军问题），所以设备会重试；
        服务器必须对重试免疫，否则"一条求助"会变成三条。
        """
        now = self._clock()
        with self._lock:
            if event_id:
                old = self._row(event_id)
                if old is not None:
                    return dict(old), True
            eid = event_id or f"HELP-{int(now)}-{_rand4()}"
            self._conn.execute(
                "INSERT INTO help_events"
                " (id, device_id, button, source, simulated, local_feedback,"
                "  snapshot, press_delay_ms, created_ms, state)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (eid, device_id, button, source, 1 if simulated else 0,
                 local_feedback,
                 json.dumps(snapshot or {}, ensure_ascii=False),
                 press_delay_ms, now, "open"))
            self._conn.commit()
            return dict(self._row(eid)), False

    def reply(self, eid: str, action: str, by: str | None = None,
              reason: str | None = None) -> tuple[dict | None, str]:
        """查看者对一条求助做「回应」或「取消」。

        返回 (事件, 结果码)，结果码用于让接口给出准确的 HTTP 状态：
            ok         —— 状态已迁移
            duplicate  —— 同样的动作之前已经做过（幂等，不报错）
            closed     —— 这条已经取消了，不能再改
            notfound   —— 没有这条事件
            bad_action —— 动作不认识
        """
        if action not in ACTIONS:
            return None, "bad_action"
        now = self._clock()
        with self._lock:
            row = self._row(eid)
            if row is None:
                return None, "notfound"
            st = row["state"]

            if action == "ack":
                if st == "cancelled":
                    return dict(row), "closed"
                if st == "acknowledged":
                    return dict(row), "duplicate"
                self._conn.execute(
                    "UPDATE help_events SET state='acknowledged', ack_ms=?,"
                    " ack_by=? WHERE id=? AND state='open'",
                    (now, by, eid))
            else:                                  # cancel
                if st == "cancelled":
                    return dict(row), "duplicate"
                # 已回应也可以取消：现场情况变了（人到了、问题自己好了），
                # 允许撤回比强迫走完更贴近真实使用
                self._conn.execute(
                    "UPDATE help_events SET state='cancelled', cancel_ms=?,"
                    " cancel_reason=? WHERE id=? AND state IN ('open','acknowledged')",
                    (now, reason, eid))
            self._conn.commit()
            return dict(self._row(eid)), "ok"

    def set_reply_cmd(self, eid: str, cmd_id: str):
        """记下「回应/取消已搭车下发到设备」用的是哪条指令，便于回溯。"""
        with self._lock:
            self._conn.execute(
                "UPDATE help_events SET reply_cmd_id=? WHERE id=?", (cmd_id, eid))
            self._conn.commit()

    # ---------------------------------------------------------------- 查询
    def get(self, eid: str) -> dict | None:
        with self._lock:
            row = self._row(eid)
        return dict(row) if row else None

    def recent(self, device_id: str | None = None, state: str | None = None,
               limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        sql = "SELECT * FROM help_events"
        where, args = [], []
        if device_id:
            where.append("device_id = ?")
            args.append(device_id)
        if state in STATES:
            where.append("state = ?")
            args.append(state)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_ms DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def counts(self, device_id: str | None = None) -> dict:
        sql = "SELECT state, COUNT(*) n FROM help_events"
        args: list = []
        if device_id:
            sql += " WHERE device_id = ?"
            args.append(device_id)
        sql += " GROUP BY state"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        out = {s: 0 for s in STATES}
        for r in rows:
            out[r["state"]] = r["n"]
        return out

    # ---------------------------------------------------------------- 视图
    @staticmethod
    def to_public(row: dict) -> dict:
        """给网页用的视图：把三级反馈**显式拆成三个独立字段**。

        刻意不让网页自己去推断 —— 三级反馈混在一起显示，正是任务卡
        反复强调要避免的事。每一级都把"证据是什么"一起给出去。
        """
        if not row:
            return {}
        out = dict(row)
        try:
            out["snapshot"] = json.loads(row.get("snapshot") or "{}")
        except (TypeError, ValueError):
            out["snapshot"] = {}
        out["simulated"] = bool(row.get("simulated"))

        def iso(ms):
            if not ms:
                return None
            return time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(ms / 1000)) + f".{ms % 1000:03d}"

        out["created_at"] = iso(row.get("created_ms"))
        out["ack_at"] = iso(row.get("ack_ms"))
        out["cancel_at"] = iso(row.get("cancel_ms"))
        # 一个东西只留一个名字。库里是 reply_cmd_id，但接口对外统一叫
        # reply_command_id —— 之前 POST 返回后者、GET 返回前者，
        # 同一份数据两个名字，读的人（和写测试的人）迟早会踩到。
        out.pop("reply_cmd_id", None)
        out["reply_command_id"] = row.get("reply_cmd_id")
        out["age_seconds"] = round(
            (time.time() * 1000 - row["created_ms"]) / 1000, 2) \
            if row.get("created_ms") else None

        out["levels"] = {
            "local": {
                "done": bool(row.get("local_feedback")),
                "how": row.get("local_feedback"),
                "evidence": "设备自报（不是服务器观测）",
                "note": "板端已给出物理反馈，断网也有效" if row.get("local_feedback")
                        else "设备未报告本地反馈",
            },
            "server": {
                "done": bool(row.get("created_ms")),
                "at": out["created_at"],
                "evidence": "服务器落库时刻（服务器时钟）",
                "note": "VPS 已接收这条求助",
            },
            "viewer": {
                "done": bool(row.get("ack_ms")),
                "at": out["ack_at"],
                "by": row.get("ack_by"),
                "evidence": "网页上真人点击「我来处理」",
                # 这句话就是任务卡那条硬规矩，直接写进接口返回值里
                "note": (f"{row.get('ack_by') or '有人'} 已回应"
                         if row.get("ack_ms")
                         else "还没有人回应 —— 因此界面不显示「对方已收到」"),
            },
        }
        return out


def _rand4() -> str:
    """网页侧模拟事件用的短随机后缀（设备侧的事件 id 自带随机前缀）。"""
    import random
    return "".join(random.choice("0123456789abcdef") for _ in range(4))
