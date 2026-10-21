"""Durable employee notifications; private-note ambiguity never causes a repost."""

from sqlalchemy import select

from .business_models import AfterSalesCase, BusinessJob, Operation, Proposal
from .models import Conversation


def notify_once(settings, factory, adapter):
    with factory.begin() as session:
        job = session.scalar(select(BusinessJob).where(
            BusinessJob.kind.in_(["staff_notification", "manual_operation"]),
            BusinessJob.status == "PENDING").order_by(BusinessJob.created_at, BusinessJob.id)
            .with_for_update(skip_locked=True).limit(1))
        if not job:
            return False
        operation = session.get(Operation, job.payload.get("operation_id")) if job.kind == "manual_operation" else None
        case_id = operation.case_id if operation else job.payload.get("case_id")
        case = session.get(AfterSalesCase, case_id) if case_id else None
        proposal = session.get(Proposal, case.proposal_id) if case else None
        conversation = session.get(Conversation, proposal.agent_conversation_id) if proposal and proposal.agent_conversation_id else None
        if not case:
            job.status = "BLOCKED"
            job.payload = {**job.payload, "notification_error": "missing_case"}
            return True
        if not conversation:
            # Form-only applications have no chat thread. The authoritative case
            # and manual-operation lists are the employee notification channel.
            job.status = "SUCCEEDED"
            job.payload = {**job.payload, "notification_channel": "staff_dashboard"}
            return True
        if (conversation.instance_id != settings.chatwoot_instance_id
                or conversation.account_id != settings.chatwoot_account_id
                or conversation.inbox_id != settings.chatwoot_inbox_id):
            job.status = "BLOCKED"
            job.payload = {**job.payload, "notification_error": "conversation_scope_changed"}
            return True
        job_id, external_id, customer_id, contact_id = job.id, conversation.external_id, case.customer_id, conversation.contact_id
        label = "需员工登记外部履约证据" if operation else "待员工初审"
        root = settings.allowed_origins.split(",")[0].strip().rstrip("/")
        content = f"员工待办 · {case.number} · {label}\n{root}/staff/cases/{case.id}\n仅门户中的确认、初审和验收操作能改变业务状态。"
        # Commit before network IO. SENDING after process loss is displayed as
        # uncertain, never selected by this poller again.
        job.status = "SENDING"
        job.payload = {**job.payload, "notification_channel": "chatwoot_private_note"}
    try:
        remote = adapter.get_conversation(external_id)
        meta = remote.get("meta") or {}
        sender = meta.get("sender") or {}
        if (meta.get("hmac_verified") is not True or sender.get("id") != contact_id
                or sender.get("identifier") != "aftersales:" + customer_id):
            raise ValueError("notification_identity_unverified")
        result = adapter.append_private_note(external_id, content)
        if not isinstance(result.get("id"), int) or result.get("private") is not True:
            raise ValueError("notification_receipt_unconfirmed")
    except Exception as exc:
        with factory.begin() as session:
            job = session.get(BusinessJob, job_id, with_for_update=True)
            job.status = "UNKNOWN"
            job.payload = {**job.payload, "notification_error": type(exc).__name__}
    else:
        with factory.begin() as session:
            job = session.get(BusinessJob, job_id, with_for_update=True)
            job.status = "SUCCEEDED"
            job.payload = {**job.payload, "chatwoot_message_id": result["id"]}
    return True
