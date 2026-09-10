from __future__ import annotations

import io
import json
import os
import shlex
import struct
from pathlib import Path
import time
from typing import Optional

import pytest

from app import firmware as firmware_module


def make_image(chip_id: int = firmware_module.ESP32C3_CHIP_ID) -> bytes:
    payload = b"api-firmware-fixture"
    header = bytearray(24)
    header[0] = firmware_module.ESP_IMAGE_MAGIC
    header[1] = 1
    struct.pack_into("<I", header, 4, 0x40380000)
    struct.pack_into("<H", header, 12, chip_id)
    segment = struct.pack("<II", 0x3FC80000, len(payload)) + payload
    checksum = 0xEF
    for byte in payload:
        checksum ^= byte
    image = bytes(header) + segment + bytes([checksum])
    return image + bytes((-len(image)) % 16)


def make_esptool_image() -> bytes:
    payload = b"esptool-aligned-image"
    header = bytearray(24)
    header[0] = firmware_module.ESP_IMAGE_MAGIC
    header[1] = 1
    struct.pack_into("<I", header, 4, 0x40380000)
    struct.pack_into("<H", header, 12, firmware_module.ESP32C3_CHIP_ID)
    image = bytes(header) + struct.pack("<II", 0x3FC80000, len(payload)) + payload
    checksum = 0xEF
    for byte in payload:
        checksum ^= byte
    image += b"\x00" * (15 - (len(image) % 16)) + bytes([checksum])
    return image + bytes(range(32))


def upload(client, filename: str = "passport.bin", data: Optional[bytes] = None):
    return client.post(
        "/api/firmware",
        data={"file": (io.BytesIO(data or make_image()), filename)},
        content_type="multipart/form-data",
    )


def test_firmware_upload_validate_and_analyze(client):
    response = upload(client)
    assert response.status_code == 201
    artifact = response.json
    assert artifact["filename"] == "passport.bin"
    assert artifact["load_status"] == "validated"
    assert artifact["analysis"]["chip"] == "ESP32-C3"
    assert len(artifact["sha256"]) == 64

    artifact_id = artifact["id"]
    assert client.get("/api/firmware").json["firmware"][0]["id"] == artifact_id
    assert client.get(f"/api/firmware/{artifact_id}").json["id"] == artifact_id

    response = client.post(f"/api/firmware/{artifact_id}/analyze")
    assert response.status_code == 200
    assert response.json["analysis"]["size_bytes"] == len(make_image())


def test_firmware_parser_accepts_esptool_aligned_checksum():
    image = make_esptool_image()
    parsed = firmware_module.parse_image(image)

    assert parsed.checksum_valid is True
    assert parsed.size == len(image)


def test_firmware_upload_rejects_unsafe_or_invalid_files(client):
    assert upload(client, "passport.txt").status_code == 400
    assert upload(client, "broken.bin", b"not an esp image").status_code == 400
    assert upload(client, "wrong-chip.bin", make_image(chip_id=0x0009)).status_code == 400


def test_firmware_run_reports_unavailable_without_qemu(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(firmware_module, "find_qemu", lambda: None)
    response = upload(client)
    artifact_id = response.json["id"]
    assert response.json["execution"]["qemu_available"] is False

    response = client.post(f"/api/firmware/{artifact_id}/run")
    assert response.status_code == 200
    assert response.json["run"]["status"] == "unavailable"
    assert "qemu-system-riscv32" in response.json["run"]["reason"]
    assert client.get(f"/api/firmware/{artifact_id}/run").json["run"]["status"] == "not-running"


def test_configured_qemu_process_can_start_and_stop(tmp_path, monkeypatch: pytest.MonkeyPatch):
    command = (
        f"{shlex.quote(os.sys.executable)} -u -c "
        "\"import time; print('qemu-fixture', flush=True); time.sleep(5)\""
    )
    monkeypatch.setenv("AI_PASSPORT_QEMU_COMMAND", command)
    assert firmware_module.qemu_is_configured() is True
    image_path = tmp_path / "firmware.bin"
    image_path.write_bytes(make_image())

    runtime = firmware_module.FirmwareRuntime()
    started = runtime.start(7, image_path)
    assert started["status"] == "running"
    stopped = runtime.stop(7)
    assert stopped["status"] == "exited"


def test_running_backend_frame_is_bound_to_the_session(client, monkeypatch: pytest.MonkeyPatch):
    command = (
        f"{shlex.quote(os.sys.executable)} -u -c "
        f"\"import json,time; print(json.dumps({{'type':'frame','width':1,'height':1,'format':'rgb565-le','frame_rgb565_b64':'AQI='}}), flush=True); time.sleep(5)\""
    )
    monkeypatch.setenv("AI_PASSPORT_QEMU_COMMAND", command)
    monkeypatch.setenv("AI_PASSPORT_FIRMWARE_INPUT", "1")
    artifact_id = upload(client).json["id"]
    session_id = client.post("/api/sessions", json={}).json["session_id"]

    response = client.post(
        f"/api/firmware/{artifact_id}/run",
        json={"session_id": session_id},
    )
    assert response.status_code == 200
    assert response.json["run"]["status"] == "running"
    assert response.json["run"]["input_bridge"] == "json-lines"
    time.sleep(0.1)
    response = client.get(
        f"/api/firmware/{artifact_id}/run?session_id={session_id}"
    )
    screen = response.json["session"]["state"]["screen"]
    assert screen["frame_rgb565_b64"] == "AQI="
    assert response.json["session"]["state"]["input"]["available"] is True

    response = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "button", "button": "OK", "event": "CLICK"},
    )
    assert response.status_code == 200
    assert response.json["state"]["input"]["events"] == ["OK: CLICK"]


def test_session_stream_forwards_latest_frame_without_app_context_error(client, monkeypatch: pytest.MonkeyPatch):
    command = (
        f"{shlex.quote(os.sys.executable)} -u -c "
        f"\"import json,time; print(json.dumps({{'type':'frame','width':1,'height':1,'format':'rgb565-le','frame_rgb565_b64':'AQI='}}), flush=True); time.sleep(1)\""
    )
    monkeypatch.setenv("AI_PASSPORT_QEMU_COMMAND", command)
    artifact_id = upload(client).json["id"]
    session_id = client.post("/api/sessions", json={}).json["session_id"]
    response = client.post(
        f"/api/firmware/{artifact_id}/run",
        json={"session_id": session_id},
    )
    assert response.status_code == 200

    chunks = []
    stream = client.get(f"/api/sessions/{session_id}/stream", buffered=False)
    for chunk in stream.response:
        text = chunk.decode("utf-8")
        chunks.append(text)
        if "frame_rgb565_b64" in text:
            break
    stream.close()

    assert any("frame_rgb565_b64" in chunk for chunk in chunks)


def test_run_status_can_skip_large_frame_payload(client, monkeypatch: pytest.MonkeyPatch):
    command = (
        f"{shlex.quote(os.sys.executable)} -u -c "
        f"\"import json,time; print(json.dumps({{'type':'frame','width':1,'height':1,'format':'rgb565-le','frame_rgb565_b64':'AQI='}}), flush=True); time.sleep(1)\""
    )
    monkeypatch.setenv("AI_PASSPORT_QEMU_COMMAND", command)
    artifact_id = upload(client).json["id"]
    session_id = client.post("/api/sessions", json={}).json["session_id"]
    client.post(f"/api/firmware/{artifact_id}/run", json={"session_id": session_id})
    time.sleep(0.1)

    response = client.get(
        f"/api/firmware/{artifact_id}/run?session_id={session_id}&include_frame=0"
    )
    assert response.status_code == 200
    assert response.json["run"]["framebuffer"] is None
    assert response.json["run"]["frame_revision"] >= 1


def test_deleting_session_stops_its_firmware_process(client, monkeypatch: pytest.MonkeyPatch):
    command = (
        f"{shlex.quote(os.sys.executable)} -u -c "
        "\"import time; time.sleep(5)\""
    )
    monkeypatch.setenv("AI_PASSPORT_QEMU_COMMAND", command)
    artifact_id = upload(client).json["id"]
    session_id = client.post("/api/sessions", json={}).json["session_id"]
    started = client.post(
        f"/api/firmware/{artifact_id}/run",
        json={"session_id": session_id},
    )
    assert started.json["run"]["status"] == "running"

    deleted = client.delete(f"/api/sessions/{session_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/firmware/{artifact_id}/run").json["run"]["status"] == "exited"


def test_qemu_flash_is_padded_and_cleaned(tmp_path):
    image_path = tmp_path / "firmware.bin"
    image_path.write_bytes(make_image())

    flash_path, temporary_flash = firmware_module.prepare_qemu_flash(image_path)

    assert temporary_flash == flash_path
    assert flash_path.stat().st_size == firmware_module.DEFAULT_QEMU_FLASH_SIZE
    with flash_path.open("rb") as stream:
        stream.seek(image_path.stat().st_size)
        assert stream.read(64) == b"\xff" * 64
    flash_path.unlink()


def test_default_qemu_rejects_app_only_image(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AI_PASSPORT_QEMU_COMMAND", raising=False)
    monkeypatch.setattr(firmware_module, "find_qemu", lambda: "/tmp/qemu-system-riscv32")
    rom_dir = tmp_path / "qemu-data"
    rom_dir.mkdir()
    (rom_dir / "esp32c3-rom.bin").write_bytes(b"rom")
    monkeypatch.setattr(firmware_module, "find_qemu_data_dir", lambda _qemu: rom_dir)
    image_path = tmp_path / "firmware.bin"
    image_path.write_bytes(make_image())

    result = firmware_module.FirmwareRuntime().start(9, image_path)

    assert result["status"] == "unavailable"
    assert "complete merged image" in result["reason"]


def test_invalid_qemu_command_is_reported(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_PASSPORT_QEMU_COMMAND", "'unterminated")
    runtime = firmware_module.FirmwareRuntime()
    result = runtime.start(8, tmp_path / "firmware.bin")
    assert result["status"] == "error"
    assert "QEMU_COMMAND" in result["reason"]


def test_bundled_qemu_metadata_cleanup_removes_macos_attributes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    qemu_path = tmp_path / "qemu-system-riscv32"
    qemu_path.write_bytes(b"qemu")
    removed: list[str] = []
    monkeypatch.setattr(firmware_module.sys, "platform", "darwin")
    monkeypatch.setattr(firmware_module.os, "listxattr", None, raising=False)
    monkeypatch.setattr(
        firmware_module.shutil,
        "which",
        lambda name: "/usr/bin/xattr" if name == "xattr" else None,
    )
    def fake_xattr(command, **_kwargs):
        if len(command) == 4:
            removed.append(command[2])
            return type("Completed", (), {"stdout": "", "returncode": 0})()
        return type(
            "Completed",
            (),
            {
                "stdout": (
                    "com.apple.FinderInfo\n"
                    "com.apple.ResourceFork\n"
                    "user.keep\n"
                ),
                "returncode": 0,
            },
        )()

    monkeypatch.setattr(firmware_module.subprocess, "run", fake_xattr)
    firmware_module._QEMU_METADATA_SANITIZED.clear()

    assert firmware_module._sanitize_qemu_metadata(qemu_path) == [
        "com.apple.FinderInfo",
        "com.apple.ResourceFork",
    ]
    assert removed == [
        "com.apple.FinderInfo",
        "com.apple.ResourceFork",
    ]


def test_backend_audio_frames_reach_the_session(client, monkeypatch: pytest.MonkeyPatch):
    command = (
        f"{shlex.quote(os.sys.executable)} -u -c "
        f"\"import json,time; print(json.dumps({{'type':'audio','dir':'play','format':'pcm-s16le','rate':16000,'channels':1,'samples':'AQID'}}), flush=True); time.sleep(5)\""
    )
    monkeypatch.setenv("AI_PASSPORT_QEMU_COMMAND", command)
    monkeypatch.setenv("AI_PASSPORT_FIRMWARE_INPUT", "1")
    artifact_id = upload(client).json["id"]
    session_id = client.post("/api/sessions", json={}).json["session_id"]
    client.post(f"/api/firmware/{artifact_id}/run", json={"session_id": session_id})
    time.sleep(0.2)

    response = client.get(f"/api/firmware/{artifact_id}/run?session_id={session_id}")
    run = response.json["run"]
    session_state = response.json["session"]["state"]
    assert run["audio_bridge"] == "json-lines"
    assert run["audio"]["samples"] == "AQID"
    assert run["audio_out_frames"] == 1
    audio = session_state["audio"]
    assert audio["available"] is True
    assert audio["frame"]["rate"] == 16000
    assert audio["out_frames"] == 1

    # Polling for telemetry may skip the bulky audio payload.
    polled = client.get(f"/api/firmware/{artifact_id}/run?session_id={session_id}&include_frame=0").json["run"]
    assert polled["audio"] is None
    assert polled["audio_out_frames"] == 1


def test_session_audio_endpoint_forwards_mic_frames(client, tmp_path, monkeypatch: pytest.MonkeyPatch):
    sink = tmp_path / "stdin-lines.txt"
    command = (
        f"{shlex.quote(os.sys.executable)} -u -c "
        f"\"import json,sys;"
        f"print(json.dumps({{'type':'audio','dir':'play','format':'pcm-s16le','rate':16000,'channels':1,'samples':'AQID'}}), flush=True);"
        f"out=open(r'{sink}', 'w', buffering=1);"
        f"[out.write(line) for line in sys.stdin]\""
    )
    monkeypatch.setenv("AI_PASSPORT_QEMU_COMMAND", command)
    monkeypatch.setenv("AI_PASSPORT_FIRMWARE_INPUT", "1")
    artifact_id = upload(client).json["id"]
    session_id = client.post("/api/sessions", json={}).json["session_id"]
    client.post(f"/api/firmware/{artifact_id}/run", json={"session_id": session_id})
    time.sleep(0.2)

    response = client.post(
        f"/api/sessions/{session_id}/audio",
        json={"format": "pcm-s16le", "rate": 16000, "channels": 1, "samples": "AQID"},
    )
    assert response.status_code == 200
    assert response.json["accepted"] is True
    assert response.json["audio_in_frames"] == 1

    # The forwarded line reaches the backend stdin as a record-direction frame.
    time.sleep(0.2)
    forwarded = sink.read_text().strip().splitlines()
    assert len(forwarded) == 1
    payload = json.loads(forwarded[0])
    assert payload["type"] == "audio"
    assert payload["dir"] == "record"
    assert payload["samples"] == "AQID"

    # Session telemetry reflects the forwarded frame count.
    state = client.get(f"/api/sessions/{session_id}").json["state"]
    assert state["audio"]["in_frames"] == 1

    # Validation errors are rejected honestly.
    assert client.post(f"/api/sessions/{session_id}/audio", json={"samples": ""}).status_code == 400
    assert client.post(f"/api/sessions/{session_id}/audio", json={"samples": "AQID", "rate": 1}).status_code == 400
    assert client.post(f"/api/sessions/{session_id}/audio", json={"samples": "AQID", "format": "mp3"}).status_code == 400
    assert client.post(f"/api/sessions/{session_id}/audio", json={"samples": "A" * 100_001}).status_code == 413


def test_session_audio_without_firmware_reports_conflict(client):
    session_id = client.post("/api/sessions", json={}).json["session_id"]
    response = client.post(
        f"/api/sessions/{session_id}/audio",
        json={"samples": "AQID"},
    )
    assert response.status_code == 409


def test_speaker_audio_queue_endpoint_delivers_every_frame(client, tmp_path, monkeypatch: pytest.MonkeyPatch):
    """The lossless queue returns all play frames in order and flags gaps."""
    command = (
        f"{shlex.quote(os.sys.executable)} -u -c "
        f"\"import json,time;"
        f"[print(json.dumps({{'type':'audio','dir':'play','format':'pcm-s16le','rate':16000,'channels':1,'samples':'AQID'}}), flush=True) or time.sleep(0.05) for _ in range(4)];"
        f"time.sleep(5)\""
    )
    monkeypatch.setenv("AI_PASSPORT_QEMU_COMMAND", command)
    monkeypatch.setenv("AI_PASSPORT_FIRMWARE_INPUT", "1")
    artifact_id = upload(client).json["id"]
    client.post(f"/api/firmware/{artifact_id}/run", json={})
    deadline = time.time() + 3
    while time.time() < deadline:
        status = client.get(f"/api/firmware/{artifact_id}/run?include_frame=0").json["run"]
        if status.get("audio_out_frames", 0) >= 4:
            break
        time.sleep(0.05)
    assert status.get("audio_out_frames", 0) >= 4

    fresh = client.get(f"/api/firmware/{artifact_id}/audio?since=-1").json
    assert fresh["running"] is True
    assert len(fresh["frames"]) == 1
    assert fresh["frames"][0]["revision"] == fresh["latest"]
    assert fresh["frames"][0]["dir"] == "play"

    full = client.get(f"/api/firmware/{artifact_id}/audio?since=0").json
    revisions = [frame["revision"] for frame in full["frames"]]
    assert revisions == list(range(1, len(revisions) + 1))
    assert full["gap"] is False

    tail = client.get(f"/api/firmware/{artifact_id}/audio?since={revisions[-1]}").json
    assert tail["frames"] == []
    assert tail["latest"] == revisions[-1]

    stale = client.get(f"/api/firmware/{artifact_id}/audio?since=-5").json
    assert stale["gap"] is False  # fresh listener: only the newest frame

    missing = client.get(f"/api/firmware/99999/audio?since=0").json
    assert missing["status"] == "not-running"
    assert missing["frames"] == []


def test_upload_disabled_returns_403(tmp_path: Path):
    """Demo instances may disallow user uploads; plays catalog stays usable."""
    from app import create_app

    app = create_app(
        {
            "TESTING": True,
            "AI_PASSPORT_DATABASE_URL": f"sqlite:///{tmp_path / 'test.sqlite3'}",
            "AI_PASSPORT_FIRMWARE_DIR": str(tmp_path / "firmware"),
            "AI_PASSPORT_ALLOW_UPLOAD": False,
        }
    )
    with app.test_client() as test_client:
        response = test_client.post(
            "/api/firmware",
            data={"file": (io.BytesIO(b"malicious-or-not"), "firmware.bin")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 403
        assert "plays catalog" in response.json["error"]

        # The page still renders, without the upload controls.
        page = test_client.get("/")
        assert page.status_code == 200
        assert b"firmware-file" not in page.data
    app.extensions["session_manager"].stop_all()
