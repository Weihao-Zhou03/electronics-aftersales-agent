"""Explicit demo-only recovery fixture: real UNKNOWN operation plus private evidence bytes."""
import argparse
import hashlib
import io
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image
from sqlalchemy import select

from aftersales import domain
from aftersales.agent_models import EvidenceAttachment
from aftersales.auth import User
from aftersales.business_models import Operation, PolicyVersion, Product
from aftersales.config import Settings
from aftersales.db import create_session_factory, utcnow
from aftersales.demo_seed import import_order
from aftersales.operations_worker import OperationsWorker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-demo", action="store_true", required=True)
    parser.add_argument("--resume-operation")
    args = parser.parse_args()
    settings = Settings.from_env()
    if settings.app_mode != "demo" or settings.fulfillment_mode != "demo":
        raise ValueError("recovery fixture is demo-only")
    factory = create_session_factory(settings.database_url)
    if args.resume_operation:
        with factory.begin() as session:
            row = session.get(Operation, args.resume_operation, with_for_update=True)
            if row is None or row.environment_id != settings.environment_id or row.execution_mode != "demo":
                raise ValueError("probe operation scope mismatch")
            row.available_at = utcnow()
        print(json.dumps({"resumed_operation": args.resume_operation}))
        return
    image = io.BytesIO()
    Image.new("RGB", (4, 4), (90, 130, 180)).save(image, format="PNG")
    content = image.getvalue()
    storage_key = uuid4().hex + ".png"
    storage = Path(settings.attachment_storage_path)
    storage.mkdir(parents=True, exist_ok=True)
    (storage / storage_key).write_bytes(content)
    with factory.begin() as session:
        customer_id = session.scalar(select(User.id).where(User.username == "customer-a"))
        product_id = session.scalar(select(Product.id).where(Product.sku == "HP-001"))
        policy_id = session.scalar(select(PolicyVersion.id).where(PolicyVersion.version == "demo_policy_v1"))
        evidence = EvidenceAttachment(owner_id=customer_id, storage_key=storage_key, mime="image/png",
                                      size=len(content), sha256=hashlib.sha256(content).hexdigest())
        session.add(evidence)
        order = import_order(session, customer_id=customer_id, external_id="BACKUP-SMOKE-" + uuid4().hex,
                             product_id=product_id, quantity=1, paid_cents=12345, policy_id=policy_id,
                             delivered_at=utcnow() - timedelta(days=2))
        from aftersales.business_models import OrderItem, OrderUnit
        unit = session.scalar(select(OrderUnit).join(OrderItem).where(OrderItem.order_id == order.id))
        proposal = domain.create_proposal(session, customer_id, unit.id, "return", {
            "reason": "恢复演练 · 虚构演示订单", "unused": True, "accessories_complete": True,
            "attachment_ids": [evidence.id]})
        serial = unit.serial_number
    with factory.begin() as session:
        case = domain.confirm_proposal(session, customer_id, proposal["id"], proposal["version"], proposal["summary_hash"])
    with factory.begin() as session:
        case = domain.review_case(session, "backup-demo-probe", case["id"], case["version"], "approve_return", "恢复验证演示")
    with factory.begin() as session:
        case = domain.record_return_shipment(session, customer_id, case["id"], case["version"], "模拟寄回", "SIM-BACKUP-RETURN")
    with factory.begin() as session:
        case = domain.receive_case(session, "backup-demo-probe", case["id"], case["version"], serial)
    # Configure loss before creating the operation, so no background executor can win the race.
    key = "refund:" + case["id"]
    with httpx.Client(timeout=10, trust_env=False) as client:
        result = client.put(os.environ["DEMO_PROVIDER_URL"].rstrip("/") + "/admin/faults/" + key,
                            json={"mode": "apply-then-drop"}, headers={"Authorization": "Bearer " + os.environ["DEMO_PROVIDER_ADMIN_TOKEN"]})
        result.raise_for_status()
    with factory.begin() as session:
        domain.inspect_case(session, "backup-demo-probe", case["id"], case["version"], True,
                            {"reason": "恢复演练验收", "unused": True, "accessories_complete": True},
                            config={"app_mode": settings.app_mode, "fulfillment_mode": settings.fulfillment_mode,
                                    "environment_id": settings.environment_id})
    runner = OperationsWorker(factory, app_mode=settings.app_mode, environment_id=settings.environment_id,
                              demo_url=os.environ["DEMO_PROVIDER_URL"], demo_access_token=os.environ["DEMO_PROVIDER_ACCESS_TOKEN"])
    with factory() as session:
        target_id = session.scalar(select(Operation.id).where(Operation.idempotency_key == key))
    if not runner.run_once(target_id):
        raise ValueError("probe requires exclusive operations worker window")
    with factory.begin() as session:
        row = session.scalar(select(Operation).where(Operation.idempotency_key == key))
        if row.status != "UNKNOWN":
            raise ValueError("actual response-loss UNKNOWN was not observed")
        row.available_at = utcnow() + timedelta(days=1)
        print(json.dumps({"simulated": True, "operation_id": row.id, "case_id": row.case_id,
                          "status": row.status, "attachment_sha256": hashlib.sha256(content).hexdigest()}))


if __name__ == "__main__":
    main()
