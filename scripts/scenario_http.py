"""Role-authorized HTTP helpers for real demo/recovery acceptance, no route mocks."""
import json
import os
import subprocess
import time
from uuid import uuid4

import httpx
from backup_support import COMPOSE, ROOT
from doctor import env_values


def configuration():
    values = env_values(ROOT / ".env")
    password = os.getenv("M0_DEMO_PASSWORD") or values.get("M0_DEMO_PASSWORD")
    if not password:
        local = ROOT / ".local/demo-password.txt"
        password = local.read_text().strip() if local.exists() else ""
    if not password:
        raise ValueError("seeded demo password must be provided locally, not hardcoded")
    return values, password


class Actor:
    def __init__(self, username, password, *, base="http://127.0.0.1:8000", origin="http://localhost:8000"):
        self.client = httpx.Client(base_url=base, timeout=20, trust_env=False,
                                   headers={"Origin": origin})
        response = self.client.post("/api/v1/auth/login", json={"username": username, "password": password})
        if response.status_code != 200:
            raise RuntimeError(f"role login failed HTTP {response.status_code}; credentials withheld")
        self.client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

    def get(self, path):
        response = self.client.get("/api/v1" + path)
        if response.status_code != 200:
            raise RuntimeError(f"role read {path} HTTP {response.status_code}")
        return response.json()

    def post(self, path, body):
        response = self.client.post("/api/v1" + path, json=body,
                                    headers={"Idempotency-Key": str(uuid4())})
        if response.status_code not in (200, 201):
            raise RuntimeError(f"role action {path} HTTP {response.status_code}; body withheld")
        return response.json()


def ready_to_inspect(entry, customer, staff, *, kind="return"):
    customer.get("/orders/" + entry["order_id"])
    proposal = customer.post("/proposals", {"order_unit_id": entry["unit_id"], "type": kind,
        "facts": {"reason": "真实恢复演练 · 模拟售后", "unused": True, "accessories_complete": True, "quality_issue": True}})
    case = customer.post(f"/proposals/{proposal['id']}/confirm", {
        "expected_version": proposal["version"], "summary_hash": proposal["summary_hash"]})
    case = staff.post(f"/staff/cases/{case['id']}/review", {
        "expected_version": case["version"], "decision": "approve_return", "reason": "同意寄回演示"})
    case = customer.post(f"/cases/{case['id']}/return-shipment", {
        "expected_version": case["version"], "carrier": "模拟寄回", "tracking_number": "SIM-RETURN-" + uuid4().hex[:12]})
    return staff.post(f"/staff/cases/{case['id']}/receive", {
        "expected_version": case["version"], "received_serial": entry["serial"]})


def inspect(case, staff):
    return staff.post(f"/staff/cases/{case['id']}/inspect", {"expected_version": case["version"], "passed": True,
        "verification": {"reason": "员工真实 API 验收通过", "unused": True, "accessories_complete": True, "quality_issue": True}})


def operation_for_case(staff, case_id):
    matches = [row for row in staff.get("/staff/operations") if row["case_id"] == case_id]
    if len(matches) != 1:
        raise ValueError("expected one immutable operation for the confirmed case")
    return matches[0]


def await_completed(staff, operation_id, *, timeout=90):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = staff.get("/staff/operations/" + operation_id)
        if value["status"] == "SUCCEEDED":
            return value
        time.sleep(0.5)
    raise RuntimeError("original operation did not reconcile within the real lease/retry window")


def compose(*args, data=None, expected=0):
    result = subprocess.run(COMPOSE + list(args), cwd=ROOT, input=data, capture_output=True, check=False)
    if result.returncode != expected:
        private = ROOT / ".local/scenario-last-error.log"
        private.parent.mkdir(exist_ok=True)
        private.write_bytes(result.stdout + b"\n" + result.stderr)
        raise RuntimeError(f"scenario container command exit={result.returncode}; private output withheld")
    return result.stdout


def sql(database, query):
    raw = compose("exec", "-T", "postgres", "psql", "-X", "-U", "postgres", "-d", database,
                  "-v", "ON_ERROR_STOP=1", "-At", "-c", query)
    return raw.decode().strip()


def provider_count(key):
    # Keys below are generated UUID-based business obligation keys, never user SQL input.
    if not key.startswith("refund:") or any(char not in "refund:0123456789abcdef-" for char in key):
        raise ValueError("unexpected demo obligation key")
    return int(sql("demo_provider", f"SELECT count(*) FROM provider_operations WHERE idempotency_key='{key}'"))


def fresh_entries():
    """Fixture-only seed; every business transition after this uses authorized HTTP."""
    source = (ROOT / "scripts/seed_ui_demo.py").read_bytes()
    result = json.loads(compose("exec", "-T", "api", "uv", "run", "--frozen", "--no-sync", "--no-dev",
                                "python", "-", data=source).decode().strip().splitlines()[-1])
    return [{"username": "customer-a", "order_id": result["order_id"], "unit_id": unit["id"],
             "serial": unit["serial"]} for unit in result["units"]]


def run_one_worker(operation_id, *, crash_phase=None):
    source = """import json,os,sys
from aftersales.config import Settings
from aftersales.db import create_session_factory
from aftersales.auth import User
from aftersales.agent_models import AgentRun
from aftersales.operations_worker import OperationsWorker
s=Settings.from_env()
w=OperationsWorker(create_session_factory(s.database_url),app_mode=s.app_mode,environment_id=s.environment_id,
 demo_url=os.environ['DEMO_PROVIDER_URL'],demo_access_token=os.environ['DEMO_PROVIDER_ACCESS_TOKEN'],lease_seconds=30)
operation_id=sys.argv[1]
phase=sys.argv[2]
if phase=='once':
 assert w.run_once(operation_id)
 print(json.dumps({'once':True}),flush=True)
else:
 claim=w._claim(operation_id)
 assert claim is not None and claim['id']==operation_id
 if phase=='after':
  provider=w._provider(claim)
  receipt=provider.submit(claim['kind'],claim['request_snapshot'],claim['idempotency_key'])
  assert receipt.status=='SUCCEEDED'
 print(json.dumps({'claimed_operation_id':claim['id'],'idempotency_key':claim['idempotency_key'],'phase':phase,'real_process_exit':91}),flush=True)
 os._exit(91)
"""
    mount = str(ROOT / "src/aftersales/operations_worker.py") + ":/app/src/aftersales/operations_worker.py:ro"
    raw = compose("run", "--rm", "-T", "--no-deps", "-v", mount, "operations-worker", "uv", "run",
                  "--frozen", "--no-sync", "--no-dev", "python", "-", operation_id,
                  crash_phase or "once", data=source.encode(), expected=91 if crash_phase else 0)
    return json.loads(raw.decode().strip().splitlines()[-1])
