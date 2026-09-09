#!/usr/bin/env bash
#
# Download and install a QEMU runtime for the AI Passport simulator into
# <repo>/.runtime/qemu-esp32c3/qemu.
#
# Modes:
#   (default) --ai-passport  Project QEMU build with the AI Passport ST7789
#                            display bridge and GPIO0 ADC key bridge. Requires
#                            AI_PASSPORT_QEMU_RELEASE_BASE to point at a
#                            directory (or release base URL) that hosts
#                              qemu-system-riscv32-ai-passport-<ver>-<os>-<arch>.tar.xz
#                              qemu-system-riscv32-ai-passport-<ver>-<os>-<arch>.tar.xz.sha256
#                            See third_party/qemu/README.md for how these are
#                            produced with tools/package_qemu_release.sh.
#   --upstream               Official Espressif QEMU release (tag
#                            esp_develop_9.2.2_20260417). Boots real firmware
#                            and gives serial logs, but has NO AI Passport
#                            display/input bridges, so the web UI will show
#                            DISPLAY OUTPUT UNAVAILABLE.
#   --force                  Re-download even if a runtime already exists.
#
# Env overrides:
#   AI_PASSPORT_QEMU_RELEASE_BASE   base URL/dir for --ai-passport assets
#   AI_PASSPORT_QEMU_UPSTREAM_TAG   upstream release tag (default in script)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_ROOT="${PROJECT_ROOT}/.runtime/qemu-esp32c3"
QEMU_HOME="${RUNTIME_ROOT}/qemu"
QEMU_BIN="${QEMU_HOME}/bin/qemu-system-riscv32"

MODE="--ai-passport"
FORCE=0
for arg in "$@"; do
  case "${arg}" in
    --ai-passport|--upstream) MODE="${arg}" ;;
    --force) FORCE=1 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "${arg}" >&2; exit 2 ;;
  esac
done

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  OS=apple-darwin;  ARCH=aarch64 ;;
  Darwin-x86_64) OS=apple-darwin;  ARCH=x86_64 ;;
  Linux-aarch64) OS=linux-gnu;     ARCH=aarch64 ;;
  Linux-x86_64)  OS=linux-gnu;     ARCH=x86_64 ;;
  *) printf 'Unsupported platform: %s-%s\n' "$(uname -s)" "$(uname -m)" >&2; exit 1 ;;
esac

fetch() { # fetch <url> <dest>
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$2" "$1"
  else
    printf 'Need curl or wget to download\n' >&2; exit 1
  fi
}

if [[ -x "${QEMU_BIN}" && "${FORCE}" -ne 1 ]]; then
  if "${QEMU_BIN}" -machine help 2>/dev/null | grep -q '^esp32c3'; then
    printf 'QEMU runtime already installed: %s\n' "${QEMU_BIN}"
    printf '(use --force to reinstall)\n'
    "${QEMU_BIN}" --version | head -1 || true
    exit 0
  fi
fi

mkdir -p "${RUNTIME_ROOT}"
TMP_DIR="$(mktemp -d "${RUNTIME_ROOT}/download.XXXXXX")"
trap 'rm -rf "${TMP_DIR}"' EXIT

if [[ "${MODE}" == "--upstream" ]]; then
  TAG="${AI_PASSPORT_QEMU_UPSTREAM_TAG:-esp-develop-9.2.2-20260417}"
  ASSET="qemu-riscv32-softmmu-${TAG//-/_}-${ARCH}-${OS}.tar.xz"
  URL="https://github.com/espressif/qemu/releases/download/${TAG}/${ASSET}"
  SUMS="${PROJECT_ROOT}/third_party/qemu/upstream-release-sha256sums.txt"
  printf 'Downloading upstream Espressif QEMU %s for %s/%s...\n' "${TAG}" "${ARCH}" "${OS}"
  fetch "${URL}" "${TMP_DIR}/${ASSET}"
  EXPECTED="$(grep -v '^#' "${SUMS}" | grep -F "${ASSET}" | awk '{print $1}' | head -1 || true)"
  if [[ -z "${EXPECTED}" ]]; then
    printf 'No checksum for %s in %s; refusing to install unverified file\n' "${ASSET}" "${SUMS}" >&2
    exit 1
  fi
  ACTUAL="$(shasum -a 256 "${TMP_DIR}/${ASSET}" | awk '{print $1}')"
  if [[ "${ACTUAL}" != "${EXPECTED}" ]]; then
    printf 'Checksum mismatch for %s\n  expected %s\n  actual   %s\n' "${ASSET}" "${EXPECTED}" "${ACTUAL}" >&2
    exit 1
  fi
  tar -xJf "${TMP_DIR}/${ASSET}" -C "${TMP_DIR}"
else
  BASE="${AI_PASSPORT_QEMU_RELEASE_BASE:-}"
  if [[ -z "${BASE}" ]]; then
    cat >&2 <<'EOF'
AI_PASSPORT_QEMU_RELEASE_BASE is not set.

The AI Passport QEMU build is distributed through the project's GitHub
releases. Set the variable to the release download base, e.g.:

  export AI_PASSPORT_QEMU_RELEASE_BASE="https://github.com/xyz8069/ai-passport-simulator/releases/download/qemu-v1"

Assets are produced with tools/package_qemu_release.sh. Alternatively:

  * run with --upstream for the official Espressif QEMU (logs only, no display)
  * build from source: see third_party/qemu/README.md
EOF
    exit 1
  fi
  # Strip trailing slash, resolve version by probing a versions list if present,
  # otherwise require an explicit full base pointing at one version.
  BASE="${BASE%/}"
  ASSET="qemu-system-riscv32-ai-passport-unknown-${OS}-${ARCH}.tar.xz"
  if [[ "${BASE}" == *"/releases/download/"* ]]; then
    TAGGED="${BASE##*/releases/download/}"
    VER="${TAGGED%%/*}"
    ASSET="qemu-system-riscv32-ai-passport-${VER}-${OS}-${ARCH}.tar.xz"
  fi
  URL="${BASE}/${ASSET}"
  printf 'Downloading AI Passport QEMU for %s/%s...\n' "${ARCH}" "${OS}"
  fetch "${URL}" "${TMP_DIR}/${ASSET}" || {
    printf 'Download failed: %s\n' "${URL}" >&2
    printf 'Check AI_PASSPORT_QEMU_RELEASE_BASE and the release assets.\n' >&2
    exit 1
  }
  fetch "${URL}.sha256" "${TMP_DIR}/${ASSET}.sha256"
  ( cd "${TMP_DIR}" && shasum -a 256 -c "${ASSET}.sha256" >/dev/null )
  tar -xJf "${TMP_DIR}/${ASSET}" -C "${TMP_DIR}"
fi

# Locate the extracted qemu directory.  Both the official Espressif release
# and the AI Passport assets extract to a top-level qemu/ directory; some
# layouts nest it one level or keep the version in the directory name.
SRC_DIR="$(find "${TMP_DIR}" -maxdepth 2 -type d -name qemu | head -1)"
if [[ -z "${SRC_DIR}" ]]; then
  SRC_DIR="$(find "${TMP_DIR}" -maxdepth 1 -type d -name 'qemu*' | head -1)"
fi

if [[ -z "${SRC_DIR}" || ! -d "${SRC_DIR}" ]]; then
  printf 'Could not locate extracted qemu directory in archive\n' >&2
  exit 1
fi

rm -rf "${QEMU_HOME}"
mkdir -p "${QEMU_HOME}"
cp -R "${SRC_DIR}/" "${QEMU_HOME}/"
rm -rf "${SRC_DIR}"

"${SCRIPT_DIR}/prepare_qemu_runtime.sh" "${RUNTIME_ROOT}"
printf 'Installed QEMU runtime: %s\n' "${QEMU_BIN}"
