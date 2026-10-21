# 独立 DemoProvider 与履约恢复验证

本服务只产生明确标记 `simulated=true` 的模拟退款和出库。没有接入真实支付、仓库或承运商；“模拟退款成功”不表示真实到账。

## 实现与隔离

- 独立 FastAPI 服务，Compose `demo` profile，独立 PostgreSQL 数据库/用户 `demo_provider`。已实际查询确认该角色不是超级用户、不能创建数据库/角色。
- `provider_environment` 保存 `app_mode=demo`、环境标识与 schema v1。不同环境/版本复用数据库时启动失败；pilot 配置拒绝启动模拟服务。
- 业务 operation 创建时固定模式、供应商、版本、环境、请求快照、请求哈希和幂等键。executor 依据每条 operation 的这些记录选择连接器；没有真实 external 连接器时明确阻断，没有模拟成功兜底。
- API service token、故障控制 admin token、回调 signing secret 分离。admin token 只注入 demo-provider，不注入业务 API/worker。故障接口需要独立测试管理员认证，并且不在 pilot 运行。
- 数据库初始脚本与 `scripts/init_provider_db.py` 可在已有卷上添加缺失的 provider 库，不修改已有密码或数据。
- 独立 `provider_alembic.ini` / `provider_alembic/` 管理 provider schema。显式 `provider-migrate` 命令执行迁移并初始化环境标记；服务启动只校验已迁移版本、必需表和环境，不执行建表。原开发库已逐项校验表、列、类型、空值、主键、唯一约束、检查约束与索引后，以显式 `--adopt-existing` stamp v1，保留已有 3 条模拟操作与回调。

## 实际协议

| 接口 | 约定 |
|---|---|
| `POST /refunds` | `{idempotency_key,snapshot:{case_id,device_id,amount_cents,currency:"CNY"}}` |
| `POST /shipments` | `{idempotency_key,snapshot:{case_id,device_id,sku,outbound_device_id,shipping_address}}` |
| `GET /operations/by-key/{key}` | 返回同一持久化 operation；不存在时 404 |
| `GET /shipments/{id}` | 返回模拟出库回执 |

换货地址必须含非空、有限长度的 `name/phone/region/detail`；不能默认假定目的地。模拟出库结果含原请求设备、分配的新品设备 ID、完整确认地址、模拟运单号、时间与 `simulated=true`。同 key 同请求复用操作；同 key 改金额、设备或地址返回 409。运行服务重启不清空操作。

故障控制 `PUT /admin/faults/{key}` 支持 `reject`、`disconnect-before`、`apply-then-drop`、`processing`、`none`。后两种中，处理状态可经 `/admin/operations/{key}/settle` 明确完成；`/callbacks` 可投递完全相同的重复 delivery 与滞后的 RUNNING 事件。

`disconnect-before` 表示在模拟业务效果之前截断已建立 HTTP 连接的响应。另有真正绑定但不监听端口的测试，验证实际连接失败；本次 Windows 返回 `ConnectTimeout`，系统也可能返回 `ConnectError`，两者均必须保持 UNKNOWN。`apply-then-drop` 在数据库事务提交成功以后截断 chunked HTTP 响应，实际客户端观察到 `RemoteProtocolError`，然后通过原 key 查询持久化结果。

回调签名：`X-Demo-Timestamp` 原始字符串 + `.` + 原始请求体，以独立 secret 计算 HMAC-SHA256，放入 `X-Demo-Signature: sha256=...`；`X-Demo-Delivery` 辅助记录。业务端验证时间窗、签名、供应商、版本和环境后，事务内保存事件与核对任务。未知 key 只记录忽略，不新建退款。重复、乱序事件不倒退状态；矛盾终态触发供应商查询，无法核实则 BLOCKED 可见。

## 已运行检查

- `tests/test_provider.py`：合成持久化/认证/请求冲突/处理后完成/完全相同重复事件/模式隔离/原始签名；真实 TCP 拒绝连接测试。
- 同文件两项标记 `postgres` 的实际验证：真实 PostgreSQL 下 12 次并发同 key 只产生 1 条模拟操作；真实 uvicorn HTTP 响应丢失前后分别确认无操作/已有成功操作，查询与同 key 重发保持唯一效果。
- provider 的 PostgreSQL 测试均先执行真正 Alembic 迁移；另有独立 schema 的 downgrade/upgrade 试验，验证缺失迁移时运行服务拒绝启动，迁移恢复后环境校验通过。
- `tests/test_operations_worker.py`：合成 provider 结果验证领域事务中的 UNKNOWN 资源保留、查询后结算、过期租约阻断、manual 不发 HTTP、回调去重和滞后状态查询。这些不冒充真实供应商。
- 同文件 `postgres + live` 的退款/换货两项：实际独立业务 PostgreSQL 临时 schema + 正在运行的独立 provider PostgreSQL + HTTP，故障为已执行后响应丢失。第一次结果 UNKNOWN，退款分配/新品预占保持；第二次查询原 key 得 SUCCEEDED，在同一领域事务完成结算。换货验证地址与新品设备完全匹配。两项均已运行通过。

运行真实测试需明确设置 `TEST_DATABASE_URL`（专用测试库）、`LIVE_PROVIDER_URL`、`DEMO_PROVIDER_ACCESS_TOKEN`、`DEMO_PROVIDER_ADMIN_TOKEN` 与相同 `ENVIRONMENT_ID`，然后执行：

```bash
uv run pytest tests/test_provider.py tests/test_operations_worker.py -q
```

未设置连接信息时带环境依赖的检查明确 skip，不称为通过。测试 schema 使用随机名称，测试结束只清理自己创建的 schema；真实 provider 保留相应模拟回执作为恢复证据。

## 尚需区别的能力

完整场景 B 的角色 API 实测为 `uv run python scripts/run_demo_provider_scenarios.py --execute-demo`，证据 `docs/evidence/demo-B-response-loss.json`：客户确认、员工初审/收货/验收均经过真正认证 HTTP，provider 已提交后截断响应，主系统观察到 UNKNOWN 且退款分配仍 RESERVED；普通履约 worker 查询原 key 完成，provider 效果始终只有 1 次。随后实际异步签名 HTTP 投递 2 份相同成功 delivery 与 1 份滞后 RUNNING：只新增 2 条唯一 inbox 事件和 2 条核对任务，已完成状态不倒退。

实际调用前/成功后进程退出与正常 daemon 恢复，见 `scripts/run_process_recovery.py --execute-demo`、`docs/evidence/process-recovery.json`；这与合成租约测试分别记录。真实 UI 双流程、场景 C 最后库存争用、加密备份恢复与空卷安装另有相应运行报告。

上述证据证明模拟服务、HTTP 和数据库恢复逻辑；不等于真实支付/物流连接器验收。provider 后续结构升级必须增加新的显式迁移，不重写已应用迁移、已有列或历史义务。
