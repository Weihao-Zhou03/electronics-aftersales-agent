# 环境与基础设施实测记录

记录日期：2026-10-21（America/New_York）。此文件记录开发运行证据，不代替第 5.5 节 Widget/权限/接管验收或独立审查。代码版本由最终 HANDOFF 指明。

## 主机与 Docker

- 工作目录为独立的 `electronics-aftersales-agent`，Windows PowerShell；不在 Windows 原生安装 Rails。
- Docker Desktop 4.95.0 (242831)，Engine/Client 29.8.2，Linux/amd64，Compose v5.6.0。
- WSL 2.6.2.0，Linux kernel 6.6.87.2，Ubuntu/`docker-desktop` 均为 WSL2。
- 首次检查 Linux daemon 不可用。Docker Desktop 曾报告 `sailor-ingest.sock` 重命名失败；未删除 socket、未重置数据、未提升权限、未重启系统。用户手动重新打开后，`docker version`/`docker info` 确认 daemon 正常，再启动本项目容器。
- 遵从用户后续偏好：今后 Docker Desktop 必须由用户手动开启。诊断脚本只读检查，绝不自动开启或重启桌面程序。

## 已确认的不可变镜像

使用实际 `docker buildx imagetools inspect` 访问 registry 取得以下 OCI index digest。Compose 与 Dockerfile 同时固定版本 tag 和 digest；不是推测值。后续升级需更新此表并重跑验证。

| 用途 | 镜像 | OCI index digest |
|---|---|---|
| Chatwoot 社区版 Rails + Sidekiq | `chatwoot/chatwoot:v4.19.0-ce` | `sha256:6f296843097eee1eb8b6968027908fb4bb3de0b72236aa6a8f21386603266c91` |
| PostgreSQL 16 + pgvector | `pgvector/pgvector:0.8.1-pg16-bookworm` | `sha256:33198da2828a14c30348d2ccb4750833d5ed9a44c88d840a0e523d7417120337` |
| Redis | `redis:7.4.2-alpine` | `sha256:02419de7eddf55aa5bcf49efb74e88fa8d931b4d77c07eff8a6b2144472b6952` |
| Python | `python:3.11.14-slim-bookworm` | `sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d` |
| uv | `ghcr.io/astral-sh/uv:0.12.15` | `sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3` |

实际选择 CE 分发以使用社区功能，版本仍为 4.19.0。[官方 Docker 文档](https://developers.chatwoot.com/self-hosted/deployment/docker) 说明版本对应 CE tag 格式，并要求首次使用 `db:chatwoot_prepare`；[固定版本上游 Compose](https://github.com/chatwoot/chatwoot/blob/v4.19.0/docker-compose.production.yaml) 是 Rails/Sidekiq 服务命令与 pgvector 选型依据。项目没有沿用上游示例中的浮动 `latest`。

## 已运行的真实命令与结果

以下检查均访问实际 Docker 或数据库，没有 mock 响应：

1. `python scripts/doctor.py --compose-only --example`：退出 0。用临时合成秘密值验证 Compose 结构，不创建容器，不代表真实接入通过。
2. `docker compose --env-file .env --env-file .env.chatwoot config --quiet`：退出 0，实际本地配置可解析；没有输出展开的凭据。
3. 使用主 Compose 与 `infra/compose.test.yaml` 启动 PostgreSQL/Redis：均 healthy。主 Compose 不暴露数据库或 Redis；可选测试覆盖仅将 PostgreSQL 映射到 `127.0.0.1:15432`。
4. PostgreSQL `select version()`：`PostgreSQL 16.12 (Debian 16.12-1.pgdg12+1)`，x86_64，64-bit。
5. 数据库 `aftersales` / `chatwoot` 分别由同名用户拥有；两个运行角色均 `rolsuper=false`、`rolcreatedb=false`、`rolcreaterole=false`，PUBLIC 数据库权限撤销。
6. Chatwoot 数据库扩展：`vector 0.8.1`、`pg_stat_statements 1.10`、`pg_trgm 1.6`、`pgcrypto 1.3`、`plpgsql 1.0`。扩展由初始化管理员创建，不授予业务服务超级用户权限。
7. Redis `INFO server`：实际 `redis_version:7.4.2`，有密码认证，未映射主机端口。
8. `docker compose ... run --rm --no-deps chatwoot-rails bundle exec rails db:chatwoot_prepare`：退出 0，实际创建 108 张 public 表。首次 schema 建立之前日志曾输出 `installation_configs` 尚不存在，随后初始化成功；此瞬态信息没有被视作正常部署失败的替代证据。
9. Chatwoot Rails `/api` 与 SidekiqAlive `http://127.0.0.1:7433/` 均实际 HTTP 200。Sidekiq 日志显示 Rails 7.2.3.1、Ruby 3.4.4、Sidekiq 7.3.10 与 Redis 连接成功，心跳队列已注册；真正 webhook 异步回调是否到达另见兼容性验收证据。
10. API Docker BuildKit 构建通过，Python 3.11.14 与冻结的 uv.lock 安装成功。首次构建在并行开发尚未写出 `alembic.ini` 时失败；文件到位后重建通过。
11. `docker compose ... run --rm --no-deps migrate`：实际 PostgreSQL Alembic `0001_m0` 迁移退出 0。
12. 已为测试创建独立 `aftersales_test` 数据库，owner 为受限 `aftersales` 角色；测试结果由 core 测试报告记录。没有用 SQLite 的并发行为冒充 PostgreSQL 锁验证。

## 运行边界

- `.env`、`.env.chatwoot` 与 `.local/` 含仅本地配置/凭据，均受 `.gitignore` 忽略。管理员 token 不注入 API/worker；读取消息所需客服 token 使用独立变量，并受适配器方法与 inbox 检查限制。
- 未配置 Chatwoot 真实 token/secret 时，API readiness 返回 503、worker fail closed；bootstrap 后必须重建服务环境。此行为不能用来声明消息链路已经成功。
- 当前 HTTP/localhost 配置用于本地验收；HTTPS、互联网暴露、备份恢复与资源容量未在上述检查中验证。
- Windows 中文目录的 Python 3.11 editable `.pth` 路径解码与系统代码页存在兼容问题。Makefile 导出 `PYTHONPATH=<项目>/src`；容器路径为 `/app`，不受该主机路径问题影响。
- `scripts/live_probe.py` 只报告实际网络状态和明确选定的 token/API 检查；其输出明确列出不能由该脚本证明的浏览器和异步能力。

## 固定版本的本地回调网络配置

真实 Sidekiq 首次投递 AgentBot/control 回调时拒绝私网地址。随后逐行核对固定 v4.19.0 的 [lib/safe_fetch.rb](https://github.com/chatwoot/chatwoot/blob/v4.19.0/lib/safe_fetch.rb) 与 [lib/safe_fetch/fetcher.rb](https://github.com/chatwoot/chatwoot/blob/v4.19.0/lib/safe_fetch/fetcher.rb)：`SAFE_FETCH_ALLOW_PRIVATE_NETWORK` 由共享 fetcher 检查，实际覆盖此版本这两种回调，并非仅从 API inbox 文档推断。

本项目隔离的 localhost demo 将本地忽略文件中的该值设为 `true` 后重建 Rails/Sidekiq 容器。Compose 与 `.env.example` 默认仍为 `false`。此设置放宽 Chatwoot 的私网回调限制，只用于受控本地网络；部署环境必须重新评估允许目的地或使用 HTTPS 回调。没有改 Ruby 核心、禁用回调签名或伪造回调响应。
