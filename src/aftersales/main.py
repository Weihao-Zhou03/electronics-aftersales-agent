"""Authenticated portal API, restricted integrations and same-origin React app."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from . import attachments, auth, business_api, identity, outbound_api, system_api, webhooks
from .chatwoot import ChatwootAdapter
from .config import Settings
from .control import ControlConflict, handoff_conversation, return_conversation_to_bot
from .db import create_session_factory
from .models import Conversation


class ControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


class _UnconfiguredAdapter:
    def __getattr__(self, name):
        raise RuntimeError("Chatwoot runtime credentials must be configured")


def create_app(settings=None, session_factory=None, adapter=None):
    settings = settings or Settings.from_env()
    application = FastAPI(title="电子产品售后办理 Agent", version="0.1.0")
    application.state.settings = settings
    application.state.session_factory = session_factory or create_session_factory(settings.database_url)
    application.state.adapter = adapter or (ChatwootAdapter(
        settings.chatwoot_base_url, settings.chatwoot_account_id, settings.chatwoot_inbox_id,
        settings.chatwoot_bot_access_token, bot_id=settings.chatwoot_bot_id,
        read_access_token=getattr(settings, "chatwoot_read_access_token", "") or None,
    ) if settings.chatwoot_bot_access_token and settings.chatwoot_account_id > 0 and settings.chatwoot_inbox_id > 0 else _UnconfiguredAdapter())
    application.include_router(auth.router)
    application.include_router(identity.router)
    application.include_router(webhooks.router)
    application.include_router(business_api.router)
    application.include_router(attachments.router)
    application.include_router(system_api.router)
    application.include_router(outbound_api.router)

    @asynccontextmanager
    async def lifespan(app):
        from .domain import ensure_environment
        with app.state.session_factory.begin() as session:
            ensure_environment(session, settings.app_mode, settings.environment_id)
            if settings.app_mode == "pilot" and session.scalar(select(auth.User.id).where(auth.User.is_demo.is_(True))):
                raise RuntimeError("pilot database cannot contain demo users")
        yield
    application.router.lifespan_context = lifespan

    @application.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @application.exception_handler(SQLAlchemyError)
    async def database_failure(request, exc):
        return JSONResponse({"detail": "Database unavailable; no acknowledgement committed"}, status_code=503)

    from .domain import DomainError

    @application.exception_handler(DomainError)
    async def domain_failure(request, exc):
        return JSONResponse({"detail": exc.code, "data": exc.data}, status_code=exc.status)

    @application.get("/health")
    @application.get("/api/v1/health/live")
    def live():
        return {"status": "live", "app_mode": settings.app_mode,
                "agent_reply_mode": settings.agent_reply_mode, "model": settings.model_name or "not configured"}

    @application.get("/api/v1/app-config")
    def app_config():
        return {"app_mode": settings.app_mode, "fulfillment_mode": settings.fulfillment_mode}

    @application.get("/api/v1/health/ready")
    def ready():
        missing = [name.upper() for name in (
            "chatwoot_bot_access_token", "chatwoot_bot_webhook_secret", "chatwoot_account_webhook_secret",
            "chatwoot_widget_identity_secret", "chatwoot_widget_token", "session_secret",
        ) if not getattr(settings, name)]
        with application.state.session_factory() as db:
            db.execute(text("SELECT 1"))
        if settings.chatwoot_account_id < 1 or settings.chatwoot_inbox_id < 1:
            missing.append("CHATWOOT_ACCOUNT_ID/CHATWOOT_INBOX_ID")
        return JSONResponse({"status": "ready" if not missing else "configuration_required",
                             "missing": missing, "live_chatwoot_contract": "see compatibility evidence"},
                            status_code=503 if missing else 200)

    @application.get("/m0")
    def m0_page():
        if settings.app_mode != "demo":
            raise HTTPException(404, "Not available")
        return FileResponse(Path(__file__).parent / "static" / "m0.html")

    @application.get("/m0.js")
    def m0_script():
        return FileResponse(Path(__file__).parent / "static" / "m0.js", media_type="text/javascript")

    @application.post("/integrations/demo-provider")
    async def provider_callback(request: Request):
        from .operations_worker import ingest_callback
        if settings.app_mode != "demo":
            raise HTTPException(404, "Not available")
        body = await request.body()
        if len(body) > 1024 * 1024:
            raise HTTPException(413, "Callback too large")
        try:
            return ingest_callback(application.state.session_factory, body,
                request.headers.get("X-Demo-Signature"), request.headers.get("X-Demo-Timestamp"),
                os.environ.get("DEMO_PROVIDER_SIGNING_SECRET", ""), environment_id=settings.environment_id)
        except ValueError as exc:
            raise HTTPException(401, "Invalid provider callback") from exc

    @application.get("/api/v1/conversations/{conversation_id}")
    def customer_conversation(conversation_id: int, user=Depends(auth.current_user)):
        if user.role != "customer":
            raise HTTPException(403, "Customer role required")
        from .business_models import AfterSalesCase, Proposal
        with application.state.session_factory() as db:
            row = db.scalar(select(Conversation).where(
                Conversation.instance_id == settings.chatwoot_instance_id,
                Conversation.account_id == settings.chatwoot_account_id,
                Conversation.inbox_id == settings.chatwoot_inbox_id,
                Conversation.external_id == conversation_id))
            cases = db.scalars(select(AfterSalesCase).join(Proposal,
                Proposal.confirmed_case_id == AfterSalesCase.id).where(
                AfterSalesCase.customer_id == user.user_id,
                Proposal.agent_conversation_id == row.id)).all() if row else []
            if not row or not cases:
                raise HTTPException(404, "Conversation unavailable")
            contact_id = row.contact_id
        remote = application.state.adapter.get_conversation(conversation_id)
        meta = remote.get("meta") or {}
        sender = meta.get("sender") or {}
        if (meta.get("hmac_verified") is not True or sender.get("id") != contact_id
                or sender.get("identifier") != "aftersales:" + user.user_id):
            raise HTTPException(404, "Conversation unavailable")
        history = application.state.adapter.get_messages(conversation_id)
        from .worker import _message_kind
        messages = [{"id": message.get("id"), "content": message.get("content") or "",
                     "created_at": message.get("created_at"), "message_type": _message_kind(message)}
                    for message in history.get("payload", [])
                    if message.get("private") is False and _message_kind(message) in {"incoming", "outgoing"}]
        return {"id": conversation_id, "history_scope": "latest_page", "messages": messages,
                "linked_cases": [{"id": case.id, "number": case.number, "type": case.type, "status": case.status}
                                 for case in cases]}

    @application.get("/api/v1/staff/conversations/{conversation_id}")
    def conversation_state(conversation_id: int, request: Request,
                           user=Depends(auth.current_user)):
        if user.role not in {"support", "admin"}:
            raise HTTPException(403, "Staff role required")
        with application.state.session_factory() as db:
            row = db.scalar(select(Conversation).where(
                Conversation.instance_id == settings.chatwoot_instance_id,
                Conversation.account_id == settings.chatwoot_account_id,
                Conversation.inbox_id == settings.chatwoot_inbox_id,
                Conversation.external_id == conversation_id))
            if not row:
                raise HTTPException(404, "Conversation unavailable")
            from .business_models import AfterSalesCase, OrderItem, OrderUnit, Proposal
            related = db.execute(select(AfterSalesCase, OrderItem.order_id).join(
                Proposal, Proposal.confirmed_case_id == AfterSalesCase.id).join(
                OrderUnit, AfterSalesCase.order_unit_id == OrderUnit.id).join(
                OrderItem, OrderUnit.order_item_id == OrderItem.id).where(
                Proposal.agent_conversation_id == row.id)).all()
            return {"conversation_id": conversation_id, "control_mode": row.control_mode,
                    "control_revision": row.control_revision, "input_revision": row.input_revision,
                    "linked_cases": [{"id": case.id, "number": case.number, "type": case.type, "status": case.status, "order_id": order_id}
                                      for case, order_id in related],
                    "chatwoot_url": f"{settings.chatwoot_public_url}/app/accounts/{settings.chatwoot_account_id}/conversations/{conversation_id}"}

    def change_control(conversation_id, data, user, operation):
        try:
            result = operation(settings, application.state.session_factory, application.state.adapter,
                               conversation_id, user.user_id, expected_revision=data.expected_revision)
            return result if result is not None else {"ok": True}
        except ControlConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "Chatwoot sync failed; automatic actions remain paused") from exc

    @application.post("/api/v1/staff/conversations/{conversation_id}/takeover")
    def takeover(conversation_id: int, data: ControlInput, user=Depends(auth.staff_user)):
        return change_control(conversation_id, data, user, handoff_conversation)

    @application.post("/api/v1/staff/conversations/{conversation_id}/return-to-bot")
    def return_bot(conversation_id: int, data: ControlInput, user=Depends(auth.staff_user)):
        return change_control(conversation_id, data, user, return_conversation_to_bot)

    frontend = Path(os.environ.get("FRONTEND_DIST", str(Path(__file__).resolve().parents[2] / "frontend" / "dist")))
    if (frontend / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=frontend / "assets"), name="frontend-assets")

    @application.get("/{path:path}")
    def frontend_page(path: str):
        if path.startswith(("api/", "integrations/")):
            raise HTTPException(404, "Not found")
        if (frontend / "index.html").is_file():
            return FileResponse(frontend / "index.html")
        if path == "":
            if settings.app_mode != "demo":
                raise HTTPException(503, "Frontend build required")
            return FileResponse(Path(__file__).parent / "static" / "m0.html")
        raise HTTPException(404, "Frontend not built; run npm run build in frontend")

    return application


app = create_app()
