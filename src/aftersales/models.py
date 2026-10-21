"""Minimal M0 tables; business orders/cases/LLM state belong to later milestones."""
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .db import utcnow


def new_id():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class RateBucket(Base):
    __tablename__ = "rate_limits"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    observed_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatBinding(Base):
    __tablename__ = "chat_bindings"
    __table_args__ = (UniqueConstraint("instance_id", "account_id", "inbox_id", "contact_id", name="uq_chat_binding_scope"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    instance_id: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[int] = mapped_column(Integer)
    inbox_id: Mapped[int] = mapped_column(Integer)
    contact_id: Mapped[int] = mapped_column(Integer)
    customer_id: Mapped[str] = mapped_column(String(36))
    auth_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source: Mapped[str] = mapped_column(String(128))
    verified_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("instance_id", "account_id", "external_id", name="uq_conversation_scope"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    instance_id: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[int] = mapped_column(Integer)
    inbox_id: Mapped[int] = mapped_column(Integer)
    external_id: Mapped[int] = mapped_column(Integer)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    control_mode: Mapped[str] = mapped_column(String(32), default="BOT_ACTIVE")
    control_revision: Mapped[int] = mapped_column(Integer, default=0)
    input_revision: Mapped[int] = mapped_column(Integer, default=0)
    last_processed_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_remote_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fencing_revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class WebhookEvent(Base):
    __tablename__ = "webhook_inbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_key: Mapped[str] = mapped_column(String(512), unique=True)
    provider: Mapped[str] = mapped_column(String(32), default="chatwoot")
    channel: Mapped[str] = mapped_column(String(24))
    event_type: Mapped[str] = mapped_column(String(64))
    delivery_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="ACCEPTED")
    received_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_ready", "status", "available_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32), default="fixed_reply")
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    event_id: Mapped[str | None] = mapped_column(ForeignKey("webhook_inbox.id"), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(512), unique=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_version: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(256), nullable=True)


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    intent_key: Mapped[str] = mapped_column(String(255), unique=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    content: Mapped[str] = mapped_column(Text)
    control_revision: Mapped[int] = mapped_column(Integer)
    input_revision: Mapped[int] = mapped_column(Integer)
    fencing_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="PREPARED")
    chatwoot_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class ControlAudit(Base):
    __tablename__ = "conversation_control_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboundReconciliation(Base):
    __tablename__ = "outbound_reconciliations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    outbound_id: Mapped[str] = mapped_column(ForeignKey("outbound_messages.id"), unique=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    outcome: Mapped[str] = mapped_column(String(24))
    control_revision: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(OutboundReconciliation, "before_update")
@event.listens_for(OutboundReconciliation, "before_delete")
def immutable_outbound_reconciliation(_mapper, _connection, _target):
    raise ValueError("outbound reconciliation audit is immutable")
