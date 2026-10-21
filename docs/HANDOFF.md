# 持续交接记录

更新于2026-10-21；证据时间使用UTC。用户授权连续M0—M5、最终一个总PR、不自动合并。独立审查状态仍为 **待审查**。

## 仓库、源码与历史

私有仓库：https://github.com/Weihao-Zhou03/electronics-aftersales-agent 。专用分支`codex/full-implementation`；原`codex/m0-chatwoot-integration`及提交保留。目录为`C:\Users\19484\Documents\ChatGPT\agent客服\electronics-aftersales-agent`。父目录原有空Git、其他项目和下载方案未覆盖。

完整原方案保存在初始提交`a407661`，当前SPEC仅第21节按用户后续指令更新。第1—20及22节与原文一致，见`evidence/spec-baseline.json`。

阶段提交：M0`bed2f18`、M1`fff9f2c`、M2`7dcb04d`、M3`50b6db9`、M4`25826ae`、M5工具`29a4c75`；冻结字节保留`876b0bb`、真实Widget测试等待修复`ed054e8`、A06强制移交`955902a`、有限预算/评测条件`f84808b`、可复现演示种子参数`0b722bc`。未重写历史。

最终代码验证commit：`0b722bca25f5b07275e9d813150f586b2b51b033`。业务src、prompt、frontend整树仍与冻结基线`f84808b255e4dcfa624a63de37886d7dc6ca221c`相同；后续代码只有测试元数据和独立交付种子脚本。Ruff、mypy、129项合成、20项真实PG（含实际HTTP模拟履约）、TypeScript/Vite构建及Compose检查均通过。原a6ee407的116/18报告保留为`evidence/release-verification-a6ee407.json`。总PR为 https://github.com/Weihao-Zhou03/electronics-aftersales-agent/pull/1 ，待独立审查、未合并；GitHub连接器创建返回404后由同账号本机gh成功创建，并附加到本任务。M0—M5实现及开发自测已完成，失败任务与人工待办仍如实保留。交接文档在被测代码之后单独提交，PR指向最新文档版本，最终精确head和工作区状态见交付回复，不自引本文commit。

## 实现与核心保护

独立FastAPI、React客户/员工页面、PostgreSQL领域状态机/金额/库存事务、独立DemoProvider、履约worker、Chatwoot适配及签名回调、单LangGraph Agent和购买版本政策混合检索。客户确认、员工初审、收货验收分权；模型不持有金额、身份、审批或外部履约权限。

人工接管先提交本地暂停、撤销未确认Agent草稿，再同步真实Chatwoot；旧任务不发出。UNKNOWN消息不重发，需员工核对再显式交回；UNKNOWN履约保留资源、查询原key。合法履约独立于聊天控制继续执行。v1评测暴露A06仅口头人工后，补成服务端权威政策缺失/映射冲突必然实际移交，真实Widget及5项真实PG均已验证。

窗口、原政策、整数金额分摊、幂等、并发申请、最后一件库存、失败资源占用、实物责任和人工凭证均有验证。映射冲突使用明确的历史坏记录fixture，不冒称真实公司输入。

## 启动与演示账号

用户手动打开Docker Desktop和EasyCLIProxyAPI核心。项目不自动启动/重启Desktop。按用户授权，仅将本地代理接口密钥写入忽略的`.env`，未读取OAuth/Codex登录token，无需在聊天发送密钥。

```powershell
cd 'C:\Users\19484\Documents\ChatGPT\agent客服\electronics-aftersales-agent'
$env:PYTHONPATH = 'src;.'
uv sync --frozen
uv run python scripts/doctor.py
uv run python scripts/dev.py
```

门户 http://localhost:8000；Chatwoot http://localhost:3000。Compose始终加载`.env`和`.env.chatwoot`。本工作区专用demo账号`customer-a`、`customer-b`、`support`、`admin`，密码`ElectronicsDemo2026!`；seed复用已有账号，不重置密码。Chatwoot专用管理员/reader凭据只存`.local/chatwoot-admin.json`，不进Git。新克隆的独立秘密值、真实bootstrap和seed步骤见deployment。

模型`openai-responses` / `gpt-6.1-sol`；host API为`http://127.0.0.1:8317/v1`，container API为`http://host.docker.internal:8317/v1`。FastEmbed `BAAI/bge-small-zh-v1.5`实际512维，不需要第二个API key。运行前checkpoint setup与policy index已实际执行，复现命令见README。

v2运行9次模型、8次工具、HTTP60秒、轮次120秒。SPEC建议的6次原样保留；v1正常工具链产生草稿后可能没有最终答复名额，因此实施选择改为8工具槽+最终回答。业务资格和85%/95%门槛不变，提示词/评分未改；旧6/30秒结果保留。

## 实际验证与复现

A01—A34逐项及测试类型见acceptance；M0第5.5节十项真实集成见chatwoot-compatibility。真实PG+合成会话端口不等于真实Chatwoot，模拟回执不等于真实资金或物流。

```powershell
uv run ruff check .
uv run mypy src/aftersales src/demo_provider
uv run pytest -m 'not postgres and not live' -q
# TEST_DATABASE_URL须为独立测试库；实际provider/embedding条件见provider-evidence及release-verification。
uv run pytest -m postgres -q
npm ci --prefix frontend
npm run build --prefix frontend
npm ci --prefix tests/e2e
npx --prefix tests/e2e playwright install chromium
$env:M0_DEMO_PASSWORD = 'ElectronicsDemo2026!'
npm test --prefix tests/e2e -- business.spec.js
uv run python scripts/run_demo_scenarios.py --execute-demo
```

三演示主入口在`ed054e8`实际整体退出0：A真实Widget/模型→确认→换货51.4秒，B真实丢响应→UNKNOWN→原key唯一退款效果，C两客户库存竞争/补货2.9秒，C真实延迟模型接管及明确本地503降级10.9秒。首次因Widget身份初始化测试等待竞争失败的诊断保留；修复仅等待真实set_user完成。A06/新预算后的A与A06定向复验另报告，B/C履约逻辑未改。

A21真实子worker在调用前/成功后`os._exit(91)`，等待真实30秒租约，普通daemon恢复原义务/key，每项仅1个模拟效果。未用SQL改lease伪装进程中断。实际签名重复/旧provider回调不倒退结算。

空卷独立安装包含业务/provider/Chatwoot迁移、幂等bootstrap、身份及真实Widget收发，主项目未重置。加密恢复覆盖三库、1条真实UNKNOWN、5个私有附件与配置；3个受限角色均可读取新恢复库，原库未改，新测试库已清理。正式备份需用户保管自己的口令和异地副本。

精确命令/退出码见`evidence/release-verification.json`，被验证代码0b722bc，129项合成与20项实际PG全过（含真实中文embedding及独立HTTP provider）。测试库和全部依赖变量的复现见`verification.md`；不配置依赖导致的skip不能算通过。首次最终验证入口漏启用UTF-8，GBK打印构建输出失败，诊断保留为`evidence/release-verification-0b722bc-runner-attempt1.json`；随后用`uv run python -X utf8 .local/verify_release.py`完整重跑七项并通过。入口是本地编排器，可移植的各条实际命令见verification。原ed054e8的108/13、a6ee407的116/18均为保留历史。模型9/60的A06通过11.5秒，独占正常A完整换货通过47.7秒，worker源码SHA256一致；并发时60秒模型不可用的失败报告仍保留。

SPEC15.1的可选`--at`/`--seed`/`--scenario`已补齐，默认仍是当前时间、新UUID和全部四个输入场景。新增13项参数/边界与2项真实PG/Alembic验证纳入上述129/20。重复固定种子不改密码、已确认/已完成申请或已消耗库存，冲突拒绝覆盖。重建后的真实镜像与Git脚本SHA一致，两次one-off容器对主demo库播种C均退出0且返回相同输入，仍只有1件AVAILABLE库存、0正式单，见`evidence/scenario-seed-cli.json`。这只是虚构初始输入验证，未调用模型、未运行新的完整履约演示。

## 模型评测与保留失败

v1开发40条38完成95%、引用17/17；完整冻结60条57完成95%、引用25/26=96.15%、禁止副作用0。028仅口头人工未移交仍使A06失败，v1不算完整工程验收。一次9条冻结尝试因真实超时停止，稳定性因A06在10/30中止，均原样保留；后者有精确schema清理sidecar及在途用量未知说明。

修复后v2使用同一40开发任务，独立新60由未继承旧结果上下文的编写子任务生成，未读旧模型输出/凭据；类别/fixture/outcome/评分不变，全新措辞、订单前缀及政策窗口。数据编写不是独立产品审查。新60须等新开发集达标后才冻结，不将已曝光旧60再次称作盲测。

v2完整开发集真实执行40/40、完成39/40=97.5%、自动引用17/17=100%、禁止副作用0、非预期运行错误0；Agent p50/p95为21.054/28.219秒，158次模型调用、131次工具、441,038 observed token。唯一dev-018只核验旧政策未生成草稿，保持失败。原始报告为`evidence/eval-dev-v2-latest.json`，精确源码SHA对应f84808b，运行期间仅测试/文档变动。

`evals/freeze-v2.json`已保存原始开发报告、13项关键源码/提示/评测器、新60数据、模型协议和9/8/60/120/1200预算的哈希/身份。新60 fixture真实PG/检查点预检60/60、零模型调用且独立schema已清理。Git归档逐字节验证13项源码、两数据集和开发报告一致，旧v1冻结文件不变且旧源码可按历史commit回查，无私有配置/实际秘密值，见`evidence/archive-portability.json`。

新60第一次完整实测：56/60=93.33%、自动引用26/26=100%、禁止副作用0，达到85%/95%/0自动门槛；228次模型调用、188次工具、649,928 observed token，Agent p50/p95为21.281/33.906秒，独立schema已清理。019/020序列号冲突直接转人工而未按任务澄清，024/025只核验旧政策而未生成草稿，四项保持未完成；原始runtime_error_count为2，均为前两项受控AgentStopped，非预期代理通信失败为0，不把所有错误概括为零。027/028缺政策实际进入HUMAN_ACTIVE且无草稿/正式单/履约/库存增量，原A06缺口复验通过。原始完整报告为`evidence/eval-holdout-v2-latest.json`，未按其结果改提示词或评分。

10个关键场景各3次稳定性已真实执行30/30：28完成93.33%、引用18/18、禁止副作用0、原始运行错误0、非预期通信失败0；128次模型调用、102次工具、376,006 observed token，Agent p50/p95为24.710/28.424秒，schema已清理。旧政策场景仅1/3完成，两次只核验未建草稿；其余9类均3/3。原始证据为`evidence/eval-stability-v2-latest.json`，不能只报告其中成功的一次。

三份完整v2报告合计514次模型调用、421次工具、1,466,972 observed token；这仅是该三次完整运行的代理usage，不含历史失败/在途未返回usage的未知消耗，货币成本未知，不是完整订阅额度账单。详表和机器可读汇总见evaluation及其JSON。引用语义人工抽查仍待用户/审查者；开发代理对照见citation-check，均不等于独立审查。

## 限制及用户待办

首版为虚构商家模拟试用，不含真实支付/物流、公司身份/OMS/WMS、维修/跨SKU等连接器；external明确失败，pilot拒绝demo账号/履约。未测公网HTTPS、多机、容量或生产灾难切换。历史仅最近公开一页，bot历史列表使用明确专用非管理员reader。其余边界见known-limitations。

当前无需用户再提供模型/embedding key或逐阶段批准，没有未解决的本地环境/权限阻塞。用户需在审查端提供此私有仓库读取权限或源码归档，安排ChatGPT独立审查和政策引用人工语义抽查。REVIEW保持待审查，不宣称真实生产就绪。旧政策不生成草稿与序列号冲突过度转人工仍是实测质量限制，后续修复需保留本轮结果并采用明确新版本协议。后续继续先fetch远程、读PR/交接/审查材料，保留工作，不自动合并。

## 独立审查交接

将总PR、本文、SPEC、acceptance、evaluation及其原始报告交给审查者。Codex本机gh能访问私有仓库，不代表ChatGPT已获得同一授权；由用户在审查端授予此仓库读取权限，或附上用`git archive --format=zip --output=.local/review-bundle.zip HEAD`生成的源码包。Git归档不含忽略的`.env`/`.local`，模型密钥和OAuth资料不应随审查提供。

建议审查请求：按SPEC业务范围与A01—A34独立检查实现、权限/事务/故障保护及证据真实性，并人工抽查政策引用的自然语言是否受版本/条款支持；逐项给出严重性、代码位置、复现条件和建议。保留v1失败及v2完整/中断记录，区分开发者自测与独立结论。当前没有收到此类意见，`REVIEW.md`保持待审查，PR不自动合并。
