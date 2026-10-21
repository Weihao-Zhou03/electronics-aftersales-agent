"""Explicit mock-model tests; real API/model evaluation is recorded separately."""

import json
import os
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from aftersales.agent_models import AgentRun
from aftersales.agent_runtime import run_agent
from aftersales.agent_tools import AgentStopped, ToolContext, tool_definitions
from aftersales.auth import User
from aftersales.config import Settings
from aftersales.db import create_session_factory, init_db, utcnow
from aftersales.model_provider import (
    HTTPModelProvider,
    MockProvider,
    ModelReply,
    ModelUnavailable,
    ToolCall,
)
from aftersales.models import ChatBinding, Conversation, Job
from aftersales.worker import Worker


def test_provider_protocols_preserve_tool_ids_and_no_fallback():
    seen = []
    def transport(request):
        body = json.loads(request.content)
        seen.append(body)
        if request.url.path == "/v1/responses":
            assert body["input"][-1] == {"type": "function_call_output", "call_id": "call-1", "output": "{}"}
            assert body["store"] is False
            return httpx.Response(200, json={"status": "completed", "output": [
                {"type": "function_call", "call_id": "call-2", "name": "list_my_orders", "arguments": "{}"}],
                "usage": {"input_tokens": 20, "output_tokens": 5}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    client = httpx.Client(transport=httpx.MockTransport(transport))
    messages = [{"role": "user", "content": "订单"},
                {"role": "assistant", "tool_calls": [{"id": "call-1", "function": {"name": "list_my_orders", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "call-1", "content": "{}"}]
    provider = HTTPModelProvider("openai-responses", "https://example.test/v1", "synthetic-key", "test-model", client=client)
    result = provider.complete(messages, tool_definitions())
    assert result.calls[0].id == "call-2" and result.usage["output_tokens"] == 5
    assert HTTPModelProvider("chat-completions", "https://example.test/v1", "synthetic-key", "test-model", client=client).complete(messages, []).text == "ok"
    with pytest.raises(ModelUnavailable):
        HTTPModelProvider("openai-responses", "https://example.test/v1", "", "test-model")
    schemas = json.dumps(tool_definitions())
    for forbidden in ["customer_id", "refund_amount", "approval", "sql", "url"]:
        assert forbidden not in schemas


class Adapter:
    def __init__(self, customer_id):
        self.status = "pending"
        self.customer_id = customer_id
    def get_conversation(self, conversation_id):
        return {"id": conversation_id, "account_id": 1, "inbox_id": 1, "status": self.status,
                "meta": {"assignee_type": "AgentBot", "assignee": {"id": 1}, "hmac_verified": True,
                         "sender": {"id": 11, "identifier": "aftersales:" + self.customer_id}}}
    def get_messages(self, conversation_id):
        return {"payload": [{"id": 1, "message_type": 0, "private": False, "content": "请查我的订单"}]}
    def handoff(self, conversation_id):
        self.status = "open"
        return self.get_conversation(conversation_id)


def agent_context(factory=None, settings=None):
    settings = settings or replace(Settings(), chatwoot_bot_id=1, model_provider="mock", model_name="scripted-test")
    factory = factory or create_session_factory("sqlite://")
    init_db(factory)
    with factory.begin() as session:
        user = User(username="agent-test", role="customer", password_hash="test-not-login")
        session.add(user)
        session.flush()
        session.add(ChatBinding(instance_id=settings.chatwoot_instance_id, account_id=1, inbox_id=1,
            contact_id=11, customer_id=user.id, source="explicit-synthetic-test", expires_at=utcnow() + timedelta(minutes=5)))
        conv = Conversation(instance_id=settings.chatwoot_instance_id, account_id=1, inbox_id=1, external_id=5,
                            contact_id=11, control_mode="BOT_ACTIVE", input_revision=1)
        session.add(conv)
        session.flush()
        session.add(Job(conversation_id=conv.id, dedupe_key="test-message-1", payload={"message_id": 1}))
    adapter = Adapter(user.id)
    claim = Worker(settings, factory, adapter)._claim()
    return settings, factory, adapter, claim


def test_graph_tools_are_customer_scoped_and_checkpoints_not_identity_authority():
    settings, factory, adapter, claim = agent_context()
    provider = MockProvider([ModelReply(calls=[ToolCall("first", "list_my_orders", {"customer_id": "victim"})]),
                             ModelReply(calls=[ToolCall("second", "list_my_orders", {})]), ModelReply("没有找到本人的订单，请联系人工核实。")])
    result = run_agent(settings, factory, adapter, claim, provider=provider, test_checkpointer=InMemorySaver())
    assert "本人的订单" in result and provider.calls == 3
    with factory() as session:
        assert session.scalar(select(AgentRun)).status == "SUCCEEDED"


def test_graph_discards_response_after_human_takes_ownership():
    settings, factory, adapter, claim = agent_context()
    class DelayedProvider:
        def complete(self, messages, tools):
            with factory.begin() as session:
                conv = session.get(Conversation, claim["id"])
                conv.control_mode = "HUMAN_ACTIVE"
                conv.control_revision += 1
            return ModelReply(calls=[ToolCall("late", "prepare_after_sales_proposal", {})])
    with pytest.raises(AgentStopped):
        run_agent(settings, factory, adapter, claim, provider=DelayedProvider(), test_checkpointer=InMemorySaver())
    with factory() as session:
        assert session.scalar(select(AgentRun)).status == "STALE"


def test_order_lookup_accepts_display_number_without_crossing_customer_scope():
    from aftersales import domain
    from aftersales.business_models import Product
    from aftersales.demo_seed import import_order, policy_package

    settings, factory, adapter, claim = agent_context()
    context = ToolContext(settings, factory, adapter, claim)
    with factory.begin() as session:
        other = User(username="another-customer", role="customer", password_hash="synthetic")
        product = Product(sku="AGENT-LOOKUP", name="测试耳机", category="headphone")
        session.add_all([other, product])
        session.flush()
        policy = domain.publish_policy(session, policy_package())
        own = import_order(session, customer_id=context.customer_id, external_id="PUBLIC-ORDER-001",
                           product_id=product.id, quantity=1, paid_cents=10000, policy_id=policy["id"],
                           delivered_at=utcnow())
        foreign = import_order(session, customer_id=other.id, external_id="PRIVATE-ORDER-002",
                               product_id=product.id, quantity=1, paid_cents=10000, policy_id=policy["id"],
                               delivered_at=utcnow())
        own_id, foreign_id = own.id, foreign.id
    for key in [own_id, "PUBLIC-ORDER-001"]:
        result = context.call("get_order_detail", {"order_id": key})
        assert result["ok"] and result["data"]["id"] == own_id
    for key in [foreign_id, "PRIVATE-ORDER-002", "UNKNOWN-ORDER"]:
        result = context.call("get_order_detail", {"order_id": key})
        assert not result["ok"] and result["reason_codes"] == ["RESOURCE_NOT_ACCESSIBLE"]


def _check_serial_filter(factory=None):
    from aftersales import domain
    from aftersales.business_models import OrderItem, OrderUnit, Product
    from aftersales.demo_seed import import_order, policy_package

    settings, factory, adapter, claim = agent_context(factory=factory)
    context = ToolContext(settings, factory, adapter, claim)
    with factory.begin() as session:
        other = User(username="filter-foreign", role="customer", password_hash="synthetic")
        product = Product(sku="SERIAL-FILTER", name="检索耳机", category="headphone")
        session.add_all([other, product])
        session.flush()
        policy = domain.publish_policy(session, policy_package())
        old = import_order(session, customer_id=context.customer_id, external_id="OLD-OWNED",
            product_id=product.id, quantity=1, paid_cents=12000, policy_id=policy["id"], delivered_at=utcnow() - timedelta(days=2))
        import_order(session, customer_id=context.customer_id, external_id="NEW-OWNED",
            product_id=product.id, quantity=1, paid_cents=14000, policy_id=policy["id"], delivered_at=utcnow())
        unit = session.scalar(select(OrderUnit).join(OrderItem).where(OrderItem.order_id == old.id))
        serial, unit_id, order_id = unit.serial_number, unit.id, old.id
        import_order(session, customer_id=other.id, external_id=serial, product_id=product.id, quantity=1,
            paid_cents=99999, policy_id=policy["id"], delivered_at=utcnow())
    result = context.call("list_my_orders", {"keyword": serial, "limit": 1})
    assert result["ok"] and [row["id"] for row in result["data"]] == [order_id]
    unavailable = context.call("search_policy", {"order_unit_id": unit_id, "question": "退货政策"})
    assert unavailable["ok"] is False and unavailable["error_kind"] == "retryable"
    assert unavailable["reason_codes"] == ["POLICY_RETRIEVAL_UNAVAILABLE"] and unavailable["source_refs"] == []
    draft = context.call("prepare_after_sales_proposal", {"order_unit_id": unit_id, "type": "return",
        "facts": {"reason": "重复购买", "unused": True, "accessories_complete": True}})
    assert draft["ok"] and draft["data"]["preview_url"] == settings.allowed_origins.split(",")[0].strip().rstrip("/") + "/proposals/" + draft["data"]["id"]
    assert not draft["data"]["preview_url"].startswith(settings.chatwoot_public_url + "/")


def test_serial_filter_precedes_limit_and_retrieval_failure_is_structured():
    _check_serial_filter()


@pytest.mark.postgres
def test_real_postgres_serial_filter_handles_json_orders_and_customer_scope():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required")
    engine = create_engine(url)
    schema = "agent_filter_test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        factory = sessionmaker(bind=engine.execution_options(schema_translate_map={None: schema}), expire_on_commit=False)
        _check_serial_filter(factory)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def _check_policy_gap_handoff(method, factory=None):
    from aftersales import domain
    from aftersales.business_models import (
        AfterSalesCase,
        OrderItem,
        OrderUnit,
        PolicyVersion,
        Product,
        Proposal,
    )
    from aftersales.demo_seed import import_order, policy_package

    settings, factory, adapter, claim = agent_context(factory=factory)
    context = ToolContext(settings, factory, adapter, claim)
    with factory.begin() as session:
        product = Product(sku="POLICY-GAP", name="旧数据耳机", category="headphone")
        session.add(product)
        session.flush()
        policy = domain.publish_policy(session, policy_package())
        order = import_order(session, customer_id=context.customer_id, external_id="POLICY-GAP-ORDER",
            product_id=product.id, quantity=1, paid_cents=12345, policy_id=policy["id"], delivered_at=utcnow())
        unit = session.scalar(select(OrderUnit).join(OrderItem).where(OrderItem.order_id == order.id))
        if method.startswith("mapping"):
            # Explicit corrupt historical input fixture; publication validation
            # rejects this package, but a legacy/imported record must fail closed.
            legacy = PolicyVersion(version="legacy-map-gap", scope=policy["scope"], rules_json=policy["rules"],
                sections=policy["sections"], rule_source_map={}, document_text="legacy missing source mapping",
                content_hash=domain.digest({"legacy": "missing-map"}))
            session.add(legacy)
            session.flush()
            unit.sale_policy_version_id = legacy.id
        else:
            unit.sale_policy_version_id = None
        unit_id = unit.id
    name = "search_policy" if method in {"search", "mapping-search"} else "prepare_after_sales_proposal" if method == "prepare" else "evaluate_eligibility"
    args = {"order_unit_id": unit_id, "question": "退货政策"} if name == "search_policy" else {
        "order_unit_id": unit_id, "type": "return", "facts": {"reason": "买重复", "unused": True, "accessories_complete": True}}
    with pytest.raises(AgentStopped, match="policy gap"):
        context.call(name, args)
    with factory() as session:
        assert session.get(Conversation, claim["id"]).control_mode == "HUMAN_ACTIVE"
        assert session.scalar(select(Proposal.id)) is None
        assert session.scalar(select(AfterSalesCase.id)) is None
        assert all(job.status == "CANCELLED" for job in session.scalars(select(Job).where(Job.kind == "fixed_reply")))
    assert adapter.status == "open"


@pytest.mark.parametrize("method", ["search", "evaluate", "prepare", "mapping", "mapping-search"])
def test_authoritative_policy_gap_forces_handoff_without_model_request(method):
    _check_policy_gap_handoff(method)


@pytest.mark.postgres
@pytest.mark.parametrize("method", ["search", "evaluate", "prepare", "mapping", "mapping-search"])
def test_actual_postgres_policy_handoff_releases_tool_transaction(method):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required")
    engine = create_engine(url, connect_args={"options": "-c lock_timeout=2000 -c statement_timeout=5000"})
    schema = "policy_handoff_test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        factory = sessionmaker(bind=engine.execution_options(schema_translate_map={None: schema}), expire_on_commit=False)
        _check_policy_gap_handoff(method, factory)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
