from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def utcnow():
    return datetime.now(UTC)


def as_utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def create_session_factory(url: str):
    options: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            from sqlalchemy.pool import StaticPool
            options["poolclass"] = StaticPool
    engine = create_engine(url, **options)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def sqlite_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(session_factory):
    """Create a schema for explicitly synthetic tests only. Runtime uses Alembic."""
    from .models import Base
    Base.metadata.create_all(session_factory.kw["bind"])
