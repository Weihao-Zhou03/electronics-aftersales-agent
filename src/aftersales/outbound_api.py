"""Staff reconciliation of uncertain chat delivery; old intents are never resent."""
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .auth import current_user, staff_user
from .business_models import IdempotencyRequest
from .db import as_utc, utcnow
from .domain import DomainError, digest, idempotent, require, run_transaction, timestamp
from .models import Conversation, Job, OutboundMessage, OutboundReconciliation

router = APIRouter(prefix="/api/v1/staff/outbound-messages")


class ReconcileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_control_revision: int = Field(ge=0)
    outcome: Literal["delivered", "not_delivered"]
    message_id: int | None = Field(default=None, gt=0)
    evidence: dict


def _staff(user=Depends(current_user)):
    if user.role not in {"support", "admin"}:
        raise HTTPException(403, "Staff role required")
    return user


def _scope(settings, conversation):
    require(conversation is not None and conversation.instance_id == settings.chatwoot_instance_id
        and conversation.account_id == settings.chatwoot_account_id and conversation.inbox_id == settings.chatwoot_inbox_id,
        "RESOURCE_NOT_ACCESSIBLE", 404)


def _audit_dict(row):
    return {"id": row.id, "actor_id": row.actor_id, "outcome": row.outcome, "control_revision": row.control_revision,
            "version": row.version, "message_id": row.message_id, "evidence": row.evidence, "created_at": timestamp(row.created_at)}


def outbound_dict(session, row, conversation):
    return {"id": row.id, "external_conversation_id": conversation.external_id, "status": row.status,
            "content": row.content, "control_revision": row.control_revision,
            "current_control_revision": conversation.control_revision, "control_mode": conversation.control_mode,
            "input_revision": row.input_revision, "chatwoot_message_id": row.chatwoot_message_id,
            "created_at": timestamp(row.created_at), "reconciliations": [_audit_dict(audit) for audit in session.scalars(
                select(OutboundReconciliation).where(OutboundReconciliation.outbound_id == row.id))]}


def reconcile_outbound(settings, factory, adapter, outbound_id, expected_control_revision, outcome,
                       evidence, actor_id, key, message_id=None):
    require(outcome in {"delivered", "not_delivered"}, "OUTBOUND_OUTCOME_INVALID", 422)
    require(bool(key) and len(key) <= 128, "IDEMPOTENCY_KEY_REQUIRED", 422)
    require(isinstance(evidence, dict) and all(isinstance(evidence.get(name), str) and 5 <= len(evidence[name].strip()) <= 2000
            for name in ["reason", "proof"]), "OUTBOUND_RECONCILIATION_PROOF_REQUIRED", 422)
    request = {"expected_control_revision": expected_control_revision, "outcome": outcome, "evidence": evidence,
               "message_id": message_id}
    scope = "outbound_reconcile:" + outbound_id
    with factory() as session:
        prior = session.scalar(select(IdempotencyRequest).where(IdempotencyRequest.actor_id == actor_id,
            IdempotencyRequest.route_scope == scope, IdempotencyRequest.key == key))
        if prior:
            require(prior.request_hash == digest(request), "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH")
            return prior.response
        outbound = session.get(OutboundMessage, outbound_id)
        require(outbound is not None, "RESOURCE_NOT_ACCESSIBLE", 404)
        conversation = session.get(Conversation, outbound.conversation_id)
        _scope(settings, conversation)
        require(conversation.control_revision == expected_control_revision, "CONTROL_REVISION_CONFLICT")
        require(conversation.control_mode in {"HUMAN_ACTIVE", "HANDOFF_PENDING"}, "PAUSE_BEFORE_RECONCILIATION")
        require(outbound.status in {"UNKNOWN", "SENDING"}, "OUTBOUND_RECONCILIATION_STATE_CONFLICT")
        if outbound.status == "SENDING":
            require((utcnow() - as_utc(outbound.created_at)).total_seconds() > settings.worker_lease_seconds,
                    "OUTBOUND_SEND_MAY_STILL_BE_IN_FLIGHT")
        external_id, content = conversation.external_id, outbound.content
    verified_message = None
    if outcome == "delivered":
        require(type(message_id) is int and message_id > 0, "DELIVERED_MESSAGE_ID_REQUIRED", 422)
        try:
            remote = adapter.get_conversation(external_id)
            require(remote.get("id") == external_id and remote.get("inbox_id") == settings.chatwoot_inbox_id
                and remote.get("account_id") == settings.chatwoot_account_id, "REMOTE_CONVERSATION_SCOPE_MISMATCH")
            messages = adapter.get_messages(external_id).get("payload")
            require(isinstance(messages, list), "REMOTE_MESSAGE_HISTORY_UNCONFIRMED", 503)
        except DomainError:
            raise
        except Exception as error:
            raise DomainError(503, "REMOTE_DELIVERY_CANNOT_BE_VERIFIED") from error
        target = next((row for row in messages if row.get("id") == message_id), None)
        sender = (target or {}).get("sender") or {}
        sender_type = str(sender.get("type") or sender.get("user_type") or "").replace("_", "").lower()
        require(target is not None and target.get("content") == content and target.get("private") is False
                and target.get("message_type") in {1, "outgoing"} and sender_type == "agentbot"
                and settings.chatwoot_bot_id is not None and sender.get("id") == settings.chatwoot_bot_id,
                "REMOTE_MESSAGE_ID_TYPE_OR_CONTENT_MISMATCH")
        verified_message = {"id": message_id, "sender_type": "AgentBot", "sender_id": sender["id"],
                            "account_id": settings.chatwoot_account_id, "inbox_id": settings.chatwoot_inbox_id,
                            "verified_at": timestamp(utcnow())}
    else:
        require(message_id is None, "NOT_DELIVERED_MUST_NOT_CLAIM_MESSAGE_ID", 422)

    def transaction(session):
        def reconcile():
            outbound = session.get(OutboundMessage, outbound_id, with_for_update=True)
            conversation = session.get(Conversation, outbound.conversation_id, with_for_update=True)
            _scope(settings, conversation)
            require(conversation.control_revision == expected_control_revision, "CONTROL_REVISION_CONFLICT")
            require(conversation.control_mode in {"HUMAN_ACTIVE", "HANDOFF_PENDING"}, "PAUSE_BEFORE_RECONCILIATION")
            require(outbound.status in {"UNKNOWN", "SENDING"}, "OUTBOUND_RECONCILIATION_STATE_CONFLICT")
            require(outbound.content == content, "OUTBOUND_CONTENT_CHANGED")
            audit = OutboundReconciliation(outbound_id=outbound.id, actor_id=actor_id, outcome=outcome,
                control_revision=conversation.control_revision, version=1, message_id=message_id,
                content_hash=hashlib.sha256(content.encode()).hexdigest(), evidence={**evidence,
                    "verification_method": "actual_chatwoot_message_read" if outcome == "delivered" else "explicit_staff_non_delivery_attestation",
                    "remote_message": verified_message})
            session.add(audit)
            outbound.status = "SENT" if outcome == "delivered" else "NOT_DELIVERED"
            outbound.chatwoot_message_id = message_id
            outbound.last_error = None
            consumed_ids = []
            for job in session.scalars(select(Job).where(Job.conversation_id == conversation.id,
                    Job.kind == "fixed_reply", Job.status == "UNKNOWN")):
                if job.payload.get("input_revision", 0) <= outbound.input_revision:
                    job.status = "ACKNOWLEDGED" if outcome == "delivered" else "NOT_DELIVERED"
                    if outcome == "delivered" and type(job.payload.get("message_id")) is int:
                        consumed_ids.append(job.payload["message_id"])
            if consumed_ids:
                conversation.last_processed_message_id = max([conversation.last_processed_message_id or 0, *consumed_ids])
            session.flush()
            # Ownership is deliberately unchanged; explicit employee return is still required.
            return outbound_dict(session, outbound, conversation)
        return idempotent(session, actor_id, scope, key, request, reconcile)
    return run_transaction(factory, transaction)


@router.get("")
def list_outbound(request: Request, conversation_id: int | None = None, status: str | None = None, user=Depends(_staff)):
    settings = request.app.state.settings
    with request.app.state.session_factory() as session:
        query = select(OutboundMessage, Conversation).join(Conversation).where(
            Conversation.instance_id == settings.chatwoot_instance_id, Conversation.account_id == settings.chatwoot_account_id,
            Conversation.inbox_id == settings.chatwoot_inbox_id).order_by(OutboundMessage.created_at.desc())
        if conversation_id is not None:
            query = query.where(Conversation.external_id == conversation_id)
        if status:
            query = query.where(OutboundMessage.status == status)
        return [outbound_dict(session, row, conversation) for row, conversation in session.execute(query)]


@router.post("/{outbound_id}/reconcile")
def reconcile_route(outbound_id: str, data: ReconcileInput, request: Request, user=Depends(staff_user), idempotency_key: str = Header(default="")):
    try:
        return reconcile_outbound(request.app.state.settings, request.app.state.session_factory, request.app.state.adapter,
            outbound_id, data.expected_control_revision, data.outcome, data.evidence, user.user_id, idempotency_key, data.message_id)
    except DomainError as error:
        raise HTTPException(error.status, {"code": error.code, "data": error.data}) from error
