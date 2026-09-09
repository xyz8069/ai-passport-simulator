#!/usr/bin/env bash
#
# Package the local QEMU runtime (<repo>/.runtime/qemu-esp32c3/qemu) into
# release assets that tools/fetch_qemu_runtime.sh --ai-passport can consume:
#
#   qemu-system-riscv32-ai-passport-<tag>-<os>-<arch>.tar.xz
#   qemu-system-riscv32-ai-passport-<tag>-<os>-<arch>.tar.xz.sha256
#
# Usage: tools/package_qemu_release.sh <tag> [output-dir]
#   <tag>        release tag used in asset names, e.g. qemu-v1
#   [output-dir] defaults to ./dist
#
# GPL reminder: when you publish these binaries, publish the corresponding
# source as well (third_party/qemu/ + the source archive linked in
# third_party/qemu/README.md). Binaries without source violate the GPL.
set -euo pipefail

TAG="${1:?usage: package_qemu_release.sh <tag> [output-dir]}"
OUT_DIR="${2:-dist}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
QEMU_HOME="${PROJECT_ROOT}/.runtime/qemu-esp32c3/qemu"
QEMU_BIN="${QEMU_HOME}/bin/qemu-system-riscv32"

if [[ ! -x "${QEMU_BIN}" ]]; then
  printf 'QEMU runtime not found: %s\n' "${QEMU_BIN}" >&2
  printf 'Install it first: tools/fetch_qemu_runtime.sh --upstream (or build from source)\n' >&2
  exit 1
fi

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  OS=apple-darwin;  ARCH=aarch64 ;;
  Darwin-x86_64) OS=apple-darwin;  ARCH=x86_64 ;;
  Linux-aarch64) OS=linux-gnu;     ARCH=aarch64 ;;
  Linux-x86_64)  OS=linux-gnu;     ARCH=x86_64 ;;
  *) printf 'Unsupported platform: %s-%s\n' "$(uname -s)" "$(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "${OUT_DIR}"
ASSET="qemu-system-riscv32-ai-passport-${TAG}-${OS}-${ARCH}.tar.xz"
printf 'Packaging %s ...\n' "${ASSET}"
COPYFILE_DISABLE=1 tar -cJf "${OUT_DIR}/${ASSET}" -C "${QEMU_HOME}/.." qemu
( cd "${OUT_DIR}" && shasum -a 256 "${ASSET}" > "${ASSET}.sha256" )
cat "${OUT_DIR}/${ASSET}.sha256"
printf '\nDone. Remember to also publish the corresponding source (GPL).\n'
printf 'Suggested upload set:\n'
printf '  %s\n  %s\n' "${OUT_DIR}/${ASSET}" "${OUT_DIR}/${ASSET}.sha256"
printf '  the QEMU source archive from third_party/qemu/README.md\n'
