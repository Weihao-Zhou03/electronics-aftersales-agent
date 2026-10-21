"""Model-visible schemas omit identity, amounts, approval and arbitrary IO."""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import or_, select

from . import domain
from .business_models import AfterSalesCase, Order, OrderItem, OrderUnit, Product, StockUnit
from .control import ControlConflict, handoff_conversation, remote_allows_bot
from .db import as_utc, utcnow
from .models import ChatBinding, Conversation
from .retrieval import EmbeddingUnavailable, search_policy


class AgentStopped(RuntimeError):
    pass


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListOrders(Parameters):
    keyword: str = Field(default="", max_length=80,
        description="Optional own-order filter: device serial number, product name/SKU or visible order number. When only a serial is known, use this tool first to resolve the authoritative order/unit UUID.")
    limit: int = Field(default=10, ge=1, le=20)


class OrderDetail(Parameters):
    order_id: str = Field(min_length=1, max_length=80,
        description="Server order UUID or visible order number, never a device serial number. If only a device serial is known, first call list_my_orders with that serial as keyword.")


class PolicyQuery(Parameters):
    order_unit_id: str
    question: str = Field(min_length=1, max_length=1000)


class Unit(Parameters):
    order_unit_id: str


class Facts(Parameters):
    reason: str = Field(default="", max_length=2000)
    unused: StrictBool | None = None
    accessories_complete: StrictBool | None = None
    quality_issue: StrictBool | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)


class ProposalInput(Unit):
    type: str = Field(pattern="^(return|exchange)$")
    facts: Facts


class CaseQuery(Parameters):
    case_id: str


class Handoff(Parameters):
    reason_code: str = Field(min_length=1, max_length=128)


TOOLS: dict[str, tuple[type[BaseModel], str]] = {
    "list_my_orders": (ListOrders, "查询本人订单和设备；不包含地址、支付凭据。"),
    "get_order_detail": (OrderDetail, "用内部订单ID或客户提供的订单号读取本人订单设备和购买时政策。"),
    "search_policy": (PolicyQuery, "按本人的设备购买版本检索真实政策条款。"),
    "evaluate_eligibility": (ProposalInput, "按权威订单金额和政策规则判断；未知条件保持未知。"),
    "check_exchange_availability": (Unit, "查询同SKU新品可用数，不预占。"),
    "prepare_after_sales_proposal": (ProposalInput, "准备需客户在已登录门户明确确认的草稿；不正式建单。"),
    "get_case_status": (CaseQuery, "按本人权限查询真实申请进度。"),
    "request_handoff": (Handoff, "立即停止Agent并请求人工处理。"),
}


def tool_definitions():
    return [{"type": "function", "function": {"name": name, "description": desc,
             "parameters": model.model_json_schema(), "strict": False}} for name, (model, desc) in TOOLS.items()]


def redact_for_model(value):
    if isinstance(value, dict):
        blocked = {"shipping_address", "shipping_address_snapshot", "address", "phone", "email", "password",
                   "token", "api_key", "payment_ref", "confirmed_payload", "result", "request_snapshot"}
        return {k: redact_for_model(v) for k, v in value.items() if k not in blocked}
    if isinstance(value, list):
        return [redact_for_model(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已隐藏]", value)
        value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[电话已隐藏]", value)
        value = re.sub(r"(?i)(?:api[_-]?key|authorization|password|密码)\s*[:=：]\s*\S+", "[凭据已隐藏]", value)
        value = re.sub(r"(?:收件地址|详细地址|地址)\s*[:：]?[^\n。]+", "[地址请在门户填写]", value)
    return value


class ToolContext:
    def __init__(self, settings, factory, adapter, claim):
        self.settings, self.factory, self.adapter, self.claim = settings, factory, adapter, claim
        with factory() as session:
            binding = self._binding(session)
            if binding is None:
                raise AgentStopped("No trusted customer binding")
            self.customer_id = binding.customer_id

    def _binding(self, session):
        return session.scalar(select(ChatBinding).where(
            ChatBinding.instance_id == self.settings.chatwoot_instance_id,
            ChatBinding.account_id == self.settings.chatwoot_account_id,
            ChatBinding.inbox_id == self.settings.chatwoot_inbox_id,
            ChatBinding.contact_id == self.claim["contact_id"],
            ChatBinding.revoked_at.is_(None), ChatBinding.expires_at > utcnow()))

    def guard_local(self, session):
        from .system_api import is_agent_enabled
        if not is_agent_enabled(self.settings, session):
            raise AgentStopped("Agent paused by administrator")
        conv = session.scalar(select(Conversation).where(Conversation.id == self.claim["id"]).with_for_update())
        binding = self._binding(session)
        if (not conv or conv.control_mode != "BOT_ACTIVE" or conv.control_revision != self.claim["control"]
            or conv.input_revision != self.claim["revision"] or conv.fencing_revision != self.claim["fence"]
            or conv.lease_owner != self.claim["owner"] or not conv.lease_until or as_utc(conv.lease_until) <= utcnow()
            or not binding or binding.customer_id != self.customer_id):
            raise AgentStopped("Conversation input/ownership/authorization changed")

    def guard(self):
        remote = self.adapter.get_conversation(self.claim["external_id"])
        meta = remote.get("meta") or {}
        sender = meta.get("sender") or {}
        if (not remote_allows_bot(self.settings, remote) or meta.get("hmac_verified") is not True
                or sender.get("id") != self.claim["contact_id"]
                or sender.get("identifier") != "aftersales:" + self.customer_id):
            raise AgentStopped("Chatwoot ownership unavailable")
        with self.factory.begin() as session:
            self.guard_local(session)

    def enforce_policy_review(self, result):
        """Authoritative policy gaps require a real handoff, not model discretion.

        Call only after closing the tool's database transaction: handoff opens a
        separate transaction to commit silence before remote IO.
        """
        codes = set(result.get("reason_codes", [])) if isinstance(result, dict) else set()
        policy_codes = codes.intersection({"POLICY_NEEDS_REVIEW", "POLICY_MISSING_OR_CONTRADICTORY", "POLICY_RULE_SOURCE_MISSING"})
        if not policy_codes:
            return
        self.guard()
        try:
            handoff_conversation(self.settings, self.factory, self.adapter, self.claim["external_id"],
                                 "policy-review:" + sorted(policy_codes)[0] + ":" + self.customer_id,
                                 expected_revision=self.claim["control"])
        except ControlConflict as exc:
            raise AgentStopped("Ownership changed during policy handoff") from exc
        raise AgentStopped("Authoritative policy gap requires human review")

    def call(self, name, arguments):
        self.guard()
        if name not in TOOLS:
            return self.error("validation", "UNKNOWN_TOOL")
        try:
            data: Any = TOOLS[name][0].model_validate(arguments)
        except ValueError:
            return self.error("validation", "INVALID_TOOL_PARAMETERS")
        try:
            if name == "request_handoff":
                handoff_conversation(self.settings, self.factory, self.adapter, self.claim["external_id"],
                                     "agent-request:" + self.customer_id)
                raise AgentStopped("Agent requested human handoff")
            if name == "search_policy":
                policy_result = search_policy(self.factory, self.settings, self.customer_id, data.order_unit_id, data.question)
                self.enforce_policy_review(policy_result)
                return policy_result
            with self.factory.begin() as session:
                self.guard_local(session)
                result: Any
                if name == "list_my_orders":
                    query = select(Order).where(Order.customer_id == self.customer_id)
                    if data.keyword:
                        matching_item = select(OrderItem.id).join(OrderUnit).join(Product, OrderItem.product_id == Product.id).where(
                            OrderItem.order_id == Order.id, or_(
                            OrderUnit.serial_number.contains(data.keyword, autoescape=True),
                            Product.sku.contains(data.keyword, autoescape=True),
                            Product.name.contains(data.keyword, autoescape=True))).exists()
                        query = query.where(or_(Order.external_id.contains(data.keyword, autoescape=True), matching_item))
                    rows = session.scalars(query.order_by(Order.delivered_at.desc()).limit(data.limit)).all()
                    result = [domain.order_dict(session, row) for row in rows]
                elif name == "get_order_detail":
                    row = session.scalar(select(Order).where(
                        or_(Order.id == data.order_id, Order.external_id == data.order_id),
                        Order.customer_id == self.customer_id))
                    domain.require(row is not None, "RESOURCE_NOT_ACCESSIBLE", 404)
                    result = domain.order_dict(session, row)
                elif name == "evaluate_eligibility":
                    result = domain.evaluate_eligibility(session, self.customer_id, data.order_unit_id, data.type,
                                                        data.facts.model_dump(exclude_none=True))
                elif name == "check_exchange_availability":
                    unit, item, order, product = domain._unit_context(session, data.order_unit_id, self.customer_id)
                    available = list(session.scalars(select(StockUnit.id).where(
                        StockUnit.product_id == product.id, StockUnit.status == "AVAILABLE")))
                    result = {"sku": product.sku, "available": len(available), "reserved": False,
                              "notice": "库存于退回验收后安排；此查询不锁库存"}
                elif name == "prepare_after_sales_proposal":
                    request = data.model_dump(exclude_none=True)
                    key = domain.digest({"conversation": self.claim["id"], "revision": self.claim["revision"], "request": request})
                    result = domain.idempotent(session, self.customer_id, "agent_prepare", key, request,
                        lambda: domain.create_proposal(session, self.customer_id, data.order_unit_id, data.type,
                                                       data.facts.model_dump(exclude_none=True)))
                    from .business_models import Proposal
                    proposal = session.get(Proposal, result["id"])
                    proposal.agent_conversation_id = self.claim["id"]
                    proposal.agent_control_revision = self.claim["control"]
                    portal_origin = self.settings.allowed_origins.split(",")[0].strip().rstrip("/")
                    result = {**result, "preview_url": portal_origin + "/proposals/" + result["id"],
                              "requires_customer_confirmation": True, "inventory_reserved": False}
                elif name == "get_case_status":
                    row = session.scalar(select(AfterSalesCase).where(AfterSalesCase.id == data.case_id,
                                                                      AfterSalesCase.customer_id == self.customer_id))
                    domain.require(row is not None, "RESOURCE_NOT_ACCESSIBLE", 404)
                    result = domain.case_dict(row, session)
                else:
                    return self.error("validation", "UNKNOWN_TOOL")
            self.enforce_policy_review(result)
            result = redact_for_model(result)
            return {"ok": True, "data": result, "reason_codes": result.get("reason_codes", []) if isinstance(result, dict) else [],
                    "missing_fields": result.get("missing_fields", []) if isinstance(result, dict) else [],
                    "source_refs": result.get("source_refs", []) if isinstance(result, dict) else [],
                    "observed_at": utcnow().isoformat(), "error_kind": None}
        except domain.DomainError as exc:
            self.enforce_policy_review(exc.data)
            kind = "permission" if exc.status in {403, 404} else "business_rule" if exc.status == 409 else "validation"
            return self.error(kind, exc.code)
        except EmbeddingUnavailable:
            return self.error("retryable", "POLICY_RETRIEVAL_UNAVAILABLE")

    @staticmethod
    def error(kind, code):
        return {"ok": False, "data": None, "reason_codes": [code], "missing_fields": [],
                "source_refs": [], "observed_at": utcnow().isoformat(), "error_kind": kind}
