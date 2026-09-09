# 第1周 · 开发板传感数据采集与 Web 展示（运行说明）

目标：真实传感器（IMU）→ ESP32-S3-EYE 采集 → **独立联网（WiFi）**上传到自己的服务器 → 网页实时展示并验证。

当前状态：✅ **真机链路全部打通**。ESP32-S3-EYE 板载 QMA7981 加速度计以 1Hz 通过 WiFi HTTP 上报，服务端存储并提供查询接口，网页实时展示。

> 部署说明：本组暂无 VPS，按任务卡"备用路径"以本地电脑（局域网 IP `10.1.41.18`）充当服务器，**VPS 部署待补**。板端为 WiFi 独立联网上传，非 USB 桥接（USB 串口仅用于看日志）。

## 目录结构

```
week1/
├── server/server.py        接收 + 存储 + 查询服务（Python 标准库，零依赖）
├── web/index.html          展示页面（无 CDN，纯原生；API_BASE 固定指向 127.0.0.1:8000）
├── sim/esp32_sim.py        板端模拟器（无硬件时联调用，已在使用阶段验证过）
├── firmware/esp32_imu/     ESP-IDF 5.4.4 工程（真板子，已烧录运行）
│   ├── main/main.c         主程序：QMA7981 裸驱动 + WiFi + HTTP 上报（含实测标定）
│   └── run_idf.py          编译启动器（自动补齐工具链环境变量，绕开 Git Bash 环境坑）
├── data/readings.db        SQLite 数据库（真机数据 5400+ 条）
├── debug_logs/             编译/烧录日志与 IMU 标定实验原始数据（排错过程证据）
└── docs/esp32-s3-eye_manual.pdf  开发板原理图/手册
```

## 快速开始（当前真机环境）

```bash
# 1) 起服务（Windows，任意 Python 3.8+，零依赖）
python D:\aijiaohu\week1\server\server.py          # 监听 0.0.0.0:8000

# 2) 浏览器打开页面
http://127.0.0.1:8000     （或直接双击 web/index.html，页面会连 127.0.0.1:8000）

# 3) 板子上电后自动连 WiFi("431") 并开始上报，无需人工干预
```

板端固件已烧录在开发板内；如需重烧，见下文"固件编译烧录"。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/data` | 板端上传一条记录，字段：`device_id`(必填)、`seq`、`ts`、`ax/ay/az`、`gx/gy/gz` |
| GET | `/api/latest` | 最新一条 + `age_seconds` + `stale`（是否超阈值） |
| GET | `/api/history?limit=20` | 最近 N 条 |
| GET | `/api/devices` | 出现过的设备及其最后上报时间 |
| GET | `/` | 展示页面 |

服务端额外记录 `server_ms`（收到时刻）和 `src_ip`（来源 IP）——这两个字段是"数据来自本组真实设备"的证据（板子 WiFi IP：`10.1.41.160`）。

## 页面功能

- 三轴加速度实时数值卡片（单位 g，已标定：静止合加速度 ≈ 1.00g）
- 角速度三轴为 0：**板载 IMU 为 QMA7981，仅加速度计，无陀螺仪**（硬件限制，见观测记录说明）
- 最近 60 条趋势曲线（加速度/角速度切换，原生 canvas，无外部依赖）
- 数据年龄与「未更新」提示（超 5 秒判定过期）
- 最近 8 条服务端原始记录表格（含服务器时间、来源 IP）

## 验收演练（老师要看的）

1. 打开页面 → 数值实时刷新，板子静置时 |a|≈1.00g
2. 拿起开发板晃动/翻转 → 数值与曲线同步变化
3. **拔掉板子 USB 供电** → 数值冻结、时间停在最后一刻
4. 等 5 秒 → 页面提示「未更新 · 已 N 秒」

第 3~4 步是核心：证明页面数值不是写死的，而是真的来自一台会停掉的设备。

## 固件编译烧录（ESP32-S3-EYE）

1. 固件宏在 `firmware/esp32_imu/main/main.c` 顶部：`WIFI_SSID`("431")、`WIFI_PASS`、`SERVER_URL`(http://10.1.41.18:8000/api/data)、`DEVICE_ID`
2. 编译（本项目用 `run_idf.py` 启动器，解决 Git Bash 环境下 MSYSTEM/代理/工具链路径问题）：

```bash
cd D:\aijiaohu\week1\firmware\esp32_imu
D:\gongju\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe run_idf.py build
D:\gongju\Espressif\python_env\idf5.4_py3.11_env\Scripts\python.exe run_idf.py -p COM5 flash
```

3. 串口 `COM5`（ESP32-S3 内置 USB-JTAG/串口），波特率 115200，仅供看日志

## 本组个人修改记录（相对通用示例的关键改动）

1. **QMA7981 裸驱动**：官方 BSP 无此芯片驱动，按数据手册 + 参考开源驱动自行实现 I2C 读写、初始化、数据组装（14 位补码，`(msb<<6)|(lsb&0x3F)`，LSB bit7 为 NEWDATA 标志）
2. **实测排错**（详见 `debug_logs/`）：
   - chip_id 实测 0x90（非资料的 0xE7）：新版硅片，只提示不拦截
   - Y 轴上电冻结：写入不生效，软复位（0x36←0xB6）解决
   - 量程/带宽寄存器合法值与旧资料不符：±2g=0x01、BW=0b101（1024Hz）
   - 零偏偏大（X -0.55g / Y -0.15g / Z +0.39g，Z 灵敏度 0.912）：静止两点法实测标定，标定后静止 |a|=0.99~1.00
   - I2C 扫描必须用 1 字节真实读，零长度读全程报 `i2c data read length error`
3. **服务端增强**：`stale` 判定、来源 IP 记录、CORS 放行（支持页面跨端口访问）
4. **页面增强**：API_BASE 固定指向数据服务端口，避免静态预览时请求指错

## 常见坑

- **Windows 防火墙拦了**：本机浏览器先访问 `http://10.1.41.18:8000` 验证，不通就给 Python 放行
- **时间对不上**：板子没有 RTC，`ts` 是上电毫秒数；权威时间是服务端的 `server_ms`
- **5GHz WiFi 连不上**：ESP32 只支持 2.4GHz
- **USB 串口只用于看日志调试**，不能算"开发板独立联网上传"（本项目走 WiFi，合规）

## 提交清单

- [x] 板端代码（`firmware/esp32_imu/`）
- [x] 服务端代码（`server/server.py`）
- [x] 网页代码（`web/index.html`）
- [x] 一条真实现测记录：见 `观测记录.md`
- [ ] 页面截图 2 张（正常态 / 停采未更新态）—— 需自行截取
- [ ] VPS 部署（按备用路径声明"本地服务定位，部署待补"）
