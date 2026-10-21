"""Deterministic aftersales transactions, independent of chat and model availability."""
import hashlib
import json
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .business_models import (
    AfterSalesCase,
    BusinessJob,
    CaseEvent,
    Environment,
    IdempotencyRequest,
    InventoryReservation,
    Operation,
    Order,
    OrderItem,
    OrderUnit,
    PolicyVersion,
    Product,
    Proposal,
    RefundAllocation,
    ReturnedItem,
    StockUnit,
)
from .db import as_utc, utcnow
from .models import new_id

CLOSED_RELEASED = {"REJECTED", "CANCELLED", "CLOSED_UNFULFILLED"}
KINDS = {"return", "exchange"}


class DomainError(ValueError):
    def __init__(self, status, code, data=None):
        super().__init__(code)
        self.status, self.code, self.data = status, code, data


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def timestamp(value):
    return as_utc(value).isoformat() if value else None


def require(condition, code, status=409, data=None):
    if not condition:
        raise DomainError(status, code, data)


def run_transaction(factory, action):
    for attempt in range(2):
        try:
            with factory.begin() as session:
                return action(session)
        except IntegrityError as error:
            if attempt:
                raise DomainError(409, "CONCURRENT_OR_DUPLICATE_REQUEST") from error
    raise RuntimeError("unreachable")


def idempotent(session, actor, scope, key, request, action):
    require(bool(key) and len(key) <= 128, "IDEMPOTENCY_KEY_REQUIRED", 422)
    hashed = digest(request)
    row = session.scalar(select(IdempotencyRequest).where(IdempotencyRequest.actor_id == actor,
        IdempotencyRequest.route_scope == scope, IdempotencyRequest.key == key).with_for_update())
    if row is not None:
        require(row.request_hash == hashed, "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH")
        return row.response
    row = IdempotencyRequest(actor_id=actor, route_scope=scope, key=key, request_hash=hashed, response={})
    session.add(row)
    session.flush()
    result = action()
    row.response = result
    return result


def ensure_environment(session, app_mode, environment_id, **_unused):
    require(app_mode in {"demo", "pilot"} and bool(environment_id), "ENVIRONMENT_CONFIGURATION_INVALID", 503)
    row = session.get(Environment, "singleton", with_for_update=True)
    if row is None:
        session.add(Environment(id="singleton", app_mode=app_mode, environment_id=environment_id))
    else:
        require(row.app_mode == app_mode and row.environment_id == environment_id, "DATABASE_ENVIRONMENT_MISMATCH", 503)


def validate_policy(package):
    require(isinstance(package, dict), "POLICY_PACKAGE_INVALID", 422)
    required = {"version", "scope", "rules", "sections", "rule_source_map"}
    require(set(package) == required, "POLICY_PACKAGE_FIELDS_INVALID", 422)
    require(isinstance(package["version"], str) and 1 <= len(package["version"]) <= 80, "POLICY_VERSION_INVALID", 422)
    categories = package["scope"].get("categories") if isinstance(package["scope"], dict) else None
    require(isinstance(categories, list) and bool(categories) and all(isinstance(x, str) and x for x in categories), "POLICY_SCOPE_INVALID", 422)
    rules = package["rules"]
    expected = {"return_window_days", "exchange_window_days", "return_requires_unused", "return_requires_all_accessories",
                "exchange_requires_inspection", "same_sku_only"}
    require(isinstance(rules, dict) and set(rules) == expected, "POLICY_RULES_INVALID", 422)
    for key in ["return_window_days", "exchange_window_days"]:
        require(type(rules[key]) is int and 0 < rules[key] <= 365, "POLICY_WINDOW_INVALID", 422)
    for key in expected - {"return_window_days", "exchange_window_days"}:
        require(type(rules[key]) is bool, "POLICY_BOOLEAN_INVALID", 422)
    require(rules["same_sku_only"] is True and rules["exchange_requires_inspection"] is True, "UNSUPPORTED_POLICY_SCOPE", 422)
    sections = package["sections"]
    require(isinstance(sections, list) and all(isinstance(x, dict) and set(x) == {"id", "text"}
            and isinstance(x["id"], str) and isinstance(x["text"], str) and x["text"].strip() for x in sections), "POLICY_SECTIONS_INVALID", 422)
    ids = {x["id"] for x in sections}
    require(len(ids) == len(sections) and len(ids) >= 2, "POLICY_SECTION_IDS_INVALID", 422)
    mapping = package["rule_source_map"]
    require(isinstance(mapping, dict) and all(key in mapping and mapping[key] in ids for key in ["return_window_days", "exchange_window_days"]), "POLICY_RULE_MAPPING_INVALID", 422)
    return {"valid": True, "content_hash": digest(package)}


def publish_policy(session, package, clock=utcnow):
    validated = validate_policy(package)
    existing = session.scalar(select(PolicyVersion).where(PolicyVersion.version == package["version"]))
    if existing:
        require(existing.content_hash == validated["content_hash"], "PUBLISHED_POLICY_IMMUTABLE")
        return policy_dict(existing)
    row = PolicyVersion(version=package["version"], scope=package["scope"], rules_json=package["rules"],
        sections=package["sections"], rule_source_map=package["rule_source_map"],
        document_text="\n\n".join(x["text"] for x in package["sections"]), content_hash=validated["content_hash"], published_at=clock())
    session.add(row)
    session.flush()
    return policy_dict(row)


def policy_dict(row):
    return {"id": row.id, "version": row.version, "scope": row.scope, "rules": row.rules_json,
            "sections": row.sections, "rule_source_map": row.rule_source_map,
            "content_hash": row.content_hash, "published_at": timestamp(row.published_at)}


def validate_address(address):
    require(isinstance(address, dict) and set(address) <= {"name", "phone", "region", "detail", "country"}, "ADDRESS_FIELDS_INVALID", 422)
    limits = {"name": 80, "phone": 40, "region": 160, "detail": 300}
    for name, limit in limits.items():
        require(isinstance(address.get(name), str) and 1 <= len(address[name].strip()) <= limit, "ADDRESS_" + name.upper() + "_INVALID", 422)
    if "country" in address:
        require(address["country"] == "CN", "ADDRESS_COUNTRY_MANUAL_REVIEW", 422)
    return {key: value.strip() for key, value in address.items()}


def _unit_context(session, unit_id, customer_id=None, lock=False):
    query = select(OrderUnit, OrderItem, Order, Product).join(OrderItem, OrderUnit.order_item_id == OrderItem.id).join(
        Order, OrderItem.order_id == Order.id).join(Product, OrderItem.product_id == Product.id).where(OrderUnit.id == unit_id)
    if customer_id is not None:
        query = query.where(Order.customer_id == customer_id)
    if lock:
        query = query.with_for_update(of=OrderUnit)
    row = session.execute(query).first()
    require(row is not None, "RESOURCE_NOT_ACCESSIBLE", 404)
    return row


def evaluate_eligibility(session, customer_id, unit_id, kind, facts, clock=utcnow):
    require(kind in KINDS, "UNSUPPORTED_CASE_TYPE", 422)
    unit, _item, order, product = _unit_context(session, unit_id, customer_id)
    policy = session.get(PolicyVersion, unit.sale_policy_version_id) if unit.sale_policy_version_id else None
    reasons, missing, decision = [], [], "eligible"
    if order.currency != "CNY" or order.unsupported_features or order.source not in {"demo", "verified"}:
        reasons.append("UNSUPPORTED_ORDER_REQUIRES_HUMAN")
    if unit.replacement_for_case_id:
        reasons.append("REPLACEMENT_DEVICE_REQUIRES_HUMAN")
    if unit.allocated_paid_cents <= 0:
        reasons.append("ZERO_VALUE_REQUIRES_HUMAN")
    if policy is None or product.category not in (policy.scope.get("categories") or []):
        reasons.append("POLICY_MISSING_OR_CONTRADICTORY")
    if reasons:
        decision = "needs_review"
    source_refs, deadline = [], None
    if policy:
        window_key = "return_window_days" if kind == "return" else "exchange_window_days"
        rule_days = policy.rules_json.get(window_key)
        section = policy.rule_source_map.get(window_key)
        if type(rule_days) is not int or not section or not any(x["id"] == section for x in policy.sections):
            decision = "needs_review"
            reasons.append("POLICY_RULE_SOURCE_MISSING")
        else:
            source_refs = [{"version": policy.version, "section_id": section}]
            deadline = as_utc(order.delivered_at) + timedelta(days=rule_days)
            if clock() >= deadline:
                reasons.append("APPLICATION_WINDOW_EXPIRED")
                if decision != "needs_review":
                    decision = "ineligible"
            if clock() < as_utc(order.delivered_at):
                reasons.append("DELIVERY_SOURCE_CONTRADICTORY")
                decision = "needs_review"
        checks = []
        if kind == "return":
            if policy.rules_json.get("return_requires_unused") is True:
                checks.append(("unused", "ITEM_USED", "UNUSED_STATUS_REQUIRED"))
            if policy.rules_json.get("return_requires_all_accessories") is True:
                checks.append(("accessories_complete", "ACCESSORIES_INCOMPLETE", "ACCESSORIES_STATUS_REQUIRED"))
        else:
            checks.append(("quality_issue", "QUALITY_ISSUE_NOT_CONFIRMED", "QUALITY_ISSUE_REQUIRED"))
        for field, no_reason, missing_reason in checks:
            if type(facts.get(field)) is not bool:
                missing.append(field)
                reasons.append(missing_reason)
            elif facts[field] is False:
                reasons.append(no_reason)
                if decision != "needs_review":
                    decision = "ineligible"
    if not isinstance(facts.get("reason"), str) or not facts["reason"].strip():
        missing.append("reason")
        reasons.append("REASON_REQUIRED")
    if missing and decision == "eligible":
        decision = "missing_information"
    return {"decision": decision, "reason_codes": reasons, "missing_fields": missing,
            "policy_version": policy.version if policy else None, "policy_version_id": policy.id if policy else None,
            "source_refs": source_refs, "refund_amount_cents": unit.allocated_paid_cents if kind == "return" else 0,
            "currency": order.currency, "deadline": timestamp(deadline), "simulated": order.source == "demo"}


def _summary(row):
    return {"order_unit_id": row.order_unit_id, "type": row.type, "facts": row.payload,
            "shipping_address": row.shipping_address_snapshot, "amount_cents": row.quoted_amount_cents,
            "policy_version_id": row.policy_version_id, "version": row.version}


def proposal_dict(row, session=None):
    result = {"id": row.id, "customer_id": row.customer_id, **_summary(row), "summary_hash": row.summary_hash,
              "expires_at": timestamp(row.expires_at), "confirmed_case_id": row.confirmed_case_id,
              "revoked_at": timestamp(row.revoked_at)}
    if session:
        result["policy"] = policy_dict(session.get(PolicyVersion, row.policy_version_id))
    return result


def create_proposal(session, customer_id, unit_id, kind, facts, shipping_address=None, clock=utcnow, ttl_seconds=1800):
    if facts.get("evidence_ids"):
        from .attachments import validate_evidence
        validate_evidence(session, customer_id, facts["evidence_ids"])
    result = evaluate_eligibility(session, customer_id, unit_id, kind, facts, clock)
    require(result["decision"] == "eligible", "ELIGIBILITY_REQUIRES_ATTENTION", data=result)
    _unit, _item, order, _product = _unit_context(session, unit_id, customer_id)
    address = validate_address(shipping_address or order.shipping_address) if kind == "exchange" else None
    row = Proposal(customer_id=customer_id, order_unit_id=unit_id, type=kind, payload=facts,
                   shipping_address_snapshot=address, policy_version_id=result["policy_version_id"],
                   quoted_amount_cents=result["refund_amount_cents"], version=1, expires_at=clock() + timedelta(seconds=ttl_seconds), summary_hash="")
    row.summary_hash = digest(_summary(row))
    session.add(row)
    session.flush()
    return proposal_dict(row, session)


def _proposal(session, proposal_id, customer_id, lock=False):
    query = select(Proposal).where(Proposal.id == proposal_id, Proposal.customer_id == customer_id)
    row = session.scalar(query.with_for_update() if lock else query)
    require(row is not None, "RESOURCE_NOT_ACCESSIBLE", 404)
    require(row.revoked_at is None, "PROPOSAL_REVOKED_DURING_HANDOFF")
    return row


def update_proposal(session, customer_id, proposal_id, expected_version, facts=None, shipping_address=None, clock=utcnow):
    row = _proposal(session, proposal_id, customer_id, True)
    require(row.version == expected_version, "VERSION_CONFLICT")
    require(row.confirmed_case_id is None and clock() < as_utc(row.expires_at), "PROPOSAL_CONFIRMED_OR_EXPIRED")
    new_facts = facts if facts is not None else row.payload
    if new_facts.get("evidence_ids"):
        from .attachments import validate_evidence
        validate_evidence(session, customer_id, new_facts["evidence_ids"])
    eligibility = evaluate_eligibility(session, customer_id, row.order_unit_id, row.type, new_facts, clock)
    require(eligibility["decision"] == "eligible", "ELIGIBILITY_REQUIRES_ATTENTION", data=eligibility)
    row.payload = new_facts
    if shipping_address is not None:
        require(row.type == "exchange", "RETURN_ADDRESS_NOT_APPLICABLE", 422)
        row.shipping_address_snapshot = validate_address(shipping_address)
    row.quoted_amount_cents = eligibility["refund_amount_cents"]
    row.policy_version_id = eligibility["policy_version_id"]
    row.version += 1
    row.summary_hash = digest(_summary(row))
    return proposal_dict(row, session)


def _event(session, case, actor, action, before, detail=None, operation_id=None, clock=utcnow):
    session.add(CaseEvent(case_id=case.id, event=action, from_status=before, to_status=case.status,
                          actor_id=str(actor), operation_id=operation_id, detail=detail or {}, created_at=clock()))


def confirm_proposal(session, customer_id, proposal_id, expected_version, summary_hash, clock=utcnow):
    accepted_at = clock()
    def accepted_clock():
        return accepted_at
    row = _proposal(session, proposal_id, customer_id, True)
    require(row.version == expected_version and row.summary_hash == summary_hash, "PREVIEW_CHANGED_RECONFIRM")
    if row.confirmed_case_id:
        return case_dict(session.get(AfterSalesCase, row.confirmed_case_id), session)
    require(accepted_at < as_utc(row.expires_at), "PROPOSAL_EXPIRED")
    unit, _item, _order, _product = _unit_context(session, row.order_unit_id, customer_id, True)
    eligibility = evaluate_eligibility(session, customer_id, unit.id, row.type, row.payload, accepted_clock)
    require(eligibility["decision"] == "eligible", "ELIGIBILITY_REQUIRES_ATTENTION", data=eligibility)
    require(eligibility["refund_amount_cents"] == row.quoted_amount_cents and unit.sale_policy_version_id == row.policy_version_id,
            "PREVIEW_CHANGED_RECONFIRM", data=eligibility)
    occupied = session.scalar(select(AfterSalesCase.id).where(AfterSalesCase.order_unit_id == unit.id,
                                                           AfterSalesCase.status.not_in(CLOSED_RELEASED)))
    require(occupied is None, "DEVICE_ALREADY_HAS_CASE")
    policy = session.get(PolicyVersion, row.policy_version_id)
    case_id = new_id()
    case = AfterSalesCase(id=case_id, number="AS-" + accepted_at.strftime("%Y%m%d") + "-" + case_id[:8].upper(),
        proposal_id=row.id, customer_id=customer_id, order_unit_id=unit.id, type=row.type, status="PENDING_REVIEW", version=1,
        accepted_at=accepted_at, policy_snapshot={**policy_dict(policy), "eligibility": eligibility},
        confirmed_payload={**_summary(row), "summary_hash": row.summary_hash}, shipping_address_snapshot=row.shipping_address_snapshot)
    session.add(case)
    session.flush()
    row.confirmed_case_id = case.id
    _event(session, case, customer_id, "customer_confirmed", None, {"proposal_id": row.id, "summary_hash": row.summary_hash}, clock=clock)
    session.add(BusinessJob(kind="staff_notification", dedupe_key="new-case:" + case.id,
                            payload={"case_id": case.id}, status="PENDING"))
    return case_dict(case, session)


def _case(session, case_id, customer_id=None, expected_version=None):
    query = select(AfterSalesCase).where(AfterSalesCase.id == case_id)
    if customer_id is not None:
        query = query.where(AfterSalesCase.customer_id == customer_id)
    row = session.scalar(query.with_for_update())
    require(row is not None, "RESOURCE_NOT_ACCESSIBLE", 404)
    if expected_version is not None:
        require(row.version == expected_version, "VERSION_CONFLICT")
    return row


def case_dict(row, session=None):
    result = {"id": row.id, "number": row.number, "customer_id": row.customer_id, "order_unit_id": row.order_unit_id,
              "type": row.type, "status": row.status, "version": row.version, "accepted_at": timestamp(row.accepted_at),
              "policy_snapshot": row.policy_snapshot, "confirmed_payload": row.confirmed_payload,
              "pending_reason": row.pending_reason, "return_shipment": row.return_shipment,
              "shipping_address": row.shipping_address_snapshot}
    if session:
        session.flush()
        result["events"] = [{"id": event.id, "event": event.event, "from_status": event.from_status,
            "to_status": event.to_status, "actor_id": event.actor_id, "detail": event.detail,
            "created_at": timestamp(event.created_at)} for event in session.scalars(select(CaseEvent).where(
                CaseEvent.case_id == row.id).order_by(CaseEvent.created_at, CaseEvent.id))]
        result["operations"] = [operation_dict(operation) for operation in session.scalars(select(Operation).where(Operation.case_id == row.id))]
        returned = session.scalar(select(ReturnedItem).where(ReturnedItem.case_id == row.id))
        result["returned_item"] = {"id": returned.id, "received_at": timestamp(returned.received_at),
            "received_serial": returned.received_serial, "verification": returned.verification,
            "custody_status": returned.custody_status, "disposition": returned.disposition} if returned else None
    return result


def _advance(session, case, actor, action, status, detail=None, clock=utcnow):
    before = case.status
    case.status = status
    case.version += 1
    _event(session, case, actor, action, before, detail, clock=clock)
    return case_dict(case, session)


def review_case(session, actor_id, case_id, expected_version, decision, reason, clock=utcnow):
    case = _case(session, case_id, expected_version=expected_version)
    require(case.status == "PENDING_REVIEW", "CASE_STATE_CONFLICT")
    require(decision in {"approve_return", "reject", "request_info"} and bool(reason.strip()), "REVIEW_DECISION_REASON_REQUIRED", 422)
    status = {"approve_return": "AWAITING_RETURN", "reject": "REJECTED", "request_info": "PENDING_REVIEW"}[decision]
    case.pending_reason = reason if decision != "approve_return" else None
    return _advance(session, case, actor_id, "staff_review_" + decision, status, {"reason": reason}, clock)


def record_return_shipment(session, customer_id, case_id, expected_version, carrier, tracking_number, clock=utcnow):
    case = _case(session, case_id, customer_id, expected_version)
    require(case.status == "AWAITING_RETURN" and case.return_shipment is None, "RETURN_SHIPMENT_STATE_CONFLICT")
    require(1 <= len(carrier.strip()) <= 80 and 1 <= len(tracking_number.strip()) <= 128, "TRACKING_INVALID", 422)
    case.return_shipment = {"carrier": carrier.strip(), "tracking_number": tracking_number.strip(), "recorded_at": timestamp(clock())}
    return _advance(session, case, customer_id, "return_shipment_recorded", case.status, {}, clock)


def supplement_case(session, customer_id, case_id, expected_version, information, clock=utcnow):
    case = _case(session, case_id, customer_id, expected_version)
    require(case.status in {"PENDING_REVIEW", "AWAITING_RETURN", "MANUAL_REVIEW"}, "SUPPLEMENT_STATE_CONFLICT")
    require(bool(information), "SUPPLEMENT_REQUIRED", 422)
    if information.get("evidence_ids"):
        from .attachments import validate_evidence
        validate_evidence(session, customer_id, information["evidence_ids"])
    return _advance(session, case, customer_id, "customer_supplement", case.status, {"information": information}, clock)


def cancel_case(session, customer_id, case_id, expected_version, clock=utcnow):
    case = _case(session, case_id, customer_id, expected_version)
    require(case.status == "PENDING_REVIEW" or (case.status == "AWAITING_RETURN" and case.return_shipment is None), "CANCELLATION_REQUIRES_STAFF")
    return _advance(session, case, customer_id, "customer_cancelled", "CANCELLED", {}, clock)


def receive_case(session, actor_id, case_id, expected_version, received_serial, clock=utcnow):
    case = _case(session, case_id, expected_version=expected_version)
    require(case.status == "AWAITING_RETURN" and case.return_shipment is not None, "RECEIVE_STATE_CONFLICT")
    require(isinstance(received_serial, str) and 1 <= len(received_serial.strip()) <= 128, "RECEIVED_SERIAL_REQUIRED", 422)
    session.add(ReturnedItem(case_id=case.id, order_unit_id=case.order_unit_id, received_at=clock(),
                             received_serial=received_serial.strip(), custody_status="HELD", verification={}))
    return _advance(session, case, actor_id, "item_received", "INSPECTING", {"received_serial": received_serial.strip()}, clock)


def _create_obligation(session, case, unit, product, config, clock):
    mode = config.get("fulfillment_mode", "demo")
    app_mode = config.get("app_mode", "demo")
    environment_id = config.get("environment_id", "electronics-aftersales-demo")
    require(mode in {"demo", "manual", "external"}, "FULFILLMENT_MODE_INVALID", 503)
    require(not (app_mode == "pilot" and mode == "demo"), "PILOT_DEMO_PROVIDER_FORBIDDEN", 503)
    require(mode != "external", "EXTERNAL_ADAPTER_NOT_CONFIGURED", 503)
    ensure_environment(session, app_mode, environment_id)
    provider_name = "manual" if mode == "manual" else "demo-provider"
    snapshot = {"case_id": case.id, "device_id": unit.id}
    if case.type == "return":
        reserved = session.scalar(select(func.coalesce(func.sum(RefundAllocation.amount_cents), 0)).where(
            RefundAllocation.order_unit_id == unit.id, RefundAllocation.status.in_(["RESERVED", "SETTLED"])))
        require(reserved + unit.allocated_paid_cents <= unit.allocated_paid_cents, "REFUND_AMOUNT_ALREADY_OCCUPIED")
        session.add(RefundAllocation(case_id=case.id, order_unit_id=unit.id, amount_cents=unit.allocated_paid_cents, status="RESERVED"))
        snapshot.update(amount_cents=unit.allocated_paid_cents, currency="CNY")
        kind = "refund"
    else:
        stock = session.scalar(select(StockUnit).where(StockUnit.product_id == product.id, StockUnit.status == "AVAILABLE")
                               .order_by(StockUnit.id).with_for_update(skip_locked=True).limit(1))
        if stock is None:
            return None
        stock.status = "RESERVED"
        session.add(InventoryReservation(case_id=case.id, stock_unit_id=stock.id, status="ACTIVE"))
        snapshot.update(sku=product.sku, shipping_address=validate_address(case.shipping_address_snapshot), outbound_device_id=stock.id)
        kind = "shipment"
    obligation = kind + ":" + case.id
    operation = Operation(case_id=case.id, kind=kind, obligation_key=obligation, idempotency_key=obligation,
                          request_snapshot=snapshot, request_hash=digest(snapshot), execution_mode=mode,
                          provider_name=provider_name, provider_version="1", environment_id=environment_id,
                          status="AWAITING_MANUAL" if mode == "manual" else "PENDING", version=1,
                          available_at=clock(), attempts=0, lease_version=0)
    session.add(operation)
    session.flush()
    session.add(BusinessJob(kind="manual_operation" if mode == "manual" else "execute_operation",
                            dedupe_key=obligation, payload={"operation_id": operation.id}, status="PENDING"))
    return operation


def inspect_case(session, actor_id, case_id, expected_version, passed, verification, config=None, clock=utcnow):
    case = _case(session, case_id, expected_version=expected_version)
    require(case.status in {"INSPECTING", "MANUAL_REVIEW"}, "INSPECTION_STATE_CONFLICT")
    unit, _item, _order, product = _unit_context(session, case.order_unit_id, lock=True)
    returned = session.scalar(select(ReturnedItem).where(ReturnedItem.case_id == case.id).with_for_update())
    require(returned is not None and returned.custody_status == "HELD", "ITEM_NOT_IN_CUSTODY")
    require(type(passed) is bool and isinstance(verification, dict) and bool(verification.get("reason")), "INSPECTION_EVIDENCE_REQUIRED", 422)
    require(session.scalar(select(Operation.id).where(Operation.case_id == case.id)) is None, "OPERATION_ALREADY_EXISTS")
    returned.verification = verification
    valid = passed and returned.received_serial == unit.serial_number
    if case.type == "return":
        rules = case.policy_snapshot["rules"]
        if rules["return_requires_unused"]:
            valid = valid and verification.get("unused") is True
        if rules["return_requires_all_accessories"]:
            valid = valid and verification.get("accessories_complete") is True
    else:
        valid = valid and verification.get("quality_issue") is True
    if not valid:
        case.pending_reason = "INSPECTION_FAILED_OR_SERIAL_MISMATCH"
        return _advance(session, case, actor_id, "inspection_failed", "MANUAL_REVIEW", verification, clock)
    operation = _create_obligation(session, case, unit, product, config or {}, clock)
    status = "WAITING_STOCK" if operation is None else ("FULFILLING" if operation.execution_mode == "manual" else "READY_TO_FULFILL")
    case.pending_reason = "WAITING_SAME_SKU_STOCK" if operation is None else None
    return _advance(session, case, actor_id, "inspection_accepted", status, verification, clock)


def resume_stock(session, actor_id, case_id, expected_version, config=None, clock=utcnow):
    case = _case(session, case_id, expected_version=expected_version)
    require(case.status == "WAITING_STOCK" and case.type == "exchange", "STOCK_RESUME_STATE_CONFLICT")
    unit, _item, _order, product = _unit_context(session, case.order_unit_id, lock=True)
    operation = _create_obligation(session, case, unit, product, config or {}, clock)
    status = "WAITING_STOCK" if operation is None else ("FULFILLING" if operation.execution_mode == "manual" else "READY_TO_FULFILL")
    case.pending_reason = "WAITING_SAME_SKU_STOCK" if operation is None else None
    return _advance(session, case, actor_id, "stock_resume", status, {}, clock)


def disposition_case(session, actor_id, case_id, expected_version, outcome, evidence, clock=utcnow):
    case = _case(session, case_id, expected_version=expected_version)
    require(case.status == "MANUAL_REVIEW", "DISPOSITION_STATE_CONFLICT")
    returned = session.scalar(select(ReturnedItem).where(ReturnedItem.case_id == case.id).with_for_update())
    require(returned is not None and returned.custody_status == "HELD", "ITEM_NOT_IN_CUSTODY")
    require(outcome in {"returned_to_customer", "customer_authorized_disposal"} and bool(evidence), "DISPOSITION_EVIDENCE_REQUIRED", 422)
    operations = session.scalars(select(Operation).where(Operation.case_id == case.id)).all()
    require(all(operation.status == "FAILED" for operation in operations), "UNSETTLED_OPERATION_PREVENTS_CLOSURE")
    # Only a formal, evidenced closure after confirmed zero effect releases resources.
    _unit_context(session, case.order_unit_id, lock=True)
    for allocation in session.scalars(select(RefundAllocation).where(RefundAllocation.case_id == case.id,
            RefundAllocation.status == "RESERVED").with_for_update()):
        allocation.status = "RELEASED"
    for reservation in session.scalars(select(InventoryReservation).where(InventoryReservation.case_id == case.id,
            InventoryReservation.status == "ACTIVE").with_for_update()):
        stock = session.get(StockUnit, reservation.stock_unit_id, with_for_update=True)
        require(stock.status == "RESERVED", "STOCK_NOT_RESERVED")
        stock.status = "AVAILABLE"
        reservation.status = "RELEASED"
    returned.custody_status = "RETURNED" if outcome == "returned_to_customer" else "DISPOSED"
    returned.disposition = {"outcome": outcome, "evidence": evidence, "recorded_at": timestamp(clock()), "actor_id": actor_id}
    return _advance(session, case, actor_id, "physical_disposition_completed", "CLOSED_UNFULFILLED", returned.disposition, clock)


def operation_dict(row):
    return {"id": row.id, "case_id": row.case_id, "kind": row.kind, "obligation_key": row.obligation_key,
            "idempotency_key": row.idempotency_key, "request_snapshot": row.request_snapshot, "request_hash": row.request_hash,
            "execution_mode": row.execution_mode, "provider_name": row.provider_name, "provider_version": row.provider_version,
            "environment_id": row.environment_id, "status": row.status, "provider_operation_id": row.provider_operation_id,
            "result": row.result, "version": row.version, "attempts": row.attempts}


def apply_operation_result(session, operation_id, status, result, actor_id="provider", clock=utcnow,
                           expected_lease_owner=None, expected_lease_version=None):
    operation = session.get(Operation, operation_id, with_for_update=True)
    require(operation is not None, "RESOURCE_NOT_ACCESSIBLE", 404)
    if expected_lease_owner is not None:
        require(operation.lease_owner == expected_lease_owner and operation.lease_version == expected_lease_version, "STALE_OPERATION_LEASE")
    require(status in {"RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN"}, "PROVIDER_STATUS_INVALID", 422)
    if operation.status in {"SUCCEEDED", "FAILED"}:
        require(operation.status == status, "TERMINAL_OPERATION_CANNOT_REGRESS")
        return operation_dict(operation)
    case = _case(session, operation.case_id)
    unit, _item, _order, _product = _unit_context(session, case.order_unit_id, lock=True)
    snapshot = operation.request_snapshot
    require(digest(snapshot) == operation.request_hash, "OPERATION_SNAPSHOT_CORRUPTED", 503)
    if status == "SUCCEEDED":
        if operation.execution_mode == "demo":
            require(result.get("simulated") is True, "DEMO_RESULT_MUST_BE_EXPLICIT")
        if operation.kind == "refund":
            require(type(result.get("amount_cents")) is int and result.get("amount_cents") == snapshot["amount_cents"] and result.get("currency") == "CNY"
                    and bool(result.get("refund_reference")), "REFUND_RESULT_EVIDENCE_MISMATCH")
            allocation = session.scalar(select(RefundAllocation).where(RefundAllocation.case_id == case.id).with_for_update())
            require(allocation is not None and allocation.amount_cents == unit.allocated_paid_cents and allocation.status == "RESERVED", "REFUND_ALLOCATION_INVALID")
            allocation.status = "SETTLED"
        else:
            require(result.get("outbound_device_id") == snapshot["outbound_device_id"] and result.get("sku") == snapshot["sku"]
                    and result.get("shipping_address") == snapshot["shipping_address"]
                    and bool(result.get("tracking_number")) and bool(result.get("shipped_at")), "SHIPMENT_RESULT_EVIDENCE_MISMATCH")
            reservation = session.scalar(select(InventoryReservation).where(InventoryReservation.case_id == case.id,
                InventoryReservation.status == "ACTIVE").with_for_update())
            require(reservation is not None and reservation.stock_unit_id == snapshot["outbound_device_id"], "STOCK_RESERVATION_INVALID")
            stock = session.get(StockUnit, reservation.stock_unit_id, with_for_update=True)
            require(stock.status == "RESERVED", "STOCK_NOT_RESERVED")
            reservation.status = "CONSUMED"
            stock.status = "DISPATCHED"
            stock.replacement_case_id = case.id
        case_status = "COMPLETED"
    elif status == "FAILED":
        # FAILED proves no external effect, but the staff must formally close and
        # dispose of the returned item before reservations are released (SPEC8.4).
        case_status = "MANUAL_REVIEW"
    else:
        case_status = "FULFILLING"
    operation.status = status
    operation.result = result
    operation.version += 1
    operation.provider_operation_id = result.get("provider_operation_id") or operation.provider_operation_id
    operation.lease_owner = None
    operation.lease_until = None
    before = case.status
    case.status = case_status
    case.version += 1
    _event(session, case, actor_id, "operation_" + status.lower(), before,
           {"operation_id": operation.id, "result": result}, operation.id, clock)
    if status in {"SUCCEEDED", "FAILED"}:
        for job in session.scalars(select(BusinessJob).where(BusinessJob.dedupe_key == operation.obligation_key)):
            job.status = "SUCCEEDED"
    return operation_dict(operation)


def record_manual_result(session, actor_id, operation_id, expected_version, obligation_hash, status, evidence, clock=utcnow):
    operation = session.get(Operation, operation_id, with_for_update=True)
    require(operation is not None, "RESOURCE_NOT_ACCESSIBLE", 404)
    require(operation.execution_mode == "manual" and operation.status == "AWAITING_MANUAL", "MANUAL_OPERATION_STATE_CONFLICT")
    require(operation.version == expected_version and operation.request_hash == obligation_hash, "OPERATION_VERSION_OR_HASH_CONFLICT")
    require(status in {"SUCCEEDED", "FAILED"} and isinstance(evidence, dict) and bool(evidence.get("external_reference"))
            and bool(evidence.get("performed_at")) and bool(evidence.get("evidence")), "MANUAL_EVIDENCE_REQUIRED", 422)
    try:
        performed_at = datetime.fromisoformat(evidence["performed_at"].replace("Z", "+00:00"))
        require(performed_at.tzinfo is not None and as_utc(performed_at) <= clock() + timedelta(minutes=5),
                "MANUAL_PERFORMED_AT_INVALID", 422)
    except (ValueError, TypeError, AttributeError) as error:
        raise DomainError(422, "MANUAL_PERFORMED_AT_INVALID") from error
    require(isinstance(evidence["external_reference"], str) and len(evidence["external_reference"]) <= 128,
            "MANUAL_EXTERNAL_REFERENCE_INVALID", 422)
    snapshot = operation.request_snapshot
    if operation.kind == "refund":
        require(type(evidence.get("amount_cents")) is int and evidence.get("amount_cents") == snapshot["amount_cents"] and evidence.get("currency") == "CNY",
                "REFUND_RESULT_EVIDENCE_MISMATCH")
    else:
        require(evidence.get("sku") == snapshot["sku"] and evidence.get("outbound_device_id") == snapshot["outbound_device_id"]
                and evidence.get("shipping_address") == snapshot["shipping_address"], "SHIPMENT_RESULT_EVIDENCE_MISMATCH")
    # Full exact amount/device/address still validated by the same settlement function.
    evidence = {**evidence, "verification_method": "manual_attestation", "recorded_by": actor_id,
                "recorded_at": timestamp(clock())}
    return apply_operation_result(session, operation_id, status, evidence, actor_id, clock)


def reconcile_operation(session, actor_id, operation_id, expected_version, status, evidence, clock=utcnow):
    operation = session.get(Operation, operation_id, with_for_update=True)
    require(operation is not None, "RESOURCE_NOT_ACCESSIBLE", 404)
    require(operation.status == "UNKNOWN" and operation.version == expected_version, "RECONCILIATION_STATE_CONFLICT")
    require(status in {"SUCCEEDED", "FAILED"} and bool(evidence.get("evidence")) and bool(evidence.get("external_reference")), "RECONCILIATION_EVIDENCE_REQUIRED", 422)
    return apply_operation_result(session, operation_id, status, evidence, actor_id, clock)


def add_stock(session, product_id, serial_numbers, source):
    require(session.get(Product, product_id) is not None, "RESOURCE_NOT_ACCESSIBLE", 404)
    require(bool(serial_numbers) and len(serial_numbers) <= 100 and len(set(serial_numbers)) == len(serial_numbers), "STOCK_SERIALS_INVALID", 422)
    require(bool(source) and len(source) <= 128 and all(isinstance(value, str) and 1 <= len(value) <= 128 for value in serial_numbers), "STOCK_SOURCE_INVALID", 422)
    rows = []
    for serial in serial_numbers:
        row = StockUnit(product_id=product_id, serial_number=serial, source=source, status="AVAILABLE")
        session.add(row)
        rows.append(row)
    session.flush()
    return {"created": len(rows), "stock_unit_ids": [row.id for row in rows]}


def order_dict(session, row):
    items = []
    for item in session.scalars(select(OrderItem).where(OrderItem.order_id == row.id)):
        product = session.get(Product, item.product_id)
        units = []
        for unit in session.scalars(select(OrderUnit).where(OrderUnit.order_item_id == item.id).order_by(OrderUnit.unit_index)):
            policy = session.get(PolicyVersion, unit.sale_policy_version_id) if unit.sale_policy_version_id else None
            active_case = session.scalar(select(AfterSalesCase).where(AfterSalesCase.order_unit_id == unit.id,
                                                    AfterSalesCase.status.not_in(CLOSED_RELEASED)))
            units.append({"id": unit.id, "unit_index": unit.unit_index, "serial_number": unit.serial_number,
                "allocated_paid_cents": unit.allocated_paid_cents, "policy": policy_dict(policy) if policy else None,
                "active_case_id": active_case.id if active_case else None, "replacement_for_case_id": unit.replacement_for_case_id})
        items.append({"id": item.id, "product_id": product.id, "sku": product.sku, "name": product.name,
                      "quantity": item.quantity, "net_goods_paid_cents": item.net_goods_paid_cents, "units": units})
    return {"id": row.id, "external_id": row.external_id, "paid_at": timestamp(row.paid_at),
            "delivered_at": timestamp(row.delivered_at), "currency": row.currency, "source": row.source,
            "shipping_address": row.shipping_address, "items": items}
