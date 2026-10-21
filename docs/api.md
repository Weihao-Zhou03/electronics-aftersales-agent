# API 使用约定

交互式 schema：启动后 `/docs` 与 `/openapi.json`。此文不替代实际 Pydantic schema；完整目标基线在 SPEC 第 11 节。

- 所有客户、员工与管理员身份来自已验证的 HttpOnly session。登录 POST 需要允许的 `Origin`；登录返回 `csrf_token`，写请求同时发送 session cookie、`Origin`、`X-CSRF-Token`。
- 创建/写业务动作带 `Idempotency-Key`；同一身份/路由的相同 key 返回原结果，同键不同内容 409。员工和草稿更新带 `expected_version`；聊天接管带 `expected_revision`，消息核对带 `expected_control_revision`。
- 输入采用白名单 schema。客户不能提供生效的 customer_id、退款金额、审批结果或任意目标状态。不可访问资源采用统一 404，角色拒绝 403，格式错误 422，冲突 409，依赖失败 503，速率限制 429。
- `GET /api/v1/app-config` 仅返回模式，供页面真实展示；不会暴露凭据。live/ready 分开，不把配置完整当成真实模型/Chatwoot 全功能验证。

| 路由组 | 主要接口 |
|---|---|
| 身份 | POST `/auth/login`、POST `/auth/logout`、GET `/auth/me`；GET `/chat/identity`、POST `/chat/bind` |
| 客户 | GET `/orders`、`/orders/{id}`；POST `/eligibility/evaluate`；POST/PATCH/GET `/proposals`；POST `/proposals/{id}/confirm` |
| 申请 | GET `/cases`、`/cases/{id}`；POST `supplements`、`return-shipment`、`cancel` |
| 私有材料 | POST `/attachments`（multipart 字段 file）；GET `/attachments/{id}`，最多每张 5 MB，PNG/JPEG/WebP，申请引用最多 4 个本人附件 |
| 员工 | `/staff/cases` 的初审、收货、验收、补货继续、实物处置；`/staff/operations` 的人工结果与 UNKNOWN 核对 |
| 会话 | 客户 GET `/conversations/{id}`（本人已确认申请来源、最近一页公开消息）；员工 GET `/staff/conversations/{id}`（相关申请）；POST `takeover`、`return-to-bot`；GET `/staff/outbound-messages`、POST `/{id}/reconcile` |
| 管理 | `/admin/inventory/receipts`；`/admin/policies` validate/publish；GET `/admin/system`；PATCH `/admin/system/agent` |

以上路径组省略公共 `/api/v1` 前缀。独立签名回调为 `/integrations/chatwoot/agentbot`、`/integrations/chatwoot/control` 和仅 demo 的 `/integrations/demo-provider`。

申请预览示例：

```json
{"order_unit_id":"设备UUID","type":"return","facts":{"reason":"设备未使用，申请退货","unused":true,"accessories_complete":true,"evidence_ids":[]}}
```

确认只传服务器返回的 `expected_version` 和 `summary_hash`；金额与身份由后端读取。换货地址在已登录门户表单核对，不进入模型工具 schema。`COMPLETED` 换货仅表示出库发货已确认，页面不声称已签收。

人工履约凭证必须匹配原义务摘要、金额/设备/地址，并记录 `verification_method=manual_attestation`。这是员工登记，不是支付机构已核验。UNKNOWN 消息核对也明确记录远端读取核实或员工未送达声明，不重发旧意图。
