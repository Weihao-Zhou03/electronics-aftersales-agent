"""M0 login and session boundary for the integration verification page."""

import hashlib
import hmac
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, String, select, update
from sqlalchemy.orm import Mapped, mapped_column

from .db import create_session_factory, utcnow
from .models import Base, ChatBinding, Conversation, Job, OutboundMessage, new_id

router = APIRouter(prefix="/api/v1/auth")
password_hasher = PasswordHasher()
COOKIE_NAME = "aftersales_session"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(128), unique=True)
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(settings, token: str) -> str:
    if len(settings.session_secret) < 32:
        raise HTTPException(503, "SESSION_SECRET requires at least 32 characters")
    return hmac.new(settings.session_secret.encode(), f"csrf:{token}".encode(), "sha256").hexdigest()


def check_origin(request: Request):
    allowed = {x.strip().rstrip("/") for x in request.app.state.settings.allowed_origins.split(",")}
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin or origin not in allowed:
        raise HTTPException(403, "Origin is required and must be allowed")


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    session_id: str
    role: str
    username: str
    expires_at: datetime


def current_user(request: Request) -> AuthContext:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        raise HTTPException(401, "Login required")
    with request.app.state.session_factory() as db:
        row = db.execute(select(AuthSession, User).join(User).where(
            AuthSession.token_hash == token_hash(token), AuthSession.revoked_at.is_(None),
            User.active.is_(True),
        )).first()
        if not row or aware(row[0].expires_at) <= utcnow():
            raise HTTPException(401, "Session expired")
        session, user = row
        return AuthContext(user.id, session.id, user.role, user.username, aware(session.expires_at))


def write_user(request: Request, user: AuthContext = Depends(current_user)) -> AuthContext:
    check_origin(request)
    expected = csrf_token(request.app.state.settings, request.cookies.get(COOKIE_NAME, ""))
    if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), expected):
        raise HTTPException(403, "Invalid CSRF token")
    return user


def staff_user(user: AuthContext = Depends(write_user)) -> AuthContext:
    if user.role not in {"support", "admin"}:
        raise HTTPException(403, "Staff role required")
    return user


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(data: LoginInput, request: Request, response: Response):
    check_origin(request)
    from .limits import limit_request
    subject = (request.client.host if request.client else "unknown") + ":" + data.username
    limit_request(request.app.state.session_factory, "login", subject, limit=20)
    settings = request.app.state.settings
    csrf_token(settings, "configuration-check")
    with request.app.state.session_factory.begin() as db:
        user = db.scalar(select(User).where(User.username == data.username, User.active.is_(True)))
        try:
            valid = bool(user) and password_hasher.verify(user.password_hash, data.password)
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid:
            raise HTTPException(401, "Invalid credentials")
        # A login replacing an existing account revokes its authorization even if JS reset failed.
        previous = request.cookies.get(COOKIE_NAME)
        if previous:
            old = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(previous)).with_for_update())
            if old:
                revoke_session(db, old)
        token = secrets.token_urlsafe(32)
        expires = utcnow() + timedelta(seconds=settings.session_ttl_seconds)
        db.add(AuthSession(user_id=user.id, token_hash=token_hash(token), expires_at=expires))
        result = {"username": user.username, "role": user.role, "csrf_token": csrf_token(settings, token)}
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=settings.session_cookie_secure,
                        samesite="lax", max_age=settings.session_ttl_seconds, path="/")
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/me")
def me(request: Request, user: AuthContext = Depends(current_user)):
    return {"username": user.username, "role": user.role,
            "csrf_token": csrf_token(request.app.state.settings, request.cookies[COOKIE_NAME])}


def revoke_session(db, session):
    session.revoked_at = utcnow()
    bindings = db.scalars(select(ChatBinding).where(ChatBinding.auth_session_id == session.id)).all()
    for binding in bindings:
        binding.revoked_at = utcnow()
        conversations = db.scalars(select(Conversation).where(
            Conversation.instance_id == binding.instance_id, Conversation.account_id == binding.account_id,
            Conversation.inbox_id == binding.inbox_id, Conversation.contact_id == binding.contact_id,
        ).with_for_update()).all()
        for conv in conversations:
            conv.control_revision += 1
            conv.customer_id = None
            db.execute(update(Job).where(Job.conversation_id == conv.id,
                                         Job.status.in_(["PENDING", "RUNNING"])).values(status="CANCELLED"))
            db.execute(update(OutboundMessage).where(OutboundMessage.conversation_id == conv.id,
                        OutboundMessage.status == "PREPARED").values(status="CANCELLED"))


@router.post("/logout")
def logout(request: Request, response: Response, user: AuthContext = Depends(write_user)):
    with request.app.state.session_factory.begin() as db:
        session = db.scalar(select(AuthSession).where(AuthSession.id == user.session_id).with_for_update())
        if session:
            revoke_session(db, session)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True, "reset_widget": True}


def seed_demo(factory, password: str):
    if len(password) < 12:
        raise ValueError("M0_DEMO_PASSWORD must contain at least 12 characters")
    with factory.begin() as db:
        for username, role in [("customer-a", "customer"), ("customer-b", "customer"),
                               ("support", "support"), ("admin", "admin")]:
            if db.scalar(select(User.id).where(User.username == username)) is None:
                db.add(User(username=username, role=role, password_hash=password_hasher.hash(password)))


if __name__ == "__main__":
    from .config import Settings
    settings = Settings.from_env()
    if sys.argv[1:] != ["seed"] or settings.app_mode != "demo":
        raise SystemExit("usage: python -m aftersales.auth seed (APP_MODE=demo)")
    seed_demo(create_session_factory(settings.database_url), os.environ.get("M0_DEMO_PASSWORD", ""))
    print("M0 demo users ready; existing passwords preserved.")
