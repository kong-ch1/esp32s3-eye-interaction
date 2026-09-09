@echo off
call D:\gongju\Espressif\idf_cmd_init.bat esp-idf-v5.4.4
cd /d D:\aijiaohu\week1\firmware\esp32_imu
idf.py set-target esp32s3
idf.py build
