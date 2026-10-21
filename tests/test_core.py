"""Synthetic M0 contract/fault tests. FakeAdapter is never real integration evidence."""
import hashlib
import hmac
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import sessionmaker

from aftersales.config import Settings
from aftersales.control import handoff_conversation, return_conversation_to_bot
from aftersales.db import create_session_factory, init_db, utcnow
from aftersales.models import ChatBinding, Conversation, Job, OutboundMessage, WebhookEvent
from aftersales.webhooks import ingest_event, verify_signature
from aftersales.worker import PUBLIC_GUIDANCE, Worker


@pytest.fixture
def settings():
    return Settings(database_url="sqlite://", chatwoot_bot_id=77, chatwoot_bot_access_token="synthetic-api-token",
                    chatwoot_bot_webhook_secret="synthetic-bot-signing", chatwoot_account_webhook_secret="synthetic-control-signing",
                    chatwoot_widget_identity_secret="synthetic-widget-signing")


@pytest.fixture
def factory():
    value = create_session_factory("sqlite://")
    init_db(value)
    yield value
    value.kw["bind"].dispose()


def message(message_id=1, **changes):
    # Shape derived from v4.19.0 Message#webhook_data + Contact#webhook_data;
    # values/content are synthetic, not captured real traffic.
    value = {"event": "message_created", "id": message_id, "account": {"id": 1}, "inbox": {"id": 1},
             "conversation": {"id": 8, "inbox_id": 1, "status": "pending"},
             "message_type": "incoming", "private": False,
             "sender": {"id": 9, "contact_type": "customer"}, "content": "synthetic private text"}
    value.update(changes)
    return value


def signed(settings, payload, channel="agentbot", timestamp=None):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    timestamp = timestamp or str(int(time.time()))
    secret = settings.chatwoot_bot_webhook_secret if channel == "agentbot" else settings.chatwoot_account_webhook_secret
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return raw, {"X-Chatwoot-Timestamp": timestamp, "X-Chatwoot-Signature": "sha256=" + digest}


def ingest(settings, factory, payload=None, channel="agentbot"):
    raw, headers = signed(settings, payload or message(), channel)
    return ingest_event(settings, factory, channel, raw, headers)


class FakeAdapter:
    def __init__(self):
        self.remote = {"id": 8, "account_id": 1, "inbox_id": 1, "status": "pending",
                       "meta": {"sender": {"id": 9}, "assignee_type": "AgentBot", "assignee": {"id": 77, "type": "agent_bot"}}}
        self.messages = [message()]
        self.sent = []
        self.fail_send = False
        self.fail_read = False
        self.on_handoff = None

    def get_conversation(self, conversation_id):
        return self.remote

    def get_messages(self, conversation_id):
        if self.fail_read:
            raise PermissionError("synthetic history permission failure")
        return {"payload": self.messages}

    def send_reply(self, conversation_id, content):
        self.sent.append((conversation_id, content))
        if self.fail_send:
            raise TimeoutError("synthetic lost response after successful POST")
        return {"id": 1000 + len(self.sent)}

    def handoff(self, conversation_id):
        if self.on_handoff:
            self.on_handoff()
        self.remote["status"] = "open"
        return self.remote

    def return_to_bot(self, conversation_id):
        self.remote["status"] = "pending"
        self.remote["meta"]["assignee_type"] = "AgentBot"
        self.remote["meta"]["assignee"] = {"id": 77, "type": "agent_bot"}
        return self.remote


def count(factory, model):
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def state(factory):
    with factory() as session:
        return session.scalar(select(Conversation))


def ready_all(factory):
    with factory.begin() as session:
        for job in session.scalars(select(Job)):
            job.available_at = utcnow() - timedelta(seconds=1)


def test_raw_signature_preserves_timestamp_and_body(settings):
    timestamp = "0" + str(int(time.time()))
    raw, headers = signed(settings, message(), timestamp=timestamp)
    verify_signature(settings, "agentbot", raw, {key.lower(): value for key, value in headers.items()})
    with pytest.raises(HTTPException, match="invalid signature"):
        verify_signature(settings, "agentbot", raw + b" ", {key.lower(): value for key, value in headers.items()})


@pytest.mark.parametrize("change", ["missing", "wrong-secret", "expired", "invalid", "other-channel"])
def test_signature_rejects_invalid_inputs(settings, change):
    raw, headers = signed(settings, message())
    headers = {key.lower(): value for key, value in headers.items()}
    channel = "agentbot"
    if change == "missing":
        headers.pop("x-chatwoot-signature")
    elif change == "wrong-secret":
        settings = replace(settings, chatwoot_bot_webhook_secret="wrong")
    elif change == "expired":
        raw, headers = signed(settings, message(), timestamp=str(int(time.time()) - 301))
        headers = {key.lower(): value for key, value in headers.items()}
    elif change == "invalid":
        headers["x-chatwoot-timestamp"] = "nan"
    else:
        channel = "control"
    with pytest.raises(HTTPException) as caught:
        verify_signature(settings, channel, raw, headers)
    assert caught.value.status_code == 401


def test_distinct_secrets_are_required(settings):
    settings = replace(settings, chatwoot_account_webhook_secret=settings.chatwoot_bot_webhook_secret)
    raw, headers = signed(settings, message())
    with pytest.raises(HTTPException) as caught:
        verify_signature(settings, "agentbot", raw, headers)
    assert caught.value.status_code == 503


def test_dedupe_without_delivery_and_redaction(settings, factory):
    assert ingest(settings, factory)["queued"]
    assert ingest(settings, factory)["duplicate"]
    assert count(factory, Job) == count(factory, WebhookEvent) == 1
    with factory() as session:
        stored = session.scalar(select(WebhookEvent))
        assert "synthetic private text" not in json.dumps(stored.payload)
        assert state(factory).input_revision == 1


def test_event_job_failure_rolls_back_ack_state(settings, factory):
    def fail_job_insert(session, _context, _instances):
        if any(isinstance(value, Job) for value in session.new):
            raise RuntimeError("synthetic transaction failure")
    event.listen(factory, "before_flush", fail_job_insert)
    try:
        with pytest.raises(RuntimeError):
            ingest(settings, factory)
    finally:
        event.remove(factory, "before_flush", fail_job_insert)
    assert count(factory, WebhookEvent) == count(factory, Job) == count(factory, Conversation) == 0


@pytest.mark.parametrize("field", ["account", "inbox"])
def test_scope_rejected_before_database_write(settings, factory, field):
    payload = message(**{field: {"id": 2}})
    with pytest.raises(HTTPException) as caught:
        ingest(settings, factory, payload)
    assert caught.value.status_code == 403
    assert count(factory, WebhookEvent) == 0


@pytest.mark.parametrize("changes", [
    {"message_type": "outgoing", "sender": {"id": 77, "type": "agent_bot"}},
    {"private": True}, {"event": "message_updated"},
])
def test_outgoing_private_updated_do_not_start_loop(settings, factory, changes):
    assert not ingest(settings, factory, message(**changes))["queued"]
    assert count(factory, Job) == 0


def test_human_type_wins_even_if_numeric_id_matches_bot(settings, factory):
    ingest(settings, factory)
    ingest(settings, factory, message(2, message_type="outgoing", sender={"id": 77, "type": "user"}))
    assert state(factory).control_mode == "HUMAN_ACTIVE"
    late_pending = {"event": "conversation_status_changed", "id": 8, "account": {"id": 1},
                    "inbox_id": 1, "status": "pending", "meta": {"assignee_type": "AgentBot", "assignee": {"id": 77}}}
    ingest(settings, factory, late_pending, "control")
    assert state(factory).control_mode == "HUMAN_ACTIVE"
    with factory() as session:
        assert session.scalar(select(Job)).status == "CANCELLED"


def test_bound_fixed_reply_and_unbound_public_guidance(settings, factory):
    ingest(settings, factory)
    adapter = FakeAdapter()
    Worker(settings, factory, adapter).run_once()
    assert adapter.sent[0][1] == PUBLIC_GUIDANCE
    ingest(settings, factory, message(2))
    adapter.messages.append(message(2))
    with factory.begin() as session:
        session.add(ChatBinding(instance_id=settings.chatwoot_instance_id, account_id=1, inbox_id=1, contact_id=9,
                                customer_id="synthetic-customer", source="synthetic-test-only",
                                expires_at=utcnow() + timedelta(minutes=5)))
    Worker(settings, factory, adapter).run_once()
    assert adapter.sent[1][1] == settings.fixed_reply_text
    assert state(factory).last_processed_message_id == 2
    with factory() as session:
        assert {row.status for row in session.scalars(select(OutboundMessage))} == {"SENT"}


def test_handoff_commits_before_sync_and_blocks_delayed_result(settings, factory):
    ingest(settings, factory)
    settings = replace(settings, fixed_reply_delay_seconds=1)
    adapter = FakeAdapter()
    adapter.on_handoff = lambda: state(factory).control_mode == "HANDOFF_PENDING" or pytest.fail("handoff not committed")
    invoked = False
    def takeover(_pause):
        nonlocal invoked
        if not invoked:
            invoked = True
            handoff_conversation(settings, factory, adapter, 8, "synthetic-staff", expected_revision=0)
    Worker(settings, factory, adapter, sleep=takeover).run_once()
    assert adapter.sent == []
    assert state(factory).control_mode == "HUMAN_ACTIVE"
    return_conversation_to_bot(settings, factory, adapter, 8, "synthetic-staff",
                               expected_revision=state(factory).control_revision)
    assert state(factory).control_mode == "BOT_ACTIVE"
    # Explicit return does not revive the old cancelled input.
    assert not Worker(settings, factory, adapter).run_once()
    ingest(settings, factory, message(2))
    adapter.messages.append(message(2))
    Worker(replace(settings, fixed_reply_delay_seconds=0), factory, adapter).run_once()
    assert len(adapter.sent) == 1


def test_new_message_invalidates_result_without_dropping_previous_batch(settings, factory):
    ingest(settings, factory)
    adapter = FakeAdapter()
    def new_input(_pause):
        ingest(settings, factory, message(2))
        adapter.messages.append(message(2))
    Worker(replace(settings, fixed_reply_delay_seconds=1), factory, adapter, sleep=new_input).run_once()
    assert adapter.sent == []
    ready_all(factory)
    Worker(settings, factory, adapter).run_once()
    assert len(adapter.sent) == 1
    assert state(factory).last_processed_message_id == 2
    with factory() as session:
        assert {job.status for job in session.scalars(select(Job))} == {"SUCCEEDED"}


def test_lease_fences_expired_worker(settings, factory):
    ingest(settings, factory)
    first, second = Worker(settings, factory, FakeAdapter()), Worker(settings, factory, FakeAdapter())
    old = first._claim()
    assert second._claim() is None
    with factory.begin() as session:
        conversation = session.get(Conversation, old["id"])
        conversation.lease_until = utcnow() - timedelta(seconds=1)
        for job in session.scalars(select(Job)):
            job.lease_until = conversation.lease_until
    new = second._claim()
    assert new["fence"] > old["fence"]
    assert first._prepare(old) is None
    assert second._prepare(new) is not None


def test_unknown_post_is_persisted_and_never_automatically_reposted(settings, factory):
    ingest(settings, factory)
    adapter = FakeAdapter()
    adapter.fail_send = True
    Worker(settings, factory, adapter).run_once()
    assert len(adapter.sent) == 1
    assert state(factory).control_mode == "HANDOFF_PENDING"
    assert not Worker(settings, factory, adapter).run_once()
    with factory() as session:
        assert session.scalar(select(OutboundMessage)).status == "UNKNOWN"
        assert session.scalar(select(Job)).status == "UNKNOWN"


def test_crash_after_marking_sending_never_reposts(settings, factory):
    ingest(settings, factory)
    adapter = FakeAdapter()
    worker = Worker(settings, factory, adapter)
    claim = worker._claim()
    out = worker._prepare(claim)
    with factory.begin() as session:
        session.get(OutboundMessage, out["id"]).status = "SENDING"
        conversation = session.get(Conversation, claim["id"])
        conversation.lease_until = utcnow() - timedelta(seconds=1)
        for job in session.scalars(select(Job)):
            job.lease_until = conversation.lease_until
    worker.run_once()
    assert adapter.sent == []
    assert state(factory).control_mode == "HANDOFF_PENDING"
    with factory() as session:
        assert session.scalar(select(OutboundMessage)).status == "UNKNOWN"


def test_history_permission_failure_is_bounded_and_silent(settings, factory):
    ingest(settings, factory)
    adapter = FakeAdapter()
    adapter.fail_read = True
    worker = Worker(replace(settings, worker_max_attempts=2), factory, adapter)
    worker.run_once()
    ready_all(factory)
    worker.run_once()
    assert adapter.sent == []
    assert state(factory).control_mode == "HANDOFF_PENDING"
    with factory() as session:
        assert session.scalar(select(Job)).status == "FAILED"


def test_remote_human_reply_blocks_without_waiting_for_webhook(settings, factory):
    ingest(settings, factory)
    adapter = FakeAdapter()
    adapter.messages.append(message(2, message_type="outgoing", sender={"id": 77, "type": "user"}))
    Worker(settings, factory, adapter).run_once()
    assert adapter.sent == []
    assert state(factory).control_mode == "HUMAN_ACTIVE"


@pytest.mark.postgres
def test_real_postgres_concurrent_ingress_and_lease_fencing(settings):
    """Real PostgreSQL isolation/locks; Chatwoot remains an explicit synthetic adapter."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for real PostgreSQL test")
    engine = create_engine(url)
    if engine.dialect.name != "postgresql":
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL, not a substitute")
    schema = "m0_test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    pg_factory = sessionmaker(bind=scoped, expire_on_commit=False)
    try:
        init_db(pg_factory)
        ingest(settings, pg_factory)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: ingest(settings, pg_factory, message(2)), range(12)))
        assert sum(not result["duplicate"] for result in results) == 1
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda number: ingest(settings, pg_factory, message(number)), [3, 4]))
        assert count(pg_factory, Job) == count(pg_factory, WebhookEvent) == 4
        assert state(pg_factory).input_revision == 4
        first = Worker(settings, pg_factory, FakeAdapter())
        second = Worker(settings, pg_factory, FakeAdapter())
        old = first._claim()
        assert second._claim() is None
        with pg_factory.begin() as session:
            conversation = session.get(Conversation, old["id"])
            conversation.lease_until = utcnow() - timedelta(seconds=1)
            for job in session.scalars(select(Job)):
                job.lease_until = conversation.lease_until
        new = second._claim()
        assert new["fence"] > old["fence"]
        assert first._prepare(old) is None
        assert second._prepare(new) is not None
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
