"""Business invariants, failure paths and real PostgreSQL concurrency evidence."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from aftersales import domain
from aftersales.auth import COOKIE_NAME, AuthSession, User, csrf_token, token_hash
from aftersales.business_models import (
    AfterSalesCase,
    InventoryReservation,
    Operation,
    Order,
    OrderUnit,
    PolicyVersion,
    Product,
    Proposal,
    RefundAllocation,
    ReturnedItem,
    StockUnit,
)
from aftersales.config import Settings
from aftersales.control import suspend_local
from aftersales.db import create_session_factory, init_db
from aftersales.demo_seed import DEMO_ADDRESS, import_order, policy_package
from aftersales.models import Conversation, OutboundMessage, OutboundReconciliation

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
FACTS = {"reason": "设备存在问题", "unused": True, "accessories_complete": True, "quality_issue": True}


def now():
    return NOW


def initialize(factory, create_schema=True):
    if create_schema:
        init_db(factory)
    with factory.begin() as session:
        alice = User(username="alice", role="customer", password_hash="synthetic-not-authenticated")
        bob = User(username="bob", role="customer", password_hash="synthetic-not-authenticated")
        staff = User(username="staff", role="support", password_hash="synthetic-not-authenticated")
        product = Product(sku="TEST-HP", name="测试耳机", category="headphone")
        session.add_all([alice, bob, staff, product])
        session.flush()
        policy = domain.publish_policy(session, policy_package(), now)
        order = import_order(session, customer_id=alice.id, external_id="TEST-ORDER", product_id=product.id,
            quantity=2, paid_cents=65901, policy_id=policy["id"], delivered_at=NOW - timedelta(days=3))
        units = session.scalars(select(OrderUnit).order_by(OrderUnit.unit_index)).all()
        return {"alice": alice.id, "bob": bob.id, "staff": staff.id, "product": product.id,
                "policy": policy["id"], "order": order.id, "units": [row.id for row in units]}


@pytest.fixture
def context():
    factory = create_session_factory("sqlite://")
    values = initialize(factory)
    yield factory, values
    factory.kw["bind"].dispose()


def tx(factory, action):
    return domain.run_transaction(factory, action)


def make_case(factory, data, kind="return", unit_index=0):
    proposal = tx(factory, lambda session: domain.create_proposal(session, data["alice"], data["units"][unit_index], kind, FACTS, clock=now))
    return tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], proposal["id"], 1, proposal["summary_hash"], now))


def to_inspection(factory, data, kind="return", unit_index=0):
    case = make_case(factory, data, kind, unit_index)
    case = tx(factory, lambda session: domain.review_case(session, data["staff"], case["id"], case["version"], "approve_return", "同意寄回", now))
    case = tx(factory, lambda session: domain.record_return_shipment(session, data["alice"], case["id"], case["version"], "演示物流", "TRACK-" + case["id"], now))
    with factory() as session:
        serial = session.get(OrderUnit, case["order_unit_id"]).serial_number
    return tx(factory, lambda session: domain.receive_case(session, data["staff"], case["id"], case["version"], serial, now))


def inspect(factory, data, case, mode="demo", clock=now):
    return tx(factory, lambda session: domain.inspect_case(session, data["staff"], case["id"], case["version"], True,
        FACTS, {"fulfillment_mode": mode, "environment_id": "test-demo", "app_mode": "demo"}, clock))


def refund_result(operation):
    return {"amount_cents": operation["request_snapshot"]["amount_cents"], "currency": "CNY",
            "refund_reference": "DEMO-REFUND-1", "provider_operation_id": "DEMO-OP-1", "simulated": True}


def test_deterministic_line_allocation_and_customer_scope(context):
    factory, data = context
    with factory() as session:
        amounts = session.scalars(select(OrderUnit.allocated_paid_cents).order_by(OrderUnit.unit_index)).all()
        assert amounts == [32951, 32950] and sum(amounts) == 65901
        for unit in [data["units"][0], str(uuid4())]:
            with pytest.raises(domain.DomainError) as caught:
                domain.evaluate_eligibility(session, data["bob"], unit, "return", FACTS, now)
            assert caught.value.status == 404 and caught.value.code == "RESOURCE_NOT_ACCESSIBLE"


@pytest.mark.parametrize("kind", ["return", "exchange"])
def test_definite_failure_holds_resources_until_evidenced_formal_closure(context, kind):
    factory, data = context
    if kind == "exchange":
        tx(factory, lambda session: domain.add_stock(session, data["product"], ["FAIL-CLOSE-STOCK"], "test-receipt"))
    case = inspect(factory, data, to_inspection(factory, data, kind))
    with factory() as session:
        operation_id = session.scalar(select(Operation.id))
    tx(factory, lambda session: domain.apply_operation_result(session, operation_id, "FAILED",
        {"reason": "explicit rejection with confirmed no effect", "applied": False, "simulated": True}, clock=now))
    with factory() as session:
        row = session.get(AfterSalesCase, case["id"])
        version = row.version
        assert row.status == "MANUAL_REVIEW"
        if kind == "return":
            assert session.scalar(select(RefundAllocation)).status == "RESERVED"
        else:
            assert session.scalar(select(InventoryReservation)).status == "ACTIVE"
            assert session.scalar(select(StockUnit)).status == "RESERVED"
    result = tx(factory, lambda session: domain.disposition_case(session, data["staff"], case["id"], version,
        "returned_to_customer", {"tracking": "EVIDENCED-RETURN-OF-ORIGINAL-ITEM"}, now))
    assert result["status"] == "CLOSED_UNFULFILLED"
    with factory() as session:
        if kind == "return":
            assert session.scalar(select(RefundAllocation)).status == "RELEASED"
        else:
            assert session.scalar(select(InventoryReservation)).status == "RELEASED"
            assert session.scalar(select(StockUnit)).status == "AVAILABLE"


def test_exact_expiry_and_unknown_information(context):
    factory, data = context
    with factory.begin() as session:
        session.get(Order, data["order"]).delivered_at = NOW - timedelta(days=14)
    with factory() as session:
        expired = domain.evaluate_eligibility(session, data["alice"], data["units"][0], "return", FACTS, now)
        before = domain.evaluate_eligibility(session, data["alice"], data["units"][0], "return", FACTS, lambda: NOW - timedelta(microseconds=1))
        after = domain.evaluate_eligibility(session, data["alice"], data["units"][0], "return", FACTS, lambda: NOW + timedelta(microseconds=1))
        unknown = domain.evaluate_eligibility(session, data["alice"], data["units"][0], "exchange", {"reason": "问题"}, now)
    assert expired["decision"] == "ineligible"
    assert before["decision"] == "eligible"
    assert after["decision"] == "ineligible"
    assert unknown["decision"] == "missing_information" and unknown["missing_fields"] == ["quality_issue"]


def test_policy_missing_and_unsupported_orders_go_to_human(context):
    factory, data = context
    with factory.begin() as session:
        session.get(OrderUnit, data["units"][0]).sale_policy_version_id = None
        session.get(Order, data["order"]).unsupported_features = ["installments"]
        session.get(OrderUnit, data["units"][1]).replacement_for_case_id = "original-case"
    with factory() as session:
        for unit_id in data["units"]:
            outcome = domain.evaluate_eligibility(session, data["alice"], unit_id, "return", FACTS, now)
            assert outcome["decision"] == "needs_review"


def test_policy_publish_is_immutable_and_old_device_keeps_version(context):
    factory, data = context
    tx(factory, lambda session: domain.publish_policy(session, policy_package("demo_policy_v2", 1, 2), now))
    with factory() as session:
        result = domain.evaluate_eligibility(session, data["alice"], data["units"][0], "return", FACTS, now)
        assert result["policy_version"] == "demo_policy_v1" and result["decision"] == "eligible"
    with pytest.raises(ValueError, match="immutable"):
        with factory.begin() as session:
            session.get(PolicyVersion, data["policy"]).document_text = "changed"
    with pytest.raises(domain.DomainError, match="IMMUTABLE"):
        tx(factory, lambda session: domain.publish_policy(session, policy_package(return_days=1), now))


def test_confirmation_is_single_and_cancel_releases_device(context):
    factory, data = context
    first = make_case(factory, data)
    with pytest.raises(domain.DomainError, match="DEVICE_ALREADY_HAS_CASE"):
        make_case(factory, data)
    repeated = tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], first["confirmed_payload"].get("proposal_id", session.get(AfterSalesCase, first["id"]).proposal_id), 1,
                    first["confirmed_payload"]["summary_hash"], now))
    assert repeated["id"] == first["id"]
    tx(factory, lambda session: domain.cancel_case(session, data["alice"], first["id"], 1, now))
    second = make_case(factory, data)
    assert second["id"] != first["id"]


def test_customer_confirmation_rechecks_exact_deadline_and_no_confirmation_no_case(context):
    factory, data = context
    with factory.begin() as session:
        session.get(Order, data["order"]).delivered_at = NOW - timedelta(days=14)
    def before():
        return NOW - timedelta(microseconds=1)
    draft = tx(factory, lambda session: domain.create_proposal(session, data["alice"], data["units"][0], "return", FACTS, clock=before))
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AfterSalesCase)) == 0
        assert session.scalar(select(func.count()).select_from(InventoryReservation)) == 0
    with pytest.raises(domain.DomainError, match="ELIGIBILITY_REQUIRES_ATTENTION"):
        tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], draft["id"], 1, draft["summary_hash"], now))
    with pytest.raises(domain.DomainError, match="ELIGIBILITY_REQUIRES_ATTENTION"):
        tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], draft["id"], 1, draft["summary_hash"], lambda: NOW + timedelta(microseconds=1)))
    case = tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], draft["id"], 1, draft["summary_hash"], before))
    assert case["accepted_at"] == before().isoformat()


def test_repeated_inspection_cannot_create_second_obligation(context):
    factory, data = context
    case = inspect(factory, data, to_inspection(factory, data))
    with pytest.raises(domain.DomainError, match="INSPECTION_STATE_CONFLICT"):
        inspect(factory, data, case)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Operation)) == 1
        assert session.scalar(select(func.count()).select_from(RefundAllocation)) == 1


def test_proposal_address_update_invalidates_old_confirmation(context):
    factory, data = context
    initial = tx(factory, lambda session: domain.create_proposal(session, data["alice"], data["units"][0], "exchange", FACTS, clock=now))
    address = {**DEMO_ADDRESS, "detail": "确认的新地址 2 号"}
    updated = tx(factory, lambda session: domain.update_proposal(session, data["alice"], initial["id"], 1, shipping_address=address, clock=now))
    assert updated["version"] == 2 and updated["summary_hash"] != initial["summary_hash"]
    with pytest.raises(domain.DomainError, match="PREVIEW_CHANGED"):
        tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], initial["id"], 1, initial["summary_hash"], now))
    confirmed = tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], initial["id"], 2, updated["summary_hash"], now))
    assert confirmed["shipping_address"] == address


def test_stale_staff_version_and_registered_return_cannot_be_cancelled(context):
    factory, data = context
    case = make_case(factory, data)
    case = tx(factory, lambda session: domain.review_case(session, data["staff"], case["id"], 1, "approve_return", "同意", now))
    with pytest.raises(domain.DomainError, match="VERSION_CONFLICT"):
        tx(factory, lambda session: domain.review_case(session, data["staff"], case["id"], 1, "reject", "旧页面", now))
    case = tx(factory, lambda session: domain.record_return_shipment(session, data["alice"], case["id"], case["version"], "物流", "1", now))
    with pytest.raises(domain.DomainError, match="CANCELLATION_REQUIRES_STAFF"):
        tx(factory, lambda session: domain.cancel_case(session, data["alice"], case["id"], case["version"], now))


def test_accepted_window_is_not_rechecked_at_late_inspection(context):
    factory, data = context
    case = to_inspection(factory, data)
    result = inspect(factory, data, case, clock=lambda: NOW + timedelta(days=90))
    assert result["status"] == "READY_TO_FULFILL"
    assert result["policy_snapshot"]["eligibility"]["decision"] == "eligible"


def test_refund_unknown_preserves_money_until_evidence_and_completed_occupies_device(context):
    factory, data = context
    case = inspect(factory, data, to_inspection(factory, data))
    operation = case["operations"][0]
    tx(factory, lambda session: domain.apply_operation_result(session, operation["id"], "UNKNOWN", {"transport_error": "lost"}, clock=now))
    with factory() as session:
        assert session.scalar(select(RefundAllocation)).status == "RESERVED"
        assert session.get(AfterSalesCase, case["id"]).status == "FULFILLING"
    bad_result = {**refund_result(operation), "amount_cents": 1}
    with pytest.raises(domain.DomainError, match="EVIDENCE_MISMATCH"):
        tx(factory, lambda session: domain.apply_operation_result(session, operation["id"], "SUCCEEDED", bad_result, clock=now))
    tx(factory, lambda session: domain.apply_operation_result(session, operation["id"], "SUCCEEDED", refund_result(operation), clock=now))
    # Duplicate provider receipt is harmless; settlement remains one allocation.
    tx(factory, lambda session: domain.apply_operation_result(session, operation["id"], "SUCCEEDED", refund_result(operation), clock=now))
    with factory() as session:
        assert session.scalar(select(RefundAllocation)).status == "SETTLED"
        assert session.scalar(select(func.count()).select_from(RefundAllocation)) == 1
    with pytest.raises(domain.DomainError, match="DEVICE_ALREADY_HAS_CASE"):
        make_case(factory, data)


def test_exchange_waits_for_stock_then_consumes_only_new_device(context):
    factory, data = context
    case = inspect(factory, data, to_inspection(factory, data, "exchange"))
    assert case["status"] == "WAITING_STOCK" and case["operations"] == []
    tx(factory, lambda session: domain.add_stock(session, data["product"], ["NEW-DEVICE-1"], "restock-proof"))
    case = tx(factory, lambda session: domain.resume_stock(session, data["staff"], case["id"], case["version"], {"environment_id": "test-demo"}, now))
    operation = case["operations"][0]
    snapshot = operation["request_snapshot"]
    tx(factory, lambda session: domain.apply_operation_result(session, operation["id"], "UNKNOWN", {}, clock=now))
    with factory() as session:
        assert session.scalar(select(StockUnit)).status == "RESERVED"
        assert session.scalar(select(InventoryReservation)).status == "ACTIVE"
        assert session.scalar(select(ReturnedItem)).custody_status == "HELD"
    result = {"sku": snapshot["sku"], "outbound_device_id": snapshot["outbound_device_id"], "shipping_address": snapshot["shipping_address"],
              "tracking_number": "DEMO-SHIP", "shipped_at": NOW.isoformat(), "simulated": True}
    tx(factory, lambda session: domain.apply_operation_result(session, operation["id"], "SUCCEEDED", result, clock=now))
    with factory() as session:
        assert session.scalar(select(StockUnit)).status == "DISPATCHED"
        assert session.scalar(select(InventoryReservation)).status == "CONSUMED"
        assert session.scalar(select(ReturnedItem)).custody_status == "HELD"


def test_bad_received_item_requires_documented_disposition(context):
    factory, data = context
    case = to_inspection(factory, data)
    case = tx(factory, lambda session: domain.inspect_case(session, data["staff"], case["id"], case["version"], False,
        {"reason": "不符合退货条件"}, {}, now))
    assert case["status"] == "MANUAL_REVIEW"
    with pytest.raises(domain.DomainError, match="EVIDENCE_REQUIRED"):
        tx(factory, lambda session: domain.disposition_case(session, data["staff"], case["id"], case["version"], "returned_to_customer", {}, now))
    result = tx(factory, lambda session: domain.disposition_case(session, data["staff"], case["id"], case["version"], "returned_to_customer", {"tracking": "RETURN-1"}, now))
    assert result["status"] == "CLOSED_UNFULFILLED"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(StockUnit)) == 0


def test_manual_fulfillment_requires_exact_snapshot_and_attestation(context):
    factory, data = context
    case = inspect(factory, data, to_inspection(factory, data), mode="manual")
    operation = case["operations"][0]
    assert case["status"] == "FULFILLING" and operation["status"] == "AWAITING_MANUAL"
    evidence = {**refund_result(operation), "performed_at": NOW.isoformat(), "external_reference": "MANUAL-1", "evidence": {"receipt": "private-proof"}}
    with pytest.raises(domain.DomainError, match="HASH_CONFLICT"):
        tx(factory, lambda session: domain.record_manual_result(session, data["staff"], operation["id"], 1, "wrong", "SUCCEEDED", evidence, now))
    result = tx(factory, lambda session: domain.record_manual_result(session, data["staff"], operation["id"], 1, operation["request_hash"], "SUCCEEDED", evidence, now))
    assert result["status"] == "SUCCEEDED" and result["result"]["verification_method"] == "manual_attestation"


def test_operation_environment_is_immutable_and_pilot_never_uses_demo(context):
    factory, data = context
    case = inspect(factory, data, to_inspection(factory, data))
    with pytest.raises(ValueError, match="cannot be changed"):
        with factory.begin() as session:
            session.get(Operation, case["operations"][0]["id"]).environment_id = "other-env"
    with pytest.raises(domain.DomainError, match="DATABASE_ENVIRONMENT_MISMATCH"):
        tx(factory, lambda session: domain.ensure_environment(session, "pilot", "different"))
    with pytest.raises(domain.DomainError, match="PILOT_DEMO_PROVIDER_FORBIDDEN"):
        # Isolated environment identity avoids conflating mismatch with provider mode rejection.
        case2 = to_inspection(factory, data, unit_index=1)
        tx(factory, lambda session: domain.inspect_case(session, data["staff"], case2["id"], case2["version"], True, FACTS,
            {"app_mode": "pilot", "fulfillment_mode": "demo", "environment_id": "test-demo"}, now))


def test_same_idempotency_key_cannot_change_request(context):
    factory, data = context
    def action(session):
        return {"ok": True}
    first = tx(factory, lambda session: domain.idempotent(session, data["alice"], "test", "key", {"x": 1}, lambda: action(session)))
    repeated = tx(factory, lambda session: domain.idempotent(session, data["alice"], "test", "key", {"x": 1}, lambda: pytest.fail("duplicate executed")))
    assert first == repeated
    with pytest.raises(domain.DomainError, match="PAYLOAD_MISMATCH"):
        tx(factory, lambda session: domain.idempotent(session, data["alice"], "test", "key", {"x": 2}, lambda: {}))


def test_takeover_revokes_only_unconfirmed_agent_proposals(context):
    factory, data = context
    first = tx(factory, lambda session: domain.create_proposal(session, data["alice"], data["units"][0], "return", FACTS, clock=now))
    second = tx(factory, lambda session: domain.create_proposal(session, data["alice"], data["units"][1], "return", FACTS, clock=now))
    with factory.begin() as session:
        conversation = Conversation(instance_id="synthetic", account_id=1, inbox_id=1, external_id=1,
                                    control_mode="BOT_ACTIVE", control_revision=0, input_revision=0, fencing_revision=0)
        session.add(conversation)
        session.flush()
        draft = session.get(Proposal, first["id"])
        draft.agent_conversation_id = conversation.id
        draft.agent_control_revision = 0
    with factory.begin() as session:
        conversation = session.scalar(select(Conversation))
        suspend_local(session, conversation, "staff", "takeover")
    with pytest.raises(domain.DomainError, match="REVOKED"):
        tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], first["id"], 1, first["summary_hash"], now))
    result = tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], second["id"], 1, second["summary_hash"], now))
    assert result["status"] == "PENDING_REVIEW"


def test_real_api_identity_amount_and_confirmation_boundaries(context):
    from aftersales.main import create_app
    factory, data = context
    settings = Settings(database_url="sqlite://", session_secret="test-only-secret-" * 4)
    token = "synthetic-alice-session"
    # Server-side sessions are injected; the actual Argon2 login is tested separately.
    with factory.begin() as session:
        session.add(AuthSession(user_id=data["alice"], token_hash=token_hash(token), expires_at=NOW + timedelta(days=1)))
    client = TestClient(create_app(settings, factory))
    client.cookies.set(COOKIE_NAME, token)
    headers = {"Origin": "http://localhost:8000", "X-CSRF-Token": csrf_token(settings, token), "Idempotency-Key": "proposal-create"}
    assert client.get("/api/v1/orders").status_code == 200
    assert client.get("/api/v1/staff/cases").status_code == 403
    assert client.post("/api/v1/proposals", json={"order_unit_id": data["units"][0], "type": "return", "facts": FACTS}, headers={}).status_code == 403
    payload = {"order_unit_id": data["units"][0], "type": "return", "facts": FACTS}
    assert client.post("/api/v1/proposals", json={**payload, "amount_cents": 1}, headers=headers).status_code == 422
    created = client.post("/api/v1/proposals", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    proposal = created.json()
    confirmation = {"expected_version": 1, "summary_hash": proposal["summary_hash"]}
    confirmed = client.post(f"/api/v1/proposals/{proposal['id']}/confirm", json=confirmation,
                           headers={**headers, "Idempotency-Key": "confirm-once"})
    assert confirmed.status_code == 200, confirmed.text
    repeat = client.post(f"/api/v1/proposals/{proposal['id']}/confirm", json=confirmation,
                         headers={**headers, "Idempotency-Key": "confirm-once"})
    assert repeat.json()["id"] == confirmed.json()["id"]
    assert repeat.json()["confirmed_payload"]["amount_cents"] == 32951


@pytest.mark.postgres
def test_real_postgres_one_active_case_and_last_stock(context):
    """Real PG threads race distinct requests; no fake database or provider result."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required")
    engine = create_engine(url)
    assert engine.dialect.name == "postgresql"
    schema = "business_test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    factory = sessionmaker(bind=engine.execution_options(schema_translate_map={None: schema}), expire_on_commit=False)
    try:
        data = initialize(factory)
        drafts = [tx(factory, lambda session: domain.create_proposal(session, data["alice"], data["units"][0], "exchange", FACTS, clock=now)) for _ in range(2)]
        def confirm(draft):
            try:
                return tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], draft["id"], 1, draft["summary_hash"], now))
            except domain.DomainError as error:
                return {"error": error.code}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, drafts))
        assert sum("id" in result for result in results) == 1
        case1 = next(result for result in results if "id" in result)
        # Advance existing first case and separately create second device's case.
        def advance(case):
            case = tx(factory, lambda session: domain.review_case(session, data["staff"], case["id"], case["version"], "approve_return", "同意", now))
            case = tx(factory, lambda session: domain.record_return_shipment(session, data["alice"], case["id"], case["version"], "物流", case["id"], now))
            with factory() as session:
                serial = session.get(OrderUnit, case["order_unit_id"]).serial_number
            return tx(factory, lambda session: domain.receive_case(session, data["staff"], case["id"], case["version"], serial, now))
        case1 = advance(case1)
        case2 = to_inspection(factory, data, "exchange", 1)
        tx(factory, lambda session: domain.add_stock(session, data["product"], ["LAST-NEW-DEVICE"], "real-pg-test-stock"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda case: inspect(factory, data, case), [case1, case2]))
        assert sorted(result["status"] for result in outcomes) == ["READY_TO_FULFILL", "WAITING_STOCK"]
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(Operation)) == 1
            assert session.scalar(select(func.count()).select_from(InventoryReservation).where(InventoryReservation.status == "ACTIVE")) == 1
        waiting = next(result for result in outcomes if result["status"] == "WAITING_STOCK")
        tx(factory, lambda session: domain.add_stock(session, data["product"], ["REPLENISHED-NEW-DEVICE"], "pg-replenishment"))
        resumed = tx(factory, lambda session: domain.resume_stock(session, data["staff"], waiting["id"], waiting["version"],
            {"app_mode": "demo", "fulfillment_mode": "demo", "environment_id": "test-demo"}, now))
        assert resumed["id"] == waiting["id"] and resumed["status"] == "READY_TO_FULFILL"
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(AfterSalesCase)) == 2
            assert session.scalar(select(func.count()).select_from(Operation)) == 2
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.postgres
def test_real_postgres_migrations_and_raw_sql_immutability(monkeypatch):
    """Actual Alembic upgrade/downgrade and raw-SQL guards, in an isolated schema."""
    from alembic.config import Config

    from alembic import command
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required")
    admin_engine = create_engine(url)
    schema = "migration_test_" + uuid4().hex
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema},public"})
    monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    engine = create_engine(scoped_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        configuration = Config("alembic.ini")
        command.upgrade(configuration, "0005_outbound_reconciliation")
        data = initialize(factory, create_schema=False)
        case = inspect(factory, data, to_inspection(factory, data))
        with factory.begin() as session:
            conversation = Conversation(instance_id="pg-migration", account_id=1, inbox_id=1, external_id=1,
                control_mode="HANDOFF_PENDING", control_revision=1, input_revision=1, fencing_revision=1)
            session.add(conversation)
            session.flush()
            outbound = OutboundMessage(intent_key="pg-audit-immutability", conversation_id=conversation.id,
                content="synthetic audit fixture", control_revision=0, input_revision=1, fencing_revision=0, status="NOT_DELIVERED")
            session.add(outbound)
            session.flush()
            audit = OutboundReconciliation(outbound_id=outbound.id, actor_id=data["staff"], outcome="not_delivered",
                control_revision=1, version=1, content_hash="0" * 64, evidence={"reason": "synthetic schema test"})
            session.add(audit)
            session.flush()
            audit_id = audit.id
        for sql, parameters in [
            ("UPDATE policy_versions SET document_text='rewritten' WHERE id=:id", {"id": data["policy"]}),
            ("UPDATE case_events SET event='rewritten' WHERE case_id=:id", {"id": case["id"]}),
            ("UPDATE operations SET environment_id='other-environment' WHERE id=:id", {"id": case["operations"][0]["id"]}),
            ("UPDATE outbound_reconciliations SET outcome='delivered' WHERE id=:id", {"id": audit_id}),
        ]:
            with pytest.raises(DBAPIError, match="immutable"):
                with engine.begin() as connection:
                    connection.execute(text(sql), parameters)
        # Mutable operation status remains usable, and settlement uses one transaction.
        operation = case["operations"][0]
        tx(factory, lambda session: domain.apply_operation_result(session, operation["id"], "SUCCEEDED", refund_result(operation), clock=now))
        with factory() as session:
            assert session.get(AfterSalesCase, case["id"]).status == "COMPLETED"
        command.downgrade(configuration, "0001_m0")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001_m0"
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()
