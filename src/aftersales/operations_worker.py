"""Lease-fenced fulfillment execution; historical obligations choose their own provider/mode."""
import hashlib
import os
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from .business_models import BusinessJob, Operation
from .db import as_utc, create_session_factory, utcnow
from .fulfillment import (
    DemoProvider,
    ProviderResult,
    ProviderUnavailable,
    select_provider,
    verify_callback,
)
from .models import WebhookEvent


def operation_snapshot(operation: Operation) -> dict:
    return {key: getattr(operation, key) for key in (
        "id", "case_id", "kind", "idempotency_key", "request_snapshot", "execution_mode",
        "provider_name", "provider_version", "environment_id", "status", "lease_version")}


def ingest_callback(factory, raw: bytes, signature: str | None, timestamp: str | None,
                    signing_secret: str, *, environment_id: str, now: int | None = None) -> dict:
    payload = verify_callback(raw, signature, timestamp, signing_secret, now=now)
    if payload.get("environment_id") != environment_id:
        raise ValueError("provider callback environment mismatch")
    event_key = "demo-provider:" + environment_id + ":" + payload["delivery_id"]
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with factory.begin() as session:
            event = session.scalar(select(WebhookEvent).where(WebhookEvent.event_key == event_key))
            if event:
                if event.payload_hash != digest:
                    # Duplicates use identical raw content. One delivery ID cannot rewrite its event.
                    raise ValueError("provider delivery ID reused with changed payload")
                return {"duplicate": True, "accepted": event.status == "ACCEPTED"}
            operation = session.scalar(select(Operation).where(
                Operation.idempotency_key == payload["idempotency_key"],
                Operation.environment_id == environment_id,
                Operation.provider_name == "demo-provider", Operation.execution_mode == "demo"))
            event = WebhookEvent(event_key=event_key, provider="demo-provider", channel="fulfillment",
                                 event_type=payload.get("event", "operation.updated"),
                                 delivery_id=payload["delivery_id"], payload_hash=digest, payload=payload,
                                 status="ACCEPTED" if operation else "IGNORED_UNKNOWN_OPERATION")
            session.add(event)
            if operation:
                session.add(BusinessJob(kind="reconcile_operation", dedupe_key="provider-callback:" + hashlib.sha256(event_key.encode()).hexdigest(),
                                        payload={"operation_id": operation.id, "provider_status": payload["status"]}, status="PENDING"))
            return {"duplicate": False, "accepted": operation is not None}
    except IntegrityError:
        with factory() as session:
            existing = session.scalar(select(WebhookEvent).where(WebhookEvent.event_key == event_key))
            if existing is None or existing.payload_hash != digest:
                raise ValueError("provider event persistence conflict") from None
            return {"duplicate": True, "accepted": existing.status == "ACCEPTED"}


class OperationsWorker:
    def __init__(self, factory, *, app_mode: str, environment_id: str, demo_url: str = "",
                 demo_access_token: str = "", max_attempts: int = 5, lease_seconds: int = 60,
                 provider_factory=None):
        if lease_seconds < 30 or max_attempts < 1:
            raise ValueError("fulfillment lease must exceed bounded HTTP timeout")
        self.factory, self.app_mode, self.environment_id = factory, app_mode, environment_id
        self.demo_url, self.demo_access_token = demo_url, demo_access_token
        self.max_attempts, self.lease_seconds = max_attempts, lease_seconds
        self.owner = str(uuid4())
        self.provider_factory = provider_factory

    def _claim(self):
        with self.factory.begin() as session:
            operation = session.scalar(select(Operation).where(
                Operation.execution_mode != "manual", Operation.status.in_(["PENDING", "RUNNING", "UNKNOWN"]),
                Operation.attempts < self.max_attempts, Operation.available_at <= utcnow(),
                or_(Operation.lease_until.is_(None), Operation.lease_until < utcnow())
            ).order_by(Operation.available_at, Operation.id).with_for_update(skip_locked=True).limit(1))
            if operation is None:
                return None
            snapshot = operation_snapshot(operation)
            operation.status = "RUNNING"
            operation.attempts += 1
            operation.lease_owner = self.owner
            operation.lease_version += 1
            operation.lease_until = utcnow() + timedelta(seconds=self.lease_seconds)
            snapshot.update(lease_version=operation.lease_version, attempts=operation.attempts)
            return snapshot

    def _provider(self, operation):
        if self.provider_factory:
            return self.provider_factory(operation)
        return select_provider(operation, app_mode=self.app_mode, environment_id=self.environment_id,
                               demo_url=self.demo_url, demo_access_token=self.demo_access_token)

    def _result(self, operation: dict, response: ProviderResult):
        from .domain import apply_operation_result
        with self.factory.begin() as session:
            row = session.scalar(select(Operation).where(Operation.id == operation["id"]).with_for_update())
            if row is None or row.lease_owner != self.owner or row.lease_version != operation["lease_version"]:
                return False
            if row.lease_until is None or as_utc(row.lease_until) < utcnow():
                return False
            status = response.status if response.status in {"SUCCEEDED", "FAILED"} else "UNKNOWN"
            result = dict(response.result)
            if response.error:
                result["transport_error"] = response.error
            if response.status == "NOT_FOUND":
                result["transport_error"] = "authoritative_key_not_found"
            if response.provider_operation_id:
                if row.provider_operation_id and row.provider_operation_id != response.provider_operation_id:
                    status, result = "UNKNOWN", {"transport_error": "provider_operation_id_changed"}
                else:
                    row.provider_operation_id = response.provider_operation_id
            apply_operation_result(session, operation["id"], status, result,
                                   actor_id="fulfillment-worker", expected_lease_owner=self.owner,
                                   expected_lease_version=operation["lease_version"])
            row.lease_owner = None
            row.lease_until = None
            row.available_at = utcnow() + timedelta(seconds=min(60, 2 ** row.attempts))
            for job in session.scalars(select(BusinessJob).where(BusinessJob.status.in_(["PENDING", "RUNNING"]))):
                if job.kind in {"execute_operation", "reconcile_operation"} and job.payload.get("operation_id") == row.id:
                    if status in {"SUCCEEDED", "FAILED"}:
                        job.status = "SUCCEEDED"
                    elif row.attempts >= self.max_attempts:
                        job.status = "BLOCKED"
            return True

    def run_once(self) -> bool:
        operation = self._claim()
        if operation is None:
            return False
        provider = None
        try:
            provider = self._provider(operation)
            if operation["status"] == "PENDING":
                result = provider.submit(operation["kind"], operation["request_snapshot"], operation["idempotency_key"])
            else:
                result = provider.query(operation["idempotency_key"])
                # Crash-before-call recovery: authoritative absence allows only the SAME key/snapshot.
                if result.status == "NOT_FOUND" and isinstance(provider, DemoProvider):
                    result = provider.submit(operation["kind"], operation["request_snapshot"], operation["idempotency_key"])
        except ProviderUnavailable as exc:
            result = ProviderResult("UNKNOWN", error=str(exc))
        except Exception as exc:
            result = ProviderResult("UNKNOWN", error=type(exc).__name__)
        finally:
            if isinstance(provider, DemoProvider):
                provider._client.close()
        try:
            self._result(operation, result)
        except ValueError as exc:
            # A receipt with wrong amount/device/address cannot settle resources.
            # Keep the obligation visible as UNKNOWN and let bounded reconciliation handle it.
            self._result(operation, ProviderResult("UNKNOWN", error="receipt_rejected:" + type(exc).__name__))
        return True

    def process_callback_once(self) -> bool:
        """Callbacks only trigger authoritative queries, never create an operation or trust ordering."""
        terminal_claim = None
        with self.factory.begin() as session:
            jobs = session.scalars(select(BusinessJob).where(
                BusinessJob.kind == "reconcile_operation", BusinessJob.status.in_(["PENDING", "RUNNING"])
            ).order_by(BusinessJob.status, BusinessJob.id).with_for_update(skip_locked=True).limit(20))
            for job in jobs:
                operation = session.get(Operation, job.payload["operation_id"], with_for_update=True)
                if operation is None:
                    job.status = "IGNORED"
                    return True
                if operation.status in {"SUCCEEDED", "FAILED"}:
                    if job.payload.get("provider_status") == operation.status:
                        job.status = "SUCCEEDED"
                        return True
                    if operation.lease_until and as_utc(operation.lease_until) >= utcnow():
                        continue
                    # A conflicting terminal callback must be checked against the provider,
                    # while local terminal state and settlement remain monotonic.
                    operation.lease_owner = self.owner
                    operation.lease_version += 1
                    operation.lease_until = utcnow() + timedelta(seconds=self.lease_seconds)
                    job.status = "RUNNING"
                    terminal_claim = (job.id, operation_snapshot(operation))
                    break
                if job.status == "RUNNING":
                    continue
                # The ordinary lease poller queries the authority and recovers after crashes.
                operation.available_at = utcnow()
                if operation.status == "PENDING":
                    operation.status = "UNKNOWN"
                job.status = "BLOCKED" if operation.attempts >= self.max_attempts else "RUNNING"
                return True
            else:
                return False
        if terminal_claim:
            job_id, snapshot = terminal_claim
            provider = None
            try:
                provider = self._provider(snapshot)
                result = provider.query(snapshot["idempotency_key"])
            except Exception as exc:
                result = ProviderResult("UNKNOWN", error=type(exc).__name__)
            finally:
                if isinstance(provider, DemoProvider):
                    provider._client.close()
            with self.factory.begin() as session:
                row = session.get(Operation, snapshot["id"], with_for_update=True)
                job = session.get(BusinessJob, job_id, with_for_update=True)
                if row.lease_owner == self.owner and row.lease_version == snapshot["lease_version"]:
                    job.status = "SUCCEEDED" if result.status == row.status else "BLOCKED"
                    if job.status == "BLOCKED":
                        row.result = {**row.result, "provider_callback_conflict": result.status,
                                      "provider_callback_error": result.error}
                    row.lease_owner = None
                    row.lease_until = None
        return True


def main():
    from .chatwoot import ChatwootAdapter
    from .config import Settings
    from .domain import ensure_environment
    from .notifications import notify_once
    from .system_api import heartbeat
    settings = Settings.from_env()
    factory = create_session_factory(settings.database_url)
    with factory.begin() as session:
        ensure_environment(session, settings.app_mode, settings.environment_id)
    worker = OperationsWorker(factory, app_mode=settings.app_mode,
                              environment_id=settings.environment_id,
                              demo_url=os.getenv("DEMO_PROVIDER_URL", ""),
                              demo_access_token=os.getenv("DEMO_PROVIDER_ACCESS_TOKEN", ""))
    adapter = ChatwootAdapter(settings.chatwoot_base_url, settings.chatwoot_account_id,
                              settings.chatwoot_inbox_id, settings.chatwoot_bot_access_token,
                              bot_id=settings.chatwoot_bot_id,
                              read_access_token=settings.chatwoot_read_access_token or None) if settings.chatwoot_bot_access_token else None
    while True:
        heartbeat(factory, "operations-worker")
        callback = worker.process_callback_once()
        executed = worker.run_once()
        notified = notify_once(settings, factory, adapter)
        if not callback and not executed and not notified:
            time.sleep(0.5)


if __name__ == "__main__":
    main()
