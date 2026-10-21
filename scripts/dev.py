"""Unified application startup, with explicit demo-provider profile selection."""
import argparse
import subprocess
import sys

from doctor import ROOT, env_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    config = env_values(ROOT / ".env")
    demo = config.get("APP_MODE", "demo") == "demo" and config.get("FULFILLMENT_MODE", "demo") == "demo"
    compose = ["docker", "compose", "--env-file", ".env", "--env-file", ".env.chatwoot"]

    def run(command):
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            raise SystemExit(result.returncode)

    # This contacts an already running Linux daemon; it never launches Docker Desktop.
    run(compose + ["up", "-d", "--wait", "postgres", "redis"])
    if demo:
        run([sys.executable, "scripts/init_provider_db.py"])
    run(compose + ["run", "--rm", "--no-deps", "chatwoot-rails", "bundle", "exec", "rails", "db:chatwoot_prepare"])
    run(compose + ["build", "api"])
    run(compose + ["run", "--rm", "--no-deps", "migrate"])
    if demo:
        run(compose + ["run", "--rm", "--no-deps", "provider-migrate"])
    if not args.prepare_only:
        services = ["api", "worker", "operations-worker", "chatwoot-rails", "chatwoot-sidekiq"]
        if demo:
            compose += ["--profile", "demo"]
            services.append("demo-provider")
        run(compose + ["up", "-d", "--no-deps", *services])
    print("Application preparation complete; providers selected from explicit environment mode")


if __name__ == "__main__":
    main()
