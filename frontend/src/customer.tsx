import { useEffect, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FileCheck2,
  Headphones,
  Keyboard,
  Mouse,
  MessageCircle,
  Package,
  RefreshCw,
  ShieldCheck,
  Truck,
} from "lucide-react";
import {
  api,
  date,
  money,
  shortId,
  statusName,
  reasonName,
  statuses,
  typeName,
  type Address,
  type Case as CaseRecord,
  type Order,
  type Proposal,
  type Unit,
} from "./api";
import {
  AddressFields,
  Alert,
  Attachments,
  Breadcrumb,
  Checklist,
  Confirm,
  Empty,
  EvidenceLinks,
  PageTitle,
  PolicyView,
  ResourceState,
  Status,
  Submit,
  useAction,
  useResource,
} from "./components";

function ProductIcon({ name }: { name: string }) {
  const Icon = /耳机|headphone/i.test(name)
    ? Headphones
    : /键盘|keyboard/i.test(name)
      ? Keyboard
      : /鼠标|mouse/i.test(name)
        ? Mouse
        : Package;
  return (
    <div className="product-icon">
      <Icon size={34} strokeWidth={1.3} />
    </div>
  );
}
export function OrdersPage() {
  const resource = useResource<Order[]>("/orders");
  return (
    <>
      <PageTitle
        eyebrow="客户服务 / 我的订单"
        title="您的设备，我们继续照顾。"
        description="查看订单、核对设备，或为其中一台发起售后申请。"
      />
      <div className="service-strip">
        <div>
          <ShieldCheck />
          <span>
            订单归属核验<small>仅展示当前账号的记录</small>
          </span>
        </div>
        <div>
          <FileCheck2 />
          <span>
            条件与政策可追溯<small>按购买时对应版本办理</small>
          </span>
        </div>
        <div>
          <Truck />
          <span>
            办理进展随时查看<small>寄回、验收与履约均有记录</small>
          </span>
        </div>
      </div>
      <div className="section-heading">
        <h2>
          我的订单 <span className="count">{resource.data?.length ?? "—"}</span>
        </h2>
        <button className="text-button" onClick={resource.reload}>
          <RefreshCw size={14} />
          刷新记录
        </button>
      </div>
      <ResourceState {...resource}>
        {!resource.data?.length ? (
          <div className="panel">
            <Empty
              title="暂无可查看的订单"
              description="订单将按当前登录账号归属显示。如需帮助，请联系人工。"
            />
          </div>
        ) : (
          <div className="order-list">
            {resource.data.map((order) => (
              <article className="order-card" key={order.id}>
                <div className="order-card-head">
                  <span>
                    订单 <strong>{order.external_id}</strong>
                  </span>
                  <span>签收于 {date(order.delivered_at)}</span>
                  <span className="tag">
                    {order.source === "demo"
                      ? "演示订单"
                      : order.source === "verified"
                        ? "已核验订单"
                        : "订单来源待核验"}
                  </span>
                </div>
                <div className="order-card-body">
                  <div className="order-products">
                    {order.items.map((item) => (
                      <div className="product-row" key={item.product_id}>
                        <ProductIcon name={item.name} />
                        <div>
                          <h3>{item.name}</h3>
                          <p>
                            {item.sku} <span className="dot-separator">·</span>{" "}
                            {item.quantity} 台设备
                          </p>
                        </div>
                        <strong className="product-price">
                          {money(item.net_goods_paid_cents)}
                        </strong>
                      </div>
                    ))}
                  </div>
                  <div className="order-card-end">
                    <span>
                      <small>商品实付</small>
                      <strong>
                        {money(
                          order.items.reduce(
                            (sum, item) => sum + item.net_goods_paid_cents,
                            0,
                          ),
                        )}
                      </strong>
                    </span>
                    <Link
                      className="button secondary"
                      to={`/orders/${order.id}`}
                    >
                      查看设备与售后
                      <ArrowRight size={16} />
                    </Link>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </ResourceState>
      <div className="subtle-note">
        <ShieldCheck size={15} />
        办理金额以该台设备的原实付分摊为准，具体条件请查看订单政策。
      </div>
    </>
  );
}
const blankAddress: Address = { name: "", phone: "", region: "", detail: "" };
function ApplicationForm({
  order,
  unit,
  onClose,
}: {
  order: Order;
  unit: Unit;
  onClose: () => void;
}) {
  const [kind, setKind] = useState("return"),
    [reason, setReason] = useState(""),
    [unused, setUnused] = useState(false),
    [accessories, setAccessories] = useState(false),
    [quality, setQuality] = useState(false),
    [address, setAddress] = useState<Address>(
      order.shipping_address || blankAddress,
    ),
    [evidence, setEvidence] = useState<string[]>([]),
    [eligibility, setEligibility] = useState<any>(null);
  const action = useAction(),
    navigate = useNavigate();
  const facts = {
    reason,
    unused,
    accessories_complete: accessories,
    quality_issue: quality,
    evidence_ids: evidence,
  };
  async function evaluate() {
    await action.run(async () => {
      const result = await api("/eligibility/evaluate", "POST", {
        order_unit_id: unit.id,
        type: kind,
        facts,
      });
      setEligibility(result);
    }, "条件核验已完成");
  }
  return (
    <section className="panel application-form">
      <div className="row between">
        <div>
          <div className="eyebrow">按设备办理</div>
          <h2>发起售后申请</h2>
        </div>
        <button className="text-button" onClick={onClose}>
          收起
        </button>
      </div>
      <p className="muted">
        设备序列号 <strong>{unit.serial_number}</strong> · 原实付分摊{" "}
        {money(unit.allocated_paid_cents)}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          action.run(async () => {
            const result = await api<Proposal>("/proposals", "POST", {
              order_unit_id: unit.id,
              type: kind,
              facts,
              ...(kind === "exchange" ? { shipping_address: address } : {}),
            });
            navigate(`/proposals/${result.id}`);
          });
        }}
      >
        <fieldset>
          <legend>请选择办理类型</legend>
          <div className="choice-grid">
            {[
              ["return", "退货退款", "验收通过后，按原实付分摊办理退款。"],
              [
                "exchange",
                "同款质量换货",
                "同一 SKU 的质量问题换货，缺货时保留申请等待。",
              ],
            ].map(([value, title, detail]) => (
              <label
                className={`choice ${kind === value ? "selected" : ""}`}
                key={value}
              >
                <input
                  type="radio"
                  name="case-type"
                  value={value}
                  checked={kind === value}
                  onChange={() => {
                    setKind(value);
                    setEligibility(null);
                  }}
                />
                <strong>{title}</strong>
                <small>{detail}</small>
              </label>
            ))}
          </div>
        </fieldset>
        <label>
          问题与申请原因
          <textarea
            required
            maxLength={2000}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="请描述设备遇到的问题、发生时间与已尝试的处理方式。"
            rows={4}
          />
        </label>
        <div className="checklist">
          {kind === "return" ? (
            <>
              <Checklist
                label="商品未使用"
                checked={unused}
                onChange={setUnused}
              />
              <Checklist
                label="商品、配件与原包装完整"
                checked={accessories}
                onChange={setAccessories}
              />
            </>
          ) : (
            <Checklist
              label="存在需要员工验收核实的质量问题"
              checked={quality}
              onChange={setQuality}
            />
          )}
        </div>
        <Attachments value={evidence} onChange={setEvidence} />
        {kind === "exchange" && (
          <fieldset>
            <legend>换货收件地址</legend>
            <p className="muted">
              请核对后提交。地址会随确认内容保存，变更后需要重新核对。
            </p>
            <AddressFields value={address} onChange={setAddress} />
          </fieldset>
        )}
        <Alert>{action.error}</Alert>
        {eligibility && (
          <div
            className={`alert ${eligibility.decision === "eligible" ? "success" : "warning"}`}
          >
            <strong>
              {(
                {
                  eligible: "当前信息满足申请条件",
                  missing_information: "仍需补充信息",
                  needs_review: "需要人工核查",
                  ineligible: "当前条件不满足自动办理",
                } as any
              )[eligibility.decision] || eligibility.decision}
            </strong>
            <p>
              适用政策 {eligibility.policy_version || "待核验"} · 截止时间{" "}
              {date(eligibility.deadline)}
            </p>
            {eligibility.missing_fields?.length > 0 && (
              <p>待补充：{eligibility.missing_fields.join("、")}</p>
            )}
            {eligibility.reason_codes?.length > 0 && (
              <details>
                <summary>查看具体核验原因</summary>
                {eligibility.reason_codes.map(reasonName).join(" ")}
              </details>
            )}
          </div>
        )}
        <div className="actions">
          <button
            className="button secondary"
            type="button"
            disabled={action.busy || !reason.trim()}
            onClick={evaluate}
          >
            先核验条件
          </button>
          <Submit busy={action.busy}>
            生成申请预览
            <ArrowRight size={16} />
          </Submit>
        </div>
        <p className="caption">
          生成预览后仍需您明确确认。员工初审同意寄回，不代表已批准退款或换货。
        </p>
      </form>
    </section>
  );
}
export function OrderPage() {
  const { id } = useParams(),
    [search] = useSearchParams();
  const resource = useResource<Order>(`/orders/${id}`),
    [selected, setSelected] = useState<Unit | null>(null);
  useEffect(() => {
    if (resource.data && search.get("unit")) {
      const unit = resource.data.items
        .flatMap((item) => item.units)
        .find((unit) => unit.id === search.get("unit"));
      if (unit) setSelected(unit);
    }
  }, [resource.data, search]);
  return (
    <>
      <Breadcrumb
        items={[
          { label: "我的订单", to: "/orders" },
          { label: resource.data?.external_id || "订单详情" },
        ]}
      />
      <PageTitle
        title="选择需要办理的设备"
        description="一个申请对应一台设备；已有办理记录的设备可直接查看进展。"
      />
      <ResourceState {...resource}>
        {resource.data && (
          <>
            <section className="panel order-detail">
              <div className="row between">
                <div>
                  <span className="caption">订单编号</span>
                  <h2>{resource.data.external_id}</h2>
                </div>
                <div className="right">
                  <span className="tag">
                    {resource.data.source === "demo"
                      ? "演示订单"
                      : resource.data.source === "verified"
                        ? "已核验订单"
                        : "订单来源待核验"}
                  </span>
                  <p className="muted">
                    签收时间 {date(resource.data.delivered_at)}
                  </p>
                </div>
              </div>
              {resource.data.items.map((item) => (
                <div className="order-item" key={item.product_id}>
                  <div className="product-row">
                    <ProductIcon name={item.name} />
                    <div>
                      <h3>{item.name}</h3>
                      <p>
                        {item.sku} · {item.quantity} 台
                      </p>
                    </div>
                  </div>
                  {item.units.map((unit) => (
                    <div
                      className={`unit-row ${selected?.id === unit.id ? "selected" : ""}`}
                      key={unit.id}
                    >
                      <div>
                        <span className="caption">设备序列号</span>
                        <strong>{unit.serial_number}</strong>
                      </div>
                      <div>
                        <span className="caption">该台实付分摊</span>
                        <strong>{money(unit.allocated_paid_cents)}</strong>
                      </div>
                      <div>
                        <span className="caption">适用政策</span>
                        <span>{unit.policy?.version || "待核验"}</span>
                      </div>
                      {unit.active_case_id ? (
                        <Link
                          className="button secondary small"
                          to={`/cases/${unit.active_case_id}`}
                        >
                          查看售后记录
                          <ChevronRight size={15} />
                        </Link>
                      ) : (
                        <button
                          className="button small"
                          onClick={() => setSelected(unit)}
                        >
                          申请售后
                          <ArrowRight size={15} />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </section>
            {selected && (
              <ApplicationForm
                key={selected.id}
                order={resource.data}
                unit={selected}
                onClose={() => setSelected(null)}
              />
            )}
            <section className="panel">
              <PolicyView
                policy={
                  selected?.policy || resource.data.items[0]?.units[0]?.policy
                }
              />
            </section>
          </>
        )}
      </ResourceState>
    </>
  );
}
export function ProposalPage() {
  const { id } = useParams(),
    resource = useResource<Proposal>(`/proposals/${id}`),
    action = useAction(),
    navigate = useNavigate();
  const [confirmed, setConfirmed] = useState(false),
    [dialog, setDialog] = useState(false),
    [editing, setEditing] = useState(false),
    [address, setAddress] = useState<Address>(blankAddress);
  useEffect(() => {
    if (resource.data?.shipping_address)
      setAddress(resource.data.shipping_address);
  }, [resource.data]);
  const proposal = resource.data;
  return (
    <>
      <Breadcrumb
        items={[{ label: "我的订单", to: "/orders" }, { label: "申请预览" }]}
      />
      <PageTitle
        eyebrow="请核对后确认"
        title="这次申请，将按以下内容受理。"
        description="确认后生成正式售后申请，由员工初审。仅生成预览不会推进办理。"
      />
      <ResourceState {...resource}>
        {proposal && (
          <div className="detail-grid">
            <div>
              <section className="panel">
                <div className="row">
                  <FileCheck2 className="accent" />
                  <h2>{typeName(proposal.type)}申请预览</h2>
                  <span className="tag">待您确认</span>
                </div>
                <dl className="definition">
                  <div>
                    <dt>设备记录</dt>
                    <dd>{shortId(proposal.order_unit_id)}</dd>
                  </div>
                  <div>
                    <dt>申请原因</dt>
                    <dd>{proposal.facts.reason}</dd>
                  </div>
                  <div>
                    <dt>办理方式</dt>
                    <dd>{typeName(proposal.type)}</dd>
                  </div>
                  <div>
                    <dt>
                      {proposal.type === "return" ? "预计退款金额" : "换货范围"}
                    </dt>
                    <dd className="large-value">
                      {proposal.type === "return"
                        ? money(proposal.amount_cents)
                        : "同一 SKU 新品"}
                    </dd>
                  </div>
                  <div>
                    <dt>政策版本</dt>
                    <dd>{proposal.policy?.version}</dd>
                  </div>
                  <div>
                    <dt>预览有效期至</dt>
                    <dd>{date(proposal.expires_at)}</dd>
                  </div>
                </dl>
                <EvidenceLinks ids={proposal.facts.evidence_ids} />
                {proposal.shipping_address && (
                  <div className="address-block">
                    <div className="row between">
                      <h3>换货收件地址</h3>
                      <button
                        className="text-button"
                        onClick={() => {
                          setEditing(!editing);
                          setConfirmed(false);
                        }}
                      >
                        修改地址
                      </button>
                    </div>
                    {editing ? (
                      <form
                        onSubmit={(event) => {
                          event.preventDefault();
                          action.run(async () => {
                            await api(`/proposals/${id}`, "PATCH", {
                              expected_version: proposal.version,
                              shipping_address: address,
                            });
                            setEditing(false);
                            setConfirmed(false);
                            resource.reload();
                          }, "地址已更新，请重新核对申请");
                        }}
                      >
                        <AddressFields value={address} onChange={setAddress} />
                        <Submit busy={action.busy}>保存并更新预览</Submit>
                      </form>
                    ) : (
                      <p>
                        {proposal.shipping_address.name} ·{" "}
                        {proposal.shipping_address.phone}
                        <br />
                        {proposal.shipping_address.region}{" "}
                        {proposal.shipping_address.detail}
                      </p>
                    )}
                  </div>
                )}
              </section>
              <section className="panel">
                <PolicyView policy={proposal.policy} />
              </section>
            </div>
            <aside>
              <section className="panel confirmation-panel">
                <span className="round-icon">
                  <ShieldCheck size={26} />
                </span>
                <h2>
                  您的明确确认
                  <br />
                  是办理的起点
                </h2>
                <p>
                  我们会保存您确认的申请内容与政策版本，提交后可在售后申请中随时查看。
                </p>
                <Checklist
                  label="我已核对设备、原因、金额/换货范围及适用政策"
                  checked={confirmed}
                  onChange={setConfirmed}
                />
                <Alert>{action.error}</Alert>
                {proposal.confirmed_case_id ? (
                  <Link
                    className="button"
                    to={`/cases/${proposal.confirmed_case_id}`}
                  >
                    查看已确认申请
                    <ArrowRight size={16} />
                  </Link>
                ) : (
                  <button
                    className="button full"
                    disabled={
                      !confirmed ||
                      editing ||
                      action.busy ||
                      Date.parse(proposal.expires_at) <= Date.now()
                    }
                    onClick={() => setDialog(true)}
                  >
                    确认并提交申请
                    <ArrowRight size={16} />
                  </button>
                )}
                <p className="caption">
                  初审、寄回与验收通过后才安排履约。演示环境中退款与物流均为模拟。
                </p>
              </section>
            </aside>
          </div>
        )}
      </ResourceState>
      {dialog && proposal && (
        <Confirm
          title="确认提交售后申请？"
          busy={action.busy}
          error={action.error}
          onClose={() => setDialog(false)}
          onConfirm={() =>
            action.run(async () => {
              const result = await api<CaseRecord>(
                `/proposals/${id}/confirm`,
                "POST",
                {
                  expected_version: proposal.version,
                  summary_hash: proposal.summary_hash,
                },
              );
              navigate(`/cases/${result.id}`);
            })
          }
        >
          <p>
            {typeName(proposal.type)} ·{" "}
            {proposal.type === "return"
              ? money(proposal.amount_cents)
              : "同一 SKU 换货"}
          </p>
          <p>请确认您已了解寄回与验收要求。员工会继续审核这次申请。</p>
        </Confirm>
      )}
    </>
  );
}
export function CaseCards({
  cases,
  staff = false,
}: {
  cases: CaseRecord[];
  staff?: boolean;
}) {
  return (
    <div className="case-list">
      {cases.map((item) => (
        <Link
          className="case-card"
          to={`${staff ? "/staff" : ""}/cases/${item.id}`}
          key={item.id}
        >
          <div className="case-card-icon">
            {item.type === "return" ? (
              <RefreshCw size={21} />
            ) : (
              <Package size={21} />
            )}
          </div>
          <div className="case-card-main">
            <div className="row">
              <h3>{typeName(item.type)}</h3>
              <Status value={item.status} />
            </div>
            <p>{item.number}</p>
            <span className="caption">受理时间 {date(item.accepted_at)}</span>
          </div>
          <div className="case-card-end">
            <strong>
              {item.type === "return"
                ? money(item.confirmed_payload?.amount_cents)
                : "同款换货"}
            </strong>
            <span>
              查看进展
              <ChevronRight size={16} />
            </span>
          </div>
        </Link>
      ))}
    </div>
  );
}
export function CasesPage() {
  const resource = useResource<CaseRecord[]>("/cases");
  return (
    <>
      <PageTitle
        eyebrow="客户服务 / 售后申请"
        title="每一步进展，都在这里。"
        description="查看申请状态、补充材料与寄回物流，了解下一步需要做什么。"
        action={
          <Link className="button secondary" to="/orders">
            从订单发起申请
            <ArrowRight size={16} />
          </Link>
        }
      />
      <ResourceState {...resource}>
        {resource.data?.length ? (
          <CaseCards cases={resource.data} />
        ) : (
          <div className="panel">
            <Empty
              title="您还没有售后申请"
              description="从自己的订单选择一台设备，核对条件后发起申请。"
              action={
                <Link to="/orders" className="button">
                  查看我的订单
                  <ArrowRight size={16} />
                </Link>
              }
            />
          </div>
        )}
      </ResourceState>
    </>
  );
}
const nextSteps: Record<string, [string, string]> = {
  PENDING_REVIEW: [
    "申请已受理，等待员工初审",
    "初审同意后再寄回商品；如需补充资料，我们会在这里说明。",
  ],
  AWAITING_RETURN: [
    "请按工作人员说明寄回商品",
    "寄出后填写承运公司与运单号，以便核对收货。",
  ],
  INSPECTING: [
    "商品已收货，正在验收",
    "员工将核对设备与申请信息。验收完成后会更新处理结果。",
  ],
  WAITING_STOCK: [
    "验收已通过，等待同款补货",
    "您的申请会继续保留，无需重新申请。补货后由员工继续安排。",
  ],
  READY_TO_FULFILL: ["验收已通过，准备安排履约", "请留意后续退款或出库结果。"],
  FULFILLING: [
    "正在处理履约",
    "遇到外部结果未知时，会先核对再推进。请勿重复提交申请。",
  ],
  MANUAL_REVIEW: [
    "这次申请需要人工继续核查",
    "员工会核对异常原因与已收到的实物，未完成实物处置前不会关闭。",
  ],
  COMPLETED: [
    "本次办理已完成",
    "演示环境：退款为模拟退款成功，换货为模拟换货已发出。请查看下方履约记录。",
  ],
  REJECTED: [
    "初审未同意寄回",
    "请查看处理原因；如有其他事实或疑问，请联系人工。",
  ],
  CANCELLED: ["申请已取消", "未继续安排履约，历史记录仍为您保留。"],
  CLOSED_UNFULFILLED: [
    "实物处置已登记，申请已关闭",
    "本次未安排退款或换货，处置依据保留在办理记录中。",
  ],
};
export function CasePage({ staff = false }: { staff?: boolean }) {
  const { id } = useParams(),
    resource = useResource<CaseRecord>(`${staff ? "/staff" : ""}/cases/${id}`),
    action = useAction();
  const [carrier, setCarrier] = useState(""),
    [tracking, setTracking] = useState(""),
    [supplement, setSupplement] = useState(""),
    [evidence, setEvidence] = useState<string[]>([]),
    [cancel, setCancel] = useState(false);
  const item = resource.data;
  const changed = async (path: string, data: any, message?: string) =>
    action.run(async () => {
      await api(`${staff ? "/staff" : ""}/cases/${id}/${path}`, "POST", {
        expected_version: item!.version,
        ...data,
      });
      resource.reload();
    }, message);
  return (
    <>
      <Breadcrumb
        items={[
          {
            label: staff ? "售后工作台" : "售后申请",
            to: staff ? "/staff/cases" : "/cases",
          },
          { label: item?.number || "申请详情" },
        ]}
      />
      <PageTitle
        title={item ? typeName(item.type) : "申请详情"}
        description={item?.number}
        action={
          <button className="button secondary" onClick={resource.reload}>
            <RefreshCw size={15} />
            刷新进展
          </button>
        }
      />
      <ResourceState {...resource}>
        {item && (
          <>
            <div className="case-progress-banner">
              <span className="round-icon">
                <Clock3 size={23} />
              </span>
              <div>
                <Status value={item.status} />
                <h2>
                  {nextSteps[item.status]?.[0] || statusName(item.status)}
                </h2>
                <p>
                  {item.status === "COMPLETED" &&
                  !item.operations?.some(
                    (operation) => operation.execution_mode === "demo",
                  )
                    ? "履约结果已确认登记，请查看下方对应记录。换货发出不代表已经签收。"
                    : nextSteps[item.status]?.[1]}
                </p>
                {item.pending_reason && (
                  <p className="pending-reason">
                    处理说明：{reasonName(item.pending_reason)}
                  </p>
                )}
              </div>
            </div>
            <Alert>{action.error}</Alert>
            <Alert tone="success">{action.success}</Alert>
            <div className="detail-grid">
              <div>
                {staff ? (
                  <StaffActions
                    item={item}
                    changed={changed}
                    busy={action.busy}
                  />
                ) : (
                  <>
                    {item.status === "AWAITING_RETURN" &&
                      !item.return_shipment && (
                        <section className="panel">
                          <h2>填写寄回物流</h2>
                          <p className="muted">
                            请按员工提供的寄回说明邮寄，并保存好寄件凭证。
                          </p>
                          <form
                            onSubmit={(event) => {
                              event.preventDefault();
                              changed(
                                "return-shipment",
                                { carrier, tracking_number: tracking },
                                "寄回物流已保存",
                              );
                            }}
                          >
                            <div className="field-grid">
                              <label>
                                承运公司
                                <input
                                  required
                                  maxLength={80}
                                  value={carrier}
                                  onChange={(event) =>
                                    setCarrier(event.target.value)
                                  }
                                  placeholder="如：顺丰速运"
                                />
                              </label>
                              <label>
                                运单号
                                <input
                                  required
                                  maxLength={128}
                                  value={tracking}
                                  onChange={(event) =>
                                    setTracking(event.target.value)
                                  }
                                />
                              </label>
                            </div>
                            <Submit busy={action.busy}>保存寄回物流</Submit>
                          </form>
                        </section>
                      )}
                    {[
                      "PENDING_REVIEW",
                      "AWAITING_RETURN",
                      "MANUAL_REVIEW",
                    ].includes(item.status) && (
                      <section className="panel">
                        <h2>补充说明与材料</h2>
                        <form
                          onSubmit={(event) => {
                            event.preventDefault();
                            changed(
                              "supplements",
                              {
                                information: {
                                  description: supplement,
                                  evidence_ids: evidence,
                                },
                              },
                              "补充材料已保存",
                            );
                          }}
                        >
                          <label>
                            补充说明
                            <textarea
                              rows={3}
                              required
                              value={supplement}
                              onChange={(event) =>
                                setSupplement(event.target.value)
                              }
                              maxLength={3000}
                            />
                          </label>
                          <Attachments
                            value={evidence}
                            onChange={setEvidence}
                          />
                          <Submit busy={action.busy}>提交补充材料</Submit>
                        </form>
                      </section>
                    )}
                  </>
                )}
                <section className="panel">
                  <h2>办理时间线</h2>
                  <div className="timeline">
                    {item.events?.map((event) => (
                      <div className="timeline-item" key={event.id}>
                        <i />
                        <div className="row between">
                          <strong>
                            {statusName(event.to_status || item.status)}
                          </strong>
                          <time>{date(event.created_at)}</time>
                        </div>
                        <p>{eventLabel(event.event)}</p>
                        {event.detail?.reason && <p>{event.detail.reason}</p>}
                        {event.detail?.information?.description && (
                          <p>{event.detail.information.description}</p>
                        )}
                        <EvidenceLinks
                          ids={event.detail?.information?.evidence_ids}
                        />
                      </div>
                    ))}
                  </div>
                </section>
                {item.operations?.length > 0 && (
                  <section className="panel">
                    <h2>履约记录</h2>
                    {item.operations.map((operation) => (
                      <div className="operation-summary" key={operation.id}>
                        <div className="row between">
                          <h3>{typeName(operation.kind)}</h3>
                          <Status value={operation.status} />
                        </div>
                        <p>
                          {operation.execution_mode === "demo"
                            ? "模拟履约"
                            : operation.execution_mode === "manual"
                              ? "人工登记履约"
                              : "外部系统履约"}
                          {operation.status === "SUCCEEDED" &&
                            ` · ${operation.kind === "refund" ? (operation.execution_mode === "demo" ? "模拟退款成功" : "退款已确认成功") : operation.execution_mode === "demo" ? "模拟换货已发出" : "换货已发出"}`}
                        </p>
                        {operation.result?.refund_reference && (
                          <p className="muted">
                            回执编号 {operation.result.refund_reference}
                          </p>
                        )}
                        {operation.result?.tracking_number && (
                          <p className="muted">
                            出库运单 {operation.result.tracking_number}
                          </p>
                        )}
                        {staff && (
                          <Link to="/staff/operations" className="text-button">
                            前往履约核对
                            <ArrowRight size={14} />
                          </Link>
                        )}
                      </div>
                    ))}
                  </section>
                )}
              </div>
              <aside>
                {item.conversation_id && (
                  <section className="panel">
                    <h2>申请来源会话</h2>
                    <p className="muted">
                      这次申请由已验证的客户聊天生成，确认记录保留在本申请中。
                    </p>
                    <Link
                      className="text-button"
                      to={
                        staff
                          ? `/staff/conversations/${item.conversation_id}`
                          : `/conversations/${item.conversation_id}`
                      }
                    >
                      <MessageCircle size={16} />
                      查看来源会话 #{item.conversation_id}
                      <ArrowRight size={14} />
                    </Link>
                  </section>
                )}
                <section className="panel">
                  <h2>已确认的申请</h2>
                  <dl className="definition">
                    <div>
                      <dt>办理类型</dt>
                      <dd>{typeName(item.type)}</dd>
                    </div>
                    <div>
                      <dt>设备记录</dt>
                      <dd>{shortId(item.order_unit_id)}</dd>
                    </div>
                    <div>
                      <dt>
                        {item.type === "return" ? "退款金额" : "换货范围"}
                      </dt>
                      <dd>
                        {item.type === "return"
                          ? money(item.confirmed_payload.amount_cents)
                          : "同一 SKU"}
                      </dd>
                    </div>
                    <div>
                      <dt>受理时间</dt>
                      <dd>{date(item.accepted_at)}</dd>
                    </div>
                    <div>
                      <dt>申请原因</dt>
                      <dd>{item.confirmed_payload.facts?.reason}</dd>
                    </div>
                    <div>
                      <dt>政策版本</dt>
                      <dd>{item.policy_snapshot?.version}</dd>
                    </div>
                  </dl>
                  <EvidenceLinks
                    ids={item.confirmed_payload.facts?.evidence_ids}
                  />
                  {item.shipping_address && (
                    <div className="address-block">
                      <h3>已确认收件地址</h3>
                      <p>
                        {item.shipping_address.name} ·{" "}
                        {item.shipping_address.phone}
                        <br />
                        {item.shipping_address.region}{" "}
                        {item.shipping_address.detail}
                      </p>
                    </div>
                  )}
                  {item.return_shipment && (
                    <div className="address-block">
                      <h3>寄回物流</h3>
                      <p>
                        {item.return_shipment.carrier}
                        <br />
                        {item.return_shipment.tracking_number}
                      </p>
                    </div>
                  )}
                  {!staff &&
                    (item.status === "PENDING_REVIEW" ||
                      (item.status === "AWAITING_RETURN" &&
                        !item.return_shipment)) && (
                      <button
                        className="text-button danger"
                        onClick={() => setCancel(true)}
                      >
                        取消这次申请
                      </button>
                    )}
                </section>
                <section className="panel">
                  <PolicyView policy={item.policy_snapshot} />
                </section>
              </aside>
            </div>
          </>
        )}
      </ResourceState>
      {cancel && (
        <Confirm
          title="取消本次申请？"
          busy={action.busy}
          error={action.error}
          onClose={() => setCancel(false)}
          onConfirm={async () => {
            await changed("cancel", {}, "申请已取消");
            setCancel(false);
          }}
        >
          <p>
            取消后不会继续处理本申请，已有记录会保留。当前是否允许取消由后端重新核验。
          </p>
        </Confirm>
      )}
    </>
  );
}
export function CustomerConversationPage() {
  const { id } = useParams();
  const resource = useResource<{
    id: number;
    history_scope: string;
    linked_cases: CaseRecord[];
    messages: {
      id: number;
      content: string;
      created_at: string | number;
      message_type: number | string;
    }[];
  }>(`/conversations/${id}`);
  return (
    <>
      <Breadcrumb
        items={[
          { label: "售后申请", to: "/cases" },
          { label: `来源会话 #${id}` },
        ]}
      />
      <PageTitle
        title={`来源会话 #${id}`}
        description="这里显示 Chatwoot 返回的最近一页公开聊天。更早消息可能不在此页，办理进度以售后申请为准。"
        action={
          <button className="button secondary" onClick={resource.reload}>
            <RefreshCw size={15} />
            刷新消息
          </button>
        }
      />
      <ResourceState {...resource}>
        {resource.data && (
          <div className="detail-grid">
            <section className="panel">
              <h2>最近公开消息</h2>
              {resource.data.messages.length ? (
                resource.data.messages.map((message) => (
                  <div className="address-block" key={message.id}>
                    <p className="caption">
                      {message.message_type === 0 ||
                      message.message_type === "incoming"
                        ? "您的消息"
                        : "服务回复"}{" "}
                      ·{" "}
                      {date(
                        typeof message.created_at === "number"
                          ? new Date(message.created_at * 1000).toISOString()
                          : message.created_at,
                      )}
                    </p>
                    <p
                      style={{
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                      }}
                    >
                      {message.content}
                    </p>
                  </div>
                ))
              ) : (
                <Empty title="最近一页暂无公开消息" />
              )}
              <button
                className="text-button"
                disabled={!window.$chatwoot}
                onClick={() => window.$chatwoot?.toggle("open")}
              >
                <MessageCircle size={16} />
                打开当前客户聊天
              </button>
            </section>
            <aside>
              <section className="panel">
                <h2>关联售后申请</h2>
                {resource.data.linked_cases.map((item) => (
                  <p key={item.id}>
                    <Link className="text-button" to={`/cases/${item.id}`}>
                      {item.number}
                      <ArrowRight size={14} />
                    </Link>
                  </p>
                ))}
              </section>
            </aside>
          </div>
        )}
      </ResourceState>
    </>
  );
}
function eventLabel(value: string) {
  const labels: Record<string, string> = {
    customer_confirmed: "客户核对并确认申请",
    staff_review_approve_return: "员工初审：同意寄回",
    staff_review_reject: "员工初审：不予受理",
    staff_review_request_info: "员工要求补充信息",
    return_shipment_recorded: "客户已登记寄回物流",
    customer_supplement: "客户补充说明或材料",
    customer_cancelled: "客户取消申请",
    physical_received: "员工已登记收货",
    inspection_passed: "员工确认验收通过",
    inspection_failed: "验收未通过，交由人工核查",
    physical_disposition_completed: "实物处置已完成",
    operation_succeeded: "履约结果已确认成功",
    operation_unknown: "履约结果未知，等待核对",
    operation_failed: "履约明确失败，等待人工处理",
  };
  return labels[value] || "办理状态已更新，记录已保存";
}
function StaffActions({
  item,
  changed,
  busy,
}: {
  item: CaseRecord;
  changed: (path: string, data: any, message?: string) => Promise<any>;
  busy: boolean;
}) {
  const [reason, setReason] = useState(""),
    [decision, setDecision] = useState("approve_return"),
    [serial, setSerial] = useState(""),
    [passed, setPassed] = useState(true),
    [unused, setUnused] = useState(false),
    [accessories, setAccessories] = useState(false),
    [quality, setQuality] = useState(false),
    [outcome, setOutcome] = useState("returned_to_customer"),
    [disposition, setDisposition] = useState("");
  return (
    <>
      {item.status === "PENDING_REVIEW" && (
        <section className="panel">
          <h2>员工初审</h2>
          <p className="muted">
            初审仅决定是否同意寄回。收货验收后，才可安排履约。
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              changed("review", { decision, reason }, "初审意见已保存");
            }}
          >
            <label>
              初审意见
              <select
                value={decision}
                onChange={(event) => setDecision(event.target.value)}
              >
                <option value="approve_return">同意寄回</option>
                <option value="request_info">请客户补充信息</option>
                <option value="reject">不予受理，并说明原因</option>
              </select>
            </label>
            <label>
              说明
              <textarea
                required
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
              />
            </label>
            <Submit busy={busy}>保存初审意见</Submit>
          </form>
        </section>
      )}
      {item.status === "AWAITING_RETURN" && (
        <section className="panel">
          <h2>登记收货</h2>
          {!item.return_shipment ? (
            <p className="muted">
              客户尚未填写寄回物流，填写后可继续登记收货。
            </p>
          ) : (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                changed(
                  "receive",
                  { received_serial: serial },
                  "收货记录已保存",
                );
              }}
            >
              <label>
                实际收到的设备序列号
                <input
                  required
                  value={serial}
                  onChange={(event) => setSerial(event.target.value)}
                />
              </label>
              <Submit busy={busy}>确认收到商品</Submit>
            </form>
          )}
        </section>
      )}
      {item.status === "INSPECTING" && (
        <section className="panel">
          <h2>核验设备并安排后续</h2>
          <p className="muted">
            收到序列号：{item.returned_item?.received_serial}
            。验收不通过仍需负责实物处置，不能直接关单。
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              changed(
                "inspect",
                {
                  passed,
                  verification: {
                    reason,
                    unused,
                    accessories_complete: accessories,
                    quality_issue: quality,
                  },
                },
                "验收结果已保存",
              );
            }}
          >
            <label>
              验收结论
              <select
                value={passed ? "pass" : "fail"}
                onChange={(event) => setPassed(event.target.value === "pass")}
              >
                <option value="pass">验收通过</option>
                <option value="fail">验收不通过，转人工处理</option>
              </select>
            </label>
            <label>
              验收说明
              <textarea
                required
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
              />
            </label>
            <div className="checklist">
              <Checklist
                checked={unused}
                onChange={setUnused}
                label="确认商品未使用"
              />
              <Checklist
                checked={accessories}
                onChange={setAccessories}
                label="确认配件完整"
              />
              <Checklist
                checked={quality}
                onChange={setQuality}
                label="确认存在质量问题"
              />
            </div>
            <Submit busy={busy}>
              {passed ? "确认验收并安排履约" : "记录验收不通过"}
            </Submit>
          </form>
        </section>
      )}
      {item.status === "WAITING_STOCK" && (
        <section className="panel">
          <h2>等待同款库存</h2>
          <p className="muted">
            补货后继续当前申请；可用库存会由后端重新原子核验。
          </p>
          <button
            className="button"
            disabled={busy}
            onClick={() => changed("resume-stock", {}, "已尝试继续换货")}
          >
            库存核验并继续
            <ArrowRight size={16} />
          </button>
          <Link to="/staff/inventory" className="text-button">
            查看库存
          </Link>
        </section>
      )}
      {item.status === "MANUAL_REVIEW" &&
        item.returned_item?.custody_status === "HELD" &&
        (item.operations || []).every(
          (operation) => operation.status === "FAILED",
        ) && (
          <section className="panel">
            <h2>实物处置登记</h2>
            <p className="muted">请在实际完成退还或客户授权处置后登记证据。</p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                changed(
                  "disposition",
                  { outcome, evidence: { description: disposition } },
                  "实物处置已登记",
                );
              }}
            >
              <label>
                处置方式
                <select
                  value={outcome}
                  onChange={(event) => setOutcome(event.target.value)}
                >
                  <option value="returned_to_customer">已退还客户</option>
                  <option value="customer_authorized_disposal">
                    已按客户授权处置
                  </option>
                </select>
              </label>
              <label>
                物流凭证或授权依据
                <textarea
                  required
                  value={disposition}
                  onChange={(event) => setDisposition(event.target.value)}
                  rows={3}
                />
              </label>
              <Submit busy={busy}>确认处置并关闭</Submit>
            </form>
          </section>
        )}
    </>
  );
}
