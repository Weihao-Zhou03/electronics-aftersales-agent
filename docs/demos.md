# 三段可复现演示

这些演示使用实际登录、真实 Chatwoot/Widget、PostgreSQL、worker 和独立 HTTP DemoProvider。初始用户/产品/订单/库存是明确的虚构输入，seed 只添加独立前缀新数据；客户确认、员工初审/验收/补货、履约核对不靠 SQL 推进。全部退款/出库均为模拟回执。

先按 README/deployment 启动已有环境，手动开启本地模型代理，确认 `AGENT_REPLY_MODE=model`、真实 provider/model、checkpoint 迁移和政策索引已配置。基础seed的 `customer-a` 密码是本地场景用户的同一演示密码，脚本不会改已有hash。

```powershell
$env:PYTHONPATH = 'src;.'
$env:M0_DEMO_PASSWORD = 'ElectronicsDemo2026!'
uv sync --frozen
npm ci --prefix tests/e2e
npx --prefix tests/e2e playwright install chromium
uv run python scripts/run_demo_scenarios.py --execute-demo
```

示例密码适用于本工作区已初始化的专用demo账号；新安装使用自行seed的密码。主入口检查当前worker实际为真实模型模式，创建新场景输入并将无密钥的角色/订单索引写入忽略的 `.local/scenario-seed.json`，依次执行 A、B、C库存与C接管故障。需要空闲本地18491端口。入口会短暂停止/恢复本项目相应worker，不启动Docker Desktop，不修改`.env`或上游模型授权。

每次全套演示重新运行主入口以获得新设备；不要直接重复确认已经办理完的旧场景。测试默认一个Chromium worker，不使用个人浏览器登录资料，也不拦截业务接口伪造返回。主入口新一次记录可能更新同名证据报告；历史办理数据保留在数据库。

### 可选固定输入回放

`seed_scenario_demo.py` 另支持 `--at`、`--seed`、`--scenario`。默认不传参数时仍为当前时间、新 UUID 前缀和全部 A/B/C/delay，不改变上述主入口。新增参数进入容器前须先执行 `uv run python scripts/dev.py` 重建项目镜像；本轮已在独立 PostgreSQL schema 验证保留已有流程，并在重建镜像的两次one-off容器实际播种中验证输入不重复，见 `evidence/scenario-seed-cli.json`。这些参数检查没有调用模型，不将其冒称为新一轮完整模型演示。

```powershell
uv run python scripts/dev.py
docker compose --env-file .env --env-file .env.chatwoot exec -T api uv run --frozen --no-sync --no-dev python scripts/seed_scenario_demo.py --at '2026-10-21T12:00:00+00:00' --seed 'replay-20261021-1' --scenario C
```

`--at` 必须是带时区的 ISO8601，归一为 UTC，仅固定虚构订单的签收/付款输入，不回拨真实业务时钟。历史日期可能已超过政策期限，不能保证 live 资格；完整当前演示继续使用默认输入或当前日期与新种子。`--scenario` 可选 `A`、`B`、`C`、`delay`、`all`，C 创建两位客户和一件新品，其余各一位；同种子可分别创建不同场景。

`--seed` 派生稳定的逻辑前缀、用户名、SKU、订单号和虚构序列号，不承诺数据库随机主键相同。重复同种子只复用已有输入，不重设密码、时间、已确认申请或库存，不补回已预占/消耗新品；不传 `--at` 时也保留第一次的日期。同种子改用不同显式 `--at` 会拒绝，需选择新种子。已有输入缺失或归属冲突时停止，不修补成一份新流程。想重新演示完整办理应换新种子，不能重播已经完成的同一设备。

## A 正常同SKU换货

实现：`tests/e2e/model.spec.js`，可在全新场景seed后单独执行 `npm test --prefix tests/e2e -- model.spec.js`。

1. 新客户在真实门户登录，打开已HMAC验证Widget，仅表达“耳机想办理售后”；验证Agent追问且无草稿。
2. 客户补充序列号、质量故障和同SKU换货诉求；Agent调用本人订单、购买版本检索、资格和库存工具，准备草稿。
3. 实际点击Widget渲染的绝对门户链接，不能把错误的相对Chatwoot链接用脚本修正。客户查看预览并明确确认，此前无正式case/库存预占。
4. 查看客户case→公开历史、员工case→会话、会话→case的真实关联。员工私有备注包含待办/申请链接，但不出现在客户API或Widget。
5. 对应员工/客户身份通过真实API执行初审、寄回、收货与验收，worker取得模拟出库回执。只预占一台新品，最终设备/地址/case与operation一致。

证据：`scenario-a-real-model.json`、`m3-real-model-widget.png`、`scenario-a-exchange.png`。员工办理的HTTP身份操作明确列出，不算作模型自主审批。

## B 退货与结果未知

实现：`uv run python scripts/run_demo_provider_scenarios.py --execute-demo`，会添加自己独立的当前时间演示输入。

1. 客户确认退货，员工初审/收货；在验收创建义务前，用独立demo管理员接口设置该key的 `apply-then-drop` 故障。
2. 暂停普通履约worker，以单次实际worker执行该义务。模拟provider先提交成功事务，再截断真实HTTP响应。
3. 读取真实管理API与数据库：operation为UNKNOWN，退款allocation为RESERVED，provider中原key只有1条效果。
4. 恢复普通worker，它查询同一个key并结算原义务。最终COMPLETED/SETTLED，provider仍只有1笔模拟退款。
5. 由provider实际发送重复/滞后签名回调，确认去重和状态不倒退。

证据：`demo-B-response-loss.json`。管理台 `/staff/operations` 可查看实际义务、回执和待核对状态；UNKNOWN并不表示已经确定失败，不改key盲重发。

## C 人工接管与最后一件库存

库存部分实现：`tests/e2e/concurrency.spec.js`；接管部分：`uv run python scripts/run_model_fault_acceptance.py --execute-demo`，主入口已依次运行。

两位不同客户分别明确确认，员工并发验收，同SKU只有一件新品：一个case准备履约，另一个WAITING_STOCK。管理员通过真实入库API补货后，员工在原case继续，最终两件唯一新品出库，未创建替代申请。

接管演练使用显式测试中继，先向配置的真实上游发送模型请求，收到真实响应后暂存。员工经授权API接管，再释放响应；真实AgentRun变STALE，未产生旧草稿/正式单/外发。随后员工显式交回，独立注入本地HTTP503，验证Widget明确服务不可用，但客户门户确认与员工初审仍可继续。

中继没有生成模型答案、改prompt或替换模型；503是明确本地故障注入，不能标成真实上游故障。中继独立随机token只保存在本地忽略文件，finally恢复原worker和关闭中继。

证据：`scenario-c-real-concurrency.json`、`scenario-c-stock.png`、`m4-real-model-takeover.json`、`m4-model-unavailable-form.png`。

## 其他故障和固定模式

`uv run python scripts/run_process_recovery.py --execute-demo` 另测真实进程在provider调用前/提交后退出，等待实际30秒租约，由普通worker查询同key恢复；不改SQL租约或状态。结果见`process-recovery.json`。

M0的 `widget.spec.js` 专门要求fixed模式，与模型演示不是同一配置。`make test-e2e` 仅运行不依赖模型的`business.spec.js`；`make test-e2e-fixed` 明确验证fixed Widget；`make demo-scenarios` 执行上述真实模型主入口。不要不分模式一次运行全部spec，再把跳过/失败算通过。
