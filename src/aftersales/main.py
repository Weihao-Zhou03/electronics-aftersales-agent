"""M0 API and a small integration verification page, not the M2 portal."""

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from . import auth, identity, webhooks
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
    application = FastAPI(title="电子产品售后办理 Agent · M0", version="0.0.1")
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

    @application.get("/health")
    @application.get("/api/v1/health/live")
    def live():
        return {"status": "live", "milestone": "M0", "model": "not used"}

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

    @application.get("/")
    def m0_page():
        return FileResponse(Path(__file__).parent / "static" / "m0.html")

    @application.get("/m0.js")
    def m0_script():
        return FileResponse(Path(__file__).parent / "static" / "m0.js", media_type="text/javascript")

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
            return {"conversation_id": conversation_id, "control_mode": row.control_mode,
                    "control_revision": row.control_revision, "input_revision": row.input_revision}

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

    return application


app = create_app()
