/* AI 交互课 第1周 —— ESP32 板端固件（待实机验证）
 *
 * 功能：连 WiFi -> I2C 读 MPU6050 -> 每 1 秒 HTTP POST 一条 JSON 到服务端
 *
 * 使用前改下面 5 个宏：WIFI_SSID / WIFI_PASS / SERVER_URL / DEVICE_ID / I2C 引脚
 * 编译烧录：
 *     idf.py set-target esp32
 *     idf.py build
 *     idf.py -p COM3 flash monitor
 *
 * 注意：SERVER_URL 要填电脑的局域网 IP（如 http://192.168.1.23:8000/api/data），
 *       不能用 127.0.0.1 —— 那是板子自己。
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
#define DEVICE_ID      "team01-esp32"

#define I2C_SDA_GPIO   21
#define I2C_SCL_GPIO   22
#define I2C_PORT       I2C_NUM_0
#define I2C_FREQ_HZ    400000
#define MPU6050_ADDR   0x68

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

static esp_err_t mpu6050_write_reg(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_master_write_to_device(I2C_PORT, MPU6050_ADDR, buf, 2,
                                      pdMS_TO_TICKS(100));
}

static esp_err_t mpu6050_read(uint8_t reg, uint8_t *out, size_t len)
{
    return i2c_master_write_read_device(I2C_PORT, MPU6050_ADDR, &reg, 1,
                                        out, len, pdMS_TO_TICKS(100));
}

static void mpu6050_init(void)
{
    /* PWR_MGMT_1 = 0 -> 退出睡眠，使用内部 8MHz 振荡器 */
    ESP_ERROR_CHECK(mpu6050_write_reg(0x6B, 0x00));
    vTaskDelay(pdMS_TO_TICKS(100));
    /* 量程：加速度 ±2g(默认)，陀螺仪 ±250°/s(默认) */
    ESP_LOGI(TAG, "MPU6050 初始化完成");
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
    wifi_init_sta();

    /* 等 WiFi 拿到 IP */
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT,
                        pdFALSE, pdTRUE, portMAX_DELAY);

    i2c_master_init();
    mpu6050_init();

    uint32_t seq = 0;
    while (1) {
        uint8_t raw[14];
        if (mpu6050_read(0x3B, raw, sizeof(raw)) != ESP_OK) {
            ESP_LOGE(TAG, "读取 MPU6050 失败");
            vTaskDelay(pdMS_TO_TICKS(POST_INTERVAL_MS));
            continue;
        }

        int16_t ax_raw = (int16_t)((raw[0] << 8) | raw[1]);
        int16_t ay_raw = (int16_t)((raw[2] << 8) | raw[3]);
        int16_t az_raw = (int16_t)((raw[4] << 8) | raw[5]);
        int16_t gx_raw = (int16_t)((raw[8] << 8) | raw[9]);
        int16_t gy_raw = (int16_t)((raw[10] << 8) | raw[11]);
        int16_t gz_raw = (int16_t)((raw[12] << 8) | raw[13]);

        /* 换算成物理单位：±2g -> 16384 LSB/g；±250°/s -> 131 LSB/(°/s) */
        float ax = ax_raw / 16384.0f;
        float ay = ay_raw / 16384.0f;
        float az = az_raw / 16384.0f;
        float gx = gx_raw / 131.0f;
        float gy = gy_raw / 131.0f;
        float gz = gz_raw / 131.0f;

        char payload[256];
        int n = snprintf(payload, sizeof(payload),
                         "{\"device_id\":\"%s\",\"seq\":%lu,\"ts\":%lld,"
                         "\"ax\":%.4f,\"ay\":%.4f,\"az\":%.4f,"
                         "\"gx\":%.2f,\"gy\":%.2f,\"gz\":%.2f}",
                         DEVICE_ID, (unsigned long)seq,
                         (long long)(esp_timer_get_time() / 1000),
                         ax, ay, az, gx, gy, gz);
        if (n > 0 && n < (int)sizeof(payload)) {
            ESP_LOGI(TAG, "ax=%.3f ay=%.3f az=%.3f | gx=%.1f gy=%.1f gz=%.1f",
                     ax, ay, az, gx, gy, gz);
            http_post_json(payload);
        }

        seq++;
        vTaskDelay(pdMS_TO_TICKS(POST_INTERVAL_MS));
    }
}
