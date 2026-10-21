import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowRight,
  Boxes,
  CircleHelp,
  ClipboardCheck,
  ExternalLink,
  FileCheck2,
  Headphones,
  MessageCircle,
  PackageCheck,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
} from "lucide-react";
import {
  api,
  date,
  money,
  shortId,
  statusName,
  statuses,
  typeName,
  type Case as CaseRecord,
  type Operation,
  type Policy,
} from "./api";
import {
  Alert,
  Checklist,
  Confirm,
  Empty,
  PageTitle,
  PolicyView,
  ResourceState,
  Status,
  Submit,
  useAction,
  useResource,
} from "./components";
import { CaseCards } from "./customer";
import { useAuth } from "./main";

export function StaffCasesPage() {
  const resource = useResource<CaseRecord[]>("/staff/cases"),
    [filter, setFilter] = useState(""),
    [kind, setKind] = useState(""),
    [search, setSearch] = useState("");
  const rows = resource.data || [],
    visible = rows.filter(
      (row) =>
        (!filter || row.status === filter) &&
        (!kind || row.type === kind) &&
        (!search ||
          [row.number, row.order_unit_id, row.pending_reason]
            .join(" ")
            .toLowerCase()
            .includes(search.toLowerCase())),
    );
  const cards = [
    [
      "待初审",
      rows.filter((row) => row.status === "PENDING_REVIEW").length,
      ClipboardCheck,
    ],
    [
      "待收货 / 验收",
      rows.filter((row) =>
        ["AWAITING_RETURN", "INSPECTING"].includes(row.status),
      ).length,
      PackageCheck,
    ],
    [
      "异常需关注",
      rows.filter((row) =>
        ["MANUAL_REVIEW", "WAITING_STOCK"].includes(row.status),
      ).length,
      CircleHelp,
    ],
  ];
  return (
    <>
      <PageTitle
        eyebrow="员工工作区"
        title="让每一件售后，顺利往前走。"
        description="核对事实、记录审核与验收，再按授权安排履约。"
        action={
          <button className="button secondary" onClick={resource.reload}>
            <RefreshCw size={15} />
            刷新待办
          </button>
        }
      />
      <div className="metric-grid">
        {cards.map(([label, value, Icon]: any) => (
          <div className="metric" key={label}>
            <Icon size={23} />
            <div>
              <span>{label}</span>
              <strong>{resource.loading ? "—" : value}</strong>
            </div>
            <small>来自当前申请记录</small>
          </div>
        ))}
      </div>
      <section className="filter-bar">
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="搜索售后申请"
            placeholder="搜索申请编号、设备记录或异常原因"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <select
          aria-label="申请状态"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <option value="">全部状态</option>
          {Object.entries(statuses)
            .slice(0, 11)
            .map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
        </select>
        <select
          aria-label="办理类型"
          value={kind}
          onChange={(event) => setKind(event.target.value)}
        >
          <option value="">全部办理类型</option>
          <option value="return">退货退款</option>
          <option value="exchange">同款换货</option>
        </select>
      </section>
      <ResourceState {...resource}>
        {visible.length ? (
          <CaseCards staff cases={visible} />
        ) : (
          <div className="panel">
            <Empty
              title={rows.length ? "没有符合筛选的申请" : "当前没有售后待办"}
              description="新的客户申请会出现在这里，待办数来自真实记录。"
            />
          </div>
        )}
      </ResourceState>
    </>
  );
}
interface Inventory {
  product_id: string;
  sku: string;
  name: string;
  available: number;
  reserved: number;
  dispatched: number;
  units: {
    id: string;
    serial_number: string;
    status: string;
    source: string;
  }[];
}
export function InventoryPage() {
  const resource = useResource<Inventory[]>("/staff/inventory"),
    { user } = useAuth(),
    action = useAction();
  const [product, setProduct] = useState(""),
    [serials, setSerials] = useState(""),
    [source, setSource] = useState(""),
    [expanded, setExpanded] = useState("");
  return (
    <>
      <PageTitle
        eyebrow="员工工作区 / 新品库存"
        title="库存有依据，换货才有着落。"
        description="这里只统计可销售新品。客户寄回的故障设备单独保管，不计入可用库存。"
        action={
          <button className="button secondary" onClick={resource.reload}>
            <RefreshCw size={15} />
            刷新库存
          </button>
        }
      />
      <ResourceState {...resource}>
        <section className="panel table-panel">
          <table>
            <thead>
              <tr>
                <th>商品 / SKU</th>
                <th>可用新品</th>
                <th>已预占</th>
                <th>已出库</th>
                <th>设备明细</th>
              </tr>
            </thead>
            <tbody>
              {resource.data?.map((row) => (
                <tr key={row.product_id}>
                  <td>
                    <strong>{row.name}</strong>
                    <small>{row.sku}</small>
                  </td>
                  <td>
                    <span
                      className={`stock-count ${row.available === 0 ? "low" : ""}`}
                    >
                      {row.available}
                    </span>
                  </td>
                  <td>{row.reserved}</td>
                  <td>{row.dispatched}</td>
                  <td>
                    <button
                      className="text-button"
                      onClick={() =>
                        setExpanded(
                          expanded === row.product_id ? "" : row.product_id,
                        )
                      }
                    >
                      {expanded === row.product_id ? "收起" : "查看明细"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!resource.data?.length && <Empty title="尚无库存记录" />}
        </section>
        {resource.data
          ?.filter((row) => row.product_id === expanded)
          .map((row) => (
            <section className="panel" key={row.product_id}>
              <h2>{row.name} · 设备明细</h2>
              <div className="inventory-units">
                {row.units.map((unit) => (
                  <div className="row between" key={unit.id}>
                    <code>{unit.serial_number}</code>
                    <span className="tag">
                      {(
                        {
                          AVAILABLE: "可用新品",
                          RESERVED: "售后预占",
                          DISPATCHED: "已出库",
                        } as any
                      )[unit.status] || unit.status}
                    </span>
                    <small>{unit.source}</small>
                  </div>
                ))}
              </div>
            </section>
          ))}
      </ResourceState>
      {user?.role === "admin" && (
        <section className="panel narrow">
          <h2>登记新品补货</h2>
          <p className="muted">
            每台新品登记独立序列号。保存后，缺货申请仍需员工核验并继续。
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              action.run(async () => {
                await api("/admin/inventory/receipts", "POST", {
                  product_id: product,
                  serial_numbers: serials
                    .split(/\r?\n/)
                    .map((value) => value.trim())
                    .filter(Boolean),
                  source,
                });
                setSerials("");
                resource.reload();
              }, "新品补货已登记");
            }}
          >
            <label>
              商品
              <select
                required
                value={product}
                onChange={(event) => setProduct(event.target.value)}
              >
                <option value="">选择商品</option>
                {resource.data?.map((row) => (
                  <option value={row.product_id} key={row.product_id}>
                    {row.name} · {row.sku}
                  </option>
                ))}
              </select>
            </label>
            <label>
              新品序列号（每行一台）
              <textarea
                required
                rows={4}
                value={serials}
                onChange={(event) => setSerials(event.target.value)}
              />
            </label>
            <label>
              补货来源或入库单号
              <input
                required
                maxLength={128}
                value={source}
                onChange={(event) => setSource(event.target.value)}
              />
            </label>
            <Alert>{action.error}</Alert>
            <Alert tone="success">{action.success}</Alert>
            <Submit busy={action.busy}>
              确认登记补货
              <Boxes size={16} />
            </Submit>
          </form>
        </section>
      )}
    </>
  );
}
export function OperationsPage() {
  const resource = useResource<Operation[]>("/staff/operations"),
    [filter, setFilter] = useState("");
  const rows = (resource.data || []).filter(
    (row) => !filter || row.status === filter,
  );
  return (
    <>
      <PageTitle
        eyebrow="员工工作区 / 履约与核对"
        title="结果不明确时，先核对再继续。"
        description="查看退款与出库任务；外部结果未知时保留义务与库存，避免重复执行。"
        action={
          <button className="button secondary" onClick={resource.reload}>
            <RefreshCw size={15} />
            刷新任务
          </button>
        }
      />
      <section className="filter-bar">
        <select
          aria-label="履约状态"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <option value="">全部履约状态</option>
          {[
            "PENDING",
            "RUNNING",
            "UNKNOWN",
            "AWAITING_MANUAL",
            "SUCCEEDED",
            "FAILED",
          ].map((value) => (
            <option key={value} value={value}>
              {value === "AWAITING_MANUAL"
                ? "待人工办理登记"
                : statusName(value)}
            </option>
          ))}
        </select>
        <span className="caption">仅展示数据库已创建的履约任务</span>
      </section>
      <ResourceState {...resource}>
        {rows.length ? (
          rows.map((operation) => (
            <OperationCard
              key={operation.id + ":" + operation.version}
              operation={operation}
              reload={resource.reload}
            />
          ))
        ) : (
          <div className="panel">
            <Empty title="当前没有符合条件的履约任务" />
          </div>
        )}
      </ResourceState>
    </>
  );
}
function OperationCard({
  operation,
  reload,
}: {
  operation: Operation;
  reload: () => void;
}) {
  const action = useAction(),
    [open, setOpen] = useState(false),
    [reference, setReference] = useState(""),
    [evidenceText, setEvidenceText] = useState(""),
    [status, setStatus] = useState("SUCCEEDED"),
    [amount, setAmount] = useState(""),
    [tracking, setTracking] = useState(""),
    [device, setDevice] = useState(""),
    [performedAt, setPerformedAt] = useState(""),
    [verified, setVerified] = useState(false),
    [confirm, setConfirm] = useState(false);
  const snapshot = operation.request_snapshot;
  const canRecord =
    operation.status === "UNKNOWN" ||
    (operation.execution_mode === "manual" &&
      operation.status === "AWAITING_MANUAL");
  async function record() {
    await action.run(async () => {
      const evidence = {
        external_reference: reference,
        evidence: evidenceText,
        performed_at: new Date(performedAt).toISOString(),
        ...(operation.kind === "refund"
          ? {
              amount_cents: Math.round(Number(amount) * 100),
              currency: "CNY",
              refund_reference: reference,
            }
          : {
              outbound_device_id: device,
              sku: snapshot.sku,
              shipping_address: snapshot.shipping_address,
              tracking_number: tracking,
              shipped_at: new Date(performedAt).toISOString(),
            }),
        ...(operation.execution_mode === "demo" ? { simulated: true } : {}),
      };
      await api(
        `/staff/operations/${operation.id}/${operation.execution_mode === "manual" ? "record-manual-result" : "reconcile"}`,
        "POST",
        {
          expected_version: operation.version,
          status,
          evidence,
          ...(operation.execution_mode === "manual"
            ? { obligation_hash: operation.request_hash }
            : {}),
        },
      );
      setConfirm(false);
      reload();
    }, "核对结果已保存");
  }
  return (
    <section className="panel operation-card">
      <div className="row between">
        <div className="row">
          <span className="round-icon">
            <PackageCheck size={20} />
          </span>
          <div>
            <h2>{typeName(operation.kind)}</h2>
            <span className="caption">操作记录 {shortId(operation.id)}</span>
          </div>
        </div>
        <Status value={operation.status} />
      </div>
      <div className="operation-facts">
        <div>
          <small>执行方式</small>
          <strong>
            {operation.execution_mode === "demo"
              ? "模拟履约"
              : operation.execution_mode === "manual"
                ? "外部人工办理"
                : "外部连接器"}
          </strong>
        </div>
        <div>
          <small>
            {operation.kind === "refund" ? "应退款金额" : "换货 SKU"}
          </small>
          <strong>
            {operation.kind === "refund"
              ? money(snapshot.amount_cents)
              : snapshot.sku}
          </strong>
        </div>
        <div>
          <small>已尝试次数</small>
          <strong>{operation.attempts}</strong>
        </div>
        <Link to={`/staff/cases/${operation.case_id}`} className="text-button">
          查看关联申请
          <ArrowRight size={14} />
        </Link>
      </div>
      {operation.status === "SUCCEEDED" && (
        <Alert tone="success">
          {operation.kind === "refund"
            ? `${operation.execution_mode === "demo" ? "模拟" : ""}退款成功`
            : `${operation.execution_mode === "demo" ? "模拟" : ""}换货已发出`}
        </Alert>
      )}
      {operation.status === "UNKNOWN" && (
        <Alert tone="warning">
          外部结果未知。请核实原操作结果，禁止直接重发退款或重复出库。
        </Alert>
      )}
      {operation.status === "FAILED" && (
        <Alert tone="warning">
          外部已明确失败，请核查申请中的处理说明并联系相关人员。
        </Alert>
      )}
      {operation.kind !== "refund" && snapshot.shipping_address && (
        <details>
          <summary>核对本次出库设备与地址</summary>
          <p>设备标识 {snapshot.outbound_device_id}</p>
          <p>
            {snapshot.shipping_address.name} · {snapshot.shipping_address.phone}
            <br />
            {snapshot.shipping_address.region}{" "}
            {snapshot.shipping_address.detail}
          </p>
        </details>
      )}
      {canRecord && (
        <>
          <button className="button secondary" onClick={() => setOpen(!open)}>
            {open
              ? "收起核对表单"
              : operation.execution_mode === "manual"
                ? "登记人工办理结果"
                : "登记核对证据"}
          </button>
          {open && (
            <form
              className="reconcile-form"
              onSubmit={(event) => {
                event.preventDefault();
                setConfirm(true);
              }}
            >
              <div className="field-grid">
                <label>
                  核对结论
                  <select
                    value={status}
                    onChange={(event) => setStatus(event.target.value)}
                  >
                    <option value="SUCCEEDED">确认已成功</option>
                    <option value="FAILED">确认已明确失败</option>
                  </select>
                </label>
                <label>
                  外部回执编号
                  <input
                    required
                    maxLength={128}
                    value={reference}
                    onChange={(event) => setReference(event.target.value)}
                  />
                </label>
                <label>
                  实际办理时间
                  <input
                    type="datetime-local"
                    required
                    value={performedAt}
                    onChange={(event) => setPerformedAt(event.target.value)}
                  />
                </label>
                {operation.kind === "refund" ? (
                  <label>
                    核对实际退款金额（元）
                    <input
                      type="number"
                      required
                      step="0.01"
                      min="0"
                      value={amount}
                      onChange={(event) => setAmount(event.target.value)}
                    />
                  </label>
                ) : (
                  <>
                    <label>
                      实际出库设备标识
                      <input
                        required
                        value={device}
                        onChange={(event) => setDevice(event.target.value)}
                      />
                    </label>
                    <label>
                      出库运单号
                      <input
                        required
                        value={tracking}
                        onChange={(event) => setTracking(event.target.value)}
                      />
                    </label>
                  </>
                )}
              </div>
              <label>
                核对依据与凭证说明
                <textarea
                  required
                  rows={3}
                  value={evidenceText}
                  onChange={(event) => setEvidenceText(event.target.value)}
                />
              </label>
              <Checklist
                label="我已核对原义务与外部证据，确认金额或设备、收件地址一致"
                checked={verified}
                onChange={setVerified}
              />
              <Alert>{action.error}</Alert>
              <Submit busy={action.busy} disabled={!verified}>
                保存核对结果
              </Submit>
            </form>
          )}
        </>
      )}
      {confirm && (
        <Confirm
          title="确认保存核对结果？"
          busy={action.busy}
          error={action.error}
          onClose={() => setConfirm(false)}
          onConfirm={record}
        >
          <p>
            这会按证据推进履约与申请状态。请再次确认外部回执 {reference}{" "}
            及原办理义务。
          </p>
        </Confirm>
      )}
    </section>
  );
}
export function ConversationPage() {
  const { id } = useParams(),
    [conversationId, setConversationId] = useState(id || ""),
    [current, setCurrent] = useState(id || ""),
    navigate = useNavigate();
  return (
    <>
      <PageTitle
        eyebrow="员工工作区 / 人工接待"
        title="需要人工时，稳妥地接过来。"
        description="接管后暂停机器人新回复与草稿；已合法授权的业务履约继续按申请状态处理。"
      />
      <section className="panel narrow">
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            setCurrent(conversationId);
            navigate(`/staff/conversations/${conversationId}`);
          }}
        >
          <label>
            Chatwoot 会话编号
            <input
              type="number"
              min="1"
              required
              value={conversationId}
              onChange={(event) => setConversationId(event.target.value)}
              placeholder="从聊天台查看会话编号"
            />
          </label>
          <Submit>
            读取会话
            <ArrowRight size={16} />
          </Submit>
        </form>
      </section>
      {current && <ConversationControl key={current} id={current} />}
    </>
  );
}
function ConversationControl({ id }: { id: string }) {
  const resource = useResource<any>(`/staff/conversations/${id}`),
    action = useAction(),
    [confirm, setConfirm] = useState("");
  return (
    <ResourceState {...resource}>
      {resource.data && (
        <section className="panel narrow">
          <div className="row between">
            <div className="row">
              <MessageCircle size={24} />
              <h2>会话 #{id}</h2>
            </div>
            <Status value={resource.data.control_mode} />
          </div>
          <p className="muted">
            只有明确交回并核验 Chatwoot 状态与指派后，机器人服务才会恢复。
          </p>
          <Alert>{action.error}</Alert>
          <Alert tone="success">{action.success}</Alert>
          <div className="actions">
            {resource.data.control_mode === "BOT_ACTIVE" ? (
              <button className="button" onClick={() => setConfirm("takeover")}>
                <Headphones size={17} />
                接管会话
              </button>
            ) : (
              <button
                className="button"
                onClick={() => setConfirm("return-to-bot")}
              >
                <ShieldCheck size={17} />
                显式交回机器人
              </button>
            )}
            <button className="button secondary" onClick={resource.reload}>
              <RefreshCw size={15} />
              重新核验
            </button>
            {resource.data.chatwoot_url && (
              <a
                className="text-button"
                href={resource.data.chatwoot_url}
                target="_blank"
                rel="noreferrer"
              >
                打开 Chatwoot
                <ExternalLink size={14} />
              </a>
            )}
          </div>
          <OutboundRecovery
            conversation={Number(id)}
            onChange={resource.reload}
          />
          {resource.data.linked_cases?.length > 0 && (
            <div className="address-block">
              <h3>关联售后申请</h3>
              {resource.data.linked_cases.map((item: any) => (
                <p key={item.id}>
                  <Link to={`/staff/cases/${item.id}`}>
                    {item.number}
                    <ArrowRight size={13} />
                  </Link>
                </p>
              ))}
            </div>
          )}
          {confirm && (
            <Confirm
              title={
                confirm === "takeover" ? "确认接管会话？" : "确认交回机器人？"
              }
              busy={action.busy}
              error={action.error}
              onClose={() => setConfirm("")}
              onConfirm={() =>
                action.run(
                  async () => {
                    await api(`/staff/conversations/${id}/${confirm}`, "POST", {
                      expected_revision: resource.data.control_revision,
                    });
                    setConfirm("");
                    resource.reload();
                  },
                  confirm === "takeover"
                    ? "已请求接管，机器人保持静默"
                    : "已提交显式交回并重新核验",
                )
              }
            >
              <p>
                {confirm === "takeover"
                  ? "机器人将暂停新的对话回复与方案准备。您可以在 Chatwoot 继续接待。"
                  : "这会清理人工指派并尝试恢复机器人。状态无法确认时仍保持暂停。"}
              </p>
            </Confirm>
          )}
        </section>
      )}
    </ResourceState>
  );
}
function OutboundRecovery({
  conversation,
  onChange,
}: {
  conversation: number;
  onChange: () => void;
}) {
  const resource = useResource<any[]>("/staff/outbound-messages");
  const rows = (resource.data || []).filter(
    (row) =>
      row.external_conversation_id === conversation &&
      ["UNKNOWN", "SENDING"].includes(row.status),
  );
  if (resource.loading)
    return <p className="caption">正在核对待确认的机器人消息…</p>;
  if (resource.error)
    return (
      <Alert tone="warning">
        消息恢复记录读取失败。请刷新核验后再交回机器人。
      </Alert>
    );
  if (!rows.length) return null;
  return (
    <div className="address-block">
      <h3>机器人消息结果待确认</h3>
      <p className="muted">
        先对照 Chatwoot
        实际消息核对发送结果。结果未知期间会话保持静默；登记后仍需员工显式交回，不会自动重发。
      </p>
      {rows.map((row) => (
        <OutboundRecord
          key={row.id}
          row={row}
          onChange={() => {
            resource.reload();
            onChange();
          }}
        />
      ))}
    </div>
  );
}
function OutboundRecord({ row, onChange }: { row: any; onChange: () => void }) {
  const action = useAction(),
    [outcome, setOutcome] = useState("delivered"),
    [messageId, setMessageId] = useState(""),
    [reason, setReason] = useState(""),
    [proof, setProof] = useState(""),
    [verified, setVerified] = useState(false),
    [confirm, setConfirm] = useState(false);
  async function save() {
    await action.run(async () => {
      await api(`/staff/outbound-messages/${row.id}/reconcile`, "POST", {
        expected_control_revision: row.current_control_revision,
        outcome,
        ...(outcome === "delivered" ? { message_id: Number(messageId) } : {}),
        evidence: { reason, proof },
      });
      setConfirm(false);
      onChange();
    }, "核对结论已保存；会话继续保持暂停，需显式交回");
  }
  return (
    <div className="outbound-record">
      <div className="row between">
        <span className="caption">待确认消息 {shortId(row.id)}</span>
        <Status value={row.status} />
      </div>
      <blockquote>{row.content}</blockquote>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          setConfirm(true);
        }}
      >
        <label>
          已核实的发送结果
          <select
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
          >
            <option value="delivered">Chatwoot 中可找到这条机器人消息</option>
            <option value="not_delivered">已人工确认消息没有送达</option>
          </select>
        </label>
        {outcome === "delivered" && (
          <label>
            Chatwoot 消息 ID
            <input
              type="number"
              min="1"
              required
              value={messageId}
              onChange={(event) => setMessageId(event.target.value)}
            />
          </label>
        )}
        <label>
          核对原因
          <textarea
            required
            minLength={5}
            rows={2}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <label>
          核对证据与依据
          <textarea
            required
            minLength={5}
            rows={2}
            value={proof}
            onChange={(event) => setProof(event.target.value)}
            placeholder="记录对照聊天台的核对过程、凭据或负责人员。"
          />
        </label>
        <Checklist
          label="我已核实原消息与实际记录，愿意对该核对结论负责"
          checked={verified}
          onChange={setVerified}
        />
        <Alert>{action.error}</Alert>
        <Submit busy={action.busy} disabled={!verified}>
          保存消息核对结果
        </Submit>
      </form>
      {confirm && (
        <Confirm
          title="确认记录消息核对结论？"
          busy={action.busy}
          error={action.error}
          onClose={() => setConfirm(false)}
          onConfirm={save}
        >
          <p>
            {outcome === "delivered"
              ? "后端会再次读取真实 Chatwoot 历史，验证消息 ID、机器人发送者及原文完全一致。"
              : "这是一项明确的员工人工核对声明，会保留审计记录。保存后不会自动重发旧消息。"}
          </p>
        </Confirm>
      )}
    </div>
  );
}
export function PoliciesPage() {
  const resource = useResource<Policy[]>("/admin/policies"),
    action = useAction(),
    [selected, setSelected] = useState(""),
    [source, setSource] = useState(""),
    [validated, setValidated] = useState(false),
    [publish, setPublish] = useState(false);
  const policy =
    resource.data?.find((item) => item.id === selected) || resource.data?.[0];
  function copy() {
    if (!policy) return;
    setSource(
      JSON.stringify(
        {
          version: policy.version + "-new",
          scope: policy.scope,
          rules: policy.rules,
          sections: policy.sections,
          rule_source_map: policy.rule_source_map,
        },
        null,
        2,
      ),
    );
    setValidated(false);
  }
  return (
    <>
      <PageTitle
        eyebrow="管理员 / 政策版本"
        title="规则有版本，承诺有依据。"
        description="已发布政策不可直接修改。新版本经完整校验后发布，历史申请继续引用原快照。"
      />
      <ResourceState {...resource}>
        <div className="detail-grid">
          <div>
            <section className="panel">
              <label>
                查看已发布版本
                <select
                  value={policy?.id || ""}
                  onChange={(event) => setSelected(event.target.value)}
                >
                  {resource.data?.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.version} · {date(item.published_at)}
                    </option>
                  ))}
                </select>
              </label>
              <PolicyView policy={policy} />
              <button className="button secondary" onClick={copy}>
                <FileCheck2 size={16} />
                复制为新版本草案
              </button>
            </section>
            <section className="panel">
              <h2>校验与发布新版本</h2>
              <p className="muted">
                政策包必须包含范围、可执行规则、条款与规则来源映射。请先校验再确认发布。
              </p>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  action.run(async () => {
                    await api(
                      "/admin/policies/validate",
                      "POST",
                      JSON.parse(source),
                    );
                    setValidated(true);
                  }, "政策包校验通过，可以核对后发布");
                }}
              >
                <label>
                  新版本政策包
                  <textarea
                    className="code-editor"
                    rows={18}
                    required
                    value={source}
                    onChange={(event) => {
                      setSource(event.target.value);
                      setValidated(false);
                    }}
                    spellCheck={false}
                    placeholder="从已发布版本复制草案，再修改版本号与条款内容。"
                  />
                </label>
                <Alert>{action.error}</Alert>
                <Alert tone="success">{action.success}</Alert>
                <div className="actions">
                  <Submit busy={action.busy}>校验政策包</Submit>
                  <button
                    className="button secondary"
                    disabled={!validated || action.busy}
                    type="button"
                    onClick={() => setPublish(true)}
                  >
                    确认发布新版本
                    <ArrowRight size={16} />
                  </button>
                </div>
              </form>
            </section>
          </div>
          <aside>
            <section className="panel tip-panel">
              <ShieldCheck size={28} />
              <h2>发布前请核对</h2>
              <p>
                条款文本与可执行规则必须一致，退货和换货时间窗需要明确的条款来源。
              </p>
              <p>
                发布后保存为不可变版本，不能覆盖已发布内容。业务仍会在关键操作时再次校验。
              </p>
            </section>
          </aside>
        </div>
      </ResourceState>
      {publish && (
        <Confirm
          title="发布这份政策版本？"
          busy={action.busy}
          error={action.error}
          onClose={() => setPublish(false)}
          onConfirm={() =>
            action.run(async () => {
              await api("/admin/policies/publish", "POST", JSON.parse(source));
              setPublish(false);
              resource.reload();
            }, "政策新版本已发布")
          }
        >
          <p>
            发布后不可原地修改。历史订单和申请仍按其已有政策版本及快照办理。
          </p>
        </Confirm>
      )}
    </>
  );
}
export function SystemPage() {
  const resource = useResource<any>("/admin/system"),
    action = useAction(),
    [confirm, setConfirm] = useState(false);
  const data = resource.data;
  const dependencies = Array.isArray(data?.dependencies)
    ? data.dependencies
    : data?.dependencies
      ? Object.entries(data.dependencies).map(([name, status]) => ({
          name,
          status,
        }))
      : [];
  return (
    <>
      <PageTitle
        eyebrow="管理员 / 系统运行"
        title="运行状态，清楚可见。"
        description="查看依赖与任务积压；必要时暂停 Agent，让人工继续处理业务。"
        action={
          <button className="button secondary" onClick={resource.reload}>
            <RefreshCw size={15} />
            刷新状态
          </button>
        }
      />
      <ResourceState {...resource}>
        {data && (
          <>
            <div className="metric-grid">
              <div className="metric">
                <Server />
                <div>
                  <span>待处理任务</span>
                  <strong>
                    {data.pending_jobs ?? data.jobs?.pending ?? "—"}
                  </strong>
                </div>
              </div>
              <div className="metric">
                <CircleHelp />
                <div>
                  <span>失败任务</span>
                  <strong>
                    {data.failed_jobs ?? data.jobs?.failed ?? "—"}
                  </strong>
                </div>
              </div>
              <div className="metric">
                <PackageCheck />
                <div>
                  <span>结果待核对</span>
                  <strong>{data.unknown_operations ?? "—"}</strong>
                </div>
              </div>
            </div>
            <section className="panel">
              <div className="row between">
                <div>
                  <h2>Agent 服务开关</h2>
                  <p className="muted">
                    暂停新的 Agent 对话行为；正常授权的履约任务不受此开关影响。
                  </p>
                </div>
                <Status
                  value={data.agent_enabled ? "BOT_ACTIVE" : "HUMAN_ACTIVE"}
                />
              </div>
              <Alert>{action.error}</Alert>
              <Alert tone="success">{action.success}</Alert>
              <button
                className="button secondary"
                onClick={() => setConfirm(true)}
              >
                {data.agent_enabled ? "暂停 Agent 服务" : "恢复 Agent 服务"}
              </button>
              <p className="caption">
                运行模式 {data.app_mode ?? data.mode ?? "—"} · 环境{" "}
                {data.environment_id ?? "—"}
              </p>
            </section>
            <section className="panel">
              <h2>依赖与连接状态</h2>
              {dependencies.length ? (
                <div className="dependency-list">
                  {dependencies.map((item: any) => (
                    <div className="row between" key={item.name}>
                      <span>
                        <Server size={17} />
                        {item.name}
                      </span>
                      <span className="tag">
                        {typeof item.status === "object"
                          ? item.status.status
                          : String(item.status)}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted">
                  当前响应未提供依赖明细，请查看部署检查记录。
                </p>
              )}
            </section>
            {data.failed_job_details?.length > 0 && (
              <section className="panel">
                <h2>失败任务记录</h2>
                {data.failed_job_details.map((job: any) => (
                  <div className="operation-summary" key={job.id}>
                    <strong>{job.kind}</strong>
                    <p>{job.last_error}</p>
                    <span className="caption">{shortId(job.id)}</span>
                  </div>
                ))}
              </section>
            )}
            {confirm && (
              <Confirm
                title={
                  data.agent_enabled ? "暂停 Agent 服务？" : "恢复 Agent 服务？"
                }
                busy={action.busy}
                error={action.error}
                onClose={() => setConfirm(false)}
                onConfirm={() =>
                  action.run(async () => {
                    await api("/admin/system/agent", "PATCH", {
                      enabled: !data.agent_enabled,
                    });
                    setConfirm(false);
                    resource.reload();
                  }, "Agent 开关已更新")
                }
              >
                <p>
                  会话仍受本地接管状态、实时 Chatwoot
                  状态与身份权限约束。恢复开关不会自动交回人工持有的会话。
                </p>
              </Confirm>
            )}
          </>
        )}
      </ResourceState>
    </>
  );
}
