"""Full fresh Compose installation in an isolated project, volumes and loopback ports."""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx
from bootstrap_chatwoot import Bootstrap, write_local
from configure_demo import configure
from doctor import ROOT, env_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=int, default=18000)
    parser.add_argument("--chatwoot-port", type=int, default=13000)
    parser.add_argument("--provider-port", type=int, default=18100)
    args = parser.parse_args()
    if any(port < 1024 or port > 65535 for port in (args.api_port, args.chatwoot_port, args.provider_port)):
        raise ValueError("use unprivileged loopback ports")
    token = secrets.token_hex(5)
    project = "eaa-clean-" + token
    directory = ROOT / ".local" / project
    if directory.exists():
        raise ValueError("new clean-install directory must not exist")
    configure(directory)
    config = env_values(directory / ".env")
    config.update(APP_MODE="demo", FULFILLMENT_MODE="demo", ENVIRONMENT_ID="demo-" + project,
                  CHATWOOT_PUBLIC_URL=f"http://127.0.0.1:{args.chatwoot_port}",
                  CHATWOOT_BASE_URL=f"http://127.0.0.1:{args.chatwoot_port}",
                  ALLOWED_ORIGINS=f"http://127.0.0.1:{args.api_port}",
                  SAFE_FETCH_ALLOW_PRIVATE_NETWORK="true", AGENT_REPLY_MODE="fixed", MODEL_API_KEY="")
    write_local(directory / ".env", "".join(f"{key}={value}\n" for key, value in config.items()))
    image_id = subprocess.check_output(["docker", "image", "inspect", "electronics-aftersales-agent:m0",
                                        "--format", "{{.Id}}"], cwd=ROOT, text=True).strip()
    image_tag = "electronics-aftersales-clean:" + token
    subprocess.run(["docker", "image", "tag", "electronics-aftersales-agent:m0", image_tag], cwd=ROOT, check=True)
    override = directory / "compose.override.yaml"
    services = {name: f"    image: {image_tag}\n    build: !reset null\n    pull_policy: never\n" for name in (
        "api", "worker", "operations-worker", "migrate", "provider-migrate", "demo-provider")}
    for service, port, internal in (("api", args.api_port, 8000), ("chatwoot-rails", args.chatwoot_port, 3000),
                                    ("demo-provider", args.provider_port, 8100)):
        services[service] = services.get(service, "") + f"    ports: !override\n      - 127.0.0.1:{port}:{internal}\n"
    override.write_text("services:\n" + "".join(f"  {name}:\n{values}" for name, values in services.items()), encoding="utf-8")
    prefix = ["docker", "compose", "--project-name", project, "--env-file", str(directory / ".env"),
              "--env-file", str(directory / ".env.chatwoot"), "-f", str(ROOT / "compose.yaml"),
              "-f", str(override), "--profile", "demo"]
    report = {"source": "Fresh real Docker Compose project, new named volumes and separate loopback ports",
              "project": project, "created_at": datetime.now(UTC).isoformat(), "checks": {},
              "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "tested_worktree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip()),
              "uncommitted_infrastructure_harness": True,
              "primary_project_modified": False, "configuration_directory": str(directory)}
    hash_script = ("import hashlib,json; from pathlib import Path; "
        "print(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() "
        "for root in ['src','prompts','frontend/dist'] for p in sorted(Path(root).rglob('*')) "
        "if p.is_file() and '__pycache__' not in str(p)}))")
    report["image_product_file_sha256"] = json.loads(subprocess.check_output(
        ["docker", "run", "--rm", "--entrypoint", "python", image_tag, "-c", hash_script], cwd=ROOT, text=True))

    def run(arguments, *, data=None, env=None, timeout=300):
        result = subprocess.run(arguments, input=data, cwd=ROOT, env=env, capture_output=True,
                                timeout=timeout, check=False)
        if result.returncode:
            write_local(directory / "last-step-error.log", result.stdout.decode(errors="replace") + "\n" + result.stderr.decode(errors="replace"))
            raise RuntimeError(f"clean-install step failed exit={result.returncode}; private details in {directory / 'last-step-error.log'}")
        return result.stdout

    def compose(*arguments, **kwargs):
        return run(prefix + list(arguments), **kwargs)

    def wait_url(url):
        for _ in range(90):
            try:
                with httpx.Client(timeout=3, trust_env=False) as client:
                    if client.get(url).status_code == 200:
                        return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise RuntimeError("clean-install health endpoint did not become ready")

    started = False
    try:
        compose("config", "--quiet")
        report["image_id"] = image_id
        report["image_tag"] = image_tag
        compose("up", "-d", "--wait", "postgres", "redis")
        started = True
        print("Fresh isolated PostgreSQL and Redis healthy")
        compose("run", "--rm", "--no-deps", "chatwoot-rails", "bundle", "exec", "rails", "db:chatwoot_prepare")
        compose("run", "--rm", "--no-deps", "migrate")
        compose("run", "--rm", "--no-deps", "provider-migrate")
        report["checks"]["fresh_business_provider_chatwoot_migrations"] = "pass"
        compose("up", "-d", "--no-deps", "chatwoot-rails", "chatwoot-sidekiq")
        admin_password, reader_password = "M0a!" + secrets.token_hex(24), "M0b!" + secrets.token_hex(24)
        ruby = (ROOT / "scripts/initial_chatwoot_admin.rb").read_text(encoding="utf-8").replace("\r\n", "\n").encode()
        raw = compose("exec", "-T", "-e", "APP_MODE=demo", "-e", "CLEAN_ADMIN_PASSWORD=" + admin_password,
                      "-e", "CLEAN_READER_PASSWORD=" + reader_password, "chatwoot-rails", "bundle", "exec", "rails", "runner", "-", data=ruby)
        credentials = json.loads(next(line for line in reversed(raw.decode().splitlines()) if line.startswith("{")))
        write_local(directory / "initial-admin.json", json.dumps({**credentials, "admin_password": admin_password,
                                                                  "reader_password": reader_password}, indent=2))
        wait_url(config["CHATWOOT_PUBLIC_URL"] + "/api")
        bootstrap = Bootstrap(config["CHATWOOT_BASE_URL"], credentials["account_id"], credentials["admin_token"])
        website = f"http://127.0.0.1:{args.api_port}"
        state, values = bootstrap.provision("http://agent-api.internal:8000", website)
        second_state, _second_values = bootstrap.provision("http://agent-api.internal:8000", website, state)
        if state != second_state:
            raise ValueError("clean bootstrap was not idempotent")
        bootstrap.request("POST", "inbox_members", {"inbox_id": state["inbox_id"], "user_ids": [credentials["reader_id"]]})
        values["CHATWOOT_READ_ACCESS_TOKEN"] = credentials["reader_token"]
        write_local(directory / ".env.chatwoot", "".join(f"{key}={value}\n" for key, value in values.items()))
        write_local(directory / "chatwoot-bootstrap.json", json.dumps(state, indent=2))
        report["checks"]["real_bootstrap_idempotent"] = "pass"
        demo_password = "M0c!" + secrets.token_hex(24)
        write_local(directory / "demo-credentials.json", json.dumps({"password": demo_password,
                    "usernames": ["customer-a", "customer-b", "support", "admin"]}, indent=2))
        compose("run", "--rm", "--no-deps", "-e", "M0_DEMO_PASSWORD=" + demo_password,
                "api", "uv", "run", "--frozen", "--no-sync", "--no-dev", "python", "-m", "aftersales.demo_seed")
        compose("up", "-d", "--no-deps", "api", "worker", "operations-worker", "demo-provider")
        wait_url(website + "/api/v1/health/ready")
        wait_url(f"http://127.0.0.1:{args.provider_port}/health")
        report["checks"]["fresh_application_readiness"] = "pass"
        node = os.getenv("NODE_BIN") or shutil.which("node")
        if not node:
            report["checks"]["real_widget_roundtrip"] = "not_run_missing_node"
        else:
            env = os.environ.copy()
            env.update(SOURCE_ROOT=str(ROOT), CLEAN_API_URL=website, CLEAN_DEMO_PASSWORD=demo_password,
                       CLEAN_SCREENSHOT=str(directory / "widget.png"), CLEAN_WIDGET_REPORT=str(directory / "widget-report.json"))
            report["checks"]["real_widget_roundtrip"] = "fail"
            run([node, "scripts/clean_widget_smoke.mjs"], env=env, timeout=120)
            widget = json.loads((directory / "widget-report.json").read_text())
            report["checks"]["real_widget_roundtrip"] = "pass" if widget["fixed_reply_roundtrip"] == "pass" else "fail"
        report["passed"] = all(value == "pass" for value in report["checks"].values())
    finally:
        if started:
            compose("down", "--timeout", "20")
        report["stopped_after_test"] = True
        report.setdefault("passed", False)
        report["new_named_volumes_retained"] = True
        path = ROOT / "docs/evidence/clean-install.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Clean installation report: {path}; pass={report.get('passed', False)}")
    return 0 if report.get("passed") else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Clean installation failed: {type(exc).__name__}: {exc}; primary project was not reset")
        sys.exit(1)
