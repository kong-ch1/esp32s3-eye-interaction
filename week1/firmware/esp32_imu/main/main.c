/* AI 交互课 第1周 —— ESP32-S3-EYE 板端固件（板载三轴加速度计）
 *
 * 功能：连 WiFi -> I2C 读板载加速度计（GPIO4=SDA, GPIO5=SCL, 地址 0x12）
 *       -> 每 1 秒 HTTP POST 一条 JSON 到服务端
 *
 * ====== 关于芯片型号（重要，调试踩坑的根源）======
 * 原理图和官方手册标注的是 QMA7981，但**本板实装为 QMA6100P**。
 * 两者是 pin-to-pin 兼容的换代关系（QMA7981 已 EOL 停产），封装、
 * I2C 地址(0x12)、14 位 ADC 都一样，所以硬件上换料看不出来。
 * 本板的实测证据（上电时 imu_identify() 会自动打印一遍）：
 *   1) CHIP_ID(0x00) = 0x90  -> QMA6100P（QMA7981 公开手册为 0xE7）
 *   2) QMA6100P 手册特有寄存器 0x33/0x45/0x46/0x4A/0x56/0x5F 均有有效应答
 *   3) 量程位编码 0x01/0x02/0x04/0x08/0x0F(±2/4/8/16/32g) 全部被接受，
 *      而 0x00 是非法值 —— 当初按 QMA7981 手册写 0x00 导致模长异常 1.5g
 * 结论：本驱动按 QMA6100P 的真实寄存器行为编写，不要照 QMA7981 手册改。
 *
 * 注意：QMA6100P 只有三轴加速度，没有陀螺仪 —— gx/gy/gz 固定发 0。
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
#include <math.h>
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
#include "driver/temperature_sensor.h"   /* ESP32-S3 内置温度传感器*/
#include "esp_system.h"                  /* esp_get_free_heap_size() 等内存统计 */
#include "esp_heap_caps.h"               /* heap_caps_get_total_size() 堆总大小 */
#include "camera_stream.h"

/* ============ 需要按实际情况修改 ============ */
#define WIFI_SSID      "431"
#define WIFI_PASS      "88888888"
#define SERVER_URL     "http://10.1.41.18:8000/api/data"
#define DEVICE_ID      "team01-esp32s3eye"

/* ============ ESP32-S3-EYE 固定接线（不用改） ============ */
#define I2C_SDA_GPIO   4      /* 与摄像头 SCCB 共用总线 */
#define I2C_SCL_GPIO   5
#define I2C_PORT       I2C_NUM_0
#define I2C_FREQ_HZ    400000
#define QMA6100P_ADDR   0x12

/* 灵敏度：±2g 量程、14位输出 下每 1g 对应的原始计数值。
 * QMA6100P 数据为 14 位左对齐（先组装 16 位再算术右移 2 位），
 * ±2g 满量程 16384 计数跨 4g => 4096 LSB/g。 */
#define QMA_SENS_LSB_PER_G  4096.0f

#define POST_INTERVAL_MS  1000

/* ===== 实测标定（旋转实验：面朝上/朝下静止两点法拟合） =====
 * 本板 QMA6100P（chip_id 0x90）零偏偏大：未标定静止模长 0.78~1.6g 漂移。
 * X 零偏 -0.553g、Y 零偏 -0.148g、Z 零偏 +0.389g、Z 灵敏度系数 0.912，
 * 标定后各静置姿态模长 0.97~1.01。换板子需重新标定。 */
#define CAL_X_OFF    2265.0f   /* 计数 = 0.553g * 4096 */
#define CAL_Y_OFF     608.0f   /* 计数 = 0.148g * 4096 */
#define CAL_Z_OFF    1594.0f   /* 计数 = 0.389g * 4096 */
#define CAL_Z_SCALE    0.912f
/* =========================================== */
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

    /* —— 关掉 WiFi 省电（Modem-sleep）——
     * 默认省电模式下板子会周期性休眠，AP 只能先把包缓存起来，
     * 等板子醒了再发，结果就是延迟动辄几百毫秒到几秒、还伴随丢包
     * （实测：ping 板子平均 984ms、丢包 33%，而 ping 有线网关 0ms 零丢包）。
     * 数据上报是持续业务，这点电不值得省，直接关掉。 */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    ESP_LOGI(TAG, "WiFi 启动（已关闭省电模式），等待连接 %s ...", WIFI_SSID);
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

/* 启动时扫一遍 I2C 总线，把挂着的设备地址打出来 —— 首次调试定位问题用
 * 注意：探测必须用 1 字节真实读，零长度读在本驱动上会全程报
 * "i2c data read length error" 且永远探测不到设备 */
static void i2c_scan(void)
{
    ESP_LOGI(TAG, "I2C 扫描 (SDA=%d SCL=%d)...", I2C_SDA_GPIO, I2C_SCL_GPIO);
    for (uint8_t addr = 1; addr < 127; addr++) {
        uint8_t dummy;
        esp_err_t err = i2c_master_write_read_device(I2C_PORT, addr, &dummy, 0,
                                                     &dummy, 1,
                                                     pdMS_TO_TICKS(20));
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "  发现设备: 0x%02x%s", addr,
                     addr == QMA6100P_ADDR ? "  <-- 加速度计(QMA6100P)" : "");
        }
    }
}

static esp_err_t qma_write_reg(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_master_write_to_device(I2C_PORT, QMA6100P_ADDR, buf, 2,
                                      pdMS_TO_TICKS(100));
}

static esp_err_t qma_read(uint8_t reg, uint8_t *out, size_t len)
{
    return i2c_master_write_read_device(I2C_PORT, QMA6100P_ADDR, &reg, 1,
                                        out, len, pdMS_TO_TICKS(100));
}

static bool qma6100p_init(void)
{
    uint8_t chip_id = 0;
    if (qma_read(0x00, &chip_id, 1) != ESP_OK) {
        ESP_LOGE(TAG, "读不到 QMA6100P (0x%02x)，检查地址/接线", QMA6100P_ADDR);
        return false;
    }
    ESP_LOGI(TAG, "QMA6100P chip_id = 0x%02x", chip_id);
    /* 本板实测为 0x90；不同硅版本有差异（0xE7 亦见诸资料），只提示不拦 */
    if (chip_id != 0x90 && chip_id != 0xE7) {
        ESP_LOGW(TAG, "chip_id 非常见值(0x90/0xE7)，继续尝试");
    }

    /* 软复位（0x36=SOFT_RESET 写 0xB6 后回写 0x00）：
     * 实测本板上电后寄存器写入不生效、Y 轴数据冻结，软复位后恢复正常 */
    qma_write_reg(0x36, 0xB6);
    vTaskDelay(pdMS_TO_TICKS(20));
    qma_write_reg(0x36, 0x00);
    vTaskDelay(pdMS_TO_TICKS(20));

    /* 按参考驱动顺序：先退出睡眠，再设量程/带宽 */
    ESP_ERROR_CHECK(qma_write_reg(0x11, 0xC0));  /* POWER: 退出睡眠，主动模式 */
    vTaskDelay(pdMS_TO_TICKS(20));
    ESP_ERROR_CHECK(qma_write_reg(0x0F, 0x01));  /* RANGE: ±2g (QMA_RANGE_2G=0b0001) */
    ESP_ERROR_CHECK(qma_write_reg(0x10, 0x05));  /* BW: 1024Hz (合法值 0b101) */
    vTaskDelay(pdMS_TO_TICKS(50));

    /* 回读配置，确认写入是否生效 */
    uint8_t cfg[3];
    qma_read(0x0F, cfg, 3);
    ESP_LOGI(TAG, "配置回读 0F/10/11 = %02x %02x %02x (期望 01 05 C0)",
             cfg[0], cfg[1], cfg[2]);
    ESP_LOGI(TAG, "加速度计初始化完成");
    return true;
}

/* 型号鉴定：原理图/手册标 QMA7981，但官方 BSP 只有 qma6100p 组件。
 * 用三条独立证据确认实装型号：
 *   1) CHIP_ID(0x00)：QMA6100P=0x90，QMA7981=0xE7
 *   2) QMA6100P 手册特有寄存器（0x33/0x45/0x46/0x4A/0x56/0x5F）是否有有效内容
 *   3) 量程支持性：QMA6100P 支持到 ±32g(0b1111)，QMA7981 老手册只到 ±8g/±16g
 * 鉴定结束后把量程恢复为 ±2g。 */
static void imu_identify(void)
{
    uint8_t v = 0;
    ESP_LOGI(TAG, "======== 型号鉴定 ========");

    qma_read(0x00, &v, 1);
    ESP_LOGI(TAG, "ID  CHIP_ID(0x00) = 0x%02x  (0x90=QMA6100P / 0xE7=QMA7981)", v);

    uint8_t probe[]   = {0x33, 0x45, 0x46, 0x4A, 0x56, 0x5F};
    const char *nm[]  = {"NVM", "CHIP_STATE", "ULPS", "TST0_ANA", "AFE_ANA", "TST1_ANA"};
    for (int i = 0; i < 6; i++) {
        uint8_t r = 0xFF;
        esp_err_t e = qma_read(probe[i], &r, 1);
        ESP_LOGI(TAG, "REG 0x%02x %s = %s 0x%02x", probe[i], nm[i],
                 e == ESP_OK ? "ACK " : "NACK", r);
    }

    uint8_t ranges[] = {0x01, 0x02, 0x04, 0x08, 0x0F};
    const char *rn[] = {"2g", "4g", "8g", "16g", "32g"};
    for (int i = 0; i < 5; i++) {
        uint8_t rb = 0xFF;
        qma_write_reg(0x0F, ranges[i]);
        vTaskDelay(pdMS_TO_TICKS(30));
        qma_read(0x0F, &rb, 1);
        ESP_LOGI(TAG, "RANGE wr=0x%02x(+-%s) rd=0x%02x %s",
                 ranges[i], rn[i], rb, (rb == ranges[i]) ? "接受" : "拒绝/不生效");
    }
    qma_write_reg(0x0F, 0x01);
    vTaskDelay(pdMS_TO_TICKS(30));
    qma_read(0x0F, &v, 1);
    ESP_LOGI(TAG, "RANGE 恢复 = 0x%02x (期望 0x01)", v);
    ESP_LOGI(TAG, "======== 鉴定结束 ========");
}

static esp_err_t http_post_json(const char *json)
{
    esp_http_client_config_t cfg = {
        .url = SERVER_URL,
        .method = HTTP_METHOD_POST,
        /* 超时只给 2 秒：WiFi 差时 HTTP 会卡到超时才失败，
         * 卡多久 = 主循环停多久 = 丢多少条数据。快速失败反而更容易恢复。 */
        .timeout_ms = 2000,
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

/* QMA6100P 14位数据组装：
 * MSB 寄存器 = DX[13:6]，LSB 寄存器 bit5:0 = DX[5:0]，bit7 = NEWDATA 标志
 * 14位二进制补码，bit13 为符号位 */
static inline int16_t qma_assemble14(uint8_t lsb, uint8_t msb)
{
    int16_t v = (int16_t)(((uint16_t)msb << 6) | (lsb & 0x3F));
    if (v & 0x2000) {
        v -= 0x4000;
    }
    return v;
}

/* ================= 设备自身状态：芯片温度 + 堆内存 =================
 *
 * 温度：ESP32-S3 芯片内部自带一颗温度传感器，不用外接任何元件。
 *   注意它测的是**芯片裸片温度**，不是环境温度——芯片自己会发热，
 *   通常比室温高几度。适合观察"板子负载/温升趋势"，不能当室温计用。
 *
 * 内存：esp_get_free_heap_size() 返回当前可用堆（字节），
 *   esp_get_minimum_free_heap_size() 返回开机以来的历史最低值，
 *   后者是判断"有没有内存泄漏"的关键：它随时间持续下降 = 有泄漏。
 *   总堆用 heap_caps_get_total_size(MALLOC_CAP_DEFAULT)——必须与上面
 *   两个函数**同口径**（它们内部都是 MALLOC_CAP_DEFAULT），否则
 *   "总-剩余"算出来的已用值会离谱。ESP32-S3-EYE 带 8MB PSRAM，
 *   所以这里总量约 8MB 而不是内部 RAM 的几百 KB。
 *
 * 这两个量与 IMU 一起上报，网页上就能同时看到"被测对象的物理量"
 * 和"设备自身的健康度"。初始化失败不影响主链路，只是字段报 null。
 * ================================================================ */
static temperature_sensor_handle_t s_tsens = NULL;

static void temp_sensor_init(void)
{
    /* 量程必须完整落在芯片预定义档位内，否则 install 会报
     * "Out of testing range" + ESP_ERR_INVALID_ARG（实测踩过）。
     * ESP32-S3 只有 5 档：50~125 / 20~100 / -10~80 / -30~50 / -40~20，
     * 其中 -10~80 误差最小(±1°C)，且完全覆盖芯片工作温区(通常 40~60°C)。 */
    temperature_sensor_config_t cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
    esp_err_t err = temperature_sensor_install(&cfg, &s_tsens);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "温度传感器 install 失败: %s (%d), temp_c 将上报 null",
                 esp_err_to_name(err), err);
        s_tsens = NULL;
        return;
    }
    err = temperature_sensor_enable(s_tsens);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "温度传感器 enable 失败: %s (%d), temp_c 将上报 null",
                 esp_err_to_name(err), err);
        s_tsens = NULL;
        return;
    }
    float t = 0;
    err = temperature_sensor_get_celsius(s_tsens, &t);
    ESP_LOGI(TAG, "温度传感器就绪，当前 %.1f C (读取返回 %s)", t, esp_err_to_name(err));
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    temp_sensor_init();
    i2c_master_init();
    i2c_scan();

    if (!qma6100p_init()) {
        ESP_LOGE(TAG, "IMU 初始化失败，停机（不发送假数据）");
        return;
    }
    imu_identify();

    wifi_init_sta();
    /* 等 WiFi 拿到 IP */
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT,
                        pdFALSE, pdTRUE, portMAX_DELAY);

    /* 摄像头视频流是独立功能：就算它挂了，也不许影响 IMU 上报链路 */
    if (camera_stream_start() != ESP_OK) {
        ESP_LOGW(TAG, "摄像头未就绪，IMU 上报链路继续工作");
    }

    uint32_t seq = 0;
    while (1) {
        uint8_t raw[6];
        if (qma_read(0x01, raw, sizeof(raw)) != ESP_OK) {
            ESP_LOGE(TAG, "读取加速度计数据寄存器(0x01)失败");
            vTaskDelay(pdMS_TO_TICKS(POST_INTERVAL_MS));
            continue;
        }

        /* QMA6100P：0x01 起 6 字节 = [DXL,DXM,DYL,DYM,DZL,DZM]，LSB 高位含 NEWDATA 标志 */
        int16_t ax_raw = qma_assemble14(raw[0], raw[1]);
        int16_t ay_raw = qma_assemble14(raw[2], raw[3]);
        int16_t az_raw = qma_assemble14(raw[4], raw[5]);

        float ax = (ax_raw + CAL_X_OFF) / QMA_SENS_LSB_PER_G;
        float ay = (ay_raw + CAL_Y_OFF) / QMA_SENS_LSB_PER_G;
        float az = (az_raw - CAL_Z_OFF) / (QMA_SENS_LSB_PER_G * CAL_Z_SCALE);

        /* —— 读设备自身状态：芯片温度 + 堆内存 ——
         * 温度读不到时上报 JSON 的 null（而不是 0），免得网页显示 "0 °C"
         * 这种看起来像真的、其实是假的读数。 */
        float temp_c = 0.0f;
        char tbuf[16];
        bool temp_ok = (s_tsens != NULL) &&
                       (temperature_sensor_get_celsius(s_tsens, &temp_c) == ESP_OK);
        snprintf(tbuf, sizeof(tbuf), temp_ok ? "%.1f" : "null", temp_c);

        uint32_t free_heap  = esp_get_free_heap_size();
        uint32_t min_free   = esp_get_minimum_free_heap_size();
        uint32_t total_heap = heap_caps_get_total_size(MALLOC_CAP_DEFAULT);

        /* —— WiFi 信号强度 RSSI（dBm，越接近 0 越好）——
         * 数据断流时第一个该看的就是它：-60 以上很好，-70 尚可，
         * -80 以下基本必然丢包。信号差要先改善物理位置，改代码没用。 */
        wifi_ap_record_t ap = {0};
        char rbuf[8];
        bool rssi_ok = (esp_wifi_sta_get_ap_info(&ap) == ESP_OK);
        snprintf(rbuf, sizeof(rbuf), rssi_ok ? "%d" : "null", (int)ap.rssi);

        char payload[384];
        int n = snprintf(payload, sizeof(payload),
                         "{\"device_id\":\"%s\",\"seq\":%lu,\"ts\":%lld,"
                         "\"ax\":%.4f,\"ay\":%.4f,\"az\":%.4f,"
                         "\"gx\":0.00,\"gy\":0.00,\"gz\":0.00,"
                         "\"temp_c\":%s,\"free_heap\":%lu,\"min_free_heap\":%lu,"
                         "\"total_heap\":%lu,\"rssi\":%s}",
                         DEVICE_ID, (unsigned long)seq,
                         (long long)(esp_timer_get_time() / 1000),
                         ax, ay, az,
                         tbuf,
                         (unsigned long)free_heap, (unsigned long)min_free,
                         (unsigned long)total_heap, rbuf);
        if (n > 0 && n < (int)sizeof(payload)) {
            float norm = sqrtf(ax * ax + ay * ay + az * az);
            ESP_LOGI(TAG, "raw=[%d %d %d] ax=%.3f ay=%.3f az=%.3f |a|=%.3f"
                          " | %sC 信号%sdBm 堆 %.2fMB(已用%.2f/共%.2f, 最低%.2fMB)",
                     ax_raw, ay_raw, az_raw, ax, ay, az, norm,
                     tbuf, rbuf,
                     free_heap / 1048576.0,
                     (total_heap - free_heap) / 1048576.0,
                     total_heap / 1048576.0,
                     min_free / 1048576.0);
            http_post_json(payload);
        }

        seq++;
        vTaskDelay(pdMS_TO_TICKS(POST_INTERVAL_MS));
    }
}
