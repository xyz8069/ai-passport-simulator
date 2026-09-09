<p align="right">
  <a href="DEPLOY.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Deploying the simulator

The simulator ships as a self-contained Docker image: the multi-stage build
compiles the AI Passport QEMU customization from the published source patch
and installs it at the app's bundled runtime path, so the deployed instance
behaves exactly like a local one — real firmware, real framebuffer, JSON
Lines input bridge enabled.

## Requirements

- A VPS or home server with Docker (2 GB RAM minimum recommended; 4 GB is
  comfortable). x86_64 and aarch64 both work — QEMU is compiled during the
  image build.
- Ports: the app listens on `8000` inside the container; put a TLS reverse
  proxy in front for anything public (example below).

## First deployment

```bash
git clone https://github.com/xyz8069/ai-passport-simulator.git
cd ai-passport-simulator

AI_PASSPORT_SECRET_KEY="$(openssl rand -hex 32)" docker compose up -d --build
```

The first build takes roughly 15–40 minutes (it compiles QEMU from source);
later rebuilds are fast thanks to layer caching. Check progress with
`docker compose logs -f`.

Then open `http://<your-server>:8000` and verify:

```bash
curl http://127.0.0.1:8000/api/health
# {"status": "ok", "service": "ai-passport-web-simulator"}

docker compose exec simulator \
  .runtime/qemu-esp32c3/qemu/bin/qemu-system-riscv32 -machine help | grep esp32c3
# esp32c3          RISC-V ESP32-C3 (should print exactly this line)
```

## TLS with a reverse proxy

Do not expose plain HTTP beyond a private network. The simplest automatic
HTTPS is Caddy:

```text
# /etc/caddy/Caddyfile
simulator.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

`docker compose` maps port 8000 on the host; point Caddy (or nginx) at
`127.0.0.1:8000`. SSE works through both without extra configuration; if you
add buffering headers in nginx, set `proxy_buffering off;` for the SSE
endpoint (`/api/...` stream routes).

## What you should know before going public

- **No authentication.** Anyone who can reach the site can upload firmware
  and run it in QEMU. Uploads are capped at 8 MiB, and firmware executes
  inside QEMU — that emulation boundary is the sandbox, nothing outside it.
  If you need stricter control, put the site behind your proxy's basic auth
  or an IP allowlist.
- **One web process owns all QEMU children.** The image runs exactly one
  gunicorn worker (`--workers 1` is load-bearing). Threads handle concurrent
  visitors; `--timeout 0` keeps SSE streams alive. Do not scale workers.
- **Resource limits** are set in `docker-compose.yml` (`mem_limit: 1g`,
  `cpus: 2`). Each active session runs one Python worker plus one QEMU
  process while firmware executes; raise the limits for busier demos.
- **Data** (sessions, validated firmware, snapshots, input logs) lives in the
  `simulator-instance` Docker volume. Back it up with:

  ```bash
  docker run --rm -v ai-passport-simulator_simulator-instance:/data \
    -v "$PWD":/backup alpine tar czf /backup/instance-data.tgz -C /data .
  ```

## Updating

```bash
git pull
AI_PASSPORT_SECRET_KEY="$(openssl rand -hex 32)" docker compose up -d --build
```

Running firmware does not survive a restart by design: the app clears
persisted RUNNING markers at boot because the new web process cannot re-attach
to the old QEMU children. Sessions and artifacts are kept.

## Troubleshooting

- **Build fails with OOM** — the QEMU compile peaks around 1.5–2 GB per
  `make -j` job. Add swap on small VPS boxes, or lower parallelism by
  replacing `make -j"$(nproc)"` with `make -j2` in the Dockerfile.
- **`DISPLAY OUTPUT UNAVAILABLE`** — expected on backends without the display
  bridge. In this image the AI Passport QEMU build is bundled, so real frames
  should appear; if not, check the log panel for QEMU startup errors.
- **Health check fails** — `docker compose logs simulator`; the most common
  cause is a corrupted instance volume, which a fresh volume resolves.

## No-server alternative

For a free managed deployment of the same image,
[Hugging Face Spaces](https://huggingface.co/spaces) (Docker SDK) works with
minor tweaks — the same Dockerfile builds there. Keep in mind Spaces sleep
after 48 hours of inactivity and are fully public.
