# -*- coding: utf-8 -*-
"""抓一段串口日志，找出板子反复重启的真正原因"""
import serial, sys, time, io

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM5"
SECS = int(sys.argv[2]) if len(sys.argv) > 2 else 45

out_lines = []
try:
    ser = serial.Serial(PORT, 115200, timeout=1, dsrdtr=False, rtscts=False)
except Exception as e:
    with open(r"D:\aijiaohu\week1\serial_out.txt", "w", encoding="utf-8") as f:
        f.write("打不开 %s: %r" % (PORT, e))
    sys.exit(1)

# 不要拉低 GPIO0 / EN，避免把板子按进下载模式
ser.setDTR(False); ser.setRTS(False)
time.sleep(0.2)

print("抓串口 %s %d 秒..." % (PORT, SECS), flush=True)
t0 = time.time()
try:
    ser.reset_input_buffer()
    while time.time() - t0 < SECS:
        line = ser.readline()
        if line:
            try:
                s = line.decode("utf-8", errors="replace").rstrip("\r\n")
            except Exception:
                s = repr(line)
            print(s, flush=True)
            out_lines.append(s)
except KeyboardInterrupt:
    pass
finally:
    ser.close()

with open(r"D:\aijiaohu\week1\serial_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out_lines))
print("抓完，共 %d 行 -> serial_out.txt" % len(out_lines))
