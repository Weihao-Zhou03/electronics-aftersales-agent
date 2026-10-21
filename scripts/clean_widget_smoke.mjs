// Real isolated Chromium and real Widget. No mocks, fixtures or personal browser profile.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const root = process.env.SOURCE_ROOT;
const require = createRequire(path.join(root, "tests/e2e/package.json"));
const { chromium } = require("@playwright/test");
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(process.env.CLEAN_API_URL + "/m0");
  await page.getByLabel("用户名", { exact: true }).fill("customer-a");
  await page.getByLabel("密码", { exact: true }).fill(process.env.CLEAN_DEMO_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator("#result").filter({ hasText: "登录成功" }).waitFor({ timeout: 15000 });
  const identified = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/widget/contact/set_user");
  await page.getByRole("button", { name: "初始化当前客户 Widget" }).click();
  if ((await identified).status() !== 200) throw new Error("Widget identity validation failed");
  await page.evaluate(() => window.$chatwoot.toggle("open"));
  const widget = page.frameLocator("#chatwoot_live_chat_widget");
  const start = widget.getByRole("button", { name: /^(开始会话|继续对话)$/ });
  await start.or(widget.getByLabel("输入您的消息")).first().waitFor({ timeout: 30000 });
  if (await start.isVisible()) await start.click();
  const firstMessage = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/widget/messages" && response.request().method() === "POST");
  await widget.getByLabel("输入您的消息").fill("全新独立部署固定回复验证");
  await widget.getByLabel("输入您的消息").press("Enter");
  const first = await (await firstMessage).json();
  await widget.getByText("演示环境 · 模拟订单与履约。请先在客户门户登录并完成聊天身份验证；当前仅提供公共接入说明。", { exact: true }).last().waitFor({ timeout: 45000 });
  await page.getByLabel("会话编号").fill(String(first.conversation_id));
  await page.getByRole("button", { name: "核验并建立绑定" }).click();
  await page.locator("#result").filter({ hasText: '"ok": true' }).waitFor({ timeout: 15000 });
  await widget.getByLabel("输入您的消息").fill("完整可信绑定后固定回复验证");
  await widget.getByLabel("输入您的消息").press("Enter");
  await widget.getByText("演示环境 · 模拟订单与履约。M0 固定回复：已收到您的消息；当前仅验证聊天接入。", { exact: true }).waitFor({ timeout: 45000 });
  await page.screenshot({ path: process.env.CLEAN_SCREENSHOT, fullPage: true });
  fs.writeFileSync(process.env.CLEAN_WIDGET_REPORT, JSON.stringify({ source: "Real isolated Chromium + new Chatwoot instance + Sidekiq + API + worker", widget_identity: "pass", fixed_reply_roundtrip: "pass", no_llm: true }, null, 2));
  console.log("Fresh-install real Widget baseline passed");
} finally {
  await browser.close();
}
