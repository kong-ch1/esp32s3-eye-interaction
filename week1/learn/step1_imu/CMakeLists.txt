cmake_minimum_required(VERSION 3.16)
# 这行把 ESP-IDF 自带的编译规则引进来（不用自己写 Makefile）
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# 工程名，随便起，会决定生成的 .bin 文件名
project(step1_imu)
