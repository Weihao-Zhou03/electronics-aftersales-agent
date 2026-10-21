"""Only API-verified contact_inbox HMAC may establish a customer binding."""

import hashlib
import hmac
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .auth import AuthContext, AuthSession, aware, current_user, write_user
from .db import utcnow
from .models import ChatBinding

router = APIRouter(prefix="/api/v1/chat")


def identifier_for(user_id: str):
    return f"aftersales:{user_id}"


@router.get("/identity")
def identity(request: Request, user: AuthContext = Depends(current_user)):
    if request.query_params:
        raise HTTPException(422, "Identity parameters are not accepted")
    if user.role != "customer":
        raise HTTPException(403, "Customer role required")
    settings = request.app.state.settings
    secret = settings.chatwoot_widget_identity_secret
    if not secret or not settings.chatwoot_widget_token:
        raise HTTPException(503, "Widget identity not configured")
    identifier = identifier_for(user.user_id)
    return {"identifier": identifier,
            "identifier_hash": hmac.new(secret.encode(), identifier.encode(), hashlib.sha256).hexdigest(),
            "website_token": settings.chatwoot_widget_token, "base_url": settings.chatwoot_public_url}


class BindInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: int = Field(gt=0)


@router.post("/bind")
def bind_contact(data: BindInput, request: Request, user: AuthContext = Depends(write_user)):
    if user.role != "customer":
        raise HTTPException(403, "Customer role required")
    settings = request.app.state.settings
    try:
        conv = request.app.state.adapter.get_conversation(data.conversation_id)
    except Exception as exc:
        raise HTTPException(503, "Cannot verify Chatwoot identity") from exc
    meta = conv.get("meta") or {}
    sender = meta.get("sender") or {}
    if (conv.get("account_id") != settings.chatwoot_account_id
            or conv.get("inbox_id") != settings.chatwoot_inbox_id
            or meta.get("hmac_verified") is not True
            or sender.get("identifier") != identifier_for(user.user_id)
            or not isinstance(sender.get("id"), int) or isinstance(sender.get("id"), bool)):
        raise HTTPException(403, "Verified contact identity does not match the current login")
    with request.app.state.session_factory.begin() as db:
        # Login could have been revoked while the network GET was in flight.
        session = db.scalar(select(AuthSession).where(AuthSession.id == user.session_id).with_for_update())
        if not session or session.revoked_at or aware(session.expires_at) <= utcnow():
            raise HTTPException(401, "Session revoked during verification")
        row = db.scalar(select(ChatBinding).where(
            ChatBinding.instance_id == settings.chatwoot_instance_id,
            ChatBinding.account_id == settings.chatwoot_account_id,
            ChatBinding.inbox_id == settings.chatwoot_inbox_id,
            ChatBinding.contact_id == sender["id"],
        ).with_for_update())
        if row and row.customer_id != user.user_id:
            raise HTTPException(409, "Contact already belongs to another customer")
        if row is None:
            row = ChatBinding(instance_id=settings.chatwoot_instance_id,
                account_id=settings.chatwoot_account_id, inbox_id=settings.chatwoot_inbox_id,
                contact_id=sender["id"], customer_id=user.user_id)
            db.add(row)
        row.auth_session_id = user.session_id
        row.source = "chatwoot-v4.19.0-api:meta.hmac_verified+sender.identifier"
        row.verified_at = utcnow()
        row.expires_at = min(aware(session.expires_at), utcnow() + timedelta(seconds=settings.chatwoot_binding_ttl_seconds))
        row.revoked_at = None
    return {"ok": True, "conversation_id": data.conversation_id, "contact_id": sender["id"]}
