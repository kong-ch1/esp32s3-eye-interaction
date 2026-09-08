# 第1周 · 开发板传感数据采集与 Web 展示

目标：真实传感器（IMU）→ ESP32 采集 → **独立联网**上传到自己的服务器 → 网页实时展示并验证。

目前状态：**没有硬件也能跑**。用 `sim/esp32_sim.py` 顶替开发板，服务端与网页先完整跑通；拿到板子后换成 `firmware/esp32_imu` 烧进去即可。

## 目录结构

```
week1/
├── server/server.py        接收 + 存储 + 查询服务（Python 标准库，零依赖）
├── web/index.html          展示页面（无 CDN，纯原生）
├── sim/esp32_sim.py        板端模拟器，代替真板子发数据
├── firmware/esp32_imu/     ESP-IDF 工程，真板子用（待实机验证）
└── data/readings.db        运行后自动生成的 SQLite 数据库
```

## 快速开始

```bash
# 1) 起服务（默认 8000 端口）
python server/server.py --port 8000

# 2) 另开一个终端，跑模拟器顶替板子
python sim/esp32_sim.py --device team01-esp32 --interval 1.0

# 3) 浏览器打开
http://127.0.0.1:8000
```

不需要 `pip install` 任何东西，Python 3.8+ 就能跑。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/data` | 板端上传一条记录，字段：`device_id`(必填)、`seq`、`ts`、`ax/ay/az`、`gx/gy/gz` |
| GET | `/api/latest` | 最新一条 + `age_seconds` + `stale`（是否超阈值） |
| GET | `/api/history?limit=20` | 最近 N 条 |
| GET | `/api/devices` | 出现过的设备及其最后上报时间 |
| GET | `/` | 展示页面 |

服务端会额外记录 `server_ms`（收到时刻）和 `src_ip`（来源 IP），**这两个字段是"数据来自真实设备"的证据**。

## 页面功能

- 六轴实时数值卡片（加速度 g、角速度 °/s）
- **最近 60 条趋势曲线**，可在「加速度 / 角速度」间切换（原生 canvas 绘制，无任何外部依赖）
- 数据年龄与「未更新」提示（默认超 5 秒判定过期）
- 最近 8 条服务端原始记录表格（含服务器时间、来源 IP）

## 验收演练（老师要看的）

1. 打开页面 → 六轴数值在动，右上角绿色「实时更新中」
2. 让模拟器数值变化（修改 `--interval` 或晃动模型参数）→ 页面同步变化
3. **Ctrl+C 停掉模拟器** → 数值立即冻结
4. 等 5 秒 → 右上角变红「未更新 · 已 N 秒」

第 4 步是核心：证明页面数值不是写死的，而是真的来自一个会停掉的设备。

## 提交清单

- [ ] 板端代码（`firmware/esp32_imu/`）
- [ ] 服务端代码（`server/server.py`）
- [ ] 网页代码（`web/index.html`）
- [ ] 一条真实现测记录：截图 + 服务端 `data/readings.db` 中对应那一行
- [ ] 个人修改与运行说明

## 换成真板子

1. 改 `firmware/esp32_imu/main/main.c` 顶部 5 个宏：WiFi 名、密码、服务器地址、设备 ID、I2C 引脚
2. 服务器地址必须是**电脑的局域网 IP**（`ipconfig` 查），不能写 127.0.0.1
3. 板子和电脑连同一个 WiFi
4. 编译烧录：

```bash
idf.py set-target esp32
idf.py build
idf.py -p COM3 flash monitor
```

5. 板子跑起来后，服务端终端会打印 `POST /api/data`，页面出现数据

## 常见坑

- **Windows 防火墙拦了**：板子死活连不上时，先在本机浏览器访问 `http://<你的局域网IP>:8000` 试试，访问不了就是防火墙；给 Python 放行专用/公用网络，或临时关防火墙验证。
- **端口不通**：确认服务启动时用的 `--host 0.0.0.0`（默认即是），只监听 127.0.0.1 的话板子连不进来。
- **时间对不上**：板子没有 RTC，`ts` 是上电毫秒数；权威时间是服务端的 `server_ms`，页面显示的也是服务器时间。
- **USB 串口只用于看日志调试**，不能算"开发板独立联网上传"。
