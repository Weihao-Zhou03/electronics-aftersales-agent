"""Shared encrypted-backup helpers. Never prints subprocess configuration or credentials."""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import subprocess
import tarfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

try:
    from .doctor import ROOT
except ImportError:
    from doctor import ROOT

COMPOSE = ["docker", "compose", "--env-file", ".env", "--env-file", ".env.chatwoot", "--profile", "demo"]
MAGIC = b"EAA-BACKUP-1"


def run(arguments, *, data=None):
    result = subprocess.run(arguments, input=data, cwd=ROOT, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"backup command failed ({arguments[0]}, exit {result.returncode}); credentials/output withheld")
    return result.stdout


def compose(*arguments, data=None):
    return run(COMPOSE + list(arguments), data=data)


def passphrase():
    value = os.getenv("BACKUP_PASSPHRASE", "")
    if len(value) < 20:
        raise ValueError("BACKUP_PASSPHRASE must be at least 20 characters; keep it separately from the backup")
    return value


def _key(password, salt):
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000).derive(password.encode())


def encrypt(data, password):
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    return MAGIC + salt + nonce + AESGCM(_key(password, salt)).encrypt(nonce, data, MAGIC)


def decrypt(data, password):
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 44:
        raise ValueError("invalid encrypted backup format")
    offset = len(MAGIC)
    salt, nonce = data[offset:offset + 16], data[offset + 16:offset + 28]
    return AESGCM(_key(password, salt)).decrypt(nonce, data[offset + 28:], MAGIC)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def sql(database, query):
    raw = compose("exec", "-T", "postgres", "psql", "-X", "-U", "postgres", "-d", database,
                  "-v", "ON_ERROR_STOP=1", "-At", "-c", query)
    return raw.decode().strip()


def snapshot(database):
    query = """
    SELECT COALESCE(json_object_agg(table_name,row_count),'{}'::json) FROM (
      SELECT schemaname||'.'||tablename table_name,
      ((xpath('/row/c/text()',query_to_xml(format('SELECT count(*) AS c FROM %I.%I',
        schemaname,tablename),true,true,'')))[1])::text::bigint row_count
      FROM pg_tables WHERE schemaname NOT LIKE 'pg_%' AND schemaname <> 'information_schema'
    ) counts;
    """
    result = {"table_counts": json.loads(sql(database, query)), "unknown_operations": []}
    if "public.operations" in result["table_counts"]:
        raw = sql(database, "SELECT COALESCE(json_agg(row_to_json(o)),'[]'::json) FROM "
                  "(SELECT id,status,idempotency_key,request_hash,provider_operation_id,result FROM operations "
                  "WHERE status='UNKNOWN' ORDER BY id) o")
        for operation in json.loads(raw):
            operation["result_hash"] = sha256(json.dumps(operation.pop("result"), sort_keys=True, ensure_ascii=False).encode())
            result["unknown_operations"].append(operation)
    return result


def tar_hashes(raw):
    values = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for item in archive.getmembers():
            if item.isdir():
                continue
            if not item.isfile():
                raise ValueError("backup archive contains unsupported link/special file")
            values[item.name.removeprefix("./")] = sha256(archive.extractfile(item).read())
    return values


def extract_tar(raw, target):
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = (target / member.name).resolve()
            if not path.is_relative_to(target) or member.issym() or member.islnk():
                raise ValueError("unsafe restore archive path/link")
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.extractfile(member).read())
            else:
                raise ValueError("unsupported restore archive member")


def safe_restore_db(name):
    if not re.fullmatch(r"eaa_restore_[a-f0-9]{12}_(aftersales|chatwoot|demo_provider)", name):
        raise ValueError("restore database must be a newly generated isolated target")
    return name


def config_bytes():
    config = {}
    for name in (".env", ".env.chatwoot", ".local/chatwoot-admin.json", ".local/chatwoot-bootstrap.json"):
        path = ROOT / name
        if path.is_file():
            config[name] = base64.b64encode(path.read_bytes()).decode()
    return json.dumps(config, sort_keys=True).encode()
