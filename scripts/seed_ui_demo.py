"""Create isolated, clearly labelled demo records for real browser acceptance.

Does not reset accounts, replace existing data, or bypass application transitions.
Only the demo seed is direct SQL; browser tests perform all business actions via UI.
"""

import json
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
        raise RuntimeError("Browser acceptance seeding is restricted to APP_MODE=demo")
    factory = create_session_factory(settings.database_url)
    run_id = uuid4().hex[:12]
    with factory.begin() as session:
        ensure_environment(session, settings.app_mode, settings.environment_id)
        customer = session.scalar(select(User).where(User.username == "customer-a"))
        product = session.scalar(select(Product).where(Product.sku == "HP-001"))
        policy = session.scalar(select(PolicyVersion).where(PolicyVersion.version == "demo_policy_v1"))
        if not customer or not product or not policy:
            raise RuntimeError("Run the normal demo seed before browser acceptance")
        order = import_order(session, customer_id=customer.id, external_id=f"E2E-{run_id}",
            product_id=product.id, quantity=2, paid_cents=65901, policy_id=policy.id, delivered_at=utcnow())
        # Give this acceptance run its own real stock unit. The exchange still
        # acquires it through the normal guarded domain transaction.
        add_stock(session, product.id, [f"E2E-STOCK-{run_id}"], "e2e-demo-seed")
        item_ids = session.scalars(select(OrderItem.id).where(OrderItem.order_id == order.id)).all()
        units = session.scalars(select(OrderUnit).where(OrderUnit.order_item_id.in_(item_ids)).order_by(OrderUnit.unit_index)).all()
        result = {"source": "real PostgreSQL demo seed for isolated UI acceptance", "order_id": order.id,
                  "external_id": order.external_id, "units": [{"id": unit.id, "serial": unit.serial_number} for unit in units]}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
