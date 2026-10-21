"""Explicit Responses/Chat-Completions protocols; mock use is never a fallback."""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ModelReply:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    def as_message(self):
        result: dict[str, Any] = {"role": "assistant", "content": self.text or None}
        if self.calls:
            result["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                "name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}} for c in self.calls]
        return result


class ModelProvider(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply: ...


def _arguments(raw):
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError()
        return parsed
    except (TypeError, ValueError) as exc:
        raise ModelUnavailable("Invalid model tool arguments") from exc


class HTTPModelProvider:
    def __init__(self, protocol: str, base_url: str, api_key: str, model: str,
                 timeout: float = 30, max_output_tokens: int = 1200, client=None):
        if protocol not in {"openai-responses", "chat-completions"}:
            raise ValueError("Select an explicitly supported model protocol")
        url = httpx.URL(base_url)
        if url.scheme not in {"http", "https"} or not url.host or url.username or url.password or url.query:
            raise ValueError("Invalid model base URL")
        if not api_key or not model:
            raise ModelUnavailable("MODEL_API_KEY and MODEL_NAME are required")
        self.protocol, self.base_url, self.key, self.model = protocol, base_url.rstrip("/"), api_key, model
        self.timeout, self.max_output_tokens, self.client = timeout, max_output_tokens, client

    @staticmethod
    def _responses_input(messages):
        result = []
        for message in messages:
            if message["role"] == "tool":
                result.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]})
                continue
            if message.get("content"):
                result.append({"role": message["role"], "content": message["content"]})
            for call in message.get("tool_calls", []):
                result.append({"type": "function_call", "call_id": call["id"],
                               "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
        return result

    def complete(self, messages, tools):
        if self.protocol == "openai-responses":
            endpoint = "/responses"
            payload = {"model": self.model, "input": self._responses_input(messages),
                       "tools": [{"type": "function", **tool["function"]} for tool in tools],
                       "max_output_tokens": self.max_output_tokens, "store": False,
                       "parallel_tool_calls": False}
        else:
            endpoint = "/chat/completions"
            payload = {"model": self.model, "messages": messages, "tools": tools,
                       "max_completion_tokens": self.max_output_tokens, "parallel_tool_calls": False}
        try:
            kwargs: dict[str, Any] = {"json": payload, "headers": {"Authorization": f"Bearer {self.key}"},
                      "timeout": self.timeout, "follow_redirects": False}
            if self.client:
                response = self.client.post(self.base_url + endpoint, **kwargs)
            else:
                with httpx.Client(trust_env=False) as client:
                    response = client.post(self.base_url + endpoint, **kwargs)
            response.raise_for_status()
            data = response.json()
            if self.protocol == "openai-responses":
                if data.get("status") not in {None, "completed"}:
                    raise ModelUnavailable("Model response incomplete")
                text = ""
                calls = []
                for item in data.get("output", []):
                    if item.get("type") == "message":
                        text += "".join(part.get("text", "") for part in item.get("content", []) if part.get("type") == "output_text")
                    elif item.get("type") == "function_call":
                        calls.append(ToolCall(item["call_id"], item["name"], _arguments(item["arguments"])))
                return ModelReply(text, calls, data.get("usage") or {})
            message = data["choices"][0]["message"]
            calls = [ToolCall(c["id"], c["function"]["name"], _arguments(c["function"]["arguments"]))
                     for c in message.get("tool_calls") or []]
            return ModelReply(message.get("content") or "", calls, data.get("usage") or {})
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise ModelUnavailable(f"Model request failed: {type(exc).__name__}") from None


class MockProvider:
    """Deterministic scripted responses; visibly marked and used only when selected."""
    def __init__(self, replies=None):
        self.replies = list(replies or [ModelReply("【MockProvider 测试回复】请在门户选择设备；确认后才会提交申请。")])
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if not self.replies:
            raise ModelUnavailable("Mock script exhausted")
        return self.replies.pop(0)


def configured_provider(settings):
    if settings.model_provider == "mock":
        if settings.app_mode != "demo":
            raise ModelUnavailable("MockProvider cannot run in pilot")
        return MockProvider()
    return HTTPModelProvider(settings.model_provider, settings.model_api_base, settings.model_api_key,
                             settings.model_name, settings.model_timeout_seconds,
                             settings.model_max_output_tokens)
