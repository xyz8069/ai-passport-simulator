from __future__ import annotations

import sys
from pathlib import Path

import pytest

WEB_ROOT = Path(__file__).resolve().parents[1]
if str(WEB_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_ROOT))

from app import create_app


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
