# 验证环境与复现

精确已执行命令、退出码及被测代码版本在 `evidence/release-verification.json`。当前交付基线为 129 项确定性/合成测试和 20 项真实 PostgreSQL 检查（含实际 HTTP 模拟履约）；测试未配置依赖而显示 skip 时不能写成通过。模型、真实 Widget 与故障演示另有独立报告，不从 pytest 数量推导。

## 主机依赖与独立测试库

先按 deployment 启动本地 demo。Docker Desktop 由用户手动打开；准备 uv/Python 3.11、Node 24、`uv sync --frozen`、`npm ci --prefix frontend`。所有命令在仓库目录执行。仅测试覆盖文件将 PostgreSQL 发布至 loopback，默认主部署不发布数据库端口：

```powershell
docker compose --env-file .env --env-file .env.chatwoot -f compose.yaml -f infra/compose.test.yaml up -d postgres
```

本工作区已存在独立 `aftersales_test`，不要重建或清空。新安装首次确认该数据库不存在后，执行：

```powershell
docker compose --env-file .env --env-file .env.chatwoot exec -T postgres psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'CREATE DATABASE aftersales_test OWNER aftersales;'
docker compose --env-file .env --env-file .env.chatwoot exec -T postgres psql -U postgres -d aftersales_test -v ON_ERROR_STOP=1 -c 'CREATE EXTENSION IF NOT EXISTS vector; REVOKE ALL ON DATABASE aftersales_test FROM PUBLIC;'
```

此处管理员只初始化测试库/扩展，测试连接使用受限 `aftersales` 角色。禁止把测试 URL 指向业务数据库。各测试只清理自己随机创建的 schema；模拟 provider 的唯一 key 回执作为集成记录保留。

## 全部 20 项 PostgreSQL 检查

普通 `pytest` 不会替 shell 加载 `.env`。以下 PowerShell 在子进程环境中读取所需秘密，不输出密码，不改 `.env`，也不调用真实 LLM。它显式启用实际中文 embedding 和实际独立 DemoProvider，否则相应项会 skip。首次 FastEmbed 需要下载模型；provider 必须按同一 `ENVIRONMENT_ID` 运行于本机 8100。

```powershell
@'
import os
import subprocess
import sys
from dotenv import dotenv_values
from sqlalchemy.engine import URL

config = dotenv_values('.env')
assert config.get('APP_MODE', 'demo') == 'demo'
assert config.get('FULFILLMENT_MODE', 'demo') == 'demo'
for name in ['AFTERSALES_DB_PASSWORD', 'DEMO_PROVIDER_ACCESS_TOKEN',
             'DEMO_PROVIDER_ADMIN_TOKEN', 'ENVIRONMENT_ID']:
    assert config.get(name), f'Missing local configuration: {name}'
environment = os.environ.copy()
environment.update(
    PYTHONPATH=os.pathsep.join(['src', '.']), PYTHONUTF8='1',
    TEST_DATABASE_URL=URL.create('postgresql+psycopg', username='aftersales',
        password=config['AFTERSALES_DB_PASSWORD'], host='127.0.0.1', port=15432,
        database='aftersales_test', query={'connect_timeout': '5'}
    ).render_as_string(hide_password=False),
    RUN_LOCAL_EMBEDDING_TESTS='1', LIVE_PROVIDER_URL='http://127.0.0.1:8100',
    DEMO_PROVIDER_ACCESS_TOKEN=config['DEMO_PROVIDER_ACCESS_TOKEN'],
    DEMO_PROVIDER_ADMIN_TOKEN=config['DEMO_PROVIDER_ADMIN_TOKEN'],
    ENVIRONMENT_ID=config['ENVIRONMENT_ID'],
)
raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-m', 'postgres', '-q'], env=environment))
'@ | uv run python -
```

`RUN_LOCAL_EMBEDDING_TESTS` 指中文向量模型，不是 LLM。`live` provider 测试访问真实 HTTP/数据库，但业务效果仍为明确的虚构退款与出库。

## 其余检查

```powershell
$env:PYTHONPATH = 'src;.'
uv run ruff check .
uv run mypy src/aftersales src/demo_provider
uv run pytest -m 'not postgres and not live' -q
npm run build --prefix frontend
uv run python scripts/doctor.py --compose-only --example
docker compose --env-file .env --env-file .env.chatwoot config --quiet
```

doctor 的 example 检查使用一次性合成配置，只证明 Compose 结构。真实模型评测的独立测试库、冻结、预算及不覆盖原报告规则见 [evaluation](evaluation.md) 和 `evals/README.md`。真实页面需要 `npm ci --prefix tests/e2e` 及 `npx --prefix tests/e2e playwright install chromium`；按 [demos](demos.md) 区分 fixed 与 model 模式，不将依赖缺失的 skip 记为通过。
