"""Transactional simulation: one stable key has exactly one durable simulated effect."""
import hashlib
import json
from uuid import uuid4

from sqlalchemy import Column, MetaData, String, Table, inspect, select, text
from sqlalchemy.exc import IntegrityError

from .models import Base, Callback, Environment, Fault, Operation, utcnow

PROVIDER_NAME = "demo-provider"
PROVIDER_VERSION = "1"
SCHEMA_VERSION = "1"
FAULT_MODES = {"none", "reject", "disconnect-before", "apply-then-drop", "processing"}


class RequestConflict(ValueError):
    pass


def request_digest(kind: str, snapshot: dict) -> str:
    canonical = json.dumps({"kind": kind, "snapshot": snapshot}, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def bootstrap_environment(factory, environment_id: str):
    """Explicit deployment command initializes environment identity after Alembic migration."""
    with factory.begin() as session:
        marker = session.get(Environment, 1)
        if marker is None:
            session.add(Environment(id=1, app_mode="demo", environment_id=environment_id,
                                    schema_version=SCHEMA_VERSION))
        elif (marker.app_mode, marker.environment_id, marker.schema_version) != (
                "demo", environment_id, SCHEMA_VERSION):
            raise ValueError("provider database environment/schema mismatch; refusing mixed mode")


def initialize(factory, environment_id: str):
    """Runtime only checks the migrated schema and environment; never runs DDL or seeds."""
    engine = factory.kw["bind"]
    inspector = inspect(engine)
    schema = engine.get_execution_options().get("schema_translate_map", {}).get(None)
    if not inspector.has_table("provider_alembic_version", schema=schema):
        raise ValueError("provider database is not migrated; run explicit provider_migrate.py")
    if not set(Base.metadata.tables).issubset(set(inspector.get_table_names(schema=schema))):
        raise ValueError("provider migrated tables are missing; run explicit migrations")
    version_table = Table("provider_alembic_version", MetaData(), Column("version_num", String(32)), schema=schema)
    with factory() as session:
        version = session.scalar(select(version_table.c.version_num))
        marker = session.get(Environment, 1)
        if version != "0001_provider" or marker is None or (
                marker.app_mode, marker.environment_id, marker.schema_version) != ("demo", environment_id, SCHEMA_VERSION):
            raise ValueError("provider database migration/environment mismatch")


def init_synthetic_schema(factory, environment_id: str):
    """Only explicit synthetic SQLite tests may create metadata without Alembic."""
    if factory.kw["bind"].dialect.name != "sqlite":
        raise ValueError("real PostgreSQL tests must use provider Alembic migrations")
    Base.metadata.create_all(factory.kw["bind"])
    with factory.begin() as session:
        session.execute(text("CREATE TABLE provider_alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        session.execute(text("INSERT INTO provider_alembic_version VALUES ('0001_provider')"))
    bootstrap_environment(factory, environment_id)


def validate_snapshot(kind: str, snapshot: dict):
    for field in ("case_id", "device_id"):
        if not isinstance(snapshot.get(field), str) or not 1 <= len(snapshot[field]) <= 128:
            raise ValueError(f"invalid {field}")
    if kind == "refund":
        amount = snapshot.get("amount_cents")
        if isinstance(amount, bool) or not isinstance(amount, int) or not 0 < amount <= 2**31 - 1:
            raise ValueError("amount_cents must be positive integer cents")
        if snapshot.get("currency") != "CNY":
            raise ValueError("only CNY is supported")
    elif kind == "shipment":
        for field in ("sku", "outbound_device_id"):
            if not isinstance(snapshot.get(field), str) or not 1 <= len(snapshot[field]) <= 128:
                raise ValueError(f"invalid {field}")
        address = snapshot.get("shipping_address")
        if not isinstance(address, dict):
            raise ValueError("explicit shipping_address is required")
        for field, maximum in (("name", 80), ("phone", 32), ("region", 200), ("detail", 500)):
            if not isinstance(address.get(field), str) or not 1 <= len(address[field].strip()) <= maximum:
                raise ValueError(f"invalid shipping_address.{field}")
    else:
        raise ValueError("unsupported operation kind")


def receipt(operation: Operation) -> dict:
    return {"provider_name": PROVIDER_NAME, "provider_version": PROVIDER_VERSION,
            "environment_id": operation.environment_id,
            "provider_operation_id": operation.id, "idempotency_key": operation.idempotency_key,
            "kind": operation.kind, "request_hash": operation.request_hash,
            "status": operation.status, "result": operation.result, "simulated": True}


def successful_result(operation: Operation) -> dict:
    snapshot = operation.snapshot
    common = {"simulated": True, "case_id": snapshot["case_id"], "device_id": snapshot["device_id"]}
    if operation.kind == "refund":
        return {**common, "amount_cents": snapshot["amount_cents"], "currency": "CNY",
                "refund_reference": f"SIM-REFUND-{operation.id}"}
    return {**common, "sku": snapshot["sku"], "outbound_device_id": snapshot["outbound_device_id"],
            "shipping_address": snapshot["shipping_address"],
            "tracking_number": f"SIM-SHIP-{operation.id}", "shipped_at": utcnow().isoformat()}


def enqueue_callback(session, operation: Operation, *, status: str | None = None, delivery_id: str | None = None):
    event = receipt(operation)
    if status:
        event["status"] = status
    event["event"] = "operation.updated"
    event["delivery_id"] = delivery_id or str(uuid4())
    event["occurred_at"] = utcnow().isoformat()
    callback = Callback(operation_id=operation.id, payload=event)
    session.add(callback)
    return callback


def submit(factory, kind: str, snapshot: dict, key: str) -> tuple[dict | None, str]:
    validate_snapshot(kind, snapshot)
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise ValueError("idempotency_key must be 1..200 characters")
    digest = request_digest(kind, snapshot)
    try:
        with factory.begin() as session:
            existing = session.scalar(select(Operation).where(Operation.idempotency_key == key))
            if existing:
                if existing.request_hash != digest:
                    raise RequestConflict("same key used with a different immutable request")
                return receipt(existing), "none"
            fault = session.get(Fault, key)
            mode = fault.mode if fault else "none"
            if mode == "disconnect-before":
                return None, mode
            operation = Operation(id=str(uuid4()), idempotency_key=key, kind=kind,
                                  environment_id=session.get(Environment, 1).environment_id,
                                  request_hash=digest, snapshot=snapshot, result={},
                                  status="FAILED" if mode == "reject" else "RUNNING" if mode == "processing" else "SUCCEEDED")
            if operation.status == "SUCCEEDED":
                operation.result = successful_result(operation)
            elif operation.status == "FAILED":
                operation.result = {"simulated": True, "reason": "injected_explicit_rejection", "applied": False}
            session.add(operation)
            session.flush()
            enqueue_callback(session, operation)
            return receipt(operation), mode
    except IntegrityError:
        # Concurrent same-key posts converge on the unique durable operation.
        with factory() as session:
            existing = session.scalar(select(Operation).where(Operation.idempotency_key == key))
            if existing is None:
                raise
            if existing.request_hash != digest:
                raise RequestConflict("same key used with a different immutable request") from None
            return receipt(existing), "none"


def query(factory, key: str) -> dict | None:
    with factory() as session:
        operation = session.scalar(select(Operation).where(Operation.idempotency_key == key))
        return receipt(operation) if operation else None


def settle(factory, key: str, success: bool = True) -> dict | None:
    with factory.begin() as session:
        operation = session.scalar(select(Operation).where(Operation.idempotency_key == key).with_for_update())
        if operation is None:
            return None
        if operation.status not in {"SUCCEEDED", "FAILED"}:
            operation.status = "SUCCEEDED" if success else "FAILED"
            operation.result = successful_result(operation) if success else {
                "simulated": True, "reason": "injected_processing_rejection", "applied": False}
            enqueue_callback(session, operation)
        return receipt(operation)
