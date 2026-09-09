# AI Passport 官方玩法随机 10 项固件回归报告

## 结论

修复 QEMU 的 ESP32-C3 GPIO0 释放态后，随机抽取的 10 个官方玩法全部通过真实固件运行测试：10/10。

测试链路为：官方目录元数据 → 已下载官方固件 → SHA-256/ESP32-C3 镜像校验 → QEMU 启动 → 真实 framebuffer → JSON Lines 按键输入。

## 抽样与判定

- 官方接口：`https://ai-passport.folotoy.cn/api/plays/catalog`
- 刷新目录数量：160
- 随机种子：`16581439023348564508`
- 固件来源：官方目录下载结果，未生成或替换固件内容
- 通过条件：哈希一致、完整合并镜像可启动、QEMU 运行状态正常、捕获真实 240×320 RGB565 帧、输入桥接受 OK PASS、无恢复分区误启动日志

## 结果

| ID | slug | 固件大小 | 镜像格式 | QEMU | framebuffer | 帧 SHA-256（完整 RGB565） |
|---:|---|---:|---|---|---|---|
| 5 | `answer-book` | 2,893,040 | esp-idf-merged | running | 240×320，153,600 bytes | `d143c450e77ad859ac5ec9ee6f810e00eb5345aa8e4cb21dfed8691800c333e4` |
| 6 | `f1-pit-wall` | 1,783,328 | esp-idf-merged | running | 240×320，153,600 bytes | `49d768a7eacae174a0bb8057675a9e7a29390440656e637db54a880796103cf6` |
| 7 | `community-09fdfe06` | 801,328 | esp-idf-merged | running | 240×320，153,600 bytes | `2b2fbdbde0f7f235617595cd2b98af587f33037f0f8a99b2358afd1e2e82ebd5` |
| 8 | `ueg` | 2,638,560 | esp-idf-merged | running | 240×320，153,600 bytes | `6f69db0c3128ebf25d857d8c1839cd87b9672d92fde45dd4ea0db1490052f56` |
| 9 | `community-9b881111` | 646,640 | esp-idf-merged | running | 240×320，153,600 bytes | `117aee7fcb163d0d0c92d32802261b6290c9ebb5b7929285542a2d89c05b02ba` |
| 10 | `60` | 1,654,912 | esp-idf-merged | running | 240×320，153,600 bytes | `492e5423ed98e3967e905cdc5643a40b72cd5e10782740393a8b32aaecbdfa1f` |
| 11 | `community-d1480fc8` | 1,450,496 | esp-idf-merged | running | 240×320，153,600 bytes | `63057ce3095b0945a86896bf849587512372623c482b3744a30b908306026ebd` |
| 12 | `passport-radar` | 1,105,040 | esp-idf-merged | running | 240×320，153,600 bytes | `49287d2243beb84cf99875ab4d513bc5f263c9fe2a774e51b5a9f5b343f6a0b5` |
| 13 | `community-93d969a0` | 2,653,248 | esp-idf-merged | running | 240×320，153,600 bytes | `7dd1dd5765e2a99c7d7f8b84660eb747fedff6479b0eabbbff232badde16a979` |
| 14 | `community-a50f5993` | 1,320,672 | esp-idf-merged | running | 240×320，153,600 bytes | `20880688eef6a5c852a67763d0e56e6df8e711141a71cb31809c1bc4f7cae779` |

每项的镜像 SHA-256、标题、作者和下载记录见 [notes.md](../notes.md)。

## 失败原因与修复

修复前，5 个项目在启动约 5 秒后出现：

```text
recovery_boot: UP held: booting permanent recovery at 0x700000
boot: No bootable app partitions in the partition table
```

官方恢复钩子使用 GPIO0 低电平检测 UP 长按。QEMU GPIO 模型将复位后的 `gpio_in` 默认为 0，导致“未按键”被识别为长按。修复包括：

1. ESP32-C3 GPIO 复位后将 GPIO0 置为高电平释放态。
2. 虚拟按键按下/释放同步驱动 GPIO0 和 ADC 按键梯形输入。
3. 重新编译并安装内置 QEMU；macOS 安装使用无 FinderInfo/ResourceFork 的字节流方式，避免执行文件卡在 `dyld_start`。

## 服务端验证

通过当前 Flask 服务创建会话并调用 `POST /api/firmware/6/run`：

- 返回状态：`running`
- 输入桥：`json-lines`
- framebuffer：`240×320`，Base64 长度 `204800`
- 会话屏幕类型：`firmware`

这确认浏览器使用的是固件执行后端返回的真实帧，而不是前端内置演示画面。

## 最终工程检查

- `./simulator/tools/prepare_qemu_runtime.sh`：通过，QEMU 9.2.2 与 `esp32c3` 机型检查通过。
- `simulator/web/.venv/bin/pytest -q simulator/web/tests simulator/worker`：通过（退出码 0）。
- `node --check simulator/web/app/static/app.js`：通过。
- Python 模块编译检查：通过。
