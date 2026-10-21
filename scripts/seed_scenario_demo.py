"""Add isolated, clearly labelled demo inputs; never mutate existing workflows.

Direct SQL is only for initial fictional users/products/orders/stock. Confirmations,
employee decisions, fulfillment and replenishment must use role-authorized APIs.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text

from aftersales.auth import User
from aftersales.business_models import (
    Order,
    OrderItem,
    OrderUnit,
    PolicyVersion,
    Product,
    StockUnit,
)
from aftersales.config import Settings
from aftersales.db import as_utc, create_session_factory, utcnow
from aftersales.demo_seed import import_order
from aftersales.domain import add_stock, ensure_environment

SCENARIOS = {"A": 1, "B": 1, "C": 2, "delay": 1}


def parse_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone is required")
        return parsed.astimezone(UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--at must be timezone-aware ISO8601, e.g. 2026-10-21T12:00:00Z") from exc


def parse_seed(value: str) -> str:
    if not value.strip() or len(value) > 128:
        raise argparse.ArgumentTypeError("--seed must contain 1–128 characters and cannot be blank")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--at", type=parse_at, help="fixed delivered_at for fixture replay; timezone required")
    result.add_argument("--seed", type=parse_seed, help="stable fictional input prefix; repeats reuse unchanged inputs")
    result.add_argument("--scenario", choices=[*SCENARIOS, "all"], default="all")
    return result


def seed_scenarios(factory, settings, *, at: datetime | None = None, seed: str | None = None,
                   scenario: str = "all") -> dict[str, Any]:
    if settings.app_mode != "demo":
        raise RuntimeError("Scenario inputs are restricted to APP_MODE=demo")
    if scenario not in {*SCENARIOS, "all"}:
        raise ValueError("unsupported scenario")
    if at is not None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("--at must be timezone-aware")
        at = at.astimezone(UTC)
    if seed is not None:
        parse_seed(seed)
    fingerprint = hashlib.sha256(("scenario-input-v1\0" + seed).encode()).hexdigest() if seed is not None else None
    run_id = "seed-" + fingerprint[:16] if fingerprint else uuid4().hex[:10]
    delivered_at = at or utcnow()
    selected = SCENARIOS if scenario == "all" else {scenario: SCENARIOS[scenario]}
    with factory.begin() as session:
        if fingerprint and session.bind.dialect.name == "postgresql":
            # Serialize concurrent invocations of this demo prefix only. This is
            # input creation, not an alternative business lock/transition path.
            key = int(fingerprint[:16], 16) - (1 << 63)
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        ensure_environment(session, settings.app_mode, settings.environment_id)
        template = session.scalar(select(User).where(User.username == "customer-a", User.is_demo.is_(True)))
        policy = session.scalar(select(PolicyVersion).where(PolicyVersion.version == "demo_policy_v1"))
        if not template or not policy:
            raise RuntimeError("Run the normal demo seed first")
        result: dict[str, Any] = {"source": "real PostgreSQL, fictional input creation or unchanged reuse only",
                                  "run_id": run_id, "scenarios": {}}
        for name, customers in selected.items():
            sku = f"E2E-{name}-{run_id}"
            product = session.scalar(select(Product).where(Product.sku == sku))
            existing = product is not None
            if not existing:
                product = Product(sku=sku, name=f"演示无线耳机 {name} {run_id}", category="headphone")
                session.add(product)
                session.flush()
                add_stock(session, product.id, [f"E2E-{name}-STOCK-{run_id}"], "isolated-scenario-demo-seed")
            elif product.category != "headphone" or session.scalar(select(StockUnit.id).where(
                    StockUnit.product_id == product.id, StockUnit.serial_number == f"E2E-{name}-STOCK-{run_id}")) is None:
                raise RuntimeError("Existing scenario inputs conflict; no records or stock were reset")
            entries = []
            for index in range(customers):
                username, external_id = f"demo-{name.lower()}-{index}-{run_id}", f"E2E-{name}-{index}-{run_id}"
                if existing:
                    customer = session.scalar(select(User).where(User.username == username))
                    order = session.scalar(select(Order).where(Order.external_id == external_id))
                    if (customer is None or customer.role != "customer" or not customer.is_demo or order is None
                            or order.customer_id != customer.id or order.source != "demo"):
                        raise RuntimeError("Existing scenario identity/order conflicts; nothing was reset")
                    if at is not None and as_utc(order.delivered_at) != at:
                        raise ValueError("This seed already has a different --at; use a new seed for different input time")
                else:
                    if session.scalar(select(User.id).where(User.username == username)) is not None:
                        raise RuntimeError("Existing scenario username conflicts; no account was overwritten")
                    customer = User(username=username, role="customer", password_hash=template.password_hash, active=True, is_demo=True)
                    session.add(customer)
                    session.flush()
                    order = import_order(session, customer_id=customer.id, external_id=external_id,
                                         product_id=product.id, quantity=1, paid_cents=32951, policy_id=policy.id,
                                         delivered_at=delivered_at)
                items = session.scalars(select(OrderItem).where(OrderItem.order_id == order.id)).all()
                if len(items) != 1 or items[0].product_id != product.id or items[0].quantity != 1:
                    raise RuntimeError("Existing scenario order structure conflicts; nothing was reset")
                units = session.scalars(select(OrderUnit).where(OrderUnit.order_item_id == items[0].id)).all()
                if len(units) != 1 or units[0].allocated_paid_cents != 32951 or units[0].sale_policy_version_id != policy.id:
                    raise RuntimeError("Existing scenario device conflicts; nothing was reset")
                unit = units[0]
                if not existing and seed is not None:
                    unit.serial_number = f"DEMO-{name}-{index}-{run_id}"
                entries.append({"username": customer.username, "customer_id": customer.id, "order_id": order.id,
                                "external_id": order.external_id, "unit_id": unit.id, "serial": unit.serial_number})
            result["scenarios"][name] = {"product_id": product.id, "sku": product.sku, "customers": entries}
    return result


def main():
    args = parser().parse_args()
    settings = Settings.from_env()
    result = seed_scenarios(create_session_factory(settings.database_url), settings,
                            at=args.at, seed=args.seed, scenario=args.scenario)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
