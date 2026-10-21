# 电子产品售后办理 Agent 完整实现方案

版本：1.1（统一 M0 启动与 GitHub 交接约定）  
方案日期：2026-10-21  
用途：交给 Codex 分阶段实现的产品与工程规格。本文描述的是目标和验收标准，不代表代码已经完成、部署或通过测试。

## 1. 最终交付应该达到什么水平

目标是一套**可以独立部署、实际处理业务记录、具备异常恢复能力的小团队试用版**。第一版使用虚构商家、模拟订单和模拟履约服务；用户登录、订单权限、政策判断、申请确认、人工审核、数据库事务、库存预占、人工接管与审计均须真实实现。

验收完成后，可以用于求职演示、技术面试和企业沙箱试用。接入一家公司的真实身份、订单、库存、政策和履约系统，并完成业务与运行验收后，才能用于该公司的受控业务。它不是适用于任意公司的开箱即用生产产品。

| 层面 | 第一版的实际要求 | 对外表述 |
|---|---|---|
| 业务界面 | 客户门户、现成客服聊天台、售后业务管理台，按钮调用真实后端 | 可部署的业务应用 |
| 办理行为 | 真实写入数据库、推进状态、记录审批、占用和释放库存 | 有完整业务闭环 |
| 模型能力 | 使用真实模型接口执行理解、查询、追问、解释和准备申请 | 单 Agent 业务办理 |
| 业务数据 | 虚构商家、客户、订单、设备、库存和政策 | 演示数据 |
| 外部履约 | 独立模拟服务产生退款和发货回执，可注入故障 | 模拟退款与物流 |
| 企业上线 | 第一版尚未接任何真实企业、资金或物流账户 | 需企业适配和验收 |

所有演示入口持续显示“演示环境 · 模拟订单与履约”。模拟退款成功时写“模拟退款成功”；不要写成真实到账。系统必须允许停用演示适配器后进入人工办理模式，不得默默回退到模拟成功。

### 1.1 工程完成度的判断

必须同时满足：有可运行系统、有真实业务记录、有失败处理、有可重复验收。页面漂亮、模型回复自然、README 写着 production-ready，都不能替代这些条件。

个人项目的合理终点是上述试用版。没有真实客户和公司验收时，简历使用“设计并实现”“模拟业务环境验证”“可部署原型”等表述，不写“已在企业投产”“日处理数千单”等没有证据的结果。

## 2. 固定业务范围

### 2.1 第一版必须完成

- 一家商家、一个网站聊天入口、中文界面、CNY 单币种、一次性付款订单。
- 耳机、键盘、鼠标等少量电子产品；不需要接真实电商平台。
- 两条主流程：退货退款、同 SKU 质量问题换货。
- 一个订单可包含多台设备；一个售后申请只处理一台设备。
- 查询本人订单、选择具体设备、核验售后条件、补充故障信息、查看政策依据。
- 生成申请预览，由客户在已登录门户明确确认。
- 员工初审、客户填写寄回物流、员工收货和验收、执行退款或换货。
- 换货缺货等待、补货后继续、异常转人工、业务记录和会话关联。
- 人工接管、Agent 暂停与显式交回。
- 重复消息、重复提交、并发库存、进程重启、外部结果未知的处理。
- 可重复启动的演示数据、真实模型评测、自动化业务验收。

### 2.2 首版约束

退款只处理该台设备分摊后的商品实付金额；演示订单采用免运费，金额已包含演示所需税费。运费退款、单独税额调整、赠品、零元商品、分期、多笔支付、组合套装、跨币种、跨 SKU 补差价均不进入自动流程，返回明确的人工处理原因。

换出的新设备必须保存与原申请的关联以及出库设备标识。新品再次申请售后在本版转人工，不将其判断为“没有售后权利”。

维修、上门取件、图像自动鉴定、自动拒赔、多渠道、复杂审批流、多租户、企业 SSO、自动采购补货、多个协作 Agent、Kubernetes 不属于首版交付。

### 2.3 三种运行方式

| 配置 | 数据与履约 | 使用场景 |
|---|---|---|
| APP_MODE=demo | 演示身份和订单，DemoProvider 模拟退款及物流 | 本地开发、录屏、面试 |
| APP_MODE=pilot + FULFILLMENT_MODE=manual | 经公司确认的身份和订单来源，履约由员工在外部完成并登记证据 | 售后受理与人工办理试用 |
| APP_MODE=pilot + FULFILLMENT_MODE=external | 真实适配器，经过沙箱和小范围验收 | 后续公司受控接入 |

pilot 模式拒绝加载 demo 账号切换、故障注入界面和模拟履约适配器。未配置真实连接器时，external 模式启动失败，不能返回伪造成功。

## 3. 用户真正会使用到的产品

### 3.1 客户门户

| 页面 | 内容与操作 |
|---|---|
| /login | 登录；仅 demo 环境显示演示账号说明 |
| /orders | 本人的订单、签收时间、设备、实付金额、申请入口 |
| /orders/:id | 商品与设备列表、已存在的申请、适用政策摘要、独立申请表单 |
| /proposals/:id | 办理类型、设备、原因、金额或换货 SKU、政策版本、条件、确认按钮 |
| /cases | 本人的申请列表与下一步 |
| /cases/:id | 状态时间线、待补材料、寄回物流、履约记录、允许阶段的取消操作 |

页面右下嵌入 Chatwoot Website Widget。Agent 返回申请预览链接，用户在门户确认。第一版不依赖聊天气泡里的复杂表单和自定义按钮协议。

确认页不接受 URL 中传来的金额或客户身份；所有内容从后端读取。刷新和重新登录之后，申请仍然存在。

### 3.2 员工使用的两个界面

- Chatwoot：接待消息、阅读上下文、人工接管和回复。
- 自己的业务管理台：审核申请、核对设备、录入验收、处理库存、执行或核对履约。

两边通过会话链接、申请编号、私有备注互相跳转。首版允许分别登录，不为做单点登录改造 Chatwoot 核心，也不强制嵌入 iframe。

### 3.3 自研管理台

| 页面 | 必须具备的能力 |
|---|---|
| /staff/cases | 按状态、类型、异常原因筛选；显示真实待办数 |
| /staff/cases/:id | 订单和设备、规则判断、政策引用、客户确认快照、审计时间线 |
| /staff/operations | 执行中、待核对、明确失败的退款和出库操作；人工核查入口 |
| /staff/inventory | 可销售新品、预占数、可用数、补货记录 |
| /staff/conversations/:id | 当前接管方、关联申请、接管与交回按钮 |
| /admin/policies | 查看版本包、校验、发布新版本；旧版本不可直接修改 |
| /admin/system | Agent 开关、依赖状态、任务积压、失败任务、运行模式 |

操作按钮依权限和状态显示。初审按钮文案为“同意寄回”，验收通过后的按钮为“确认验收并安排履约”。不要把这两件事都叫“批准退款”。

采用一个 React 工程、客户和员工两组路由。布局以列表、详情、状态标签、时间线、确认框为主；所有统计来自数据库。不制作虚构成交额或自动化率图表。

## 4. 技术方案与开源复用

### 4.1 选定技术

| 部分 | 技术 | 选择理由 |
|---|---|---|
| 聊天入口与人工客服 | 自托管 Chatwoot 社区可用功能 | 复用成熟会话与接待界面 |
| 自研服务 | Python 3.11、FastAPI、Pydantic 2 | 一个业务后端，便于 Codex 分模块实现 |
| 数据访问 | SQLAlchemy 2、Alembic | 显式事务与迁移 |
| Agent 编排 | LangGraph，单 Agent | 保存执行状态、工具循环和恢复 |
| 数据库 | PostgreSQL 16 + pgvector | 业务数据、政策向量、任务、审计 |
| 中文检索 | jieba 分词 + BM25 + 向量检索 | 避免把英文全文检索直接用于中文政策 |
| 后台任务 | 自研轻量 Postgres job worker | 事务内写任务，减少额外队列系统 |
| 前端 | React、TypeScript、Vite | 一套门户和业务台 |
| 部署 | Docker Compose；试用时加 HTTPS 反向代理 | 单机可复现部署 |
| 验收 | pytest、真实 Postgres 集成测试、Playwright、任务评测脚本 | 验证业务结果与实际页面 |

Chatwoot 本身需要 Rails、Sidekiq、Redis 和数据库；Redis 在本方案中服务于 Chatwoot。自研业务 worker 使用 Postgres 任务表，不再增加 Kafka 或另一套队列框架。Chatwoot 的官方 Docker 部署方式见来源 [8]。

一个 Postgres 实例可为 chatwoot、aftersales、demo_provider 创建独立数据库和用户。业务服务不能直接写 Chatwoot 的 Rails 表，只经 API 通信。LangGraph 检查点放在业务数据库的独立 schema；它不是售后单的权威数据源。

### 4.2 架构

~~~mermaid
flowchart TD
  Portal["客户门户"] --> API["业务 API"]
  Portal --> CW["Chatwoot"]
  Staff["客服"] --> CW
  Staff --> Console["业务管理台"]
  Console --> API
  CW --> Inbox["验签与事件入库"]
  Inbox --> Worker["后台 Worker"]
  Worker --> Agent["单 Agent"]
  Agent --> Domain["规则与业务服务"]
  API --> Domain
  Domain --> DB["Postgres"]
  Worker --> External["履约适配器"]
  Worker --> CW
~~~

API 和 worker 共用同一份领域代码；不为每个模块创建微服务。独立 DemoProvider 只是用于模拟公司外部系统，验证“外部已成功但本地没有收到响应”等故障。

### 4.3 模型选择

通过 ModelProvider 接口接入一个支持工具调用的模型。模型名称、API base URL、密钥、超时和预算全部配置化；首版验证一个真实供应商即可，同时提供显式 MockProvider 用于无费用的确定性测试。

Codex 是开发工具，应用运行时仍需要自己的模型服务配置。不要读取浏览器登录态、ChatGPT 会话凭据或开发工具的认证文件来充当应用 API key。

按实际供应商接口实现协议适配；不要假设所有模型供应商的工具调用格式完全兼容。依赖版本在阶段 0 实测后写入 uv.lock 和前端 lockfile。

### 4.4 自己必须实现的部分

开源部分负责聊天界面和执行框架。项目自己的核心是：售后领域模型、政策版本与规则一致性、订单权限、客户确认、审核验收、库存和金额约束、故障恢复、人工接管控制、评测数据及验收。

这些实现都应放在自己的仓库里，保留第三方许可证与来源说明。不将 Chatwoot 自带的客服界面描述为自己开发。

## 5. 阶段 0 先验证 Chatwoot 契约

本方案按 2026-10-21 可查资料选择 Chatwoot v4.19.0 作为初始候选，固定镜像版本和 digest，不用 latest。当前文档的 Bearer 认证适用于 v4.19.0 及以后 [1][5]。实际实施如需更换版本，先更新兼容记录并重跑本节检查。

### 5.1 初始化与受控适配器

运行时的 ChatwootAdapter 只暴露：

- get_conversation
- get_messages
- send_reply
- append_private_note
- handoff
- return_to_bot

初始化管理员 token 仅用于配置，不用作运行时默认兜底。优先使用 bot token；如固定版本的读取权限不足，可配置专用客服账号 token，并在应用限制允许方法和 inbox。不要称它为 Chatwoot 原生只读 token。

必要 API 契约：

| 用途 | 方法和路径 | 关键字段 |
|---|---|---|
| 创建 bot | POST /api/v1/accounts/{account_id}/agent_bots | name、outgoing_url、bot_type=0 |
| 绑定 inbox | POST /api/v1/accounts/{account_id}/inboxes/{inbox_id}/set_agent_bot | agent_bot，字段名不是 agent_bot_id |
| 发回复或私有备注 | POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/messages | message_type=outgoing，private=false/true |
| 读会话 | GET /api/v1/accounts/{account_id}/conversations/{conversation_id} | 校验实际会话状态和来源 |
| 交人工 | POST .../conversations/{conversation_id}/toggle_status | status=open |
| 交回 bot | 同上 | status=pending，另经本地显式授权 |
| 订阅控制事件 | POST /api/v1/accounts/{account_id}/webhooks | conversation_status_changed、conversation_updated |

这些接口见来源 [2]—[7]。注册脚本按保存的 ID 或唯一配置名复用现有 bot 和 inbox，重复启动不能不断创建新对象。

### 5.2 三种不同密钥

必须区分：API access token、Webhook signing secret、Widget 身份验证 secret。它们不可混用。AgentBot 指南说明创建 bot 会产生验签 secret，但部分 OpenAPI 示例没有列出该字段；阶段 0 必须通过固定版本响应或设置页取得并验证，缺失则报配置错误 [2]。

两条回调使用不同 secret：

- /integrations/chatwoot/agentbot：消息事件。
- /integrations/chatwoot/control：账户级会话控制事件。

不要依赖 AgentBot 在所有版本都投递完整状态事件；额外的账户级回调只订阅状态和会话更新，减少消息重复入口。

### 5.3 Webhook 接收契约

读取 X-Chatwoot-Signature 和 X-Chatwoot-Timestamp，缺失则拒绝；X-Chatwoot-Delivery 可缺失。timestamp 是 Unix 秒；校验窗口时可以解析为整数，签名拼接仍使用 Header 的原始字符串。

根据官方协议，以原始请求体计算签名 [3]：

~~~text
signed_bytes = timestamp_utf8 + b"." + raw_body
expected = "sha256=" + HMAC_SHA256(secret, signed_bytes).hexdigest()
~~~

比较使用常量时间函数；时间戳窗口默认正负 300 秒，可配置并保持服务器时钟同步。签名无效、缺失密钥或时间戳非法直接拒绝。

验签后检查 account_id 和 inbox_id 白名单；在一个事务中保存事件与任务，再返回 200。数据库写入失败不能提前返回成功。LLM 调用不在回调请求内执行。

消息去重使用 instance_id + account_id + event_type + 顶层 message_id；Delivery header 可辅助记录，不能假定必有。控制事件优先使用 delivery_id；没有时以带账户范围的规范化事件摘要辅助去重，最终以读取到的会话状态为准。

### 5.4 真实网络验证

Windows 使用 WSL2 + Docker 的 Linux 容器。不要要求在 Windows 原生安装 Rails。

Chatwoot 对私网回调有 SafeFetch 校验。官方维护者关于 SAFE_FETCH_ALLOW_PRIVATE_NETWORK 的说明针对特定 API inbox 路径，不能直接假定同样覆盖所有 AgentBot 回调 [9]。

先用合法内部 hostname，如 agent-api.internal，测试固定版本。若被阻止，按该版本官方支持方式配置；开发环境可使用指向接收器的 HTTPS tunnel，并保留验签。不要修改 Ruby 核心来绕过校验。

### 5.5 必须留下的验证证据

1. Widget 发真实消息，回调到达。
2. 正确签名通过；改正文、错 secret、过期时间戳失败。
3. 同一消息重复投递只创建一个待处理任务。
4. bot 回复显示在 Widget，自己发出的消息不触发循环。
5. 私有备注只出现在客服端。
6. 人工接管后，滞后的模型任务不能继续生成新的办理方案或发送回复。
7. 显式交回后恢复。
8. 逐项验证 bot token 的发消息、读会话、切换状态权限。
9. 强制 Widget 身份验证生效；伪造用户标识被拒绝。
10. Sidekiq 实际运行，异步回调有效，不能只检查 Rails 首页能打开。

产物：ChatwootAdapter、脱敏真实 fixtures、bootstrap 脚本、docs/chatwoot-compatibility.md。任何未验证能力必须标为未验证，不能用假响应冒充。

## 6. 身份、权限与会话控制

### 6.1 身份从服务端来

门户用户通过账号密码登录，密码使用 Argon2id，会话用 HttpOnly cookie。HTTPS 环境启用 Secure，SameSite=Lax；写操作做 CSRF 防护。角色固定 customer、support、admin。

每个工具的 customer_id 由已验证的服务端上下文注入，不放进模型可填写的参数 schema。输入别人的订单号时，后端查询也必须带本人范围；返回一致的不可访问响应，避免泄露订单是否存在。

Widget 在后端生成 identifier_hash，并在 inbox 强制要求身份验证。identifier 只能从当前已登录 session 推导；签名接口不接受客户端任意指定其他客户的标识。计算方式为 HMAC-SHA256(widget_identity_secret, identifier_utf8).hexdigest()。Chatwoot 官方将 contact matching 和 identity validation 分开，普通邮箱、名称或 custom attributes 不能证明身份 [10][11]。

本项目由服务端以 instance_id、account_id、inbox_id、contact_id 的完整组合查询可信 customer_id 绑定，保留核验来源和有效期；不能从消息、邮箱或可编辑属性生成业务身份。阶段 0 检查固定版本实际身份字段与强制设置；只接受服务端建立并核验的绑定。客户退出登录或换账号时 reset Widget，并撤销对应会话授权；禁止继承前一个人的聊天身份。未建立可信绑定或授权已失效的会话只能得到公共说明和登录指引。A02 同时验证两个已登录客户之间的标识替换与退出换号。

### 6.2 会话与业务分别管理

本地 conversation_control：

- BOT_ACTIVE：允许处理客户消息。
- HANDOFF_PENDING：已请求人工，立即停止新的 Agent 行为，等待同步。
- HUMAN_ACTIVE：客服处理；只有显式“交回机器人”操作恢复。

Chatwoot 的 pending/open/resolved 是聊天状态；售后单状态另存。会话 resolved 不会自动完成、取消售后单。

只对新的、公开的 incoming 客户消息启动 Agent。outgoing、自身回复、私有备注、message_updated 不启动新一轮。消息类型做归一化，真实 webhook 与 API fixture 分开维护。

人工接管首先提交本地控制状态与 revision，然后取消未提交的 Agent 草稿/回复任务，再同步 Chatwoot。同步失败时保留静默并重试。客服直接在 Chatwoot 操作时，通过控制回调与执行前 GET 复查检测。

启动模型任务、保存 Agent 新草稿和发送回复时，必须同时满足本地 BOT_ACTIVE、实时 Chatwoot status=pending、未确认存在人工持有、input_revision 未过期。open、resolved、snoozed 或无法确认状态时停止新自动行为。人工指派和人工公开 outgoing 消息也作为接管信号；根据固定版本 fixture 区分 User 与 AgentBot，不能只比较数值 ID。M0 需要验证 inbox 的自动指派设置，避免 pending 会话被误认为已交给人工。

迟到的 pending 事件不能把 HUMAN_ACTIVE 自动恢复；交回必须关联一次员工显式授权，并验证/清理该版本的人工指派状态。已过期的模型结果丢弃；本轮内所有未处理客户消息都应纳入上下文，不能只取最后一句而丢掉前面的约束。

同一会话的 Agent 任务使用独立会话租约和 fencing revision 串行领取，不能同时调用同一 LangGraph thread。记录消息处理水位；推理期间的新消息进入待处理集合。旧结果过期时保留未确认处理的消息，下一轮合并读取，不让 last_processed_message_id 跳过未消费内容。

由于 Chatwoot 与业务服务没有共同事务，不能承诺跨系统绝无竞态；目标是接管事件及时处理、动作前复查、状态不明时停止自动处理。

### 6.3 不要误停已经确认的业务

人工接管停止的是 Agent 对话与新草稿行为，不会自动取消已被客户确认、员工验收通过的履约任务。退款和发货 worker 按业务状态与授权执行。取消售后必须走独立业务操作。

当人工持有会话时，自动履约结果先更新门户、管理台与内部备注；不让聊天 Agent 抢着回复客户。

## 7. 业务模型与状态机

### 7.1 以单台设备为单位

order_unit_id 是业务身份，序列号是核验资料。不能把用户输入序列号当成订单归属依据。

导入订单时将订单行实付商品金额确定性分摊到每台设备：按原订单行优惠后的金额分摊，整数除法后的余数按稳定单位序号分配，保证各设备金额之和等于该订单行实付金额。不同价格商品先完成订单行金额分摊，不能直接把整单金额按件数平均。

一张申请固定一个 order_unit_id、一个办理类型。活动申请以及已完成售后都会占住该原设备的办理权；真正取消、初审拒绝或实物已妥善处置的无履约关闭，才允许按规则再次申请。

### 7.2 草稿独立于售后单

proposals 保存未提交草稿：设备、类型、原因、证据、退款金额或换货 SKU、政策版本、资格结果、摘要哈希、版本号、过期时间，以及换货收件地址快照。

换货地址默认取该订单原地址；客户可在已登录的预览表单中核对或修改，再重新生成摘要哈希和确认。姓名、电话、地区与详细地址在后端做结构和长度校验，不进入模型上下文。已提交申请的地址不再直接修改；需变更时转人工并重新确认，首版不自动变更已经创建的履约请求。DemoProvider 也必须收到明确地址，不能假定目的地。

客户点击确认时提交 proposal_id、expected_version、summary_hash 和 Idempotency-Key。服务端重新校验身份、资格、设备是否已办理、报价和库存提示的时效性。金额、类型或关键条件变化时返回 409，生成新的预览并重新确认。

默认草稿 30 分钟过期，配置化。后端以收到确认时刻确定是否在申请窗口内，保存 accepted_at 和资格快照。后续初审、验收、补货不会用更晚的当前时间把及时受理的申请重新判为过期；规则复核采用 accepted_at，实物条件按验收事实更新。保存草稿不等于已受理；“聊天里说好的”也不能替代确认接口。

### 7.3 售后单状态

| 状态 | 含义 | 允许推进者与条件 |
|---|---|---|
| PENDING_REVIEW | 客户已确认，等待初审；可标记待补资料 | 员工审核受理快照及补充事实 |
| AWAITING_RETURN | 同意寄回，尚未收货 | 客户填物流，员工确认收货 |
| INSPECTING | 已收货，等待验收 | 员工核对设备、配件与故障 |
| WAITING_STOCK | 验收通过，但无可销售新品 | 补货后重新原子预占 |
| READY_TO_FULFILL | 验收通过，退款额度或新品已预留 | 持久化任务交 worker |
| FULFILLING | 外部履约执行中或结果待核对 | 回执、查询或有证据的人工核对 |
| MANUAL_REVIEW | 异常或实物处置待人工处理 | 员工按受控操作处理，不能任意改状态 |
| COMPLETED | 退款成功；或换货已确认出库发货 | 外部成功证据 |
| REJECTED | 初审拒绝，商品尚未寄回或收货 | 员工给出原因 |
| CANCELLED | 符合允许条件的申请取消 | 客户或员工 |
| CLOSED_UNFULFILLED | 未退款/换货，但实物处置已完成 | 员工记录退回客户等证据 |

用户界面可将 COMPLETED 的换货显示为“换货已发出”，不要声称已经签收。仅创建面单不算完成，必须有出库/发货确认。真实公司如要求签收才关单，属于适配时需要调整的业务规则。

### 7.4 两条主流程

退货退款：

~~~text
准备草稿 → 客户确认 → 初审同意寄回 → 退回与验收
→ 预留可退金额 → 提交退款 → 查询/回执确认 → 完成
~~~

同 SKU 换货：

~~~text
准备草稿 → 客户确认 → 初审同意寄回 → 退回与验收
→ 原子预占可销售新品（缺货则等待）→ 出库发货 → 完成
~~~

初审批准只代表允许寄回；验收通过是最终履约前的人工授权节点。所有申请第一版都保留这两个节点。

### 7.5 异常与实物责任

- 初审缺资料：仍为 PENDING_REVIEW，写 pending_reason 和需要补充的字段。
- 验收不符、序列号异常：进入 MANUAL_REVIEW，保留实物在库事实。
- 员工可要求复核；修正验收记录须新建事件、记录理由，不能覆盖历史。
- 确定不能履约且设备已收到时，记录退回客户的操作和出库证据后，才能 CLOSED_UNFULFILLED。
- 坏品、已退回设备放在独立 returned_items 记录，不能直接增加可销售库存。
- 缺货后可补货继续。本版不自动换 SKU、不自动改成退款；需要改变办理类型时由人工与客户重新确认，超出首版自动流程则明确转人工。
- 客户自助取消仅在 PENDING_REVIEW，或 AWAITING_RETURN 且尚未登记寄回时开放。登记寄回后由员工处理，避免物流在途但申请已消失。
- FULFILLING 或外部 UNKNOWN 时禁止直接取消和释放预留，先对账。
- 所有迁移在领域服务校验；没有通用的“把状态设成 X”接口。


## 8. 数据库契约

所有业务主键用 UUID；展示编号独立生成。时间用 UTC timestamptz，金额用整数分并带 currency。JSON 只保存可变规则、快照和工具事件，不把关键状态、权限或金额藏进无约束 JSON。

### 8.1 必要实体

| 表 | 核心字段与约束 |
|---|---|
| users | id、role、password_hash、active；演示账号须有标记 |
| sessions | user_id、token_hash、expires_at、revoked_at |
| products | id、sku、name、category、active |
| orders | customer_id、external_id、payment_ref、paid_at、delivered_at、currency、source、shipping_address |
| order_items | order_id、product_id、quantity、net_goods_paid_cents |
| order_units | order_item_id、unit_index、serial_number、allocated_paid_cents、sale_policy_version_id；行内序号唯一 |
| chat_bindings | instance/account/inbox/contact 与 customer_id 的可信绑定、授权有效期及核验记录 |
| conversations | 外部会话 ID、customer_id、control_mode、control_revision、input_revision、消息水位、执行租约与 fencing revision |
| proposals | customer_id、order_unit_id、type、payload、shipping_address_snapshot、policy_version_id、version、summary_hash、expires_at、confirmed_case_id |
| after_sales_cases | proposal_id、customer_id、order_unit_id、type、status、version、accepted_at、policy_snapshot、confirmed_payload、pending_reason |
| case_events | case_id、事件、前后状态、actor_type/id、request_id、operation_id、时间、结构化理由 |
| evidence_attachments | owner_id、case/proposal_id、私有存储键、MIME、大小、摘要 |
| returned_items | case_id、原设备标识、收货时间、核验结果、保管状态、最终去向 |
| stock_units | product_id、可销售新品设备标识、AVAILABLE/RESERVED/DISPATCHED、来源记录 |
| inventory_reservations | case_id、stock_unit_id、ACTIVE/CONSUMED/RELEASED、时间 |
| refund_allocations | case_id、order_unit_id、amount_cents、RESERVED/SETTLED/RELEASED |
| operations | case_id、kind、obligation_key、不可变 request_snapshot/hash、idempotency_key、execution_mode、provider_name/version、environment_id、status、provider_id、结果与核对记录 |
| jobs | kind、payload、dedupe_key、status、attempts、available_at、lease_owner、lease_until、lease_version、last_error |
| webhook_inbox | provider、event_key、payload_hash、脱敏事件、received_at、处理状态 |
| outbound_messages | intent_key、conversation_id、内容、control_revision、status、chatwoot_message_id、last_error |
| idempotency_requests | actor、route_scope、key、request_hash、response_ref、状态、过期时间 |
| policy_versions | version、scope、effective_range、rules_json、document_text、content_hash、published_at |
| policy_chunks | version_id、section_id、text、embedding、embedding_model、retrieval_metadata |
| agent_runs / agent_events | 会话、模型、工具、耗时、用量、引用、结果、错误、关联 case/operation |
| system_settings | Agent 开关、已批准规则配置；不保存明文 API key |

LangGraph 自带检查点表由其对应迁移/初始化管理，不手工模拟成一张随意 JSON 表。

### 8.2 必须有的约束

- 一份 proposal 最多确认生成一个 case。
- 同一 order_unit_id 最多一个未关闭或已完成的 case；排除条件仅为 REJECTED、CANCELLED、CLOSED_UNFULFILLED。可使用部分唯一索引。
- 同一 case 的同一种履约义务 obligation_key 唯一。重复批准不能生成第二个退款或出库义务。
- 一个 stock_unit_id 最多一个 ACTIVE 预占；一个 case 最多一个 ACTIVE 预占。
- RESERVED 库存必须能找到相应有效预占；DISPATCHED 必须有成功出库记录。
- 同一 order_unit_id 的有效退款占用与已退金额不能超过 allocated_paid_cents。本版整台全额商品退款，禁止模型修改金额。
- 同一订单行的设备分摊金额之和必须等于行实付金额。导入验证失败整批回滚或逐行明确报错，不静默修正。
- webhook 业务事件键、job dedupe_key、outbound intent_key 唯一。
- 所有状态迁移递增 case.version；员工提交 expected_version，避免旧页面覆盖新结果。

金额和跨行总量约束通过锁定相应 order_unit 与领域事务保证；不能仅声明数据库 CHECK 就能校验任意跨表和聚合关系。

### 8.3 事务边界

确认申请的单个事务：

1. 核验客户、幂等键和请求摘要。
2. 锁定 proposal 和 order_unit。
3. 校验草稿版本、有效期、政策快照、申请窗口、设备可办理性。
4. 创建 case，保存客户确认快照。
5. 写 case_event、后续通知 job，保存幂等结果。
6. 提交，再返回 case_id。

验收通过的单个事务：

1. 锁 case 与 order_unit，检查员工权限及版本。
2. 写不可覆盖的验收事件。
3. 退款：预留该设备可退金额。换货：原子挑选一台 AVAILABLE 新品并设为 RESERVED。
4. 缺货时进入 WAITING_STOCK，不创建出库义务。
5. 资源准备好时创建唯一 operation，并固定其 execution_mode 与 provider。demo/external：case 进入 READY_TO_FULFILL，创建执行 job。manual：在同一事务将 case 置为 FULFILLING、operation 置为 AWAITING_MANUAL，只创建人工待办通知，不调用外部履约接口。
6. 提交。外部 HTTP 请求在事务之外执行。

预占新品可用 SELECT ... FOR UPDATE SKIP LOCKED 选择设备；不要“先读 count，再无锁扣减”。首版用逐台库存状态避免计数和实物编号不一致。无需建完整仓储系统。

### 8.4 退款与库存释放

- 确定未执行且已正式取消/关闭的操作，才释放预留。
- 外部结果 UNKNOWN 时持续占用原设备办理权、金额和库存。
- 退款成功后 allocation 进入 SETTLED。
- 换货出库确认后 reservation 进入 CONSUMED，stock_unit 进入 DISPATCHED；记录新设备标识。
- 外部终态 FAILED 转人工，首版不支持修改参数后自动重办。修改请求不能复用原幂等键，也不能偷偷换键创建第二个义务。后续要支持重办，需要另行设计“义务与执行尝试”的关系及前次零效果证据。
- 已实际退款或出库不能用“重置状态”撤回。

## 9. 政策与检索

### 9.1 示例政策只是演示规则

提供两个不可变版本，例如：

- demo_policy_v1：未使用且配件齐全的商品在签收后 14 天内可申请退货；质量问题在 30 天内可申请同 SKU 换货。
- demo_policy_v2：用于测试不同商品适用条件或不同窗口，不自动追溯修改旧订单。

这些数字是虚构商家的演示政策，不是任何地区的法律解释或行业统一承诺。

窗口按精确时间定义：申请确认时间小于 delivered_at 加窗口时长才通过，恰好到期视为到期。业务界面明确显示截止时刻。演示采用这一口径，接公司时替换为公司已审定口径。

订单设备记录购买时适用的 policy_version_id，以签收时间计算窗口。规则缺失、政策版本缺失、来源矛盾时进入 needs_review，不能猜一个版本继续办理。

### 9.2 文本与规则一起发布

一个政策包包括：

~~~json
{
  "version": "demo_policy_v1",
  "scope": {"categories": ["headphone", "keyboard", "mouse"]},
  "rules": {
    "return_window_days": 14,
    "exchange_window_days": 30,
    "return_requires_unused": true,
    "return_requires_all_accessories": true,
    "exchange_requires_inspection": true,
    "same_sku_only": true
  },
  "sections": [
    {"id": "return_eligibility", "text": "演示商家退货条件……"},
    {"id": "exchange_eligibility", "text": "演示商家换货条件……"}
  ],
  "rule_source_map": {
    "return_window_days": "return_eligibility",
    "exchange_window_days": "exchange_eligibility"
  }
}
~~~

后台对结构、适用范围、条款映射和金额口径进行校验；管理员审核后一次发布。发布后禁止原地改文案或规则。修订生成新版本及内容哈希。

case 保存受理时的政策和判断快照。政策更新不会让已受理申请的金额或条件静默变化。需要改动已确认办理内容时必须显式新版本、重新确认；首版可以交人工处理该变更。

### 9.3 检索流程

1. 从服务器确认的设备和订单选出适用版本。
2. 限制版本、商品类别和条款范围。
3. 中文 BM25 与 pgvector 向量检索分别取候选。
4. 用 RRF 合并，返回最多 6 段相关条款。
5. 返回 version_id、section_id、引用文本、来源标题。
6. Agent 用规则引擎结果解释，并引用同版本条款。

业务资格、金额和状态完全不通过向量相似度决定。没有匹配证据时说明需要核实，不能生成不存在的政策。

首版只导入管理员维护的 Markdown/结构化政策包，不做任意网页爬取和复杂 PDF OCR。少量政策可精确向量检索，不强制 HNSW 或重排模型。embedding 模型与维度在初始化时确定并校验，换模型须重建对应索引/向量，禁止混用。

### 9.4 规则函数

evaluate_eligibility 输入为服务端订单设备、请求类型、已收集字段、固定时钟与政策包，输出：

~~~json
{
  "decision": "missing_information",
  "reason_codes": ["ACCESSORIES_STATUS_REQUIRED"],
  "missing_fields": ["accessories_complete"],
  "policy_version": "demo_policy_v1",
  "source_refs": [
    {"version": "demo_policy_v1", "section_id": "return_eligibility"}
  ],
  "refund_amount_cents": 32900,
  "currency": "CNY"
}
~~~

decision 只取 eligible、ineligible、missing_information、needs_review。规则函数是普通 Python 代码，可单独测试，不依赖 LLM。未知信息不能按 true 补齐。

## 10. Agent 设计

### 10.1 一个 Agent 足够

Agent 负责识别诉求、选择订单、追问缺失资料、查询政策、准备申请和解释状态。它不负责审批、库存扣减或退款金额决策。

状态保存会话标识、已知字段、关联订单/草稿/申请、下一步、工具结果引用与处理过的消息。身份和权限每轮从服务器重新装载；摘要不能成为权限来源。

推荐图节点：

- validate_context：检查身份、开关、会话控制权与消息版本。
- load_context：读取最近消息和权威业务状态。
- decide：模型输出下一工具调用或用户可见答复。
- execute_tool：参数 schema 校验并调用受控工具。
- prepare_response：整理事实、引用和下一步。
- persist_outbound：再次检查控制权，将发送意图落库。
- finish_or_wait：结束本轮，等待客户或员工事件。

工具循环要有上限，建议首版每轮最多 6 次模型调用、8 次工具调用，总时长和供应商超时配置化。超限记录原因并给出可继续的提示或转人工，不无限自我反思。

### 10.2 工具契约

| 工具 | 模型可以传入 | 服务端必须约束 |
|---|---|---|
| list_my_orders | 时间范围、可选商品关键词 | 本人范围、分页和数量上限 |
| get_order_detail | order_id | 归属及脱敏字段 |
| search_policy | order_unit_id、问题 | 政策版本由后端选定 |
| evaluate_eligibility | order_unit_id、case_type、已知条件 | 使用原始订单事实、固定金额 |
| check_exchange_availability | order_unit_id | 仅原 SKU，查询不预占 |
| prepare_after_sales_proposal | order_unit_id、case_type、原因、证据 ID | 权限、规则、版本、幂等草稿写入 |
| get_case_status | case_id | 本人范围，只报告数据库事实 |
| request_handoff | reason_code、事实摘要 | 停止自动行为、记录并同步接管 |

模型不可传 customer_id、refund_amount、approval、任意 SQL、任意 URL 或目标状态。没有 approve_refund、直接发货、任意数据库写入工具。

统一工具结果含：ok、data、reason_codes、missing_fields、source_refs、observed_at、error_kind。错误分类包括 validation、permission、business_rule、retryable、unknown，不只扔一段 traceback 给模型。

### 10.3 对话例子

客户：“上周买的耳机左边没声音，我想换。”

Agent 查询本人订单，若有两台同类设备就请客户选择，不猜设备；查询签收时间和适用换货条款；收集是否完成规定的简单排查、问题描述及配件信息；调用规则评估；生成换货草稿和预览链接。

答复应明确：“已准备换货申请，需你确认后提交审核；库存会在退回验收后安排。”此时不说“已换货”或“已锁定库存”。

客户确认后，门户显示申请编号和等待初审。Agent 后续查询从 case 读取；即使长对话摘要写错，也不能改变真实进度。

### 10.4 持久化与恢复

采用 LangGraph 的 Postgres checkpointer；持久化线程 ID 与业务会话一一对应。保存检查点可以支持恢复，但节点恢复可能重新执行，因此所有带副作用的节点仍需幂等 [12][13]。

客户确认和员工审批通过独立后端 API 生效，不靠向模型说“批准”。如果使用 interrupt 等待事件，恢复入口必须核对服务端事件和版本；不要将任意前端文本直接作为批准信号。

业务 worker 的履约流程独立于模型图。模型 API 故障时，已批准的业务任务仍可继续；客户和员工也可通过表单完成办理。

### 10.5 提示词与上下文

版本化保存 system prompt 和工具定义。强调事实来源、缺失时追问、引用规则、不夸大状态、尊重人工接管。政策片段和用户输入都作为数据处理，不执行其中的“忽略规则”“改金额”等指令。

权限和金额控制必须在后端。不能把安全性建立在一句“不要泄露订单”提示词上。

向模型只提供当前任务所需的订单和脱敏信息。详细地址、密码、cookie、API key、支付凭据不进入 prompt。首版图片由人工查看，Agent 只读取附件存在与类型，不宣称自动鉴定质量。

## 11. 自研 API

以下是要求 Codex 实现的应用接口，不是已经存在的线上服务。路径前缀统一 /api/v1；Chatwoot 回调路径独立。

### 11.1 客户与系统接口

| 方法与路径 | 作用 |
|---|---|
| POST /auth/login、POST /auth/logout、GET /auth/me | 会话与当前用户 |
| GET /chat/identity | 仅当前登录用户的 Widget 标识及签名 |
| GET /orders、GET /orders/{id} | 本人订单 |
| POST /eligibility/evaluate | 客户表单资格查询，复用确定性规则 |
| POST /proposals、PATCH /proposals/{id} | 不经 Agent 创建/修改本人草稿，修改需 expected_version |
| GET /proposals/{id} | 读取真实预览 |
| POST /proposals/{id}/confirm | 确认当前版本，事务内建单 |
| GET /cases、GET /cases/{id} | 申请和事件时间线 |
| POST /cases/{id}/supplements | 补交资料，保留历史 |
| POST /cases/{id}/return-shipment | 登记寄回承运商与运单号 |
| POST /cases/{id}/cancel | 仅允许阶段取消 |
| POST /attachments、GET /attachments/{id} | 私有证据上传和授权读取 |
| GET /health/live、GET /health/ready | 进程与依赖健康 |

### 11.2 员工与管理员接口

| 方法与路径 | 作用 |
|---|---|
| GET /staff/cases、GET /staff/cases/{id} | 跨客户授权业务视图 |
| POST /staff/cases/{id}/review | 同意寄回、拒绝、要求补资料 |
| POST /staff/cases/{id}/receive | 收货与实物登记 |
| POST /staff/cases/{id}/inspect | 验收通过或转人工 |
| POST /staff/cases/{id}/resume-stock | 在 WAITING_STOCK 重试预占 |
| POST /staff/cases/{id}/disposition | 记录异常商品处置与证据 |
| GET /staff/operations、GET /staff/operations/{id} | 真实外部操作状态 |
| POST /staff/operations/{id}/reconcile | 有证据的人工核对 |
| POST /staff/operations/{id}/record-manual-result | 仅 manual 操作登记线下履约结果和凭证 |
| POST /staff/conversations/{id}/takeover | 本地先停止 Agent，再同步 |
| POST /staff/conversations/{id}/return-to-bot | 显式交回并校验实际状态 |
| POST /admin/inventory/receipts | 导入可销售新品，留来源 |
| POST /admin/policies/validate、POST /admin/policies/publish | 校验并发布新版本 |
| POST /admin/agent/switch | 启用或停用新的 Agent 任务 |

员工业务变更都携带 expected_version；创建或执行动作带 Idempotency-Key。接口拒绝同键不同内容。无权资源采用一致的 404/403 策略；请求格式错误 422、业务或版本冲突 409、依赖暂不可用 503。

### 11.3 确认请求示例

~~~http
POST /api/v1/proposals/6e9.../confirm
Idempotency-Key: client-generated-uuid
X-CSRF-Token: session-bound-token
Content-Type: application/json
~~~

~~~json
{
  "expected_version": 3,
  "summary_hash": "sha256-of-server-canonical-proposal"
}
~~~

返回 case_id、display_number、status、version。后端从草稿读取设备、金额、类型和客户，不接受这些字段被前端覆盖。

### 11.4 进度与附件

首版使用普通轮询获取申请和任务状态即可，不额外搭一套聊天 WebSocket。Chatwoot Widget 负责聊天实时更新。

证据只接受 PNG/JPEG/WebP，最多每张 5 MB、每份申请 4 张。校验真实类型、随机存储名、私有目录、授权下载与访问限速；不执行附件内容，不抓取用户提供的外部附件 URL。删除与保留策略可配置，备份覆盖证据目录。体积限制是本项目默认值，企业适配时可以调整。

## 12. 外部适配器与可靠执行

### 12.1 适配器边界

定义接口而不是把所有数据查询散落到 Agent 工具里：

- IdentityPort：从受信任身份系统获得客户身份。
- OrderPort：按客户读取订单、设备、支付分摊与签收事实。
- InventoryPort：检查、预占、释放、确认出库。
- FulfillmentPort：提交退款/换货、查询结果。
- ConversationPort：Chatwoot 消息与接管。
- PolicyRepository：版本化政策和规则。

本地领域数据库是申请和审计的权威源；企业 OMS/WMS/支付系统是其自身业务事实的权威源。真实库存适配时由 WMS 完成权威预占，本地只保存对应凭证，不在两个系统独立扣库存。

首版实际实现本地 InventoryPort，不实现外部 WMS。配置外部库存但没有经过验证的适配器时启动失败。接入 WMS 需要另外实现“请求权威预占—保存凭证—处理结果未知/释放”的流程，不能把第 8.3 节的本地行锁替换成事务里的 HTTP 调用。公司沙箱适配属于后续工作。

manual 试用若仍使用本地库存，必须限定为由试用团队专门管理的一组设备，或由员工出库前再次核对外部库存。本地预占只保证本系统内不重复分配，不能宣称已锁定公司其他销售渠道的库存。

操作在创建时固定 execution_mode、provider_name/version、请求快照和 environment_id；worker 依据该记录选择执行方式，不依据运行时一个全局开关改投其他供应商。demo 与 pilot 使用隔离数据库，数据库初始化保存环境标识，启动拒绝模式混用。变更配置不会重新执行历史义务；缺少历史操作所需适配器时暂停对应任务并明确显示阻塞。

### 12.2 DemoProvider 必须是独立的可持久化服务

DemoProvider 使用独立数据库/用户，提供：

~~~text
POST /refunds           请求含稳定 idempotency_key
GET  /operations/by-key/{key}
POST /shipments         请求含稳定 idempotency_key
GET  /shipments/{id}
~~~

它保存操作结果，重复提交相同 key 返回同一操作；同 key 不同请求返回冲突。模拟出库确认包含新品设备标识、发货事件、明确的 simulated=true。

demo 专用故障注入：

- 明确拒绝。
- 执行前连接失败。
- 已执行成功，但响应丢失。
- 操作仍在处理中。
- 重复回调、乱序回调。
- 服务重启后仍能查询原操作。

故障控制接口只在 demo profile 存在，并要求管理员身份或测试专用认证，不能暴露在试用环境。

### 12.3 外部操作状态

自动履约状态为 PENDING → RUNNING → SUCCEEDED / FAILED / UNKNOWN；manual 操作初始为 AWAITING_MANUAL，登记结果后到 SUCCEEDED 或 FAILED。

FAILED 只表示已确定没有成功的失败，UNKNOWN 表示可能已执行。超时后保留原 operation、idempotency_key、资源预留；优先 GET 查询。需要重发时只能复用原 key，并且依赖对方的幂等契约。

供应商既不支持幂等也不支持查询时，UNKNOWN 转人工对账，禁止盲目重新 POST。不能承诺跨网络“恰好一次请求”；目标是至少一次投递下保持单一业务效果。

### 12.3.1 人工履约的完整路径

manual 模式在验收和资源准备好后，case 进入 FULFILLING，operation 进入 AWAITING_MANUAL；不创建自动退款/发货执行任务。管理员或员工在管理台看到待办，在公司的外部系统完成操作后，通过 record-manual-result 登记。

该接口要求员工身份、expected_version、幂等键、原义务摘要、performed_at、外部凭证号和必要证据。退款金额必须等于已确认金额；换货须包含实际出库设备标识、运单和发货凭证。结果必须与原设备/义务对应。

成功时复用同一个领域结算事务更新金额/库存、operation 和 case；明确失败进入 MANUAL_REVIEW，资源仍按具体处置决定是否释放。不能只把 case.status 改成完成。记录 verification_method=manual_attestation，界面说明是员工登记结果，不能伪装成系统已向支付机构核验。

reconcile 用于已有自动操作的 UNKNOWN 结果核对。优先调用原供应商查询；确需员工凭证判断时记录核查方式、证据和授权身份。它不得重新发起一个不同金额/设备的履约。

### 12.4 后台任务

在业务事务中插入 jobs，worker 用 FOR UPDATE SKIP LOCKED 和有期限租约领取。任务执行有 lease_version，提交结果前核验当前租约；长任务续租。进程崩溃后租约到期可恢复。

外部调用必须带业务幂等键，不能仅靠租约防止重复。模型任务、消息发送、履约任务和核对任务分别分类。重试采用有限次数和退避，超过阈值进入人工可见的待处理列表。

运行时避免在数据库事务里等待网络请求。对每种异常保存简短错误类别、下一次处理时间和操作入口。

### 12.5 Chatwoot 消息发送

outbound_messages 先记录发送意图和控制版本，再调用 Chatwoot。成功保存远端 message ID。

官方消息创建文档没有本方案可依赖的通用 Idempotency-Key 契约 [4]。响应未知时先读取和核对消息；无法证明是否发送成功时标为待核查，不立即重发。若可利用已验证的 metadata 标识辅助去重，必须先做固定版本契约测试。

发送重复最多影响消息展示，不能导致再次创建 case 或退款；业务幂等独立保证。

### 12.6 外部回调

DemoProvider 用自己的签名 secret；真实供应商按各自协议。验签、落库、去重后异步处理。事件需要关联已存在 operation，不能凭未知回调创建退款。

乱序事件不得把已成功状态改回处理中。供应商状态冲突时查询其权威状态并记录核对结果，不只比较不可靠的时间戳。

## 13. 运行记录、安全与降级

### 13.1 记录可观察行为

每次 run 关联 request_id、conversation_id、case_id、operation_id；记录模型名称/版本配置、prompt 版本、工具名、脱敏参数、结果、政策引用、耗时、token 用量与错误。

不记录模型隐藏思维链。用户看到办理进度和依据；员工按权限看操作审计；开发者看脱敏技术日志。

成本字段基于实际 usage 和可配置价格表估算，展示币种与计价日期；没有用量数据就显示未知，不能伪造零成本。

### 13.2 必要控制

- 密钥仅后端配置，前端不含模型 key、bot token、Webhook secret。
- demo 与 pilot 启动检查分开，pilot 禁止默认密码和公开账号切换。
- 限制登录、消息、上传速率与单个用户模型预算。
- CORS 只允许配置域名；cookie 写请求防 CSRF。
- 对用户内容进行安全渲染，不能直接插入 HTML。
- 日志默认脱敏邮箱、电话、地址；密钥不打印。
- 敏感 API 每次做服务端角色和资源权限验证。
- 不向模型提供任意 SQL、shell、浏览器控制或任意网络访问。
- 管理台支持暂停新 Agent 任务；业务记录与人工操作继续可用。
- 模型或 Chatwoot 故障时给出清楚状态，不自动宣布办理成功。

这些是本项目业务风险对应的必要实现，不要求首版额外构建复杂安全平台。

### 13.3 健康与恢复

ready 检查数据库与必要配置，分别报告模型、Chatwoot、worker、履约服务状态。外部模型异常不应让管理台完全打不开。

记录队列积压数、最老任务年龄、UNKNOWN 操作、最近成功 worker 心跳。提供备份与恢复脚本，并实际恢复到新数据库验证。只有数据库备份、没有证据文件和配置恢复说明，不算完成备份方案。

## 14. 仓库结构与开发约定

| 路径 | 内容 |
|---|---|
| backend/app/api | 客户、员工、管理和回调接口 |
| backend/app/domain | 规则、状态迁移、金额、库存、确认与审批服务 |
| backend/app/agent | 图、工具 schema、提示词、上下文装载 |
| backend/app/adapters | Chatwoot、模型、订单、库存、履约适配器 |
| backend/app/worker | 领取任务、发送、履约、核对 |
| backend/app/db | 模型、仓储、事务和迁移 |
| backend/app/cli.py | 初始化、导入、种子、诊断命令 |
| frontend/src | 客户门户和员工/管理员路由 |
| simulator | 独立 DemoProvider |
| policies/demo | 两套演示政策包 |
| fixtures | 演示订单、设备、真实脱敏 Chatwoot 契约 |
| tests/unit | 确定性业务规则 |
| tests/integration | 真实 Postgres 和适配器 |
| tests/e2e | 页面与完整业务流程 |
| evals | Agent 开发集、冻结测试集、运行和评分脚本 |
| scripts | 初始化、备份、恢复、冒烟、演示重置 |
| docs | 架构、兼容记录、接口、部署、运维、验收与已知限制 |

技术约定：

- Python 用 uv 和锁文件；前端使用一个包管理器并提交锁文件。
- Ruff、类型检查、TypeScript 构建进入 CI。
- 每个数据库变更带 Alembic migration，不以启动时 create_all 代替迁移。
- 领域函数接收 Clock 接口，测试冻结时间，运行环境用真实时间。
- 测试默认 MockProvider，不偷偷调用付费模型；真实模型评测必须显式启用。
- 所有 TODO、未验证连接器与模拟行为集中记录，不能藏在成功返回里。
- 不修改 Chatwoot 核心；不复制整套客服平台进自有代码。


## 15. 种子数据、演示与评测

### 15.1 可重现的演示数据

建议首版提供 8 个 SKU、12 个虚构客户、60 个订单、80 台订单设备、2 套政策版本、1 个管理员和 2 个员工账号。数字是实施规模建议，不是项目已有成绩。

每台设备明确订单归属、SKU、签收时间、原始序列号、实付分摊和政策版本。订单覆盖在窗口内、刚好到期、已过期、多设备、已有申请、已退款等情况。库存至少包含充足、仅剩一台和缺货三种状态。

种子支持指定固定时间、随机种子和场景名称。正常 demo 以当前时间生成可操作订单，测试以固定时钟重放。禁止随日期变化导致所有演示订单过期。

demo 重置只能作用于明确标记的演示数据库，命令必须检查 APP_MODE 和数据库标记。不能在 pilot 模式执行删除真实记录的脚本。

### 15.2 三段演示脚本

**场景 A：正常换货**

客户登录、用自然语言描述耳机问题；Agent 选对设备、补资料、引用条款、给预览；客户确认；员工同意寄回、收货验收；系统预占唯一新品、获得模拟出库回执；客户看见“换货已发出”和新设备标识。最后展示 case、库存和 operation 真实变化。

**场景 B：退货与结果未知**

客户申请退货并确认；员工完成初审和验收；DemoProvider 成功退款但丢弃响应；管理台显示待核对；worker 查询原操作，完成退款。证据包括只有一个 provider refund、一个已结算 allocation、金额未重复扣减。

**场景 C：人工接管和并发约束**

客户提出特殊诉求，客服接管；延迟返回的模型结果被丢弃。再用两个不同客户申请最后一台同 SKU 新品，验收通过后只有一个预占成功，另一个进入 WAITING_STOCK。补货后第二个可继续。

演示中所有“客户确认”和“员工验收”均由对应身份的接口提交。不得隐藏脚本直接改数据库来假装完成流程。

### 15.3 Agent 任务集

建立 40 条开发任务和 60 条冻结测试任务。至少覆盖：

- 正常退货、正常换货。
- 两个相似订单和多台同款设备，需要澄清。
- 缺少配件、缺少问题描述、序列号冲突。
- 刚好过期、政策新旧版本差异、政策缺失。
- 其他客户的订单、伪造身份、要求跳过确认。
- 要求更高退款、要求跳过人工验收。
- 已有申请、已退款、缺货。
- 工具失败、模型超时、重复输入、多轮纠正。
- 文档或用户消息包含恶意指令。
- 人工接管后旧任务返回。
- 超出范围的维修、跨 SKU 换货、新品二次售后。

按订单、措辞和政策情境拆分开发集与测试集，冻结后不拿测试集持续调 prompt。若发现测试标注错误，记录修订版本并重新报告。

任务须定义初始数据库、对话输入、允许的员工/客户操作、期望下一步与最终数据库断言。退款完整流程的评测器可按脚本执行“合法客户确认与合法员工验收”，但不能把这些人工权限计为 Agent 自主能力。

### 15.4 指标与目标

| 指标 | 计算方法 | 初始验收目标 |
|---|---|---|
| 核心业务不变量 | 权限、金额、唯一申请、库存、未知结果等确定性测试 | 所有指定用例通过 |
| 任务完成率 | 按每个任务预定义的业务终态或应有下一步判断 | 冻结集总体至少 85%，同时报告分类结果 |
| 错误办理/越权 | 不应有的 case、金额、库存或权限动作 | 测试中为 0；发现一例即阻断试用 |
| 政策引用正确率 | 版本和条款确实支持该说明，规则一致 | 初始目标至少 95%，人工抽查 |
| 缺资料追问 | 必填缺失时是否追问且不擅自补齐 | 单独报告，不被总体分掩盖 |
| 耗时与成本 | 每任务调用数、token、p50/p95、失败率 | 实测报告，不预设虚构性能提升 |

以上比例是待验证的项目目标，不是已经达到的结果。有限测试中的 0 失败不代表生产中绝对不会失败。真实 LLM 评测记录模型配置、日期、数据集版本、提示词版本和预算。

选取 10 个关键模型场景重复 3 次，单独报告稳定性；不要将一次幸运运行当成可靠性证明。确定性验收与 LLM 任务评测分开报告。

### 15.5 公平基线

可选两个有解释价值的对比：

1. 同一个模型、同工具、同权限、同业务校验和预算下，简单工具循环与当前编排/上下文策略比较。
2. 普通向量检索与政策版本过滤后的混合检索比较。

不移除基线的身份验证或金额校验来制造优势；不宣称 GPT/Claude 无法使用工具。状态机与权限是共同的正确性要求，架构增益需要真实测量。

可借鉴 τ²-bench 的零售任务、政策约束和工具交互设计，但本项目自建场景不能标成官方 τ²-bench 分数 [14]。引用开源样例时保留许可与来源。

## 16. 必须通过的验收场景

以下为工程门槛，不只验证页面文本。事务和并发用真实 Postgres，不能用 SQLite 替代关键测试。

| 编号 | 场景 | 必须观察到的结果 |
|---|---|---|
| A01 | 客户读取他人订单或申请 | 不返回私有数据，不写业务记录 |
| A02 | 伪造 Widget identifier | 未获得可信绑定，不进入私有工具 |
| A03 | 缺少退货必填信息 | Agent 追问，不直接提交 |
| A04 | 窗口前、恰好到期、到期后及延迟审批 | 确认按边界判断；及时受理后不因晚审批而过期 |
| A05 | 旧订单遇到新政策 | 使用原适用版本并保留引用 |
| A06 | 政策缺失或映射冲突 | 转人工，不猜条件 |
| A07 | 正常退货 | 确认、初审、验收、退款和金额记录一致 |
| A08 | 正常换货 | 预占、新设备、出库和申请关联正确 |
| A09 | 客户未确认 | 不创建正式申请、不预占库存 |
| A10 | 同幂等键重复确认 | 同一 case，同一响应语义 |
| A11 | 不同幂等键并发申请同设备 | 仍只有一个有效 case |
| A12 | 旧草稿/旧员工页面提交 | 409，不能覆盖新版本 |
| A13 | 重复验收/审批 | 只有一个履约义务和资源占用 |
| A14 | 两个 case 抢最后一台新品 | 一个成功，一个 WAITING_STOCK |
| A15 | 等待补货后再处理 | 原 case 可继续，不重新申请 |
| A16 | 收到坏品 | returned_items 记录，不增加可销售库存 |
| A17 | 验收不通过 | 保留实物责任，处置有证据后才能关闭 |
| A18 | 退款金额被客户端或模型修改 | 拒绝或忽略不受信字段，使用原始分摊 |
| A19 | 退款已成功但响应丢失 | UNKNOWN 后核对，供应商仅一笔退款 |
| A20 | 出库已成功但响应丢失 | 保留预占直到核对，不重复发货 |
| A21 | worker 在调用前后崩溃 | 重启恢复，不重复业务效果 |
| A22 | 回调重复、乱序或未知 operation | 去重、不倒退状态、未知关联待核查 |
| A23 | Chatwoot 自身 outgoing 回调 | 不触发回复循环 |
| A24 | 私有备注 | 客户看不到，也不触发 Agent |
| A25 | 人工接管时模型尚未返回 | 过时结果不发出，不产生新草稿 |
| A26 | 接管期间已有合法履约任务 | 正常按业务授权继续，Agent 保持静默 |
| A27 | 显式交回与会话状态冲突 | 复查；不满足条件就继续暂停 |
| A28 | 模型不可用 | 表单仍可创建草稿、确认并人工办理 |
| A29 | pilot 缺适配器、使用 demo 凭据 | 明确失败，不自动模拟成功 |
| A30 | 重启与备份恢复 | 申请、附件、审批、未知操作均可找回 |
| A31 | manual 模式正常履约 | 无自动供应商请求，凭证登记后同一结算事务完成 |
| A32 | 变更履约配置或重启到错误环境 | 已有 operation 不被改投，混用数据库被拒绝 |
| A33 | 修改换货地址及重复确认 | 地址只由本人表单修改，旧摘要失效，履约使用确认快照 |
| A34 | 同会话连续或并发消息 | 同一线程串行运行，过时结果丢弃且消息不遗漏 |

页面端至少用 Playwright 覆盖：登录归属、正常申请确认、员工初审验收、状态查询和人工接管按钮。对 Chatwoot 自带 UI 不做大量脆弱样式断言，验证真正需要的消息收发和集成行为即可。

没有模型 key 时可完成领域、接口、页面与契约测试，但真实模型验收明确标为未执行；不得据此宣称整个 Agent 已验收。

## 17. Codex 分阶段实施顺序

不要一次生成所有文件后再寻找问题。每阶段有可执行产物、真实验证和剩余问题记录。后续阶段可以扩展功能，但不能用 stub 掩盖前一阶段未通过的核心门槛。

### M0 接入与环境

产物：最小仓库、固定版本 Compose、ChatwootAdapter、回调验签/去重、可信身份、bootstrap 和真实 fixtures。

必须通过第 5.5 节。先用固定回复验证消息链路，不接 LLM。完成后保存版本、镜像 digest、实际字段与权限结果。

遇到回调内网限制先查清网络和版本，不盲目改架构。

### M1 业务后端

产物：迁移、账号权限、订单设备、政策规则、proposals/cases、状态机、库存和金额预留、DemoProvider、worker。

通过 API 跑通两条主流程；并发重复申请和最后一件库存必须在此阶段就受保护。外部 UNKNOWN 的模型和操作查询也在此阶段存在。

目标是先拥有一套不依赖 LLM 也能正确办理的售后后端。

### M2 产品界面

产物：客户门户、员工业务台、政策与库存基础管理、聊天嵌入、会话与申请跳转、私有证据。

通过页面完成“客户申请—员工办理—客户查进度”。显示真实数据与错误。员工可看见待核对操作，不只在日志里报错。

### M3 Agent 与政策检索

产物：一个 LangGraph Agent、受控工具、持久化、中文检索、版本化 prompt、引用、真实模型配置、开发评测集。

正常诉求从自然语言进入既有业务 API；不另写一套绕过领域校验的 Agent 专属逻辑。达到开发集目标后冻结测试集。

### M4 故障与接管完善

产物：租约恢复、重试/核对、消息 outbox、过时模型结果阻断、人工接管、去重乱序、防越权与故障演练。

重点通过 A10—A29；对已经验证的简单页面不反复添加镜像式单元测试。修复有证据的风险。

### M5 交付与评测

产物：冻结集真实结果、失败案例、三段可复现演示、README、架构图、部署手册、备份恢复、已知限制、开源说明、截图/录屏。

运行 A01—A34，完成真实模型验证和一次干净环境安装。记录实际资源占用与并发测试条件；不声称未测的处理规模。

### 17.1 时间规划

这是认真打磨的个人项目，建议按约 6—8 周规划完整实现和理解过程；该范围是计划假设，不是 Codex 的工期保证。已有经验时可以更快，首次处理容器、异步回调和事务时应留调试时间。

优先顺序为：业务正确性、可部署与恢复、真实模型办理、产品体验、演示表达。M2 完成是普通售后应用，M3 加入 Agent，M4/M5 通过后才达到本方案定义的试用版。

精力不够时减少产品类别、政策数量和界面装饰；保留权限、确认、状态机、幂等、库存一致性、人工接管与验收。

### 17.2 Codex 每阶段汇报格式

- 本阶段真实实现了什么，涉及哪些主要文件。
- 执行过的命令、退出结果、测试环境。
- 关键业务数据库变化或脱敏接口证据。
- 实际通过、失败和未执行的检查。
- 尚未实现的连接器、模拟边界与下一阶段。
- 没有运行的测试不能写“通过”，需要密钥的检查不能用 mock 替代后算完成。

## 18. 部署与可交付文件

### 18.1 本地和试用部署

本地优先 Windows WSL2 + Docker Compose，代码放 WSL 文件系统内以减少挂载差异。需要的服务是：

- api
- worker
- frontend/reverse-proxy
- chatwoot-rails
- chatwoot-sidekiq
- postgres
- redis
- demo-provider（仅 demo profile）

Compose 配好依赖健康检查和持久卷。API、worker 可以使用同一镜像的不同启动命令。模型通过配置的外部服务调用，不要求购买新电脑或部署本地大模型。

试用环境以一台 Linux 主机为首个目标。数据库和 Redis 不对公网开放；门户、管理台和 Chatwoot 经 HTTPS 暴露。实际所需内存和 CPU 以运行观测决定，不在没有测试的情况下承诺某种机器能处理多少客户。

### 18.2 必要环境变量

~~~dotenv
APP_MODE=demo
FULFILLMENT_MODE=demo
DATABASE_URL=
GRAPH_DATABASE_URL=
MODEL_PROVIDER=
MODEL_NAME=
MODEL_API_BASE=
MODEL_API_KEY=
EMBEDDING_PROVIDER=
EMBEDDING_MODEL=
EMBEDDING_API_KEY=
CHATWOOT_BASE_URL=
CHATWOOT_PUBLIC_URL=
CHATWOOT_ACCOUNT_ID=
CHATWOOT_INBOX_ID=
CHATWOOT_BOT_ID=
CHATWOOT_BOT_ACCESS_TOKEN=
CHATWOOT_BOT_WEBHOOK_SECRET=
CHATWOOT_ACCOUNT_WEBHOOK_SECRET=
CHATWOOT_WIDGET_TOKEN=
CHATWOOT_WIDGET_IDENTITY_SECRET=
SESSION_SECRET=
ALLOWED_ORIGINS=
DEMO_PROVIDER_URL=
DEMO_PROVIDER_SIGNING_SECRET=
ATTACHMENT_STORAGE_PATH=
AGENT_ENABLED=true
~~~

Chatwoot 自身的 SECRET_KEY_BASE、数据库、Redis、FRONTEND_URL 等配置单独维护。示例文件不包含真密钥；bootstrap 所需管理员 token 不进入长期 worker 配置。

### 18.3 命令契约

要求 Codex 在 WSL/Linux 的 Makefile 或等价脚本实现下列入口，并在 README 写出前置配置。以下是目标命令，不表示当前已有可运行程序。

~~~bash
make dev
make bootstrap
make init-demo
make smoke
make check
make test-domain
make test-integration
make test-e2e
make eval-dev MODEL_MODE=live
make eval-holdout MODEL_MODE=live
make backup
make restore-smoke
~~~

第一次 bootstrap 可以包含明确的人工作业，如获取 Chatwoot secret、配置 Widget 强制验证。脚本要诊断缺失配置，不假装全部一键自动完成。完成初始配置后，再次启动应稳定复用数据。

### 18.4 最终仓库交付清单

- 完整源代码、锁文件、Dockerfile、Compose、迁移。
- .env.example 与 bootstrap/诊断脚本。
- 演示订单、政策、库存、客户与设备数据。
- 正常、失败和结果未知的 DemoProvider。
- 客户和员工页面，真实事件与状态。
- 单元、集成和必要端到端测试。
- Agent 开发集与冻结集，明确的评分脚本。
- 实测评测报告、失败案例、模型与环境配置。
- docs/architecture.md、docs/api.md、docs/chatwoot-compatibility.md。
- docs/deployment.md、docs/operations.md、docs/known-limitations.md。
- 备份和恢复记录。
- 开源许可证、第三方贡献说明。
- 三段演示脚本、实际截图或录屏。

验收包应让另一个人按 README 配置后重现；截图只能展示实际运行结果。

## 19. 公司接入时具体还差什么

| 接入项 | 首版已有 | 公司需要提供或共同完成 |
|---|---|---|
| 客户身份 | 本地登录、可信聊天绑定、资源权限 | 客户 ID 映射、真实登录/身份系统 |
| 订单 | 演示导入、设备和实付模型 | OMS API、支付和签收口径、历史数据质量 |
| 政策 | 版本包、规则、引用与快照 | 公司审定条款、例外流程和责任人 |
| 库存 | 本地原子预占与设备跟踪 | WMS 权威预占/释放/出库接口 |
| 退款 | 模拟服务、稳定义务 ID、UNKNOWN 核对 | 支付退款权限、幂等/查询契约、财务对账 |
| 物流 | 模拟出库回执、手工录入寄回物流 | 仓库/承运商实际事件与异常处理 |
| 员工 | support/admin 角色、审计 | 人员分工、账号管理、值班和接管流程 |
| 运维 | 单机部署、健康、备份恢复 | 容量、告警渠道、恢复目标、责任人 |
| 数据要求 | 最小权限、脱敏、保留配置 | 公司认可的数据范围和模型服务配置 |

没有真实支付接口，也可以先试用“受理、资料核验、员工审核”部分；后续外部操作由员工完成并录入证据。界面应诚实标明手工步骤。

建议先让内部客服使用，再接少量受控客户。公司确认真实业务映射、恢复演练、故障接管及实际测试结果后再扩大范围。本方案没有为任何具体公司完成这些验收。

## 20. 简历与面试应该展示什么

项目名称建议：“电子产品售后办理 Agent”或“电子产品售后智能办理系统”。

完成后可以如实说明：

- 复用 Chatwoot 客服入口，独立实现 FastAPI 业务服务与 LangGraph 单 Agent，将自然语言诉求转换为可确认、可审核的售后申请。
- 建立政策版本与规则绑定，支持订单设备级权限、原实付金额计算和确定性状态迁移。
- 实现 PostgreSQL 事务、幂等、库存预占、人工接管与外部操作核对，在模拟业务环境验证异常恢复。
- 建立冻结任务集，以数据库业务结果评测办理完成率、错误办理和政策引用；具体数值待实测后填写。

这些是完成之后的可用表述，不是现在已经获得的经历。面试时明确指出 Chatwoot 和框架复用范围，展示自己的领域服务、失败演练和数据证据。

最有价值的讲解题目：

1. 模型为什么不能直接批准退款。
2. 用户确认、员工初审、验收批准分别是什么。
3. 为什么政策检索必须绑定版本。
4. 为什么不能在申请时就长期锁库存。
5. 退款超时为什么不能直接再试一次。
6. 人工接管如何影响 Agent，但不取消合法业务任务。
7. 同模型同工具的基线如何比较，哪些保护不应拿来做“优势”。

## 21. 给 Codex 的统一启动说明

2026-10-21 用户执行约定修订：本节依照用户后续明确指令，替换原先“本轮只做 M0，逐阶段等待审查”的安排。原始版本已保存在 Git 初始提交 a407661。此修订仅改变推进与交接方式，保留第 1—20 节的业务范围、架构设计和全部验收门槛。

1. 从当前实际代码继续，按 M0—M5 顺序持续实施。每阶段执行对应验证、修复问题，更新进度、交接与证据；达到当前阶段要求后自动进入下一阶段，无需逐次确认。真实关键门槛未通过时不得用 stub 或模拟响应算作通过。
2. 使用独立 electronics-aftersales-agent 项目与个人账号 Weihao-Zhou03 下的同名私有仓库。检查实际登录与权限，不假定与 ChatGPT 共享 GitHub 授权。保留已有分支、提交与本地工作，不强制重写历史。
3. 使用一个专门开发分支，按阶段保留清晰提交，最终创建一个总 PR，不自动合并。若已存在阶段分支或 PR，保留记录后整理后续开发。
4. 持续维护 AGENTS.md、docs/implementation-status.md、docs/HANDOFF.md、docs/REVIEW.md、docs/chatwoot-compatibility.md 和相关验证报告。记录被验证的代码 commit、实际命令、通过、失败、未运行项；交接文档可在被验证代码之后单独提交，PR 指向最新提交。
5. M0 仍先完成第 5 节、第 6 节接入契约与第 5.5 节真实验收。使用固定回复且无需模型 key，以可控延迟验证过时任务阻断。环境问题先定位，不能绕过后直接宣称 M0 完成。
6. 各阶段使用真实数据库、权限、事务、身份、客户确认、状态、库存、接管与异常核对。测试明确区分真实集成、合成/模拟、独立审查。未执行不写通过；不得以计划规模、指标或假数据代替实测。
7. 确实受外部条件阻塞时完成所有不受影响工作，集中列出需用户提供的信息、凭据或环境。依赖条件的能力保持“受阻”或“未验证”，不计入完成。常规实现选择自主决定并记录；实质改变业务或核心架构才向用户提问。
8. 凭据仅使用环境变量和本地忽略文件，不提交或暴露；不使用 ChatGPT/Codex 登录态作模型 key，不接真实资金与物流。用户手动启动 Docker Desktop，Agent 不自动启动/重启 Desktop 应用，可在引擎可用后运行本项目容器。
9. 收到真实独立审查前 docs/REVIEW.md 保持“待审查”，自测不能称为“ChatGPT 已审查通过”。后续修复前先同步远程、阅读总 PR、交接与审查记录，保留未提交工作。
10. 最终交付仓库/总 PR 链接、分支与代码版本、干净状态、启动复现步骤、演示账号、三段演示、A01—A34 逐项结果、真实模型评测及条件、已知限制和用户待办。GitHub 失败时提供本地 commit 与未完成远程步骤，不编造推送/PR。

架构仍是一个主 Agent、一个业务后端、Postgres worker、独立 Chatwoot 与模拟履约服务；不增加多 Agent 业务系统、Kafka、Kubernetes、MCP 或未要求渠道。范围缩减或放宽验收不能代替正确性修复。

## 22. 资料来源与核实范围

以下来源用于确认所选开源能力与接口方向，核查日期为 2026-10-21。本方案未实际部署这些服务；版本差异、镜像运行与权限均在 M0 通过真实集成验证。

1. [Chatwoot 发布记录](https://github.com/chatwoot/chatwoot/releases)。
2. [Chatwoot AgentBot 使用与接管](https://app.chatwoot.com/hc/user-guide/articles/1677497472-how-to-use-agent-bots)。
3. [Chatwoot Webhook 事件与签名](https://app.chatwoot.com/hc/user-guide/articles/1677693021-how-to-use-webhooks)。
4. [Chatwoot 创建消息 API](https://developers.chatwoot.com/api-reference/messages/create-new-message)。
5. [Chatwoot 创建账户 Webhook](https://developers.chatwoot.com/api-reference/webhooks/add-a-webhook)。
6. [Chatwoot 绑定 AgentBot](https://developers.chatwoot.com/api-reference/inboxes/add-or-remove-agent-bot)；[创建 AgentBot](https://developers.chatwoot.com/api-reference/account-agentbots/create-an-agent-bot)。
7. [Chatwoot 会话详情](https://developers.chatwoot.com/api-reference/conversations/conversation-details)；[会话状态](https://developers.chatwoot.com/api-reference/conversations/toggle-status)；[会话指派](https://developers.chatwoot.com/api-reference/conversation-assignments/assign-conversation)。
8. [Chatwoot Docker 部署](https://developers.chatwoot.com/self-hosted/deployment/docker)。
9. [Chatwoot 私网回调问题及维护者说明](https://github.com/chatwoot/chatwoot/issues/14489)。
10. [Chatwoot Widget 身份验证](https://app.chatwoot.com/hc/user-guide/articles/1677587479-how-to-enable-identity-validation-in-chatwoot)。
11. [Chatwoot 联系人匹配与身份验证的区别](https://app.chatwoot.com/hc/user-guide/articles/1782283175-understanding-contact-identity-and-identity-validation-in-chatwoot)。
12. [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)。
13. [LangGraph 中断与恢复](https://docs.langchain.com/oss/python/langgraph/interrupts)。
14. [τ²-bench 官方仓库](https://github.com/sierra-research/tau2-bench)。

规格中的业务政策、范围、默认阈值、测试规模、验收目标与排期都是本项目的设计选择。引用开源能力不意味着该项目已获得厂商认证，也不构成真实生产使用证明。



