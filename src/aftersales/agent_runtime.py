"""One LangGraph agent; durable checkpoints never supply identity or approval."""

import json
import sys
import time
from pathlib import Path
from typing import TypedDict

import psycopg
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy import func, select

from .agent_models import AgentEvent, AgentRun
from .agent_tools import AgentStopped, ToolContext, redact_for_model, tool_definitions
from .config import Settings
from .db import create_session_factory, utcnow
from .model_provider import ModelUnavailable, configured_provider

PROMPT_VERSION = "aftersales-v1"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "aftersales-v1.txt"
FALLBACK_TEXT = "演示环境 · 模拟订单与履约。模型服务暂不可用，请在门户查看草稿、使用表单或联系人工客服；本轮未提交新的正式申请，已有进度以门户为准。"


class State(TypedDict, total=False):
    messages: list[dict]
    model_calls: int
    tool_calls: int
    calls: list[dict]
    reply: str
    usage: dict


def build_graph(context, provider, checkpointer, audit):
    settings = context.settings
    started = time.monotonic()

    def guard():
        if time.monotonic() - started > settings.agent_turn_timeout_seconds:
            raise ModelUnavailable("Agent turn time budget exceeded")
        context.guard()

    def decide(state):
        guard()
        if state["model_calls"] >= settings.agent_max_model_calls:
            return {"reply": "本轮查询已达到上限，请在门户继续或联系人工客服。", "calls": []}
        reply = provider.complete(state["messages"], tool_definitions())
        guard()
        usage = dict(state["usage"])
        for key, value in reply.usage.items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0) + value
        audit("model", None, {"usage": reply.usage, "call_count": state["model_calls"] + 1})
        return {"messages": state["messages"] + [reply.as_message()],
                "model_calls": state["model_calls"] + 1, "usage": usage,
                "calls": [vars(call) for call in reply.calls], "reply": reply.text}

    def execute(state):
        messages, count = list(state["messages"]), state["tool_calls"]
        for call in state["calls"]:
            guard()
            if count >= settings.agent_max_tool_calls:
                result = ToolContext.error("validation", "TOOL_BUDGET_EXCEEDED")
            else:
                result = context.call(call["name"], call["arguments"])
                count += 1
            # Store structured result IDs/reasons/citations, never user addresses or raw arguments.
            audit("tool", call["name"], {"ok": result.get("ok"), "reason_codes": result.get("reason_codes", []),
                                         "source_refs": result.get("source_refs", [])})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False)})
        return {"messages": messages, "tool_calls": count, "calls": []}

    graph = StateGraph(State)
    graph.add_node("decide", decide)
    graph.add_node("execute_tools", execute)
    graph.add_edge(START, "decide")
    graph.add_conditional_edges("decide", lambda state: "tools" if state.get("calls") else "finish",
                                {"tools": "execute_tools", "finish": END})
    graph.add_edge("execute_tools", "decide")
    return graph.compile(checkpointer=checkpointer)


def _graph_uri(settings):
    uri = settings.graph_database_url or settings.database_url
    if not uri.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ModelUnavailable("Production checkpoints require PostgreSQL")
    return uri.replace("postgresql+psycopg://", "postgresql://", 1)


def setup_checkpoints(settings):
    with psycopg.connect(_graph_uri(settings), autocommit=True, prepare_threshold=0,
                         row_factory=dict_row) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(settings.graph_checkpoint_schema)))
        connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(settings.graph_checkpoint_schema)))
        PostgresSaver(connection).setup()
    return {"schema": settings.graph_checkpoint_schema, "implementation": "langgraph-checkpoint-postgres"}


def run_agent(settings, factory, adapter, claim, *, provider=None, test_checkpointer=None):
    context = ToolContext(settings, factory, adapter, claim)
    context.guard()
    with factory.begin() as session:
        from .auth import User
        user = session.scalar(select(User).where(User.id == context.customer_id,
            User.active.is_(True), User.role == "customer").with_for_update())
        if user is None:
            raise AgentStopped("Customer authorization inactive")
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        count = session.scalar(select(func.count()).select_from(AgentRun).where(
            AgentRun.customer_id == context.customer_id, AgentRun.created_at >= today))
        if count >= settings.agent_daily_run_limit:
            raise ModelUnavailable("Conversation daily model run limit exceeded")
        run = AgentRun(conversation_id=claim["id"], customer_id=context.customer_id, provider=settings.model_provider,
                       model=settings.model_name, prompt_version=PROMPT_VERSION)
        session.add(run)
        session.flush()
        run_id = run.id
    started = time.monotonic()

    def audit(kind, name, result):
        with factory.begin() as session:
            session.add(AgentEvent(run_id=run_id, kind=kind, tool_name=name, result=result))

    try:
        history = adapter.get_messages(claim["external_id"])["payload"]
        history = sorted(history, key=lambda message: message["id"])
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        messages = [{"role": "system", "content": prompt}]
        incoming_ids = set(claim["message_ids"])
        seen = set()
        for message in history:
            if message.get("private") is not False:
                continue
            kind = message.get("message_type")
            if kind in {0, "incoming"}:
                role = "user"
                seen.add(message["id"])
            elif kind in {1, "outgoing"}:
                role = "assistant"
            else:
                continue
            content = redact_for_model(message.get("content") or "")
            if content:
                messages.append({"role": role, "content": content})
        if not incoming_ids.issubset(seen):
            raise AgentStopped("Some pending messages are absent from the authoritative history")
        if len(json.dumps(messages, ensure_ascii=False)) > 60000:
            raise ModelUnavailable("Conversation exceeds the configured context limit; manual assistance required")
        initial = {"messages": messages, "model_calls": 0, "tool_calls": 0, "calls": [], "reply": "", "usage": {}}
        selected = provider or configured_provider(settings)
        if test_checkpointer is not None:
            if not isinstance(test_checkpointer, InMemorySaver):
                raise ValueError("Only explicit synthetic tests may use in-memory checkpoints")
            result = build_graph(context, selected, test_checkpointer, audit).invoke(
                initial, {"configurable": {"thread_id": claim["id"]}, "recursion_limit": 30})
        else:
            with psycopg.connect(_graph_uri(settings), autocommit=True, prepare_threshold=0,
                                 row_factory=dict_row, options=f"-c search_path={settings.graph_checkpoint_schema},public") as connection:
                result = build_graph(context, selected, PostgresSaver(connection), audit).invoke(
                    initial, {"configurable": {"thread_id": claim["id"]}, "recursion_limit": 30})
        context.guard()
        output = result.get("reply") or "请在已登录门户核对设备和申请预览，确认后再提交审核。"
        with factory.begin() as session:
            row = session.get(AgentRun, run_id)
            row.status, row.usage = "SUCCEEDED", result["usage"]
            row.duration_ms = int((time.monotonic() - started) * 1000)
        return output[:10000]
    except Exception as exc:
        with factory.begin() as session:
            row = session.get(AgentRun, run_id)
            row.status = "STALE" if isinstance(exc, AgentStopped) else "FAILED"
            row.error_kind = type(exc).__name__
            row.duration_ms = int((time.monotonic() - started) * 1000)
        raise


def main():
    settings = Settings.from_env()
    if sys.argv[1:] == ["setup"]:
        print(json.dumps(setup_checkpoints(settings)))
    elif sys.argv[1:] == ["index-policies"]:
        from .retrieval import index_policies
        print(json.dumps(index_policies(create_session_factory(settings.database_url), settings)))
    else:
        raise SystemExit("usage: python -m aftersales.agent_runtime setup | index-policies")


if __name__ == "__main__":
    main()
