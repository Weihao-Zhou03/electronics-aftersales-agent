"""M0 configuration. No model credentials are required or loaded."""
import os
from dataclasses import dataclass, fields
from typing import Any


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg://aftersales:aftersales@localhost:5432/aftersales"
    app_mode: str = "demo"
    fulfillment_mode: str = "demo"
    environment_id: str = "electronics-aftersales-demo"
    chatwoot_instance_id: str = "local-chatwoot"
    chatwoot_base_url: str = "http://chatwoot-rails:3000"
    chatwoot_public_url: str = "http://localhost:3000"
    chatwoot_account_id: int = 1
    chatwoot_inbox_id: int = 1
    chatwoot_bot_id: int | None = None
    chatwoot_bot_access_token: str = ""
    chatwoot_read_access_token: str = ""
    chatwoot_bot_webhook_secret: str = ""
    chatwoot_account_webhook_secret: str = ""
    chatwoot_widget_identity_secret: str = ""
    chatwoot_widget_token: str = ""
    session_secret: str = ""
    session_cookie_secure: bool = False
    session_ttl_seconds: int = 3600
    chatwoot_binding_ttl_seconds: int = 3600
    allowed_origins: str = "http://localhost:8000"
    agent_enabled: bool = True
    fixed_reply_delay_seconds: float = 0.0
    fixed_reply_text: str = "演示环境 · 模拟订单与履约。M0 固定回复：已收到您的消息；当前仅验证聊天接入。"
    webhook_tolerance_seconds: int = 300
    worker_lease_seconds: int = 60
    worker_max_attempts: int = 5
    agent_reply_mode: str = "fixed"
    model_provider: str = ""
    model_name: str = ""
    model_api_base: str = ""
    model_api_key: str = ""
    model_timeout_seconds: float = 30.0
    model_max_output_tokens: int = 1200
    agent_max_model_calls: int = 6
    agent_max_tool_calls: int = 8
    agent_turn_timeout_seconds: float = 120.0
    agent_daily_run_limit: int = 150
    graph_database_url: str = ""
    graph_checkpoint_schema: str = "agent_checkpoints"
    embedding_provider: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimension: int = 512
    embedding_api_base: str = ""
    embedding_api_key: str = ""
    embedding_cache_dir: str = ".local/embedding-cache"
    attachment_storage_path: str = ".local/attachments"

    @classmethod
    def from_env(cls):
        defaults = cls()
        values: dict[str, Any] = {}
        for item in fields(cls):
            value = os.getenv(item.name.upper())
            if value is None:
                continue
            default = getattr(defaults, item.name)
            if item.name == "chatwoot_bot_id":
                values[item.name] = int(value) if value else None
            elif isinstance(default, bool):
                if value.lower() not in {"true", "false", "1", "0"}:
                    raise ValueError(f"{item.name.upper()} must be true/false")
                values[item.name] = value.lower() in {"true", "1"}
            elif isinstance(default, int):
                values[item.name] = int(value)
            elif isinstance(default, float):
                values[item.name] = float(value)
            else:
                values[item.name] = value
        settings = cls(**values)
        if settings.app_mode not in {"demo", "pilot"}:
            raise ValueError("APP_MODE must be demo or pilot")
        if settings.fulfillment_mode not in {"demo", "manual", "external"}:
            raise ValueError("FULFILLMENT_MODE must be demo, manual or external")
        if settings.app_mode == "pilot" and settings.fulfillment_mode == "demo":
            raise ValueError("pilot cannot use demo fulfillment")
        if settings.fulfillment_mode == "external":
            raise ValueError("external fulfillment adapter is not configured")
        if settings.fixed_reply_delay_seconds < 0 or settings.worker_lease_seconds < 5:
            raise ValueError("invalid worker delay/lease configuration")
        if settings.webhook_tolerance_seconds < 1:
            raise ValueError("WEBHOOK_TOLERANCE_SECONDS must be positive")
        if settings.agent_reply_mode not in {"fixed", "model"}:
            raise ValueError("AGENT_REPLY_MODE must be fixed or model")
        if settings.app_mode == "pilot" and settings.model_provider == "mock":
            raise ValueError("pilot cannot use MockProvider")
        if settings.agent_max_model_calls < 1 or settings.agent_max_tool_calls < 1 or settings.model_timeout_seconds <= 0:
            raise ValueError("invalid model budgets/timeouts")
        import re
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", settings.graph_checkpoint_schema):
            raise ValueError("GRAPH_CHECKPOINT_SCHEMA must be a safe PostgreSQL identifier")
        return settings

    def webhook_secret(self, channel: str) -> str:
        secret = self.chatwoot_bot_webhook_secret if channel == "agentbot" else self.chatwoot_account_webhook_secret
        others = [self.chatwoot_bot_access_token, self.chatwoot_read_access_token, self.chatwoot_widget_identity_secret,
                  self.chatwoot_account_webhook_secret if channel == "agentbot" else self.chatwoot_bot_webhook_secret]
        if not secret or secret in [value for value in others if value]:
            raise ValueError("distinct API, webhook channel and widget secrets must be configured")
        return secret
