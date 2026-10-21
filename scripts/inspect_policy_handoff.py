"""Read-only A06 evidence scoped to an actual conversation and fixture customer."""

import argparse
import json

from sqlalchemy import select

from aftersales.agent_models import AgentEvent, AgentRun
from aftersales.business_models import AfterSalesCase, Proposal
from aftersales.config import Settings
from aftersales.db import create_session_factory
from aftersales.models import ControlAudit, Conversation, Job, OutboundMessage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--conversation-id", required=True, type=int)
    args = parser.parse_args()
    settings = Settings.from_env()
    if settings.app_mode != "demo":
        raise RuntimeError("Read-only acceptance inspection is restricted to demo")
    with create_session_factory(settings.database_url)() as session:
        conversation = session.scalar(select(Conversation).where(
            Conversation.instance_id == settings.chatwoot_instance_id,
            Conversation.account_id == settings.chatwoot_account_id,
            Conversation.inbox_id == settings.chatwoot_inbox_id,
            Conversation.external_id == args.conversation_id))
        if conversation is None:
            raise RuntimeError("Actual scoped conversation is not persisted yet")
        runs = session.scalars(select(AgentRun).where(AgentRun.conversation_id == conversation.id,
            AgentRun.customer_id == args.customer_id).order_by(AgentRun.created_at)).all()
        proposals = session.scalars(select(Proposal).where(Proposal.customer_id == args.customer_id)).all()
        cases = session.scalars(select(AfterSalesCase).where(AfterSalesCase.customer_id == args.customer_id)).all()
        outbound = session.scalars(select(OutboundMessage).where(OutboundMessage.conversation_id == conversation.id)).all()
        audit = session.scalars(select(ControlAudit).where(ControlAudit.conversation_id == conversation.id)).all()
        jobs = session.scalars(select(Job).where(Job.conversation_id == conversation.id)).all()
        print(json.dumps({"source": "Actual PostgreSQL read-only; outbound queried by full conversation scope, independent of nullable customer_id",
            "conversation": {"external_id": conversation.external_id, "control_mode": conversation.control_mode,
                             "control_revision": conversation.control_revision},
            "runs": [{"id": row.id, "provider": row.provider, "model": row.model, "status": row.status,
                      "error_kind": row.error_kind, "duration_ms": row.duration_ms, "usage": row.usage,
                      "events": [{"kind": event.kind, "tool": event.tool_name, "result": event.result}
                                 for event in session.scalars(select(AgentEvent).where(AgentEvent.run_id == row.id).order_by(AgentEvent.created_at))]}
                     for row in runs],
            "control_audit": [{"action": row.action, "actor_id": row.actor_id, "revision": row.revision} for row in audit],
            "jobs": [{"kind": row.kind, "status": row.status, "last_error": row.last_error} for row in jobs],
            "proposals": [{"id": row.id} for row in proposals], "cases": [{"id": row.id} for row in cases],
            "outbound": [{"id": row.id, "status": row.status, "message_id": row.chatwoot_message_id} for row in outbound],
        }))


if __name__ == "__main__":
    main()
