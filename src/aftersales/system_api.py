"""Actual dependency/queue records and a persistent pause switch."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import func, select

from .auth import current_user, write_user
from .business_models import BusinessJob, Operation
from .db import as_utc, utcnow
from .domain import idempotent, run_transaction
from .models import Conversation, Job, OutboundMessage, SystemSetting, WorkerHeartbeat

router = APIRouter(prefix="/api/v1/admin")


def is_agent_enabled(settings, session):
    row = session.get(SystemSetting, "agent_enabled")
    return settings.agent_enabled and (row is None or row.value.get("enabled") is True)


def heartbeat(factory, name):
    with factory.begin() as session:
        row = session.get(WorkerHeartbeat, name)
        if row is None:
            session.add(WorkerHeartbeat(name=name))
        elif as_utc(row.observed_at) < utcnow() - timedelta(seconds=5):
            row.observed_at = utcnow()


def admin_read(user=Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "Admin role required")
    return user


def admin_write(user=Depends(write_user)):
    if user.role != "admin":
        raise HTTPException(403, "Admin role required")
    return user


@router.get("/system")
def system_state(request: Request, user=Depends(admin_read)):
    settings = request.app.state.settings
    with request.app.state.session_factory() as session:
        enabled = is_agent_enabled(settings, session)
        def count(model, states):
            return session.scalar(select(func.count()).select_from(model).where(model.status.in_(states)))
        pending = count(Job, ["PENDING", "RUNNING"]) + count(BusinessJob, ["PENDING", "RUNNING"])
        failed = count(Job, ["FAILED", "UNKNOWN"]) + count(BusinessJob, ["FAILED", "BLOCKED", "UNKNOWN", "SENDING"])
        uncertain_notifications = session.scalar(select(func.count()).select_from(BusinessJob).where(
            BusinessJob.kind.in_(["staff_notification", "manual_operation"]), BusinessJob.status.in_(["SENDING", "UNKNOWN"])))
        unknown = count(Operation, ["UNKNOWN"])
        unknown_messages = count(OutboundMessage, ["UNKNOWN", "SENDING"])
        heartbeats = {row.name: as_utc(row.observed_at).isoformat() for row in session.scalars(select(WorkerHeartbeat))}
        dependencies = [{"name": "database", "status": "up"}]
        for name in ["chat-worker", "operations-worker"]:
            observed = session.get(WorkerHeartbeat, name)
            dependencies.append({"name": name, "status": "up" if observed and as_utc(observed.observed_at) > utcnow() - timedelta(seconds=30) else "unknown"})
        dependencies.extend([{"name": "model", "status": "configured_unverified" if settings.model_provider and settings.model_name else "not_configured"},
                             {"name": "chatwoot", "status": "see live probe"}])
    return {"agent_enabled": enabled, "dependencies": dependencies, "pending_jobs": pending,
            "failed_jobs": failed, "unknown_operations": unknown, "unknown_messages": unknown_messages,
            "uncertain_private_notifications": uncertain_notifications,
            "heartbeats": heartbeats, "mode": settings.app_mode, "environment_id": settings.environment_id}


class SwitchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.patch("/system/agent")
def switch_agent(data: SwitchInput, request: Request, user=Depends(admin_write), idempotency_key: str = Header(default="")):
    def action(session):
        def apply():
            row = session.get(SystemSetting, "agent_enabled", with_for_update=True)
            if row is None:
                session.add(SystemSetting(key="agent_enabled", value={"enabled": data.enabled}))
            else:
                row.value = {"enabled": data.enabled}
            if not data.enabled:
                # Invalidate in-flight inference immediately; do not affect authorized fulfillment.
                for conv in session.scalars(select(Conversation).where(Conversation.control_mode == "BOT_ACTIVE").with_for_update()):
                    conv.control_revision += 1
            return {"agent_enabled": data.enabled}
        return idempotent(session, user.user_id, "agent_switch", idempotency_key, data.model_dump(), apply)
    return run_transaction(request.app.state.session_factory, action)
