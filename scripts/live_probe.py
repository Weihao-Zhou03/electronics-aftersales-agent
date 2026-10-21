"""Probe actual running services; HTTP success is not the complete Widget acceptance."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from doctor import ROOT, env_values


def request(url: str, token: str = "", payload: dict | None = None) -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation-id", type=int)
    parser.add_argument("--send-private-note", action="store_true",
                        help="explicitly write one M0 probe private note to the chosen live conversation")
    args = parser.parse_args()
    if args.send_private_note and not args.conversation_id:
        parser.error("--send-private-note requires --conversation-id")
    config = env_values(ROOT / ".env")
    config.update(env_values(ROOT / ".env.chatwoot"))
    config.update(os.environ)
    api = config.get("APP_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    cw = config.get("CHATWOOT_PUBLIC_URL", "http://localhost:3000").rstrip("/")
    results = []
    try:
        for label, url in (("API readiness", f"{api}/api/v1/health/ready"),
                           ("Chatwoot Rails HTTP", f"{cw}/api")):
            status, _ = request(url)
            results.append(status == 200)
            print(f"{'PASS' if status == 200 else 'FAIL'} {label}: HTTP {status}")
        if args.conversation_id:
            account = int(config.get("CHATWOOT_ACCOUNT_ID", "0"))
            token = config.get("CHATWOOT_BOT_ACCESS_TOKEN", "")
            if account < 1 or not token:
                print("NOT RUN bot token permission: missing account/token")
                return 2
            base = f"{cw}/api/v1/accounts/{account}/conversations/{args.conversation_id}"
            for label, url in (("bot token get_conversation", base),
                               ("bot token get_messages", f"{base}/messages")):
                status, _ = request(url, token)
                results.append(status == 200)
                print(f"{'PASS' if status == 200 else 'FAIL'} {label}: HTTP {status}")
            if args.send_private_note:
                status, body = request(f"{base}/messages", token, {
                    "content": "M0 integration probe · private note", "message_type": "outgoing", "private": True})
                ok = status in (200, 201) and body.get("private") is True
                results.append(ok)
                print(f"{'PASS' if ok else 'FAIL'} live private-note API: HTTP {status}")
        print("NOT RUN by this probe: Widget visibility/identity, Sidekiq delivery, dedupe, takeover/return")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL real network probe: {type(exc).__name__}; no simulated fallback used")
        return 2
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
