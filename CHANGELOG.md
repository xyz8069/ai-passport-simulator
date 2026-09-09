# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org).

## [Unreleased]

## [0.1.0] - 2026-09-07

First open-source release.

### Added

- Browser workbench with official AI Passport device appearance and three
  virtual buttons (UP / OK / DOWN).
- ESP32-C3 merged-image upload and structural validation (bootloader,
  partition table, factory app, chip ID, checksum).
- Firmware session binding and lifecycle: load, run, stop, poll.
- Pluggable QEMU execution backend with serial log streaming.
- AI Passport QEMU customization published as a source patch
  (`third_party/qemu/`): ST7789 SPI display bridge, GDMA row forwarding,
  GPIO0 ADC key bridge and reset hold fix.
- Real `240×320 RGB565` framebuffer forwarding over SSE with stale-frame
  dropping on the browser side.
- Honest degradation: when no framebuffer is available the UI reports the
  real state instead of drawing substitute screens.
- Official plays catalog browsing with server-side download, size/SHA-256/
  structure validation and import.
- SQLite persistence for sessions, input events, snapshots and firmware
  metadata.
- JSON Lines input protocol for external QEMU backends
  (`AI_PASSPORT_FIRMWARE_INPUT`).
- Tooling: `tools/fetch_qemu_runtime.sh` (install a backend),
  `tools/prepare_qemu_runtime.sh` (macOS metadata self-repair),
  `tools/package_qemu_release.sh` (build release assets).
- Test suites for the web API, firmware analyzer, plays import and worker
  (`pytest`), plus frontend syntax checks (`node --check`).
