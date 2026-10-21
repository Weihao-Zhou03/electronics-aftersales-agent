"""Demo-only persistent simulated refunds and shipments, with controlled transport faults."""
import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from . import service
from .models import Callback, Fault, Operation, utcnow


@dataclass(frozen=True)
class ProviderSettings:
    database_url: str
    access_token: str
    admin_token: str
    signing_secret: str
    environment_id: str = "demo-local-1"
    app_mode: str = "demo"
    callback_url: str = ""

    @classmethod
    def from_env(cls):
        return cls(database_url=os.getenv("PROVIDER_DATABASE_URL", ""),
                   access_token=os.getenv("DEMO_PROVIDER_ACCESS_TOKEN", ""),
                   admin_token=os.getenv("DEMO_PROVIDER_ADMIN_TOKEN", ""),
                   signing_secret=os.getenv("DEMO_PROVIDER_SIGNING_SECRET", ""),
                   environment_id=os.getenv("ENVIRONMENT_ID", "demo-local-1"),
                   app_mode=os.getenv("APP_MODE", "demo"),
                   callback_url=os.getenv("DEMO_PROVIDER_CALLBACK_URL", ""))

    def validate(self):
        if self.app_mode != "demo":
            raise ValueError("DemoProvider must never run in pilot mode")
        if not self.database_url.startswith("postgresql"):
            raise ValueError("DemoProvider runtime requires its own PostgreSQL database")
        values = (self.access_token, self.admin_token, self.signing_secret)
        if any(len(value) < 32 for value in values) or len(set(values)) != 3:
            raise ValueError("provider service/admin/signing credentials must be long and distinct")
        if not self.environment_id:
            raise ValueError("ENVIRONMENT_ID is required")
        if self.callback_url and not self.callback_url.startswith(("http://", "https://")):
            raise ValueError("invalid provider callback URL")


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=200)
    snapshot: dict


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str


class SettleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool = True


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    copies: int = Field(default=2, ge=1, le=10)
    include_stale_processing: bool = True


def _dropped_response():
    # Headers arrive but the chunked response terminates without its final chunk.
    # This is an actual network read failure under uvicorn, not a forged timeout receipt.
    async def broken_stream():
        yield b'{"simulated":true,"injected_transport_loss":'
        raise ConnectionResetError("demo fault: intentional incomplete transport response")
    return StreamingResponse(broken_stream(), media_type="application/json")


async def deliver_once(factory, settings: ProviderSettings) -> bool:
    if not settings.callback_url:
        return False
    with factory.begin() as session:
        item = session.scalar(select(Callback).where(
            Callback.status.in_(["PENDING", "RETRY"]), Callback.available_at <= utcnow(),
            (Callback.lease_until.is_(None)) | (Callback.lease_until < utcnow())
        ).order_by(Callback.available_at, Callback.id).with_for_update(skip_locked=True).limit(1))
        if item is None:
            return False
        item.lease_until = utcnow() + timedelta(seconds=30)
        item.attempts += 1
        callback_id, payload, attempts = item.id, item.payload, item.attempts
    # No SQL transaction is held across HTTP.
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = "sha256=" + hmac.new(settings.signing_secret.encode(), timestamp.encode() + b"." + raw,
                                      hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-Demo-Signature": signature,
               "X-Demo-Timestamp": timestamp, "X-Demo-Delivery": payload["delivery_id"]}
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.post(settings.callback_url, content=raw, headers=headers)
        ok, error = 200 <= response.status_code < 300, f"HTTP_{response.status_code}"
    except httpx.HTTPError as exc:
        ok, error = False, type(exc).__name__
    with factory.begin() as session:
        item = session.get(Callback, callback_id)
        if item is not None:
            item.status = "DELIVERED" if ok else "FAILED" if attempts >= 8 else "RETRY"
            item.last_error = None if ok else error
            item.lease_until = None
            item.available_at = utcnow() + timedelta(seconds=min(60, 2**attempts))
    return True


def create_app(settings: ProviderSettings | None = None, factory=None) -> FastAPI:
    settings = settings or ProviderSettings.from_env()
    if factory is None:
        settings.validate()
        factory = sessionmaker(bind=create_engine(settings.database_url, pool_pre_ping=True),
                               expire_on_commit=False)

    async def pump():
        while True:
            try:
                did_work = await deliver_once(factory, settings)
            except Exception:
                # Keep pending rows durable; avoid writing callback payload/credentials to logs.
                did_work = False
            await asyncio.sleep(0.05 if did_work else 0.5)

    @asynccontextmanager
    async def lifespan(_app):
        service.initialize(factory, settings.environment_id)
        task = asyncio.create_task(pump())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="DemoProvider · 模拟退款与物流", lifespan=lifespan)
    app.state.provider_factory = factory
    app.state.provider_settings = settings

    def access(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, f"Bearer {settings.access_token}"):
            raise HTTPException(401, "provider service authentication required")

    def admin(authorization: str = Header(default="")):
        if settings.app_mode != "demo" or not secrets.compare_digest(authorization, f"Bearer {settings.admin_token}"):
            raise HTTPException(403, "demo test administrator authentication required")

    @app.get("/health")
    def health():
        with factory() as session:
            session.execute(select(Operation.id).limit(1))
        return {"status": "ok", "simulated": True, "environment_id": settings.environment_id}

    def submit(kind: str, data: SubmitRequest):
        try:
            result, mode = service.submit(factory, kind, data.snapshot, data.idempotency_key)
        except service.RequestConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return _dropped_response() if mode in {"disconnect-before", "apply-then-drop"} else result

    @app.post("/refunds", dependencies=[Depends(access)])
    def refunds(data: SubmitRequest):
        return submit("refund", data)

    @app.post("/shipments", dependencies=[Depends(access)])
    def shipments(data: SubmitRequest):
        return submit("shipment", data)

    @app.get("/operations/by-key/{key}", dependencies=[Depends(access)])
    def operation(key: str):
        result = service.query(factory, key)
        if result is None:
            raise HTTPException(404, "operation not found")
        return result

    @app.get("/shipments/{shipment_id}", dependencies=[Depends(access)])
    def shipment(shipment_id: str):
        with factory() as session:
            item = session.get(Operation, shipment_id)
            if item is None or item.kind != "shipment":
                raise HTTPException(404, "shipment not found")
            return service.receipt(item)

    @app.put("/admin/faults/{key}", dependencies=[Depends(admin)])
    def fault(key: str, data: FaultRequest):
        if data.mode not in service.FAULT_MODES or not 1 <= len(key) <= 200:
            raise HTTPException(422, "invalid fault mode/key")
        with factory.begin() as session:
            session.merge(Fault(idempotency_key=key, mode=data.mode))
        return {"idempotency_key": key, "mode": data.mode, "simulated": True}

    @app.post("/admin/operations/{key}/settle", dependencies=[Depends(admin)])
    def settle(key: str, data: SettleRequest):
        result = service.settle(factory, key, data.success)
        if result is None:
            raise HTTPException(404, "operation not found")
        return result

    @app.post("/admin/operations/{key}/callbacks", dependencies=[Depends(admin)])
    def callbacks(key: str, data: ReplayRequest):
        with factory.begin() as session:
            item = session.scalar(select(Operation).where(Operation.idempotency_key == key).with_for_update())
            if item is None:
                raise HTTPException(404, "operation not found")
            delivery_id = str(uuid4())
            first = service.enqueue_callback(session, item, delivery_id=delivery_id)
            for _ in range(data.copies - 1):
                session.add(Callback(operation_id=item.id, payload=dict(first.payload)))
            if data.include_stale_processing:
                service.enqueue_callback(session, item, status="RUNNING")
        return {"queued": data.copies + int(data.include_stale_processing), "simulated": True}

    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8100")))


if __name__ == "__main__":
    main()
