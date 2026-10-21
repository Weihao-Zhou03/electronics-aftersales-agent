"""Encrypted logical backups; quiesce project writers or explicitly select online snapshots."""
import argparse
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

from backup_support import (
    ROOT,
    compose,
    config_bytes,
    encrypt,
    passphrase,
    run,
    sha256,
    snapshot,
    sql,
    tar_hashes,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--online", action="store_true",
                        help="do not pause writers; per-database snapshots, not a cross-service consistency guarantee")
    args = parser.parse_args()
    password = passphrase()
    output = args.output or ROOT / ".local/backups" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4))
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    raw_services = compose("ps", "--status", "running", "--format", "json").decode().strip()
    services = json.loads(raw_services) if raw_services.startswith("[") else [json.loads(line) for line in raw_services.splitlines() if line]
    writers = sorted({item["Service"] for item in services} & {
        "api", "worker", "operations-worker", "demo-provider", "chatwoot-rails", "chatwoot-sidekiq"})
    manifest = {"format": "electronics-aftersales-backup-v1", "created_at": datetime.now(UTC).isoformat(),
                "consistency": "online_per_database" if args.online else "project_writers_quiesced",
                "paused_services": [] if args.online else writers, "files": {}}
    try:
        manifest["code_commit"] = run(["git", "rev-parse", "HEAD"]).decode().strip()
        manifest["worktree_dirty"] = bool(run(["git", "status", "--porcelain"]).strip())
    except RuntimeError:
        manifest["code_commit"] = "unavailable"
    paused = False

    def save(name, plain, **metadata):
        encrypted = encrypt(plain, password)
        (output / name).write_bytes(encrypted)
        manifest["files"][name] = {"encrypted_sha256": sha256(encrypted), "plaintext_sha256": sha256(plain), **metadata}

    try:
        if writers and not args.online:
            paused = True
            compose("stop", "--timeout", "30", *writers)
        for database in ("aftersales", "chatwoot", "demo_provider"):
            exists = sql("postgres", f"SELECT 1 FROM pg_database WHERE datname='{database}'")
            if not exists:
                continue
            print(f"Backing up PostgreSQL database: {database}")
            before = snapshot(database)
            raw = compose("exec", "-T", "postgres", "pg_dump", "-U", "postgres", "--format=custom",
                          "--no-owner", "--no-privileges", "--dbname", database)
            save(database + ".pgdump.enc", raw, kind="postgres", database=database, snapshot=before)
        for name, service, directory in (("attachments", "api", "/app/.local/attachments"),
                                          ("chatwoot-storage", "chatwoot-rails", "/app/storage")):
            raw = compose("run", "--rm", "--no-deps", "--entrypoint", "tar", service, "-C", directory, "-czf", "-", ".")
            save(name + ".tar.enc", raw, kind="files", file_hashes=tar_hashes(raw))
        save("configuration.json.enc", config_bytes(), kind="configuration")
        manifest_data = encrypt(json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2).encode(), password)
        (output / "manifest.enc").write_bytes(manifest_data)
        (output / "backup.json").write_text(json.dumps({"format": manifest["format"], "created_at": manifest["created_at"],
            "manifest_sha256": sha256(manifest_data), "encrypted": True}, indent=2), encoding="utf-8")
    finally:
        if paused:
            compose("start", *writers)
    print(f"Encrypted backup created: {output}")
    print(f"Consistency: {manifest['consistency']}; {len(manifest['files'])} encrypted artifacts; no secrets printed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Backup failed: {type(exc).__name__}: {exc}; local partial backup retained; no successful backup claimed")
        sys.exit(1)
