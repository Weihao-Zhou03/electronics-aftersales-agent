# 自建 Agent 任务评测

`dev-v1.json` 有 40 个开发情境，`holdout-v1.json` 有 60 个冻结候选情境；措辞、订单编号、SKU、分摊金额和政策窗口在两集合分离。内容为本项目自行编写，不是官方 τ²-bench 分数。

每项定义初始数据库、对话、允许的人类操作、预期下一步和禁止的模型副作用。数据库、LangGraph PostgreSQL 检查点和模型 HTTP 调用均为真实实现；会话适配器是明确的合成身份/历史 fixture，不代替 Chatwoot 的真实 Widget 与 Sidekiq 验收。两个完整办理情境由对应客户/员工通过 ASGI TestClient 合法登录及带 CSRF、版本和幂等键的 API 执行确认、初审、物流、验收和模拟人工凭证登记，不算作模型自主批准或真实退款/物流。

仅对名称包含 `eval` 或以 `_test` 结尾的独立 PostgreSQL 数据库运行。每轮使用随机独立业务及检查点 schema，并只清理本轮明确创建的 schema；模型凭据只从本地忽略配置读取。实际模型不可用、限额或通信失败时停止排新任务并保存部分结果，不回退 MockProvider。`model_timeout` 是明确故障注入，真实 HTTP 客户端采用 1ms 超时，结果单列。

```powershell
$env:PYTHONPATH='src'
uv run python scripts/evaluate.py --split dev --limit 3 --output docs/evidence/eval-dev-smoke.json
uv run python scripts/evaluate.py --split dev
uv run python scripts/evaluate.py --freeze docs/evidence/eval-dev-latest.json
uv run python scripts/evaluate.py --split holdout
uv run python scripts/evaluate.py --split stability
```

v1 保留集发现缺政策时只说需人工却没有实际交回的 A06 工程缺口，原报告/冻结记录保留，不能因总体统计达标而判定验收通过。修复为服务端确定性转人工保护后，使用未继承旧结果上下文的另一 Codex 子代理编写的新 `holdout-v2.json`，并重新跑同一 40 项开发集；不是对已经暴露的保留集继续改提示词。数据编写不等于独立审查。协议示例：

```powershell
uv run python scripts/evaluate.py --revision v2 --split dev --concurrency 2
uv run python scripts/evaluate.py --revision v2 --freeze docs/evidence/eval-dev-v2-latest.json
uv run python scripts/evaluate.py --revision v2 --split holdout --concurrency 2
uv run python scripts/evaluate.py --revision v2 --split stability --concurrency 2
```

v2 使用 `freeze-v2.json` 和 `eval-*-v2-latest.json`，`dev-v1.json` 保持不变。冻结记录不允许覆盖；开发 smoke 的 `--limit` 和 `--task-id` 不得用于冻结集或稳定性集。每次运行实际选中的数据集文件路径与字节哈希写入报告。

开发集完整跑完且完成率至少 85%、自动引用支持检查至少 95%、禁止副作用为 0，才允许写入 `freeze-v1.json` 和运行冻结集。冻结文件记录两个数据集哈希、开发报告哈希、模型与提示词/关键实现哈希。冻结后不得从测试结果持续调 prompt；标注有误时须记录新版本和原因并重新报告。10 个关键冻结情境各运行 3 次，单列稳定性。

评分读取模型真实工具调用、返回回复和数据库记录，检查正式申请/履约/预占未被模型擅自创建、选定设备/归属/金额正确、引用匹配实际政策版本与条款。引用自动门槛要求返回文本含正确版本与条款，且工具返回过相同支持依据；仍须人工抽查语义是否受条款支持，不把自动匹配当人工审查通过。成本只报告实际 token；本地订阅代理没有核实的货币价格和额度记账时，货币成本保持未知。
