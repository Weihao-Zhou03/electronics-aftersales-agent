// Real UI acceptance over real API/PostgreSQL/DemoProvider. No intercepted routes.
import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const evidence = path.join(root, "docs/evidence");

async function login(page, username) {
  await page.goto("/login");
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.M0_DEMO_PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(
    username.startsWith("customer-") ? /\/orders$/ : /\/staff\/cases$/,
  );
  await expect(page.locator(".demo-banner")).toContainText(
    "演示环境 · 模拟订单与履约",
  );
}

async function realConversation(page) {
  await page.waitForFunction(() => window.$chatwoot?.hasLoaded);
  await page.evaluate(() => window.$chatwoot.toggle("open"));
  const widget = page.frameLocator("#chatwoot_live_chat_widget");
  const start = widget.getByRole("button", { name: /^(开始会话|继续对话)$/ });
  const input = widget.getByLabel("输入您的消息");
  await start.or(input).first().waitFor({ state: "visible", timeout: 30000 });
  if (await start.isVisible()) await start.click();
  const bound = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/chat/bind" &&
      response.status() === 200,
  );
  const sent = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/widget/messages" &&
      response.request().method() === "POST",
  );
  await input.fill(`M2真实门户会话验证 ${Date.now()}`);
  await input.press("Enter");
  const body = await (await sent).json();
  expect((await bound).status()).toBe(200);
  await expect(page.getByText("聊天身份已验证", { exact: true })).toBeVisible();
  await page.evaluate(() => window.$chatwoot.toggle("close"));
  return body.conversation_id;
}

async function returnThroughUi(
  staff,
  conversation,
  concurrentTakeover = false,
) {
  await staff
    .getByRole("button", { name: "显式交回机器人", exact: true })
    .click();
  if (concurrentTakeover) {
    // A real authorized concurrent staff action invalidates the already-open
    // confirmation. This is an actual HTTP request, never a mocked response.
    const me = await (await staff.request.get("/api/v1/auth/me")).json();
    const state = await (
      await staff.request.get(`/api/v1/staff/conversations/${conversation}`)
    ).json();
    const changed = await staff.request.post(
      `/api/v1/staff/conversations/${conversation}/takeover`,
      {
        headers: {
          Origin: new URL(staff.url()).origin,
          "X-CSRF-Token": me.csrf_token,
          "Idempotency-Key": crypto.randomUUID(),
        },
        data: { expected_revision: state.control_revision },
      },
    );
    expect(changed.status()).toBe(200);
  }
  const responsePromise = staff.waitForResponse(
    (response) =>
      new URL(response.url()).pathname.endsWith("/return-to-bot") &&
      response.request().method() === "POST",
  );
  await staff
    .getByRole("dialog")
    .getByRole("button", { name: "确认提交", exact: true })
    .click();
  const response = await responsePromise;
  if (concurrentTakeover) expect(response.status()).toBe(409);
  if (response.status() === 409) {
    await expect(staff.getByRole("dialog").getByRole("alert")).toContainText(
      "会话状态已变化，本次操作未生效",
    );
    const stopped = await (
      await staff.request.get(`/api/v1/staff/conversations/${conversation}`)
    ).json();
    expect(stopped.control_mode).toBe("HUMAN_ACTIVE");
    await staff.screenshot({
      path: path.join(evidence, "m2-control-conflict.png"),
      fullPage: true,
    });
    // An explicit close, refreshed state and new confirmation is required.
    await staff
      .getByRole("dialog")
      .getByRole("button", { name: "再核对一下", exact: true })
      .click();
    const refreshed = staff.waitForResponse(
      (value) =>
        new URL(value.url()).pathname ===
          `/api/v1/staff/conversations/${conversation}` &&
        value.request().method() === "GET",
    );
    await staff.getByRole("button", { name: "重新核验", exact: true }).click();
    expect((await refreshed).status()).toBe(200);
    await staff
      .getByRole("button", { name: "显式交回机器人", exact: true })
      .click();
    const confirmed = staff.waitForResponse(
      (value) =>
        new URL(value.url()).pathname.endsWith("/return-to-bot") &&
        value.request().method() === "POST",
    );
    await staff
      .getByRole("dialog")
      .getByRole("button", { name: "确认提交", exact: true })
      .click();
    expect((await confirmed).status()).toBe(200);
  } else expect(response.status()).toBe(200);
  await expect(staff.locator(".status")).toHaveText("机器人服务中");
}

async function createCase(page, seed, unit, kind) {
  await page.goto(`/orders/${seed.order_id}`);
  await expect(
    page.getByRole("heading", { name: seed.external_id, exact: true }),
  ).toBeVisible();
  await page
    .locator(".unit-row")
    .filter({ hasText: unit.serial })
    .getByRole("button", { name: "申请售后" })
    .click();
  const form = page.locator(".application-form");
  if (kind === "exchange")
    await form.getByText("同款质量换货", { exact: true }).click();
  await form
    .getByLabel("问题与申请原因")
    .fill(
      kind === "return"
        ? "UI真实验收：设备未使用且包装配件齐全，申请退货。"
        : "UI真实验收：耳机持续断连，申请同 SKU 质量换货。",
    );
  if (kind === "return") {
    await form.getByText("商品未使用", { exact: true }).click();
    await form.getByText("商品、配件与原包装完整", { exact: true }).click();
  } else
    await form
      .getByText("存在需要员工验收核实的质量问题", { exact: true })
      .click();
  if (kind === "return") {
    const upload = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/attachments" &&
        response.request().method() === "POST",
    );
    await form.locator("input[type=file]").setInputFiles({
      name: "UI真实测试材料.png",
      mimeType: "image/png",
      buffer: await page.screenshot(),
    });
    expect((await upload).status()).toBe(200);
    await expect(form.getByRole("link", { name: "材料 1" })).toBeVisible();
  }
  await form.getByRole("button", { name: "先核验条件" }).click();
  await expect(
    form.getByText("当前信息满足申请条件", { exact: true }),
  ).toBeVisible();
  await form.getByRole("button", { name: "生成申请预览" }).click();
  await expect(page).toHaveURL(/\/proposals\//);
  await expect(
    page.getByRole("heading", { name: "这次申请，将按以下内容受理。" }),
  ).toBeVisible();
  await page
    .getByText("我已核对设备、原因、金额/换货范围及适用政策", { exact: true })
    .click();
  await page.getByRole("button", { name: "确认并提交申请" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认提交", exact: true })
    .click();
  await expect(page).toHaveURL(/\/cases\//);
  const id = new URL(page.url()).pathname.split("/").pop();
  await expect(page.locator(".case-progress-banner .status")).toHaveText(
    "待员工初审",
  );
  return id;
}

async function processCase(staff, customer, caseId, unit, kind) {
  await staff.goto(`/staff/cases/${caseId}`);
  const review = staff.locator(".panel").filter({
    has: staff.getByRole("heading", { name: "员工初审", exact: true }),
  });
  await review
    .getByLabel("说明", { exact: true })
    .fill("真实 UI 验收：核对记录后同意寄回。");
  await review.getByRole("button", { name: "保存初审意见" }).click();
  await expect(staff.locator(".case-progress-banner .status")).toHaveText(
    "待寄回商品",
  );
  await customer.goto(`/cases/${caseId}`);
  await customer.getByLabel("承运公司", { exact: true }).fill("演示快递");
  await customer
    .getByLabel("运单号", { exact: true })
    .fill(`UI-${caseId.slice(0, 10)}`);
  await customer.getByRole("button", { name: "保存寄回物流" }).click();
  await expect(customer.getByRole("status")).toContainText("寄回物流已保存");
  await staff.getByRole("button", { name: "刷新进展" }).click();
  await staff
    .getByLabel("实际收到的设备序列号", { exact: true })
    .fill(unit.serial);
  await staff.getByRole("button", { name: "确认收到商品" }).click();
  await expect(staff.locator(".case-progress-banner .status")).toHaveText(
    "收货验收中",
  );
  await staff
    .getByLabel("验收说明", { exact: true })
    .fill("真实 UI 验收：序列号与申请一致，已核对商品状态。");
  if (kind === "return") {
    await staff.getByText("确认商品未使用", { exact: true }).click();
    await staff.getByText("确认配件完整", { exact: true }).click();
  } else await staff.getByText("确认存在质量问题", { exact: true }).click();
  await staff.getByRole("button", { name: "确认验收并安排履约" }).click();
  await expect(staff.getByRole("status")).toContainText("验收结果已保存");
  // The actual operations worker and independent DemoProvider finish the task.
  await expect(async () => {
    await customer.goto(`/cases/${caseId}`);
    await expect(customer.locator(".case-progress-banner .status")).toHaveText(
      "办理完成",
      { timeout: 1500 },
    );
  }).toPass({ timeout: 45000, intervals: [1000, 2000, 3000] });
  await expect(
    customer.getByText(
      kind === "return"
        ? "模拟履约 · 模拟退款成功"
        : "模拟履约 · 模拟换货已发出",
      { exact: true },
    ),
  ).toBeVisible();
}

test("M2 actual customer confirmation, staff review/receipt/inspection, refund/exchange and controls", async ({
  page,
  browser,
}) => {
  test.skip(
    !process.env.M0_DEMO_PASSWORD,
    "Requires the local seeded password and actual Compose services.",
  );
  test.setTimeout(240000);
  fs.mkdirSync(evidence, { recursive: true });
  const seedText = execFileSync(
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
      "scripts/seed_ui_demo.py",
    ],
    { cwd: root, encoding: "utf8" },
  );
  const seed = JSON.parse(seedText.trim().split(/\r?\n/).pop());
  await login(page, "customer-a");
  const conversation = await realConversation(page);
  const staffContext = await browser.newContext(),
    staff = await staffContext.newPage();
  await login(staff, "support");
  const refund = await createCase(page, seed, seed.units[0], "return");
  await processCase(staff, page, refund, seed.units[0], "return");
  await page.screenshot({
    path: path.join(evidence, "m2-refund-completed.png"),
    fullPage: true,
  });
  const exchange = await createCase(page, seed, seed.units[1], "exchange");
  await processCase(staff, page, exchange, seed.units[1], "exchange");
  await page.screenshot({
    path: path.join(evidence, "m2-exchange-completed.png"),
    fullPage: true,
  });
  await staff.goto("/staff/cases");
  await expect(
    staff.getByRole("heading", { name: "让每一件售后，顺利往前走。" }),
  ).toBeVisible();
  await staff.getByLabel("搜索售后申请").fill(exchange.slice(0, 8));
  await staff.getByLabel("搜索售后申请").clear();
  await staff.screenshot({
    path: path.join(evidence, "m2-staff-workbench.png"),
    fullPage: true,
  });
  // Backend authorization remains authoritative even if a customer types a staff URL.
  await page.goto("/staff/cases");
  await expect(page.getByRole("alert")).toContainText("此页面仅向授权员工开放");

  {
    await staff.goto(`/staff/conversations/${conversation}`);
    await expect(staff.locator(".status")).toBeVisible();
    if (
      await staff
        .getByRole("button", { name: "显式交回机器人", exact: true })
        .count()
    )
      await returnThroughUi(staff, conversation);
    await staff.getByRole("button", { name: "接管会话", exact: true }).click();
    await staff
      .getByRole("dialog")
      .getByRole("button", { name: "确认提交", exact: true })
      .click();
    await expect(staff.locator(".status")).toHaveText("人工接待中");
    await returnThroughUi(staff, conversation, true);
    await staff.screenshot({
      path: path.join(evidence, "m2-conversation-control.png"),
      fullPage: true,
    });
  }
  const mobile = await browser.newContext({
      viewport: { width: 390, height: 844 },
    }),
    mobilePage = await mobile.newPage();
  await login(mobilePage, "customer-a");
  await expect(
    mobilePage.getByRole("heading", { name: "您的设备，我们继续照顾。" }),
  ).toBeVisible();
  await mobilePage.screenshot({
    path: path.join(evidence, "m2-mobile-orders.png"),
    fullPage: true,
  });
  const width = await mobilePage.evaluate(() => ({
    viewport: innerWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(width.content).toBeLessThanOrEqual(width.viewport);
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await login(page, "customer-b");
  await expect(page.getByText(seed.external_id, { exact: true })).toHaveCount(
    0,
  );
  const deniedOrder = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/v1/orders/${seed.order_id}` &&
      response.status() === 404,
  );
  await page.goto(`/orders/${seed.order_id}`);
  expect((await deniedOrder).status()).toBe(404);
  await expect(page.getByRole("alert")).toContainText(
    "该记录不存在或您无权访问",
  );
  fs.writeFileSync(
    path.join(evidence, "m2-ui-results.json"),
    JSON.stringify(
      {
        source:
          "Real isolated Chromium -> React UI -> FastAPI -> PostgreSQL -> operations worker -> independent DemoProvider",
        captured_at: new Date().toISOString(),
        order_id: seed.order_id,
        refund_case_id: refund,
        exchange_case_id: exchange,
        conversation_id: conversation,
        checks: {
          authenticated_order_scope: "pass",
          runtime_demo_mode_banner: "pass",
          widget_server_identity_and_automatic_api_binding: "pass",
          logout_account_switch_and_cross_customer_order_denial: "pass",
          private_evidence_upload: "pass",
          customer_explicit_confirmation: "pass",
          staff_review_receive_inspect: "pass",
          simulated_refund_receipt: "pass",
          simulated_exchange_outbound: "pass",
          customer_staff_guard: "pass",
          mobile_layout: "pass",
          takeover_and_explicit_return_buttons: "pass",
          actual_concurrent_takeover_invalidates_old_dialog_409_remains_paused:
            "pass",
          explicit_close_refresh_and_new_confirmation_restores_bot: "pass",
        },
      },
      null,
      2,
    ) + "\n",
  );
  await mobile.close();
  await staffContext.close();
});
