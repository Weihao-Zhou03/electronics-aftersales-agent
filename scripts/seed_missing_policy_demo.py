"""Explicit A06 missing-policy input; new fictional records only, demo only.

The deliberately absent sale_policy_version_id is fixture data, not a simulated
tool/API response. No existing policy/order/device or workflow state is modified.
"""

import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from aftersales.auth import User
from aftersales.business_models import Order, OrderItem, OrderUnit, Product
from aftersales.config import Settings
from aftersales.db import create_session_factory, utcnow
from aftersales.demo_seed import DEMO_ADDRESS
from aftersales.domain import ensure_environment


def main():
    settings = Settings.from_env()
    if settings.app_mode != "demo":
        raise RuntimeError("Missing-policy acceptance input is restricted to demo")
    run_id = uuid4().hex[:10]
    factory = create_session_factory(settings.database_url)
    with factory.begin() as session:
        ensure_environment(session, settings.app_mode, settings.environment_id)
        template = session.scalar(select(User).where(User.username == "customer-a", User.is_demo.is_(True)))
        if template is None:
            raise RuntimeError("Run the normal demo seed first")
        customer = User(username=f"demo-policy-missing-{run_id}", role="customer", active=True, is_demo=True,
                        password_hash=template.password_hash)
        product = Product(sku=f"A06-MISSING-{run_id}", name=f"缺政策输入演示耳机 {run_id}", category="headphone")
        session.add_all([customer, product])
        session.flush()
        now = utcnow()
        order = Order(customer_id=customer.id, external_id=f"A06-MISSING-{run_id}",
                      payment_ref=f"demo-a06-missing:{run_id}", paid_at=now - timedelta(days=2),
                      delivered_at=now, currency="CNY", source="demo", shipping_address=DEMO_ADDRESS,
                      unsupported_features=[])
        session.add(order)
        session.flush()
        item = OrderItem(order_id=order.id, product_id=product.id, quantity=1, net_goods_paid_cents=32951)
        session.add(item)
        session.flush()
        unit = OrderUnit(order_item_id=item.id, unit_index=1, serial_number=f"A06-MISSING-SERIAL-{run_id}",
                         allocated_paid_cents=32951, sale_policy_version_id=None)
        session.add(unit)
        session.flush()
        result = {"source": "Actual PostgreSQL new fictional input explicitly lacking purchase-policy association",
                  "fixture_condition": "new OrderUnit.sale_policy_version_id=None", "run_id": run_id,
                  "customer_id": customer.id, "username": customer.username, "order_id": order.id,
                  "external_id": order.external_id, "unit_id": unit.id, "serial": unit.serial_number,
                  "policy_id": None}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
