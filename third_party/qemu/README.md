# QEMU for AI Passport (third_party/qemu)

The simulator executes real ESP32-C3 firmware through a customized build of
[Espressif's QEMU fork](https://github.com/espressif/qemu). This directory
holds everything needed to redistribute that build in a GPL-compliant way.

## What is customized

Compared with upstream `esp-develop` at base commit
`febae182e132e4055529be423a818225ebddaa3a` (2026-04-29), the build adds the
peripherals the real AI Passport board uses:

| Change | Files | Purpose |
|---|---|---|
| ST7789 display model | `hw/display/st7789.c` (+header) | Emulates the real 240×320 ST7789 panel; rows are emitted on the JSON Lines output protocol as `{"type":"frame", ...}` RGB565 frames |
| GP SPI controller extensions | `hw/ssi/esp32c3_spi.c` | Wires the firmware's SPI2 display driver to the ST7789 model |
| GDMA row forwarding | `hw/dma/esp_gdma.c`, `esp32c3_gdma.c` | Forwards display rows from GDMA to the panel model |
| GPIO0 ADC key bridge | `hw/gpio/esp32_gpio.c`, `esp32c3_gpio.c`, machine file | UP/DOWN/OK buttons drive both digital GPIO and the ADC resistor ladder the recovery hook expects; released GPIO0 models the real external pull-up |
| GPIO reset hold fix | `hw/gpio/esp32c3_gpio.c` | Keeps GPIO0 high through reset hold so official recovery mode is not misdetected |
| Machine wiring | `hw/riscv/esp32c3.c` (+ clock/jtag/i2c/cache touches) | Instantiates and connects the above |

The complete customization is provided as a single patch:
[`ai-passport-qemu-esp32c3.patch`](./ai-passport-qemu-esp32c3.patch)
(25 files, ~3200 lines, generated with `git diff` including intent-to-add new
files against the base commit).

## Rebuilding from source

```bash
git clone https://github.com/espressif/qemu.git qemu
cd qemu
git checkout febae182e132e4055529be423a818225ebddaa3a
git apply /path/to/ai-passport-qemu-esp32c3.patch
./configure --target-list=riscv32-softmmu --disable-gtk --disable-sdl \
  --disable-vnc --disable-curl --disable-opengl --disable-virglrenderer \
  --disable-vhost-user --disable-xkbcommon --disable-docs --disable-tools \
  --disable-werror --disable-pie --enable-fdt=internal --enable-plugins
make -j"$(nproc)"
```

QEMU needs quite a few build dependencies; follow the upstream
[building docs](https://www.qemu.org/docs/master/devel/build-system.html) for
your platform.

## Reference build

The macOS arm64 build referenced by this project was configured exactly as
above on 2026-09-04 and produced a binary with sha256
`97f99d19ac92665e2a5121fda0d0a640f0d7856f`, byte-identical to the build output
of the same source tree. A full source archive matching that binary
(`qemu-esp_develop_9.2.2_20260417-ai-passport-src.tar.xz`, sha256
`237120dddcc62550a20319c9cbf5b42aeb4b7b44af46ce7ba9df0bd9c26e67c4`) is attached
to the project's GitHub release for GPL compliance.

## License

QEMU is distributed under the GPL-2.0-or-later. The customization in
[`ai-passport-qemu-esp32c3.patch`](./ai-passport-qemu-esp32c3.patch) is
provided under the same license. Binaries, if you distribute them, must ship
with the corresponding source (this directory plus the base commit satisfies
that requirement).

The upstream release assets' checksums are kept in
[`upstream-release-sha256sums.txt`](./upstream-release-sha256sums.txt) for the
`tools/fetch_qemu_runtime.sh --upstream` path. Note the unmodified upstream
QEMU boots firmware and prints serial logs, but has **no** AI Passport display
or input bridges, so the web UI will honestly show
`DISPLAY OUTPUT UNAVAILABLE`.
