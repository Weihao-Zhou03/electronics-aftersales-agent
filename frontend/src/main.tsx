import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Link,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Box,
  Boxes,
  ClipboardList,
  Headphones,
  HelpCircle,
  LayoutDashboard,
  LogOut,
  MessageCircle,
  PackageCheck,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { api, setCsrf, type User } from "./api";
import { Alert, Loading, Submit, useAction } from "./components";
import {
  CasesPage,
  CasePage,
  CustomerConversationPage,
  OrderPage,
  OrdersPage,
  ProposalPage,
} from "./customer";
import {
  ConversationPage,
  InventoryPage,
  OperationsPage,
  PoliciesPage,
  StaffCasesPage,
  SystemPage,
} from "./staff";
import "./style.css";

declare global {
  interface Window {
    $chatwoot?: any;
    chatwootSDK?: any;
    chatwootSettings?: any;
  }
}
type Auth = {
  user: User | null;
  loading: boolean;
  mode: "demo" | "pilot" | null;
  fulfillmentMode: string | null;
  setUser: (user: User | null) => void;
};
const AuthContext = createContext<Auth>({
  user: null,
  loading: true,
  mode: null,
  fulfillmentMode: null,
  setUser: () => {},
});
export const useAuth = () => useContext(AuthContext);
export function resetChat() {
  if (window.$chatwoot) {
    window.$chatwoot.toggle("close");
    window.$chatwoot.reset();
  }
}
function AuthProvider({ children }: { children: ReactNode }) {
  const [user, updateUser] = useState<User | null>(null),
    [loading, setLoading] = useState(true),
    [environment, setEnvironment] = useState<{
      mode: "demo" | "pilot" | null;
      fulfillmentMode: string | null;
    }>({ mode: null, fulfillmentMode: null });
  const setUser = (value: User | null) => {
    setCsrf(value?.csrf_token || "");
    updateUser(value);
  };
  useEffect(() => {
    Promise.all([
      api<User>("/auth/me")
        .then(setUser)
        .catch(() => {}),
      api<{ app_mode: string; fulfillment_mode: string }>("/app-config")
        .then((value) =>
          setEnvironment({
            mode: ["demo", "pilot"].includes(value.app_mode)
              ? (value.app_mode as "demo" | "pilot")
              : null,
            fulfillmentMode: value.fulfillment_mode,
          }),
        )
        .catch(() => {}),
    ]).finally(() => setLoading(false));
  }, []);
  return (
    <AuthContext value={{ user, loading, setUser, ...environment }}>
      {children}
    </AuthContext>
  );
}
function EnvironmentBanner() {
  const { mode, fulfillmentMode } = useAuth();
  return (
    <div className="demo-banner">
      <span className="demo-dot" />
      {mode === "demo" ? (
        <>
          演示环境 · 模拟订单与履约
          <span className="banner-note">所有退款与物流回执均为模拟记录</span>
        </>
      ) : mode === "pilot" ? (
        <>
          试用环境 ·{" "}
          {fulfillmentMode === "manual"
            ? "人工登记履约"
            : fulfillmentMode === "external"
              ? "外部系统履约"
              : "履约方式待核验"}
        </>
      ) : (
        "电子产品售后服务"
      )}
    </div>
  );
}
function Guard({
  staff = false,
  admin = false,
}: {
  staff?: boolean;
  admin?: boolean;
}) {
  const { user, loading } = useAuth(),
    location = useLocation();
  if (loading) return <Loading />;
  if (!user)
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  if ((staff && user.role === "customer") || (admin && user.role !== "admin"))
    return (
      <div className="panel">
        <Alert>此页面仅向授权员工开放。</Alert>
        <Link to="/">返回首页</Link>
      </div>
    );
  return <Outlet />;
}
function ChatWidget() {
  const { user } = useAuth();
  const [status, setStatus] = useState("");
  useEffect(() => {
    document.documentElement.dataset.role = user?.role || "anonymous";
    if (!user || user.role !== "customer") return;
    let active = true;
    let identity: any;
    const bound = new Set<number>();
    const identify = () => {
      if (active && identity && window.$chatwoot)
        window.$chatwoot.setUser(identity.identifier, {
          identifier_hash: identity.identifier_hash,
          name: user.username,
        });
    };
    const onMessage = (event: Event) => {
      const id = (event as CustomEvent).detail?.conversation_id;
      if (!active || !Number.isInteger(id) || id < 1 || bound.has(id)) return;
      // The SDK event is only a hint. The server independently GETs Chatwoot,
      // checks hmac_verified, identifier and full scope before accepting it.
      api("/chat/bind", "POST", { conversation_id: id })
        .then(() => {
          if (active) {
            bound.add(id);
            setStatus("聊天身份已验证");
          }
        })
        .catch(() => {
          if (active) setStatus("聊天身份待核验；可继续使用订单表单");
        });
    };
    window.addEventListener("chatwoot:ready", identify);
    window.addEventListener("chatwoot:on-message", onMessage);
    api("/chat/identity")
      .then((value) => {
        if (!active) return;
        identity = value;
        if (window.$chatwoot) identify();
        else {
          window.chatwootSettings = {
            locale: "zh_CN",
            position: "right",
            type: "standard",
          };
          const script = document.createElement("script");
          script.src = identity.base_url + "/packs/js/sdk.js";
          script.async = true;
          script.onload = () => {
            if (active)
              window.chatwootSDK.run({
                websiteToken: identity.website_token,
                baseUrl: identity.base_url,
              });
          };
          script.onerror = () => {
            if (active) setStatus("聊天暂时不可用；订单表单仍可办理");
          };
          document.head.appendChild(script);
        }
      })
      .catch(() => {
        if (active) setStatus("聊天暂时不可用；订单表单仍可办理");
      });
    return () => {
      active = false;
      window.removeEventListener("chatwoot:ready", identify);
      window.removeEventListener("chatwoot:on-message", onMessage);
    };
  }, [user?.username, user?.role]);
  return user?.role === "customer" && status ? (
    <div className="chat-status">
      <ShieldCheck size={12} />
      {status}
    </div>
  ) : null;
}
function Layout() {
  const { user, setUser } = useAuth(),
    navigate = useNavigate(),
    action = useAction();
  const staff = user?.role !== "customer";
  async function logout() {
    await action.run(async () => {
      await api("/auth/logout", "POST", {});
      resetChat();
      setUser(null);
      navigate("/login");
    });
  }
  const links = staff
    ? [
        ["/staff/cases", "售后工作台", ClipboardList],
        ["/staff/operations", "履约与核对", PackageCheck],
        ["/staff/inventory", "新品库存", Boxes],
        ["/staff/conversations", "人工接待", MessageCircle],
        ...(user?.role === "admin"
          ? [
              ["/admin/policies", "政策版本", ShieldCheck],
              ["/admin/system", "系统运行", LayoutDashboard],
            ]
          : []),
      ]
    : [
        ["/orders", "我的订单", Box],
        ["/cases", "售后申请", ClipboardList],
        ["/help", "服务指南", HelpCircle],
      ];
  return (
    <div className={`app ${staff ? "staff" : ""}`}>
      <EnvironmentBanner />
      <header className="topbar">
        <Link className="brand" to={staff ? "/staff/cases" : "/orders"}>
          <span className="brand-mark">
            <Box size={22} strokeWidth={1.7} />
          </span>
          <span>
            归程<small>电子产品售后服务</small>
          </span>
        </Link>
        <nav>
          {links.map(([to, label, Icon]: any) => (
            <NavLink key={to} to={to}>
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="account-menu">
          <span className="avatar">
            {user?.username.slice(0, 1).toUpperCase()}
          </span>
          <span>
            {user?.username}
            <small>
              {user?.role === "customer"
                ? "客户"
                : user?.role === "admin"
                  ? "管理员"
                  : "售后专员"}
            </small>
          </span>
          <button
            className="icon-button"
            aria-label="退出登录"
            onClick={logout}
            disabled={action.busy}
          >
            <LogOut size={17} />
          </button>
        </div>
      </header>
      <main className="main">
        <Alert>{action.error}</Alert>
        <Outlet />
      </main>
      <footer>
        <span>归程 · 让每一次售后，有据可循。</span>
        <span>自研售后业务系统 · 聊天入口由 Chatwoot 提供</span>
      </footer>
      <ChatWidget />
    </div>
  );
}
function LoginPage() {
  const { user, loading, setUser, mode } = useAuth(),
    action = useAction(),
    navigate = useNavigate(),
    location = useLocation();
  const [username, setUsername] = useState(""),
    [password, setPassword] = useState("");
  if (loading) return <Loading />;
  if (user)
    return (
      <Navigate
        to={user.role === "customer" ? "/orders" : "/staff/cases"}
        replace
      />
    );
  return (
    <div className="login-page">
      <EnvironmentBanner />
      <div className="login-shell">
        <section className="login-story">
          <Link className="brand light" to="/login">
            <span className="brand-mark">
              <Box size={24} />
            </span>
            <span>
              归程<small>电子产品售后服务</small>
            </span>
          </Link>
          <div>
            <span className="eyebrow">SERVICE THAT FOLLOWS THROUGH</span>
            <h1>
              好产品，
              <br />
              也需要好售后。
            </h1>
            <p>
              找到您的设备，核对办理条件，
              <br />
              每一步进展，都有清楚的记录。
            </p>
            <div className="login-steps">
              <span>
                <ShieldCheck />
                身份与订单安全核验
              </span>
              <span>
                <PackageCheck />
                退货退款与同款换货
              </span>
              <span>
                <Headphones />
                机器人与人工协同接待
              </span>
            </div>
          </div>
          <small>
            {mode === "demo"
              ? "本系统使用演示身份、订单与模拟履约。"
              : "办理以核验后的订单、政策与实际履约依据为准。"}
          </small>
        </section>
        <section className="login-form">
          <div className="eyebrow">欢迎回来</div>
          <h2>登录售后服务</h2>
          <p className="muted">使用您的账号继续办理或查看进展。</p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              action.run(async () => {
                const me = await api<User>("/auth/login", "POST", {
                  username,
                  password,
                });
                resetChat();
                setUser(me);
                setPassword("");
                navigate(
                  (location.state as any)?.from ||
                    (me.role === "customer" ? "/orders" : "/staff/cases"),
                );
              });
            }}
          >
            <label>
              用户名
              <input
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
                placeholder="输入客户或员工账号"
              />
            </label>
            <label>
              密码
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
                placeholder="输入登录密码"
              />
            </label>
            <Alert>{action.error}</Alert>
            <Submit busy={action.busy}>
              登录
              <ArrowRight size={17} />
            </Submit>
          </form>
          <div className="demo-login-note">
            <ShieldCheck size={17} />
            <p>
              {mode === "demo"
                ? "演示账号由本地初始化提供。客户使用 customer-a / customer-b，员工使用 support / admin；密码由部署者配置。"
                : "请使用部署者分配的客户或员工账号。遇到登录问题，请联系管理员。"}
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
function HelpPage() {
  return (
    <>
      <div className="eyebrow">服务指南</div>
      <h1>办理前，先了解这几步。</h1>
      <div className="help-grid">
        {[
          [
            "01",
            "选择自己的设备",
            "从订单进入，按设备单独发起。每台设备按原商品实付金额分摊，金额由后端核验。",
          ],
          [
            "02",
            "核对条件并明确确认",
            "查看对应版本的政策。生成预览后仍需您点击确认；聊天中的意向不等于正式申请。",
          ],
          [
            "03",
            "收到通知后寄回",
            "员工同意寄回后填写物流。收到商品并验收通过后，才安排退款或换货。",
          ],
          [
            "04",
            "随时查看办理记录",
            "申请页面显示材料、物流与履约进展。政策边界或异常结果由员工协助核查。",
          ],
        ].map(([number, title, text]) => (
          <section className="panel" key={number}>
            <div className="step-number">{number}</div>
            <h2>{title}</h2>
            <p className="muted">{text}</p>
          </section>
        ))}
      </div>
      <div className="panel help-contact">
        <Headphones size={30} />
        <div>
          <h2>需要协助？</h2>
          <p>在右下角聊天入口说明问题，或前往订单使用表单办理。</p>
        </div>
        <Link to="/orders" className="button">
          查看我的订单
          <ArrowRight size={16} />
        </Link>
      </div>
    </>
  );
}
function NotFound() {
  return (
    <div className="empty">
      <h1>页面未找到</h1>
      <Link to="/" className="button">
        <ArrowLeft size={16} />
        返回首页
      </Link>
    </div>
  );
}
function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<Guard />}>
            <Route element={<Layout />}>
              <Route index element={<Landing />} />
              <Route element={<Guard />}>
                <Route path="orders" element={<OrdersPage />} />
                <Route path="orders/:id" element={<OrderPage />} />
                <Route path="proposals/:id" element={<ProposalPage />} />
                <Route path="cases" element={<CasesPage />} />
                <Route path="cases/:id" element={<CasePage />} />
                <Route
                  path="conversations/:id"
                  element={<CustomerConversationPage />}
                />
                <Route path="help" element={<HelpPage />} />
              </Route>
              <Route element={<Guard staff />}>
                <Route path="staff/cases" element={<StaffCasesPage />} />
                <Route path="staff/cases/:id" element={<CasePage staff />} />
                <Route path="staff/operations" element={<OperationsPage />} />
                <Route path="staff/inventory" element={<InventoryPage />} />
                <Route
                  path="staff/conversations"
                  element={<ConversationPage />}
                />
                <Route
                  path="staff/conversations/:id"
                  element={<ConversationPage />}
                />
              </Route>
              <Route element={<Guard admin />}>
                <Route path="admin/policies" element={<PoliciesPage />} />
                <Route path="admin/system" element={<SystemPage />} />
              </Route>
              <Route path="*" element={<NotFound />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
function Landing() {
  const { user } = useAuth();
  return (
    <Navigate
      replace
      to={user?.role === "customer" ? "/orders" : "/staff/cases"}
    />
  );
}
createRoot(document.getElementById("root")!).render(<App />);
