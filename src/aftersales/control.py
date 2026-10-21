"""Fail-closed local conversation ownership and explicit employee transitions."""
from sqlalchemy import inspect, select

from .db import utcnow
from .models import ControlAudit, Conversation, Job, OutboundMessage


class ControlConflict(ValueError):
    pass


def assignee_kind(conversation: dict) -> str | None:
    meta = conversation.get("meta") or {}
    assignee = meta.get("assignee") or conversation.get("assignee") or {}
    kind = meta.get("assignee_type") or assignee.get("type") or assignee.get("user_type")
    if kind:
        return str(kind).replace("_", "").lower()
    return "unknown" if assignee else None


def human_held(conversation: dict) -> bool:
    return assignee_kind(conversation) in {"user", "agent", "unknown"}


def remote_allows_bot(settings, conversation: dict) -> bool:
    """An ambiguous assignee or state never authorizes automatic behavior."""
    if conversation.get("status") != "pending":
        return False
    if conversation.get("inbox_id") != settings.chatwoot_inbox_id:
        return False
    account = conversation.get("account_id")
    if account is not None and account != settings.chatwoot_account_id:
        return False
    kind = assignee_kind(conversation)
    if kind is None:
        return True
    if kind != "agentbot":
        return False
    assignee = (conversation.get("meta") or {}).get("assignee") or conversation.get("assignee") or {}
    return settings.chatwoot_bot_id is not None and assignee.get("id") == settings.chatwoot_bot_id


def suspend_local(session, conversation, actor_id, action, mode="HUMAN_ACTIVE"):
    conversation.control_mode = mode
    conversation.control_revision += 1
    conversation.fencing_revision += 1
    conversation.lease_owner = None
    conversation.lease_until = None
    for job in session.scalars(select(Job).where(Job.conversation_id == conversation.id,
                                                Job.status.in_(["PENDING", "RUNNING"]))):
        job.status = "CANCELLED"
        job.last_error = action
    for outbound in session.scalars(select(OutboundMessage).where(
            OutboundMessage.conversation_id == conversation.id, OutboundMessage.status == "PREPARED")):
        outbound.status = "CANCELLED"
        outbound.last_error = action
    session.add(ControlAudit(conversation_id=conversation.id, actor_id=actor_id,
                             action=action, revision=conversation.control_revision))
    # Keep the M0-only schema usable; after 0004, takeover also invalidates only
    # unconfirmed drafts prepared by this conversation's agent.
    bind = session.get_bind()
    mapped_schema = bind.get_execution_options().get("schema_translate_map", {}).get(None)
    if inspect(session.connection()).has_table("proposals", schema=mapped_schema):
        from .business_models import Proposal
        for proposal in session.scalars(select(Proposal).where(Proposal.agent_conversation_id == conversation.id,
                Proposal.confirmed_case_id.is_(None), Proposal.revoked_at.is_(None)).with_for_update()):
            proposal.revoked_at = utcnow()


def _get_conversation(session, settings, external_id):
    conversation = session.scalar(select(Conversation).where(
        Conversation.instance_id == settings.chatwoot_instance_id,
        Conversation.account_id == settings.chatwoot_account_id,
        Conversation.inbox_id == settings.chatwoot_inbox_id,
        Conversation.external_id == external_id).with_for_update())
    if conversation is None:
        raise LookupError("conversation not found")
    return conversation


def handoff_conversation(settings, session_factory, adapter, external_id, actor_id, expected_revision=None):
    # Commit silence before performing any network IO. Failure cannot re-enable the bot.
    with session_factory.begin() as session:
        conversation = _get_conversation(session, settings, external_id)
        if expected_revision is not None and conversation.control_revision != expected_revision:
            raise ControlConflict("control revision changed")
        suspend_local(session, conversation, str(actor_id), "employee_handoff", "HANDOFF_PENDING")
        revision = conversation.control_revision
    try:
        adapter.handoff(external_id)
        remote = adapter.get_conversation(external_id)
        if (remote.get("status") != "open" or remote.get("inbox_id") != settings.chatwoot_inbox_id
                or remote.get("account_id", settings.chatwoot_account_id) != settings.chatwoot_account_id):
            raise ControlConflict("Chatwoot did not confirm handoff")
    except Exception:
        with session_factory.begin() as session:
            conversation = _get_conversation(session, settings, external_id)
            if conversation.control_revision == revision:
                session.add(Job(kind="sync_handoff", conversation_id=conversation.id,
                                dedupe_key=f"handoff:{conversation.id}:{revision}",
                                payload={"control_revision": revision}))
        return {"control_mode": "HANDOFF_PENDING", "control_revision": revision, "synchronized": False}
    with session_factory.begin() as session:
        conversation = _get_conversation(session, settings, external_id)
        if conversation.control_revision == revision:
            conversation.control_mode = "HUMAN_ACTIVE"
            conversation.last_remote_status = "open"
    return {"control_mode": "HUMAN_ACTIVE", "control_revision": revision, "synchronized": True}


def return_conversation_to_bot(settings, session_factory, adapter, external_id, actor_id, expected_revision=None):
    # Authorization is an explicit authenticated employee action, never a pending webhook.
    with session_factory.begin() as session:
        conversation = _get_conversation(session, settings, external_id)
        if expected_revision is not None and conversation.control_revision != expected_revision:
            raise ControlConflict("control revision changed")
        uncertain = session.scalar(select(OutboundMessage.id).where(
            OutboundMessage.conversation_id == conversation.id, OutboundMessage.status.in_(["SENDING", "UNKNOWN"])))
        if uncertain is not None:
            raise ControlConflict("unconfirmed outbound requires manual reconciliation before return")
        suspend_local(session, conversation, str(actor_id), "employee_return_requested", "HANDOFF_PENDING")
        revision = conversation.control_revision
    adapter.return_to_bot(external_id)
    remote = adapter.get_conversation(external_id)
    if not remote_allows_bot(settings, remote):
        raise ControlConflict("Chatwoot ownership/state could not be verified; bot remains paused")
    with session_factory.begin() as session:
        conversation = _get_conversation(session, settings, external_id)
        if conversation.control_revision != revision:
            raise ControlConflict("ownership changed during return; bot remains paused")
        conversation.control_mode = "BOT_ACTIVE"
        conversation.last_remote_status = "pending"
        session.add(ControlAudit(conversation_id=conversation.id, actor_id=str(actor_id),
                                 action="employee_return_verified", revision=revision))
    return {"control_mode": "BOT_ACTIVE", "control_revision": revision, "synchronized": True}
