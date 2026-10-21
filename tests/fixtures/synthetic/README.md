# 合成 fixtures

此目录及 `tests/test_adapter.py`、`tests/test_core.py` 中的事件/HTTP 响应是开发人员按固定版本官方源码手写的合成数据；`httpx.MockTransport` 不访问 Chatwoot。它们验证本地防护与契约处理，不证明真实网络、权限、Widget 或 Sidekiq 已通过。

源码核查路径和实际待测项见 `docs/chatwoot-source-notes.md`。真实捕获必须另存 `../real/` 并提供来源元数据、脱敏说明及验证证据。
