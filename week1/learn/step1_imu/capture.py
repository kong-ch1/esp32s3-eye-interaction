import serial, time, sys

port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
baud = 115200
ser = serial.Serial(port, baud, timeout=1)
t0 = time.time()
out_path = "monitor_capture.txt"
with open(out_path, "w", encoding="utf-8") as f:
    while time.time() - t0 < 12:
        line = ser.readline()
        if line:
            try:
                text = line.decode("utf-8", errors="replace")
            except Exception:
                text = repr(line)
            f.write(text)
            print(text, end="")
ser.close()
print("\n=== capture finished ===")
