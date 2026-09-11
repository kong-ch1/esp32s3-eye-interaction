/* ESP32-S3-EYE 板载 OV2640 摄像头 —— MJPEG 视频流服务
 *
 * 为什么能在一块 MCU 上跑视频监控：OV2640 传感器自带 JPEG 硬编码，
 * 它吐出来的就是压缩好的 JPEG 码流，ESP32 只负责搬运转发，不需要自己编码。
 *
 * 三个要点：
 *  1) 必须先开 PSRAM（sdkconfig: CONFIG_SPIRAM=y），帧缓冲放外部 RAM。
 *     内部 DRAM 只有 200 多 KB，一张 320x240 的 JPEG 就可能上百 KB，
 *     不开 PSRAM 必崩 —— 这是摄像头最典型的踩坑点。
 *  2) 摄像头 SCCB 走 I2C_NUM_1（sdkconfig: CONFIG_SCCB_HARDWARE_I2C_PORT1=y），
 *     IMU 用的是 I2C_NUM_0 + 老驱动。两条 I2C 总线挂在同一组引脚 GPIO4/5 上，
 *     但分属不同硬件外设、不同驱动实例，互不打扰。
 *  3) 视频流经 81 端口，跟 IMU 上报走的 8000 端口是两条完全独立的路，
 *     网页直接 <img src="http://板子IP:81/stream"> 就能看。
 */

#include <string.h>
#include <stdio.h>
#include "esp_log.h"
#include "esp_system.h"
#include "esp_heap_caps.h"
#include "driver/i2c.h"
#include "esp_camera.h"
#include "esp_http_server.h"
#include "camera_stream.h"

static const char *TAG = "week1:cam";

/* ESP32-S3-EYE 摄像头引脚（来源：esp-bsp bsp/esp32_s3_eye 官方定义） */
#define CAM_PIN_PWDN   -1      /* 未接，省电控制用不上 */
#define CAM_PIN_RESET  -1      /* 未接，走软件复位 */
#define CAM_PIN_XCLK   15
#define CAM_PIN_SIOD   4       /* 与 IMU 共用 SDA */
#define CAM_PIN_SIOC   5       /* 与 IMU 共用 SCL */
#define CAM_PIN_D7     16
#define CAM_PIN_D6     17
#define CAM_PIN_D5     18
#define CAM_PIN_D4     12
#define CAM_PIN_D3     10
#define CAM_PIN_D2     8
#define CAM_PIN_D1     9
#define CAM_PIN_D0     11
#define CAM_PIN_VSYNC  6
#define CAM_PIN_HREF   7
#define CAM_PIN_PCLK   13

#define CAM_STREAM_PORT  81

/* MJPEG 流格式：multipart/x-mixed-replace，每帧用 boundary 分隔 */
#define PART_BOUNDARY "123456789000000000000987654321"
static const char *STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char *STREAM_BOUNDARY     = "\r\n--" PART_BOUNDARY "\r\n";
static const char *STREAM_PART_HDR     = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static httpd_handle_t s_stream_httpd = NULL;

static camera_config_t s_cam_cfg = {
    .pin_pwdn       = CAM_PIN_PWDN,
    .pin_reset      = CAM_PIN_RESET,
    .pin_xclk       = CAM_PIN_XCLK,
    /* SCCB（摄像头配置总线）与 IMU 共用 GPIO4/5 那条物理 I2C。
     * 如果这里填具体引脚号，SCCB 会自己装一套 I2C 驱动去抢这些引脚，
     * 结果就是 IMU 读不到数据（实测：两边都装老驱动，引脚被后装的抢走）。
     * 正确做法：填 -1，让摄像头直接复用已经初始化好的 I2C_NUM_0 总线，
     * 两个设备挂在同一条总线上（IMU=0x12，OV2640=0x30），互不干扰。 */
    .pin_sccb_sda   = -1,
    .pin_sccb_scl   = -1,
    .sccb_i2c_port  = I2C_NUM_0,
    .pin_d7         = CAM_PIN_D7,
    .pin_d6         = CAM_PIN_D6,
    .pin_d5         = CAM_PIN_D5,
    .pin_d4         = CAM_PIN_D4,
    .pin_d3         = CAM_PIN_D3,
    .pin_d2         = CAM_PIN_D2,
    .pin_d1         = CAM_PIN_D1,
    .pin_d0         = CAM_PIN_D0,
    .pin_vsync      = CAM_PIN_VSYNC,
    .pin_href       = CAM_PIN_HREF,
    .pin_pclk       = CAM_PIN_PCLK,

    .xclk_freq_hz   = 20000000,
    .ledc_timer     = LEDC_TIMER_0,
    .ledc_channel   = LEDC_CHANNEL_0,

    .pixel_format   = PIXFORMAT_JPEG,   /* 关键：让传感器输出 JPEG，不要 GRB565 */
    .frame_size     = FRAMESIZE_QVGA,   /* 320x240，帧率与清晰度最均衡 */
    .jpeg_quality   = 12,               /* 数值越小画质越高、码流越大 */
    .fb_count       = 2,                /* 双缓冲，采集与发送并行 */
    .fb_location    = CAMERA_FB_IN_PSRAM,
    .grab_mode      = CAMERA_GRAB_LATEST,
};

/* MJPEG 流 handler：客户端不断开就一直推帧 */
static esp_err_t stream_handler(httpd_req_t *req)
{
    camera_fb_t *fb = NULL;
    char part_hdr[64];
    int n;

    esp_err_t res = httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
    if (res != ESP_OK) {
        return res;
    }

    /* 关掉这个 sockfd 的 Nagle，减小片段延迟 */
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

    while (true) {
        fb = esp_camera_fb_get();
        if (!fb) {
            ESP_LOGE(TAG, "取帧失败");
            res = ESP_FAIL;
            break;
        }

        /* 帧头 */
        n = snprintf(part_hdr, sizeof(part_hdr), STREAM_PART_HDR, (unsigned)fb->len);
        if (n < 0 || n >= (int)sizeof(part_hdr)) {
            esp_camera_fb_return(fb);
            res = ESP_FAIL;
            break;
        }
        res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
        if (res != ESP_OK) { esp_camera_fb_return(fb); break; }
        res = httpd_resp_send_chunk(req, part_hdr, n);
        if (res != ESP_OK) { esp_camera_fb_return(fb); break; }

        /* 帧数据 */
        res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
        esp_camera_fb_return(fb);
        if (res != ESP_OK) {
            /* 客户端断开或 sock 出错，退出本次连接等下一次请求 */
            break;
        }
    }

    ESP_LOGI(TAG, "视频流连接结束: %s", esp_err_to_name(res));
    return res;
}

/* 单帧快照：适合做定时抓拍、存档 */
static esp_err_t capture_handler(httpd_req_t *req)
{
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "取帧失败");
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    return res;
}

static esp_err_t start_stream_server(void)
{
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.server_port    = CAM_STREAM_PORT;
    /* 默认 4096 的栈跑拼接格式字符串 + 发送很紧，给足一点 */
    cfg.stack_size     = 8192;
    cfg.max_uri_handlers = 8;

    if (httpd_start(&s_stream_httpd, &cfg) != ESP_OK) {
        ESP_LOGE(TAG, "视频流服务启动失败");
        return ESP_FAIL;
    }

    httpd_uri_t stream_uri = {
        .uri      = "/stream",
        .method   = HTTP_GET,
        .handler  = stream_handler,
        .user_ctx = NULL,
    };
    httpd_register_uri_handler(s_stream_httpd, &stream_uri);

    httpd_uri_t capture_uri = {
        .uri      = "/capture",
        .method   = HTTP_GET,
        .handler  = capture_handler,
        .user_ctx = NULL,
    };
    httpd_register_uri_handler(s_stream_httpd, &capture_uri);

    ESP_LOGI(TAG, "视频流已就绪: http://<板子IP>:%d/stream  (单帧: /capture)", CAM_STREAM_PORT);
    return ESP_OK;
}

esp_err_t camera_stream_start(void)
{
    /* 打印可用内存，排错时第一眼就看这个 */
    ESP_LOGI(TAG, "PSRAM 总容量=%u 字节, 空闲堆=%u",
             (unsigned)heap_caps_get_total_size(MALLOC_CAP_SPIRAM),
             (unsigned)esp_get_free_heap_size());

    esp_err_t err = esp_camera_init(&s_cam_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "摄像头初始化失败: %s", esp_err_to_name(err));
        ESP_LOGE(TAG, "最常见原因：PSRAM 没开，或引脚定义与板子不符");
        return err;
    }

    sensor_t *s = esp_camera_sensor_get();
    if (s) {
        /* OV2640 出厂朝向可能与握持方向不一致，需要时打开下面两行 */
        /* s->set_vflip(s, 1); */
        /* s->set_hmirror(s, 1); */
        ESP_LOGI(TAG, "摄像头就绪: pid=0x%02x ver=0x%02x",
                 (unsigned)s->id.PID, (unsigned)s->id.VER);
    }

    ESP_LOGI(TAG, "初始化后空闲堆=%u, PSRAM 空闲=%u",
             (unsigned)esp_get_free_heap_size(),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

    return start_stream_server();
}
