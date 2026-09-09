@echo off
rem week1 data server - double click to start, keep window open while collecting
cd /d D:\aijiaohu\week1\server
echo [week1] starting server on 0.0.0.0:8000 ...
echo [week1] webpage: http://127.0.0.1:8000  (close this window to stop)
python server.py
pause
