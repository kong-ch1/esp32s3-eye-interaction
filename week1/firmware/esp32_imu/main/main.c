/* AI 交互课 第1周 —— ESP32-S3-EYE 板端固件（板载 QMA7981 加速度计）
 *
 * 功能：连 WiFi -> I2C 读板载 QMA7981（GPIO4=SDA, GPIO5=SCL, 地址 0x12）
 *       -> 每 1 秒 HTTP POST 一条 JSON 到服务端
 *
 * 注意：QMA7981 只有三轴加速度，没有陀螺仪 —— gx/gy/gz 固定发 0。
 *
 * 使用前改 3 个宏：WIFI_SSID / WIFI_PASS / SERVER_URL
 * 编译烧录（用桌面的 ESP-IDF 5.4 CMD）：
 *     idf.py set-target esp32s3
 *     idf.py build
 *     idf.py -p COM5 flash monitor
 *
 * SERVER_URL 填电脑的局域网 IP（如 http://192.168.1.23:8000/api/data），
 * 不能用 127.0.0.1 —— 那是板子自己。
 */

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_http_client.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "driver/i2c.h"

/* ============ 需要按实际情况修改 ============ */
#define WIFI_SSID      "YOUR_WIFI_SSID"
#define WIFI_PASS      "YOUR_WIFI_PASSWORD"
#define SERVER_URL     "http://192.168.1.23:8000/api/data"
#define DEVICE_ID      "team01-esp32s3eye"

/* ============ ESP32-S3-EYE 固定接线（不用改） ============ */
#define I2C_SDA_GPIO   4      /* 与摄像头 SCCB 共用总线 */
#define I2C_SCL_GPIO   5
#define I2C_PORT       I2C_NUM_0
#define I2C_FREQ_HZ    400000
#define QMA7981_ADDR   0x12

/* 灵敏度：±2g 量程下每 1g 对应的原始计数值。
 * 不同批次/资料有 1024 / 4096 两种说法，首次运行看 monitor 日志：
 * 板子水平放平，若 az 显示的 g 值不等于 1.0，按 raw 值反过来改这个宏即可。 */
#define QMA_SENS_LSB_PER_G  1024.0f

#define POST_INTERVAL_MS  1000
/* =========================================== */

static const char *TAG = "week1";
static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "WiFi 断开，重试中...");
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ev = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "拿到 IP: " IPSTR, IP2STR(&ev->ip_info.ip));
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static void wifi_init_sta(void)
{
    s_wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               &wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               &wifi_event_handler, NULL));

    wifi_config_t wifi_cfg = {0};
    strncpy((char *)wifi_cfg.sta.ssid, WIFI_SSID, sizeof(wifi_cfg.sta.ssid) - 1);
    strncpy((char *)wifi_cfg.sta.password, WIFI_PASS, sizeof(wifi_cfg.sta.password) - 1);
    wifi_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "WiFi 启动，等待连接 %s ...", WIFI_SSID);
}

static void i2c_master_init(void)
{
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_SDA_GPIO,
        .scl_io_num = I2C_SCL_GPIO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_FREQ_HZ,
    };
    ESP_ERROR_CHECK(i2c_param_config(I2C_PORT, &conf));
    ESP_ERROR_CHECK(i2c_driver_install(I2C_PORT, conf.mode, 0, 0, 0));
}

/* 启动时扫一遍 I2C 总线，把挂着的设备地址打出来 —— 首次调试定位问题用 */
static void i2c_scan(void)
{
    ESP_LOGI(TAG, "I2C 扫描 (SDA=%d SCL=%d)...", I2C_SDA_GPIO, I2C_SCL_GPIO);
    for (uint8_t addr = 1; addr < 127; addr++) {
        uint8_t dummy;
        esp_err_t err = i2c_master_write_read_device(I2C_PORT, addr, &dummy, 0,
                                                     &dummy, 0,
                                                     pdMS_TO_TICKS(20));
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "  发现设备: 0x%02x%s", addr,
                     addr == QMA7981_ADDR ? "  <-- QMA7981" : "");
        }
    }
}

static esp_err_t qma_write_reg(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_master_write_to_device(I2C_PORT, QMA7981_ADDR, buf, 2,
                                      pdMS_TO_TICKS(100));
}

static esp_err_t qma_read(uint8_t reg, uint8_t *out, size_t len)
{
    return i2c_master_write_read_device(I2C_PORT, QMA7981_ADDR, &reg, 1,
                                        out, len, pdMS_TO_TICKS(100));
}

static bool qma7981_init(void)
{
    uint8_t chip_id = 0;
    if (qma_read(0x00, &chip_id, 1) != ESP_OK) {
        ESP_LOGE(TAG, "读不到 QMA7981 (0x%02x)，检查地址/接线", QMA7981_ADDR);
        return false;
    }
    ESP_LOGI(TAG, "QMA7981 chip_id = 0x%02x (期望 0xE7)", chip_id);

    /* 上电初始化：±2g 量程、带宽档、使能加速度 */
    ESP_ERROR_CHECK(qma_write_reg(0x0F, 0x00));  /* RANGE: ±2g */
    ESP_ERROR_CHECK(qma_write_reg(0x10, 0x03));  /* BW_LPF 滤波带宽 */
    ESP_ERROR_CHECK(qma_write_reg(0x11, 0x80));  /* POWER_CTL: EN_ACC, 主动模式 */
    vTaskDelay(pdMS_TO_TICKS(50));
    ESP_LOGI(TAG, "QMA7981 初始化完成");
    return true;
}

static esp_err_t http_post_json(const char *json)
{
    esp_http_client_config_t cfg = {
        .url = SERVER_URL,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 5000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (client == NULL) {
        return ESP_FAIL;
    }
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, json, strlen(json));

    esp_err_t err = esp_http_client_perform(client);
    if (err == ESP_OK) {
        int status = esp_http_client_get_status_code(client);
        ESP_LOGI(TAG, "上报完成 HTTP %d", status);
        if (status != 200) {
            err = ESP_FAIL;
        }
    } else {
        ESP_LOGW(TAG, "上报失败: %s", esp_err_to_name(err));
    }
    esp_http_client_cleanup(client);
    return err;
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    i2c_master_init();
    i2c_scan();

    if (!qma7981_init()) {
        ESP_LOGE(TAG, "IMU 初始化失败，停机（不发送假数据）");
        return;
    }

    wifi_init_sta();
    /* 等 WiFi 拿到 IP */
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT,
                        pdFALSE, pdTRUE, portMAX_DELAY);

    uint32_t seq = 0;
    while (1) {
        uint8_t raw[6];
        if (qma_read(0x12, raw, sizeof(raw)) != ESP_OK) {
            ESP_LOGE(TAG, "读取 QMA7980 失败");
            vTaskDelay(pdMS_TO_TICKS(POST_INTERVAL_MS));
            continue;
        }

        /* QMA7981：小端（低字节在前），与 MPU6050 大端相反 */
        int16_t ax_raw = (int16_t)((raw[1] << 8) | raw[0]);
        int16_t ay_raw = (int16_t)((raw[3] << 8) | raw[2]);
        int16_t az_raw = (int16_t)((raw[5] << 8) | raw[4]);

        float ax = ax_raw / QMA_SENS_LSB_PER_G;
        float ay = ay_raw / QMA_SENS_LSB_PER_G;
        float az = az_raw / QMA_SENS_LSB_PER_G;

        char payload[256];
        int n = snprintf(payload, sizeof(payload),
                         "{\"device_id\":\"%s\",\"seq\":%lu,\"ts\":%lld,"
                         "\"ax\":%.4f,\"ay\":%.4f,\"az\":%.4f,"
                         "\"gx\":0.00,\"gy\":0.00,\"gz\":0.00}",
                         DEVICE_ID, (unsigned long)seq,
                         (long long)(esp_timer_get_time() / 1000),
                         ax, ay, az);
        if (n > 0 && n < (int)sizeof(payload)) {
            ESP_LOGI(TAG, "raw=[%d %d %d] ax=%.3f ay=%.3f az=%.3f",
                     ax_raw, ay_raw, az_raw, ax, ay, az);
            http_post_json(payload);
        }

        seq++;
        vTaskDelay(pdMS_TO_TICKS(POST_INTERVAL_MS));
    }
}
