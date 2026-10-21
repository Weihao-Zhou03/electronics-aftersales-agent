// Shared helpers perform real HTTP/UI operations; they never intercept routes.
import { expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const root = path.resolve(import.meta.dirname, "../..");
export const evidence = path.join(root, "docs/evidence");
export const seed = () =>
  JSON.parse(
    fs.readFileSync(path.join(root, ".local/scenario-seed.json"), "utf8"),
  );
export const password = () =>
  process.env.M0_DEMO_PASSWORD ||
  fs.readFileSync(path.join(root, ".local/demo-password.txt"), "utf8").trim();
export const env = Object.fromEntries(
  fs
    .readFileSync(path.join(root, ".env.chatwoot"), "utf8")
    .split(/\r?\n/)
    .filter((line) => line.includes("=") && !line.startsWith("#"))
    .map((line) => [
      line.slice(0, line.indexOf("=")),
      line.slice(line.indexOf("=") + 1),
    ]),
);

// Read only allowlisted effective runtime fields; credentials are never emitted.
export function runtimeEvidence() {
  const source = JSON.parse(
    execFileSync(
      "docker",
      [
        "compose",
        "--env-file",
        ".env",
        "--env-file",
        ".env.chatwoot",
        "exec",
        "-T",
        "worker",
        "uv",
        "run",
        "--frozen",
        "--no-sync",
        "--no-dev",
        "python",
        "-c",
        "import hashlib,json,pathlib; from aftersales.config import Settings; s=Settings.from_env(); print(json.dumps({'runtime_budget':{'model_calls':s.agent_max_model_calls,'tool_calls':s.agent_max_tool_calls,'http_timeout_seconds':s.model_timeout_seconds,'turn_timeout_seconds':s.agent_turn_timeout_seconds},'provider':s.model_provider,'model':s.model_name,'verified_worker_source_sha256':{p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in ['src/aftersales/config.py','src/aftersales/agent_tools.py','src/aftersales/retrieval.py','prompts/aftersales-v1.txt']}}))",
      ],
      { cwd: root, encoding: "utf8" },
    ),
  );
  const hostSource = Object.fromEntries(
    Object.keys(source.verified_worker_source_sha256).map((file) => [
      file,
      createHash("sha256")
        .update(fs.readFileSync(path.join(root, file)))
        .digest("hex"),
    ]),
  );
  expect(source.verified_worker_source_sha256).toEqual(hostSource);
  expect(source.runtime_budget).toEqual({
    model_calls: 9,
    tool_calls: 8,
    http_timeout_seconds: 60,
    turn_timeout_seconds: 120,
  });
  expect(source.provider).not.toBe("mock");
  return {
    code_commit: execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: root,
      encoding: "utf8",
    }).trim(),
    ...source,
  };
}

export async function login(page, username) {
  await page.goto("/login");
  const customer = username !== "support" && username !== "admin";
  // Observe the real asynchronous HMAC setUser exchange. Opening the anonymous
  // widget before this completes can legitimately reset its start screen.
  const identified = customer
    ? page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname ===
            "/api/v1/widget/contact/set_user" &&
          response.request().method() === "PATCH",
        { timeout: 30000 },
      )
    : null;
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password());
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(
    username === "support" || username === "admin"
      ? /\/staff\/cases$/
      : /\/orders$/,
  );
  if (identified) {
    const response = await identified;
    expect(response.status()).toBe(200);
    await response.finished();
  }
}
export async function write(page, path, body, method = "POST") {
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  const response = await page.request.fetch("/api/v1" + path, {
    method,
    headers: {
      Origin: new URL(page.url()).origin,
      "X-CSRF-Token": me.csrf_token,
      "Idempotency-Key": crypto.randomUUID(),
    },
    data: body,
  });
  expect(response.status(), `${method} ${path}`).toBeLessThan(300);
  return response.json();
}
export async function widget(page) {
  await page.waitForFunction(
    () => typeof window.$chatwoot?.toggle === "function",
    null,
    { timeout: 30000 },
  );
  await page.evaluate(() => window.$chatwoot.toggle("open"));
  const frame = page.frameLocator("#chatwoot_live_chat_widget");
  const start = frame.getByRole("button", { name: /^(开始会话|继续对话)$/ });
  const input = frame.getByLabel("输入您的消息");
  await start.or(input).first().waitFor({ state: "visible", timeout: 30000 });
  if (await start.isVisible()) await start.click();
  await expect(input).toBeVisible({ timeout: 30000 });
  return frame;
}
export async function send(page, frame, text) {
  const input = frame.getByLabel("输入您的消息");
  await expect(input).toBeVisible({ timeout: 10000 });
  await input.fill(text);
  const sent = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/widget/messages" &&
      response.request().method() === "POST",
    { timeout: 30000 },
  );
  await input.press("Enter");
  const response = await sent;
  expect(response.status()).toBe(200);
  const incoming = await response.json();
  await expect(page.getByText("聊天身份已验证", { exact: true })).toBeVisible();
  return incoming;
}
export async function history(page, conversation) {
  const response = await page.request.get(
    `${process.env.CHATWOOT_PUBLIC_URL || "http://localhost:3000"}/api/v1/accounts/${env.CHATWOOT_ACCOUNT_ID}/conversations/${conversation}/messages`,
    { headers: { Authorization: `Bearer ${env.CHATWOOT_READ_ACCESS_TOKEN}` } },
  );
  expect(response.status()).toBe(200);
  return (await response.json()).payload;
}
export async function botReply(page, incoming) {
  let replies;
  await expect
    .poll(
      async () => {
        replies = (await history(page, incoming.conversation_id)).filter(
          (message) =>
            message.id > incoming.id &&
            message.message_type === 1 &&
            message.private === false &&
            message.sender?.type === "agent_bot",
        );
        return replies.length;
      },
      { timeout: 120000, intervals: [500, 1000, 2000] },
    )
    .toBe(1);
  return replies[0];
}
export function inspect(customerId) {
  const result = execFileSync(
    "docker",
    [
      "compose",
      "--env-file",
      ".env",
      "--env-file",
      ".env.chatwoot",
      "exec",
      "-T",
      "api",
      "uv",
      "run",
      "--frozen",
      "--no-sync",
      "--no-dev",
      "python",
      "scripts/scenario_inspect.py",
      "--customer-id",
      customerId,
    ],
    { cwd: root, encoding: "utf8" },
  );
  return JSON.parse(result.trim().split(/\r?\n/).pop());
}
export async function staffReceiveInspect(
  staff,
  customer,
  caseId,
  serial,
  kind = "exchange",
) {
  const get = async () =>
    (await staff.request.get(`/api/v1/staff/cases/${caseId}`)).json();
  let record = await get();
  await write(staff, `/staff/cases/${caseId}/review`, {
    expected_version: record.version,
    decision: "approve_return",
    reason: "真实演示：已核对申请，同意寄回。",
  });
  record = await get();
  await write(customer, `/cases/${caseId}/return-shipment`, {
    expected_version: record.version,
    carrier: "演示快递",
    tracking_number: "DEMO-" + caseId.slice(0, 8),
  });
  record = await get();
  await write(staff, `/staff/cases/${caseId}/receive`, {
    expected_version: record.version,
    received_serial: serial,
  });
  record = await get();
  return write(staff, `/staff/cases/${caseId}/inspect`, {
    expected_version: record.version,
    passed: true,
    verification: {
      reason: "真实员工身份验收：序列号相符，条件已核对。",
      quality_issue: kind === "exchange",
      unused: kind === "return",
      accessories_complete: kind === "return",
    },
  });
}
