"""Bounded real-model fault acceptance; restore the original worker in finally.

This does not edit .env. It starts a separately named test worker whose only
credential override is a random relay token. The production provider/prompt code
is unchanged and the relay forwards to the explicitly configured real upstream.
"""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env", "--env-file", ".env.chatwoot"]


def run(command, **kwargs):
    try:
        return subprocess.run(command, cwd=ROOT, check=True, text=True, capture_output=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        # A command may carry the ephemeral relay credential. Never log argv.
        raise RuntimeError(f"Local container control failed with exit {exc.returncode}; credentials withheld") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-demo", action="store_true", required=True)
    parser.add_argument("--port", type=int, default=18491)
    args = parser.parse_args()
    if httpx.get("http://localhost:8000/api/v1/app-config").json().get("app_mode") != "demo":
        raise RuntimeError("Fault acceptance is restricted to the local demo")
    private = ROOT / ".local/model-relay.json"
    token = secrets.token_urlsafe(48)
    private.write_text(json.dumps({"token": token, "url": f"http://127.0.0.1:{args.port}"}), encoding="utf-8")
    worker_name = "aftersales-model-acceptance-" + uuid4().hex[:8]
    original_running = bool(run(COMPOSE + ["ps", "--status", "running", "-q", "worker"]).stdout.strip())
    server = None
    test_exit = 1
    restored = False
    try:
        server = subprocess.Popen([sys.executable, "scripts/model_test_relay.py", "--state", str(private), "--port", str(args.port)],
                                  cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        with httpx.Client(trust_env=False, headers={"Authorization": "Bearer " + token}) as client:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    if client.get(f"http://127.0.0.1:{args.port}/test/state").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.2)
            else:
                raise RuntimeError("Test relay did not become ready")
        probe = run(COMPOSE + ["exec", "-T", "api", "uv", "run", "--frozen", "--no-sync", "--no-dev",
                               "python", "-c", "import httpx,sys; r=httpx.get(sys.argv[1],headers={'Authorization':'Bearer '+sys.argv[2]},trust_env=False,timeout=5); assert r.status_code==200; print('container_relay_reachable')",
                               f"http://host.docker.internal:{args.port}/test/state", token])
        if probe.stdout.strip() != "container_relay_reachable":
            raise RuntimeError("Container-to-loopback relay preflight failed")
        if original_running:
            run(COMPOSE + ["stop", "worker"])
        run(COMPOSE + ["run", "--detach", "--no-deps", "--name", worker_name,
                       "-e", "AGENT_REPLY_MODE=model", "-e", f"MODEL_API_BASE=http://host.docker.internal:{args.port}/v1",
                       "-e", "MODEL_API_KEY=" + token, "worker"])
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js is required for the actual browser test")
        completed = subprocess.run([node, "tests/e2e/node_modules/@playwright/test/cli.js", "test", "--config",
                                    "tests/e2e/playwright.config.js", "model-fault.spec.js"], cwd=ROOT, timeout=240)
        test_exit = completed.returncode
    finally:
        subprocess.run(["docker", "rm", "-f", worker_name], cwd=ROOT, capture_output=True, check=False)
        try:
            if original_running:
                run(COMPOSE + ["start", "worker"])
            restored = True
        finally:
            if server:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
        print(json.dumps({"test_exit_code": test_exit, "original_worker_running_restored": restored,
                          "env_file_modified": False, "faults": "held actual upstream response, then explicit local HTTP503"}))
    raise SystemExit(test_exit)


if __name__ == "__main__":
    main()
