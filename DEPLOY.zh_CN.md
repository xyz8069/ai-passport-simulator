<p align="right">
  <strong>简体中文</strong> · <a href="DEPLOY.md">English</a>
</p>

# 部署模拟器

模拟器以自带 Docker 镜像交付：多阶段构建会根据已发布的源码补丁编译 AI
Passport 定制版 QEMU，并安装到应用的内置运行时路径。因此线上实例和本地完全
一致——真实固件、真实 framebuffer、JSON Lines 输入桥默认启用。

## 环境要求

- 一台装好 Docker 的 VPS 或家庭服务器（建议 2GB 内存起步，4GB 更从容）。
x86_64 和 aarch64 都可以——QEMU 在镜像构建时从源码编译。
- 端口：应用在容器内监听 `8000`；公开访问请务必加 TLS 反向代理（见下文）。

## 首次部署

```bash
git clone https://github.com/xyz8069/ai-passport-simulator.git
cd ai-passport-simulator

AI_PASSPORT_SECRET_KEY="$(openssl rand -hex 32)" docker compose up -d --build
```

首次构建大约需要 15–40 分钟（要从源码编译 QEMU），后续重建有层缓存，很快。
构建进度可用 `docker compose logs -f` 查看。

然后打开 `http://<你的服务器>:8000`，并做两项验证：

```bash
curl http://127.0.0.1:8000/api/health
# {"status": "ok", "service": "ai-passport-web-simulator"}

docker compose exec simulator \
  .runtime/qemu-esp32c3/qemu/bin/qemu-system-riscv32 -machine help | grep esp32c3
# esp32c3          RISC-V ESP32-C3 （应输出这一行）
```

## 用反向代理加 TLS

不要把纯 HTTP 暴露到公网。最简单的自动 HTTPS 是 Caddy：

```text
# /etc/caddy/Caddyfile
simulator.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

`docker compose` 已把容器的 8000 端口映射到宿主机；让 Caddy（或 nginx）指向
`127.0.0.1:8000` 即可。SSE 在两者下都开箱即用；如果用 nginx 并配置了缓冲，
请对 SSE 流式路由加 `proxy_buffering off;`。

## 公开部署前必读

- **没有登录鉴权。** 任何能访问站点的人都可以上传固件并在 QEMU 中运行。
  上传上限 8 MiB，固件在 QEMU 内执行——这层仿真边界就是沙箱，仅此而已。
  如需更严格的控制，请在反向代理层加 basic auth 或 IP 白名单。
- **一个 Web 进程持有全部 QEMU 子进程。** 镜像固定只跑一个 gunicorn
  worker（`--workers 1` 是关键参数，不能改），并发访客由线程承担，
  `--timeout 0` 保证 SSE 长连接不被杀。请勿扩 worker 数量。
- **资源限制** 写在 `docker-compose.yml`（`mem_limit: 1g`、`cpus: 2`）。
  每个活跃会话是一个 Python Worker，固件执行期间再加一个 QEMU 进程；
  访问量大时调高限额。
- **数据**（会话、已验证固件、快照、输入日志）保存在
  `simulator-instance` Docker 卷里。备份方式：

  ```bash
  docker run --rm -v ai-passport-simulator_simulator-instance:/data \
    -v "$PWD":/backup alpine tar czf /backup/instance-data.tgz -C /data .
  ```

## 升级

```bash
git pull
AI_PASSPORT_SECRET_KEY="$(openssl rand -hex 32)" docker compose up -d --build
```

运行中的固件按设计不跨重启：应用启动时会清掉持久化的 RUNNING 标记，因为新
Web 进程无法重新挂接旧的 QEMU 子进程。会话和固件数据会保留。

## 常见问题

- **构建时内存不足** —— QEMU 编译每个 `make -j` 任务峰值约 1.5–2 GB。小内存
  VPS 加 swap，或把 Dockerfile 里的 `make -j"$(nproc)"` 改成 `make -j2`。
- **屏幕显示 `DISPLAY OUTPUT UNAVAILABLE`** —— 没有显示桥的后端就是这样的，
  属于正常降级。本镜像内置 AI Passport 定制 QEMU，应能出真实画面；如果没出，
  看日志面板里 QEMU 的启动报错。
- **健康检查失败** —— `docker compose logs simulator`；最常见原因是实例卷
  损坏，换一个新卷即可恢复。

## 没有服务器的替代方案

同一个镜像也可以免费托管到
[Hugging Face Spaces](https://huggingface.co/spaces)（Docker SDK），Dockerfile
可直接构建。注意 Spaces 在 48 小时无访问后会休眠，且完全公开。
