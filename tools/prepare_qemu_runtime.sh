#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_ROOT="${1:-${PROJECT_ROOT}/.runtime/qemu-esp32c3}"
QEMU_PATH="${RUNTIME_ROOT}/qemu/bin/qemu-system-riscv32"
ROM_PATH="${RUNTIME_ROOT}/qemu/share/qemu/esp32c3-rom.bin"

if [[ ! -f "${QEMU_PATH}" ]]; then
  printf 'QEMU executable not found: %s\n' "${QEMU_PATH}" >&2
  exit 1
fi
if [[ ! -f "${ROM_PATH}" ]]; then
  printf 'ESP32-C3 ROM not found: %s\n' "${ROM_PATH}" >&2
  exit 1
fi

# macOS archives can carry FinderInfo/ResourceFork metadata onto executable
# files.  The Espressif QEMU binary does not need those attributes, and some
# macOS loaders can hang in dyld_start when they are present.
if command -v xattr >/dev/null 2>&1; then
  xattr -rc "${RUNTIME_ROOT}"
fi

# A plain copy from an extracted macOS archive can retain a resource fork in
# the executable's file metadata even after xattr cleanup.  Re-materialize
# only the executable bytes so dyld sees a normal Mach-O file.  This is kept
# local to macOS; Linux runtimes do not need this workaround.
if [[ "$(uname -s)" == "Darwin" ]]; then
  CLEAN_QEMU_PATH="$(mktemp "${QEMU_PATH}.clean.XXXXXX")"
  trap 'rm -f "${CLEAN_QEMU_PATH}"' EXIT
  dd if="${QEMU_PATH}" of="${CLEAN_QEMU_PATH}" bs=1048576 status=none
  chmod 755 "${CLEAN_QEMU_PATH}"
  mv -f "${CLEAN_QEMU_PATH}" "${QEMU_PATH}"
  trap - EXIT
fi

chmod 755 "${QEMU_PATH}"
"${QEMU_PATH}" --version | sed -n '1,3p'
if ! "${QEMU_PATH}" -machine help | grep -q '^esp32c3'; then
  printf 'QEMU does not expose the esp32c3 machine type\n' >&2
  exit 1
fi

printf 'Prepared ESP32-C3 QEMU runtime: %s\n' "${RUNTIME_ROOT}"
