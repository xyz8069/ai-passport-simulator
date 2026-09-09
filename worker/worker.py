#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Optional


ALLOWED_BUTTONS = {"UP", "DOWN", "OK"}
ALLOWED_EVENTS = {"PRESS", "CLICK", "DOUBLE", "LONG"}
ALLOWED_FIRMWARE_STATUSES = {
    "not-loaded",
    "ready",
    "running",
    "unavailable",
    "exited",
    "error",
}


@dataclass
class Device:
    """Session telemetry only.

    The worker deliberately does not implement an application model or draw a
    substitute screen. The loaded firmware is the only source of device
    behavior; until a real framebuffer bridge is available, the worker exposes
    status and input telemetry without pretending to execute firmware UI logic.
    """

    page: str = "firmware"
    revision: int = 0
    virtual_time_ms: int = 0
    button_log: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    firmware_status: str = "not-loaded"
    firmware_id: int | None = None
    firmware_filename: str | None = None
    firmware_execution_status: str = "not-running"
    firmware_execution_reason: str | None = None
    firmware_input_bridge: str = "unavailable"
    audio_bridge: str = "unavailable"
    audio_out_frames: int = 0
    audio_in_frames: int = 0
    framebuffer: dict[str, Any] | None = None

    def log(self, message: str) -> None:
        self.logs.append(message)
        del self.logs[:-12]

    def handle(self, command: dict[str, Any]) -> None:
        command_type = command.get("type")
        if command_type == "shutdown":
            raise SystemExit
        if command_type == "reset":
            self.page = "firmware"
            self.virtual_time_ms = 0
            self.button_log.clear()
            self.framebuffer = None
            self.log("session telemetry reset; firmware selection preserved")
            return
        if command_type == "button":
            self._record_button(command.get("button"), command.get("event", "CLICK"))
            return
        if command_type == "firmware_state":
            self._set_firmware_state(command)
            return
        if command_type == "firmware_input":
            button = command.get("button", "?")
            event = command.get("event", "CLICK")
            if command.get("accepted"):
                self.log(f"firmware input forwarded: {button} {event}")
            else:
                self.log(
                    f"firmware input unavailable: {button} {event}; "
                    f"{command.get('reason', 'backend rejected input')}"
                )
            return
        self.log(f"command ignored in firmware-only mode: {command_type or 'unknown'}")

    def _record_button(self, button: Optional[str], event: str) -> None:
        if button not in ALLOWED_BUTTONS or event not in ALLOWED_EVENTS:
            self.log("input ignored: invalid button event")
            return
        self.virtual_time_ms += 100
        line = f"{button}: {event}"
        self.button_log.append(line)
        del self.button_log[:-12]
        if self.firmware_status == "running" and self.firmware_input_bridge != "unavailable":
            self.log(f"input queued for firmware: {line}")
        elif self.firmware_status in {"ready", "not-loaded"}:
            self.log(f"input blocked: {line}; start a loaded firmware image first")
        elif self.firmware_status == "running":
            self.log(f"input blocked: {line}; firmware GPIO bridge unavailable")
        else:
            self.log(f"input blocked: {line}; firmware status is {self.firmware_status}")

    def _set_firmware_state(self, command: dict[str, Any]) -> None:
        status = str(command.get("status", self.firmware_status))
        if status not in ALLOWED_FIRMWARE_STATUSES:
            raise ValueError(f"unsupported firmware status: {status}")
        self.firmware_status = status
        # Key-presence semantics: full syncs from the web layer always carry
        # every key (explicit None clears), while narrow pings (audio counters)
        # omit keys they do not touch.
        if "artifact_id" in command:
            artifact_id = command.get("artifact_id")
            self.firmware_id = int(artifact_id) if artifact_id is not None else None
        if "filename" in command:
            filename = command.get("filename")
            self.firmware_filename = str(filename) if filename else None
        if "execution_status" in command:
            self.firmware_execution_status = str(command.get("execution_status") or "not-running")
        if "input_bridge" in command:
            self.firmware_input_bridge = str(command.get("input_bridge") or "unavailable")
        self.audio_bridge = str(command.get("audio_bridge", self.audio_bridge))
        self.audio_out_frames = int(command.get("audio_out_frames", self.audio_out_frames))
        self.audio_in_frames = int(command.get("audio_in_frames", self.audio_in_frames))
        if "reason" in command:
            reason = command.get("reason")
            self.firmware_execution_reason = str(reason) if reason else None
        if "framebuffer" in command:
            frame = command.get("framebuffer")
            self.framebuffer = frame if isinstance(frame, dict) else None
        label = self.firmware_filename or "no image"
        self.log(f"firmware state: {status} ({label})")

    def state(self) -> dict[str, Any]:
        self.revision += 1
        input_available = (
            self.firmware_status == "running"
            and self.firmware_input_bridge != "unavailable"
        )
        if input_available:
            input_reason = "Firmware GPIO/ADC input bridge connected"
        elif self.firmware_status == "running":
            input_reason = "A real firmware GPIO/input bridge is not connected"
        else:
            input_reason = "Start a loaded firmware image to connect the GPIO/ADC input bridge"
        return {
            "revision": self.revision,
            "page": self.page,
            "virtual_time_ms": self.virtual_time_ms,
            "firmware": {
                "status": self.firmware_status,
                "artifact_id": self.firmware_id,
                "filename": self.firmware_filename,
                "execution_status": self.firmware_execution_status,
                "reason": self.firmware_execution_reason,
                "display_output": "framebuffer" if self.framebuffer else "unavailable",
                "input_bridge": self.firmware_input_bridge,
                "audio_bridge": self.audio_bridge,
            },
            "screen": self.screen(),
            "input": {
                "events": self.button_log,
                "available": input_available,
                "reason": input_reason,
            },
            "audio": {
                "bridge": self.audio_bridge,
                "available": self.audio_bridge != "unavailable",
                "out_frames": self.audio_out_frames,
                "in_frames": self.audio_in_frames,
            },
            "logs": self.logs,
        }

    def screen(self) -> dict[str, Any]:
        screen = {
            "title": "FIRMWARE CONSOLE",
            "kind": "firmware",
            "status": self.firmware_status,
            "artifact_id": self.firmware_id,
            "filename": self.firmware_filename,
            "execution_status": self.firmware_execution_status,
            "reason": self.firmware_execution_reason,
            "display_output": "framebuffer" if self.framebuffer else "unavailable",
            "input_bridge": self.firmware_input_bridge,
            "message": self._screen_message(),
            "hint": "LOAD AND RUN A FIRMWARE IMAGE IN FIRMWARE LAB",
        }
        if self.framebuffer:
            screen.update(self.framebuffer)
        return screen

    def _screen_message(self) -> str:
        if self.firmware_status == "not-loaded":
            return "NO FIRMWARE LOADED"
        if self.firmware_status == "ready":
            return "FIRMWARE READY"
        if self.firmware_status == "running":
            return "FIRMWARE RUNNING / DISPLAY OUTPUT UNAVAILABLE"
        if self.firmware_status == "unavailable":
            return "FIRMWARE LOADED / EXECUTION UNAVAILABLE"
        if self.firmware_status == "exited":
            return "FIRMWARE EXECUTION EXITED"
        return "FIRMWARE EXECUTION ERROR"

    def close(self) -> None:
        return None


def run(session_id: str, emit_ready: bool = True) -> None:
    device = Device()
    try:
        if emit_ready:
            print(json.dumps({"type": "ready", "session_id": session_id}), flush=True)
        print(json.dumps({"type": "state", "state": device.state()}), flush=True)
        for raw_line in sys.stdin:
            try:
                command = json.loads(raw_line)
                device.handle(command)
                print(json.dumps({"type": "state", "state": device.state()}, ensure_ascii=False), flush=True)
            except SystemExit:
                break
            except (TypeError, ValueError, KeyError) as error:
                device.log(f"command error: {error}")
                print(json.dumps({"type": "state", "state": device.state()}, ensure_ascii=False), flush=True)
    finally:
        device.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="headless")
    args = parser.parse_args()
    run(args.session)
