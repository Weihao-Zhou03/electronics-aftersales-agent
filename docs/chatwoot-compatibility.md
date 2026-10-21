# Chatwoot 兼容性与 M0 验收

实测日期：2026-10-21。真实服务为 Chatwoot Community `v4.19.0-ce`，镜像：
`chatwoot/chatwoot:v4.19.0-ce@sha256:6f296843097eee1eb8b6968027908fb4bb3de0b72236aa6a8f21386603266c91`。
其余镜像、真实运行版本和环境见 [environment-evidence.md](environment-evidence.md)。源码契约出处见 [chatwoot-source-notes.md](chatwoot-source-notes.md)。

## 第 5.5 节逐项结果

| # | 结果 | 实际证据与边界 |
|---|---|---|
| 1 Widget 真消息到回调 | 通过 | 隔离 Chromium 中实际 Widget 发消息，Sidekiq 异步投递，真实 receiver/PG 保存事件。`evidence/m0-widget-results.json` |
| 2 正确签名及篡改拒绝 | 通过 | 原始真实 Sidekiq 回调验签通过；另以脱敏真实 fixture 本地重新签名，真实 HTTP receiver 对改正文、错 secret、过期时间戳均 401。重放不冒称 Chatwoot 原始网络投递。`evidence/m0-live-control.json` |
| 3 同消息去重 | 通过 | 同一顶层 message ID 经实际 HTTP 重放仅一个 job；另有真实 PG 并发去重与事务测试。 |
| 4 回复显示、无循环 | 通过 | 实际 Widget 显示 bot 固定回复；公开 outgoing 回调未产生新回复，浏览器观察 2 秒；过滤规则另有合成测试。 |
| 5 私有备注隔离 | 通过 | 专用客服账号 API 能读取实际 private note；Widget API 和 UI 均不存在该内容。未做 Chatwoot 客服 UI 样式断言。 |
| 6 接管阻断过时任务 | 通过 | 实际 Widget HTTP 消息触发真实任务，固定回复可控延迟 2 秒，员工 API 接管后没有新公开回复。M0 不接 LLM、不创建业务草稿。 |
| 7 显式交回恢复 | 通过 | 真实迟到 pending 事件不能恢复；员工显式交回并读回 AgentBot 指派后，新输入恰好产生一个 SENT。 |
| 8 bot 权限 | 通过（有已确认限制） | bot 发消息、读会话、切 open 和 typed AgentBot/pending 均成功；history GET 为 401，显式专用非管理员客服 read token 读取成功。`tests/fixtures/real/api-provenance.json` |
| 9 强制 Widget 身份 | 通过 | `hmac_mandatory=true`，两已登录客户交换 identifier/hash 均被实际 Chatwoot 拒绝（401）；退出撤权、换号不能绑定旧会话；绑定来源为服务端 GET `meta.hmac_verified` + `meta.sender.identifier`。 |
| 10 Sidekiq 异步 | 通过 | Sidekiq 实际容器/心跳，实际异步签名事件和 Widget 往返均验证；不是仅检查 Rails 首页。 |

执行入口：`tests/e2e/widget.spec.js`、`scripts/verify_live_control.py`、`scripts/verify_chatwoot_permissions.py`、`tests/test_core.py`。最后精确代码版本和复验命令在 HANDOFF 记录；报告生成时的源码/时间见各 JSON，不把后续文档 commit 当成已测代码。

## 实际字段与初始化

- bot 创建响应含 `access_token` 和独立 `secret`；账户 webhook 响应 `payload.webhook.secret`；Widget secret 是 inbox `hmac_token`，公开 Website token 单独保存。三种用途和两路签名 secret 不复用。
- 绑定 body 字段是 `agent_bot`；账户 webhook body 为 `webhook` 对象，只订阅 `conversation_status_changed` / `conversation_updated`。
- GET 会话含顶层 account/inbox/id/status，`meta.assignee_type` 必须区分 User/AgentBot。指派数值 ID 重合不能证明身份。
- 显式交回通过 assignments `{assignee_type: AgentBot, assignee_id: bot_id}` 清除人工并设 pending，再 GET 验证；本地员工授权独立记录。
- message webhook 顶层 `id` 为 message ID；Contact sender 有 `contact_type` 而不一定有 `type`。消息 API、原始 webhook 和内部归一化 fixture 分开维护。
- bootstrap 连续运行两次复用同一 account/inbox/bot/webhook，未新建重复对象；auto assignment=false 已读回确认。管理员 token 未注入 worker。
- 专用客服 token 没有 Chatwoot 原生方法级“只读”保证；应用只允许其用于固定账户/inbox 的消息历史 GET。不会在 bot 失败时回退管理员。

## 网络条件

初次真实回调被 SafeFetch 拒绝（内部 hostname 无公网 IP）。查证固定版本 `lib/safe_fetch.rb` 和 `lib/safe_fetch/fetcher.rb` 后，在本地隔离演示环境显式启用官方 `SAFE_FETCH_ALLOW_PRIVATE_NETWORK=true`，实际 AgentBot/control 回调成功。Compose 示例默认 false；生产/试用必须重新确认网络边界，当前未做公网 HTTPS/tunnel 验收。没有修改 Ruby 核心。

脱敏实际 API 与 webhook fixtures 位于 `tests/fixtures/real`；`tests/fixtures/synthetic` 与 MockTransport 测试不作为真实 Chatwoot 证据。
