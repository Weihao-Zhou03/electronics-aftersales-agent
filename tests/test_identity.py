"""Synthetic HTTP/SQLite boundary tests; not Chatwoot Widget integration evidence."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aftersales.auth import User, seed_demo
from aftersales.config import Settings
from aftersales.db import create_session_factory, init_db
from aftersales.main import create_app
from aftersales.models import ChatBinding

PASSWORD = "synthetic-test-password-only"
ORIGIN = {"Origin": "http://localhost:8000"}


class FakeIdentityAdapter:
    remote = {}

    def get_conversation(self, conversation_id):
        return self.remote


@pytest.fixture
def context():
    settings = replace(Settings(), session_secret="s" * 48,
                       chatwoot_widget_identity_secret="widget-test-only",
                       chatwoot_widget_token="test-website-token")
    factory = create_session_factory("sqlite://")
    init_db(factory)
    seed_demo(factory, PASSWORD)
    adapter = FakeIdentityAdapter()
    client = TestClient(create_app(settings, factory, adapter))
    return client, factory, adapter


def login(client, username):
    response = client.post("/api/v1/auth/login", headers=ORIGIN,
                           json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return {**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def test_current_identity_cannot_be_replaced_and_hmac_must_be_api_verified(context):
    client, factory, adapter = context
    headers = login(client, "customer-a")
    mine = client.get("/api/v1/chat/identity").json()
    assert client.get("/api/v1/chat/identity?identifier=someone-else").status_code == 422
    with factory() as db:
        other = db.scalar(select(User).where(User.username == "customer-b"))
        other_id = other.id
    adapter.remote = {"account_id": 1, "inbox_id": 1,
                      "meta": {"hmac_verified": True, "sender": {"id": 7, "identifier": f"aftersales:{other_id}"}}}
    assert client.post("/api/v1/chat/bind", headers=headers, json={"conversation_id": 1}).status_code == 403
    adapter.remote["meta"]["sender"]["identifier"] = mine["identifier"]
    adapter.remote["meta"]["hmac_verified"] = False
    assert client.post("/api/v1/chat/bind", headers=headers, json={"conversation_id": 1}).status_code == 403
    adapter.remote["meta"]["hmac_verified"] = True
    assert client.post("/api/v1/chat/bind", headers=headers, json={"conversation_id": 1}).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers, json={}).status_code == 200
    with factory() as db:
        assert db.scalar(select(ChatBinding)).revoked_at is not None
    assert client.get("/api/v1/chat/identity").status_code == 401


def test_csrf_role_and_account_switch_revoke_old_binding(context):
    client, factory, adapter = context
    headers = login(client, "customer-a")
    mine = client.get("/api/v1/chat/identity").json()
    adapter.remote = {"account_id": 1, "inbox_id": 1,
                      "meta": {"hmac_verified": True, "sender": {"id": 8, "identifier": mine["identifier"]}}}
    assert client.post("/api/v1/chat/bind", headers=ORIGIN, json={"conversation_id": 1}).status_code == 403
    assert client.post("/api/v1/chat/bind", headers=headers, json={"conversation_id": 1}).status_code == 200
    assert client.post("/api/v1/staff/conversations/1/takeover", headers=headers,
                       json={"expected_revision": 0}).status_code == 403
    login(client, "customer-b")
    with factory() as db:
        assert db.scalar(select(ChatBinding)).revoked_at is not None
    assert client.get("/api/v1/chat/identity").json()["identifier"] != mine["identifier"]
