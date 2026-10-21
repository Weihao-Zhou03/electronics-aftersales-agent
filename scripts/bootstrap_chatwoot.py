"""Provision the dedicated Website inbox and bot with an admin token.

Only this command reads CHATWOOT_ADMIN_ACCESS_TOKEN. Generated credentials are
written to an ignored local env file, never printed or used as runtime fallbacks.
No live Chatwoot response has been captured merely by implementing this command.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class BootstrapError(RuntimeError):
    pass


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise BootstrapError(f"Missing {name}")
    return value


def http_url(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise BootstrapError(f"{name} must be an HTTP(S) URL without credentials")
    if parsed.query or parsed.fragment:
        raise BootstrapError(f"{name} cannot contain a query or fragment")
    return value.rstrip("/")


class Bootstrap:
    """Admin-only, intentionally separate from the runtime adapter."""

    def __init__(self, base_url: str, account_id: int, token: str, client=None):
        self.base_url = http_url(base_url, "CHATWOOT_BASE_URL")
        self.account_id = int(account_id)
        if self.account_id <= 0 or not token:
            raise BootstrapError("Positive account ID and explicit admin token required")
        self.token = token
        self.client = client

    def request(self, method, path, body=None):
        kwargs = {"headers": {"Authorization": f"Bearer {self.token}"}, "timeout": 15, "follow_redirects": False}
        if body is not None:
            kwargs["json"] = body
        url = f"{self.base_url}/api/v1/accounts/{self.account_id}/{path}"
        try:
            if self.client is None:
                with httpx.Client() as client:
                    response = client.request(method, url, **kwargs)
            else:
                response = self.client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise BootstrapError(f"Chatwoot {method} {path.split('/')[0]} failed (status={status}); check access/network/version") from None

    @staticmethod
    def unique(items, predicate, label):
        matches = [item for item in items if predicate(item)]
        if len(matches) > 1:
            raise BootstrapError(f"Multiple {label} matches; resolve duplicates before retrying")
        return matches[0] if matches else None

    def provision(self, callback_base, website_url, state=None):
        callback_base = http_url(callback_base, "CHATWOOT_CALLBACK_BASE_URL")
        website_url = http_url(website_url, "CHATWOOT_WEBSITE_URL")
        state = state or {}
        if state and (state.get("base_url") != self.base_url or state.get("account_id") != self.account_id):
            raise BootstrapError("Saved bootstrap state belongs to another instance/account")
        bot_name = "electronics-aftersales-agent-m0"
        inbox_name = "electronics-aftersales-agent-website"
        bot_url = f"{callback_base}/integrations/chatwoot/agentbot"
        control_url = f"{callback_base}/integrations/chatwoot/control"

        bots = self.request("GET", "agent_bots")
        if not isinstance(bots, list):
            raise BootstrapError("Unexpected agent_bots response; compare fixed-version source")
        bot = self.unique(bots, lambda item: item.get("id") == state.get("bot_id") if state.get("bot_id") else item.get("name") == bot_name, "bot")
        if state.get("bot_id") and bot is None:
            raise BootstrapError("Saved bot no longer exists; inspect before removing bootstrap state")
        if bot is None:
            bot = self.request("POST", "agent_bots", {"name": bot_name, "outgoing_url": bot_url, "bot_type": 0})
        elif bot.get("name") != bot_name or bot.get("account_id") != self.account_id:
            raise BootstrapError("Saved bot does not match this project's dedicated account bot")
        if bot.get("outgoing_url") != bot_url:
            self.request("PATCH", f"agent_bots/{bot['id']}", {"outgoing_url": bot_url})
        bot = self.request("GET", f"agent_bots/{bot['id']}")

        inboxes = self.request("GET", "inboxes")
        if not isinstance(inboxes, dict) or not isinstance(inboxes.get("payload"), list):
            raise BootstrapError("Unexpected inboxes response; compare fixed-version source")
        inbox = self.unique(inboxes["payload"], lambda item: item.get("id") == state.get("inbox_id") if state.get("inbox_id") else item.get("name") == inbox_name, "inbox")
        if state.get("inbox_id") and inbox is None:
            raise BootstrapError("Saved inbox no longer exists; inspect before removing bootstrap state")
        if inbox is None:
            inbox = self.request("POST", "inboxes", {
                "name": inbox_name, "enable_auto_assignment": False,
                "channel": {"type": "web_widget", "website_url": website_url, "widget_color": "#2563eb", "hmac_mandatory": True},
            })
        elif inbox.get("name") != inbox_name or inbox.get("channel_type") != "Channel::WebWidget":
            raise BootstrapError("Existing inbox is not this project's dedicated Website inbox")
        self.request("PATCH", f"inboxes/{inbox['id']}", {
            "enable_auto_assignment": False,
            "channel": {"hmac_mandatory": True, "website_url": website_url},
        })
        inbox = self.request("GET", f"inboxes/{inbox['id']}")
        if inbox.get("hmac_mandatory") is not True or inbox.get("enable_auto_assignment") is not False:
            raise BootstrapError("Could not confirm mandatory identity validation and disabled auto assignment")
        # The exact fixed-version input is agent_bot, never agent_bot_id.
        self.request("POST", f"inboxes/{inbox['id']}/set_agent_bot", {"agent_bot": bot["id"]})
        bound = self.request("GET", f"inboxes/{inbox['id']}/agent_bot")
        if not isinstance(bound.get("agent_bot"), dict) or bound["agent_bot"].get("id") != bot["id"]:
            raise BootstrapError("Inbox bot binding was not confirmed")

        webhooks = self.request("GET", "webhooks")
        try:
            webhooks = webhooks["payload"]["webhooks"]
        except (KeyError, TypeError):
            raise BootstrapError("Unexpected webhooks response; compare fixed-version source") from None
        if not isinstance(webhooks, list):
            raise BootstrapError("Unexpected webhooks response")
        webhook = self.unique(webhooks, lambda item: item.get("id") == state.get("webhook_id") if state.get("webhook_id") else item.get("url") == control_url or item.get("name") == bot_name + "-control", "control webhook")
        if state.get("webhook_id") and webhook is None:
            raise BootstrapError("Saved webhook no longer exists; inspect before removing bootstrap state")
        config = {"name": bot_name + "-control", "url": control_url, "inbox_id": None, "subscriptions": ["conversation_status_changed", "conversation_updated"]}
        if webhook is None:
            result = self.request("POST", "webhooks", {"webhook": config})
        else:
            result = self.request("PATCH", f"webhooks/{webhook['id']}", {"webhook": config})
        try:
            webhook = result["payload"]["webhook"]
        except (KeyError, TypeError):
            raise BootstrapError("Webhook response missing payload.webhook") from None
        if webhook.get("url") != control_url or set(webhook.get("subscriptions", [])) != set(config["subscriptions"]):
            raise BootstrapError("Control event subscriptions were not confirmed")

        # These fields are independently generated by Chatwoot. Never invent a
        # local secret, or substitute an API token for an unavailable secret.
        values = {
            "CHATWOOT_BASE_URL": self.base_url,
            "CHATWOOT_ACCOUNT_ID": str(self.account_id),
            "CHATWOOT_INBOX_ID": str(inbox["id"]),
            "CHATWOOT_BOT_ID": str(bot["id"]),
            "CHATWOOT_BOT_ACCESS_TOKEN": bot.get("access_token"),
            "CHATWOOT_BOT_WEBHOOK_SECRET": bot.get("secret"),
            "CHATWOOT_ACCOUNT_WEBHOOK_SECRET": webhook.get("secret"),
            "CHATWOOT_WIDGET_TOKEN": inbox.get("website_token"),
            "CHATWOOT_WIDGET_IDENTITY_SECRET": inbox.get("hmac_token"),
        }
        missing = [key for key, value in values.items() if not isinstance(value, str) or not value.strip()]
        if missing:
            raise BootstrapError("Fixed-version response missing required fields: " + ", ".join(missing))
        secrets = [values[key] for key in ("CHATWOOT_BOT_ACCESS_TOKEN", "CHATWOOT_BOT_WEBHOOK_SECRET", "CHATWOOT_ACCOUNT_WEBHOOK_SECRET", "CHATWOOT_WIDGET_IDENTITY_SECRET")]
        if len(set(secrets)) != len(secrets):
            raise BootstrapError("Distinct API token, webhook secrets and Widget secret required")
        saved_state = {"base_url": self.base_url, "account_id": self.account_id, "bot_id": bot["id"], "inbox_id": inbox["id"], "webhook_id": webhook["id"]}
        return saved_state, values


def write_local(path: Path, value: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass  # Windows uses inherited user ACLs; keep this within the user's workspace.
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path(".local/chatwoot-bootstrap.json"))
    parser.add_argument("--output", type=Path, default=Path(".env.chatwoot"))
    args = parser.parse_args()
    try:
        state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {}
        bootstrap = Bootstrap(required("CHATWOOT_BASE_URL"), int(required("CHATWOOT_ACCOUNT_ID")), required("CHATWOOT_ADMIN_ACCESS_TOKEN"))
        saved, values = bootstrap.provision(required("CHATWOOT_CALLBACK_BASE_URL"), required("CHATWOOT_WEBSITE_URL"), state)
        # Optional dedicated non-admin user token is configured explicitly. This
        # does not use the admin token when bot history is unavailable.
        if os.getenv("CHATWOOT_READ_ACCESS_TOKEN"):
            read_token = required("CHATWOOT_READ_ACCESS_TOKEN")
            if read_token == bootstrap.token:
                raise BootstrapError("The runtime read token must not be the bootstrap admin token")
            values["CHATWOOT_READ_ACCESS_TOKEN"] = read_token
        # Chatwoot secrets/tokens contain no newlines; reject malformed env values.
        if any("\n" in value or "\r" in value for value in values.values()):
            raise BootstrapError("Response contains an invalid multiline configuration value")
        write_local(args.state, json.dumps(saved, indent=2) + "\n")
        write_local(args.output, "# Generated from real Chatwoot responses; keep private.\n" + "".join(f"{key}={value}\n" for key, value in values.items()))
        print(f"Provisioned account={saved['account_id']} inbox={saved['inbox_id']} bot={saved['bot_id']}; secrets saved locally to {args.output}")
        print("Real Widget round-trip, bot permissions, signature delivery and identity rejection still require M0 acceptance tests.")
    except (BootstrapError, ValueError, OSError) as exc:
        print(f"Bootstrap stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
