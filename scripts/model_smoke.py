"""Two real model requests validate tool calling and continuation, without business effects."""

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from aftersales.config import Settings
from aftersales.model_provider import configured_provider


def main():
    load_dotenv(".env")
    settings = Settings.from_env()
    if settings.model_provider == "mock":
        raise SystemExit("Real model smoke refuses MockProvider")
    provider = configured_provider(settings)
    messages = [{"role": "user", "content": "请调用health_ping，value为CPA_TOOL_OK；收到工具结果后只回答CPA_CONTINUATION_OK。"}]
    tools = [{"type": "function", "function": {"name": "health_ping", "description": "验证工具调用，不执行业务",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"],
                       "additionalProperties": False}, "strict": True}}]
    started = time.monotonic()
    first = provider.complete(messages, tools)
    assert len(first.calls) == 1 and first.calls[0].name == "health_ping" and first.calls[0].arguments == {"value": "CPA_TOOL_OK"}
    messages.extend([first.as_message(), {"role": "tool", "tool_call_id": first.calls[0].id,
                                         "content": json.dumps({"ok": True})}])
    second = provider.complete(messages, tools)
    assert second.text.strip() == "CPA_CONTINUATION_OK" and not second.calls
    usage = {key: sum(reply.usage.get(key, 0) for reply in [first, second])
             for key in ["input_tokens", "output_tokens", "total_tokens"]
             if any(key in reply.usage for reply in [first, second])}
    report = {"source": "Real configured model API; synthetic health tool; no business changes",
              "captured_at": datetime.now(UTC).isoformat(), "model": settings.model_name,
              "protocol": settings.model_provider, "api_base": settings.model_api_base,
              "checks": {"tool_call": "pass", "tool_continuation": "pass"}, "api_calls": 2,
              "usage": usage or None, "cost": None, "cost_reason": "No verified monetary price configured; local subscription proxy",
              "elapsed_seconds": round(time.monotonic() - started, 3),
              "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()}
    path = Path("docs/evidence/model-smoke.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"model": report["model"], "checks": report["checks"], "usage": usage,
                      "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
