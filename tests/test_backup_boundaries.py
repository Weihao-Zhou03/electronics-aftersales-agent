"""Security boundaries of portable encrypted backup files and isolated restores."""
import io
import tarfile

import pytest
from cryptography.exceptions import InvalidTag

from scripts.backup_support import decrypt, encrypt, extract_tar, safe_restore_db


def test_backups_reject_wrong_key_tampering_and_do_not_contain_secret_plaintext():
    payload = b"MODEL_API_KEY=example-sensitive-test-value"
    password = "isolated-test-passphrase-that-is-not-a-user-credential"
    encrypted = encrypt(payload, password)
    assert payload not in encrypted
    assert decrypt(encrypted, password) == payload
    with pytest.raises(InvalidTag):
        decrypt(encrypted, password + "wrong")
    with pytest.raises(InvalidTag):
        decrypt(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), password)


def test_restore_rejects_archive_path_escape_and_existing_database_names(tmp_path):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        item = tarfile.TarInfo("../escaped-secret")
        item.size = 4
        archive.addfile(item, io.BytesIO(b"test"))
    with pytest.raises(ValueError, match="unsafe"):
        extract_tar(raw.getvalue(), tmp_path / "restore")
    assert not (tmp_path / "escaped-secret").exists()
    for database in ("aftersales", "chatwoot", "postgres", "eaa_restore_existing_aftersales"):
        with pytest.raises(ValueError):
            safe_restore_db(database)
    assert safe_restore_db("eaa_restore_012345abcdef_aftersales").endswith("_aftersales")
