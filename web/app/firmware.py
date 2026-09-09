from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import shutil
import subprocess
import sys
import threading
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ESP_IMAGE_MAGIC = 0xE9
ESP32C3_CHIP_ID = 0x0005
PARTITION_MAGIC = 0x50AA
PARTITION_MD5_MAGIC = 0xEBEB
PARTITION_TABLE_OFFSET = 0x8000
PARTITION_ENTRY_SIZE = 32
PARTITION_TABLE_MAX_ENTRIES = 95
SUPPORTED_FLASH_SIZES = (2 * 1024 * 1024, 4 * 1024 * 1024, 8 * 1024 * 1024, 16 * 1024 * 1024)
DEFAULT_QEMU_FLASH_SIZE = 8 * 1024 * 1024
BUNDLED_QEMU_RELATIVE_PATH = (
    ".runtime/qemu-esp32c3/qemu/bin/qemu-system-riscv32"
)
# Repository root (the directory containing web/).  The bundled QEMU runtime
# downloaded by tools/fetch_qemu_runtime.sh lives under <repo>/.runtime/.
BUNDLED_QEMU_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_QEMU_METADATA_LOCK = threading.Lock()
_QEMU_METADATA_SANITIZED: set[str] = set()
_MACOS_QEMU_PROBLEMATIC_XATTRS = {
    "com.apple.FinderInfo",
    "com.apple.ResourceFork",
    "com.apple.provenance",
}


class FirmwareFormatError(ValueError):
    """Raised when an uploaded file is not a structurally valid ESP image."""


@dataclass
class EspImage:
    offset: int
    size: int
    entry_addr: int
    chip_id: int
    segment_count: int
    checksum_valid: bool
    segments: List[Dict[str, int]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "offset": self.offset,
            "size": self.size,
            "entry_addr": f"0x{self.entry_addr:08x}",
            "chip_id": self.chip_id,
            "chip": "ESP32-C3" if self.chip_id == ESP32C3_CHIP_ID else "unknown",
            "segment_count": self.segment_count,
            "checksum_valid": self.checksum_valid,
            "segments": self.segments,
        }


@dataclass
class Partition:
    name: str
    type: int
    subtype: int
    offset: int
    size: int
    flags: int

    @property
    def is_app(self) -> bool:
        return self.type == 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "subtype": self.subtype,
            "offset": self.offset,
            "size": self.size,
            "flags": self.flags,
            "is_app": self.is_app,
        }


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def parse_image(data: bytes, offset: int = 0, limit: Optional[int] = None) -> EspImage:
    """Parse an ESP-IDF image header and segment table at an image offset."""
    end_limit = len(data) if limit is None else min(limit, len(data))
    if offset < 0 or offset + 24 > end_limit or data[offset] != ESP_IMAGE_MAGIC:
        raise FirmwareFormatError("ESP image header not found")

    segment_count = data[offset + 1]
    if segment_count == 0 or segment_count > 16:
        raise FirmwareFormatError("invalid ESP image segment count")

    entry_addr = int.from_bytes(data[offset + 4:offset + 8], "little")
    chip_id = int.from_bytes(data[offset + 12:offset + 14], "little")
    cursor = offset + 24
    checksum = 0xEF
    segments: List[Dict[str, int]] = []

    for _ in range(segment_count):
        if cursor + 8 > end_limit:
            raise FirmwareFormatError("truncated ESP segment header")
        load_addr = int.from_bytes(data[cursor:cursor + 4], "little")
        data_len = int.from_bytes(data[cursor + 4:cursor + 8], "little")
        cursor += 8
        if data_len > end_limit - cursor:
            raise FirmwareFormatError("truncated ESP segment data")
        segment_data = data[cursor:cursor + data_len]
        for byte in segment_data:
            checksum ^= byte
        segments.append({"load_addr": load_addr, "data_len": data_len})
        cursor += data_len

    if cursor >= end_limit:
        raise FirmwareFormatError("missing ESP image checksum")

    # esptool aligns the checksum to the next 16-byte boundary and writes the
    # optional validation hash after it. Keep accepting the compact checksum
    # layout used by older fixtures and small hand-built images.
    aligned_checksum = _align(cursor + 1, 16) - 1
    checksum_offset = aligned_checksum if (
        aligned_checksum < end_limit and data[aligned_checksum] == checksum
    ) else cursor
    checksum_valid = data[checksum_offset] == checksum
    if not checksum_valid:
        raise FirmwareFormatError(
            f"ESP image checksum mismatch: expected 0x{checksum:02x}, got 0x{data[checksum_offset]:02x}"
        )
    image_end = checksum_offset + 1
    # ESP-IDF app images normally carry a 32-byte validation hash. Include it
    # in the reported image extent without requiring it for legacy images.
    if image_end + 32 <= end_limit:
        image_end += 32
    return EspImage(
        offset=offset,
        size=image_end - offset,
        entry_addr=entry_addr,
        chip_id=chip_id,
        segment_count=segment_count,
        checksum_valid=checksum_valid,
        segments=segments,
    )


def parse_partition_table(data: bytes, offset: int = PARTITION_TABLE_OFFSET) -> List[Partition]:
    if offset + PARTITION_ENTRY_SIZE > len(data):
        return []
    partitions: List[Partition] = []
    for index in range(PARTITION_TABLE_MAX_ENTRIES):
        start = offset + index * PARTITION_ENTRY_SIZE
        if start + PARTITION_ENTRY_SIZE > len(data):
            break
        magic = int.from_bytes(data[start:start + 2], "little")
        if magic == 0xFFFF:
            break
        if magic == PARTITION_MD5_MAGIC:
            if start + PARTITION_ENTRY_SIZE > len(data):
                raise FirmwareFormatError("truncated partition table checksum marker")
            expected = hashlib.md5(data[offset:start]).digest()
            actual = data[start + 16:start + 32]
            if actual != expected:
                raise FirmwareFormatError("partition table MD5 mismatch")
            break
        if magic != PARTITION_MAGIC:
            if index == 0:
                return []
            break
        part_type = data[start + 2]
        subtype = data[start + 3]
        part_offset = int.from_bytes(data[start + 4:start + 8], "little")
        part_size = int.from_bytes(data[start + 8:start + 12], "little")
        raw_name = data[start + 12:start + 28].split(b"\0", 1)[0]
        name = raw_name.decode("utf-8", errors="replace")
        flags = int.from_bytes(data[start + 28:start + 32], "little")
        if part_offset + part_size > len(data):
            # A sparse/full flash dump can omit trailing bytes. Keep the table
            # entry, but do not attempt to parse an image outside the upload.
            partitions.append(Partition(name, part_type, subtype, part_offset, part_size, flags))
            continue
        partitions.append(Partition(name, part_type, subtype, part_offset, part_size, flags))
    return partitions


def analyze_firmware(data: bytes, filename: str = "firmware.bin") -> Dict[str, Any]:
    if len(data) < 32:
        raise FirmwareFormatError("firmware file is too small")

    partitions = parse_partition_table(data)
    images: List[EspImage] = []
    image_errors: List[str] = []
    candidates: List[Tuple[int, Optional[int]]] = []

    if partitions:
        # IDF merge-bin normally places the bootloader at 0x0. Keep 0x1000 as
        # a compatibility candidate for older/custom merged layouts.
        candidates.extend(((0x0, None), (0x1000, None)))
        for partition in partitions:
            if partition.is_app:
                candidates.append((partition.offset, partition.offset + partition.size))
    else:
        candidates.append((0, None))

    seen = set()
    for offset, limit in candidates:
        if offset in seen or offset >= len(data) or data[offset] != ESP_IMAGE_MAGIC:
            continue
        seen.add(offset)
        try:
            images.append(parse_image(data, offset, limit))
        except FirmwareFormatError as error:
            image_errors.append(f"0x{offset:x}: {error}")

    if not images:
        raise FirmwareFormatError(
            "no valid ESP image found" + (f" ({'; '.join(image_errors)})" if image_errors else "")
        )
    if any(image.chip_id not in {ESP32C3_CHIP_ID, 0} for image in images):
        raise FirmwareFormatError("image is not an ESP32-C3-compatible firmware")

    app_offsets = {part.offset for part in partitions if part.is_app}
    app_images = [image for image in images if not partitions or image.offset in app_offsets]
    if not app_images:
        app_images = images
    factory = next(
        (image for image in app_images if any(
            part.name == "factory" and part.offset == image.offset for part in partitions
        )),
        app_images[0],
    )
    # `idf.py merge-bin` places the bootloader image at the beginning of the
    # Flash file.  A valid application image at 0x1000 is not a substitute:
    # the ESP32-C3 ROM always starts its boot flow from offset zero.
    bootloader_image = next((image for image in images if image.offset == 0x0), None)
    return {
        "filename": filename,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "format": "esp-idf-merged" if partitions else "esp-idf-app",
        "chip": "ESP32-C3" if factory.chip_id in {ESP32C3_CHIP_ID, 0} else "unknown",
        "chip_id": factory.chip_id,
        "entry_addr": factory.entry_addr,
        "segment_count": factory.segment_count,
        "partitions": [partition.as_dict() for partition in partitions],
        "images": [image.as_dict() for image in images],
        "factory_image": factory.as_dict(),
        "bootable_flash": bool(partitions and bootloader_image),
        "loadable": True,
        "execution": {
            "qemu_supported": qemu_is_configured(),
            "qemu_command_configured": bool(os.environ.get("AI_PASSPORT_QEMU_COMMAND")),
            "status": "loaded",
        },
    }


def _sanitize_qemu_metadata(path: Path) -> List[str]:
    """Remove filesystem metadata that can make macOS dyld hang on launch.

    FinderInfo/ResourceFork attributes have caused extracted QEMU binaries to
    remain stuck in ``dyld_start`` on macOS.  The executable does not need any
    extended attributes to run, so clear all attributes on the bundled binary
    once per process.  Non-macOS hosts and files without xattrs are no-ops.
    """
    if sys.platform != "darwin":
        return []
    resolved = str(path.resolve())
    with _QEMU_METADATA_LOCK:
        if resolved in _QEMU_METADATA_SANITIZED:
            return []

        listxattr = getattr(os, "listxattr", None)
        if listxattr is not None:
            try:
                attributes = list(listxattr(path, follow_symlinks=False))
            except OSError:
                attributes = []
        else:
            xattr = shutil.which("xattr")
            if not xattr:
                _QEMU_METADATA_SANITIZED.add(resolved)
                return []
            result = subprocess.run(
                [xattr, str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            attributes = [line.strip() for line in result.stdout.splitlines() if line.strip()]

        removed: List[str] = []
        for attribute in attributes:
            if attribute not in _MACOS_QEMU_PROBLEMATIC_XATTRS:
                continue
            try:
                if listxattr is not None:
                    os.removexattr(path, attribute, follow_symlinks=False)
                else:
                    result = subprocess.run(
                        [xattr, "-d", attribute, str(path)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if result.returncode != 0:
                        continue
            except (AttributeError, OSError):
                continue
            removed.append(attribute)
        _QEMU_METADATA_SANITIZED.add(resolved)
        return removed


def find_qemu() -> Optional[str]:
    configured = os.environ.get("AI_PASSPORT_QEMU")
    if configured:
        path = Path(configured)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        return shutil.which(configured)

    bundled = BUNDLED_QEMU_PROJECT_ROOT / BUNDLED_QEMU_RELATIVE_PATH
    if bundled.is_file() and os.access(bundled, os.X_OK):
        _sanitize_qemu_metadata(bundled)
        return str(bundled)
    return shutil.which("qemu-system-riscv32")


def find_qemu_data_dir(qemu: Optional[str] = None) -> Optional[Path]:
    """Find the directory containing the ESP32-C3 ROM used by QEMU.

    A locally built QEMU is often run directly from its build directory, while
    an installed QEMU normally finds its data directory by itself.  Supplying
    ``AI_PASSPORT_QEMU_DATA`` makes both layouts explicit and is useful when
    the Web process has a different working directory from the shell that
    launched QEMU.
    """
    configured = os.environ.get("AI_PASSPORT_QEMU_DATA")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file() and configured_path.name == "esp32c3-rom.bin":
            configured_path = configured_path.parent
        if (configured_path / "esp32c3-rom.bin").is_file():
            return configured_path.resolve()
        return None

    qemu = qemu or find_qemu()
    if not qemu:
        return None
    qemu_path = Path(qemu).expanduser().resolve()
    candidates = (
        qemu_path.parent / "pc-bios",
        qemu_path.parent / "qemu-bundle" / "opt" / "homebrew" / "share" / "qemu",
        qemu_path.parent / "qemu-bundle" / "usr" / "local" / "share" / "qemu",
        qemu_path.parent.parent / "share" / "qemu",
        qemu_path.parent.parent / "pc-bios",
    )
    for candidate in candidates:
        if (candidate / "esp32c3-rom.bin").is_file():
            return candidate.resolve()
    return None


def qemu_is_configured() -> bool:
    """Return whether an executable or explicit command template is usable."""
    qemu = find_qemu()
    if qemu:
        return find_qemu_data_dir(qemu) is not None
    template = os.environ.get("AI_PASSPORT_QEMU_COMMAND")
    if not template:
        return False
    try:
        command = shlex.split(template.replace("{firmware}", "firmware.bin"))
    except ValueError:
        return False
    if not command:
        return False
    executable = Path(command[0])
    return (executable.is_file() and os.access(executable, os.X_OK)) or bool(shutil.which(command[0]))


def qemu_input_is_supported(qemu: Optional[str], command_template: Optional[str]) -> bool:
    """Return whether the selected backend exposes the simulator input bridge."""
    if os.environ.get("AI_PASSPORT_FIRMWARE_INPUT"):
        return True
    if command_template or not qemu:
        return False
    bundled = BUNDLED_QEMU_PROJECT_ROOT / BUNDLED_QEMU_RELATIVE_PATH
    try:
        return Path(qemu).resolve() == bundled.resolve()
    except OSError:
        return False


def _qemu_flash_size() -> int:
    raw_size = os.environ.get("AI_PASSPORT_QEMU_FLASH_SIZE")
    if not raw_size:
        return DEFAULT_QEMU_FLASH_SIZE
    try:
        size = int(raw_size, 0)
    except ValueError as error:
        raise ValueError("AI_PASSPORT_QEMU_FLASH_SIZE must be an integer byte count") from error
    if size not in SUPPORTED_FLASH_SIZES:
        supported = ", ".join(str(item) for item in SUPPORTED_FLASH_SIZES)
        raise ValueError(f"AI_PASSPORT_QEMU_FLASH_SIZE must be one of: {supported}")
    return size


def prepare_qemu_flash(firmware_path: Path) -> Tuple[Path, Optional[Path]]:
    """Create a QEMU-compatible flash image without changing the uploaded file.

    ESP-IDF app images are commonly much smaller than a physical flash chip,
    but the ESP32-C3 QEMU machine selects its SPI flash model from the exact
    drive size.  Pad short images with erased-flash bytes and leave already
    sized images untouched.  This helper does not move or synthesize a
    bootloader: the default ESP32-C3 ROM path still requires a complete merged
    image with a bootloader at offset zero.
    """
    source = firmware_path.resolve()
    source_size = source.stat().st_size
    target_size = _qemu_flash_size()
    if source_size in SUPPORTED_FLASH_SIZES:
        return source, None
    if source_size > target_size:
        target_size = next(
            (size for size in SUPPORTED_FLASH_SIZES if size >= source_size),
            None,
        )
        if target_size is None:
            raise ValueError("firmware image is larger than the supported QEMU flash sizes")

    fd, temporary_name = tempfile.mkstemp(
        prefix="qemu-flash-", suffix=".bin", dir=str(source.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as source_file, temporary.open("wb") as target_file:
            remaining = source_size
            while remaining:
                chunk = source_file.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise OSError("firmware file ended while preparing the QEMU flash image")
                target_file.write(chunk)
                remaining -= len(chunk)
            target_file.write(b"\xff" * (target_size - source_size))
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary, temporary


@dataclass
class FirmwareProcess:
    artifact_id: int
    command: List[str]
    process: subprocess.Popen[str]
    temporary_flash: Optional[Path] = None
    serial_log: Optional[Path] = None
    input_supported: bool = False
    output: List[str] = field(default_factory=list)
    latest_frame: Optional[Dict[str, Any]] = None
    frame_revision: int = 0
    latest_audio: Optional[Dict[str, Any]] = None
    audio_revision: int = 0
    audio_out_frames: int = 0
    audio_in_frames: int = 0
    # Ring of recent speaker frames as (revision, payload) so clients can pull
    # every frame losslessly; SSE snapshots only carry the latest frame and
    # drop audio whenever more than one frame lands between two pushes.
    audio_queue: List[Tuple[int, Dict[str, Any]]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    cleaned: bool = False
    serial_offset: int = 0

    def __post_init__(self) -> None:
        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()
        self.error_reader = threading.Thread(target=self._read_errors, daemon=True)
        self.error_reader.start()

    def _read_output(self) -> None:
        if self.process.stdout is None:
            return
        try:
            for line in self.process.stdout:
                raw_line = line.rstrip()
                try:
                    message = json.loads(raw_line)
                except json.JSONDecodeError:
                    message = None
                with self.lock:
                    if isinstance(message, dict) and message.get("type") == "frame":
                        self.latest_frame = {
                            key: value for key, value in message.items() if key != "type"
                        }
                        self.frame_revision += 1
                    elif isinstance(message, dict) and message.get("type") == "audio":
                        # Speaker audio: the backend forwards PCM produced by the
                        # firmware (ES8311/I2S) as base64 JSON lines.
                        audio = {
                            key: value for key, value in message.items() if key != "type"
                        }
                        audio.setdefault("dir", "play")
                        self.latest_audio = audio
                        self.audio_revision += 1
                        if audio.get("dir") != "record":
                            self.audio_out_frames += 1
                            self.audio_queue.append((self.audio_revision, dict(audio)))
                            del self.audio_queue[:-256]
                    else:
                        self.output.append(raw_line)
                    del self.output[:-100]
        finally:
            self._collect_serial_output()

    def _read_errors(self) -> None:
        if self.process.stderr is None:
            return
        for line in self.process.stderr:
            raw_line = line.rstrip()
            if not raw_line:
                continue
            with self.lock:
                self.output.append(raw_line)
                del self.output[:-100]

    def _collect_serial_output(self) -> None:
        if self.serial_log is None:
            return
        try:
            with self.serial_log.open("r", encoding="utf-8", errors="replace") as stream:
                stream.seek(self.serial_offset)
                chunk = stream.read()
                self.serial_offset = stream.tell()
        except OSError:
            return
        if not chunk:
            return
        with self.lock:
            self.output.extend(line.rstrip() for line in chunk.splitlines() if line.rstrip())
            del self.output[:-100]

    def status(self) -> Dict[str, Any]:
        self._collect_serial_output()
        exit_code = self.process.poll()
        if exit_code is not None:
            self._collect_serial_output()
            self.cleanup()
        return {
            "artifact_id": self.artifact_id,
            "status": "running" if exit_code is None else "exited",
            "pid": self.process.pid,
            "exit_code": exit_code,
            "command": self.command,
            "output": list(self.output),
            "framebuffer": dict(self.latest_frame) if self.latest_frame else None,
            "frame_revision": self.frame_revision,
            "input_bridge": "json-lines" if self.input_supported else "unavailable",
            "audio": dict(self.latest_audio) if self.latest_audio else None,
            "audio_revision": self.audio_revision,
            "audio_out_frames": self.audio_out_frames,
            "audio_in_frames": self.audio_in_frames,
            "audio_bridge": "json-lines" if self.input_supported else "unavailable",
        }

    def audio_frames_since(self, since: int) -> Dict[str, Any]:
        """Return queued speaker frames with a revision greater than `since`.

        `since < 0` means "fresh listener": only the newest frame is returned so
        a just-started speaker does not replay minutes of backlog.  `gap` is
        true when the requested revision is older than the queue's history, so
        the client knows the stream has a hole and must resync its timeline.
        """
        with self.lock:
            latest = self.audio_revision
            if not self.audio_queue:
                return {"frames": [], "latest": latest, "gap": False}
            oldest = self.audio_queue[0][0]
            if since < 0:
                revision, payload = self.audio_queue[-1]
                frames = [{"revision": revision, **payload}]
                gap = False
            else:
                frames = [
                    {"revision": revision, **payload}
                    for revision, payload in self.audio_queue
                    if revision > since
                ]
                gap = since < oldest - 1
            return {"frames": frames, "latest": latest, "gap": gap}

    def send_input(self, button: str, event: str) -> Dict[str, Any]:
        if not self.input_supported:
            return {
                "accepted": False,
                "reason": "the selected firmware backend does not expose an input protocol",
            }
        return self._write_line({"type": "button", "button": button, "event": event})

    def send_audio(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Forward one captured-microphone PCM frame to the firmware backend."""
        if not self.input_supported:
            return {
                "accepted": False,
                "reason": "the selected firmware backend does not expose the audio input protocol",
            }
        result = self._write_line({"type": "audio", "dir": "record", **payload})
        if result.get("accepted"):
            with self.lock:
                self.audio_in_frames += 1
            result["audio_in_frames"] = self.audio_in_frames
        return result

    def _write_line(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.process.poll() is not None:
            return {"accepted": False, "reason": "firmware process is not running"}
        if self.process.stdin is None:
            return {"accepted": False, "reason": "firmware input channel is unavailable"}
        line = json.dumps(payload, ensure_ascii=False)
        try:
            self.process.stdin.write(line + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            return {"accepted": False, "reason": str(error)}
        return {"accepted": True, "reason": "forwarded to firmware process"}

    def stop(self) -> None:
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except (AttributeError, ProcessLookupError, PermissionError):
                self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except (AttributeError, ProcessLookupError, PermissionError):
                    self.process.kill()
                self.process.wait(timeout=1.0)
        self.cleanup()

    def cleanup(self) -> None:
        with self.lock:
            if self.cleaned:
                return
            self.cleaned = True
            if self.temporary_flash is not None:
                self.temporary_flash.unlink(missing_ok=True)
            if self.serial_log is not None:
                self.serial_log.unlink(missing_ok=True)


class FirmwareRuntime:
    def __init__(self) -> None:
        self._runs: Dict[int, FirmwareProcess] = {}
        self._lock = threading.RLock()

    def start(self, artifact_id: int, firmware_path: Path) -> Dict[str, Any]:
        qemu = find_qemu()
        command_template = os.environ.get("AI_PASSPORT_QEMU_COMMAND")
        if not qemu and not command_template:
            return {
                "artifact_id": artifact_id,
                "status": "unavailable",
                "reason": "qemu-system-riscv32 was not found; install/configure an ESP32-C3 QEMU build",
            }
        with self._lock:
            existing = self._runs.get(artifact_id)
            if existing and existing.process.poll() is None:
                return existing.status()
        if command_template:
            try:
                shlex.split(command_template.replace("{firmware}", "firmware.bin"))
            except (KeyError, ValueError) as error:
                return {
                    "artifact_id": artifact_id,
                    "status": "error",
                    "reason": f"invalid AI_PASSPORT_QEMU_COMMAND: {error}",
                }
        if command_template:
            try:
                command = shlex.split(
                    command_template.replace("{firmware}", shlex.quote(str(firmware_path)))
                )
            except ValueError as error:
                return {
                    "artifact_id": artifact_id,
                    "status": "error",
                    "reason": f"invalid AI_PASSPORT_QEMU_COMMAND: {error}",
                }
            if not command:
                return {
                    "artifact_id": artifact_id,
                    "status": "error",
                    "reason": "AI_PASSPORT_QEMU_COMMAND is empty",
                }
            temporary_flash = None
        else:
            data_dir = find_qemu_data_dir(qemu)
            if not data_dir:
                return {
                    "artifact_id": artifact_id,
                    "status": "unavailable",
                    "reason": "ESP32-C3 QEMU ROM data was not found; set AI_PASSPORT_QEMU_DATA to the directory containing esp32c3-rom.bin",
                }
            try:
                report = analyze_firmware(firmware_path.read_bytes(), firmware_path.name)
            except (OSError, FirmwareFormatError) as error:
                return {"artifact_id": artifact_id, "status": "error", "reason": str(error)}
            if not report["bootable_flash"]:
                return {
                    "artifact_id": artifact_id,
                    "status": "unavailable",
                    "reason": "default ESP32-C3 ROM execution requires a complete merged image with a bootloader at offset 0; upload the full Flash image",
                }
            try:
                flash_path, temporary_flash = prepare_qemu_flash(firmware_path)
            except (OSError, ValueError) as error:
                return {"artifact_id": artifact_id, "status": "error", "reason": str(error)}
            serial_fd, serial_name = tempfile.mkstemp(
                prefix="qemu-serial-", suffix=".log", dir=str(firmware_path.parent)
            )
            os.close(serial_fd)
            serial_log = Path(serial_name)
            command = [
                qemu,
                "-M", "esp32c3",
                "-nographic",
                # A guest watchdog/reset must terminate this isolated run.
                # Without -no-reboot, a firmware fault can leave QEMU in an
                # endless reset loop and the Web UI keeps reporting a stale
                # RUNNING state instead of preserving the last real frame.
                "-no-reboot",
                "-serial", f"file:{serial_log}",
                "-monitor", "none",
                "-drive", f"file={flash_path},if=mtd,format=raw",
            ]
            command[3:3] = ["-L", str(data_dir)]
            # Optional extra QEMU flags (accelerator tuning, TB cache size).
            # Kept outside the stored command metadata so runs stay reproducible;
            # bad flags surface through the run status error honestly.
            extra_flags = os.environ.get("AI_PASSPORT_QEMU_EXTRA", "").strip()
            if extra_flags:
                try:
                    command.extend(shlex.split(extra_flags))
                except ValueError:
                    return {"artifact_id": artifact_id, "status": "error", "reason": "invalid AI_PASSPORT_QEMU_EXTRA flags"}
        with self._lock:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    start_new_session=os.name == "posix",
                )
            except OSError as error:
                if temporary_flash:
                    temporary_flash.unlink(missing_ok=True)
                if not command_template and "serial_log" in locals():
                    serial_log.unlink(missing_ok=True)
                return {"artifact_id": artifact_id, "status": "error", "reason": str(error), "command": command}
            run = FirmwareProcess(
                artifact_id,
                command,
                process,
                temporary_flash,
                serial_log if not command_template else None,
                input_supported=qemu_input_is_supported(qemu, command_template),
            )
            self._runs[artifact_id] = run
            return run.status()

    def status(self, artifact_id: int) -> Dict[str, Any]:
        with self._lock:
            run = self._runs.get(artifact_id)
        if not run:
            return {"artifact_id": artifact_id, "status": "not-running"}
        return run.status()

    def audio_queue(self, artifact_id: int, since: int) -> Dict[str, Any]:
        with self._lock:
            run = self._runs.get(artifact_id)
        if not run:
            return {"artifact_id": artifact_id, "status": "not-running", "frames": [], "latest": 0, "gap": False}
        result = run.audio_frames_since(since)
        result["artifact_id"] = artifact_id
        result["running"] = run.process.poll() is None
        return result

    def stop(self, artifact_id: int) -> Dict[str, Any]:
        with self._lock:
            run = self._runs.get(artifact_id)
        if run:
            run.stop()
            return run.status()
        return {"artifact_id": artifact_id, "status": "not-running"}

    def send_input(self, artifact_id: int, button: str, event: str) -> Dict[str, Any]:
        with self._lock:
            run = self._runs.get(artifact_id)
        if not run:
            return {
                "artifact_id": artifact_id,
                "accepted": False,
                "reason": "firmware is not running",
            }
        result = run.send_input(button, event)
        return {"artifact_id": artifact_id, **result}

    def send_audio(self, artifact_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            run = self._runs.get(artifact_id)
        if not run:
            return {
                "artifact_id": artifact_id,
                "accepted": False,
                "reason": "firmware is not running",
            }
        result = run.send_audio(payload)
        return {"artifact_id": artifact_id, **result}

    def stop_all(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
            self._runs.clear()
        for run in runs:
            run.stop()
