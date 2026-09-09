from __future__ import annotations

from worker import Device


def test_worker_starts_without_any_application_screen():
    state = Device().state()

    assert state["page"] == "firmware"
    assert state["screen"]["kind"] == "firmware"
    assert state["screen"]["status"] == "not-loaded"
    assert state["screen"]["message"] == "NO FIRMWARE LOADED"
    assert "demo" not in state
    assert "device" not in state


def test_firmware_state_is_the_only_screen_source():
    device = Device()
    device.handle(
        {
            "type": "firmware_state",
            "status": "ready",
            "artifact_id": 7,
            "filename": "passport.bin",
            "execution_status": "not-running",
        }
    )
    state = device.state()

    assert state["screen"]["status"] == "ready"
    assert state["screen"]["message"] == "FIRMWARE READY"
    assert state["screen"]["filename"] == "passport.bin"


def test_running_without_framebuffer_does_not_create_a_fake_screen():
    device = Device()
    device.handle(
        {
            "type": "firmware_state",
            "status": "running",
            "artifact_id": 9,
            "filename": "passport-full.bin",
            "execution_status": "running",
        }
    )
    device.handle({"type": "button", "button": "OK", "event": "CLICK"})
    state = device.state()

    assert state["screen"]["message"] == "FIRMWARE RUNNING / DISPLAY OUTPUT UNAVAILABLE"
    assert "frame_rgb565_b64" not in state["screen"]
    assert state["input"]["events"] == ["OK: CLICK"]
    assert any("GPIO bridge unavailable" in entry for entry in state["logs"])


def test_real_framebuffer_is_forwarded_unchanged():
    device = Device()
    frame = {
        "width": 240,
        "height": 320,
        "format": "rgb565-le",
        "frame_rgb565_b64": "AQID",
    }
    device.handle(
        {
            "type": "firmware_state",
            "status": "running",
            "artifact_id": 3,
            "filename": "frame.bin",
            "execution_status": "running",
            "framebuffer": frame,
        }
    )

    screen = device.state()["screen"]
    for key, value in frame.items():
        assert screen[key] == value


def test_json_lines_firmware_bridge_reports_real_input_available():
    device = Device()
    device.handle(
        {
            "type": "firmware_state",
            "status": "running",
            "artifact_id": 9,
            "filename": "codex-buddy-fixed.bin",
            "execution_status": "running",
            "input_bridge": "json-lines",
        }
    )
    device.handle({"type": "button", "button": "OK", "event": "CLICK"})

    state = device.state()

    assert state["input"]["available"] is True
    assert "connected" in state["input"]["reason"]
    assert state["firmware"]["input_bridge"] == "json-lines"


def test_demo_and_virtual_hardware_commands_are_ignored():
    device = Device()
    device.handle({"type": "demo_select", "demo_id": "tetris"})
    device.handle({"type": "sleep", "mode": "deep"})
    device.handle({"type": "audio", "action": "tone"})
    state = device.state()

    assert state["screen"]["status"] == "not-loaded"
    assert any("firmware-only mode" in entry for entry in state["logs"])


def test_audio_bridge_telemetry_and_partial_state_merge():
    device = Device()
    device.handle(
        {
            "type": "firmware_state",
            "status": "running",
            "artifact_id": 3,
            "filename": "fw.bin",
            "framebuffer": {"frame_rgb565_b64": "AA=="},
            "audio_bridge": "json-lines",
            "audio_out_frames": 2,
            "audio_in_frames": 5,
        }
    )
    state = device.state()
    assert state["audio"] == {
        "bridge": "json-lines",
        "available": True,
        "out_frames": 2,
        "in_frames": 5,
    }
    assert state["firmware"]["audio_bridge"] == "json-lines"

    # A narrow counter ping (audio endpoint) merges without clearing the rest.
    device.handle({"type": "firmware_state", "status": "running", "audio_in_frames": 6})
    state = device.state()
    assert state["audio"]["in_frames"] == 6
    assert state["audio"]["out_frames"] == 2
    assert device.firmware_filename == "fw.bin"
    assert device.framebuffer == {"frame_rgb565_b64": "AA=="}


def test_audio_defaults_to_unavailable_bridge():
    state = Device().state()
    assert state["audio"]["available"] is False
    assert state["audio"]["bridge"] == "unavailable"
