"""Signed Chatwoot ingress. Event + task are committed before acknowledging delivery."""
import hashlib
import hmac
import json
import time

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from .control import human_held, suspend_local
from .models import Conversation, Job, WebhookEvent

router = APIRouter()


def verify_signature(settings, channel, raw_body, headers, now=None):
    try:
        secret = settings.webhook_secret(channel)
    except ValueError as error:
        raise HTTPException(503, str(error)) from error
    signature = headers.get("x-chatwoot-signature")
    timestamp = headers.get("x-chatwoot-timestamp")
    if not signature or not timestamp:
        raise HTTPException(401, "signature and timestamp required")
    try:
        if not timestamp.isascii() or not timestamp.isdecimal():
            raise ValueError()
        unix_seconds = int(timestamp)
        if abs((time.time() if now is None else now) - unix_seconds) > settings.webhook_tolerance_seconds:
            raise ValueError()
    except (ValueError, OverflowError):
        raise HTTPException(401, "invalid or expired timestamp") from None
    expected = "sha256=" + hmac.new(secret.encode(), timestamp.encode("utf-8") + b"." + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "invalid signature")


def _positive_int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HTTPException(422, "positive integer Chatwoot IDs required")
    return value


def normalize_event(payload, channel):
    event_type = payload.get("event")
    if not isinstance(event_type, str):
        raise HTTPException(422, "event is required")
    conversation = payload.get("conversation") or (payload if event_type.startswith("conversation_") else {})
    if not isinstance(conversation, dict):
        raise HTTPException(422, "conversation object required")
    account_id = (payload.get("account") or {}).get("id") or payload.get("account_id") or conversation.get("account_id")
    inbox_id = (payload.get("inbox") or {}).get("id") or payload.get("inbox_id") or conversation.get("inbox_id")
    if inbox_id is None:
        inbox_id = (conversation.get("inbox") or {}).get("id")
    conversation_id = _positive_int(conversation.get("id"))
    sender = payload.get("sender") or {}
    message_type = payload.get("message_type")
    # Rails enum values are accepted only in this documented normalization boundary.
    message_type = {0: "incoming", 1: "outgoing", 2: "activity", 3: "template"}.get(message_type, message_type)
    sender_type = sender.get("type") or sender.get("user_type")
    sender_type = str(sender_type).replace("_", "").lower() if sender_type else None
    # v4.19 Contact#webhook_data uses contact_type, while User/AgentBot use type.
    if sender_type is None and sender.get("contact_type") in {"visitor", "lead", "customer"}:
        sender_type = "contact"
    contact_id = (conversation.get("meta") or {}).get("sender", {}).get("id")
    contact_id = contact_id or (conversation.get("contact_inbox") or {}).get("contact_id")
    if sender_type == "contact":
        contact_id = sender.get("id") or contact_id
    if contact_id is not None:
        contact_id = _positive_int(contact_id)
    message_id = _positive_int(payload.get("id")) if event_type.startswith("message_") else None
    incoming = (channel == "agentbot" and event_type == "message_created" and message_type == "incoming"
                and payload.get("private") is False and sender_type == "contact")
    human_message = (event_type == "message_created" and message_type == "outgoing"
                     and payload.get("private") is False and sender_type in {"user", "agent"})
    return {"event_type": event_type, "account_id": _positive_int(account_id), "inbox_id": _positive_int(inbox_id),
            "conversation_id": conversation_id, "contact_id": contact_id, "message_id": message_id,
            "message_type": message_type, "private": payload.get("private"), "sender_type": sender_type,
            "incoming": incoming, "human_message": human_message, "status": conversation.get("status"),
            "human_held": human_held(conversation)}


def ingest_event(settings, session_factory, channel, raw_body, headers):
    headers = {key.lower(): value for key, value in headers.items()}
    verify_signature(settings, channel, raw_body, headers)
    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError()
        normalized = normalize_event(payload, channel)
    except (ValueError, TypeError, AttributeError, KeyError):
        raise HTTPException(422, "invalid JSON event shape") from None
    if normalized["account_id"] != settings.chatwoot_account_id or normalized["inbox_id"] != settings.chatwoot_inbox_id:
        raise HTTPException(403, "account/inbox outside allowlist")
    if channel == "control" and normalized["event_type"] not in {"conversation_status_changed", "conversation_updated"}:
        raise HTTPException(422, "control channel only accepts subscribed conversation events")
    scope = f"{settings.chatwoot_instance_id}:{normalized['account_id']}"
    delivery = headers.get("x-chatwoot-delivery")
    if normalized["message_id"] is not None:
        event_key = f"{scope}:{normalized['event_type']}:{normalized['message_id']}"
    else:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        event_key = f"{scope}:{channel}:{normalized['event_type']}:{delivery or hashlib.sha256(canonical).hexdigest()}"
    if len(event_key) > 512 or (delivery and len(delivery) > 255):
        raise HTTPException(422, "delivery identifier too long")
    for attempt in range(2):
        try:
            with session_factory.begin() as session:
                if session.scalar(select(WebhookEvent.id).where(WebhookEvent.event_key == event_key)):
                    return {"accepted": True, "duplicate": True, "queued": False}
                event = WebhookEvent(event_key=event_key, channel=channel, event_type=normalized["event_type"],
                                     delivery_id=delivery, payload_hash=hashlib.sha256(raw_body).hexdigest(), payload=normalized)
                session.add(event)
                session.flush()
                conversation = session.scalar(select(Conversation).where(
                    Conversation.instance_id == settings.chatwoot_instance_id,
                    Conversation.account_id == settings.chatwoot_account_id,
                    Conversation.external_id == normalized["conversation_id"]).with_for_update())
                if conversation is None:
                    conversation = Conversation(instance_id=settings.chatwoot_instance_id,
                                                account_id=settings.chatwoot_account_id, inbox_id=settings.chatwoot_inbox_id,
                                                external_id=normalized["conversation_id"], contact_id=normalized["contact_id"],
                                                control_mode="BOT_ACTIVE", control_revision=0, input_revision=0, fencing_revision=0)
                    session.add(conversation)
                    session.flush()
                conversation.last_remote_status = normalized["status"]
                stop = normalized["human_message"] or normalized["human_held"] or normalized["status"] in {"open", "resolved", "snoozed"}
                if conversation.contact_id and normalized["contact_id"] and conversation.contact_id != normalized["contact_id"]:
                    stop = True
                elif conversation.contact_id is None:
                    conversation.contact_id = normalized["contact_id"]
                if stop:
                    suspend_local(session, conversation, "chatwoot_webhook", "remote_human_or_inactive")
                # A late pending event is recorded but cannot revive paused work or ownership.
                queued = bool(normalized["incoming"] and settings.agent_enabled and conversation.control_mode == "BOT_ACTIVE")
                if queued:
                    conversation.input_revision += 1
                    session.add(Job(conversation_id=conversation.id, event_id=event.id, dedupe_key=event_key,
                                    payload={"message_id": normalized["message_id"], "input_revision": conversation.input_revision}))
                event.status = "QUEUED" if queued else "IGNORED"
            return {"accepted": True, "duplicate": False, "queued": queued}
        except IntegrityError:
            with session_factory() as session:
                if session.scalar(select(WebhookEvent.id).where(WebhookEvent.event_key == event_key)):
                    return {"accepted": True, "duplicate": True, "queued": False}
            if attempt:
                raise
    raise RuntimeError("unreachable")


@router.post("/integrations/chatwoot/agentbot")
async def agentbot_webhook(request: Request):
    raw = await request.body()
    if len(raw) > 1024 * 1024:
        raise HTTPException(413, "webhook payload exceeds 1 MiB")
    return await run_in_threadpool(ingest_event, request.app.state.settings, request.app.state.session_factory,
                                  "agentbot", raw, request.headers)


@router.post("/integrations/chatwoot/control")
async def control_webhook(request: Request):
    raw = await request.body()
    if len(raw) > 1024 * 1024:
        raise HTTPException(413, "webhook payload exceeds 1 MiB")
    return await run_in_threadpool(ingest_event, request.app.state.settings, request.app.state.session_factory,
                                  "control", raw, request.headers)
