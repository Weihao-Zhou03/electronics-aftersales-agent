# 真实 Chatwoot fixtures

`api-*.json` 是 2026-10-21 对真实运行的固定 CE 镜像，以专属 M0 测试 contact/conversation 调用 HTTP API 后捕获的脱敏响应；不是手写响应。`api-provenance.json` 记录实际版本 digest、时间及 API 权限探测结果。该 test contact 的数据是演示数据，响应来自真实 Chatwoot。这些 API fixtures 不能证明 Widget 消息可见、真实异步回调或私有备注在 Widget 隐藏。

实际运行固定版本后，只保存已脱敏的 Webhook 与 API 响应，并附同名元数据：Chatwoot 版本与镜像 digest、捕获时间、请求路径/事件名、token 类型、原始体验签结果、敏感字段替换规则、对应验证命令和代码 commit。不得提交 token、secret、Cookie、真实用户身份或完整未经脱敏的签名 header。合成数据在 `../synthetic/`。

API 捕获复现：在根目录执行 `uv run python scripts/verify_chatwoot_permissions.py --conversation-id 1`；1 必须替换为本实例专属测试会话 ID，该脚本会发送固定测试回复、私有备注，并将会话先交人工再交回 bot。独立 read token 只用于 messages GET；发送和状态操作始终使用 bot token。脱敏器移除 token、secret、source_id、名字、email、电话、identifier、头像 URL、消息文本；保留字段结构、类型、状态及测试 ID。签名并不适用于已脱敏正文，未在 API fixtures 中伪造验签证明。
