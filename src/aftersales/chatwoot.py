"""Restricted runtime API. Admin provisioning lives in scripts/bootstrap_chatwoot.py.

Contracts are checked against upstream v4.19.0 source; live compatibility is a
separate acceptance gate. No token fallback or automatic HTTP mutation retries.
"""

from typing import Any

import httpx


class ChatwootError(RuntimeError):
    """The remote state/action could not be confirmed. Stop automatic work."""


class ChatwootScopeError(ChatwootError):
    """The conversation is outside the configured instance/account/inbox."""


class ChatwootOwnershipError(ChatwootError):
    """The conversation cannot be confirmed as available to this bot."""


class ChatwootAdapter:
    def __init__(
        self,
        base_url: str,
        account_id: int,
        inbox_id: int,
        access_token: str,
        bot_id: int | None = None,
        timeout: float = 10,
        client: httpx.Client | None = None,
        read_access_token: str | None = None,
    ) -> None:
        url = httpx.URL(base_url)
        if url.scheme not in {"http", "https"} or not url.host or url.username or url.password:
            raise ValueError("CHATWOOT_BASE_URL must be an HTTP(S) URL without credentials")
        if url.query or url.fragment:
            raise ValueError("CHATWOOT_BASE_URL cannot contain a query or fragment")
        if not access_token or not access_token.strip():
            raise ValueError("An explicit runtime access token is required")
        self._base_url = str(url).rstrip("/")
        self._account_id = self._positive_id(account_id)
        self._inbox_id = self._positive_id(inbox_id)
        self._bot_id = self._positive_id(bot_id) if bot_id is not None else None
        self._token = access_token
        self._timeout = timeout
        self._client = client
        if read_access_token is not None and not read_access_token.strip():
            raise ValueError("Explicit read token must not be blank")
        self._read_token = read_access_token

    @staticmethod
    def _positive_id(value: Any) -> int:
        if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
            raise ValueError("IDs must be positive integers")
        return int(value)

    def _request(self, method: str, path: str, body: dict | None = None, *, access_token: str | None = None) -> dict:
        url = f"{self._base_url}/api/v1/accounts/{self._account_id}/{path}"
        kwargs: dict[str, Any] = {
            "headers": {"Authorization": f"Bearer {access_token or self._token}"},
            "timeout": self._timeout,
            "follow_redirects": False,
        }
        if body is not None:
            kwargs["json"] = body
        try:
            if self._client is None:
                with httpx.Client() as client:
                    response = client.request(method, url, **kwargs)
            else:
                response = self._client.request(method, url, **kwargs)
            response.raise_for_status()
            data = response.json() if response.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            # Do not include response bodies, credentials or customer content in errors.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise ChatwootError(f"Chatwoot {method} failed (status={status}); state unknown") from None
        if not isinstance(data, dict):
            raise ChatwootError("Unexpected Chatwoot response shape")
        return data

    def _conversation_path(self, conversation_id: int) -> str:
        return f"conversations/{self._positive_id(conversation_id)}"

    def get_conversation(self, conversation_id: int) -> dict:
        conversation = self._request("GET", self._conversation_path(conversation_id))
        # v4.19.0 GET has these top-level fields; missing fields fail closed.
        if (
            conversation.get("account_id") != self._account_id
            or conversation.get("inbox_id") != self._inbox_id
            or conversation.get("id") != self._positive_id(conversation_id)
        ):
            raise ChatwootScopeError("Conversation scope does not match the configured account/inbox")
        return conversation

    def get_messages(self, conversation_id: int) -> dict:
        self.get_conversation(conversation_id)
        # Upstream bot token allow-list excludes index; a 401 is surfaced as a
        # compatibility failure, never retried with the bootstrap admin token.
        return self._request("GET", f"{self._conversation_path(conversation_id)}/messages", access_token=self._read_token)

    def _require_bot_ownership(self, conversation: dict) -> None:
        if conversation.get("status") != "pending":
            raise ChatwootOwnershipError("Conversation is not pending")
        meta = conversation.get("meta")
        if not isinstance(meta, dict):
            raise ChatwootOwnershipError("Missing verified assignment metadata")
        assignee = meta.get("assignee")
        owner_type = meta.get("assignee_type")
        if assignee is None and owner_type is None:
            return  # Explicit API shape for an unassigned pending conversation.
        if (
            owner_type != "AgentBot"
            or not isinstance(assignee, dict)
            or self._bot_id is None
            or assignee.get("id") != self._bot_id
        ):
            raise ChatwootOwnershipError("Human, unknown or another bot owns the conversation")

    @staticmethod
    def _content(content: str) -> str:
        if not isinstance(content, str) or not content.strip() or len(content) > 10000:
            raise ValueError("Content must contain 1 to 10000 characters")
        return content

    def send_reply(self, conversation_id: int, content: str) -> dict:
        content = self._content(content)
        self._require_bot_ownership(self.get_conversation(conversation_id))
        return self._request(
            "POST", f"{self._conversation_path(conversation_id)}/messages",
            {"content": content, "message_type": "outgoing", "private": False},
        )

    def append_private_note(self, conversation_id: int, content: str) -> dict:
        content = self._content(content)
        self.get_conversation(conversation_id)
        return self._request(
            "POST", f"{self._conversation_path(conversation_id)}/messages",
            {"content": content, "message_type": "outgoing", "private": True},
        )

    def handoff(self, conversation_id: int) -> dict:
        self.get_conversation(conversation_id)
        self._request("POST", f"{self._conversation_path(conversation_id)}/toggle_status", {"status": "open"})
        result = self.get_conversation(conversation_id)
        if result.get("status") != "open":
            raise ChatwootOwnershipError("Handoff state was not confirmed")
        return result

    def return_to_bot(self, conversation_id: int) -> dict:
        """Caller must already have persisted an explicit employee authorization.

        v4.19.0 AssignmentService clears the User and sets pending atomically for
        AgentBot assignments. A plain toggle to pending could retain a human.
        """
        self.get_conversation(conversation_id)
        if self._bot_id is None:
            raise ChatwootOwnershipError("An explicit bot ID is required to return the conversation")
        self._request(
            "POST", f"{self._conversation_path(conversation_id)}/assignments",
            {"assignee_id": self._bot_id, "assignee_type": "AgentBot"},
        )
        result = self.get_conversation(conversation_id)
        self._require_bot_ownership(result)
        if result["meta"].get("assignee_type") != "AgentBot":
            raise ChatwootOwnershipError("Bot assignment was not confirmed")
        return result
