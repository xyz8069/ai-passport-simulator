<p align="right">
  <a href="README.md">简体中文</a> · <strong>English</strong>
</p>

# AI Passport Simulator

A browser-based workbench that runs **real** ESP32-C3 firmware for the
[FoloToy AI Passport](https://github.com/FoloToy/ai-passport) on a pluggable
QEMU backend, and forwards the `240×320 RGB565` framebuffer to a
device-shaped canvas in your browser.

## Features

- Official device appearance with three virtual buttons (UP / OK / DOWN).
- ESP32-C3 `.bin` upload with image-structure validation: bootloader,
  partition table, factory app, chip ID, checksum.
- Firmware session binding: a validated image is loaded into the current
  device session before it can run.
- Pluggable QEMU execution backend: run, stop, poll status, stream serial logs.
- Optional framebuffer row protocol: when the backend emits
  `{"type":"frame", ...}` JSON lines, the canvas decodes real RGB565 output;
  when it does not, nothing is faked.
- Performance-minded display path: QEMU pushes changed frames only, SSE
  forwards the latest frame, the browser drops stale frames with
  `requestAnimationFrame`, and status polling can skip large frames with
  `include_frame=0`.
- AI Passport–aware QEMU build: SPI2/GDMA/ST7789 display bridge plus a GPIO0
  ADC key bridge (pressing a virtual key drives both digital GPIO and the ADC
  resistor ladder the official recovery hook expects).
- SQLite persistence for sessions, input events, snapshots and firmware
  metadata; firmware files live in the instance directory.
- Official plays catalog: integrated with the official plays page
  <https://ai-passport.folotoy.cn/plays/>. Download, validation and import
  all happen server-side.

## How it works

```text
Browser                       Flask web app                    Worker process
┌─────────────────┐   HTTP/SSE   ┌──────────────────────┐        ┌──────────────────┐
│ device canvas    │◄───────────►│ upload / validate     │───────►│ QEMU (esp32c3)   │
│ virtual buttons  │   JSON      │ session binding       │ stdout │  real firmware   │
│ status panels    │             │ SQLite persistence    │◄───────│  frame rows /    │
└─────────────────┘             │ plays catalog import  │ frame  │  GPIO0 ADC keys  │
                                └──────────────────────┘        └──────────────────┘
```

- The web app never executes firmware itself; the worker owns the QEMU
  process and its lifecycle.
- The screen only ever shows frames that the backend captured from the
  running firmware.
- Serial logs are displayed as logs — they are never rendered as a screen.

## Quick start

Requirements: Python 3.10+, and one of the QEMU backends below.

```bash
# 1. Python dependencies
python3 -m venv web/.venv
. web/.venv/bin/activate
pip install -r web/requirements.txt

# 2. QEMU backend (pick one)
tools/fetch_qemu_runtime.sh --upstream      # official Espressif QEMU: boots, serial logs
#    or, for the AI Passport build with display + input bridges:
#      export AI_PASSPORT_QEMU_RELEASE_BASE="https://github.com/xyz8069/ai-passport-simulator/releases/download/qemu-v1"
#      tools/fetch_qemu_runtime.sh
#    or build from source: third_party/qemu/README.md

# 3. Run
flask --app web.app run --debug --port 5050
```

Open <http://127.0.0.1:5050>, upload a merged ESP32-C3 image (or import one
from the official plays catalog) and press **Run**.

On macOS, if the QEMU runtime was re-extracted from an archive, run
`tools/prepare_qemu_runtime.sh` once: it clears FinderInfo/ResourceFork
extended attributes and re-materializes the Mach-O binary as a plain byte
stream so it does not hang in `dyld_start`. The web backend performs the same
one-shot self-repair on the bundled QEMU at startup.

## Getting firmware

- **Official plays catalog** — the page lists the catalog from
  `ai-passport.folotoy.cn`; "download and load/run" goes through the server,
  which verifies size, SHA-256 and the ESP32-C3 merged-image structure
  before binding it to the session.
- **Build it yourself** — clone the upstream firmware repo
  [FoloToy/ai-passport](https://github.com/FoloToy/ai-passport) (MIT) and
  build with ESP-IDF; the simulator needs a full merged image
  (`0x0` bootloader, `0x8000` partition table, `0x10000` factory app).
- App-only images can be uploaded and analyzed but are rejected as
  non-executable.

## QEMU backends and environment variables

| Variable | Purpose |
|---|---|
| `AI_PASSPORT_QEMU` | Absolute path (or command name) of a `qemu-system-riscv32` with the `esp32c3` machine |
| `AI_PASSPORT_QEMU_DATA` | Directory containing `esp32c3-rom.bin` (for locally built or bundle layouts) |
| `AI_PASSPORT_QEMU_COMMAND` | Full command template containing `{firmware}` for non-standard backends |
| `AI_PASSPORT_FIRMWARE_INPUT` | Enable the JSON Lines input protocol for external backends |
| `AI_PASSPORT_QEMU_FLASH_SIZE` | Flash size for the temp image (default 8 MiB) |

Backend resolution order: `AI_PASSPORT_QEMU` → bundled
`.runtime/qemu-esp32c3` → `qemu-system-riscv32` on `PATH`.

## Input protocol

The three web buttons record into the session input stream. The AI Passport
QEMU build enables a JSON Lines input protocol automatically and maps
UP/DOWN/OK to real GPIO0 ADC resistor-ladder pulses. External backends can
implement the same protocol and enable it with `AI_PASSPORT_FIRMWARE_INPUT`.
Input is only forwarded to the firmware; it never changes the screen by
itself.

## Development

```bash
# create the venv with dev tools
pip install -r web/requirements-dev.txt

# run the test suites
. web/.venv/bin/activate
pytest -q web/tests worker/test_worker.py

# frontend syntax check
node --check web/app/static/app.js
```

CI runs the same checks on GitHub Actions (see `.github/workflows/ci.yml`).

Project rule for contributors: **never fake device output.** If a screen
cannot show real framebuffer data, show the real state and the real reason
instead. This principle is enforced in code review.

## Deployment

The repository ships a self-contained Docker image (`Dockerfile`,
`docker-compose.yml`) that compiles the AI Passport QEMU customization from
the published patch and runs the whole stack on your own VPS:

```bash
docker compose up -d --build   # then open http://<server>:8000
```

See [DEPLOY.md](DEPLOY.md) for TLS, security notes and sizing. The deployed
instance supports real firmware execution — it is the same app as local.

## Repository layout

```text
web/          Flask app (routes, firmware analysis, plays catalog, templates, tests)
worker/       QEMU worker process and its tests
tools/        fetch/prepare/package scripts for the QEMU runtime
third_party/  QEMU customization patch, build notes, checksums (GPL compliance)
docs/         engineering reports (display performance, firmware regression)
Dockerfile    self-contained image (builds QEMU from the published patch)
docker-compose.yml
DEPLOY.md     deployment guide (EN) / 部署指南 (zh_CN)
```

## Third-party components

- [FoloToy/ai-passport](https://github.com/FoloToy/ai-passport) — the firmware
  this simulator targets (MIT). Not included in this repository; fetch
  firmware from the official catalog or build it yourself.
- [Espressif QEMU fork](https://github.com/espressif/qemu) — GPL-2.0-or-later.
  The AI Passport customization lives in `third_party/qemu/` as a source patch
  with rebuild instructions; corresponding source is published with any
  binaries.
- Device appearance assets originate from the AI Passport project.
- The plays catalog and its firmware are provided by FoloToy's service.

## License

This project is released under the [MIT License](LICENSE). Third-party
components remain under their own licenses as noted above.
