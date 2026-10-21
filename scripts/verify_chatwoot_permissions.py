"""Exercise the six runtime operations against a dedicated real test conversation.

This intentionally sends a public test reply/private note and toggles the test
conversation open then back to the bot. Use a dedicated M0 test conversation.
Credentials are read from .env.chatwoot/process environment and never printed.
"""

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from aftersales.chatwoot import ChatwootAdapter, ChatwootError


def sanitize(value):
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = key.lower()
            if any(part in lowered for part in ("token", "secret", "password", "source_id")):
                result[key] = "[REDACTED]"
            elif lowered in {"email", "name", "available_name", "display_name", "phone_number", "identifier", "avatar", "avatar_url", "thumbnail"}:
                result[key] = "[REDACTED]" if item is not None else None
            elif lowered in {"content", "processed_message_content"}:
                result[key] = "[M0 test message redacted]" if item else item
            else:
                result[key] = sanitize(item)
        return result
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation-id", required=True, type=int)
    parser.add_argument("--fixtures", type=Path, default=Path("tests/fixtures/real"))
    parser.add_argument("--output", type=Path, default=Path(".local/chatwoot-permission-results.json"))
    args = parser.parse_args()
    load_dotenv(".env.chatwoot")
    required = ["CHATWOOT_BASE_URL", "CHATWOOT_ACCOUNT_ID", "CHATWOOT_INBOX_ID", "CHATWOOT_BOT_ID", "CHATWOOT_BOT_ACCESS_TOKEN", "CHATWOOT_READ_ACCESS_TOKEN"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        parser.error("Missing " + ", ".join(missing))
    base = os.environ["CHATWOOT_BASE_URL"].rstrip("/")
    account_id = int(os.environ["CHATWOOT_ACCOUNT_ID"])
    adapter = ChatwootAdapter(
        base, account_id, int(os.environ["CHATWOOT_INBOX_ID"]),
        os.environ["CHATWOOT_BOT_ACCESS_TOKEN"], bot_id=int(os.environ["CHATWOOT_BOT_ID"]),
        read_access_token=os.environ["CHATWOOT_READ_ACCESS_TOKEN"],
    )
    results = {"source": "real Chatwoot HTTP API; dedicated M0 probe conversation; not Widget evidence",
               "captured_at": datetime.now(UTC).isoformat(), "conversation_id": args.conversation_id,
               "chatwoot_image": "chatwoot/chatwoot:v4.19.0-ce@sha256:6f296843097eee1eb8b6968027908fb4bb3de0b72236aa6a8f21386603266c91",
               "checks": {}}
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)
    results["code_commit"] = revision.stdout.strip() if revision.returncode == 0 else "uncommitted repository"
    results["working_tree_dirty"] = bool(dirty.stdout.strip())
    args.fixtures.mkdir(parents=True, exist_ok=True)
    try:
        conversation = adapter.get_conversation(args.conversation_id)
        results["checks"]["bot_get_conversation"] = "pass"
        (args.fixtures / "api-conversation.json").write_text(json.dumps(sanitize(conversation), ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        # Probe the bot's actual unsupported history endpoint separately.
        with httpx.Client(timeout=15) as client:
            response = client.get(f"{base}/api/v1/accounts/{account_id}/conversations/{args.conversation_id}/messages",
                                  headers={"Authorization": "Bearer " + os.environ["CHATWOOT_BOT_ACCESS_TOKEN"]})
        results["checks"]["bot_get_messages_status"] = response.status_code
        if response.status_code != 401:
            raise ChatwootError("Unexpected bot history permission result; inspect fixed-version compatibility")
        messages = adapter.get_messages(args.conversation_id)
        results["checks"]["dedicated_support_get_messages"] = "pass"
        (args.fixtures / "api-messages.json").write_text(json.dumps(sanitize(messages), ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        reply = adapter.send_reply(args.conversation_id, "M0 契约验证固定回复 · 演示环境")
        results["checks"]["bot_send_reply"] = "pass"
        results["checks"]["reply_sender_type"] = reply.get("sender", {}).get("type")
        note = adapter.append_private_note(args.conversation_id, "M0 契约验证私有备注（客户不可见性须用 Widget 另验）")
        results["checks"]["bot_append_private_note"] = "pass" if note.get("private") is True else "fail"
        (args.fixtures / "api-bot-reply.json").write_text(json.dumps(sanitize(reply), ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        (args.fixtures / "api-private-note.json").write_text(json.dumps(sanitize(note), ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        handoff = adapter.handoff(args.conversation_id)
        results["checks"]["bot_handoff_open"] = "pass" if handoff["status"] == "open" else "fail"
        returned = adapter.return_to_bot(args.conversation_id)
        results["checks"]["bot_explicit_return"] = "pass" if returned["status"] == "pending" and returned["meta"]["assignee_type"] == "AgentBot" else "fail"
    except ChatwootError as exc:
        results["failure"] = str(exc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    (args.fixtures / "api-provenance.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if "failure" in results or "fail" in results["checks"].values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
