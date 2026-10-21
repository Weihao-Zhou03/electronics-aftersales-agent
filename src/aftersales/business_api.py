"""Customer and staff API. Server sessions supply identity on every domain call."""
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select

from . import domain
from .auth import AuthContext, current_user, staff_user, write_user
from .business_models import AfterSalesCase, Operation, Order, PolicyVersion, Product, StockUnit

router = APIRouter(prefix="/api/v1")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Facts(Input):
    reason: str = Field(default="", max_length=2000)
    unused: StrictBool | None = None
    accessories_complete: StrictBool | None = None
    quality_issue: StrictBool | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)


class Address(Input):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=1, max_length=40)
    region: str = Field(min_length=1, max_length=160)
    detail: str = Field(min_length=1, max_length=300)
    country: Literal["CN"] | None = None


class Eligibility(Input):
    order_unit_id: str
    type: Literal["return", "exchange"]
    facts: Facts = Field(default_factory=Facts)


class CreateProposal(Eligibility):
    shipping_address: Address | None = None


class VersionInput(Input):
    expected_version: int = Field(ge=1)


class UpdateProposal(VersionInput):
    facts: Facts | None = None
    shipping_address: Address | None = None


class Confirm(VersionInput):
    summary_hash: str = Field(min_length=64, max_length=64)


class Review(VersionInput):
    decision: Literal["approve_return", "reject", "request_info"]
    reason: str = Field(min_length=1, max_length=1000)


class Shipment(VersionInput):
    carrier: str = Field(min_length=1, max_length=80)
    tracking_number: str = Field(min_length=1, max_length=128)


class Supplement(VersionInput):
    information: dict


class Receive(VersionInput):
    received_serial: str = Field(min_length=1, max_length=128)


class Inspect(VersionInput):
    passed: StrictBool
    verification: Facts


class Disposition(VersionInput):
    outcome: Literal["returned_to_customer", "customer_authorized_disposal"]
    evidence: dict


class Reconcile(VersionInput):
    status: Literal["SUCCEEDED", "FAILED"]
    evidence: dict


class ManualResult(Reconcile):
    obligation_hash: str = Field(min_length=64, max_length=64)


class StockReceipt(Input):
    product_id: str
    serial_numbers: list[str] = Field(min_length=1, max_length=100)
    source: str = Field(min_length=1, max_length=128)


def customer(user: AuthContext = Depends(current_user)):
    if user.role != "customer":
        raise HTTPException(403, "Customer role required")
    return user


def customer_write(user: AuthContext = Depends(write_user)):
    if user.role != "customer":
        raise HTTPException(403, "Customer role required")
    return user


def admin(user: AuthContext = Depends(staff_user)):
    if user.role != "admin":
        raise HTTPException(403, "Admin role required")
    return user


def read_staff(user: AuthContext = Depends(current_user)):
    if user.role not in {"support", "admin"}:
        raise HTTPException(403, "Staff role required")
    return user


def handled(action):
    try:
        return action()
    except domain.DomainError as error:
        raise HTTPException(error.status, {"code": error.code, "data": error.data}) from error


def write(request, user, scope, key, data, action):
    payload = data.model_dump(mode="json", exclude_none=True) if isinstance(data, BaseModel) else data
    return handled(lambda: domain.run_transaction(request.app.state.session_factory,
        lambda session: domain.idempotent(session, user.user_id, scope, key, payload, lambda: action(session))))


def config(request):
    settings = request.app.state.settings
    return {"app_mode": settings.app_mode, "fulfillment_mode": getattr(settings, "fulfillment_mode", "demo"),
            "environment_id": getattr(settings, "environment_id", "electronics-aftersales-demo")}


@router.get("/orders")
def orders(request: Request, user=Depends(customer)):
    with request.app.state.session_factory() as session:
        return [domain.order_dict(session, row) for row in session.scalars(select(Order).where(Order.customer_id == user.user_id).order_by(Order.delivered_at.desc()))]


@router.get("/orders/{order_id}")
def order_detail(order_id: str, request: Request, user=Depends(customer)):
    with request.app.state.session_factory() as session:
        row = session.scalar(select(Order).where(Order.id == order_id, Order.customer_id == user.user_id))
        if row is None:
            raise HTTPException(404, "Resource not accessible")
        return domain.order_dict(session, row)


@router.post("/eligibility/evaluate")
def eligibility(data: Eligibility, request: Request, user=Depends(customer_write)):
    with request.app.state.session_factory() as session:
        return handled(lambda: domain.evaluate_eligibility(session, user.user_id, data.order_unit_id, data.type,
                                                           data.facts.model_dump(exclude_none=True)))


@router.post("/proposals")
def proposals_create(data: CreateProposal, request: Request, user=Depends(customer_write), idempotency_key: str = Header(default="")):
    return write(request, user, "create_proposal", idempotency_key, data,
                 lambda session: domain.create_proposal(session, user.user_id, data.order_unit_id, data.type,
                    data.facts.model_dump(exclude_none=True), data.shipping_address.model_dump(exclude_none=True) if data.shipping_address else None))


@router.get("/proposals/{proposal_id}")
def proposal_detail(proposal_id: str, request: Request, user=Depends(customer)):
    with request.app.state.session_factory() as session:
        return handled(lambda: domain.proposal_dict(domain._proposal(session, proposal_id, user.user_id), session))


@router.patch("/proposals/{proposal_id}")
def proposal_update(proposal_id: str, data: UpdateProposal, request: Request, user=Depends(customer_write), idempotency_key: str = Header(default="")):
    return write(request, user, "proposal_update:" + proposal_id, idempotency_key, data,
                 lambda session: domain.update_proposal(session, user.user_id, proposal_id, data.expected_version,
                    data.facts.model_dump(exclude_none=True) if data.facts else None,
                    data.shipping_address.model_dump(exclude_none=True) if data.shipping_address else None))


@router.post("/proposals/{proposal_id}/confirm")
def proposal_confirm(proposal_id: str, data: Confirm, request: Request, user=Depends(customer_write), idempotency_key: str = Header(default="")):
    return write(request, user, "confirm:" + proposal_id, idempotency_key, data,
                 lambda session: domain.confirm_proposal(session, user.user_id, proposal_id, data.expected_version, data.summary_hash))


@router.get("/cases")
def cases(request: Request, user=Depends(customer)):
    with request.app.state.session_factory() as session:
        return [domain.case_dict(row) for row in session.scalars(select(AfterSalesCase).where(
            AfterSalesCase.customer_id == user.user_id).order_by(AfterSalesCase.accepted_at.desc()))]


@router.get("/cases/{case_id}")
def case_detail(case_id: str, request: Request, user=Depends(customer)):
    with request.app.state.session_factory() as session:
        return handled(lambda: domain.case_dict(domain._case(session, case_id, user.user_id), session))


@router.post("/cases/{case_id}/return-shipment")
def return_shipment(case_id: str, data: Shipment, request: Request, user=Depends(customer_write), idempotency_key: str = Header(default="")):
    return write(request, user, "return_shipment:" + case_id, idempotency_key, data,
                 lambda session: domain.record_return_shipment(session, user.user_id, case_id, data.expected_version, data.carrier, data.tracking_number))


@router.post("/cases/{case_id}/supplements")
def supplements(case_id: str, data: Supplement, request: Request, user=Depends(customer_write), idempotency_key: str = Header(default="")):
    return write(request, user, "supplement:" + case_id, idempotency_key, data,
                 lambda session: domain.supplement_case(session, user.user_id, case_id, data.expected_version, data.information))


@router.post("/cases/{case_id}/cancel")
def cancel(case_id: str, data: VersionInput, request: Request, user=Depends(customer_write), idempotency_key: str = Header(default="")):
    return write(request, user, "cancel:" + case_id, idempotency_key, data,
                 lambda session: domain.cancel_case(session, user.user_id, case_id, data.expected_version))


@router.get("/staff/cases")
def staff_cases(request: Request, status: str | None = None, type: str | None = None, user=Depends(read_staff)):
    with request.app.state.session_factory() as session:
        query = select(AfterSalesCase).order_by(AfterSalesCase.accepted_at.desc())
        if status:
            query = query.where(AfterSalesCase.status == status)
        if type:
            query = query.where(AfterSalesCase.type == type)
        return [domain.case_dict(row) for row in session.scalars(query)]


@router.get("/staff/cases/{case_id}")
def staff_case_detail(case_id: str, request: Request, user=Depends(read_staff)):
    with request.app.state.session_factory() as session:
        return handled(lambda: domain.case_dict(domain._case(session, case_id), session))


@router.post("/staff/cases/{case_id}/review")
def review(case_id: str, data: Review, request: Request, user=Depends(staff_user), idempotency_key: str = Header(default="")):
    return write(request, user, "review:" + case_id, idempotency_key, data,
                 lambda session: domain.review_case(session, user.user_id, case_id, data.expected_version, data.decision, data.reason))


@router.post("/staff/cases/{case_id}/receive")
def receive(case_id: str, data: Receive, request: Request, user=Depends(staff_user), idempotency_key: str = Header(default="")):
    return write(request, user, "receive:" + case_id, idempotency_key, data,
                 lambda session: domain.receive_case(session, user.user_id, case_id, data.expected_version, data.received_serial))


@router.post("/staff/cases/{case_id}/inspect")
def inspect(case_id: str, data: Inspect, request: Request, user=Depends(staff_user), idempotency_key: str = Header(default="")):
    return write(request, user, "inspect:" + case_id, idempotency_key, data,
                 lambda session: domain.inspect_case(session, user.user_id, case_id, data.expected_version, data.passed,
                                                      data.verification.model_dump(exclude_none=True), config(request)))


@router.post("/staff/cases/{case_id}/resume-stock")
def resume_stock(case_id: str, data: VersionInput, request: Request, user=Depends(staff_user), idempotency_key: str = Header(default="")):
    return write(request, user, "resume_stock:" + case_id, idempotency_key, data,
                 lambda session: domain.resume_stock(session, user.user_id, case_id, data.expected_version, config(request)))


@router.post("/staff/cases/{case_id}/disposition")
def disposition(case_id: str, data: Disposition, request: Request, user=Depends(staff_user), idempotency_key: str = Header(default="")):
    return write(request, user, "disposition:" + case_id, idempotency_key, data,
                 lambda session: domain.disposition_case(session, user.user_id, case_id, data.expected_version, data.outcome, data.evidence))


@router.get("/staff/operations")
def operations(request: Request, user=Depends(read_staff)):
    with request.app.state.session_factory() as session:
        return [domain.operation_dict(row) for row in session.scalars(select(Operation).order_by(Operation.available_at.desc()))]


@router.get("/staff/operations/{operation_id}")
def operation_detail(operation_id: str, request: Request, user=Depends(read_staff)):
    with request.app.state.session_factory() as session:
        row = session.get(Operation, operation_id)
        if row is None:
            raise HTTPException(404, "Resource not accessible")
        return domain.operation_dict(row)


@router.post("/staff/operations/{operation_id}/reconcile")
def reconcile(operation_id: str, data: Reconcile, request: Request, user=Depends(staff_user), idempotency_key: str = Header(default="")):
    return write(request, user, "reconcile:" + operation_id, idempotency_key, data,
                 lambda session: domain.reconcile_operation(session, user.user_id, operation_id, data.expected_version, data.status, data.evidence))


@router.post("/staff/operations/{operation_id}/record-manual-result")
def manual_result(operation_id: str, data: ManualResult, request: Request, user=Depends(staff_user), idempotency_key: str = Header(default="")):
    return write(request, user, "manual_result:" + operation_id, idempotency_key, data,
                 lambda session: domain.record_manual_result(session, user.user_id, operation_id, data.expected_version,
                    data.obligation_hash, data.status, data.evidence))


@router.get("/staff/inventory")
def inventory(request: Request, user=Depends(read_staff)):
    with request.app.state.session_factory() as session:
        result = []
        for product in session.scalars(select(Product).order_by(Product.sku)):
            rows = session.scalars(select(StockUnit).where(StockUnit.product_id == product.id)).all()
            result.append({"product_id": product.id, "sku": product.sku, "name": product.name,
                "available": sum(row.status == "AVAILABLE" for row in rows), "reserved": sum(row.status == "RESERVED" for row in rows),
                "dispatched": sum(row.status == "DISPATCHED" for row in rows),
                "units": [{"id": row.id, "serial_number": row.serial_number, "status": row.status, "source": row.source} for row in rows]})
        return result


@router.post("/admin/inventory/receipts")
def stock_receipt(data: StockReceipt, request: Request, user=Depends(admin), idempotency_key: str = Header(default="")):
    return write(request, user, "stock_receipt", idempotency_key, data,
                 lambda session: domain.add_stock(session, data.product_id, data.serial_numbers, data.source))


@router.get("/admin/policies")
def policies(request: Request, user=Depends(read_staff)):
    with request.app.state.session_factory() as session:
        return [domain.policy_dict(row) for row in session.scalars(select(PolicyVersion).order_by(PolicyVersion.published_at.desc()))]


@router.post("/admin/policies/validate")
def policy_validate(data: dict, user=Depends(admin)):
    return handled(lambda: domain.validate_policy(data))


@router.post("/admin/policies/publish")
def policy_publish(data: dict, request: Request, user=Depends(admin), idempotency_key: str = Header(default="")):
    return write(request, user, "policy_publish", idempotency_key, data, lambda session: domain.publish_policy(session, data))
