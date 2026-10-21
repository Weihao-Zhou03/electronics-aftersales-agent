"""Real PostgreSQL checkpoints/vector storage with explicitly selected test models."""

import os
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from test_agent import agent_context

from aftersales.agent_runtime import run_agent, setup_checkpoints
from aftersales.auth import seed_demo
from aftersales.business_models import Order, OrderItem, OrderUnit
from aftersales.config import Settings
from aftersales.demo_seed import seed_business
from aftersales.model_provider import MockProvider, ModelReply, ToolCall
from aftersales.retrieval import EmbeddingUnavailable, index_policies, search_policy


@pytest.fixture
def pg_factory():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required; no SQLite substitution")
    engine = create_engine(url)
    assert engine.dialect.name == "postgresql"
    schema = "agent_test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    factory = sessionmaker(bind=engine.execution_options(schema_translate_map={None: schema}), expire_on_commit=False)
    try:
        yield factory, url
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.postgres
def test_actual_postgres_checkpointer_survives_connection_restart(pg_factory):
    factory, url = pg_factory
    settings = replace(Settings(), chatwoot_bot_id=1, model_provider="mock", model_name="scripted-test", graph_database_url=url)
    settings, factory, adapter, claim = agent_context(factory, settings)
    setup_checkpoints(settings)
    provider = MockProvider([ModelReply(calls=[ToolCall("read", "list_my_orders", {})]), ModelReply("查询已完成；没有本人订单。")])
    assert "本人订单" in run_agent(settings, factory, adapter, claim, provider=provider)
    # A new DB connection reads the checkpoint; this is not an in-memory persistence claim.
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"), autocommit=True,
                         row_factory=dict_row, options="-c search_path=agent_checkpoints,public") as connection:
        saver = PostgresSaver(connection)
        checkpoint = saver.get_tuple({"configurable": {"thread_id": claim["id"]}})
        assert checkpoint.checkpoint["channel_values"]["reply"].endswith("本人订单。")
        saver.delete_thread(claim["id"])


@pytest.mark.postgres
@pytest.mark.parametrize("actual_local_model", [False, True])
def test_version_scoped_pgvector_and_embedding_identity(pg_factory, actual_local_model):
    if actual_local_model and os.getenv("RUN_LOCAL_EMBEDDING_TESTS") != "1":
        pytest.skip("Set RUN_LOCAL_EMBEDDING_TESTS=1 to run the actual local Chinese embedding model")
    factory, _url = pg_factory
    from aftersales.db import init_db
    init_db(factory)
    seed_demo(factory, "synthetic-password-123")
    seed_business(factory)
    settings = replace(Settings(), embedding_provider="fastembed" if actual_local_model else "mock",
                       embedding_model="BAAI/bge-small-zh-v1.5" if actual_local_model else "explicit-test-hash",
                       embedding_dimension=512 if actual_local_model else 8)
    indexed = index_policies(factory, settings)
    assert indexed["chunks_indexed"] >= 4
    with factory() as session:
        unit, order = session.execute(select(OrderUnit, Order).join(OrderItem, OrderUnit.order_item_id == OrderItem.id)
                                      .join(Order, OrderItem.order_id == Order.id)).first()
        unit_id, customer_id = unit.id, order.customer_id
    result = search_policy(factory, settings, customer_id, unit_id, "耳机质量问题换货窗口多久？")
    assert result["ok"] and result["source_refs"]
    assert {ref["version"] for ref in result["source_refs"]} == {"demo_policy_v1"}
    with pytest.raises(EmbeddingUnavailable, match="identity|dimension"):
        search_policy(factory, replace(settings, embedding_model="wrong-model"), customer_id, unit_id, "退货")
