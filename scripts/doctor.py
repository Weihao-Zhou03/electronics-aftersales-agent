"""Read-only environment checks. Never starts Docker Desktop or prints secrets."""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def env_values(path: Path) -> dict[str, str]:
    """Read the project's simple KEY=value configuration without executing shell code."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return values


def run_check(label: str, command: list[str], env: dict[str, str], expected: str = "") -> bool:
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                text=True, errors="replace", timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL {label}: {type(exc).__name__}")
        return False
    ok = result.returncode == 0 and (not expected or result.stdout.strip() == expected)
    print(f"{'PASS' if ok else 'FAIL'} {label}: exit={result.returncode}")
    # Deliberately omit command stdout/stderr because configuration may contain credentials.
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-only", action="store_true")
    parser.add_argument("--example", action="store_true",
                        help="validate structure with disposable synthetic secrets, no runtime")
    args = parser.parse_args()
    config = env_values(ROOT / (".env.example" if args.example else ".env"))
    if not args.example:
        config.update(env_values(ROOT / ".env.chatwoot"))
    env = os.environ.copy()
    if args.example:
        for key in ("POSTGRES_ADMIN_PASSWORD", "AFTERSALES_DB_PASSWORD", "CHATWOOT_DB_PASSWORD",
                    "REDIS_PASSWORD", "CHATWOOT_SECRET_KEY_BASE", "SESSION_SECRET"):
            config[key] = secrets.token_hex(32)
    env.update(config)
    results = [run_check("Compose configuration (structure only)",
                         ["docker", "compose", "--env-file", os.devnull, "config", "--quiet"], env)]
    if args.example:
        print("INFO synthetic secrets used; no deployment or integration result is implied")
    if not args.compose_only:
        results.append(run_check("Linux Docker engine reachable",
                                 ["docker", "info", "--format", "{{.OSType}}"], env, "linux"))
        results.append(run_check("Compose service snapshot query",
                                 ["docker", "compose", "ps", "--status", "running"], env))
        print("INFO Docker startup is manual; this script never starts/restarts Docker Desktop")
    return 0 if all(results) else 2


if __name__ == "__main__":
    sys.exit(main())
