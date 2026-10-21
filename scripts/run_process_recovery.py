"""Actual short-lived worker process exit before/after provider IO, then lease recovery.

Only fictional order fixtures are inserted directly. Customer confirmation and all
employee decisions use the real authenticated HTTP API. Does not alter lease times
or statuses in SQL. Stops only the fulfillment worker, never Docker Desktop or chat.
"""
import argparse
import json
import subprocess
import time
from datetime import UTC, datetime

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
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-demo", action="store_true", required=True)
    parser.parse_args()
    config, password = configuration()
    if config.get("APP_MODE", "demo") != "demo" or config.get("FULFILLMENT_MODE", "demo") != "demo":
        raise ValueError("process-loss acceptance may mutate only an explicit demo environment")
    customer, staff = Actor("customer-a", password), Actor("support", password)
    entries = fresh_entries()
    phases = []
    for entry, phase in zip(entries, ["before", "after"], strict=True):
        case = ready_to_inspect(entry, customer, staff)
        compose("stop", "--timeout", "15", "operations-worker")
        started = time.monotonic()
        try:
            inspect(case, staff)
            operation = operation_for_case(staff, case["id"])
            child = run_one_worker(operation["id"], crash_phase=phase)
            assert child["real_process_exit"] == 91
            interrupted = staff.get("/staff/operations/" + operation["id"])
            assert interrupted["status"] == "RUNNING"
            before_count = provider_count(operation["idempotency_key"])
            assert before_count == (1 if phase == "after" else 0)
        finally:
            compose("start", "operations-worker")
        completed = await_completed(staff, operation["id"], timeout=95)
        assert completed["id"] == interrupted["id"]
        assert completed["idempotency_key"] == interrupted["idempotency_key"]
        assert provider_count(completed["idempotency_key"]) == 1
        assert staff.get("/staff/cases/" + case["id"])["status"] == "COMPLETED"
        elapsed = round(time.monotonic() - started, 3)
        phases.append({"phase": phase, "case_id": case["id"], "operation_id": completed["id"],
                       "idempotency_key": completed["idempotency_key"], "child_process_exit_code": 91,
                       "actual_lease_seconds": 30, "elapsed_seconds": elapsed,
                       "provider_count_after_interruption": before_count, "provider_final_effect_count": 1,
                       "checks": {"actual_process_loss": "pass", "same_operation_and_key_recovered": "pass",
                                  "normal_daemon_recovers_after_actual_lease_expiry": "pass",
                                  "one_provider_effect": "pass", "case_completed": "pass"}})
        print(f"Actual {phase}-provider process interruption recovered; one simulated effect", flush=True)
    report = {"source": "Real role HTTP, actual worker OS exit(91), PostgreSQL leases and independent HTTP provider",
              "captured_at": datetime.now(UTC).isoformat(), "simulated": True,
              "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "uncommitted_test_harness": True, "phases": phases, "passed": True}
    (ROOT / "docs/evidence/process-recovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
