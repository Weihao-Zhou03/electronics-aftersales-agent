"""Read-only local acceptance evidence. No state transitions or secret output."""

import argparse
import json

from sqlalchemy import select

from aftersales.agent_models import AgentEvent, AgentRun
from aftersales.business_models import AfterSalesCase, Proposal
from aftersales.config import Settings
from aftersales.db import create_session_factory
from aftersales.models import Conversation, OutboundMessage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customer-id", required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    if settings.app_mode != "demo":
        raise RuntimeError("Local acceptance inspection is restricted to demo")
    with create_session_factory(settings.database_url)() as session:
        runs = session.scalars(select(AgentRun).where(AgentRun.customer_id == args.customer_id).order_by(AgentRun.created_at)).all()
        proposals = session.scalars(select(Proposal).where(Proposal.customer_id == args.customer_id)).all()
        cases = session.scalars(select(AfterSalesCase).where(AfterSalesCase.customer_id == args.customer_id)).all()
        conv_ids = select(Conversation.id).where(Conversation.customer_id == args.customer_id)
        outbound = session.scalars(select(OutboundMessage).where(OutboundMessage.conversation_id.in_(conv_ids))).all()
        print(json.dumps({
            "source": "actual PostgreSQL read-only verification, not workflow mutation",
            "runs": [{"id": row.id, "status": row.status, "model": row.model, "provider": row.provider,
                      "usage": row.usage, "duration_ms": row.duration_ms, "error_kind": row.error_kind,
                      "events": [{"kind": event.kind, "tool": event.tool_name, "result": event.result}
                                 for event in session.scalars(select(AgentEvent).where(AgentEvent.run_id == row.id).order_by(AgentEvent.created_at))]}
                     for row in runs],
            "proposals": [{"id": row.id, "confirmed_case_id": row.confirmed_case_id, "revoked": row.revoked_at is not None} for row in proposals],
            "cases": [{"id": row.id, "status": row.status} for row in cases],
            "outbound": [{"id": row.id, "status": row.status, "chatwoot_message_id": row.chatwoot_message_id} for row in outbound],
        }))


if __name__ == "__main__":
    main()
