"""M0 configuration. No model credentials are required or loaded."""
import os
from dataclasses import dataclass, fields
from typing import Any


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg://aftersales:aftersales@localhost:5432/aftersales"
    app_mode: str = "demo"
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
        if settings.app_mode != "demo":
            raise ValueError("M0 only supports explicit APP_MODE=demo")
        if settings.fixed_reply_delay_seconds < 0 or settings.worker_lease_seconds < 5:
            raise ValueError("invalid worker delay/lease configuration")
        if settings.webhook_tolerance_seconds < 1:
            raise ValueError("WEBHOOK_TOLERANCE_SECONDS must be positive")
        return settings

    def webhook_secret(self, channel: str) -> str:
        secret = self.chatwoot_bot_webhook_secret if channel == "agentbot" else self.chatwoot_account_webhook_secret
        others = [self.chatwoot_bot_access_token, self.chatwoot_read_access_token, self.chatwoot_widget_identity_secret,
                  self.chatwoot_account_webhook_secret if channel == "agentbot" else self.chatwoot_bot_webhook_secret]
        if not secret or secret in [value for value in others if value]:
            raise ValueError("distinct API, webhook channel and widget secrets must be configured")
        return secret
