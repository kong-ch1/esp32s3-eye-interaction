/* 第1步最小版：只做一件事 —— 读出加速度计的数字，通过串口打印到电脑。
 * 不连 WiFi、不发包、不开摄像头。先确认"传感器 -> 板子"这条路是通的。
 *
 * 串口是什么：板子上有根 USB 线连着电脑，板子可以用它把文字"说"给电脑听。
 * 我们用 ESP_LOGI 打印，电脑上用串口监视器就能看到这些字。
 */

#include <stdio.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "driver/i2c.h"

/* ---- 硬件接线（ESP32-S3-EYE 板子固定好的，不用改）---- */
#define I2C_SDA_GPIO  4        // I2C 数据线接到 GPIO4
#define I2C_SCL_GPIO  5        // I2C 时钟线接到 GPIO5
#define I2C_PORT      I2C_NUM_0
#define I2C_FREQ_HZ   400000   // I2C 通信速率 400kHz
#define QMA6100P_ADDR 0x12     // 加速度计在 I2C 总线上的"门牌号"

/* 灵敏度：±2g 量程下，每 1g 对应的原始计数。
 * 14 位 ADC，±2g 跨度是 4g，满量程 16384 计数 => 16384 / 4 = 4096 计数每 g。 */
#define QMA_SENS_LSB_PER_G 4096.0f

static const char *TAG = "step1";  // 打印时前面会带这个标签，方便认

/* ---- 工具函数：往传感器某个寄存器写 1 个值 ----
 * 传感器内部是一排带编号的小抽屉（寄存器），要配置它就得往指定编号写值。 */
static esp_err_t qma_write_reg(uint8_t reg, uint8_t val)
{
    uint8_t buf[2] = { reg, val };   // 第1字节=抽屉编号，第2字节=要写的值
    return i2c_master_write_to_device(I2C_PORT, QMA6100P_ADDR, buf, 2, pdMS_TO_TICKS(100));
}

/* ---- 工具函数：从传感器某个寄存器读 len 个字节 ---- */
static esp_err_t qma_read(uint8_t reg, uint8_t *out, size_t len)
{
    // 先发 1 字节"从哪个寄存器开始读"，再收 len 字节数据
    return i2c_master_write_read_device(I2C_PORT, QMA6100P_ADDR, &reg, 1, out, len, pdMS_TO_TICKS(100));
}

/* ---- 初始化加速度计：唤醒它、设好量程 ---- */
static bool qma6100p_init(void)
{
    uint8_t chip_id = 0;
    if (qma_read(0x00, &chip_id, 1) != ESP_OK) {
        ESP_LOGE(TAG, "读不到传感器(0x%02x)，检查接线", QMA6100P_ADDR);
        return false;
    }
    ESP_LOGI(TAG, "chip_id = 0x%02x", chip_id);

    // 软复位：有些板上电后寄存器写不进去，复位一下才正常
    qma_write_reg(0x36, 0xB6);
    vTaskDelay(pdMS_TO_TICKS(20));
    qma_write_reg(0x36, 0x00);
    vTaskDelay(pdMS_TO_TICKS(20));

    qma_write_reg(0x11, 0xC0);  // 退出睡眠，进入工作模式
    vTaskDelay(pdMS_TO_TICKS(20));
    qma_write_reg(0x0F, 0x01);  // 量程设 ±2g
    qma_write_reg(0x10, 0x05);  // 采样带宽 1024Hz
    vTaskDelay(pdMS_TO_TICKS(50));
    return true;
}

/* ---- 把 14 位原始值拼回有符号整数 ----
 * 按 QMA6100P datasheet：每个轴 14 位，分布在两个字节里：
 *   高字节 MSB[7:0] = data[13:6]   （数据的高 8 位）
 *   低字节 LSB[7:2] = data[5:0]     （数据的低 6 位，bit1/bit0 是状态位不是数据）
 * 所以低字节要 >>2 把数据挪到最低 6 位，再和高字节拼起来。 */
static inline int16_t qma_assemble14(uint8_t lsb, uint8_t msb)
{
    int16_t v = (int16_t)(((uint16_t)msb << 6) | ((lsb >> 2) & 0x3F));
    if (v & 0x2000) v -= 0x4000;  // 第 13 位是符号位，置 1 表示负数
    return v;
}

void app_main(void)
{
    // 1) 接通 I2C 总线（相当于把板子和传感器之间的"电话线"接好）
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,        // 板子当主机，主动发起通信
        .sda_io_num = I2C_SDA_GPIO,
        .scl_io_num = I2C_SCL_GPIO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_FREQ_HZ,
    };
    ESP_ERROR_CHECK(i2c_param_config(I2C_PORT, &conf));
    ESP_ERROR_CHECK(i2c_driver_install(I2C_PORT, conf.mode, 0, 0, 0));

    // 2) 初始化传感器
    if (!qma6100p_init()) {
        ESP_LOGE(TAG, "传感器初始化失败，停机");
        return;
    }

    // 3) 主循环：每秒读一次，把数字打印出来
    while (1) {
        uint8_t raw[6];
        if (qma_read(0x01, raw, 6) != ESP_OK) {  // 0x01 起 6 字节 = X低,X高,Y低,Y高,Z低,Z高
            ESP_LOGE(TAG, "读数据失败");
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        int16_t ax_raw = qma_assemble14(raw[0], raw[1]);
        int16_t ay_raw = qma_assemble14(raw[2], raw[3]);
        int16_t az_raw = qma_assemble14(raw[4], raw[5]);

        // 原始计数 ÷ 每 g 计数 = 以 g（重力加速度）为单位的数值
        float ax = ax_raw / QMA_SENS_LSB_PER_G;
        float ay = ay_raw / QMA_SENS_LSB_PER_G;
        float az = az_raw / QMA_SENS_LSB_PER_G;

        // |a| 是三轴合成的大小。板子平放不动时理论约等于 1.0（地球重力）
        ESP_LOGI(TAG, "ax=%.3f ay=%.3f az=%.3f |a|=%.3f",
                 ax, ay, az, sqrtf(ax*ax + ay*ay + az*az));
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
