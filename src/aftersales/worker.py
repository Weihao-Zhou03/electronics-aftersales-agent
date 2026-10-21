"""Persistent fixed/model replies with per-conversation leases and fencing.

SQLite tests are synthetic. Production requires PostgreSQL row locks; a message
POST with an unknown result is never automatically replayed.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from .config import Settings
from .control import remote_allows_bot, suspend_local
from .db import as_utc, create_session_factory, utcnow
from .models import ChatBinding, Conversation, Job, OutboundMessage

logger = logging.getLogger(__name__)
PUBLIC_GUIDANCE = "演示环境 · 模拟订单与履约。请先在客户门户登录并完成聊天身份验证；当前仅提供公共接入说明。"


def _contact_id(remote):
    return ((remote.get("meta") or {}).get("sender") or {}).get("id") or (remote.get("contact_inbox") or {}).get("contact_id")


def _message_kind(message):
    value = message.get("message_type")
    return {0: "incoming", 1: "outgoing", 2: "activity", 3: "template"}.get(value, value)


def _human_message(message):
    sender = message.get("sender") or {}
    kind = str(sender.get("type") or sender.get("user_type") or "").replace("_", "").lower()
    return _message_kind(message) == "outgoing" and message.get("private") is False and kind in {"user", "agent"}


class Worker:
    def __init__(self, settings, session_factory, adapter, *, sleep=time.sleep):
        self.settings = settings
        self.session_factory = session_factory
        self.adapter = adapter
        self.sleep = sleep

    def _claim(self):
        if not self.settings.agent_enabled:
            return None
        now = utcnow()
        with self.session_factory.begin() as session:
            from .system_api import is_agent_enabled
            if not is_agent_enabled(self.settings, session):
                return None
            candidates = session.scalars(select(Job).where(
                Job.kind == "fixed_reply", Job.available_at <= now,
                or_(Job.status == "PENDING", (Job.status == "RUNNING") & (Job.lease_until < now)))
                .order_by(Job.available_at, Job.id).limit(20).with_for_update(skip_locked=True)).all()
            for candidate in candidates:
                conversation = session.scalar(select(Conversation).where(Conversation.id == candidate.conversation_id)
                                              .with_for_update(skip_locked=True))
                if conversation is None:
                    continue
                if conversation.control_mode != "BOT_ACTIVE":
                    candidate.status = "CANCELLED"
                    continue
                if conversation.lease_until and as_utc(conversation.lease_until) > now:
                    continue
                uncertain = session.scalar(select(OutboundMessage).where(
                    OutboundMessage.conversation_id == conversation.id,
                    OutboundMessage.status.in_(["SENDING", "UNKNOWN"])))
                if uncertain is not None:
                    uncertain.status = "UNKNOWN"
                    uncertain.last_error = "process interrupted or unconfirmed delivery; manual reconciliation required"
                    suspend_local(session, conversation, "worker", "unknown_outbound", "HANDOFF_PENDING")
                    continue
                conversation.fencing_revision += 1
                conversation.lease_owner = str(uuid4())
                conversation.lease_until = now + timedelta(seconds=self.settings.worker_lease_seconds)
                pending = session.scalars(select(Job).where(
                    Job.conversation_id == conversation.id, Job.kind == "fixed_reply",
                    Job.status.in_(["PENDING", "RUNNING"])).with_for_update()).all()
                for job in pending:
                    job.status = "RUNNING"
                    job.attempts += 1
                    job.lease_owner = conversation.lease_owner
                    job.lease_until = conversation.lease_until
                    job.lease_version = conversation.fencing_revision
                return {"id": conversation.id, "external_id": conversation.external_id,
                        "contact_id": conversation.contact_id, "owner": conversation.lease_owner,
                        "fence": conversation.fencing_revision, "revision": conversation.input_revision,
                        "control": conversation.control_revision,
                        "message_ids": sorted(job.payload["message_id"] for job in pending),
                        "jobs": [job.id for job in pending]}
        return None

    @staticmethod
    def _owns(conversation, claim):
        return (conversation is not None and conversation.lease_owner == claim["owner"]
                and conversation.fencing_revision == claim["fence"])

    def _valid(self, conversation, claim):
        return (self._owns(conversation, claim) and conversation.control_mode == "BOT_ACTIVE"
                and conversation.control_revision == claim["control"]
                and conversation.input_revision == claim["revision"]
                and conversation.lease_until is not None and as_utc(conversation.lease_until) > utcnow())

    def _release(self, claim, *, retry=False, error=None):
        with self.session_factory.begin() as session:
            conversation = session.scalar(select(Conversation).where(Conversation.id == claim["id"]).with_for_update())
            if not self._owns(conversation, claim):
                return
            jobs = session.scalars(select(Job).where(Job.id.in_(claim["jobs"]))).all()
            exhausted = retry and any(job.attempts >= self.settings.worker_max_attempts for job in jobs)
            if exhausted:
                suspend_local(session, conversation, "worker", "fixed_reply_attempts_exhausted", "HANDOFF_PENDING")
            for job in jobs:
                if exhausted and job.lease_owner == claim["owner"]:
                    job.status = "FAILED"
                    job.last_error = error
                    job.lease_owner = None
                    job.lease_until = None
                    continue
                if job.status == "RUNNING" and job.lease_owner == claim["owner"]:
                    job.status = "PENDING" if retry and conversation.control_mode == "BOT_ACTIVE" else "CANCELLED"
                    job.available_at = utcnow() + timedelta(seconds=min(2 ** min(job.attempts, 6), 60))
                    job.lease_owner = None
                    job.lease_until = None
                    job.last_error = error
            for outbound in session.scalars(select(OutboundMessage).where(
                    OutboundMessage.conversation_id == conversation.id, OutboundMessage.status == "PREPARED",
                    OutboundMessage.fencing_revision == claim["fence"])):
                outbound.status = "CANCELLED"
                outbound.last_error = error
            conversation.lease_owner = None
            conversation.lease_until = None

    def _heartbeat(self, claim):
        with self.session_factory.begin() as session:
            conversation = session.scalar(select(Conversation).where(Conversation.id == claim["id"]).with_for_update())
            if not self._valid(conversation, claim):
                return False
            conversation.lease_until = utcnow() + timedelta(seconds=self.settings.worker_lease_seconds)
            for job in session.scalars(select(Job).where(Job.id.in_(claim["jobs"]), Job.status == "RUNNING")):
                job.lease_until = conversation.lease_until
            return True

    def _remote_check(self, claim):
        remote = self.adapter.get_conversation(claim["external_id"])
        if not remote_allows_bot(self.settings, remote) or (claim["contact_id"] is not None and _contact_id(remote) != claim["contact_id"]):
            with self.session_factory.begin() as session:
                conversation = session.scalar(select(Conversation).where(Conversation.id == claim["id"]).with_for_update())
                if self._owns(conversation, claim):
                    suspend_local(session, conversation, "worker", "remote_recheck_rejected")
            return False
        response = self.adapter.get_messages(claim["external_id"])
        messages = response.get("payload") if isinstance(response, dict) else None
        if not isinstance(messages, list):
            raise ValueError("unrecognized Chatwoot message-list response")
        ids = {message.get("id") for message in messages}
        if not set(claim["message_ids"]).issubset(ids):
            raise ValueError("message read did not include the pending batch")
        last_input = max(claim["message_ids"])
        if any(_human_message(message) and isinstance(message.get("id"), int) and message["id"] > last_input for message in messages):
            with self.session_factory.begin() as session:
                conversation = session.scalar(select(Conversation).where(Conversation.id == claim["id"]).with_for_update())
                if self._owns(conversation, claim):
                    suspend_local(session, conversation, "worker", "human_outgoing_recheck")
            return False
        if any(_message_kind(message) == "incoming" and isinstance(message.get("id"), int)
               and message["id"] > last_input for message in messages):
            raise ValueError("newer incoming message awaits webhook persistence")
        return True

    def _prepare(self, claim, model_content=None):
        with self.session_factory.begin() as session:
            conversation = session.scalar(select(Conversation).where(Conversation.id == claim["id"]).with_for_update())
            if not self._valid(conversation, claim):
                return None
            binding = session.scalar(select(ChatBinding).where(
                ChatBinding.instance_id == self.settings.chatwoot_instance_id,
                ChatBinding.account_id == self.settings.chatwoot_account_id,
                ChatBinding.inbox_id == self.settings.chatwoot_inbox_id,
                ChatBinding.contact_id == conversation.contact_id,
                ChatBinding.revoked_at.is_(None), ChatBinding.expires_at > utcnow()))
            content = self.settings.fixed_reply_text if binding is not None else PUBLIC_GUIDANCE
            if model_content is not None and binding is not None:
                content = model_content
            conversation.customer_id = binding.customer_id if binding is not None else None
            key = f"fixed:{conversation.id}:{claim['control']}:{claim['revision']}"
            outbound = session.scalar(select(OutboundMessage).where(OutboundMessage.intent_key == key))
            if outbound is not None and outbound.status in {"SENT", "SENDING", "UNKNOWN"}:
                return None
            if outbound is None:
                outbound = OutboundMessage(intent_key=key, conversation_id=conversation.id,
                                           content=content, control_revision=claim["control"], input_revision=claim["revision"],
                                           fencing_revision=claim["fence"])
                session.add(outbound)
            else:
                outbound.content = content
                outbound.status = "PREPARED"
                outbound.fencing_revision = claim["fence"]
            session.flush()
            return {"id": outbound.id, "content": content}

    def _sync_handoff(self):
        now = utcnow()
        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.kind == "sync_handoff", Job.available_at <= now,
                                                   or_(Job.status == "PENDING", (Job.status == "RUNNING") & (Job.lease_until < now)))
                                 .with_for_update(skip_locked=True).limit(1))
            if job is None:
                return False
            conversation = session.scalar(select(Conversation).where(Conversation.id == job.conversation_id).with_for_update())
            if conversation.control_mode == "BOT_ACTIVE" or conversation.control_revision != job.payload["control_revision"]:
                job.status = "CANCELLED"
                return True
            job.status = "RUNNING"
            job.attempts += 1
            job.lease_owner = str(uuid4())
            job.lease_until = now + timedelta(seconds=self.settings.worker_lease_seconds)
            job.lease_version += 1
            job_id, external_id, revision = job.id, conversation.external_id, conversation.control_revision
            owner = job.lease_owner
        try:
            self.adapter.handoff(external_id)
            remote = self.adapter.get_conversation(external_id)
            if remote.get("status") != "open" or remote.get("inbox_id") != self.settings.chatwoot_inbox_id:
                raise ValueError("handoff state not confirmed")
        except Exception:
            with self.session_factory.begin() as session:
                job = session.get(Job, job_id)
                if job.lease_owner != owner:
                    return True
                job.status = "FAILED" if job.attempts >= self.settings.worker_max_attempts else "PENDING"
                job.available_at = utcnow() + timedelta(seconds=min(2 ** min(job.attempts, 6), 60))
                job.last_error = "handoff synchronization failed"
                job.lease_owner = None
                job.lease_until = None
            return True
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id)
            if job.lease_owner != owner:
                return True
            job.status = "SUCCEEDED"
            job.lease_owner = None
            job.lease_until = None
            conversation = session.scalar(select(Conversation).where(Conversation.id == job.conversation_id).with_for_update())
            if conversation.control_revision == revision and conversation.control_mode == "HANDOFF_PENDING":
                conversation.control_mode = "HUMAN_ACTIVE"
        return True

    def run_once(self):
        if self._sync_handoff():
            return True
        claim = self._claim()
        if claim is None:
            return False
        outbound = None
        sending = False
        try:
            if not self._remote_check(claim):
                self._release(claim, error="remote ownership rejected")
                return True
            remaining = self.settings.fixed_reply_delay_seconds
            while remaining > 0:
                pause = min(remaining, max(0.1, self.settings.worker_lease_seconds / 3), 1.0)
                self.sleep(pause)
                remaining -= pause
                if not self._heartbeat(claim):
                    self._release(claim, retry=True, error="input/control/lease changed during fixed delay")
                    return True
            if not self._remote_check(claim):
                self._release(claim, error="remote ownership rejected")
                return True
            model_content = None
            if self.settings.agent_reply_mode == "model":
                from .agent_runtime import FALLBACK_TEXT, run_agent
                from .agent_tools import AgentStopped
                from .model_provider import ModelUnavailable
                with self.session_factory() as session:
                    trusted = session.scalar(select(ChatBinding.id).where(
                        ChatBinding.instance_id == self.settings.chatwoot_instance_id,
                        ChatBinding.account_id == self.settings.chatwoot_account_id,
                        ChatBinding.inbox_id == self.settings.chatwoot_inbox_id,
                        ChatBinding.contact_id == claim["contact_id"],
                        ChatBinding.revoked_at.is_(None), ChatBinding.expires_at > utcnow()))
                if trusted:
                    # Network inference runs outside DB transactions while the main worker renews its fence.
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(run_agent, self.settings, self.session_factory, self.adapter, claim)
                        while True:
                            try:
                                model_content = future.result(timeout=1)
                                break
                            except FutureTimeout:
                                if not self._heartbeat(claim):
                                    self._release(claim, retry=True, error="stale during model call")
                                    return True
                            except AgentStopped:
                                self._release(claim, retry=True, error="model result stale")
                                return True
                            except ModelUnavailable:
                                model_content = FALLBACK_TEXT
                                break
                    if not self._remote_check(claim):
                        self._release(claim, error="ownership changed after model call")
                        return True
            outbound = self._prepare(claim, model_content)
            if outbound is None:
                self._release(claim, retry=True, error="stale input or ownership")
                return True
            with self.session_factory.begin() as session:
                conversation = session.scalar(select(Conversation).where(Conversation.id == claim["id"]).with_for_update())
                if not self._valid(conversation, claim):
                    pass
                else:
                    session.get(OutboundMessage, outbound["id"]).status = "SENDING"
                    sending = True
            if not sending:
                self._release(claim, retry=True, error="stale before send")
                return True
            result = self.adapter.send_reply(claim["external_id"], outbound["content"])
            if not isinstance(result, dict) or not isinstance(result.get("id"), int):
                raise ValueError("send response has no confirmed message id")
            with self.session_factory.begin() as session:
                record = session.get(OutboundMessage, outbound["id"])
                record.status = "SENT"
                record.chatwoot_message_id = result["id"]
                conversation = session.scalar(select(Conversation).where(Conversation.id == claim["id"]).with_for_update())
                if self._owns(conversation, claim):
                    conversation.last_processed_message_id = max(claim["message_ids"])
                    conversation.lease_owner = None
                    conversation.lease_until = None
                for job in session.scalars(select(Job).where(Job.id.in_(claim["jobs"]))):
                    if job.status == "RUNNING" and job.lease_owner == claim["owner"]:
                        job.status = "SUCCEEDED"
                        job.lease_owner = None
                        job.lease_until = None
            return True
        except Exception as error:
            # Do not persist exception text: http errors can contain URLs/credentials.
            safe_error = type(error).__name__
            if sending and outbound is not None:
                with self.session_factory.begin() as session:
                    record = session.get(OutboundMessage, outbound["id"])
                    record.status = "UNKNOWN"
                    record.last_error = safe_error
                    conversation = session.scalar(select(Conversation).where(Conversation.id == claim["id"]).with_for_update())
                    if self._owns(conversation, claim):
                        suspend_local(session, conversation, "worker", "unknown_outbound", "HANDOFF_PENDING")
                    for job in session.scalars(select(Job).where(Job.id.in_(claim["jobs"]))):
                        job.status = "UNKNOWN"
                        job.last_error = "outbound result unknown; manual reconciliation required"
            else:
                self._release(claim, retry=True, error=safe_error)
            return True


def main():
    from .chatwoot import ChatwootAdapter
    settings = Settings.from_env()
    factory = create_session_factory(settings.database_url)
    adapter = ChatwootAdapter(settings.chatwoot_base_url, settings.chatwoot_account_id,
                              settings.chatwoot_inbox_id, settings.chatwoot_bot_access_token,
                              bot_id=settings.chatwoot_bot_id,
                              read_access_token=settings.chatwoot_read_access_token or None)
    worker = Worker(settings, factory, adapter)
    while True:
        from .system_api import heartbeat
        heartbeat(factory, "chat-worker")
        if not worker.run_once():
            time.sleep(0.5)


if __name__ == "__main__":
    main()
