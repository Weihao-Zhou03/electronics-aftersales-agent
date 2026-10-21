"""Provider-owned durable state, isolated from the business database."""
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Environment(Base):
    __tablename__ = "provider_environment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_mode: Mapped[str] = mapped_column(String(16))
    environment_id: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(32))


class Operation(Base):
    __tablename__ = "provider_operations"
    __table_args__ = (CheckConstraint("kind IN ('refund','shipment')", name="ck_provider_kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    environment_id: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Fault(Base):
    __tablename__ = "provider_faults"
    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32))


class Callback(Base):
    __tablename__ = "provider_callbacks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    operation_id: Mapped[str] = mapped_column(String(36), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_until: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
