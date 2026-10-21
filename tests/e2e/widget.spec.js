// Real isolated Chromium -> Portal -> real Chatwoot Widget -> Sidekiq -> API -> worker.
// No route mocks, prerecorded messages, personal browser profile, or LLM calls.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const envFile = path.join(root, ".env.chatwoot");
const config = Object.fromEntries(
  (fs.existsSync(envFile) ? fs.readFileSync(envFile, "utf8") : "")
    .split(/\r?\n/)
    .filter((line) => line && !line.startsWith("#") && line.includes("="))
    .map((line) => [
      line.slice(0, line.indexOf("=")),
      line.slice(line.indexOf("=") + 1),
    ]),
);
const base = process.env.CHATWOOT_PUBLIC_URL || "http://localhost:3000";
const account = config.CHATWOOT_ACCOUNT_ID;
const fixed =
  "演示环境 · 模拟订单与履约。M0 固定回复：已收到您的消息；当前仅验证聊天接入。";
const artifacts = path.join(root, "docs/evidence");
fs.mkdirSync(artifacts, { recursive: true });

async function login(page, user) {
  await page.goto("/m0");
  await page.getByLabel("用户名", { exact: true }).fill(user);
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.M0_DEMO_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator("#result")).toHaveText("登录成功");
}

async function initializeWidget(page) {
  const identified = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/widget/contact/set_user",
  );
  await page.getByRole("button", { name: "初始化当前客户 Widget" }).click();
  expect((await identified).status()).toBe(200);
  await page.evaluate(() => window.$chatwoot.toggle("open"));
  const widget = page.frameLocator("#chatwoot_live_chat_widget");
  const start = widget.getByRole("button", { name: /^(开始会话|继续对话)$/ });
  await start
    .or(widget.getByLabel("输入您的消息"))
    .first()
    .waitFor({ state: "visible", timeout: 30000 });
  if (await start.isVisible()) await start.click();
  await expect(widget.getByLabel("输入您的消息")).toBeVisible();
  return widget;
}

async function sendWidget(page, widget, content) {
  const sent = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/widget/messages" &&
      response.request().method() === "POST",
  );
  await widget.getByLabel("输入您的消息").fill(content);
  await widget.getByLabel("输入您的消息").press("Enter");
  const response = await sent;
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.message_type).toBe(0);
  await expect(widget.getByText(content, { exact: true })).toBeVisible();
  return { body, request: response.request() };
}

async function history(page, conversation) {
  const response = await page.request.get(
    `${base}/api/v1/accounts/${account}/conversations/${conversation}/messages`,
    {
      headers: { Authorization: `Bearer ${config.CHATWOOT_READ_ACCESS_TOKEN}` },
    },
  );
  expect(response.status()).toBe(200);
  return (await response.json()).payload;
}

async function awaitBotReply(page, conversation, incomingId) {
  await expect
    .poll(
      async () =>
        (await history(page, conversation)).filter(
          (message) =>
            message.id > incomingId &&
            message.message_type === 1 &&
            message.private === false &&
            message.sender?.type === "agent_bot",
        ).length,
      { timeout: 30000 },
    )
    .toBe(1);
}

test("M0 real Widget round trip, private note, forged identities and logout/account switch", async ({
  page,
  browser,
}) => {
  test.skip(
    !process.env.M0_DEMO_PASSWORD || !config.CHATWOOT_BOT_ACCESS_TOKEN,
    "Set the local seeded M0_DEMO_PASSWORD and real Chatwoot config; never hardcode credentials.",
  );
  const result = {
    source: "real Playwright isolated Chromium and actual Chatwoot CE v4.19.0",
    captured_at: new Date().toISOString(),
    checks: {},
  };
  await login(page, "customer-a");
  const ownA = await (await page.request.get("/api/v1/chat/identity")).json();
  const widget = await initializeWidget(page);
  const first = await sendWidget(
    page,
    widget,
    `M0真实Widget往返验证 ${Date.now()}`,
  );
  const conversation = first.body.conversation_id;
  await awaitBotReply(page, conversation, first.body.id);
  result.checks.widget_send_sidekiq_callback_and_reply = "pass";

  await page.getByLabel("会话编号").fill(String(conversation));
  await page.getByRole("button", { name: "核验并建立绑定" }).click();
  await expect(page.locator("#result")).toContainText('"ok": true');
  const second = await sendWidget(
    page,
    widget,
    `M0可信绑定后固定回复验证 ${Date.now()}`,
  );
  await awaitBotReply(page, conversation, second.body.id);
  await expect(widget.getByText(fixed, { exact: true }).last()).toBeVisible();
  result.checks.trusted_hmac_binding_and_fixed_reply = "pass";
  await page.waitForTimeout(2000); // Negative loop assertion needs an observation window.
  const after = await history(page, conversation);
  expect(
    after.filter(
      (message) =>
        message.id > second.body.id &&
        message.message_type === 1 &&
        message.private === false &&
        message.sender?.type === "agent_bot",
    ),
  ).toHaveLength(1);
  result.checks.outgoing_does_not_loop_observed_2_seconds = "pass";

  const privateText = `M0 私有备注隐藏验证 ${Date.now()}`;
  const noteResponse = await page.request.post(
    `${base}/api/v1/accounts/${account}/conversations/${conversation}/messages`,
    {
      headers: { Authorization: `Bearer ${config.CHATWOOT_BOT_ACCESS_TOKEN}` },
      data: { content: privateText, message_type: "outgoing", private: true },
    },
  );
  expect(noteResponse.status()).toBe(200);
  expect((await noteResponse.json()).private).toBe(true);
  expect(
    (await history(page, conversation)).some(
      (message) => message.content === privateText && message.private === true,
    ),
  ).toBe(true);
  const customerHistory = await page.request.get(first.request.url(), {
    headers: first.request.headers(),
  });
  expect(customerHistory.status()).toBe(200);
  expect(JSON.stringify(await customerHistory.json())).not.toContain(
    privateText,
  );
  await expect(widget.getByText(privateText, { exact: true })).toHaveCount(0);
  result.checks.private_note_visible_to_support_absent_from_widget_api_and_ui =
    "pass";
  await page.screenshot({
    path: path.join(artifacts, "m0-widget-roundtrip.png"),
    fullPage: true,
  });

  const contextB = await browser.newContext();
  const pageB = await contextB.newPage();
  await login(pageB, "customer-b");
  const ownB = await (await pageB.request.get("/api/v1/chat/identity")).json();
  expect(ownA.identifier).not.toBe(ownB.identifier);
  const setUserPath = "/api/v1/widget/contact/set_user";
  const forged = page.waitForResponse(
    (response) => new URL(response.url()).pathname === setUserPath,
  );
  await page.evaluate(
    ({ identifier, hash }) =>
      window.$chatwoot.setUser(identifier, {
        identifier_hash: hash,
        name: "M0伪造标识测试",
      }),
    { identifier: ownB.identifier, hash: ownA.identifier_hash },
  );
  expect((await forged).status()).toBe(401);
  const widgetB = await initializeWidget(pageB);
  const forgedReverse = pageB.waitForResponse(
    (response) => new URL(response.url()).pathname === setUserPath,
  );
  await pageB.evaluate(
    ({ identifier, hash }) =>
      window.$chatwoot.setUser(identifier, {
        identifier_hash: hash,
        name: "M0伪造标识测试",
      }),
    { identifier: ownA.identifier, hash: ownB.identifier_hash },
  );
  expect((await forgedReverse).status()).toBe(401);
  await expect(widgetB.getByLabel("输入您的消息")).toBeVisible();
  result.checks.two_logged_in_customers_identifier_swaps_rejected_by_chatwoot =
    "pass";

  const loggedOut = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/auth/logout",
  );
  await page.getByRole("button", { name: "退出并重置聊天身份" }).click();
  expect((await loggedOut).status()).toBe(200);
  await expect(page.locator("#account")).toContainText("旧授权已撤销");
  expect((await page.request.get("/api/v1/chat/identity")).status()).toBe(401);
  await page.getByLabel("用户名", { exact: true }).fill("customer-b");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.M0_DEMO_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator("#result")).toHaveText("登录成功");
  await page.getByLabel("会话编号").fill(String(conversation));
  const crossBind = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/chat/bind",
  );
  await page.getByRole("button", { name: "核验并建立绑定" }).click();
  expect((await crossBind).status()).toBe(403);
  const switched = await initializeWidget(page);
  await expect(
    switched.getByText(second.body.content, { exact: true }),
  ).toHaveCount(0);
  result.checks.logout_denies_old_session_and_switched_user_cannot_bind_old_conversation =
    "pass";
  await page.screenshot({
    path: path.join(artifacts, "m0-widget-account-switch.png"),
    fullPage: true,
  });
  await contextB.close();
  result.conversation_id = conversation;
  result.incoming_message_ids = [first.body.id, second.body.id];
  fs.writeFileSync(
    path.join(artifacts, "m0-widget-results.json"),
    JSON.stringify(result, null, 2) + "\n",
  );
});
