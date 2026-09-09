from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest

from app import create_app
from app import firmware as firmware_module
from app.database import database_session
from app.models import FirmwareArtifact


@pytest.fixture()
def client(tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "AI_PASSPORT_DATABASE_URL": f"sqlite:///{tmp_path / 'test.sqlite3'}",
            "AI_PASSPORT_FIRMWARE_DIR": str(tmp_path / "firmware"),
        }
    )
    with app.test_client() as test_client:
        yield test_client
    app.extensions["session_manager"].stop_all()
    app.extensions["firmware_runtime"].stop_all()


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


def upload(client, filename: str = "passport.bin", data: bytes | None = None):
    return client.post(
        "/api/firmware",
        data={"file": (io.BytesIO(data or make_image()), filename)},
        content_type="multipart/form-data",
    )


def test_health_and_firmware_only_initial_state(client):
    assert client.get("/api/health").json["status"] == "ok"
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201
    state = response.json["state"]
    assert state["screen"]["kind"] == "firmware"
    assert state["screen"]["message"] == "NO FIRMWARE LOADED"
    assert "demo" not in state
    assert "device" not in state


def test_demo_and_virtual_device_endpoints_are_removed(client):
    assert client.get("/api/demos").status_code == 404
    assert client.get("/api/demos/tetris").status_code == 404
    session_id = client.post("/api/sessions", json={}).json["session_id"]
    assert client.get(f"/api/sessions/{session_id}/demo").status_code == 404
    assert client.post(f"/api/sessions/{session_id}/demo", json={}).status_code == 404
    assert client.post(f"/api/sessions/{session_id}/device", json={}).status_code == 404
    assert client.post("/api/scenarios", json={"name": "fake"}).status_code == 404


def test_session_accepts_real_input_without_mutating_an_application(client):
    session_id = client.post("/api/sessions", json={}).json["session_id"]
    response = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "button", "button": "OK", "event": "CLICK"},
    )
    assert response.status_code == 200
    state = response.json["state"]
    assert state["screen"]["message"] == "NO FIRMWARE LOADED"
    assert state["input"]["events"] == ["OK: CLICK"]

    assert client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "demo_action", "action": "tick"},
    ).status_code == 400


def test_firmware_can_be_loaded_into_a_session(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(firmware_module, "find_qemu", lambda: None)
    artifact = upload(client).json
    session_id = client.post("/api/sessions", json={}).json["session_id"]
    response = client.post(
        f"/api/sessions/{session_id}/firmware",
        json={"artifact_id": artifact["id"]},
    )
    assert response.status_code == 200
    state = response.json["state"]
    assert state["firmware"]["artifact_id"] == artifact["id"]
    assert state["screen"]["status"] == "ready"
    assert state["screen"]["message"] == "FIRMWARE READY"


def test_session_firmware_route_validates_artifact(client):
    session_id = client.post("/api/sessions", json={}).json["session_id"]
    assert client.post(
        f"/api/sessions/{session_id}/firmware", json={"artifact_id": 404}
    ).status_code == 404
    assert client.post(
        f"/api/sessions/{session_id}/firmware", json={"artifact_id": "x"}
    ).status_code == 400


def test_session_and_input_event_persistence(client):
    response = client.post("/api/sessions", json={})
    session_id = response.json["session_id"]
    assert response.json["state"]["page"] == "firmware"

    response = client.post(
        f"/api/sessions/{session_id}/commands",
        json={"type": "button", "button": "DOWN", "event": "LONG"},
    )
    assert response.status_code == 200
    events = client.get(f"/api/sessions/{session_id}/events").json["events"]
    assert events == [{
        "button": "DOWN",
        "event": "LONG",
        "source": "web",
        "virtual_time_ms": 100,
    }]

    snapshot = client.post(
        f"/api/sessions/{session_id}/snapshots", json={"label": "firmware-only"}
    )
    assert snapshot.status_code == 201
    assert snapshot.json["state"]["screen"]["kind"] == "firmware"


def test_scenario_create_is_rejected(client):
    response = client.post("/api/sessions", json={"scenario": {"battery": {"soc": 12}}})
    assert response.status_code == 400
    assert "load firmware" in response.json["error"]


def test_app_restart_reconciles_persisted_running_firmware(client, tmp_path):
    artifact = client.post(
        "/api/firmware",
        data={"file": (io.BytesIO(make_image()), "restart.bin")},
        content_type="multipart/form-data",
    ).json
    app = client.application
    with database_session(app) as session:
        record = session.get(FirmwareArtifact, artifact["id"])
        record.execution_status = "running"

    restarted = create_app(
        {
            "TESTING": True,
            "AI_PASSPORT_DATABASE_URL": app.config["AI_PASSPORT_DATABASE_URL"],
            "AI_PASSPORT_FIRMWARE_DIR": app.config["AI_PASSPORT_FIRMWARE_DIR"],
        }
    )
    try:
        with database_session(restarted) as session:
            record = session.get(FirmwareArtifact, artifact["id"])
            assert record.execution_status == "not-running"
            assert "simulator restarted" in (record.execution_reason or "")
    finally:
        restarted.extensions["session_manager"].stop_all()
        restarted.extensions["firmware_runtime"].stop_all()
