export type Role = "customer" | "support" | "admin";
export interface User {
  username: string;
  role: Role;
  csrf_token: string;
}
let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, message: string, data: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}
const messages: Record<string, string> = {
  "control revision changed":
    "会话状态已变化，本次操作未生效。请关闭确认框，重新核验状态后，再次明确确认。",
  "unconfirmed outbound requires manual reconciliation before return":
    "仍有机器人消息结果待确认。请先在下方核对消息，记录依据后再明确交回。",
  RESOURCE_NOT_ACCESSIBLE: "该记录不存在或您无权访问。",
  VERSION_CONFLICT: "记录已更新，请刷新后重新核对。",
  PREVIEW_CHANGED_RECONFIRM: "预览内容已变化，请重新核对后确认。",
  PROPOSAL_EXPIRED: "申请预览已过期，请从订单重新发起。",
  DEVICE_ALREADY_HAS_CASE: "该设备已有售后记录，请前往查看。",
  ELIGIBILITY_REQUIRES_ATTENTION:
    "申请条件需要补充或人工核验，请查看政策判断。",
  CASE_STATE_CONFLICT: "当前状态已变化，此操作不可执行。",
  CANCELLATION_REQUIRES_STAFF: "当前阶段需要员工协助取消。",
  IDEMPOTENCY_KEY_PAYLOAD_MISMATCH: "操作内容已变化，请刷新页面后重试。",
  POLICY_PACKAGE_INVALID: "政策包内容无效。",
};
export async function api<T = any>(
  path: string,
  method = "GET",
  data?: unknown,
  idempotencyKey?: string,
): Promise<T> {
  const write = method !== "GET";
  const form = data instanceof FormData;
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, {
      method,
      credentials: "same-origin",
      headers: {
        ...(!form ? { "Content-Type": "application/json" } : {}),
        ...(write
          ? {
              "X-CSRF-Token": csrf,
              "Idempotency-Key": idempotencyKey || crypto.randomUUID(),
            }
          : {}),
      },
      body: data === undefined ? undefined : form ? data : JSON.stringify(data),
    });
  } catch {
    throw new Error("网络连接中断。若已提交操作，请先刷新记录核实结果。");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail ?? body.error ?? "请求未完成";
    const code =
      typeof detail === "string" ? detail : (detail.code ?? body.code ?? "");
    const fallback =
      response.status === 401
        ? "登录已失效，请重新登录。"
        : response.status === 403
          ? "您没有执行此操作的权限。"
          : response.status === 404
            ? "该记录不存在或您无权访问。"
            : response.status === 503
              ? "服务暂时不可用，请稍后重试。"
              : typeof detail === "string" && /[\u3400-\u9fff]/.test(detail)
                ? detail
                : "请检查填写内容后重试。";
    throw new ApiError(response.status, messages[code] || fallback, body);
  }
  return body;
}
export const money = (cents: number = 0) =>
  new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(
    cents / 100,
  );
export const date = (value?: string | null) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        hour12: false,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
export const shortId = (value: string = "") => value.slice(0, 8).toUpperCase();
export const typeName = (value: string) =>
  value === "return"
    ? "退货退款"
    : value === "exchange"
      ? "同款换货"
      : value === "refund"
        ? "退款"
        : value === "shipment"
          ? "换货发出"
          : value;
export const statuses: Record<string, string> = {
  PENDING_REVIEW: "待员工初审",
  AWAITING_RETURN: "待寄回商品",
  INSPECTING: "收货验收中",
  WAITING_STOCK: "等待补货",
  READY_TO_FULFILL: "待安排履约",
  FULFILLING: "履约处理中",
  MANUAL_REVIEW: "待人工核查",
  COMPLETED: "办理完成",
  REJECTED: "申请未受理",
  CANCELLED: "已取消",
  CLOSED_UNFULFILLED: "已完成实物处置",
  PENDING: "待处理",
  RUNNING: "执行中",
  SUCCEEDED: "成功",
  FAILED: "明确失败",
  UNKNOWN: "结果待核对",
  AWAITING_MANUAL: "待人工办理登记",
  BOT_ACTIVE: "机器人服务中",
  HUMAN_ACTIVE: "人工接待中",
  HANDOFF_PENDING: "正在转人工",
};
export const statusName = (value: string) => statuses[value] || value;
export const reasonName = (value: string) =>
  (
    ({
      APPLICATION_WINDOW_EXPIRED:
        "当前自动办理时间窗已到期，可联系人工核查权益。",
      ITEM_USED: "退货条件要求商品未使用。",
      ACCESSORIES_INCOMPLETE: "退货条件要求配件完整。",
      UNUSED_STATUS_REQUIRED: "请确认商品是否未使用。",
      ACCESSORIES_STATUS_REQUIRED: "请确认配件完整情况。",
      QUALITY_ISSUE_REQUIRED: "请补充质量问题说明。",
      QUALITY_ISSUE_NOT_CONFIRMED: "质量问题尚未确认，需要人工核查。",
      REASON_REQUIRED: "请填写申请原因。",
      WAITING_SAME_SKU_STOCK: "等待同款新品补货，申请会继续保留。",
      UNSUPPORTED_ORDER_REQUIRES_HUMAN: "这笔订单需要人工核查办理范围。",
      REPLACEMENT_DEVICE_REQUIRES_HUMAN:
        "换出设备再次申请售后，请联系人工继续办理。",
      POLICY_MISSING_OR_CONTRADICTORY: "政策信息存在缺失或冲突，需要人工核实。",
      POLICY_RULE_SOURCE_MISSING: "政策条款依据不完整，需要人工核实。",
      ZERO_VALUE_REQUIRES_HUMAN: "零金额设备请联系人工办理。",
    }) as Record<string, string>
  )[value] || (/^[A-Z_]+$/.test(value) ? "需要人工核查具体办理条件。" : value);
export interface Address {
  name: string;
  phone: string;
  region: string;
  detail: string;
}
export interface Policy {
  id: string;
  version: string;
  published_at: string;
  sections: { id: string; text: string }[];
  scope: any;
  rules: any;
  rule_source_map: any;
}
export interface Unit {
  id: string;
  serial_number: string;
  allocated_paid_cents: number;
  policy: Policy | null;
  active_case_id: string | null;
}
export interface Order {
  id: string;
  source: string;
  external_id: string;
  delivered_at: string;
  paid_at: string;
  shipping_address: Address;
  items: {
    product_id: string;
    sku: string;
    name: string;
    quantity: number;
    net_goods_paid_cents: number;
    units: Unit[];
  }[];
}
export interface Proposal {
  id: string;
  order_unit_id: string;
  type: string;
  facts: any;
  amount_cents: number;
  version: number;
  summary_hash: string;
  expires_at: string;
  shipping_address: Address | null;
  policy: Policy;
  confirmed_case_id: string | null;
}
export interface Case {
  id: string;
  conversation_id: number | null;
  number: string;
  order_unit_id: string;
  type: string;
  status: string;
  version: number;
  accepted_at: string;
  pending_reason: string | null;
  policy_snapshot: Policy;
  confirmed_payload: any;
  shipping_address: Address | null;
  return_shipment: any;
  returned_item: any;
  events: any[];
  operations: Operation[];
}
export interface Operation {
  id: string;
  case_id: string;
  kind: string;
  status: string;
  version: number;
  execution_mode: string;
  request_hash: string;
  request_snapshot: any;
  result: any;
  provider_operation_id: string | null;
  attempts: number;
}
