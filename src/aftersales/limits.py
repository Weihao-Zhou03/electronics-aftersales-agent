"""Atomic PostgreSQL/SQLite counters; no clear-text IPs or identifiers persisted."""

import hashlib
import time
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import RateBucket


def consume(session, scope, subject, *, limit, seconds=60, now=None):
    now = time.time() if now is None else now
    window = int(now) // seconds
    key = hashlib.sha256(f"{scope}:{subject}:{window}".encode()).hexdigest()
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    statement = insert(RateBucket).values(id=key, count=1,
        expires_at=datetime.fromtimestamp((window + 2) * seconds, UTC)).on_conflict_do_update(
            index_elements=[RateBucket.id], set_={"count": RateBucket.count + 1},
            where=RateBucket.count < limit).returning(RateBucket.count)
    if session.scalar(statement) is None:
        raise HTTPException(429, "Rate limit exceeded; retry in the next minute")


def limit_request(factory, scope, subject, *, limit, seconds=60):
    with factory.begin() as session:
        consume(session, scope, subject, limit=limit, seconds=seconds)
