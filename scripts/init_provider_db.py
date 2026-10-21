"""Add provider DB to an existing local volume without changing passwords/data."""
import subprocess
import sys

from doctor import ROOT, env_values


def main():
    settings = env_values(ROOT / ".env")
    password = settings.get("DEMO_PROVIDER_DB_PASSWORD", "")
    if len(password) < 32:
        print("DEMO_PROVIDER_DB_PASSWORD is missing/too short; provider database not changed")
        return 2
    command = ["docker", "compose", "--env-file", ".env", "--env-file", ".env.chatwoot",
               "exec", "-T", "-e", "DEMO_PROVIDER_DB_PASSWORD=" + password, "postgres", "sh"]
    script = (ROOT / "infra/postgres/20-create-provider.sh").read_text().replace("\r\n", "\n").encode()
    # Bytes preserve Linux newlines even when launched in PowerShell.
    result = subprocess.run(command, input=script, cwd=ROOT, capture_output=True, check=False)
    print(f"Provider database initialization exit={result.returncode}; no credentials printed")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
