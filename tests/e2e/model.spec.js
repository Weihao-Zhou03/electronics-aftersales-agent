// Actual configured model, Chatwoot and portal. There is no mocked answer/fallback.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {
  evidence,
  seed,
  login,
  write,
  widget,
  send,
  history,
  botReply,
  inspect,
  staffReceiveInspect,
  runtimeEvidence,
} from "./live-helpers.js";

test("A: real model clarifies, cites purchase policy, prepares preview, then customer and employee explicitly act", async ({
  page,
  browser,
}) => {
  test.setTimeout(300000);
  const execution = runtimeEvidence();
  const customer = seed().scenarios.A.customers[0];
  fs.mkdirSync(evidence, { recursive: true });
  await login(page, customer.username);
  const frame = await widget(page);
  const first = await send(
    page,
    frame,
    `我的耳机订单 ${customer.external_id} 想办理售后，可以帮我吗？`,
  );
  const clarification = await botReply(page, first);
  expect(clarification.content).not.toMatch(
    /M0 固定回复|MockProvider|模型服务暂不可用/,
  );
  expect(inspect(customer.customer_id).proposals).toHaveLength(0);
  const second = await send(
    page,
    frame,
    `这台设备序列号 ${customer.serial}，耳机一直断连，存在质量问题。我申请同一 SKU 质量换货；请查询购买时适用政策，核验条件和库存，准备需要我在门户明确确认的申请预览。不要替我确认提交。`,
  );
  const reply = await botReply(page, second);
  expect(reply.content).toContain("demo_policy_v1");
  expect(reply.content).toContain("exchange_eligibility");
  expect(reply.content).not.toMatch(
    /M0 固定回复|MockProvider|模型服务暂不可用/,
  );
  const proposalId = reply.content.match(/\/proposals\/([a-f0-9-]{36})/)?.[1];
  expect(proposalId).toBeTruthy();
  const before = inspect(customer.customer_id);
  expect(before.cases).toHaveLength(0);
  expect(before.runs.at(-1).status).toBe("SUCCEEDED");
  expect(before.runs.at(-1).provider).not.toBe("mock");
  expect(
    before.runs
      .at(-1)
      .events.some(
        (event) => event.tool === "search_policy" && event.result.ok === true,
      ),
  ).toBe(true);
  expect(before.proposals).toContainEqual({
    id: proposalId,
    confirmed_case_id: null,
    revoked: false,
  });
  // Click the actual rendered Widget link: a relative Chatwoot-origin URL fails.
  const link = frame.locator(`a[href*="/proposals/${proposalId}"]`).last();
  await expect(link).toBeVisible();
  const href = await link.getAttribute("href");
  expect(new URL(href).origin).toBe(new URL(page.url()).origin);
  const opened = page.waitForEvent("popup");
  await link.click();
  const preview = await opened;
  await expect(preview).toHaveURL(new RegExp(`/proposals/${proposalId}$`));
  await expect(
    preview.getByRole("heading", { name: "这次申请，将按以下内容受理。" }),
  ).toBeVisible();
  expect(
    await (await preview.request.get("/api/v1/cases")).json(),
  ).toHaveLength(0);
  await preview
    .getByText("我已核对设备、原因、金额/换货范围及适用政策", { exact: true })
    .click();
  await preview
    .getByRole("button", { name: "确认并提交申请", exact: true })
    .click();
  await preview
    .getByRole("dialog")
    .getByRole("button", { name: "确认提交", exact: true })
    .click();
  await expect(preview).toHaveURL(/\/cases\//);
  const caseId = new URL(preview.url()).pathname.split("/").pop();
  const record = await (
    await preview.request.get(`/api/v1/cases/${caseId}`)
  ).json();
  expect(record.status).toBe("PENDING_REVIEW");
  expect(record.conversation_id).toBe(second.conversation_id);
  await preview
    .getByRole("link", { name: `查看来源会话 #${second.conversation_id}` })
    .click();
  await expect(
    preview.getByRole("heading", { name: "最近公开消息", exact: true }),
  ).toBeVisible();
  await expect(
    preview.getByRole("link", { name: record.number, exact: true }),
  ).toBeVisible();
  const customerHistory = await (
    await preview.request.get(`/api/v1/conversations/${second.conversation_id}`)
  ).json();
  expect(customerHistory.history_scope).toBe("latest_page");
  expect(
    customerHistory.messages.every(
      (message) =>
        !Object.hasOwn(message, "sender") && !Object.hasOwn(message, "private"),
    ),
  ).toBe(true);
  const staffContext = await browser.newContext(),
    staff = await staffContext.newPage();
  await login(staff, "support");
  await staff.goto(`/staff/conversations/${second.conversation_id}`);
  await expect(
    staff.getByRole("link", { name: record.number, exact: true }),
  ).toBeVisible();
  // A private notification must reach Chatwoot, without appearing in customer history.
  let note;
  await expect
    .poll(
      async () => {
        note = (await history(page, second.conversation_id)).find(
          (message) =>
            message.private === true && message.content?.includes(caseId),
        );
        return Boolean(note);
      },
      { timeout: 30000 },
    )
    .toBe(true);
  const visible = await (
    await preview.request.get(`/api/v1/conversations/${second.conversation_id}`)
  ).json();
  expect(JSON.stringify(visible)).not.toContain(note.content);
  await expect(frame.getByText(note.content, { exact: true })).toHaveCount(0);
  await staffReceiveInspect(staff, preview, caseId, customer.serial);
  let completed;
  await expect
    .poll(
      async () => {
        completed = await (
          await preview.request.get(`/api/v1/cases/${caseId}`)
        ).json();
        return completed.status;
      },
      { timeout: 45000 },
    )
    .toBe("COMPLETED");
  expect(completed.operations).toHaveLength(1);
  expect(completed.operations[0].kind).toBe("shipment");
  expect(completed.operations[0].status).toBe("SUCCEEDED");
  expect(completed.operations[0].result.tracking_number).toBeTruthy();
  await preview.goto(`/cases/${caseId}`);
  await expect(
    preview.getByText("模拟履约 · 模拟换货已发出", { exact: true }),
  ).toBeVisible();
  await preview.screenshot({
    path: path.join(evidence, "scenario-a-exchange.png"),
    fullPage: true,
  });
  await page.screenshot({
    path: path.join(evidence, "m3-real-model-widget.png"),
    fullPage: true,
  });
  fs.writeFileSync(
    path.join(evidence, "scenario-a-real-model.json"),
    JSON.stringify(
      {
        source:
          "Actual configured model -> real Chatwoot Widget -> portal confirmation -> role-authorized employee APIs -> independent DemoProvider",
        captured_at: new Date().toISOString(),
        ...execution,
        conversation_id: second.conversation_id,
        case_id: caseId,
        clarification_message_id: clarification.id,
        proposal_message_id: reply.id,
        actual_portal_link: href,
        initial_no_case: true,
        private_notification_hidden: true,
        latest_page_history_only: true,
        state: inspect(customer.customer_id),
        operation: completed.operations[0],
        checks: {
          model_policy_citation: "pass",
          actual_widget_link: "pass",
          explicit_customer_confirmation: "pass",
          source_conversation_bidirectional_links: "pass",
          staff_private_notification: "pass",
          simulated_exchange_completed: "pass",
        },
      },
      null,
      2,
    ) + "\n",
  );
  await staffContext.close();
});
