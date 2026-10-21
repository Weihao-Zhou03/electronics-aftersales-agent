// Run ONLY through scripts/run_model_fault_acceptance.py: real response hold,
// followed by explicitly labelled local HTTP503 injection. No answer is mocked.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {
  root,
  evidence,
  seed,
  login,
  write,
  widget,
  send,
  botReply,
  inspect,
  history,
} from "./live-helpers.js";

test("C model portion and A28: actual inference delayed across takeover; HTTP outage leaves portal usable", async ({
  page,
  browser,
}) => {
  test.setTimeout(210000);
  const relay = JSON.parse(
    fs.readFileSync(path.join(root, ".local/model-relay.json"), "utf8"),
  );
  const customer = seed().scenarios.delay.customers[0];
  const relayState = async () =>
    (
      await page.request.get(relay.url + "/test/state", {
        headers: { Authorization: "Bearer " + relay.token },
      })
    ).json();
  async function command(action) {
    const response = await page.request.post(relay.url + "/test/" + action, {
      headers: { Authorization: "Bearer " + relay.token },
    });
    expect(response.status()).toBe(200);
  }
  await login(page, customer.username);
  const frame = await widget(page);
  await command("hold_next");
  const incoming = await send(
    page,
    frame,
    `我的订单 ${customer.external_id}，设备 ${customer.serial} 未使用且配件齐全，申请退货。请先查询本人订单和购买政策，再准备需我明确确认的预览，不要代替提交。`,
  );
  // Held means the actual authorized upstream HTTP call completed and its exact
  // response is in memory; this is not the fixed reply task delay.
  await expect
    .poll(async () => (await relayState()).held, {
      timeout: 90000,
      intervals: [100, 250, 500],
    })
    .toBe(true);
  const held = await relayState();
  expect(held.completed_upstream_calls).toBeGreaterThan(0);
  const staffContext = await browser.newContext(),
    staff = await staffContext.newPage();
  await login(staff, "support");
  const state = await (
    await staff.request.get(
      `/api/v1/staff/conversations/${incoming.conversation_id}`,
    )
  ).json();
  await write(
    staff,
    `/staff/conversations/${incoming.conversation_id}/takeover`,
    { expected_revision: state.control_revision },
  );
  await command("release");
  await expect
    .poll(() => inspect(customer.customer_id).runs.at(-1)?.status, {
      timeout: 30000,
      intervals: [500, 1000],
    })
    .toBe("STALE");
  const stale = inspect(customer.customer_id);
  expect(stale.proposals).toHaveLength(0);
  expect(stale.cases).toHaveLength(0);
  expect(stale.outbound).toHaveLength(0);
  expect(
    (await history(page, incoming.conversation_id)).filter(
      (message) =>
        message.id > incoming.id &&
        message.private === false &&
        message.sender?.type === "agent_bot",
    ),
  ).toHaveLength(0);
  expect(stale.runs.at(-1).provider).not.toBe("mock");
  const paused = await (
    await staff.request.get(
      `/api/v1/staff/conversations/${incoming.conversation_id}`,
    )
  ).json();
  expect(paused.control_mode).toBe("HUMAN_ACTIVE");

  // The employee explicitly restores current, freshly read ownership.
  await write(
    staff,
    `/staff/conversations/${incoming.conversation_id}/return-to-bot`,
    { expected_revision: paused.control_revision },
  );
  await command("fail_next");
  const outage = await send(
    page,
    frame,
    "现在请帮我查看这笔订单可以怎样申请退货。请不要替我提交。",
  );
  const reply = await botReply(page, outage);
  expect(reply.content).toContain("模型服务暂不可用");
  expect(reply.content).not.toMatch(/MockProvider|M0 固定回复/);
  expect((await relayState()).injected_http_failures).toBe(1);
  expect(inspect(customer.customer_id).proposals).toHaveLength(0);
  await page.evaluate(() => window.$chatwoot.toggle("close"));
  await page.goto(`/orders/${customer.order_id}`);
  await page
    .locator(".unit-row")
    .filter({ hasText: customer.serial })
    .getByRole("button", { name: "申请售后" })
    .click();
  const form = page.locator(".application-form");
  await form
    .getByLabel("问题与申请原因")
    .fill("模型 HTTP503 故障注入期间，客户改用真实门户表单申请退货。");
  await form.getByText("商品未使用", { exact: true }).click();
  await form.getByText("商品、配件与原包装完整", { exact: true }).click();
  await form.getByRole("button", { name: "先核验条件", exact: true }).click();
  await expect(
    form.getByText("当前信息满足申请条件", { exact: true }),
  ).toBeVisible();
  await form.getByRole("button", { name: "生成申请预览", exact: true }).click();
  await expect(page).toHaveURL(/\/proposals\//);
  await page
    .getByText("我已核对设备、原因、金额/换货范围及适用政策", { exact: true })
    .click();
  await page
    .getByRole("button", { name: "确认并提交申请", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认提交", exact: true })
    .click();
  await expect(page).toHaveURL(/\/cases\//);
  const caseId = new URL(page.url()).pathname.split("/").pop();
  const confirmed = await (
    await staff.request.get(`/api/v1/staff/cases/${caseId}`)
  ).json();
  expect(confirmed.status).toBe("PENDING_REVIEW");
  await write(staff, `/staff/cases/${caseId}/review`, {
    expected_version: confirmed.version,
    decision: "approve_return",
    reason: "模型服务故障注入期间，人工初审与门户表单仍可继续。",
  });
  await page.getByRole("button", { name: "刷新进展", exact: true }).click();
  await expect(page.locator(".case-progress-banner .status")).toHaveText(
    "待寄回商品",
  );
  await page.screenshot({
    path: path.join(evidence, "m4-model-unavailable-form.png"),
    fullPage: true,
  });
  fs.writeFileSync(
    path.join(evidence, "m4-real-model-takeover.json"),
    JSON.stringify(
      {
        source:
          "Actual authorized model response held by explicit loopback test relay; employee takeover through real authenticated API; separate HTTP503 is deliberate local fault injection, not a claimed upstream outage",
        captured_at: new Date().toISOString(),
        conversation_id: incoming.conversation_id,
        held_completed_upstream_calls: held.completed_upstream_calls,
        stale_state: stale,
        post_outage_case_id: caseId,
        final_state: inspect(customer.customer_id),
        checks: {
          actual_model_response_after_takeover_discarded: "pass",
          no_stale_proposal_or_outbound: "pass",
          employee_explicit_return: "pass",
          injected_http503_public_guidance: "pass",
          customer_form_confirmation_and_staff_review_during_model_outage:
            "pass",
        },
      },
      null,
      2,
    ) + "\n",
  );
  await staffContext.close();
});
