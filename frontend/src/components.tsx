import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import {
  ArrowRight,
  Check,
  ChevronRight,
  FileText,
  LoaderCircle,
  Package,
  RefreshCw,
  Upload,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api, statusName, type Address } from "./api";

export function useResource<T>(path: string) {
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api<T>(path)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((error) => {
        if (active) setError(error.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [path, revision]);
  return {
    data,
    error,
    loading,
    reload: () => setRevision((value) => value + 1),
    setData,
  };
}
export function useAction() {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [success, setSuccess] = useState("");
  async function run(action: () => Promise<any>, message = "操作已保存") {
    if (busy) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const result = await action();
      setSuccess(message);
      return result;
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作未完成");
    } finally {
      setBusy(false);
    }
  }
  return {
    busy,
    error,
    success,
    run,
    clear: () => {
      setError("");
      setSuccess("");
    },
  };
}
export function Alert({
  children,
  tone = "error",
}: {
  children?: ReactNode;
  tone?: string;
}) {
  return children ? (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`alert ${tone}`}
    >
      {children}
    </div>
  ) : null;
}
export function Status({ value }: { value: string }) {
  return (
    <span
      className={`status ${["COMPLETED", "SUCCEEDED", "BOT_ACTIVE"].includes(value) ? "positive" : ["UNKNOWN", "MANUAL_REVIEW", "HANDOFF_PENDING", "WAITING_STOCK"].includes(value) ? "warning" : ["FAILED", "REJECTED"].includes(value) ? "negative" : ""}`}
    >
      <i />
      {statusName(value)}
    </span>
  );
}
export function Loading() {
  return (
    <div className="empty">
      <LoaderCircle className="spin" size={28} />
      <p>正在读取记录…</p>
    </div>
  );
}
export function Empty({
  title = "这里还没有记录",
  description = "有新的记录时，会在这里显示。",
  action,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <Package size={36} strokeWidth={1.4} />
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function ResourceState({
  loading,
  error,
  reload,
  children,
}: {
  loading: boolean;
  error: string;
  reload: () => void;
  children: ReactNode;
}) {
  return loading ? (
    <Loading />
  ) : error ? (
    <div className="panel">
      <Alert>{error}</Alert>
      <button className="button secondary" onClick={reload}>
        <RefreshCw size={16} />
        重新读取
      </button>
    </div>
  ) : (
    <>{children}</>
  );
}
export function PageTitle({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {action}
    </div>
  );
}
export function Breadcrumb({
  items,
}: {
  items: { label: string; to?: string }[];
}) {
  return (
    <nav className="breadcrumb" aria-label="面包屑">
      {items.map((item, index) => (
        <span key={index}>
          {index > 0 && <ChevronRight size={13} />}
          {item.to ? <Link to={item.to}>{item.label}</Link> : item.label}
        </span>
      ))}
    </nav>
  );
}
export function Submit({
  busy,
  disabled,
  children,
  className = "",
}: {
  busy?: boolean;
  disabled?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <button
      disabled={busy || disabled}
      className={`button ${className}`}
      type="submit"
    >
      {busy ? <LoaderCircle size={16} className="spin" /> : null}
      {children}
    </button>
  );
}
export function Confirm({
  title,
  children,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  title: string;
  children: ReactNode;
  busy?: boolean;
  error?: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="modal"
      >
        <button
          className="icon-button modal-close"
          onClick={onClose}
          aria-label="关闭确认框"
        >
          <X size={20} />
        </button>
        <h2>{title}</h2>
        <div className="modal-content">
          {children}
          <Alert>{error}</Alert>
        </div>
        <div className="actions">
          <button
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            再核对一下
          </button>
          <button className="button" disabled={busy} onClick={onConfirm}>
            {busy ? "正在提交…" : "确认提交"}
            <ArrowRight size={16} />
          </button>
        </div>
      </section>
    </div>
  );
}
export function AddressFields({
  value,
  onChange,
}: {
  value: Address;
  onChange: (value: Address) => void;
}) {
  return (
    <div className="field-grid">
      {(
        [
          ["name", "收件人"],
          ["phone", "联系电话"],
          ["region", "省市区"],
          ["detail", "详细地址"],
        ] as const
      ).map(([key, label]) => (
        <label className={key === "detail" ? "span-all" : ""} key={key}>
          {label}
          <input
            required
            value={value[key] || ""}
            maxLength={
              key === "detail"
                ? 300
                : key === "region"
                  ? 160
                  : key === "phone"
                    ? 40
                    : 80
            }
            onChange={(event) =>
              onChange({ ...value, [key]: event.target.value })
            }
            autoComplete={key === "phone" ? "tel" : "off"}
          />
        </label>
      ))}
    </div>
  );
}
export function PolicyView({ policy }: { policy: any }) {
  return !policy ? (
    <Alert tone="warning">政策信息缺失，请联系人工核验。</Alert>
  ) : (
    <div className="policy">
      <div className="row">
        <FileText size={18} />
        <h3>适用政策</h3>
        <span className="tag">{policy.version}</span>
      </div>
      {policy.sections?.map((section: any) => (
        <p key={section.id}>
          <span className="muted">{section.id}</span> {section.text}
        </p>
      ))}
    </div>
  );
}
export function Attachments({
  value,
  onChange,
}: {
  value: string[];
  onChange: (ids: string[]) => void;
}) {
  const action = useAction();
  async function upload(event: FormEvent<HTMLInputElement>) {
    const files = Array.from(event.currentTarget.files || []);
    const uploaded = [...value];
    await action.run(async () => {
      if (value.length + files.length > 4)
        throw new Error("最多上传四张材料图片。");
      for (const file of files) {
        if (file.size > 5 * 1024 * 1024)
          throw new Error("单张图片不能超过 5 MB。");
        const form = new FormData();
        form.append("file", file);
        const result = await api<{ id: string }>("/attachments", "POST", form);
        uploaded.push(result.id);
        onChange([...uploaded]);
      }
    });
  }
  return (
    <div>
      <label className="upload">
        <Upload size={18} />
        <span>
          {action.busy ? "正在保存材料…" : "上传故障照片或凭证"}
          <small>
            最多四张 PNG / JPEG / WebP，每张不超过 5
            MB；仅本人及授权员工可查看。
          </small>
        </span>
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp"
          multiple
          disabled={action.busy}
          onChange={upload}
        />
      </label>
      <Alert>{action.error}</Alert>
      <div className="attachment-list">
        {value.map((id, index) => (
          <div key={id}>
            <a
              href={`/api/v1/attachments/${id}`}
              target="_blank"
              rel="noreferrer"
            >
              <FileText size={14} />
              材料 {index + 1}
            </a>
            <button
              type="button"
              className="icon-button"
              onClick={() => onChange(value.filter((item) => item !== id))}
              aria-label={`移除材料${index + 1}`}
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
export function EvidenceLinks({ ids }: { ids?: string[] }) {
  return ids?.length ? (
    <div className="attachment-list">
      {ids.map((id, index) => (
        <a
          key={id}
          href={`/api/v1/attachments/${id}`}
          target="_blank"
          rel="noreferrer"
        >
          <FileText size={14} />
          查看材料 {index + 1}
        </a>
      ))}
    </div>
  ) : null;
}
export function Checklist({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="check">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{checked && <Check size={13} />}</span>
      {label}
    </label>
  );
}
