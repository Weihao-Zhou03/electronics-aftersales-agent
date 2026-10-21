"""Add isolated, clearly labelled demo inputs; never mutate existing workflows.

Direct SQL is only for initial fictional users/products/orders/stock. Confirmations,
employee decisions, fulfillment and replenishment must use role-authorized APIs.
"""

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from aftersales.auth import User
from aftersales.business_models import OrderItem, OrderUnit, PolicyVersion, Product
from aftersales.config import Settings
from aftersales.db import create_session_factory, utcnow
from aftersales.demo_seed import import_order
from aftersales.domain import add_stock, ensure_environment


def main():
    settings = Settings.from_env()
    if settings.app_mode != "demo":
        raise RuntimeError("Scenario inputs are restricted to APP_MODE=demo")
    factory = create_session_factory(settings.database_url)
    run_id = uuid4().hex[:10]
    with factory.begin() as session:
        ensure_environment(session, settings.app_mode, settings.environment_id)
        template = session.scalar(select(User).where(User.username == "customer-a", User.is_demo.is_(True)))
        policy = session.scalar(select(PolicyVersion).where(PolicyVersion.version == "demo_policy_v1"))
        if not template or not policy:
            raise RuntimeError("Run the normal demo seed first")
        result: dict[str, Any] = {"source": "real PostgreSQL, new fictional scenario inputs only", "run_id": run_id, "scenarios": {}}
        for scenario, customers in [("A", 1), ("B", 1), ("C", 2), ("delay", 1)]:
            product = Product(sku=f"E2E-{scenario}-{run_id}", name=f"演示无线耳机 {scenario} {run_id}", category="headphone")
            session.add(product)
            session.flush()
            add_stock(session, product.id, [f"E2E-{scenario}-STOCK-{run_id}"], "isolated-scenario-demo-seed")
            entries = []
            for index in range(customers):
                customer = User(username=f"demo-{scenario.lower()}-{index}-{run_id}", role="customer",
                                password_hash=template.password_hash, active=True, is_demo=True)
                session.add(customer)
                session.flush()
                order = import_order(session, customer_id=customer.id, external_id=f"E2E-{scenario}-{index}-{run_id}",
                                     product_id=product.id, quantity=1, paid_cents=32951, policy_id=policy.id,
                                     delivered_at=utcnow())
                item = session.scalar(select(OrderItem).where(OrderItem.order_id == order.id))
                unit = session.scalar(select(OrderUnit).where(OrderUnit.order_item_id == item.id))
                entries.append({"username": customer.username, "customer_id": customer.id, "order_id": order.id,
                                "external_id": order.external_id, "unit_id": unit.id, "serial": unit.serial_number})
            result["scenarios"][scenario] = {"product_id": product.id, "sku": product.sku, "customers": entries}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
