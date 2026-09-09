from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SimulationSession(Base):
    __tablename__ = "simulation_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    worker_pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="starting", nullable=False)
    current_page: Mapped[str] = mapped_column(String(80), default="firmware", nullable=False)
    virtual_time_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    input_events: Mapped[list["InputEvent"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class InputEvent(Base):
    __tablename__ = "input_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("simulation_sessions.id"), nullable=False)
    virtual_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    button: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="web", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[SimulationSession] = relationship(back_populates="input_events")


class DeviceSnapshot(Base):
    __tablename__ = "device_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("simulation_sessions.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(120), default="manual", nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FirmwareArtifact(Base):
    """Metadata for a validated firmware file stored outside SQLite."""

    __tablename__ = "firmware_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    load_status: Mapped[str] = mapped_column(String(24), default="uploaded", nullable=False)
    analysis_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    execution_status: Mapped[str] = mapped_column(String(24), default="not-running", nullable=False)
    execution_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(24), default="upload", nullable=False)
    play_slug: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    play_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    play_source: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
