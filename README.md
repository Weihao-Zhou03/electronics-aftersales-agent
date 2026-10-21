# 电子产品售后办理 Agent

一家虚构商家的电子产品售后试用应用。客户通过门户或 Chatwoot 聊天准备单台设备的退货/同 SKU 换货草稿，在门户明确确认；员工分别进行寄回初审和收货验收；真实数据库事务保存申请、金额预留、库存与审计。退款和物流由独立 **DemoProvider 模拟**，没有接真实资金、物流或任何企业订单系统。

需求基线：[SPEC](docs/SPEC.md)。当前进度、实际验收与独立审查状态分别见 [implementation-status](docs/implementation-status.md)、[HANDOFF](docs/HANDOFF.md)、[REVIEW](docs/REVIEW.md)。未列为实测通过的能力不能视为已验收。

## 启动已有本地环境

先由用户手动打开 Docker Desktop，确认 Linux 引擎可用。项目不会自动启动/重启 Desktop。

PowerShell（本仓库目录）：

```powershell
$env:PYTHONPATH = 'src;.'
uv sync --frozen
uv run python scripts/doctor.py
uv run python scripts/dev.py
$env:M0_DEMO_PASSWORD = 'ElectronicsDemo2026!'
docker compose --env-file .env --env-file .env.chatwoot run --rm --no-deps -e M0_DEMO_PASSWORD api uv run --frozen --no-sync --no-dev python -m aftersales.demo_seed
```

WSL/Linux 可使用 `make dev` 和 `M0_DEMO_PASSWORD='自己选择的演示密码' make init-demo`。代码使用 uv 管理 Python 3.11；Windows 中文目录下将 `PYTHONPATH` 指向 `src`，避免 Python 3.11 editable `.pth` 的本地编码差异。依赖固定在 `uv.lock`，前端固定在 `frontend/package-lock.json`。

门户：[localhost:8000](http://localhost:8000)；Chatwoot：[localhost:3000](http://localhost:3000)；M0 验证页：[localhost:8000/m0](http://localhost:8000/m0)。默认数据库和 Redis 不发布宿主端口，真实集成测试使用显式 `infra/compose.test.yaml` 的仅本机 `15432`。

本工作区实际已初始化的门户演示账号：`customer-a`、`customer-b`、`support`、`admin`，密码均为 **`ElectronicsDemo2026!`**。这些是专门的本地演示账号，不是用户个人凭据。seed 会复用已有账号，不重置密码。Chatwoot 的专用初始化管理员/客服凭据仅保存在本地忽略的 `.local/chatwoot-admin.json`，不提交到 Git。

## 首次配置

复制 `.env.example` 为 `.env`，为数据库、Redis、session、模拟履约服务/管理员/签名生成各自不同的长随机值；保留 `.env.chatwoot` 空文件，直到 bootstrap 写入实际 Chatwoot 响应。详细初始化和网络边界见 [deployment](docs/deployment.md)。不要把示例 `CHANGE_ME` 当作有效连接。

Chatwoot 首次账号、inbox、bot 和签名配置需要初始化权限。`scripts/bootstrap_chatwoot.py` 接受单独的 `CHATWOOT_ADMIN_ACCESS_TOKEN`、账户 ID、网站 URL 和回调 URL，写 `.env.chatwoot`，重复运行复用已有 ID。运行时只注入 bot token 与专用非管理员历史读取 token。bot token 在实测版本上不能读取消息列表，该限制有明确记录和验证。

所有运行命令均显式加载 `.env` 与 `.env.chatwoot`。不要只写 `docker compose up`，否则初始化后的 token/secret 可能没有进入容器。任何首次配置缺项会报告错误，不会伪造回调或成功。

## 模型与检索

`AGENT_REPLY_MODE=fixed` 使用固定回复验证 M0，不调用模型。`AGENT_REPLY_MODE=model` 才启用一个 LangGraph Agent，工具循环默认最多 9 次模型调用、8 次工具调用。人工接管、身份授权或输入版本变化会丢弃旧结果；模型不可用时门户表单与员工办理仍可用。

本机已按用户指定使用 EasyCLIProxyAPI 的 Responses 接口，模型 `gpt-6.1-sol`；本机 base 为 `http://127.0.0.1:8317/v1`，容器 base 单独设置为 `http://host.docker.internal:8317/v1`。应用只使用用户授权配置的本地接口密钥，不读取 Codex OAuth/ChatGPT 登录文件。代理的订阅额度和可用性由代理及其上游决定；没有将调用算作 OpenAI Platform 付费 API 成绩或零费用承诺。

```powershell
uv run python scripts/model_smoke.py
docker compose --env-file .env --env-file .env.chatwoot run --rm --no-deps api uv run --frozen --no-sync --no-dev python -m aftersales.agent_runtime setup
docker compose --env-file .env --env-file .env.chatwoot run --rm --no-deps api uv run --frozen --no-sync --no-dev python -m aftersales.agent_runtime index-policies
```

实际中文 embedding 使用本地 FastEmbed `BAAI/bge-small-zh-v1.5`（512 维），不需要另一个 API key；首次下载模型后使用持久缓存。检索固定订单设备购买时的政策版本，jieba/BM25 与 pgvector 余弦检索用 RRF 合并。模型或维度变更需要显式重建，不能混用向量。`MockProvider` / mock embeddings 仅用于显式选择的确定性测试，报告单独标记。

## 验证与演示

```powershell
uv run ruff check .
uv run mypy src/aftersales src/demo_provider
uv run pytest -m 'not postgres and not live'
# TEST_DATABASE_URL 必须为真实的独立 PostgreSQL 测试数据库。
uv run pytest -m postgres
npm ci --prefix tests/e2e
$env:M0_DEMO_PASSWORD = 'ElectronicsDemo2026!'
npm test --prefix tests/e2e -- business.spec.js
```

真实 Widget、签名与权限证据见 [Chatwoot compatibility](docs/chatwoot-compatibility.md)；履约故障证据见 [provider evidence](docs/provider-evidence.md)。三段可复现演示在 [demos](docs/demos.md)，A01—A34 结果在 [acceptance](docs/acceptance.md)，模型任务集与实际评测在 `evals/`。测试直接调用的模型、合成对话端口和真实 Chatwoot 端到端验证分别记录，不能相互替代。

备份/恢复入口与加密、停止写入窗口、独立恢复库校验见 [operations](docs/operations.md)。备份含数据库中的密钥与私有附件，所有归档加密且不提交。`make backup` / `make restore-smoke BACKUP_DIR=...` 使用外部环境中的 `BACKUP_PASSPHRASE`。

## 范围与协作

维护一个开发分支、按阶段提交和一个总 PR，不自动合并。独立审查前 `docs/REVIEW.md` 保持“待审查”。架构见 [architecture](docs/architecture.md)，接口见 [api](docs/api.md)，未验证项见 [known limitations](docs/known-limitations.md)。

首版不处理维修、跨 SKU 补差、分期、多币种、多商家或真实支付。`pilot` 拒绝 demo 履约/账号数据，未配置 external 连接器时明确启动失败。公司身份、订单、库存和真实履约适配及受控上线仍需公司提供和验收。Chatwoot 客服台是第三方软件，不是本项目自行开发的界面；本项目代码许可与第三方来源分别见 [LICENSE](LICENSE) 和 [THIRD_PARTY](THIRD_PARTY.md)。
