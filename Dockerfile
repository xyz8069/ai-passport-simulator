# ---------------------------------------------------------------------------
# Stage 1 — build the AI Passport QEMU customization for Linux.
#
# Reproduces the macOS reference build described in third_party/qemu/README.md
# from the published source patch, so the image ships a GPL-compliant binary
# built from the same tree as the release assets.
# ---------------------------------------------------------------------------
FROM ubuntu:24.04 AS qemu-builder

ARG QEMU_BASE_COMMIT=febae182e132e4055529be423a818225ebddaa3a
# Source snapshot of ${QEMU_BASE_COMMIT}. Override when building on networks
# without GitHub access; the override tarball must include the subprojects
# meson would otherwise fetch via git (at least dtc and keycodemapdb).
ARG QEMU_SRC_URL=https://codeload.github.com/espressif/qemu/tar.gz/${QEMU_BASE_COMMIT}
# Optional Ubuntu apt mirror for constrained networks, e.g.
#   --build-arg APT_MIRROR=mirrors.aliyun.com
ARG APT_MIRROR=""
ENV DEBIAN_FRONTEND=noninteractive

RUN if [ -n "$APT_MIRROR" ]; then \
      sed -ri "s|//archive.ubuntu.com|//$APT_MIRROR|g; s|//security.ubuntu.com|//$APT_MIRROR|g" \
        /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true; \
      sed -ri "s|//archive.ubuntu.com|//$APT_MIRROR|g; s|//security.ubuntu.com|//$APT_MIRROR|g" \
        /etc/apt/sources.list 2>/dev/null || true; \
    fi \
 && apt-get update && apt-get install -y --no-install-recommends \
      build-essential git curl ca-certificates patch ninja-build pkg-config \
      python3 python3-venv python3-pip libslirp-dev \
      libglib2.0-dev libpixman-1-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
# Download the exact upstream base commit the patch was generated against.
RUN curl -fsSL -o /tmp/qemu-src.tar.gz "${QEMU_SRC_URL}" \
 && tar -xzf /tmp/qemu-src.tar.gz \
 && mv /build/qemu-* /build/qemu

COPY third_party/qemu/ai-passport-qemu-esp32c3.patch /tmp/qemu.patch
RUN cd /build/qemu \
 && patch -p1 < /tmp/qemu.patch \
 && ./configure --prefix=/opt/qemu \
      --target-list=riscv32-softmmu --disable-gtk --disable-sdl --disable-vnc \
      --disable-curl --disable-opengl --disable-virglrenderer \
      --disable-vhost-user --disable-xkbcommon --disable-docs \
      --disable-tools --disable-werror --disable-pie --disable-guest-agent \
      LDFLAGS="-no-pie" \
      --enable-fdt=internal --enable-plugins \
 && make -j"$(nproc)" \
 && make install

# ---------------------------------------------------------------------------
# Stage 2 — runtime image: Flask app + session workers + the built QEMU.
#
# The QEMU binary is installed at the simulator's bundled runtime path
# (<repo>/.runtime/qemu-esp32c3/qemu) so find_qemu() and the JSON Lines
# input bridge are enabled without extra configuration.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# Optional pip mirror for constrained networks, e.g.
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=""
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

# Inherited from the global scope; re-declared here because ARGs do not
# cross stage boundaries automatically.
ARG APT_MIRROR=""

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN if [ -n "$APT_MIRROR" ]; then \
      sed -ri "s|//deb.debian.org|//$APT_MIRROR|g" \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -ri "s|//deb.debian.org|//$APT_MIRROR|g" \
        /etc/apt/sources.list 2>/dev/null || true; \
    fi \
 && apt-get update && apt-get install -y --no-install-recommends \
      libglib2.0-0 libpixman-1-0 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 sim

WORKDIR /app
COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r web/requirements.txt "gunicorn==23.0.0"

COPY --from=qemu-builder /opt/qemu/bin/qemu-system-riscv32 \
     /app/.runtime/qemu-esp32c3/qemu/bin/qemu-system-riscv32
COPY --from=qemu-builder /opt/qemu/share/qemu/ \
     /app/.runtime/qemu-esp32c3/qemu/share/qemu/

COPY web/ /app/web/
COPY worker/ /app/worker/

# The app persists sessions, firmware artifacts and snapshots under the
# Flask instance directory; mount a volume here to keep them across restarts.
RUN mkdir -p /app/web/instance && chown -R sim:sim /app
USER sim

EXPOSE 8000
# The QEMU child processes are owned by the web process, so exactly ONE
# gunicorn worker must run (--workers 1 is not optional). Threads absorb
# concurrent SSE streams and API calls; --timeout 0 keeps long-lived SSE
# connections alive.
CMD ["gunicorn", "--chdir", "/app/web", \
     "--workers", "1", "--worker-class", "gthread", "--threads", "12", \
     "--timeout", "0", "--graceful-timeout", "60", \
     "--access-logfile", "-", \
     "--bind", "0.0.0.0:8000", "app:app"]
