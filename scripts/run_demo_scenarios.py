"""Reproduce SPEC15.2 A/B/C using fresh demo inputs and real role/API flows.

Requires actual configured model access; this consumes its quota. No answer,
permission, business transition or provider receipt is mocked. Only fictional
initial input creation writes SQL. Results remain in docs/evidence; old data stays.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env", "--env-file", ".env.chatwoot"]


def browser(node, filename):
    subprocess.run([node, "tests/e2e/node_modules/@playwright/test/cli.js", "test", "--config",
                    "tests/e2e/playwright.config.js", filename], cwd=ROOT, check=True, timeout=330)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-demo", action="store_true", required=True)
    args = parser.parse_args()
    if not args.execute_demo:
        raise RuntimeError("Explicit demo execution is required")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node and tests/e2e dependencies/Chromium are required")
    if not os.getenv("M0_DEMO_PASSWORD") and not (ROOT / ".local/demo-password.txt").is_file():
        raise RuntimeError("Provide the existing local demo password; never replace customer hashes for acceptance")
    current = subprocess.run(COMPOSE + ["exec", "-T", "worker", "printenv", "AGENT_REPLY_MODE", "MODEL_PROVIDER", "MODEL_NAME"],
                             cwd=ROOT, check=True, capture_output=True, text=True)
    mode = current.stdout.strip().splitlines()
    if (len(mode) != 3 or mode[0] != "model" or mode[1] not in {"openai-responses", "chat-completions"}
            or not mode[2].strip()):
        raise RuntimeError("Enable the configured real model worker before running A/B/C")
    inputs = subprocess.run(COMPOSE + ["exec", "-T", "api", "uv", "run", "--frozen", "--no-sync", "--no-dev",
                                      "python", "scripts/seed_scenario_demo.py"], cwd=ROOT, check=True,
                            capture_output=True, text=True)
    initial = json.loads(inputs.stdout.strip().splitlines()[-1])
    private = ROOT / ".local/scenario-seed.json"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text(json.dumps(initial), encoding="utf-8")
    browser(node, "model.spec.js")
    subprocess.run([sys.executable, "scripts/run_demo_provider_scenarios.py", "--execute-demo"], cwd=ROOT, check=True, timeout=180)
    browser(node, "concurrency.spec.js")
    subprocess.run([sys.executable, "scripts/run_model_fault_acceptance.py", "--execute-demo"],
                   cwd=ROOT, check=True, timeout=300)
    print("Actual A/B/C and model-unavailable portal acceptance completed; inspect docs/evidence for scope and results.")


if __name__ == "__main__":
    main()
