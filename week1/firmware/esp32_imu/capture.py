"""抓一段 COM5 串口输出，用于烧录后确认启动日志（温度/内存初始化是否成功）。
用法：python capture.py COM5
输出写入同目录 monitor_capture.txt
"""
import serial, time, sys

port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
baud = 115200
ser = serial.Serial(port, baud, timeout=1)
t0 = time.time()
out_path = "monitor_capture.txt"
with open(out_path, "w", encoding="utf-8") as f:
    while time.time() - t0 < 15:
        line = ser.readline()
        if line:
            try:
                text = line.decode("utf-8", errors="replace")
            except Exception:
                text = repr(line)
            f.write(text)
ser.close()
print("=== capture finished ===")
