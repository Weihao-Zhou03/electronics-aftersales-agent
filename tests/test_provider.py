"""Explicit synthetic contract tests plus actual PostgreSQL/network provider checks."""
import hashlib
import hmac
import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from aftersales.db import create_session_factory
from aftersales.fulfillment import (
    DemoProvider,
    ManualProvider,
    ProviderUnavailable,
    select_provider,
    verify_callback,
)
from alembic import command
from demo_provider import service
from demo_provider.main import ProviderSettings, create_app
from demo_provider.models import Callback, Fault, Operation

TOKEN = "service-" + "a" * 40
ADMIN = "admin-" + "b" * 40
SIGNING = "signing-" + "c" * 40
REFUND = {"case_id": "case-example", "device_id": "device-example", "amount_cents": 12345, "currency": "CNY"}
SHIPMENT = {"case_id": "case-example", "device_id": "device-example", "sku": "DEMO-HEADPHONES",
            "outbound_device_id": "new-device-serial", "shipping_address": {
                "name": "演示收件人", "phone": "13800000000", "region": "演示省 演示市", "detail": "虚构路1号"}}


def config():
    return ProviderSettings(database_url="postgresql+psycopg://unused", access_token=TOKEN,
                            admin_token=ADMIN, signing_secret=SIGNING, environment_id="provider-test")


@pytest.fixture
def factory():
    result = create_session_factory("sqlite://")
    service.init_synthetic_schema(result, "provider-test")
    yield result
    result.kw["bind"].dispose()


def test_synthetic_restart_idempotency_conflict_and_explicit_receipts(factory):
    app = create_app(config(), factory)
    with TestClient(app) as client:
        assert client.post("/refunds", json={"idempotency_key": "k", "snapshot": REFUND}).status_code == 401
        headers = {"Authorization": f"Bearer {TOKEN}"}
        first = client.post("/refunds", json={"idempotency_key": "k", "snapshot": REFUND}, headers=headers).json()
        assert first["simulated"] is True and first["result"]["amount_cents"] == 12345
        assert first["result"]["refund_reference"].startswith("SIM-REFUND-")
        assert client.post("/refunds", json={"idempotency_key": "k", "snapshot": REFUND}, headers=headers).json() == first
        assert client.post("/refunds", json={"idempotency_key": "k", "snapshot": {**REFUND, "amount_cents": 1}}, headers=headers).status_code == 409
    # New service app against the same store, not an in-memory response registry.
    with TestClient(create_app(config(), factory)) as client:
        assert client.get("/operations/by-key/k", headers=headers).json() == first
        assert client.post("/shipments", json={"idempotency_key": "s", "snapshot": SHIPMENT}, headers=headers).json()["result"]["outbound_device_id"] == SHIPMENT["outbound_device_id"]
        assert client.post("/shipments", json={"idempotency_key": "bad", "snapshot": {**SHIPMENT, "shipping_address": {}}}, headers=headers).status_code == 422


def test_fault_auth_processing_settle_and_identical_duplicate_delivery(factory):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    admin = {"Authorization": f"Bearer {ADMIN}"}
    with TestClient(create_app(config(), factory)) as client:
        assert client.put("/admin/faults/k", json={"mode": "reject"}, headers=headers).status_code == 403
        assert client.put("/admin/faults/k", json={"mode": "processing"}, headers=admin).status_code == 200
        receipt = client.post("/refunds", json={"idempotency_key": "k", "snapshot": REFUND}, headers=headers).json()
        assert receipt["status"] == "RUNNING"
        assert client.post("/admin/operations/k/settle", json={"success": True}, headers=admin).json()["status"] == "SUCCEEDED"
        assert client.post("/admin/operations/k/settle", json={"success": False}, headers=admin).json()["status"] == "SUCCEEDED"
        assert client.post("/admin/operations/k/callbacks", json={"copies": 2}, headers=admin).status_code == 200
    with factory() as session:
        payloads = [item.payload for item in session.scalars(select(Callback))]
        duplicate_groups = {}
        for payload in payloads:
            duplicate_groups.setdefault(payload["delivery_id"], []).append(payload)
        duplicate = next(values for values in duplicate_groups.values() if len(values) == 2)
        assert duplicate[0] == duplicate[1]
        assert any(payload["status"] == "RUNNING" for payload in payloads)


def test_pilot_wrong_environment_and_manual_never_use_http(factory):
    with pytest.raises(ValueError, match="pilot"):
        ProviderSettings(**{**config().__dict__, "app_mode": "pilot"}).validate()
    with pytest.raises(ValueError, match="environment"):
        service.initialize(factory, "another-environment")
    operation = {"environment_id": "pilot-1", "execution_mode": "manual", "provider_name": "manual", "provider_version": "1"}
    assert isinstance(select_provider(operation, app_mode="pilot", environment_id="pilot-1"), ManualProvider)
    assert ManualProvider().submit("refund", REFUND, "x").status == "AWAITING_MANUAL"
    with pytest.raises(ProviderUnavailable):
        select_provider({**operation, "execution_mode": "external"}, app_mode="pilot", environment_id="pilot-1")
    with pytest.raises(ProviderUnavailable):
        select_provider({**operation, "execution_mode": "demo", "provider_name": "demo-provider"}, app_mode="pilot", environment_id="pilot-1")


def test_raw_provider_signature_and_original_timestamp_string():
    payload = {"simulated": True, "provider_name": "demo-provider", "provider_version": "1",
               "delivery_id": "delivery-1", "idempotency_key": "key", "status": "SUCCEEDED"}
    raw = json.dumps(payload).encode()
    timestamp = "0001700000000"
    signed = "sha256=" + hmac.new(SIGNING.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    assert verify_callback(raw, signed, timestamp, SIGNING, now=1700000000) == payload
    for body, signature, ts, secret in ((raw + b" ", signed, timestamp, SIGNING),
                                      (raw, signed, timestamp, "wrong" * 10),
                                      (raw, signed, "1699990000", SIGNING)):
        with pytest.raises(ValueError):
            verify_callback(body, signature, ts, secret, now=1700000000)


def test_actual_connection_failure_is_unknown_without_a_simulated_success():
    with socket.socket() as bound_but_not_listening:
        bound_but_not_listening.bind(("127.0.0.1", 0))
        port = bound_but_not_listening.getsockname()[1]
        adapter = DemoProvider(f"http://127.0.0.1:{port}", TOKEN, timeout=1)
        try:
            result = adapter.submit("refund", REFUND, "no-listener")
            assert result.status == "UNKNOWN" and result.error in {"ConnectError", "ConnectTimeout"}
            assert not result.result
        finally:
            adapter._client.close()


@pytest.fixture
def pg_factory():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for actual PostgreSQL provider checks")
    engine = create_engine(url)
    assert engine.dialect.name == "postgresql"
    schema = "provider_test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    factory = sessionmaker(bind=scoped, expire_on_commit=False)
    config = Config(str(Path(__file__).resolve().parents[1] / "provider_alembic.ini"))
    with scoped.begin() as connection:
        config.attributes.update(connection=connection, schema=schema)
        command.upgrade(config, "head")
    service.bootstrap_environment(factory, "provider-test")
    service.initialize(factory, "provider-test")
    yield factory
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


@pytest.mark.postgres
def test_actual_postgres_provider_concurrent_key_and_restart(pg_factory):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: service.submit(pg_factory, "refund", REFUND, "same-key")[0], range(12)))
    assert len({value["provider_operation_id"] for value in results}) == 1
    with pg_factory() as session:
        assert session.scalar(select(func.count()).select_from(Operation)) == 1
    service.initialize(pg_factory, "provider-test")
    assert service.query(pg_factory, "same-key")["status"] == "SUCCEEDED"
    with pytest.raises(service.RequestConflict):
        service.submit(pg_factory, "refund", {**REFUND, "amount_cents": 999}, "same-key")


@pytest.mark.postgres
def test_actual_provider_migration_downgrade_upgrade_and_runtime_refusal(pg_factory):
    scoped = pg_factory.kw["bind"]
    schema = scoped.get_execution_options()["schema_translate_map"][None]
    config = Config(str(Path(__file__).resolve().parents[1] / "provider_alembic.ini"))
    with scoped.begin() as connection:
        config.attributes.update(connection=connection, schema=schema)
        command.downgrade(config, "base")
    with pytest.raises(ValueError, match="tables|migrat"):
        service.initialize(pg_factory, "provider-test")
    with scoped.begin() as connection:
        config.attributes.update(connection=connection, schema=schema)
        command.upgrade(config, "head")
    service.bootstrap_environment(pg_factory, "provider-test")
    service.initialize(pg_factory, "provider-test")


@pytest.mark.postgres
def test_actual_http_response_loss_preserves_effect_and_query(pg_factory):
    app = create_app(config(), pg_factory)
    with socket.socket() as temporary:
        temporary.bind(("127.0.0.1", 0))
        port = temporary.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    adapter = DemoProvider(f"http://127.0.0.1:{port}", TOKEN, timeout=3)
    try:
        with pg_factory.begin() as session:
            session.add(Fault(idempotency_key="lost-after", mode="apply-then-drop"))
            session.add(Fault(idempotency_key="lost-before", mode="disconnect-before"))
        lost = adapter.submit("refund", REFUND, "lost-after")
        assert lost.status == "UNKNOWN" and lost.error == "RemoteProtocolError"
        result = adapter.query("lost-after")
        assert result.status == "SUCCEEDED" and result.result["amount_cents"] == 12345
        repeated = adapter.submit("refund", REFUND, "lost-after")
        assert repeated.provider_operation_id == result.provider_operation_id
        assert adapter.submit("refund", REFUND, "lost-before").status == "UNKNOWN"
        assert adapter.query("lost-before").status == "NOT_FOUND"
        with pg_factory() as session:
            assert session.scalar(select(func.count()).select_from(Operation)) == 1
    finally:
        adapter._client.close()
        server.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive()
