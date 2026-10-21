"""Generate independent local demo secrets, preserving every existing configured value."""
import argparse
import secrets
from pathlib import Path

from doctor import ROOT, env_values

SECRET_KEYS = ("POSTGRES_ADMIN_PASSWORD", "AFTERSALES_DB_PASSWORD", "CHATWOOT_DB_PASSWORD",
               "DEMO_PROVIDER_DB_PASSWORD", "DEMO_PROVIDER_ACCESS_TOKEN", "DEMO_PROVIDER_ADMIN_TOKEN",
               "DEMO_PROVIDER_SIGNING_SECRET", "REDIS_PASSWORD", "CHATWOOT_SECRET_KEY_BASE", "SESSION_SECRET")


def configure(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".env"
    if not path.exists():
        path.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    values = env_values(path)
    added = 0
    for name in SECRET_KEYS:
        if values.get(name):
            continue
        value = secrets.token_hex(48)
        lines = text.splitlines()
        found = False
        for index, line in enumerate(lines):
            if line.startswith(name + "="):
                lines[index] = name + "=" + value
                found = True
        if not found:
            lines.append(name + "=" + value)
        text = "\n".join(lines) + "\n"
        added += 1
    path.write_text(text, encoding="utf-8")
    chatwoot = directory / ".env.chatwoot"
    if not chatwoot.exists():
        chatwoot.write_text("# Real Chatwoot bootstrap values will be saved here.\n", encoding="utf-8")
    for private in (path, chatwoot):
        try:
            private.chmod(0o600)
        except OSError:
            pass
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT)
    args = parser.parse_args()
    count = configure(args.directory.resolve())
    print(f"Demo configuration ready; generated {count} missing independent secrets, preserved existing values")
    print("No model key was generated or copied. Configure real Chatwoot via bootstrap; Docker Desktop remains manual.")


if __name__ == "__main__":
    main()
