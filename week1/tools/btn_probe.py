#!/usr/bin/env python3
"""第3周 按键电压核验（串口侧）—— 用来验证"四个键的电压判读对不对"。

为什么要单独做这一步：
  四个键共用 GPIO1，只能靠电压区分。而原理图标注的电压（0.38/0.82/1.98/2.41V）
  是**设计值**，实际板子会有电阻误差。如果实际值偏移到相邻档位的判决区间里，
  按 MENU 会被识别成 PLAY —— 而且不会有任何报错，只是"按错了键"。
  本项目在 IMU 型号上吃过这种"文档与实物不一致"的亏，所以这里实测一遍。

用法（必须用带 pyserial 的解释器，即 ESP-IDF 自带的那个 python）：
    <IDF>/python_env/idf5.4_py3.11_env/Scripts/python.exe tools/btn_probe.py

运行后依次按下四个键，每个键按一下即可。脚本会把实测电压与原理图设计值对照。
"""
from __future__ import annotations

import argparse
import re
import sys
import time

try:
    import serial
except ImportError:
    print("缺少 pyserial。请用 ESP-IDF 自带的 python 运行：")
    print("  D:/gongju/Espressif/python_env/idf5.4_py3.11_env/Scripts/python.exe "
          "tools/btn_probe.py")
    raise SystemExit(2)

# 原理图第 4 页标注的设计值
DESIGN = {"UP+": 380, "DN-": 820, "PLAY": 1980, "MENU": 2410}
# 判决边界（与固件 btn_classify() 保持一致：相邻两档中点）
BOUNDS = [("UP+", 0, 600), ("DN-", 600, 1400), ("PLAY", 1400, 2195),
          ("MENU", 2195, 2855)]

LINE = re.compile(r"实体按键按下:\s*(\S+)\s*\((\d+)\s*mV\)")
IDLE = re.compile(r"空闲读数\s*(\d+)\s*mV")


def zone(mv: int) -> str:
    for name, lo, hi in BOUNDS:
        if lo <= mv < hi:
            return name
    return "无按键"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--raw", default="", help="把所有串口原文另存到这个文件（留证据用）")
    args = ap.parse_args()

    print("=" * 62)
    print(f"第3周 按键电压核验  ({args.port}, 最长 {args.seconds} 秒)")
    print("=" * 62)
    print("\n请依次按下四个键，每个按一下：UP+ / DN- / PLAY / MENU")
    print("（不确定位置就四个都按过去，脚本按实测电压自动归类）\n")
    print("注意：运行期间**不要按 RST**。板上 RST 会让芯片重启，USB 随之")
    print("      重新枚举，已经打开的串口句柄会失效而读不到任何数据 ——")
    print("      这正是本脚本第一版踩的坑，所以现在加了看门狗自动重连。\n")

    def open_port():
        s = serial.Serial(args.port, 115200, timeout=0.3)
        s.setDTR(False)   # DTR/RTS 都不拉：避免触发自动复位/下载模式
        s.setRTS(False)
        return s

    try:
        s = open_port()
    except Exception as e:
        print(f"打开 {args.port} 失败: {e}")
        return 1

    idle_mv = None
    seen: dict[str, int] = {}
    raw_lines: list[str] = []
    t0 = time.time()
    buf = ""
    last_data = time.time()
    reopens = 0
    try:
        while time.time() - t0 < args.seconds:
            d = s.read(4096)
            if not d:
                # 看门狗：连续 5 秒没有数据就重开串口。
                # 板子一旦重启，Windows 下旧句柄会静默失效（不报错、也不再收数据），
                # 只能靠重开来恢复。实测就是这样丢了整整一个 200 秒的采集窗口。
                if time.time() - last_data > 5:
                    try:
                        s.close()
                    except Exception:
                        pass
                    time.sleep(0.8)
                    try:
                        s = open_port()
                        reopens += 1
                        print(f"  （串口静默超过 5 秒，已重连 #{reopens}）")
                    except Exception as e:
                        print(f"  重连失败: {e}")
                    last_data = time.time()
                continue
            last_data = time.time()
            buf += d.decode("utf-8", "replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if args.raw:
                    raw_lines.append(line)
                m = IDLE.search(line)
                if m and idle_mv is None:
                    idle_mv = int(m.group(1))
                    print(f"  空闲电压 {idle_mv} mV → 判读 {zone(idle_mv)}")
                m = LINE.search(line)
                if m:
                    name, mv = m.group(1), int(m.group(2))
                    if name not in seen:
                        seen[name] = mv
                        design = DESIGN.get(name)
                        d_txt = f"（原理图 {design} mV，偏差 {mv - design:+d} mV）" \
                            if design else ""
                        print(f"  识别到 {name:<5} 实测 {mv:>5} mV {d_txt}")
                        sys.stdout.flush()
                    if len(seen) >= 4:
                        break
            if len(seen) >= 4:
                break
    finally:
        try:
            s.close()
        except Exception:
            pass

    if args.raw and raw_lines:
        with open(args.raw, "w", encoding="utf-8") as fh:
            fh.write("\n".join(raw_lines))
        print(f"\n（串口原文 {len(raw_lines)} 行已存到 {args.raw}）")

    print("\n" + "=" * 62)
    print("核验结果")
    print("=" * 62)
    print(f"  {'键名':<8}{'实测mV':>8}{'设计mV':>8}{'偏差':>8}   判定")
    ok = True
    for name in ("UP+", "DN-", "PLAY", "MENU"):
        if name in seen:
            mv, dz = seen[name], DESIGN[name]
            good = zone(mv) == name
            ok &= good
            print(f"  {name:<8}{mv:>8}{dz:>8}{mv - dz:>+8}   "
                  f"{'✓ 归类正确' if good else '✗ 被归到 ' + zone(mv)}")
        else:
            print(f"  {name:<8}{'—':>8}{DESIGN[name]:>8}{'—':>8}   未按到")
            ok = False
    print()
    if ok:
        print("结论: 四个键的实测电压都落在各自判决区间内 ✓")
    else:
        print("结论: 有键未按到或归类错误 —— 需要调整固件里的 BTN_MV_* 或判决边界")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
