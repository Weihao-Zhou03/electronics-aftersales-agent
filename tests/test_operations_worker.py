"""Synthetic provider outcomes against real domain transactions, plus lease/callback safety."""
import hashlib
import hmac
import json
import os
import time
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from aftersales import domain
from aftersales.auth import User, seed_demo
from aftersales.business_models import (
    AfterSalesCase,
    BusinessJob,
    InventoryReservation,
    Operation,
    Order,
    OrderItem,
    OrderUnit,
    RefundAllocation,
)
from aftersales.db import create_session_factory, init_db, utcnow
from aftersales.demo_seed import seed_business
from aftersales.fulfillment import ProviderResult
from aftersales.operations_worker import OperationsWorker, ingest_callback

SECRET = "test-signing-" + "x" * 40


@pytest.fixture
def factory():
    factory = create_session_factory("sqlite://")
    init_db(factory)
    seed_demo(factory, "synthetic-worker-password")
    seed_business(factory, environment_id="worker-test")
    yield factory
    factory.kw["bind"].dispose()


def make_obligation(factory, *, mode="demo", environment_id="worker-test", kind="return"):
    with factory.begin() as session:
        customer = session.scalar(select(User).where(User.username == "customer-a"))
        customer_id = customer.id
        unit = session.scalar(select(OrderUnit).join(OrderItem).join(Order).where(Order.customer_id == customer_id))
        unit_id, serial = unit.id, unit.serial_number
        proposal = domain.create_proposal(session, customer_id, unit_id, kind, {
            "reason": "演示售后", "unused": True, "accessories_complete": True, "quality_issue": True})
    with factory.begin() as session:
        case = domain.confirm_proposal(session, customer_id, proposal["id"], proposal["version"], proposal["summary_hash"])
    with factory.begin() as session:
        case = domain.review_case(session, "staff-test", case["id"], case["version"], "approve_return", "同意寄回")
    with factory.begin() as session:
        case = domain.record_return_shipment(session, customer_id, case["id"], case["version"], "模拟物流", "SIM-RETURN-1")
    with factory.begin() as session:
        case = domain.receive_case(session, "staff-test", case["id"], case["version"], serial)
    with factory.begin() as session:
        domain.inspect_case(session, "staff-test", case["id"], case["version"], True,
                            {"reason": "验收通过", "unused": True, "accessories_complete": True, "quality_issue": True}, config={"app_mode": "demo", "fulfillment_mode": mode,
                                                        "environment_id": environment_id})
    with factory() as session:
        operation = session.scalar(select(Operation))
        return operation.id, operation.request_snapshot


class SyntheticProvider:
    def __init__(self, snapshot, *, unknown=False):
        self.submissions, self.queries = [], []
        self.snapshot, self.unknown = snapshot, unknown

    def success(self):
        return ProviderResult("SUCCEEDED", "synthetic-remote-1", {
            "simulated": True, "amount_cents": self.snapshot["amount_cents"], "currency": "CNY",
            "refund_reference": "SIM-REFUND-SYNTHETIC"})

    def submit(self, kind, snapshot, key):
        self.submissions.append((kind, snapshot, key))
        return ProviderResult("UNKNOWN", error="SyntheticResponseLoss") if self.unknown else self.success()

    def query(self, key):
        self.queries.append(key)
        return self.success()


def worker(factory, provider):
    return OperationsWorker(factory, app_mode="demo", environment_id="worker-test", provider_factory=lambda _: provider)


def test_unknown_keeps_allocation_then_queries_and_settles_same_obligation(factory):
    operation_id, snapshot = make_obligation(factory)
    provider = SyntheticProvider(snapshot, unknown=True)
    runner = worker(factory, provider)
    assert runner.run_once()
    with factory.begin() as session:
        operation = session.get(Operation, operation_id)
        assert operation.status == "UNKNOWN"
        assert session.scalar(select(RefundAllocation.status)) == "RESERVED"
        assert session.get(AfterSalesCase, operation.case_id).status == "FULFILLING"
        operation.available_at = utcnow() - timedelta(seconds=1)
    assert runner.run_once()
    with factory() as session:
        operation = session.get(Operation, operation_id)
        assert operation.status == "SUCCEEDED"
        assert session.scalar(select(RefundAllocation.status)) == "SETTLED"
        assert session.get(AfterSalesCase, operation.case_id).status == "COMPLETED"
        assert all(job.status == "SUCCEEDED" for job in session.scalars(select(BusinessJob).where(
            BusinessJob.kind.in_(["execute_operation", "reconcile_operation"]))))
    assert len(provider.submissions) == 1
    assert provider.queries == [provider.submissions[0][2]]


def test_expired_old_lease_cannot_settle_and_new_claim_queries(factory):
    operation_id, snapshot = make_obligation(factory)
    provider = SyntheticProvider(snapshot)
    first, second = worker(factory, provider), worker(factory, provider)
    old = first._claim()
    assert second._claim() is None
    with factory.begin() as session:
        session.get(Operation, operation_id).lease_until = utcnow() - timedelta(seconds=1)
    current = second._claim()
    assert current["lease_version"] > old["lease_version"]
    assert first._result(old, provider.success()) is False
    assert second._result(current, provider.success()) is True
    with factory() as session:
        assert session.get(Operation, operation_id).status == "SUCCEEDED"


def test_manual_obligation_never_claimed_or_calls_provider(factory):
    operation_id, _snapshot = make_obligation(factory, mode="manual")
    def forbidden(_operation):
        pytest.fail("manual obligation must not instantiate any HTTP provider")
    runner = OperationsWorker(factory, app_mode="demo", environment_id="worker-test", provider_factory=forbidden)
    assert runner.run_once() is False
    with factory() as session:
        assert session.get(Operation, operation_id).status == "AWAITING_MANUAL"


def signed(payload):
    raw = json.dumps(payload, sort_keys=True).encode()
    timestamp = str(int(time.time()))
    signature = "sha256=" + hmac.new(SECRET.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return raw, signature, timestamp


def test_callback_dedupe_unknown_and_stale_status_queries_authority(factory):
    operation_id, snapshot = make_obligation(factory)
    provider = SyntheticProvider(snapshot)
    runner = worker(factory, provider)
    assert runner.run_once()
    with factory() as session:
        key = session.get(Operation, operation_id).idempotency_key
    payload = {"simulated": True, "provider_name": "demo-provider", "provider_version": "1",
               "environment_id": "worker-test", "delivery_id": "d1", "idempotency_key": key,
               "status": "RUNNING", "event": "operation.updated"}
    args = signed(payload)
    assert ingest_callback(factory, *args, SECRET, environment_id="worker-test") == {"duplicate": False, "accepted": True}
    assert ingest_callback(factory, *args, SECRET, environment_id="worker-test")["duplicate"] is True
    assert runner.process_callback_once()
    assert provider.queries == [key]
    with factory() as session:
        assert session.get(Operation, operation_id).status == "SUCCEEDED"
    unknown = {**payload, "delivery_id": "unknown", "idempotency_key": "unknown-operation"}
    assert ingest_callback(factory, *signed(unknown), SECRET, environment_id="worker-test")["accepted"] is False
    with pytest.raises(ValueError, match="environment"):
        ingest_callback(factory, *args, SECRET, environment_id="different-environment")


@pytest.mark.postgres
@pytest.mark.live
@pytest.mark.parametrize("kind", ["return", "exchange"])
def test_actual_postgres_business_with_live_provider_response_loss(kind):
    """Real business PostgreSQL + independent live provider PostgreSQL + HTTP.

    Business test data stays in a temporary schema. Provider receipts are durable
    simulated integration evidence with unique keys, not real money transfers.
    """
    url, base_url = os.getenv("TEST_DATABASE_URL"), os.getenv("LIVE_PROVIDER_URL")
    token, admin = os.getenv("DEMO_PROVIDER_ACCESS_TOKEN"), os.getenv("DEMO_PROVIDER_ADMIN_TOKEN")
    environment_id = os.getenv("ENVIRONMENT_ID", "demo-local-1")
    if not all((url, base_url, token, admin)):
        pytest.skip("live provider URL/auth and TEST_DATABASE_URL must be explicitly configured")
    engine = create_engine(url)
    assert engine.dialect.name == "postgresql"
    schema = "provider_system_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    factory = sessionmaker(bind=scoped, expire_on_commit=False)
    try:
        init_db(factory)
        seed_demo(factory, "integration-only-password")
        seed_business(factory, environment_id=environment_id)
        operation_id, _snapshot = make_obligation(factory, environment_id=environment_id, kind=kind)
        with factory() as session:
            key = session.get(Operation, operation_id).idempotency_key
        with httpx.Client(timeout=10, trust_env=False) as client:
            configured = client.put(base_url + "/admin/faults/" + key,
                                    json={"mode": "apply-then-drop"}, headers={"Authorization": f"Bearer {admin}"})
            assert configured.status_code == 200
        runner = OperationsWorker(factory, app_mode="demo", environment_id=environment_id,
                                  demo_url=base_url, demo_access_token=token)
        assert runner.run_once()
        with factory.begin() as session:
            operation = session.get(Operation, operation_id)
            assert operation.status == "UNKNOWN"
            if kind == "return":
                assert session.scalar(select(RefundAllocation.status)) == "RESERVED"
            else:
                assert session.scalar(select(InventoryReservation.status)) == "ACTIVE"
            operation.available_at = utcnow() - timedelta(seconds=1)
        assert runner.run_once()
        with factory() as session:
            operation = session.get(Operation, operation_id)
            assert operation.status == "SUCCEEDED" and operation.attempts == 2
            assert session.get(AfterSalesCase, operation.case_id).status == "COMPLETED"
            assert operation.result["simulated"] is True
            if kind == "return":
                assert session.scalar(select(RefundAllocation.status)) == "SETTLED"
            else:
                assert session.scalar(select(InventoryReservation.status)) == "CONSUMED"
                assert operation.result["shipping_address"] == operation.request_snapshot["shipping_address"]
                assert operation.result["outbound_device_id"] == operation.request_snapshot["outbound_device_id"]
        with httpx.Client(timeout=10, trust_env=False) as client:
            receipt = client.get(base_url + "/operations/by-key/" + key,
                                 headers={"Authorization": f"Bearer {token}"}).json()
            assert receipt["status"] == "SUCCEEDED" and receipt["simulated"] is True
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
