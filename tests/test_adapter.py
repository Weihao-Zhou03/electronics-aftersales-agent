"""Synthetic HTTP contracts only: httpx.MockTransport never reaches Chatwoot."""

import copy
import json

import httpx
import pytest

from aftersales.chatwoot import (
    ChatwootAdapter,
    ChatwootError,
    ChatwootOwnershipError,
    ChatwootScopeError,
)
from scripts.bootstrap_chatwoot import Bootstrap, BootstrapError


def conversation(**changes):
    value = {
        "id": 7, "account_id": 1, "inbox_id": 2, "status": "pending",
        "meta": {"assignee_type": "AgentBot", "assignee": {"id": 3}, "hmac_verified": True},
    }
    value.update(changes)
    return value


def adapter(handler, bot_id=3):
    return ChatwootAdapter("http://chatwoot.test", 1, 2, "synthetic-runtime-token", bot_id=bot_id,
                          client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_reply_uses_bearer_and_fresh_scoped_get():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic-runtime-token"
        if request.method == "GET":
            return httpx.Response(200, json=conversation())
        assert json.loads(request.content) == {"content": "固定回复", "message_type": "outgoing", "private": False}
        return httpx.Response(200, json={"id": 99})

    assert adapter(handler).send_reply(7, "固定回复") == {"id": 99}
    assert [request.method for request in requests] == ["GET", "POST"]


@pytest.mark.parametrize("changes", [
    {"account_id": 9}, {"inbox_id": 9}, {"id": 99}, {"account_id": None},
])
@pytest.mark.parametrize("operation", ["get_conversation", "get_messages", "send_reply", "append_private_note", "handoff", "return_to_bot"])
def test_every_operation_checks_scope_before_action(changes, operation):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=conversation(**changes))

    args = [7, "test"] if operation in {"send_reply", "append_private_note"} else [7]
    with pytest.raises(ChatwootScopeError):
        getattr(adapter(handler), operation)(*args)
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("changes", [
    {"status": "open"}, {"status": "resolved"}, {"status": "snoozed"}, {"status": None},
    {"meta": None}, {"meta": {"assignee_type": "User", "assignee": {"id": 3}}},
    {"meta": {"assignee_type": "AgentBot", "assignee": {"id": 99}}},
    {"meta": {"assignee_type": "Unknown", "assignee": {"id": 3}}},
    {"meta": {"assignee": {"id": 3}}},
])
def test_public_reply_fails_closed_for_unknown_or_human_state(changes):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=conversation(**changes))

    with pytest.raises(ChatwootOwnershipError):
        adapter(handler).send_reply(7, "must not send")
    assert len(requests) == 1


def test_private_note_is_explicit_and_allowed_during_human_handling():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=conversation(status="open"))
        assert json.loads(request.content)["private"] is True
        return httpx.Response(200, json={"id": 100, "private": True})

    assert adapter(handler).append_private_note(7, "internal")["private"] is True


def test_return_clears_human_using_typed_bot_assignment_and_rechecks():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json=conversation(status="open", meta={"assignee_type": "User", "assignee": {"id": 3}}))
        if request.method == "POST":
            assert request.url.path.endswith("/assignments")
            assert json.loads(request.content) == {"assignee_id": 3, "assignee_type": "AgentBot"}
            return httpx.Response(200, json={"id": 3, "type": "agent_bot"})
        return httpx.Response(200, json=conversation())

    assert adapter(handler).return_to_bot(7)["status"] == "pending"
    assert [request.method for request in requests] == ["GET", "POST", "GET"]


def test_return_does_not_accept_pending_when_human_assignment_remains():
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={})
        return httpx.Response(200, json=conversation(meta={"assignee_type": "User", "assignee": {"id": 3}}))

    with pytest.raises(ChatwootOwnershipError):
        adapter(handler).return_to_bot(7)


def test_bot_message_history_denial_does_not_fall_back_or_expose_body(monkeypatch):
    monkeypatch.setenv("CHATWOOT_ADMIN_ACCESS_TOKEN", "must-not-be-used")
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(401, json={"error": "sensitive-response-content"})
        return httpx.Response(200, json=conversation())

    with pytest.raises(ChatwootError) as error:
        adapter(handler).get_messages(7)
    assert "sensitive" not in str(error.value)
    assert len(requests) == 2
    assert all(request.headers["Authorization"] == "Bearer synthetic-runtime-token" for request in requests)


def test_explicit_support_read_token_is_used_only_for_history_get():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/messages") and request.method == "GET":
            assert request.headers["Authorization"] == "Bearer synthetic-support-reader"
            return httpx.Response(200, json={"payload": []})
        assert request.headers["Authorization"] == "Bearer synthetic-runtime-token"
        return httpx.Response(200, json=conversation() if request.method == "GET" else {"id": 100})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    scoped = ChatwootAdapter("http://chatwoot.test", 1, 2, "synthetic-runtime-token", bot_id=3, client=client,
                             read_access_token="synthetic-support-reader")
    assert scoped.get_messages(7) == {"payload": []}
    scoped.send_reply(7, "fixed")
    assert [request.method for request in requests] == ["GET", "GET", "GET", "POST"]


def synthetic_bootstrap_handler(missing_secret=False):
    """Stateful synthetic server, matching the v4.19.0 Jbuilder envelopes."""
    state = {"bots": [], "inboxes": [], "webhooks": [], "creates": []}

    def handler(request):
        path = request.url.path.split("/accounts/1/")[-1]
        body = json.loads(request.content) if request.content else {}
        if request.method == "POST" and path in {"agent_bots", "inboxes", "webhooks"}:
            state["creates"].append(path)
        if path == "agent_bots":
            if request.method == "GET":
                return httpx.Response(200, json=copy.deepcopy(state["bots"]))
            bot = {**body, "id": 3, "account_id": 1, "access_token": "synthetic-api-token", "secret": "synthetic-bot-secret"}
            state["bots"].append(bot)
            return httpx.Response(200, json=copy.deepcopy(bot))
        if path == "agent_bots/3":
            return httpx.Response(200, json=copy.deepcopy(state["bots"][0]))
        if path == "inboxes":
            if request.method == "GET":
                return httpx.Response(200, json={"payload": copy.deepcopy(state["inboxes"])})
            inbox = {"id": 2, "name": body["name"], "channel_type": "Channel::WebWidget", "enable_auto_assignment": False,
                     "hmac_mandatory": True, "hmac_token": "synthetic-widget-secret", "website_token": "synthetic-widget-token"}
            state["inboxes"].append(inbox)
            return httpx.Response(200, json=copy.deepcopy(inbox))
        if path == "inboxes/2":
            return httpx.Response(200, json=copy.deepcopy(state["inboxes"][0]))
        if path == "inboxes/2/set_agent_bot":
            assert body == {"agent_bot": 3}
            return httpx.Response(200)
        if path == "inboxes/2/agent_bot":
            return httpx.Response(200, json={"agent_bot": {"id": 3}})
        if path == "webhooks" and request.method == "GET":
            return httpx.Response(200, json={"payload": {"webhooks": copy.deepcopy(state["webhooks"])}})
        if path in {"webhooks", "webhooks/4"}:
            webhook = {**body["webhook"], "id": 4, "account_id": 1, "secret": "" if missing_secret else "synthetic-account-secret"}
            state["webhooks"] = [webhook]
            return httpx.Response(200, json={"payload": {"webhook": copy.deepcopy(webhook)}})
        raise AssertionError((request.method, path))

    return state, handler


def test_bootstrap_reuses_objects_and_keeps_secrets_separate():
    state, handler = synthetic_bootstrap_handler()
    bootstrap = Bootstrap("http://chatwoot.test", 1, "synthetic-admin", httpx.Client(transport=httpx.MockTransport(handler)))
    saved, values = bootstrap.provision("http://agent.test", "http://portal.test")
    repeated, values2 = bootstrap.provision("http://agent.test", "http://portal.test", saved)
    assert saved == repeated and values == values2
    assert state["creates"] == ["agent_bots", "inboxes", "webhooks"]
    assert "synthetic-admin" not in values.values()
    assert len({values["CHATWOOT_BOT_WEBHOOK_SECRET"], values["CHATWOOT_ACCOUNT_WEBHOOK_SECRET"], values["CHATWOOT_WIDGET_IDENTITY_SECRET"]}) == 3


def test_bootstrap_missing_real_response_secret_stops():
    _, handler = synthetic_bootstrap_handler(missing_secret=True)
    bootstrap = Bootstrap("http://chatwoot.test", 1, "synthetic-admin", httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(BootstrapError, match="CHATWOOT_ACCOUNT_WEBHOOK_SECRET"):
        bootstrap.provision("http://agent.test", "http://portal.test")
