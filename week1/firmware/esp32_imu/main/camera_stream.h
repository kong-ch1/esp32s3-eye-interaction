#pragma once

#include "esp_err.h"

/**
 * @brief 初始化板载 OV2640 摄像头并启动 MJPEG 视频流服务
 *
 * 成功之后：
 *   http://<板子IP>:81/stream   MJPEG 连续视频流（可直接放进 <img src=...>）
 *   http://<板子IP>:81/capture  单帧 JPEG（做快照用）
 *
 * @note 前置条件：必须在 sdkconfig 里启用 PSRAM，帧缓冲放在外部 RAM，
 *       否则 320x240 一帧就 150KB，内部 DRAM 撑不住。
 */
esp_err_t camera_stream_start(void);
