<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# AI Passport 模拟器

一个基于浏览器的固件运行工作台：把 [FoloToy AI Passport](https://github.com/FoloToy/ai-passport)
的**真实 ESP32-C3 固件**交给可插拔的 QEMU 后端执行，并将真实的
`240×320 RGB565` framebuffer 转发到浏览器中的设备外观画布上。

## 功能

- 官方设备外观与三枚虚拟按键（UP / OK / DOWN）。
- ESP32-C3 `.bin` 上传与镜像结构校验：bootloader、分区表、factory app、
  chip ID、checksum。
- 固件会话绑定：校验通过的镜像先绑定当前设备会话，再执行。
- 可插拔 QEMU 执行后端：运行、停止、轮询状态、串口日志流。
- 可选 framebuffer 行协议：后端输出 `{"type":"frame", ...}` JSON 行时，
  画布解码真实 RGB565 输出；没有输出时不绘制替代画面。
- 显示性能优化：QEMU 只推送变化的帧，SSE 实时转发最新帧，浏览器用
  `requestAnimationFrame` 丢弃过期帧，状态轮询可用 `include_frame=0`
  跳过大帧传输。
- 面向 AI Passport 的 QEMU 构建：SPI2/GDMA/ST7789 显示桥 + GPIO0 ADC
  按键桥（虚拟按键按下时同步驱动数字 GPIO 和 ADC 分压，匹配官方恢复
  钩子的检测方式）。
- SQLite 持久化会话、输入事件、快照和固件元数据；固件文件保存在实例目录。
- 官方玩法目录：集成官方玩法页面
  <https://ai-passport.folotoy.cn/plays/>。下载、校验、导入全部
  在服务端完成。

## 工作方式

```text
浏览器                        Flask Web 应用                   Worker 进程
┌─────────────────┐  HTTP/SSE   ┌──────────────────────┐        ┌──────────────────┐
│ 设备画布         │◄───────────►│ 上传 / 校验           │───────►│ QEMU (esp32c3)   │
│ 虚拟按键         │   JSON      │ 会话绑定              │ stdout │  真实固件         │
│ 状态面板         │             │ SQLite 持久化         │◄───────│  帧行 / GPIO0 ADC │
└─────────────────┘             │ 玩法目录导入          │ frame  └──────────────────┘
                                └──────────────────────┘
```

- Web 应用不直接执行固件；Worker 独占 QEMU 进程和它的生命周期。
- 屏幕只显示后端从运行中的固件捕获到的帧。
- 串口日志只作为日志展示，永远不会被渲染成屏幕画面。

## 快速开始

环境要求：Python 3.10+，以及下述任一 QEMU 后端。

```bash
# 1. Python 依赖
python3 -m venv web/.venv
. web/.venv/bin/activate
pip install -r web/requirements.txt

# 2. QEMU 后端（三选一）
tools/fetch_qemu_runtime.sh --upstream      # 官方 Espressif QEMU：可启动固件、有串口日志
#    或使用带显示桥和输入桥的 AI Passport 构建：
#      export AI_PASSPORT_QEMU_RELEASE_BASE="https://github.com/xyz8069/ai-passport-simulator/releases/download/qemu-v1"
#      tools/fetch_qemu_runtime.sh
#    或从源码构建：third_party/qemu/README.md

# 3. 启动
flask --app web.app run --debug --port 5050
```

打开 <http://127.0.0.1:5050>，上传 ESP32-C3 合并镜像（或从官方玩法目录导入），
点击 **运行**。

在 macOS 上，如果 QEMU 运行目录是从压缩包重新解压得到的，建议先执行一次
`tools/prepare_qemu_runtime.sh`：它会清除 FinderInfo/ResourceFork 扩展属性，
并以纯字节流重新物化 Mach-O 可执行文件，避免进程卡在 `dyld_start`。
Web 后端启动时也会对内置 QEMU 执行同样的单次自修复。

## 获取固件

- **官方玩法目录** —— 页面直接展示 `ai-passport.folotoy.cn` 的目录；
  "下载并加载/运行"全部经由服务端，先校验大小、SHA-256 和
  ESP32-C3 合并镜像结构，再绑定到会话。
- **自行构建** —— 克隆上游固件仓库
  [FoloToy/ai-passport](https://github.com/FoloToy/ai-passport)（MIT），用
  ESP-IDF 构建；模拟器需要完整合并镜像（`0x0` bootloader、`0x8000` 分区表、
  `0x10000` factory app）。
- 仅含 app 的镜像可以上传和分析，但会被拒绝执行。

## QEMU 后端与环境变量

| 变量 | 用途 |
|---|---|
| `AI_PASSPORT_QEMU` | 带 `esp32c3` 机型的 `qemu-system-riscv32` 绝对路径（或命令名） |
| `AI_PASSPORT_QEMU_DATA` | `esp32c3-rom.bin` 所在目录（本地构建或 bundle 布局时使用） |
| `AI_PASSPORT_QEMU_COMMAND` | 非标准后端的完整命令模板，包含 `{firmware}` 占位符 |
| `AI_PASSPORT_FIRMWARE_INPUT` | 为外部后端启用 JSON Lines 输入协议 |
| `AI_PASSPORT_QEMU_FLASH_SIZE` | 临时镜像的 Flash 大小（默认 8 MiB） |

后端解析顺序：`AI_PASSPORT_QEMU` → 内置 `.runtime/qemu-esp32c3` →
`PATH` 上的 `qemu-system-riscv32`。

## 输入协议

三个网页按键会记录到当前会话的输入事件流。AI Passport QEMU 构建自动启用
JSON Lines 输入协议，并把 UP/DOWN/OK 映射为真实 GPIO0 ADC 电阻梯脉冲。
外部后端可实现同一协议并用 `AI_PASSPORT_FIRMWARE_INPUT` 启用。输入只转发给
固件，不会自行改变屏幕状态。

## 开发

```bash
# 创建带开发工具的虚拟环境
pip install -r web/requirements-dev.txt

# 运行测试
. web/.venv/bin/activate
pytest -q web/tests worker/test_worker.py

# 前端语法检查
node --check web/app/static/app.js
```

CI 在 GitHub Actions 上执行同样的检查（见 `.github/workflows/ci.yml`）。

贡献者须知的项目规则：**绝不伪造设备输出。** 屏幕无法显示真实 framebuffer
时，就显示真实状态和真实原因。这条原则在代码评审中强制执行。

## 部署

仓库提供自带 Docker 部署镜像（`Dockerfile`、`docker-compose.yml`）：构建时会
根据已发布的补丁编译 AI Passport 定制 QEMU，可在自己的 VPS 上运行完整服务：

```bash
docker compose up -d --build   # 然后打开 http://<服务器>:8000
```

TLS、安全说明和资源规划见 [DEPLOY.zh_CN.md](DEPLOY.zh_CN.md)。线上实例支持
真实固件执行——与本地运行的同一个应用。

## 仓库结构

```text
web/          Flask 应用（路由、固件分析、玩法目录、模板、测试）
worker/       QEMU Worker 进程及测试
tools/        QEMU 运行时的下载 / 准备 / 打包脚本
third_party/  QEMU 定制补丁、构建说明、校验和（GPL 合规）
docs/         工程报告（显示性能、固件回归）
Dockerfile    自包含镜像（从已发布补丁构建 QEMU）
docker-compose.yml
DEPLOY.zh_CN.md  部署指南（中文） / deployment guide (EN)
```

## 第三方组件

- [FoloToy/ai-passport](https://github.com/FoloToy/ai-passport) —— 本模拟器
  面向的固件（MIT）。不包含在本仓库中；请从官方目录获取固件或自行构建。
- [Espressif QEMU fork](https://github.com/espressif/qemu) ——
  GPL-2.0-or-later。AI Passport 定制以源码补丁形式放在 `third_party/qemu/`，
  附构建说明；分发二进制时同时公开对应源码。
- 设备外观素材来自 AI Passport 项目。
- 玩法目录及其固件由 FoloToy 的服务提供。

## 许可证

本项目基于 [MIT License](LICENSE) 发布。第三方组件仍遵循上文所述的各自许可证。
