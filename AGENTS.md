# 项目执行与交接约定

- 本仓库仅为 `electronics-aftersales-agent`。需求基线是 `docs/SPEC.md`，保存用户原始完整方案；修改需求须记录理由。
- 用户于 2026-10-21 扩大授权：按 M0—M5 连续推进，每阶段验证、修复、记证据，达到阶段要求后自动继续，无需逐次等待审查。M0 固定回复不接 LLM；保留原有业务范围、架构和全部验收标准。
- 使用一个专门 `codex/` 开发分支，按阶段清晰提交，最终一个总 PR，不自动合并。保留已有阶段分支/提交/PR。后续修复前，先 fetch 远程、读取总 PR、审查意见及交接文件；保留未提交工作，不强制覆盖。
- 维护 `docs/implementation-status.md`、`docs/HANDOFF.md`、`docs/REVIEW.md`、`docs/chatwoot-compatibility.md`。HANDOFF 记录被验证的代码 commit；随后可单独提交交接文档，PR 指向最新提交。
- 未收到用户安排的独立审查时，REVIEW 标记“待审查”；自测或开发代理检查不等于独立审查通过。
- 分开记录真实集成、合成/模拟测试、未运行检查；关键验收未通过先修复，不得降低标准或用假响应绕过。确因外部条件阻塞时完成所有不受影响工作，依赖能力保持受阻/未验证，不宣称阶段已完成。
- 运行时仅用受限 ChatwootAdapter 六项操作；管理员 token 仅用于 bootstrap。Webhook 原始体签名、账户/inbox 白名单、事务持久化及 fail closed 接管控制不可绕过。
- 身份由已登录服务端会话推导，不信任聊天文字、邮箱或可编辑属性。Widget 强制验证、可信完整绑定和退出撤权须有实测证据。
- 不提交凭据、Cookie、未经脱敏的事件或运行数据。fixtures 必须标明真实/合成来源，不用 mock 冒充 Chatwoot。
- GitHub 仓库属于个人账号 Weihao-Zhou03；不得在其他账号或组织下创建。无法完成远程步骤时如实给出本地 commit 和具体缺口。
- Windows 上使用 PowerShell 原生命令处理文件；Linux 容器运行 Chatwoot，不在 Windows 原生安装 Rails，不修改 Ruby 核心绕过网络校验。
- 用户负责手动启动 Docker Desktop；不要自动启动/重启 Desktop 应用。可检查其状态，并在引擎可用时按任务启动项目容器。
