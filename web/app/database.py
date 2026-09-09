from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from flask import Flask
from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def init_database(app: Flask) -> None:
    engine = create_engine(
        app.config["AI_PASSPORT_DATABASE_URL"],
        connect_args={"check_same_thread": False},
    )
    if engine.url.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    Base.metadata.create_all(engine)
    if engine.url.get_backend_name() == "sqlite":
        _migrate_firmware_source_columns(engine)
    app.extensions["database_engine"] = engine
    app.extensions["database_session"] = sessionmaker(
        bind=engine, expire_on_commit=False, class_=Session
    )


def _migrate_firmware_source_columns(engine: Engine) -> None:
    """Add catalog provenance to databases created by older simulator builds."""
    columns = {column["name"] for column in inspect(engine).get_columns("firmware_artifacts")}
    additions = {
        "source_type": "VARCHAR(24) NOT NULL DEFAULT 'upload'",
        "play_slug": "VARCHAR(128)",
        "play_title": "VARCHAR(255)",
        "play_source": "VARCHAR(24)",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE firmware_artifacts ADD COLUMN {name} {definition}"))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_firmware_artifacts_play_slug "
                "ON firmware_artifacts (play_slug)"
            )
        )


@contextmanager
def database_session(app: Flask) -> Iterator[Session]:
    factory = app.extensions["database_session"]
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_engine(app: Flask) -> Engine:
    return app.extensions["database_engine"]
