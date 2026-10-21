"""Idempotent demo records; never erase existing applications or reassign policies."""
import os
from datetime import timedelta

from sqlalchemy import select

from .auth import User, seed_demo
from .business_models import Order, OrderItem, OrderUnit, PolicyVersion, Product
from .config import Settings
from .db import create_session_factory, utcnow
from .domain import add_stock, ensure_environment, publish_policy, require
from .models import new_id

DEMO_ADDRESS = {"name": "演示客户", "phone": "13800000000", "region": "演示省 演示市", "detail": "演示街道 1 号"}


def policy_package(version="demo_policy_v1", return_days=14, exchange_days=30):
    return {"version": version, "scope": {"categories": ["headphone", "keyboard", "mouse"]},
        "rules": {"return_window_days": return_days, "exchange_window_days": exchange_days,
                  "return_requires_unused": True, "return_requires_all_accessories": True,
                  "exchange_requires_inspection": True, "same_sku_only": True},
        "sections": [{"id": "return_eligibility", "text": f"演示商家政策：未使用且配件齐全的商品，在签收后 {return_days} 天内确认退货申请。恰好到期视为到期；退款仅商品实付分摊金额。"},
                     {"id": "exchange_eligibility", "text": f"演示商家政策：质量问题在签收后 {exchange_days} 天内可确认同 SKU 换货申请；须寄回并经员工验收，新品库存于验收后预占。"}],
        "rule_source_map": {"return_window_days": "return_eligibility", "exchange_window_days": "exchange_eligibility"}}


def import_order(session, *, customer_id, external_id, product_id, quantity, paid_cents, policy_id,
                 delivered_at, shipping_address=None, source="demo", unsupported_features=None):
    """Deterministic per-line integer allocation; invalid batches roll back wholly."""
    require(type(quantity) is int and quantity > 0 and type(paid_cents) is int and paid_cents >= 0,
            "ORDER_IMPORT_QUANTITY_OR_AMOUNT_INVALID", 422)
    require(session.get(Product, product_id) is not None and session.get(PolicyVersion, policy_id) is not None,
            "ORDER_IMPORT_REFERENCE_INVALID", 422)
    order = Order(customer_id=customer_id, external_id=external_id, payment_ref="demo-payment:" + external_id,
                  paid_at=delivered_at - timedelta(days=2), delivered_at=delivered_at, currency="CNY", source=source,
                  shipping_address=shipping_address or DEMO_ADDRESS, unsupported_features=unsupported_features or [])
    session.add(order)
    session.flush()
    item = OrderItem(order_id=order.id, product_id=product_id, quantity=quantity, net_goods_paid_cents=paid_cents)
    session.add(item)
    session.flush()
    quotient, remainder = divmod(paid_cents, quantity)
    amounts = []
    for index in range(quantity):
        amount = quotient + (1 if index < remainder else 0)
        amounts.append(amount)
        session.add(OrderUnit(order_item_id=item.id, unit_index=index + 1,
                              serial_number="DEMO-" + new_id(), allocated_paid_cents=amount, sale_policy_version_id=policy_id))
    require(sum(amounts) == paid_cents, "ORDER_ALLOCATION_TOTAL_MISMATCH", 422)
    session.flush()
    return order


def seed_business(factory, *, clock=utcnow, environment_id="electronics-aftersales-demo"):
    with factory.begin() as session:
        ensure_environment(session, "demo", environment_id)
        policy = publish_policy(session, policy_package(), clock)
        publish_policy(session, policy_package("demo_policy_v2", 7, 21), clock)
        products = []
        for sku, name, category in [("HP-001", "无线耳机", "headphone"), ("KB-001", "机械键盘", "keyboard"), ("MS-001", "无线鼠标", "mouse")]:
            product = session.scalar(select(Product).where(Product.sku == sku))
            if product is None:
                product = Product(sku=sku, name=name, category=category)
                session.add(product)
                session.flush()
                add_stock(session, product.id, [f"DEMO-STOCK-{sku}-{number}" for number in range(1, 4)], "demo-initial-stock")
            products.append(product)
        for username in ["customer-a", "customer-b"]:
            user = session.scalar(select(User).where(User.username == username))
            require(user is not None, "SEED_DEMO_USERS_FIRST", 503)
            for index, product in enumerate(products):
                external_id = f"DEMO-{username.upper()}-{index + 1}"
                if session.scalar(select(Order.id).where(Order.external_id == external_id)) is None:
                    import_order(session, customer_id=user.id, external_id=external_id, product_id=product.id,
                                 quantity=2 if index == 0 else 1, paid_cents=[65901, 42900, 12900][index],
                                 policy_id=policy["id"], delivered_at=clock() - timedelta(days=3 + index))


def main():
    settings = Settings.from_env()
    require(settings.app_mode == "demo", "DEMO_SEED_FORBIDDEN_IN_PILOT", 503)
    factory = create_session_factory(settings.database_url)
    seed_demo(factory, os.environ.get("M0_DEMO_PASSWORD", ""))
    seed_business(factory, environment_id=settings.environment_id)
    print("Demo users/orders/policies/stock ready; existing records preserved.")


if __name__ == "__main__":
    main()
