# 售后客户门户与员工工作区

单个 React / TypeScript / Vite 工程。客户和员工使用同源 `/api/v1`，Cookie 会话、角色、CSRF、资源归属、金额、幂等及状态迁移均由后端再次核验。前端没有内置订单、业务成功响应或模拟统计。

```bash
npm ci --prefix frontend --no-audit --no-fund
npm run build --prefix frontend
```

生产构建写入 `frontend/dist`，由 FastAPI 的 `/assets` 与 SPA 路由提供；Compose 的应用镜像在 Node 构建阶段执行相同流程。开发可运行 `npm run dev --prefix frontend`，Vite 将 API 代理至 `http://localhost:8000`；需把实际开发 origin（默认 `http://localhost:5173`）加入后端本地 `ALLOWED_ORIGINS`。不要放宽为任意 origin。

- 客户：`/login`、`/orders`、`/orders/:id`、`/proposals/:id`、`/cases`、`/cases/:id`、`/help`。
- 员工：`/staff/cases`、`/staff/cases/:id`、`/staff/operations`、`/staff/inventory`、`/staff/conversations/:id`。
- 管理员：`/admin/policies`、`/admin/system`。
- 原始 M0 验证页保留在 `/m0`。

仅登录客户初始化 Chatwoot Widget。identifier 与 HMAC 从当前服务端 session 获取；`chatwoot:on-message.detail.conversation_id` 只是绑定提示，服务端独立读取真实 Chatwoot 的身份与作用域后决定是否允许绑定。退出先撤销后端会话，再 reset Widget。前端不把聊天文本、邮箱或自定义属性当业务身份。

页面运行模式来自服务端只读 `/api/v1/app-config`，不依赖构建时伪造常量。demo 显示模拟订单/履约标识与演示账号说明；pilot 隐藏演示账号说明，按实际 manual/external 配置显示履约方式。读取不到模式时只显示普通服务入口，不宣称已接入真实企业。

图片材料经带 CSRF 的 multipart 上传至私有后端；只保留附件 ID 引用，读取仍需登录和权限。退款、地址修改、人工接管/交回、履约核对、政策发布均显示对应确认或明确操作。消息发送结果 UNKNOWN 需员工在会话页核对真实消息或明确登记未送达声明，保持暂停直到显式交回。

真实浏览器验收位于 `../tests/e2e`，使用独立 headless Chromium，无个人浏览器状态、无 HTTP route mocks。需要完整 Compose 依赖及本地演示密码：

```bash
npm ci --prefix tests/e2e
npx --prefix tests/e2e playwright install chromium
M0_DEMO_PASSWORD='部署者本地配置的演示密码' npm test --prefix tests/e2e
```

业务测试只在 `APP_MODE=demo` 下创建独立演示订单和库存，然后经 UI 执行全部业务操作；不删除既有记录。实际运行结果和截图写入 `docs/evidence/`，未执行的能力不得据此宣称通过。
