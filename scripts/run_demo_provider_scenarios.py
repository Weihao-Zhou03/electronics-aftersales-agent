"""Scenario B: role HTTP actions, actual response loss, one durable simulated refund."""
import argparse
import json
import subprocess
import time
from datetime import UTC, datetime

import httpx
from scenario_http import (
    ROOT,
    Actor,
    await_completed,
    compose,
    configuration,
    fresh_entries,
    inspect,
    operation_for_case,
    provider_count,
    ready_to_inspect,
    run_one_worker,
    sql,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-demo", action="store_true", required=True)
    parser.parse_args()
    config, password = configuration()
    if config.get("APP_MODE", "demo") != "demo" or config.get("FULFILLMENT_MODE", "demo") != "demo":
        raise ValueError("response-loss acceptance may mutate only an explicit demo environment")
    # New demo order fixture each run; do not overwrite the shared A/C model fixtures.
    entry = fresh_entries()[0]
    customer, staff = Actor(entry["username"], password), Actor("support", password)
    case = ready_to_inspect(entry, customer, staff)
    key = "refund:" + case["id"]
    base = config.get("DEMO_PROVIDER_URL", "http://127.0.0.1:8100").replace("localhost", "127.0.0.1").rstrip("/")
    with httpx.Client(timeout=10, trust_env=False) as client:
        result = client.put(base + "/admin/faults/" + key, json={"mode": "apply-then-drop"},
                            headers={"Authorization": "Bearer " + config["DEMO_PROVIDER_ADMIN_TOKEN"]})
        result.raise_for_status()
    compose("stop", "--timeout", "15", "operations-worker")
    try:
        inspect(case, staff)
        operation = operation_for_case(staff, case["id"])
        run_one_worker(operation["id"])
        unknown = staff.get("/staff/operations/" + operation["id"])
        assert unknown["status"] == "UNKNOWN"
        allocation = sql("aftersales", f"SELECT status FROM refund_allocations WHERE case_id='{case['id']}'")
        assert allocation == "RESERVED" and provider_count(key) == 1
    finally:
        compose("start", "operations-worker")
    completed = await_completed(staff, operation["id"])
    assert completed["idempotency_key"] == key and provider_count(key) == 1
    assert staff.get("/staff/cases/" + case["id"])["status"] == "COMPLETED"
    before_events = int(sql("aftersales", f"SELECT count(*) FROM webhook_inbox WHERE provider='demo-provider' AND payload->>'idempotency_key'='{key}'"))
    before_jobs = int(sql("aftersales", f"SELECT count(*) FROM business_jobs WHERE kind='reconcile_operation' AND payload->>'operation_id'='{operation['id']}'"))
    with httpx.Client(timeout=10, trust_env=False) as client:
        replay = client.post(base + "/admin/operations/" + key + "/callbacks",
            json={"copies": 2, "include_stale_processing": True},
            headers={"Authorization": "Bearer " + config["DEMO_PROVIDER_ADMIN_TOKEN"]})
        replay.raise_for_status()
        assert replay.json()["queued"] == 3
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        delivered = int(sql("demo_provider", f"SELECT count(*) FROM provider_callbacks c JOIN provider_operations o ON c.operation_id=o.id WHERE o.idempotency_key='{key}' AND c.status='DELIVERED'"))
        after_events = int(sql("aftersales", f"SELECT count(*) FROM webhook_inbox WHERE provider='demo-provider' AND payload->>'idempotency_key'='{key}'"))
        if delivered >= 4 and after_events == before_events + 2:
            break
        time.sleep(0.5)
    assert delivered >= 4 and after_events == before_events + 2
    after_jobs = int(sql("aftersales", f"SELECT count(*) FROM business_jobs WHERE kind='reconcile_operation' AND payload->>'operation_id'='{operation['id']}'"))
    assert after_jobs == before_jobs + 2
    assert staff.get("/staff/operations/" + operation["id"])["status"] == "SUCCEEDED"
    assert staff.get("/staff/cases/" + case["id"])["status"] == "COMPLETED" and provider_count(key) == 1
    report = {"source": "Real role-authorized HTTP customer/staff actions, actual PostgreSQL and independent provider HTTP", "captured_at": datetime.now(UTC).isoformat(),
        "scenario": "B", "case_id": case["id"], "operation_id": operation["id"], "idempotency_key": key,
        "checks": {"response_lost_after_commit_UNKNOWN": "pass", "refund_allocation_held": "pass",
                   "original_key_queried_and_settled": "pass", "provider_effect_count_one": "pass",
                   "real_signed_duplicate_and_stale_callbacks": "pass", "callback_event_and_job_deduplication": "pass",
                   "terminal_state_does_not_regress": "pass"}, "provider_effect_count": 1,
        "callback_replay": {"actual_HTTP_deliveries": 3, "new_unique_inbox_events": after_events-before_events,
                            "new_unique_reconcile_jobs": after_jobs-before_jobs},
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "uncommitted_test_harness": True,
        "final_status": completed["status"], "simulated": True}
    (ROOT / "docs/evidence/demo-B-response-loss.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Scenario B actual role HTTP response-loss recovery passed; one simulated provider refund")


if __name__ == "__main__":
    main()
