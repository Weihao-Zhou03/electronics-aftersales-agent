"""Real Chatwoot + Sidekiq + PostgreSQL control/ingress fault verification.

Creates a fresh real Widget HTTP session and sends through its actual API. This
is real integration evidence; browser Widget visibility is tested separately.
Run with the ordinary chat worker stopped; this process owns delayed fixed work.
Secrets are read locally and never included in reports or fixtures.
"""
import hashlib
import hmac
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.engine import URL

from aftersales.chatwoot import ChatwootAdapter
from aftersales.config import Settings
from aftersales.db import create_session_factory
from aftersales.models import Conversation, Job, OutboundMessage, WebhookEvent
from aftersales.worker import Worker


def wait_for(action, *, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = action()
        if result:
            return result
        time.sleep(0.15)
    raise AssertionError("actual integration did not reach its expected state before timeout")


def expect_http(response, expected=200):
    if response.status_code != expected:
        raise AssertionError(f"actual HTTP status {response.status_code}; expected {expected}")
    return response.json() if response.content else {}


def main():
    load_dotenv(".env")
    load_dotenv(".env.chatwoot", override=True)
    values = Settings.from_env()
    settings = replace(values, database_url=URL.create("postgresql+psycopg", username="aftersales",
        password=os.environ["AFTERSALES_DB_PASSWORD"], host="127.0.0.1", port=15432, database="aftersales",
        query={"connect_timeout": "5"}).render_as_string(hide_password=False),
        chatwoot_base_url="http://127.0.0.1:3000", fixed_reply_delay_seconds=2.0, agent_reply_mode="fixed")
    factory = create_session_factory(settings.database_url)
    adapter = ChatwootAdapter(settings.chatwoot_base_url, settings.chatwoot_account_id,
        settings.chatwoot_inbox_id, settings.chatwoot_bot_access_token, bot_id=settings.chatwoot_bot_id,
        read_access_token=settings.chatwoot_read_access_token)
    base = f"{settings.chatwoot_base_url}/api/v1/accounts/{settings.chatwoot_account_id}"
    report = {"source": "real Chatwoot CE v4.19.0 HTTP APIs, actual Sidekiq callbacks, PostgreSQL16.12; fixed reply mode",
        "message_origin": "actual Widget HTTP API with a fresh rendered Widget session; browser UI tested separately",
        "captured_at": datetime.now(UTC).isoformat(), "checks": {}}
    with httpx.Client(headers={"Authorization": "Bearer " + settings.chatwoot_read_access_token}, timeout=15) as cw, \
            httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as api:
        widget = cw.get(settings.chatwoot_base_url + "/widget", params={"website_token": settings.chatwoot_widget_token},
                        headers={"User-Agent": "Mozilla/5.0 Chrome/130.0"})
        widget.raise_for_status()
        match = re.search(r"window\.authToken = '([^']+)'", widget.text)
        assert match, "the real rendered Widget did not issue its session token"
        widget_headers = {"X-Auth-Token": match.group(1)}
        widget_url = settings.chatwoot_base_url + "/api/v1/widget/messages"
        widget_params = {"website_token": settings.chatwoot_widget_token}
        origin = settings.allowed_origins.split(",")[0]
        login = expect_http(api.post("/api/v1/auth/login", json={"username": "support", "password": os.environ["M0_DEMO_PASSWORD"]}, headers={"Origin": origin}))
        staff_headers = {"Origin": origin, "X-CSRF-Token": login["csrf_token"]}

        first = expect_http(cw.post(widget_url, params=widget_params, headers=widget_headers,
            json={"message": {"content": "M0 real delayed task before takeover " + uuid4().hex}}))
        external_id = first["conversation_id"]
        report["conversation_id"] = external_id
        path = base + f"/conversations/{external_id}"

        def local():
            with factory() as session:
                return session.scalar(select(Conversation).where(Conversation.external_id == external_id,
                    Conversation.instance_id == settings.chatwoot_instance_id, Conversation.account_id == settings.chatwoot_account_id))

        wait_for(lambda: local() if local() and local().input_revision >= 1 else None)
        worker = Worker(settings, factory, adapter)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker.run_once)
            wait_for(lambda: local().lease_owner)
            current = expect_http(api.get(f"/api/v1/staff/conversations/{external_id}"))
            takeover = expect_http(api.post(f"/api/v1/staff/conversations/{external_id}/takeover",
                json={"expected_revision": current["control_revision"]}, headers=staff_headers))
            assert takeover["control_mode"] in {"HUMAN_ACTIVE", "HANDOFF_PENDING"}
            assert future.result(timeout=15)
        time.sleep(0.5)
        messages = adapter.get_messages(external_id)["payload"]
        assert not [row for row in messages if row["id"] > first["id"] and row["message_type"] == 1 and row["private"] is False]
        report["checks"]["takeover_during_actual_2_second_fixed_task_blocks_public_reply"] = "pass"
        assert local().control_mode != "BOT_ACTIVE"

        # A late authentic pending-state notification cannot itself grant local permission.
        cw.post(path + "/toggle_status", json={"status": "pending"}).raise_for_status()
        time.sleep(1)
        assert local().control_mode != "BOT_ACTIVE"
        report["checks"]["remote_pending_without_local_employee_authorization_does_not_resume"] = "pass"
        current = expect_http(api.get(f"/api/v1/staff/conversations/{external_id}"))
        expect_http(api.post(f"/api/v1/staff/conversations/{external_id}/return-to-bot",
            json={"expected_revision": current["control_revision"]}, headers=staff_headers))
        assert local().control_mode == "BOT_ACTIVE"
        # No old task is revived by explicit return.
        assert worker.run_once() is False
        second = expect_http(cw.post(widget_url, params=widget_params, headers=widget_headers,
            json={"message": {"content": "M0 real new input after explicit return " + uuid4().hex}}))
        wait_for(lambda: local().input_revision >= 2)
        assert Worker(replace(settings, fixed_reply_delay_seconds=0), factory, adapter).run_once()
        wait_for(lambda: [row for row in adapter.get_messages(external_id)["payload"] if row["id"] > second["id"] and row["message_type"] == 1 and row["private"] is False])
        report["checks"]["explicit_staff_return_plus_new_real_input_resumes_exactly_one_reply"] = "pass"
        with factory() as session:
            conv_id = local().id
            records = session.scalars(select(OutboundMessage).where(OutboundMessage.conversation_id == conv_id)).all()
            assert len([row for row in records if row.status == "SENT"]) == 1
            event = session.scalar(select(WebhookEvent).where(WebhookEvent.event_key == f"{settings.chatwoot_instance_id}:{settings.chatwoot_account_id}:message_created:{first['id']}"))
            fixture = event.payload["wire_fixture"]
            event_key = event.event_key
            before = session.scalar(select(func.count()).select_from(Job).where(Job.event_id == event.id))
            controls = session.scalars(select(WebhookEvent).where(WebhookEvent.channel == "control")).all()
            control_fixture = next(row.payload["wire_fixture"] for row in reversed(controls) if row.payload.get("conversation_id") == external_id)
        directory = Path("tests/fixtures/real")
        directory.mkdir(parents=True, exist_ok=True)
        for name, value in [("message_created.webhook.json", fixture), ("conversation_control.webhook.json", control_fixture)]:
            (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["fixture_source"] = "actual authenticated Sidekiq-delivered wire payload retained/redacted by ingress; no secret/header export"
        raw = json.dumps(fixture, ensure_ascii=False, separators=(",", ":")).encode()
        stamp = str(int(time.time()))

        def headers(body, secret, timestamp):
            return {"X-Chatwoot-Timestamp": timestamp, "X-Chatwoot-Signature": "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest(), "Content-Type": "application/json"}

        bot_path = "/integrations/chatwoot/agentbot"
        valid_headers = headers(raw, settings.chatwoot_bot_webhook_secret, stamp)
        replay = expect_http(api.post(bot_path, content=raw, headers=valid_headers))
        assert replay["duplicate"] is True
        assert api.post(bot_path, content=raw + b" ", headers=valid_headers).status_code == 401
        assert api.post(bot_path, content=raw, headers=headers(raw, "deliberately-wrong-synthetic-secret", stamp)).status_code == 401
        old = str(int(time.time()) - settings.webhook_tolerance_seconds - 1)
        assert api.post(bot_path, content=raw, headers=headers(raw, settings.chatwoot_bot_webhook_secret, old)).status_code == 401
        with factory() as session:
            event = session.scalar(select(WebhookEvent).where(WebhookEvent.event_key == event_key))
            after = session.scalar(select(func.count()).select_from(Job).where(Job.event_id == event.id))
            assert before == after == 1
        report["checks"]["actual_HTTP_redacted_fixture_receiver_replay_valid_signature_dedupes_one_task"] = "pass"
        report["checks"]["actual_HTTP_body_tamper_wrong_secret_expired_timestamp_all_401"] = "pass"
        report["receiver_replay_boundary"] = "These receiver requests are locally re-signed redacted real fixture replays, not Chatwoot-origin network attempts. Original accepted events came through actual Sidekiq."
    report["passed"] = all(value == "pass" for value in report["checks"].values())
    Path("docs/evidence").mkdir(parents=True, exist_ok=True)
    Path("docs/evidence/m0-live-control.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
