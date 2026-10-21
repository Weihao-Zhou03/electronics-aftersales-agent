"""Restore encrypted data into NEW isolated databases and verify rows/UNKNOWN/files/config."""
import argparse
import base64
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

from backup_support import (
    ROOT,
    compose,
    decrypt,
    extract_tar,
    passphrase,
    safe_restore_db,
    sha256,
    snapshot,
    sql,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cleanup", action="store_true", help="drop only newly generated smoke-test databases after verification")
    args = parser.parse_args()
    password, source = passphrase(), args.backup.resolve()
    public = json.loads((source / "backup.json").read_text())
    encrypted_manifest = (source / "manifest.enc").read_bytes()
    if sha256(encrypted_manifest) != public["manifest_sha256"]:
        raise ValueError("encrypted manifest checksum mismatch")
    manifest = json.loads(decrypt(encrypted_manifest, password))
    if manifest["format"] != "electronics-aftersales-backup-v1":
        raise ValueError("unsupported backup version")
    token = secrets.token_hex(6)
    output = (args.output or ROOT / ".local" / ("restore-smoke-" + token)).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"format": "electronics-aftersales-restore-smoke-v1", "created_at": datetime.now(UTC).isoformat(),
              "backup_created_at": manifest["created_at"], "source_code_commit": manifest.get("code_commit"),
              "tested_worktree_dirty": manifest.get("worktree_dirty"),
              "consistency": manifest["consistency"], "databases": {}, "files": {}, "configuration_verified": False,
              "production_databases_modified": False, "cleanup_requested": args.cleanup}
    created = []
    try:
        for name, info in manifest["files"].items():
            source_file = (source / name).resolve()
            if not source_file.is_relative_to(source):
                raise ValueError("unsafe backup filename")
            encrypted = source_file.read_bytes()
            if sha256(encrypted) != info["encrypted_sha256"]:
                raise ValueError("encrypted artifact checksum mismatch")
            plain = decrypt(encrypted, password)
            if sha256(plain) != info["plaintext_sha256"]:
                raise ValueError("decrypted artifact checksum mismatch")
            if info["kind"] == "postgres":
                database = safe_restore_db("eaa_restore_" + token + "_" + info["database"])
                # A target collision is a hard error; never drop/reuse an existing database.
                role = info["database"]
                if role not in {"aftersales", "chatwoot", "demo_provider"}:
                    raise ValueError("unknown application restore role")
                sql("postgres", f'CREATE DATABASE "{database}" OWNER "{role}"')
                created.append(database)
                compose("exec", "-T", "postgres", "pg_restore", "-U", "postgres", "--exit-on-error",
                        "--no-owner", "--no-privileges", "--dbname", database, data=plain)
                # Grant only inside the newly created target. Never REASSIGN OWNED (which
                # could also change cluster-wide database ownership).
                sql(database, f"""DO $$ DECLARE s record; BEGIN
                  FOR s IN SELECT nspname FROM pg_namespace
                    WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' LOOP
                    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I',s.nspname,'{role}');
                    EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA %I TO %I',s.nspname,'{role}');
                    EXECUTE format('GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA %I TO %I',s.nspname,'{role}');
                    EXECUTE format('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA %I TO %I',s.nspname,'{role}');
                  END LOOP; END $$""")
                restored = snapshot(database)
                if restored != info["snapshot"]:
                    raise ValueError("restored table counts or UNKNOWN operation evidence differs")
                table = "after_sales_cases" if role == "aftersales" else "provider_operations" if role == "demo_provider" else "conversations"
                compose("exec", "-T", "postgres", "psql", "-X", "-U", role, "-d", database,
                        "-v", "ON_ERROR_STOP=1", "-At", "-c", f"SELECT count(*) FROM {table}")
                report["databases"][info["database"]] = {"restored_database": database, "verified": True,
                    "application_role_read_verified": True,
                    "table_count": len(restored["table_counts"]), "row_count": sum(restored["table_counts"].values()),
                    "unknown_operation_count": len(restored["unknown_operations"])}
            elif info["kind"] == "files":
                target = output / name.removesuffix(".tar.enc")
                extract_tar(plain, target)
                actual = {str(path.relative_to(target)).replace("\\", "/"): sha256(path.read_bytes())
                          for path in target.rglob("*") if path.is_file()}
                if actual != info["file_hashes"]:
                    raise ValueError("restored attachment/storage hashes differ")
                report["files"][target.name] = {"verified": True, "file_count": len(actual)}
            elif info["kind"] == "configuration":
                config = json.loads(plain)
                # Verify decryptability and shape without writing plaintext keys to disk.
                for config_name, encoded in config.items():
                    if config_name not in {".env", ".env.chatwoot", ".local/chatwoot-admin.json", ".local/chatwoot-bootstrap.json"}:
                        raise ValueError("unexpected secret/configuration backup member")
                    base64.b64decode(encoded, validate=True)
                report["configuration_verified"] = bool(config)
        report["passed"] = bool(report["databases"]) and report["configuration_verified"]
        if not report["passed"]:
            raise ValueError("required restore checks missing")
    finally:
        if args.cleanup:
            for database in created:
                safe_restore_db(database)
                sql("postgres", f'DROP DATABASE "{database}" WITH (FORCE)')
        report["temporary_databases_removed"] = args.cleanup
        (output / "restore-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Restore smoke passed: {output / 'restore-report.json'}")
    print("Verified row counts, UNKNOWN-operation evidence, storage hashes and encrypted configuration; source databases unchanged")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Restore smoke failed: {type(exc).__name__}: {exc}; no production restore performed")
        sys.exit(1)
