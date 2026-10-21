# 第三方组件与贡献边界

本项目自己实现客户/员工门户、售后领域规则、权限/确认/事务/幂等、业务 worker、受限工具、Chatwoot 适配器、评测与部署脚本。Chatwoot 客服平台、LangGraph 编排、数据库和前端框架来自第三方；没有将 Chatwoot 自带 UI、模型或其框架能力标为原创。没有复制或修改 Chatwoot 核心源码。

| 组件 | 用途与来源 | 许可证来源 |
|---|---|---|
| Chatwoot 4.19.0-ce | 自建客服、Widget、AgentBot、签名 webhook | [固定版本 LICENSE](https://github.com/chatwoot/chatwoot/blob/v4.19.0/LICENSE)：community 范围 MIT；enterprise 目录另有条款，本项目使用 ce 镜像 |
| PostgreSQL / pgvector 0.8.1 | 事务、审计、向量检索 | [PostgreSQL](https://www.postgresql.org/about/licence/)、[pgvector 固定版本](https://github.com/pgvector/pgvector/blob/v0.8.1/LICENSE) |
| Redis 7.4.2 | Chatwoot 队列 | [固定版本许可证](https://github.com/redis/redis/blob/7.4.2/LICENSE.txt)：RSALv2 / SSPLv1 双许可，不标为 BSD |
| FastAPI、SQLAlchemy、Alembic、Pydantic、httpx | Python API、事务与迁移 | 原包许可随锁定依赖分发；版本见 uv.lock |
| LangGraph / psycopg | 单 Agent 图、独立 PostgreSQL checkpoint | [LangGraph](https://github.com/langchain-ai/langgraph)、[psycopg](https://www.psycopg.org/)；版本见 uv.lock |
| FastEmbed / BAAI bge-small-zh-v1.5 | 本地中文 embedding，512 维 | [FastEmbed 支持模型与许可表](https://qdrant.github.io/fastembed/examples/Supported_Models/)，模型权重 MIT；缓存文件 hash 见本地 embedding 证据 |
| React、Vite、TypeScript、Playwright | 自有门户与真实浏览器测试 | 版本和依赖见各 package-lock.json，保留原包许可证 |
| Pillow、cryptography、Argon2 | 私有图片验证、加密备份、密码哈希 | 版本与原包许可随 uv.lock 依赖保留 |

容器镜像 digest 保存在 Compose；第三方完整依赖和许可证以锁文件及实际分发包为准。本仓库 MIT 仅覆盖本项目自己编写的代码，不改变外部软件或模型的条款。

`evals/` 为本项目自建任务与断言，未复制 τ²-bench 数据，结果不属于官方 benchmark 分数。引用 τ²-bench 仅说明任务设计的参考方向。政策和客户数据为虚构演示；真实 Chatwoot fixtures 是本地实例生成、脱敏后的实际接口结构，provenance 见 fixtures README。

EasyCLIProxyAPI 是用户自行安装并授权的外部本机模型网关，不随本仓库分发；模型访问权限、订阅额度和可用性由用户网关/上游决定，仓库不包含其密钥或 OAuth 授权资料。
