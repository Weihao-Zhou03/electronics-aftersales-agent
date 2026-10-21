# 本地运行、备份与恢复

Docker Desktop 由用户手动开启。`scripts/doctor.py` 只检查环境，从不启动或重启 Docker Desktop。应用容器启动、停止均以本项目 Compose 名称限定，不处理其他项目容器。

## 日常启动

首次执行 `uv run python scripts/configure_demo.py`，自动为缺失的内部配置生成不同的长随机秘密，并建立空 `.env.chatwoot`；重复执行保留已有值，不自动配置模型 key。完成真实 Chatwoot bootstrap 后，跨平台执行 `uv run python scripts/dev.py`，WSL/Linux 也可使用 `make dev`。命令会准备数据库、显式执行主应用和独立 provider 的 Alembic 迁移，再启动服务。只有 `APP_MODE=demo` 且 `FULFILLMENT_MODE=demo` 时启用独立模拟 provider；pilot/manual 不启动它。

后续启动必须同时读取两个 env 文件；只加载 `.env` 会遗漏真实 Chatwoot bot/reader token、签名 secret 与 inbox ID。不要输出不带 `--quiet` 的完整 Compose 展开结果。

`make init-demo` 要求在调用 shell 提供至少 12 位 `M0_DEMO_PASSWORD`。它幂等地创建演示身份、订单、设备、政策和新品库存，保留已有身份密码与所有业务记录。模型密钥不是初始化固定回复链路的前置条件。

## 加密备份

在 shell 设置自己的 `BACKUP_PASSPHRASE`（至少 20 位，单独保管），执行：

```bash
uv run python scripts/backup.py
```

默认暂停当前正在运行的本项目 API、两种 worker、provider、Rails 和 Sidekiq，成功或失败后都恢复原来运行的容器。不会启动此前停止的业务 worker，不会开启 Docker Desktop。手动运行的主机写入脚本也必须在备份窗口停止。

备份目录为本地忽略的 `.local/backups/<UTC时间-随机值>`。内容包括三个独立 PostgreSQL custom-format dump、业务私有附件、Chatwoot storage 和限定的项目配置文件。数据库 dump 也可能含 token，因此**所有产物和详细 manifest 都加密**，而非只加密 `.env`。使用 cryptography AES-256-GCM、随机 salt/nonce、PBKDF2-HMAC-SHA256 600,000 次派生；明文只存在进程内存，不先写明文临时备份。口令不会保存在备份或日志中。

`--online` 不暂停容器；该方式只有每个数据库各自的一致快照，不承诺数据库、provider 和附件之间的同一业务时点。没有整套一致性要求时才自行选用；默认演练使用暂停写入方式。

口令、备份与恢复目录均不能提交 Git。测试演练使用一次性进程环境口令，测试结束即不保留；它的备份只作为一次恢复验证的临时产物，不作为用户可长期恢复的存档。用户正式备份须自行安全保管口令并保存异地副本。

## 无损恢复演练

提供相同口令后执行：

```bash
uv run python scripts/restore_smoke.py .local/backups/<目录> --cleanup
```

脚本验证加密 manifest/文件校验和与 GCM 完整性，创建随机且严格匹配 `eaa_restore_<随机值>_<库名>` 的**全新测试数据库**，恢复表和约束，比较每张表记录数、UNKNOWN operation 的幂等键/请求/结果摘要，并验证受限应用角色可读取相应业务表。不会在现有数据库执行恢复，也不重置生产卷。

附件解密到全新的 `.local/restore-smoke-*` 证据目录，逐文件比较 SHA256；拒绝路径越界和链接。配置只在内存解密验证，不写出明文 token。`--cleanup` 只删除本次生成的测试数据库；证据目录与无秘密值的 `restore-report.json` 保留。省略该参数可保留新库供人工检查。

该演练验证逻辑数据与私有文件恢复，不自动替换正式运行实例。真实灾难恢复时应先在隔离主机复现演练，再由维护者切换相同代码/镜像、数据库连接、文件卷与安全配置，恢复 HTTPS、检查身份授权、查看 UNKNOWN 待核对项。不得重新生成义务或将 UNKNOWN 当失败重新提交。

## 已有实测与边界

已实际完成三个独立 PostgreSQL 数据库与私有附件/加密配置的恢复，最终报告为 `docs/evidence/backup-restore.json`：应用 37 表 / 1062 行、Chatwoot 106 表 / 499 行、provider 5 表 / 57 行；包含 1 条真正 HTTP 响应丢失造成的 UNKNOWN 和 5 个私有证据文件。三个库均验证受限运行角色读取恢复数据，逐表记录数、UNKNOWN 请求/幂等键摘要和文件 SHA256 均一致。回执只记录数量与验证结果，不含密码、地址或请求内容。生成 UNKNOWN 的工具严格要求 `APP_MODE=demo` 和 `--execute-demo`，不在 pilot 运行。此夹具随后通过原 key 查询真实模拟 provider 的已成功结果完成核对。

完整空卷安装实测命令为 `uv run python scripts/clean_install_smoke.py`。它在随机独立 Compose project、新命名卷和不同 loopback 端口执行真正的三库迁移、Rails 专用演示用户初始化、两次幂等 bootstrap、readiness 与核验身份后的 Widget 固定回复。`docs/evidence/clean-install.json` 记录实际镜像、代码基线和结果。测试结束停止且移除该独立项目容器，保留新卷与 `.local/eaa-clean-*` 配置供检查；不删除主项目数据。该检查依赖已经构建好的项目镜像与 `tests/e2e` Chromium，不代表未安装 Docker/Node 的裸操作系统自动安装。

实际进程中断复现为 `uv run python scripts/run_process_recovery.py --execute-demo`。它只停止履约 worker，使用真实独立子进程分别在调用 provider 前和成功响应后 `os._exit(91)`，保留已提交租约；普通 daemon 恢复后等待真正 30 秒过期，通过原 operation/key 查询或同 key 重送。报告 `docs/evidence/process-recovery.json` 的两条退款均只有 1 次模拟效果，耗时约 32 秒。没有使用 SQL 伪造租约过期，也不影响聊天 worker。

当前方案不包含 Redis 待投递队列快照、操作系统、TLS 证书或外部模型网关配置；业务任务/回调 outbox 在 PostgreSQL 中可恢复。Chatwoot 未投递的 Redis 回调可能需要从会话/API 重新核对，不能宣称完整机器镜像或零数据损失。Embedding 模型缓存可重新下载；政策 chunk、向量与运行证据保存在业务数据库中。

备份当前在内存中处理单个 dump/归档，适用于本项目小规模演示；大数据规模需要流式加密、远程存储、监控与恢复时间目标的实测。这些能力尚未验证。
