"""Authoritative domain records. No model-generated amount or identity is trusted."""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import utcnow
from .models import Base, new_id


class Environment(Base):
    __tablename__ = "environment_identity"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="singleton")
    app_mode: Mapped[str] = mapped_column(String(16))
    environment_id: Mapped[str] = mapped_column(String(128))


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sku: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[str] = mapped_column(String(80), unique=True)
    scope: Mapped[dict] = mapped_column(JSON)
    rules_json: Mapped[dict] = mapped_column(JSON)
    sections: Mapped[list] = mapped_column(JSON)
    rule_source_map: Mapped[dict] = mapped_column(JSON)
    document_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    external_id: Mapped[str] = mapped_column(String(80), unique=True)
    payment_ref: Mapped[str] = mapped_column(String(128))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    source: Mapped[str] = mapped_column(String(32), default="demo")
    shipping_address: Mapped[dict] = mapped_column(JSON)
    unsupported_features: Mapped[list] = mapped_column(JSON, default=list)


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("quantity > 0 AND net_goods_paid_cents >= 0", name="ck_order_item_amount_quantity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    net_goods_paid_cents: Mapped[int] = mapped_column(Integer)


class OrderUnit(Base):
    __tablename__ = "order_units"
    __table_args__ = (UniqueConstraint("order_item_id", "unit_index", name="uq_item_unit_index"),
                     CheckConstraint("unit_index > 0 AND allocated_paid_cents >= 0", name="ck_unit_allocation"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_item_id: Mapped[str] = mapped_column(ForeignKey("order_items.id"))
    unit_index: Mapped[int] = mapped_column(Integer)
    serial_number: Mapped[str] = mapped_column(String(128), unique=True)
    allocated_paid_cents: Mapped[int] = mapped_column(Integer)
    sale_policy_version_id: Mapped[str | None] = mapped_column(ForeignKey("policy_versions.id"), nullable=True)
    replacement_for_case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    order_unit_id: Mapped[str] = mapped_column(ForeignKey("order_units.id"))
    type: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    shipping_address_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.id"))
    quoted_amount_cents: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    summary_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_case_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    agent_conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    agent_control_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AfterSalesCase(Base):
    __tablename__ = "after_sales_cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id"), unique=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    order_unit_id: Mapped[str] = mapped_column(ForeignKey("order_units.id"))
    type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default="PENDING_REVIEW")
    version: Mapped[int] = mapped_column(Integer, default=1)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    policy_snapshot: Mapped[dict] = mapped_column(JSON)
    confirmed_payload: Mapped[dict] = mapped_column(JSON)
    pending_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    return_shipment: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    shipping_address_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)


_active_case_predicate = text("status NOT IN ('REJECTED', 'CANCELLED', 'CLOSED_UNFULFILLED')")
Index("uq_unit_nonclosed_case", AfterSalesCase.order_unit_id, unique=True,
      postgresql_where=_active_case_predicate, sqlite_where=_active_case_predicate)


class CaseEvent(Base):
    __tablename__ = "case_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("after_sales_cases.id"))
    event: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(128))
    operation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReturnedItem(Base):
    __tablename__ = "returned_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("after_sales_cases.id"), unique=True)
    order_unit_id: Mapped[str] = mapped_column(ForeignKey("order_units.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_serial: Mapped[str] = mapped_column(String(128))
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    custody_status: Mapped[str] = mapped_column(String(32), default="HELD")
    disposition: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class StockUnit(Base):
    __tablename__ = "stock_units"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    serial_number: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="AVAILABLE")
    source: Mapped[str] = mapped_column(String(128))
    replacement_case_id: Mapped[str | None] = mapped_column(ForeignKey("after_sales_cases.id"), nullable=True)


class InventoryReservation(Base):
    __tablename__ = "inventory_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("after_sales_cases.id"))
    stock_unit_id: Mapped[str] = mapped_column(ForeignKey("stock_units.id"))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


for _column in (InventoryReservation.stock_unit_id, InventoryReservation.case_id):
    Index("uq_active_reservation_" + _column.name, _column, unique=True,
          postgresql_where=text("status = 'ACTIVE'"), sqlite_where=text("status = 'ACTIVE'"))


class RefundAllocation(Base):
    __tablename__ = "refund_allocations"
    __table_args__ = (CheckConstraint("amount_cents > 0", name="ck_refund_positive"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("after_sales_cases.id"), unique=True)
    order_unit_id: Mapped[str] = mapped_column(ForeignKey("order_units.id"))
    amount_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="RESERVED")


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("after_sales_cases.id"))
    kind: Mapped[str] = mapped_column(String(16))
    obligation_key: Mapped[str] = mapped_column(String(128), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    request_snapshot: Mapped[dict] = mapped_column(JSON)
    request_hash: Mapped[str] = mapped_column(String(64))
    execution_mode: Mapped[str] = mapped_column(String(16))
    provider_name: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(40))
    environment_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    provider_operation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_version: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BusinessJob(Base):
    __tablename__ = "business_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(40))
    dedupe_key: Mapped[str] = mapped_column(String(128), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyRequest(Base):
    __tablename__ = "idempotency_requests"
    __table_args__ = (UniqueConstraint("actor_id", "route_scope", "key", name="uq_actor_scope_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(String(36))
    route_scope: Mapped[str] = mapped_column(String(128))
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(PolicyVersion, "before_update")
@event.listens_for(PolicyVersion, "before_delete")
@event.listens_for(CaseEvent, "before_update")
@event.listens_for(CaseEvent, "before_delete")
def immutable_published_record(_mapper, _connection, _target):
    raise ValueError("published policy and audit events are immutable; append a new record")


@event.listens_for(Operation, "before_update")
def immutable_operation_obligation(_mapper, _connection, target):
    frozen = ["case_id", "kind", "obligation_key", "idempotency_key", "request_snapshot", "request_hash",
              "execution_mode", "provider_name", "provider_version", "environment_id"]
    state = inspect(target)
    if any(state.attrs[name].history.has_changes() for name in frozen):
        raise ValueError("operation obligation/provider/snapshot cannot be changed")
