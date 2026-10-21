# 部署与配置

目标是单机本地演示和后续受控试用。当前 Windows 工作区使用 Docker Desktop Linux/WSL2 引擎；应用代码在 Windows 中文路径，容器内运行，未迁移用户文件。镜像版本和 digest 见 `compose.yaml`、`chatwoot-compatibility.md`。

## 首次本地安装

用户手动打开 Docker Desktop。安装 Git、uv 和 Node.js，进入项目目录；WSL/Linux 的 `make` 入口与下列脚本等价。

```powershell
$env:PYTHONPATH = 'src;.'
uv sync --frozen
uv run python scripts/configure_demo.py
```

配置生成器仅填充缺失的十种独立内部密钥，保留已有值；不生成模型接口密钥，不伪造 Chatwoot 密钥。`.env`、`.env.chatwoot`、`.local`、数据库卷和附件均不提交。

检查 `.env` 的 `APP_MODE=demo`、`FULFILLMENT_MODE=demo`、唯一 `ENVIRONMENT_ID`。本地网络允许 Chatwoot 向 `agent-api.internal:8000` 回调时，需明确设置 `SAFE_FETCH_ALLOW_PRIVATE_NETWORK=true`；这在固定版本上允许私网请求，限受控本地网络。公网部署保留 false 并使用可达的 HTTPS 回调。然后：

```powershell
uv run python scripts/doctor.py --compose-only
uv run python scripts/dev.py
```

该命令创建数据库/Redis，运行 Chatwoot prepare、主应用 Alembic 和独立 DemoProvider Alembic 后启动。首次尚无 Chatwoot bot/inbox 密钥时，身份和回复尚未就绪，需要完成下面的引导。运行时不使用 `create_all` 代替迁移。

## Chatwoot 初始引导

在 `http://localhost:3000` 创建/登录本地管理员，建立本项目账户，并获取其 API access token。为运行时创建单独的普通客服用户并获取其 token。管理员凭据只在调用 bootstrap 的 shell 中短暂提供，不能设置为 worker 的历史读取 token。

```powershell
$env:CHATWOOT_BASE_URL = 'http://localhost:3000'
$env:CHATWOOT_ACCOUNT_ID = '实际账户ID'
$env:CHATWOOT_ADMIN_ACCESS_TOKEN = '仅本次bootstrap的管理员token'
$env:CHATWOOT_READ_ACCESS_TOKEN = '独立普通客服token'
$env:CHATWOOT_CALLBACK_BASE_URL = 'http://agent-api.internal:8000'
$env:CHATWOOT_WEBSITE_URL = 'http://localhost:8000'
uv run python scripts/bootstrap_chatwoot.py
Remove-Item Env:CHATWOOT_ADMIN_ACCESS_TOKEN
uv run python scripts/dev.py
```

脚本保存真实响应中的 inbox/bot/webhook/Widget ID 和分别生成的密钥到 `.env.chatwoot`；重复执行复用相同资源。强制 Widget HMAC 验证、关闭自动分配、配置两条签名回调。版本 4.19.0 的 bot 无权读取历史，运行时仅为历史 GET 使用明确配置的普通客服 token，绝不自动回退到管理员。验签、权限和实际字段见兼容报告。

本次演示的本地管理员凭据由初始化工具私存于 `.local/chatwoot-admin.json`；克隆仓库不包含该文件。`initial_chatwoot_admin.rb` 是 demo 专用的隔离安装测试工具，需要显式传入新密码，输出含 token，必须私密捕获；无需在已配置实例重复使用。

## 演示数据与模型

```powershell
$env:M0_DEMO_PASSWORD = '自己选择的至少12位演示密码'
docker compose --env-file .env --env-file .env.chatwoot run --rm --no-deps -e M0_DEMO_PASSWORD api uv run --frozen --no-sync --no-dev python -m aftersales.demo_seed
```

seed 复用记录，不改已有密码，不清库。基础数据是 3 SKU、2 客户、6 订单、8 台设备、2 政策版本和 9 台新品；页面测试另建带独立前缀的当前时间场景，保留历史。首次 seed 以当前时间建立可操作订单，测试以固定时钟重放。

真实 Agent 需要独立授权的兼容 API。在 `.env` 设置 `MODEL_PROVIDER`、`MODEL_NAME`、`MODEL_API_BASE`、`MODEL_API_KEY`；本机代理的容器地址使用 `MODEL_CONTAINER_API_BASE`。不要提交密钥或复制 OAuth token。运行 `scripts/model_smoke.py` 确认工具续调；执行 README 中 checkpoint setup 和政策索引命令，再设置 `AGENT_REPLY_MODE=model` 并重建/更新 worker。固定模式仅供 M0 验证。

FastEmbed 首次需联网下载中文模型，缓存保存在持久 `.local` 卷。政策索引必须与配置的模型/维度一致，变更时显式重建。模型不可用时表单和员工办理不依赖模型。

## 日常复现与检查

```powershell
uv run python scripts/doctor.py
uv run python scripts/dev.py
uv run python scripts/live_probe.py
docker compose --env-file .env --env-file .env.chatwoot ps
```

只加载 `.env` 会遗漏实际 Chatwoot 密钥，所有 Compose 入口均须同时加载两个 env。数据库和 Redis 默认不暴露端口；真实数据库测试显式增加 `infra/compose.test.yaml`，只监听 127.0.0.1:15432，使用独立测试数据库，禁止指向业务库。测试的初始化/删除仅限随机 schema 或脚本生成的恢复库。

独立干净安装复现入口为 `uv run python scripts/clean_install_smoke.py`，使用新 Compose project、新卷、18000/13000/18100 端口和临时独立秘密值，不覆盖主项目；需要空闲端口和足够内存。实际结果见 `docs/evidence/clean-install.json`（仅报告通过才算通过）。

## 试用环境

使用 Linux 单机、持久卷、独立数据库角色、HTTPS reverse proxy、限定可信 Origin、`SESSION_COOKIE_SECURE=true` 和独立随机密钥。关闭 Chatwoot 公开注册；不得将数据库/Redis端口发布到公网。运行时管理台展示真实 heartbeat、UNKNOWN 和暂停状态。备份/恢复见 operations。

`APP_MODE=pilot` 需独立数据库身份标记，拒绝 demo 账号数据和 demo 履约。`manual` 模式由员工登记外部执行证据，应用不自动调用 provider；`external` 尚无真实连接器，明确失败，不自动回落到 DemoProvider。不要直接把演示数据库切成 pilot。

本次没有公网域名、TLS 终止、公司身份/订单/支付适配或受控客户试用证据；这些不能由本地安装通过推导。源码和锁定镜像可交付审查，生产配置仍需部署方提供并验证。
