from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request
from sqlalchemy import select
from werkzeug.utils import secure_filename

from .database import database_session
from .firmware import FirmwareFormatError, analyze_firmware, qemu_is_configured
from .models import DeviceSnapshot, FirmwareArtifact, InputEvent, SimulationSession
from .plays import PlayCatalogError, PlayImportError, PlayNotFoundError

page_bp = Blueprint("pages", __name__)
api_bp = Blueprint("api", __name__)


@page_bp.get("/")
def index():
    return render_template(
        "index.html",
        allow_upload=current_app.config.get("AI_PASSPORT_ALLOW_UPLOAD", True),
    )


def _manager():
    return current_app.extensions["session_manager"]


def _state_with_runtime_frame(worker, run_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Overlay the newest backend frame without forcing the worker to parse it."""
    state = worker.latest_state or {"revision": 0, "page": "firmware"}
    if not run_info:
        return state
    framebuffer = run_info.get("framebuffer")
    firmware = dict(state.get("firmware") or {})
    screen = dict(state.get("screen") or {})
    execution_status = str(run_info.get("status") or firmware.get("execution_status") or "not-running")
    status_map = {
        "running": "running",
        "unavailable": "unavailable",
        "error": "error",
        "exited": "exited",
        "not-running": "ready",
    }
    screen_status = status_map.get(execution_status, screen.get("status", "ready"))
    firmware.update(
        {
            "execution_status": execution_status,
            "display_output": "framebuffer" if isinstance(framebuffer, dict) else firmware.get("display_output", "unavailable"),
            "input_bridge": run_info.get("input_bridge", firmware.get("input_bridge", "unavailable")),
            "audio_bridge": run_info.get("audio_bridge", firmware.get("audio_bridge", "unavailable")),
        }
    )
    screen.update(
        {
            "execution_status": execution_status,
            "display_output": "framebuffer" if isinstance(framebuffer, dict) else screen.get("display_output", "unavailable"),
            "status": screen_status,
            "reason": run_info.get("reason") or screen.get("reason"),
        }
    )
    if isinstance(framebuffer, dict):
        screen.update(framebuffer)
    state = {**state, "firmware": firmware, "screen": screen}
    audio = run_info.get("audio")
    state["audio"] = {
        "bridge": run_info.get("audio_bridge", "unavailable"),
        "available": run_info.get("audio_bridge") == "json-lines",
        "revision": run_info.get("audio_revision", 0),
        "out_frames": run_info.get("audio_out_frames", 0),
        "in_frames": run_info.get("audio_in_frames", 0),
        "frame": audio if isinstance(audio, dict) and audio.get("dir") != "record" else None,
    }
    return state


def _session_payload(worker, state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state or worker.latest_state or {"revision": 0, "page": "firmware"}
    return {
        "session_id": worker.session_id,
        "worker_pid": worker.process.pid,
        "worker_status": "running" if worker.process.poll() is None else "stopped",
        "state": state,
    }


def _firmware_payload(artifact: FirmwareArtifact) -> dict[str, Any]:
    try:
        analysis = json.loads(artifact.analysis_json)
    except json.JSONDecodeError:
        analysis = {}
    return {
        "id": artifact.id,
        "filename": artifact.original_filename,
        "stored_filename": artifact.stored_filename,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
        "load_status": artifact.load_status,
        "analysis": analysis,
        "execution": {
            "status": artifact.execution_status,
            "reason": artifact.execution_reason,
            "qemu_available": qemu_is_configured(),
        },
        "source": {
            "type": artifact.source_type,
            "play_slug": artifact.play_slug,
            "play_title": artifact.play_title,
            "play_source": artifact.play_source,
        },
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "updated_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
    }


def _firmware_path(artifact: FirmwareArtifact) -> Path:
    root = Path(current_app.config["AI_PASSPORT_FIRMWARE_DIR"]).resolve()
    path = Path(artifact.file_path).resolve()
    if path.parent != root:
        raise ValueError("firmware path is outside the managed firmware directory")
    return path


def _play_catalog():
    return current_app.extensions["play_catalog"]


@api_bp.get("/plays")
def list_plays():
    force = request.args.get("refresh", "").lower() in {"1", "true", "yes"}
    try:
        plays, fetched_at = _play_catalog().catalog(
            int(current_app.config["AI_PASSPORT_MAX_FIRMWARE_BYTES"]), force=force
        )
    except PlayCatalogError as error:
        return jsonify(
            {
                "error": str(error),
                "catalog_url": "https://ai-passport.folotoy.cn/api/plays/catalog",
            }
        ), 502
    return jsonify(
        {
            "plays": plays,
            "count": len(plays),
            "source": "official",
            "catalog_url": "https://ai-passport.folotoy.cn/api/plays/catalog",
            "fetched_at": fetched_at,
        }
    )


@api_bp.post("/plays/<slug>/download")
@api_bp.post("/plays/<slug>/import")
def download_play_firmware(slug: str):
    max_bytes = int(current_app.config["AI_PASSPORT_MAX_FIRMWARE_BYTES"])
    try:
        play = _play_catalog().find(slug, max_bytes)
        data = _play_catalog().download_firmware(play, max_bytes)
    except PlayNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except PlayImportError as error:
        return jsonify({"error": str(error)}), 400
    except PlayCatalogError as error:
        return jsonify({"error": str(error)}), 502

    try:
        analysis = analyze_firmware(data, f"{slug}.bin")
    except FirmwareFormatError as error:
        return jsonify({"error": f"downloaded firmware validation failed: {error}"}), 400

    with database_session(current_app) as session:
        existing = session.scalars(
            select(FirmwareArtifact)
            .where(FirmwareArtifact.sha256 == analysis["sha256"])
            .order_by(FirmwareArtifact.id.asc())
        ).first()
        if existing is not None:
            try:
                existing_path = _firmware_path(existing)
                reusable = existing.load_status == "validated" and existing_path.is_file()
            except ValueError:
                reusable = False
            if reusable:
                existing.source_type = "catalog"
                existing.play_slug = play["slug"]
                existing.play_title = play["title"]["zh"]
                existing.play_source = play["source"]
                payload = _firmware_payload(existing)
                return jsonify(
                    {
                        "play": play,
                        "firmware": payload,
                        "artifact": payload,
                        "imported": False,
                        "reused": True,
                    }
                )

        stored_filename = f"{uuid.uuid4().hex}.bin"
        root = Path(current_app.config["AI_PASSPORT_FIRMWARE_DIR"]).resolve()
        destination = root / stored_filename
        destination.write_bytes(data)
        try:
            artifact = FirmwareArtifact(
                original_filename=f"{slug}.bin",
                stored_filename=stored_filename,
                file_path=str(destination),
                sha256=analysis["sha256"],
                size_bytes=len(data),
                load_status="validated",
                analysis_json=json.dumps(analysis, ensure_ascii=False),
                execution_status="not-running",
                source_type="catalog",
                play_slug=play["slug"],
                play_title=play["title"]["zh"],
                play_source=play["source"],
            )
            session.add(artifact)
            session.flush()
            payload = _firmware_payload(artifact)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    return jsonify(
        {
            "play": play,
            "firmware": payload,
            "artifact": payload,
            "imported": True,
            "reused": False,
        }
    ), 201


def _sync_firmware_to_session(worker, artifact: FirmwareArtifact, run: dict[str, Any] | None = None):
    run_info = run or current_app.extensions["firmware_runtime"].status(artifact.id)
    execution_status = run_info.get("status", artifact.execution_status)
    if execution_status == "running":
        firmware_status = "running"
    elif execution_status in {"unavailable", "error", "exited"}:
        firmware_status = execution_status
    else:
        firmware_status = "ready"
    return worker.send(
        {
            "type": "firmware_state",
            "status": firmware_status,
            "artifact_id": artifact.id,
            "filename": artifact.original_filename,
            "execution_status": execution_status,
            "reason": run_info.get("reason") or artifact.execution_reason,
            "framebuffer": run_info.get("framebuffer"),
            "input_bridge": run_info.get("input_bridge", "unavailable"),
            "audio_bridge": run_info.get("audio_bridge", "unavailable"),
            "audio_out_frames": run_info.get("audio_out_frames", 0),
            "audio_in_frames": run_info.get("audio_in_frames", 0),
        }
    )


@api_bp.post("/sessions")
def create_session():
    body = request.get_json(silent=True) or {}
    scenario = body.get("scenario")
    if scenario is not None:
        return jsonify({"error": "host-side scenarios are removed; load firmware instead"}), 400
    worker = _manager().create()
    with database_session(current_app) as session:
        record = SimulationSession(
            id=worker.session_id,
            worker_pid=worker.process.pid,
            status="running",
            current_page=worker.latest_state.get("page", "firmware"),
            virtual_time_ms=worker.latest_state.get("virtual_time_ms", 0),
        )
        session.add(record)
    return jsonify(_session_payload(worker)), 201


@api_bp.get("/sessions/<session_id>")
def get_session(session_id: str):
    worker = _manager().get(session_id)
    if not worker:
        return jsonify({"error": "session not found"}), 404
    return jsonify(_session_payload(worker))


@api_bp.post("/sessions/<session_id>/firmware")
def load_firmware_into_session(session_id: str):
    worker = _manager().get(session_id)
    if not worker:
        return jsonify({"error": "session not found"}), 404
    body = request.get_json(silent=True) or {}
    try:
        artifact_id = int(body.get("artifact_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "artifact_id must be an integer"}), 400
    with database_session(current_app) as session:
        artifact = session.get(FirmwareArtifact, artifact_id)
        if artifact is None:
            return jsonify({"error": "firmware not found"}), 404
        if artifact.load_status != "validated":
            return jsonify({"error": "firmware must be validated before loading"}), 409
        try:
            state = _sync_firmware_to_session(worker, artifact)
        except RuntimeError as error:
            return jsonify({"error": str(error)}), 409
    return jsonify(_session_payload(worker))


@api_bp.delete("/sessions/<session_id>")
def delete_session(session_id: str):
    worker = _manager().get(session_id)
    if not worker:
        return jsonify({"error": "session not found"}), 404
    artifact_id = (worker.latest_state.get("firmware") or {}).get("artifact_id")
    if artifact_id:
        current_app.extensions["firmware_runtime"].stop(int(artifact_id))
    _manager().stop(session_id)
    with database_session(current_app) as session:
        record = session.get(SimulationSession, session_id)
        if record:
            record.status = "stopped"
    return jsonify({"status": "stopped", "session_id": session_id})


@api_bp.post("/sessions/<session_id>/commands")
def command_session(session_id: str):
    worker = _manager().get(session_id)
    if not worker:
        return jsonify({"error": "session not found"}), 404
    body = request.get_json(silent=True) or {}
    command_type = body.get("type", "button")
    if command_type == "button":
        button = body.get("button")
        event_type = body.get("event", "CLICK")
        if button not in {"UP", "DOWN", "OK"}:
            return jsonify({"error": "button must be UP, DOWN, or OK"}), 400
        if event_type not in {"PRESS", "CLICK", "DOUBLE", "LONG"}:
            return jsonify({"error": "unsupported button event"}), 400
        command = {"type": "button", "button": button, "event": event_type}
    elif command_type in {"snapshot", "reset"}:
        command = {"type": command_type}
    else:
        return jsonify({"error": "only firmware input, reset, and snapshot commands are supported"}), 400
    try:
        state = worker.send(command)
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409

    if command_type == "button":
        with database_session(current_app) as session:
            record = session.get(SimulationSession, session_id)
            if record:
                record.current_page = state.get("page", record.current_page)
                record.virtual_time_ms = state.get("virtual_time_ms", record.virtual_time_ms)
                session.add(
                    InputEvent(
                        session_id=session_id,
                        virtual_time_ms=state.get("virtual_time_ms", 0),
                        button=body["button"],
                        event_type=body.get("event", "CLICK"),
                        source=body.get("source", "web"),
                    )
                )
        if state.get("firmware", {}).get("artifact_id"):
            artifact_id = int(state["firmware"]["artifact_id"])
            runtime_result = current_app.extensions["firmware_runtime"].send_input(
                artifact_id, body["button"], body.get("event", "CLICK")
            )
            try:
                worker.send(
                    {
                        "type": "firmware_input",
                        "button": body["button"],
                        "event": body.get("event", "CLICK"),
                        **runtime_result,
                    }
                )
                state = worker.latest_state
            except RuntimeError:
                pass
    return jsonify(_session_payload(worker))


@api_bp.get("/sessions/<session_id>/frame")
def session_frame(session_id: str):
    worker = _manager().get(session_id)
    if not worker:
        return jsonify({"error": "session not found"}), 404
    artifact_id = (worker.latest_state.get("firmware") or {}).get("artifact_id")
    run_info = None
    if artifact_id:
        run_info = current_app.extensions["firmware_runtime"].status(int(artifact_id))
    state = _state_with_runtime_frame(worker, run_info)
    return jsonify({"session_id": session_id, "screen": state.get("screen", {})})


@api_bp.get("/sessions/<session_id>/snapshot")
def session_snapshot(session_id: str):
    worker = _manager().get(session_id)
    if not worker:
        return jsonify({"error": "session not found"}), 404
    return jsonify({"session_id": session_id, "state": worker.latest_state})


@api_bp.post("/sessions/<session_id>/snapshots")
def save_snapshot(session_id: str):
    worker = _manager().get(session_id)
    if not worker:
        return jsonify({"error": "session not found"}), 404
    body = request.get_json(silent=True) or {}
    label = str(body.get("label", "manual"))[:120]
    state = worker.latest_state
    with database_session(current_app) as session:
        snapshot = DeviceSnapshot(
            session_id=session_id, label=label, state_json=json.dumps(state, ensure_ascii=False)
        )
        session.add(snapshot)
        session.flush()
        snapshot_id = snapshot.id
    return jsonify({"id": snapshot_id, "session_id": session_id, "label": label, "state": state}), 201


@api_bp.get("/sessions/<session_id>/snapshots")
def list_snapshots(session_id: str):
    if not _manager().get(session_id):
        return jsonify({"error": "session not found"}), 404
    with database_session(current_app) as session:
        snapshots = session.scalars(
            select(DeviceSnapshot)
            .where(DeviceSnapshot.session_id == session_id)
            .order_by(DeviceSnapshot.id.desc())
        ).all()
    return jsonify(
        {
            "snapshots": [
                {"id": item.id, "label": item.label, "created_at": item.created_at.isoformat()}
                for item in snapshots
            ]
        }
    )


@api_bp.post("/firmware")
def upload_firmware():
    """Store and structurally validate one ESP32-C3 .bin image."""
    if not current_app.config.get("AI_PASSPORT_ALLOW_UPLOAD", True):
        return jsonify(
            {
                "error": "firmware upload is disabled on this instance; "
                "import a firmware from the official plays catalog instead"
            }
        ), 403
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "multipart field 'file' is required"}), 400

    original_filename = secure_filename(upload.filename)
    if not original_filename or not original_filename.lower().endswith(".bin"):
        return jsonify({"error": "only .bin firmware files are supported"}), 400

    max_bytes = int(current_app.config["AI_PASSPORT_MAX_FIRMWARE_BYTES"])
    data = upload.stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        return jsonify({"error": f"firmware file exceeds the {max_bytes} byte limit"}), 413
    if not data:
        return jsonify({"error": "firmware file is empty"}), 400

    try:
        analysis = analyze_firmware(data, original_filename)
    except FirmwareFormatError as error:
        return jsonify({"error": f"firmware validation failed: {error}"}), 400

    stored_filename = f"{uuid.uuid4().hex}.bin"
    root = Path(current_app.config["AI_PASSPORT_FIRMWARE_DIR"]).resolve()
    destination = root / stored_filename
    destination.write_bytes(data)
    try:
        with database_session(current_app) as session:
            artifact = FirmwareArtifact(
                original_filename=original_filename,
                stored_filename=stored_filename,
                file_path=str(destination),
                sha256=analysis["sha256"],
                size_bytes=len(data),
                load_status="validated",
                analysis_json=json.dumps(analysis, ensure_ascii=False),
                execution_status="not-running",
                source_type="upload",
            )
            session.add(artifact)
            session.flush()
            payload = _firmware_payload(artifact)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return jsonify(payload), 201


@api_bp.get("/firmware")
def list_firmware():
    with database_session(current_app) as session:
        artifacts = session.scalars(
            select(FirmwareArtifact).order_by(FirmwareArtifact.id.desc())
        ).all()
        payload = [_firmware_payload(item) for item in artifacts]
    return jsonify({"firmware": payload})


@api_bp.get("/firmware/<int:artifact_id>")
def get_firmware(artifact_id: int):
    with database_session(current_app) as session:
        artifact = session.get(FirmwareArtifact, artifact_id)
        if artifact is None:
            return jsonify({"error": "firmware not found"}), 404
        return jsonify(_firmware_payload(artifact))


@api_bp.post("/firmware/<int:artifact_id>/analyze")
def analyze_uploaded_firmware(artifact_id: int):
    with database_session(current_app) as session:
        artifact = session.get(FirmwareArtifact, artifact_id)
        if artifact is None:
            return jsonify({"error": "firmware not found"}), 404
        try:
            path = _firmware_path(artifact)
            analysis = analyze_firmware(path.read_bytes(), artifact.original_filename)
        except (OSError, ValueError, FirmwareFormatError) as error:
            artifact.load_status = "invalid"
            artifact.execution_status = "not-running"
            artifact.execution_reason = str(error)
            return jsonify({"error": f"firmware validation failed: {error}"}), 400
        artifact.sha256 = analysis["sha256"]
        artifact.size_bytes = analysis["size_bytes"]
        artifact.analysis_json = json.dumps(analysis, ensure_ascii=False)
        artifact.load_status = "validated"
        artifact.execution_status = "not-running"
        artifact.execution_reason = None
        return jsonify(_firmware_payload(artifact))


@api_bp.post("/firmware/<int:artifact_id>/run")
def run_firmware(artifact_id: int):
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    with database_session(current_app) as session:
        artifact = session.get(FirmwareArtifact, artifact_id)
        if artifact is None:
            return jsonify({"error": "firmware not found"}), 404
        if artifact.load_status != "validated":
            return jsonify({"error": "firmware must be validated before execution"}), 409
        try:
            status = current_app.extensions["firmware_runtime"].start(
                artifact.id, _firmware_path(artifact)
            )
        except (OSError, ValueError) as error:
            status = {"artifact_id": artifact.id, "status": "error", "reason": str(error)}
        artifact.execution_status = status.get("status", "error")
        artifact.execution_reason = status.get("reason")
        payload = {"firmware": _firmware_payload(artifact), "run": status}
        if session_id:
            worker = _manager().get(str(session_id))
            if worker:
                try:
                    _sync_firmware_to_session(worker, artifact, status)
                    payload["session"] = _session_payload(worker)
                except RuntimeError as error:
                    payload["session_error"] = str(error)
        return jsonify(payload)


@api_bp.get("/firmware/<int:artifact_id>/run")
def firmware_run_status(artifact_id: int):
    session_id = request.args.get("session_id")
    include_frame = request.args.get("include_frame", "1").lower() not in {"0", "false", "no"}
    with database_session(current_app) as session:
        artifact = session.get(FirmwareArtifact, artifact_id)
        if artifact is None:
            return jsonify({"error": "firmware not found"}), 404
        status = current_app.extensions["firmware_runtime"].status(artifact.id)
        if not include_frame:
            status = {**status, "framebuffer": None, "audio": None}
        if status["status"] in {"exited", "not-running"} and artifact.execution_status == "running":
            artifact.execution_status = status["status"]
            artifact.execution_reason = (
                f"process exited with code {status.get('exit_code')}"
                if status["status"] == "exited"
                else artifact.execution_reason
            )
        payload = {"firmware": _firmware_payload(artifact), "run": status}
        if session_id:
            worker = _manager().get(session_id)
            if worker:
                if include_frame:
                    try:
                        _sync_firmware_to_session(worker, artifact, status)
                        payload["session"] = _session_payload(
                            worker, _state_with_runtime_frame(worker, status)
                        )
                    except RuntimeError as error:
                        payload["session_error"] = str(error)
        return jsonify(payload)


@api_bp.get("/firmware/<int:artifact_id>/audio")
def firmware_audio_queue(artifact_id: int):
    """Lossless speaker-frame pull endpoint.

    SSE snapshots only carry the newest audio frame, so any frame produced
    between two pushes would be lost and the played stream would crackle.
    The speaker client polls this endpoint instead and replays every queued
    frame in order; `since` positions the client inside the ring buffer.
    """
    since = request.args.get("since", default=-1, type=int)
    if since < -1:
        since = -1
    result = current_app.extensions["firmware_runtime"].audio_queue(artifact_id, since)
    return jsonify(result)


@api_bp.post("/firmware/<int:artifact_id>/stop")
def stop_firmware(artifact_id: int):
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    with database_session(current_app) as session:
        artifact = session.get(FirmwareArtifact, artifact_id)
        if artifact is None:
            return jsonify({"error": "firmware not found"}), 404
        status = current_app.extensions["firmware_runtime"].stop(artifact.id)
        artifact.execution_status = status.get("status", "not-running")
        artifact.execution_reason = status.get("reason")
        payload = {"firmware": _firmware_payload(artifact), "run": status}
        if session_id:
            worker = _manager().get(str(session_id))
            if worker:
                try:
                    _sync_firmware_to_session(worker, artifact, status)
                    payload["session"] = _session_payload(worker)
                except RuntimeError as error:
                    payload["session_error"] = str(error)
        return jsonify(payload)


MAX_AUDIO_FRAME_CHARS = 96_000  # ~72 KB PCM per forwarded frame


@api_bp.post("/sessions/<session_id>/audio")
def forward_session_audio(session_id: str):
    """Forward one captured-microphone PCM frame to the firmware backend."""
    worker = _manager().get(session_id)
    if not worker:
        return jsonify({"error": "session not found"}), 404
    body = request.get_json(silent=True) or {}
    samples = body.get("samples")
    if not isinstance(samples, str) or not samples:
        return jsonify({"error": "samples (base64 PCM) is required"}), 400
    if len(samples) > MAX_AUDIO_FRAME_CHARS:
        return jsonify({"error": "audio frame exceeds the transport limit"}), 413
    try:
        rate = int(body.get("rate", 16_000))
        channels = int(body.get("channels", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "rate and channels must be integers"}), 400
    if not 8_000 <= rate <= 96_000:
        return jsonify({"error": "rate must be between 8000 and 96000 Hz"}), 400
    if channels not in {1, 2}:
        return jsonify({"error": "channels must be 1 or 2"}), 400
    format_name = str(body.get("format", "pcm-s16le"))
    if format_name != "pcm-s16le":
        return jsonify({"error": "only pcm-s16le samples are supported"}), 400

    artifact_id = (worker.latest_state.get("firmware") or {}).get("artifact_id")
    if not artifact_id:
        return jsonify({"accepted": False, "reason": "no firmware is loaded"}), 409
    result = current_app.extensions["firmware_runtime"].send_audio(
        int(artifact_id),
        {
            "format": format_name,
            "rate": rate,
            "channels": channels,
            "samples": samples,
        },
    )
    if result.get("accepted"):
        run_info = current_app.extensions["firmware_runtime"].status(int(artifact_id))
        try:
            worker.send(
                {
                    "type": "firmware_state",
                    "status": "running",
                    "artifact_id": int(artifact_id),
                    "audio_bridge": run_info.get("audio_bridge", "json-lines"),
                    "audio_out_frames": run_info.get("audio_out_frames", 0),
                    "audio_in_frames": run_info.get("audio_in_frames", 0),
                },
                wait_for_state=False,
            )
        except RuntimeError:
            pass
    status_code = 200 if result.get("accepted") else 409
    return jsonify(result), status_code


@api_bp.get("/sessions/<session_id>/events")
def session_events(session_id: str):
    if not _manager().get(session_id):
        return jsonify({"error": "session not found"}), 404
    with database_session(current_app) as session:
        events = session.scalars(
            select(InputEvent)
            .where(InputEvent.session_id == session_id)
            .order_by(InputEvent.id.desc())
            .limit(50)
        ).all()
    return jsonify(
        {
            "events": [
                {
                    "button": event.button,
                    "event": event.event_type,
                    "source": event.source,
                    "virtual_time_ms": event.virtual_time_ms,
                }
                for event in reversed(events)
            ]
        }
    )


@api_bp.get("/sessions/<session_id>/stream")
def session_stream(session_id: str):
    manager = _manager()
    worker = manager.get(session_id)
    if not worker:
        return jsonify({"error": "session not found"}), 404
    app = current_app._get_current_object()

    def generate():
        last_revision = -1
        last_frame_revision = -1
        last_audio_revision = -1
        last_execution_status = None
        last_heartbeat = time.monotonic()
        while manager.get(session_id) is worker and worker.process.poll() is None:
            base_state = worker.latest_state
            artifact_id = (base_state.get("firmware") or {}).get("artifact_id")
            run_info = None
            if artifact_id:
                run_info = app.extensions["firmware_runtime"].status(int(artifact_id))
            state = _state_with_runtime_frame(worker, run_info)
            revision = int(state.get("revision", 0))
            frame_revision = int((run_info or {}).get("frame_revision", 0))
            audio_revision = int((run_info or {}).get("audio_revision", 0))
            execution_status = (run_info or {}).get("status")
            if (
                revision != last_revision
                or frame_revision != last_frame_revision
                or audio_revision != last_audio_revision
                or execution_status != last_execution_status
            ):
                yield f"data: {json.dumps(_session_payload(worker, state), ensure_ascii=False)}\n\n"
                last_revision = revision
                last_frame_revision = frame_revision
                last_audio_revision = audio_revision
                last_execution_status = execution_status
                last_heartbeat = time.monotonic()
            elif time.monotonic() - last_heartbeat >= 15:
                yield ": simulator heartbeat\n\n"
                last_heartbeat = time.monotonic()
            time.sleep(0.016)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
