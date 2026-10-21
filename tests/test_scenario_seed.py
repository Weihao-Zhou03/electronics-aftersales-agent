"""Scenario-input CLI checks and real isolated PostgreSQL idempotency, no model calls."""
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from aftersales import domain
from aftersales.auth import User, seed_demo
from aftersales.business_models import (
    AfterSalesCase,
    CaseEvent,
    InventoryReservation,
    Operation,
    Order,
    Product,
    StockUnit,
)
from aftersales.config import Settings
from aftersales.db import as_utc
from aftersales.demo_seed import seed_business
from alembic import command
from scripts import seed_scenario_demo as script

AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", ["2026-10-21", "2026-10-21T12:00:00", "bad-date", "2026-99-21T12:00:00Z"])
def test_cli_rejects_invalid_or_naive_time(value):
    with pytest.raises(SystemExit) as error:
        script.parser().parse_args(["--at", value])
    assert error.value.code == 2


@pytest.mark.parametrize("value", ["a", "A,B", "return", ""])
def test_cli_rejects_unknown_scenario(value):
    with pytest.raises(SystemExit) as error:
        script.parser().parse_args(["--scenario", value])
    assert error.value.code == 2


@pytest.mark.parametrize("value", ["", "  ", "x" * 129])
def test_cli_rejects_empty_or_oversized_seed(value):
    with pytest.raises(SystemExit) as error:
        script.parser().parse_args(["--seed", value])
    assert error.value.code == 2


def test_cli_defaults_and_timezone_normalization():
    default = script.parser().parse_args([])
    assert default.at is None and default.seed is None and default.scenario == "all"
    parsed = script.parser().parse_args(["--at", "2026-10-01T20:00:00+08:00", "--seed", "replay-1", "--scenario", "C"])
    assert parsed.at == AT and parsed.seed == "replay-1" and parsed.scenario == "C"


def test_demo_guard_precedes_any_database_access():
    with pytest.raises(RuntimeError, match="APP_MODE=demo"):
        script.seed_scenarios(None, Settings(app_mode="pilot", fulfillment_mode="manual"))
    with pytest.raises(ValueError, match="timezone-aware"):
        script.seed_scenarios(None, Settings(), at=datetime(2026, 1, 1))


@pytest.fixture
def pg_context(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for actual isolated PostgreSQL scenario seed checks")
    admin = create_engine(url)
    assert admin.dialect.name == "postgresql"
    schema = "scenario_seed_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema},public"})
    engine = create_engine(scoped_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        command.upgrade(config, "head")
        seed_demo(factory, "scenario-seed-test-password")
        settings = Settings(environment_id="scenario-seed-test")
        seed_business(factory, environment_id=settings.environment_id, clock=lambda: AT - timedelta(days=10))
        yield factory, settings
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def snapshot(factory):
    with factory() as session:
        return {
            "counts": {model.__tablename__: session.scalar(select(func.count()).select_from(model))
                       for model in [User, Product, Order, StockUnit, AfterSalesCase, Operation, CaseEvent]},
            "customers": session.execute(select(User.id, User.password_hash, User.active).order_by(User.id)).all(),
            "orders": session.execute(select(Order.id, Order.delivered_at, Order.paid_at).order_by(Order.id)).all(),
            "stock": session.execute(select(StockUnit.id, StockUnit.status, StockUnit.replacement_case_id).order_by(StockUnit.id)).all(),
            "cases": session.execute(select(AfterSalesCase.id, AfterSalesCase.status, AfterSalesCase.version).order_by(AfterSalesCase.id)).all(),
            "operations": session.execute(select(Operation.id, Operation.status, Operation.version, Operation.request_hash).order_by(Operation.id)).all(),
            "reservations": session.execute(select(InventoryReservation.id, InventoryReservation.status).order_by(InventoryReservation.id)).all(),
        }


@pytest.mark.postgres
def test_fixed_seed_reuses_inputs_without_resetting_case_password_or_stock(pg_context):
    factory, settings = pg_context
    first = script.seed_scenarios(factory, settings, at=AT, seed="replay-order-1")
    entry = first["scenarios"]["A"]["customers"][0]
    assert entry["serial"] == "DEMO-A-0-" + first["run_id"]
    def clock():
        return AT + timedelta(hours=1)
    # Real domain decisions create an existing confirmed workflow and reserve
    # its sole stock item. No worker/provider/model is invoked by this seed test.
    with factory.begin() as session:
        proposal = domain.create_proposal(session, entry["customer_id"], entry["unit_id"], "exchange",
            {"reason": "seed replay preservation", "quality_issue": True},
            {"name": "演示客户", "phone": "13800000000", "region": "演示市", "detail": "演示路1号"}, clock)
    with factory.begin() as session:
        case = domain.confirm_proposal(session, entry["customer_id"], proposal["id"], proposal["version"], proposal["summary_hash"], clock)
    with factory.begin() as session:
        case = domain.review_case(session, "seed-test-support", case["id"], case["version"], "approve_return", "test approval", clock)
    with factory.begin() as session:
        case = domain.record_return_shipment(session, entry["customer_id"], case["id"], case["version"], "SIM", "SIM-SEED-RETURN", clock)
    with factory.begin() as session:
        case = domain.receive_case(session, "seed-test-support", case["id"], case["version"], entry["serial"], clock)
    with factory.begin() as session:
        case = domain.inspect_case(session, "seed-test-support", case["id"], case["version"], True,
            {"reason": "seed fixture inspection", "quality_issue": True},
            {"app_mode": "demo", "fulfillment_mode": "demo", "environment_id": settings.environment_id}, clock)
        customer = session.get(User, entry["customer_id"])
        customer.password_hash = "changed-test-fixture-hash"
        customer.active = False
    before = snapshot(factory)
    assert case["status"] == "READY_TO_FULFILL"
    assert any(row.status == "RESERVED" for row in before["stock"])
    second = script.seed_scenarios(factory, settings, at=AT.astimezone(UTC), seed="replay-order-1")
    assert second == first and snapshot(factory) == before
    assert set(script.seed_scenarios(factory, settings, at=AT, seed="replay-order-1", scenario="C")["scenarios"]) == {"C"}
    assert snapshot(factory) == before
    with pytest.raises(ValueError, match="different --at"):
        script.seed_scenarios(factory, settings, at=AT + timedelta(days=1), seed="replay-order-1")
    assert snapshot(factory) == before
    # Explicit synthetic receipt solely builds the completed/consumed fixture;
    # this is not a real provider integration test or a claim of external shipment.
    with factory.begin() as session:
        operation = session.scalar(select(Operation).where(Operation.case_id == case["id"]))
        request = operation.request_snapshot
        domain.apply_operation_result(session, operation.id, "SUCCEEDED", {
            "simulated": True, "sku": request["sku"], "outbound_device_id": request["outbound_device_id"],
            "shipping_address": request["shipping_address"], "tracking_number": "SIM-SEED-UNIT-TEST",
            "shipped_at": clock().isoformat()}, "synthetic-seed-test-provider", clock)
    completed = snapshot(factory)
    assert any(row.status == "DISPATCHED" for row in completed["stock"])
    assert any(row.status == "CONSUMED" for row in completed["reservations"])
    assert script.seed_scenarios(factory, settings, at=AT, seed="replay-order-1") == first
    assert snapshot(factory) == completed
    with pytest.raises(domain.DomainError, match="DATABASE_ENVIRONMENT_MISMATCH"):
        script.seed_scenarios(factory, replace(settings, environment_id="other-test"), seed="wrong-environment")
    assert snapshot(factory) == completed


@pytest.mark.postgres
def test_default_still_creates_all_new_current_inputs_and_selection_is_additive(pg_context, monkeypatch):
    factory, settings = pg_context
    monkeypatch.setattr(script, "utcnow", lambda: AT)
    first = script.seed_scenarios(factory, settings)
    second = script.seed_scenarios(factory, settings)
    assert first["run_id"] != second["run_id"]
    assert len(first["run_id"]) == len(second["run_id"]) == 10
    assert set(first["scenarios"]) == set(second["scenarios"]) == {"A", "B", "C", "delay"}
    ids = [entry["order_id"] for result in [first, second] for value in result["scenarios"].values() for entry in value["customers"]]
    assert len(ids) == len(set(ids)) == 10
    with factory() as session:
        assert all(as_utc(value) == AT for value in session.scalars(select(Order.delivered_at).where(Order.id.in_(ids))))
    a = script.seed_scenarios(factory, settings, at=AT, seed="selected-only", scenario="A")
    assert set(a["scenarios"]) == {"A"}
    c = script.seed_scenarios(factory, settings, at=AT, seed="selected-only", scenario="C")
    assert a["run_id"] == c["run_id"] and len(c["scenarios"]["C"]["customers"]) == 2
    assert script.seed_scenarios(factory, settings, at=AT, seed="selected-only", scenario="A") == a
