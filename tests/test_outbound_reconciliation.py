"""Unknown chat deliveries require staff evidence and an independent explicit return."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from aftersales.config import Settings
from aftersales.control import ControlConflict, return_conversation_to_bot
from aftersales.db import create_session_factory, init_db, utcnow
from aftersales.domain import DomainError
from aftersales.models import Conversation, Job, OutboundMessage, OutboundReconciliation
from aftersales.outbound_api import reconcile_outbound
from aftersales.worker import Worker


@pytest.fixture
def context():
    factory = create_session_factory("sqlite://")
    init_db(factory)
    with factory.begin() as session:
        conversation = Conversation(instance_id="test", account_id=1, inbox_id=2, external_id=3, contact_id=9,
            control_mode="HANDOFF_PENDING", control_revision=2, input_revision=1, fencing_revision=2)
        session.add(conversation)
        session.flush()
        outbound = OutboundMessage(intent_key="test-uncertain", conversation_id=conversation.id, content="待核对的固定回复",
            control_revision=0, input_revision=1, fencing_revision=1, status="UNKNOWN")
        session.add(outbound)
        session.add(Job(conversation_id=conversation.id, dedupe_key="test-message", status="UNKNOWN",
                         payload={"message_id": 10, "input_revision": 1}))
        session.flush()
        ids = conversation.id, outbound.id
    yield factory, Settings(chatwoot_instance_id="test", chatwoot_account_id=1, chatwoot_inbox_id=2,
                            chatwoot_bot_id=7), ids
    factory.kw["bind"].dispose()


class Adapter:
    def __init__(self):
        self.sent = []
        self.remote = {"id": 3, "account_id": 1, "inbox_id": 2, "status": "open",
                       "meta": {"sender": {"id": 9}, "assignee_type": "AgentBot", "assignee": {"id": 7, "type": "agent_bot"}}}
        self.message = {"id": 11, "content": "待核对的固定回复", "message_type": 1, "private": False,
                        "sender": {"id": 7, "type": "agent_bot"}}
        self.new_inputs = []

    def get_conversation(self, conversation_id):
        return self.remote

    def get_messages(self, conversation_id):
        return {"payload": [self.message, *self.new_inputs]}

    def return_to_bot(self, conversation_id):
        self.remote["status"] = "pending"
        return self.remote

    def send_reply(self, conversation_id, content):
        self.sent.append(content)
        return {"id": 20 + len(self.sent)}


PROOF = {"reason": "已在真实客服端逐项核查", "proof": "会话历史与消息编号已核对的演示证据"}


def test_delivered_requires_exact_typed_bot_message_and_never_sends(context):
    factory, settings, (_conv_id, outbound_id) = context
    adapter = Adapter()
    adapter.message["sender"]["type"] = "user"  # Numeric ID collision cannot prove bot origin.
    with pytest.raises(DomainError, match="TYPE_OR_CONTENT_MISMATCH"):
        reconcile_outbound(settings, factory, adapter, outbound_id, 2, "delivered", PROOF, "staff", "proof-key", 11)
    adapter.message["sender"]["type"] = "agent_bot"
    result = reconcile_outbound(settings, factory, adapter, outbound_id, 2, "delivered", PROOF, "staff", "proof-key", 11)
    assert result["status"] == "SENT" and result["control_mode"] == "HANDOFF_PENDING"
    repeated = reconcile_outbound(settings, factory, adapter, outbound_id, 2, "delivered", PROOF, "staff", "proof-key", 11)
    assert repeated == result and adapter.sent == []
    with factory() as session:
        assert len(session.scalars(select(OutboundReconciliation)).all()) == 1
        assert session.scalar(select(Conversation)).last_processed_message_id == 10
        assert session.scalar(select(Job)).status == "ACKNOWLEDGED"


def test_not_delivered_attestation_only_unblocks_later_explicit_return(context):
    factory, settings, (_conv_id, outbound_id) = context
    adapter = Adapter()
    with pytest.raises(ControlConflict, match="reconciliation"):
        return_conversation_to_bot(settings, factory, adapter, 3, "staff", 2)
    with pytest.raises(DomainError, match="PROOF_REQUIRED"):
        reconcile_outbound(settings, factory, adapter, outbound_id, 2, "not_delivered", {}, "staff", "key")
    result = reconcile_outbound(settings, factory, adapter, outbound_id, 2, "not_delivered", PROOF, "staff", "key")
    assert result["status"] == "NOT_DELIVERED" and result["control_mode"] == "HANDOFF_PENDING"
    return_conversation_to_bot(settings, factory, adapter, 3, "staff", 2)
    with factory() as session:
        assert session.scalar(select(Conversation)).control_mode == "BOT_ACTIVE"
        assert session.scalar(select(Job)).status == "NOT_DELIVERED"
        assert session.scalar(select(OutboundMessage)).status == "NOT_DELIVERED"
    assert adapter.sent == []
    # Returning never replays the old intent; a separately persisted NEW input can run.
    with factory.begin() as session:
        conversation = session.scalar(select(Conversation))
        conversation.input_revision = 2
        session.add(Job(conversation_id=conversation.id, dedupe_key="new-customer-message", status="PENDING",
                        payload={"message_id": 12, "input_revision": 2}))
    adapter.new_inputs.append({"id": 12, "message_type": 0, "private": False, "sender": {"id": 9, "type": "contact"}})
    assert Worker(settings, factory, adapter).run_once()
    assert len(adapter.sent) == 1 and adapter.sent[0] != "待核对的固定回复"
    with factory() as session:
        assert session.get(OutboundMessage, outbound_id).status == "NOT_DELIVERED"


def test_reconciliation_rejects_stale_revision_and_active_send(context):
    factory, settings, (_conv_id, outbound_id) = context
    adapter = Adapter()
    with pytest.raises(DomainError, match="REVISION_CONFLICT"):
        reconcile_outbound(settings, factory, adapter, outbound_id, 1, "not_delivered", PROOF, "staff", "key")
    with factory.begin() as session:
        row = session.get(OutboundMessage, outbound_id)
        row.status = "SENDING"
        row.created_at = utcnow()
    with pytest.raises(DomainError, match="IN_FLIGHT"):
        reconcile_outbound(settings, factory, adapter, outbound_id, 2, "not_delivered", PROOF, "staff", "key")
    with factory.begin() as session:
        session.get(OutboundMessage, outbound_id).created_at = utcnow() - timedelta(seconds=settings.worker_lease_seconds + 1)
    reconcile_outbound(settings, factory, adapter, outbound_id, 2, "not_delivered", PROOF, "staff", "key")
    with pytest.raises(ValueError, match="immutable"):
        with factory.begin() as session:
            session.scalar(select(OutboundReconciliation)).outcome = "delivered"
