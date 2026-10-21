"""Synthetic conversation-port boundaries; live UI evidence is recorded separately."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_domain import FACTS, context, now, tx  # noqa: F401 - shared isolated domain fixture

from aftersales import domain
from aftersales.auth import COOKIE_NAME, AuthSession, token_hash
from aftersales.business_models import BusinessJob, Proposal
from aftersales.config import Settings
from aftersales.db import utcnow
from aftersales.main import create_app
from aftersales.models import Conversation
from aftersales.notifications import notify_once


def linked_case(factory, data):
    with factory.begin() as session:
        conversation = Conversation(instance_id="local-chatwoot", account_id=1, inbox_id=1,
                                    external_id=33, contact_id=37, control_mode="HUMAN_ACTIVE")
        session.add(conversation)
        session.flush()
        proposal = domain.create_proposal(session, data["alice"], data["units"][0], "return", FACTS, clock=now)
        session.get(Proposal, proposal["id"]).agent_conversation_id = conversation.id
    return tx(factory, lambda session: domain.confirm_proposal(session, data["alice"], proposal["id"],
                                                              1, proposal["summary_hash"], now))


class ConversationPort:
    def __init__(self, owner, *, lose_response=False):
        self.owner, self.sent, self.lose_response = owner, [], lose_response

    def get_conversation(self, _conversation_id):
        return {"meta": {"hmac_verified": True,
                         "sender": {"id": 37, "identifier": "aftersales:" + self.owner}}}

    def append_private_note(self, conversation_id, content):
        self.sent.append((conversation_id, content))
        if self.lose_response:
            raise TimeoutError("synthetic response loss after note created")
        return {"id": 41, "private": True}

    def get_messages(self, _conversation_id):
        return {"payload": [{"id": 1, "private": False, "message_type": 0, "content": "我的问题"},
                            {"id": 2, "private": False, "message_type": 1, "content": "公开回复"},
                            {"id": 3, "private": True, "message_type": 1, "content": "员工备注"},
                            {"id": 4, "message_type": 1, "content": "缺少private标记"},
                            {"id": 5, "private": False, "message_type": 2, "content": "内部活动"}]}


def test_private_note_loss_is_visible_and_never_reposted(context):  # noqa: F811
    factory, data = context
    case = linked_case(factory, data)
    assert case["conversation_id"] == 33
    adapter = ConversationPort(data["alice"], lose_response=True)
    assert notify_once(Settings(), factory, adapter)
    assert notify_once(Settings(), factory, adapter) is False
    assert len(adapter.sent) == 1 and "/staff/cases/" + case["id"] in adapter.sent[0][1]
    with factory() as session:
        assert session.scalar(select(BusinessJob)).status == "UNKNOWN"
        assert session.scalar(select(Conversation.control_mode)) == "HUMAN_ACTIVE"


def test_case_derived_customer_history_excludes_private_and_other_customer(context):  # noqa: F811
    factory, data = context
    case = linked_case(factory, data)
    adapter = ConversationPort(data["alice"])
    client = TestClient(create_app(Settings(), factory, adapter))
    for key in ["alice", "bob", "staff"]:
        with factory.begin() as session:
            session.add(AuthSession(user_id=data[key], token_hash=token_hash(key), expires_at=utcnow() + timedelta(hours=1)))
    client.cookies.set(COOKIE_NAME, "bob")
    assert client.get("/api/v1/conversations/33").status_code == 404
    client.cookies.set(COOKIE_NAME, "alice")
    response = client.get("/api/v1/conversations/33")
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["messages"]] == [1, 2]
    assert response.json()["linked_cases"][0]["id"] == case["id"]
    adapter.owner = data["bob"]
    assert client.get("/api/v1/conversations/33").status_code == 404
    client.cookies.set(COOKIE_NAME, "staff")
    related = client.get("/api/v1/staff/conversations/33")
    assert related.status_code == 200 and related.json()["linked_cases"][0]["id"] == case["id"]
