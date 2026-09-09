# 模拟器显示性能优化报告

## 目标

降低真实固件 framebuffer 在 Flask/SSE/Canvas 链路中的延迟，避免网页显示被 1 FPS 轮询限制，并减少重复帧和遗留 QEMU 进程对性能的影响。

## 根因

- 前端 `pollFirmwareRun()` 原来每 1 秒请求一次运行状态，并在每次请求中同步传输、Base64 解码和绘制约 200KB 的完整 framebuffer。
- QEMU 的 ST7789 适配层在完整窗口写入时可能绕过节流，重复编码相同 framebuffer。
- SSE 生成器在流式请求上下文退出后访问 `current_app`，新增高频推送后会触发 500。
- 删除 Web 会话时原来只停止 Worker，没有停止对应固件 QEMU 子进程，反复运行会积累后台进程。

## 已完成优化

### QEMU

- ST7789 输出改为仅在 framebuffer 内容发生变化时发送。
- 增加 `frame_revision`，便于服务端和前端识别新帧。
- 固定完整帧输出节奏为约 33ms 上限，避免 stdout 被高频整帧写入拖慢 guest。
- 将临时调试输出上限从 1200 条降至 64 条。
- 优化后的可执行文件已部署到：

  `.runtime/qemu-esp32c3/qemu/bin/qemu-system-riscv32`

### Flask / SSE

- SSE 直接读取固件运行器的最新 framebuffer 和 `frame_revision`，不再等待 1 秒轮询。
- 服务端只保留并推送最新帧，避免积压旧帧。
- 增加 `include_frame=0` 运行状态接口选项，右侧状态轮询不再携带完整 framebuffer。
- 修复流式生成器的 Flask 应用上下文生命周期问题。
- 删除会话时同步停止关联固件 QEMU 进程，避免后台进程泄漏。

### 浏览器 Canvas

- 使用 `requestAnimationFrame` 调度绘制，每次只绘制最新待处理帧。
- 跳过相同 `frame_revision` / framebuffer 的重复解码。
- RGB565 转 RGBA 使用预计算查找表，减少每像素浮点计算。
- framebuffer 推送期间不重复刷新右侧日志和遥测 DOM。

## 回归结果

测试固件：官方目录中的真实 `community-a50f5993.bin`，通过内置 ESP32-C3 QEMU 启动。

| 指标 | 优化前 | 优化后 |
|---|---:|---:|
| 浏览器运行状态轮询间隔 | 1000ms | 2000ms，仅用于状态兜底 |
| 真实画面传输通道 | 轮询响应 | SSE 最新帧 |
| 3 秒 SSE 数据事件 | 不适用 | 35 |
| 3 秒真实新帧 | 约受 1 秒轮询限制 | 34 |
| 实测 framebuffer 推送帧率 | 约 1 FPS 上限 | 约 11.33 FPS |
| 单帧格式 | 240×320 RGB565 | 240×320 RGB565 |
| 画面来源 | 真实固件 framebuffer | 真实固件 framebuffer |

优化后的帧序号从 `1` 连续增长到 `34`，未出现重复帧覆盖或 SSE 500。

## 自动化验证

- Web / Worker 全量测试：35 项通过。
- `node --check simulator/web/app/static/app.js`：通过。
- Python 模块编译检查：通过。
- 内置 QEMU 重新编译：通过。
- `/api/health`：返回 `ok`。

## 使用说明

Flask 服务已重新运行于 `http://127.0.0.1:5050/`。如果浏览器仍显示重启前的旧会话，请刷新页面以创建新的会话；之后加载并运行固件即可使用新的 SSE 帧通道。
