from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from flask import Flask

from .database import database_session, init_database
from .firmware import FirmwareRuntime
from .models import FirmwareArtifact
from .plays import PlayCatalogClient
from .routes import api_bp, page_bp
from .services.session_manager import SessionManager


def create_app(test_config: Optional[dict] = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("AI_PASSPORT_SECRET_KEY", "local-simulator-key"),
        AI_PASSPORT_DATABASE_URL=os.environ.get(
            "AI_PASSPORT_DATABASE_URL",
            f"sqlite:///{Path(app.instance_path) / 'ai_passport.sqlite3'}",
        ),
        AI_PASSPORT_WORKER=str(
            Path(__file__).resolve().parents[2] / "worker" / "worker.py"
        ),
        AI_PASSPORT_FIRMWARE_DIR=str(Path(app.instance_path) / "firmware"),
        AI_PASSPORT_MAX_FIRMWARE_BYTES=8 * 1024 * 1024,
        MAX_CONTENT_LENGTH=8 * 1024 * 1024 + 64 * 1024,
        AI_PASSPORT_PLAY_CATALOG_TTL=60,
        AI_PASSPORT_PLAY_CATALOG_TIMEOUT=20,
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["AI_PASSPORT_FIRMWARE_DIR"]).mkdir(parents=True, exist_ok=True)
    init_database(app)
    app.extensions["session_manager"] = SessionManager(app)
    app.extensions["firmware_runtime"] = FirmwareRuntime()
    # A QEMU child process is owned by the current Flask process and is not
    # recoverable after a server restart.  Clear persisted RUNNING markers so
    # the UI does not disable RUN FIRMWARE or claim that a framebuffer exists
    # when the new runtime has no attached process.
    with database_session(app) as session:
        session.query(FirmwareArtifact).filter(
            FirmwareArtifact.execution_status == "running"
        ).update(
            {
                FirmwareArtifact.execution_status: "not-running",
                FirmwareArtifact.execution_reason: "simulator restarted; previous firmware process is no longer attached",
            },
            synchronize_session=False,
        )
    app.extensions["play_catalog"] = PlayCatalogClient(
        timeout=int(app.config["AI_PASSPORT_PLAY_CATALOG_TIMEOUT"]),
        cache_ttl=int(app.config["AI_PASSPORT_PLAY_CATALOG_TTL"]),
    )
    app.register_blueprint(page_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "ai-passport-web-simulator"}

    return app


app = create_app()
