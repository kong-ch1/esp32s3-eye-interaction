#!/usr/bin/env python3
"""ESP32 板端模拟器 —— 在还没拿到硬件之前，用它顶替开发板。

它会像真板子一样，按固定频率把一条 IMU 记录 HTTP POST 到服务端：
    {"device_id": "...", "seq": 12, "ts": 1694..., "ax": .., "ay": .., ...}

数据用"板子在桌面上被缓慢来回倾斜"这个物理模型生成：
    倾角 theta 随时间正弦摆动 -> ax/az 由重力投影得出，gx 由角速度得出。

运行:
    python sim/esp32_sim.py --device team01-esp32
    python sim/esp32_sim.py --device team01-esp32 --interval 0.5 --duration 30

按 Ctrl+C 停止。停止后网页上的数值会冻结并在 5 秒后提示"未更新"。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request


def motion(t: float) -> tuple[float, float, float, float, float, float]:
    """返回 (ax, ay, az, gx, gy, gz)。加速度单位 g，角速度单位 deg/s。"""
    # 绕 Y 轴缓慢摆动，周期 8 秒，幅度 ±25 度
    omega = 2 * math.pi / 8.0
    theta = math.radians(25.0) * math.sin(omega * t)          # rad
    dtheta = math.radians(25.0) * omega * math.cos(omega * t)  # rad/s

    # 重力在板体坐标系下的投影（静止时只受重力）
    ax = math.sin(theta)
    ay = 0.0
    az = math.cos(theta)
    gx = 0.0
    gy = math.degrees(dtheta)
    gz = 0.0

    # 加一点传感器噪声，避免数据"太干净"看着像假的
    n = lambda s: random.gauss(0, s)
    return (ax + n(0.004), ay + n(0.004), az + n(0.004),
            gx + n(0.3), gy + n(0.3), gz + n(0.3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--device", default="team01-esp32")
    ap.add_argument("--interval", type=float, default=1.0, help="上报间隔秒数")
    ap.add_argument("--duration", type=float, default=0, help="运行多少秒后自动停止，0=一直跑")
    args = ap.parse_args()

    url = args.server.rstrip("/") + "/api/data"
    seq = 0
    t0 = time.time()
    print(f"[sim] 目标: {url}")
    print(f"[sim] 设备: {args.device}  间隔: {args.interval}s  Ctrl+C 停止")

    try:
        while True:
            t = time.time() - t0
            if args.duration and t > args.duration:
                print("[sim] 已到设定时长，停止。网页数值将冻结并提示未更新。")
                break

            ax, ay, az, gx, gy, gz = motion(t)
            payload = {
                "device_id": args.device,
                "seq": seq,
                "ts": int(time.time() * 1000),
                "ax": round(ax, 6), "ay": round(ay, 6), "az": round(az, 6),
                "gx": round(gx, 3), "gy": round(gy, 3), "gz": round(gz, 3),
            }
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"})

            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    ok = json.loads(resp.read().decode("utf-8"))
                print(f"[sim] #{seq:04d} ax={ax:+.3f} az={az:+.3f} gy={gy:+7.1f}"
                      f" -> 服务端 id={ok.get('id')}")
            except urllib.error.URLError as e:
                print(f"[sim] 上报失败: {e}  (服务没起来？端口对吗？)", file=sys.stderr)

            seq += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[sim] 已停止。网页数值将冻结并提示未更新。")


if __name__ == "__main__":
    main()
