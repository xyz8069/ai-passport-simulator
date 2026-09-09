from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from flask import Flask


@dataclass
class WorkerClient:
    session_id: str
    process: subprocess.Popen[str]
    latest_state: dict[str, Any] = field(default_factory=dict)
    latest_revision: int = 0
    output_queue: queue.Queue[dict[str, Any]] = field(default_factory=queue.Queue)
    condition: threading.Condition = field(default_factory=threading.Condition)
    write_lock: threading.Lock = field(default_factory=threading.Lock)
    ready: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()

    def _read_output(self) -> None:
        assert self.process.stdout is not None
        for raw_line in self.process.stdout:
            try:
                message = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            self.output_queue.put(message)
            if message.get("type") == "state":
                with self.condition:
                    self.latest_state = message["state"]
                    self.latest_revision = int(message["state"].get("revision", 0))
                    self.ready.set()
                    self.condition.notify_all()
        with self.condition:
            self.condition.notify_all()

    def send(self, command: dict[str, Any], wait_for_state: bool = True) -> dict[str, Any]:
        if self.process.poll() is not None:
            raise RuntimeError("simulator worker is not running")
        with self.write_lock:
            before = self.latest_revision
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        if not wait_for_state:
            return self.latest_state
        with self.condition:
            self.condition.wait_for(
                lambda: self.latest_revision > before or self.process.poll() is not None,
                timeout=1.0,
            )
            return self.latest_state

    def stop(self) -> None:
        if self.process.poll() is None:
            try:
                self.send({"type": "shutdown"}, wait_for_state=False)
            except (BrokenPipeError, RuntimeError):
                pass
            try:
                self.process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=1.0)


class SessionManager:
    def __init__(self, app: Flask) -> None:
        self.app = app
        self.worker_path = Path(app.config["AI_PASSPORT_WORKER"])
        self._workers: dict[str, WorkerClient] = {}
        self._lock = threading.RLock()

    def create(self) -> WorkerClient:
        session_id = uuid.uuid4().hex[:16]
        command = [sys.executable, str(self.worker_path), "--session", session_id]
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        worker = WorkerClient(session_id, process)
        with self._lock:
            self._workers[session_id] = worker
        if not worker.ready.wait(timeout=1.0):
            self.stop(session_id)
            raise RuntimeError("simulator worker did not become ready")
        return worker

    def get(self, session_id: str) -> Optional[WorkerClient]:
        with self._lock:
            return self._workers.get(session_id)

    def stop(self, session_id: str) -> None:
        with self._lock:
            worker = self._workers.pop(session_id, None)
        if worker:
            worker.stop()

    def stop_all(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.stop()
