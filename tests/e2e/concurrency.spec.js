// SPEC15.2 C inventory portion. Every transition uses the matching real role.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { evidence, seed, login, write } from "./live-helpers.js";

test("C stock: two different customers race for the only same-SKU unit; replenish and continue", async ({
  page,
  browser,
}) => {
  test.setTimeout(120000);
  const inputs = seed().scenarios.C;
  const contextB = await browser.newContext(),
    other = await contextB.newPage();
  const staffContext = await browser.newContext(),
    staff = await staffContext.newPage();
  const adminContext = await browser.newContext(),
    admin = await adminContext.newPage();
  await login(page, inputs.customers[0].username);
  await login(other, inputs.customers[1].username);
  await login(staff, "support");
  await login(admin, "admin");
  const customers = [page, other],
    cases = [];
  for (let index = 0; index < 2; index++) {
    const customer = customers[index],
      unit = inputs.customers[index];
    const proposal = await write(customer, "/proposals", {
      order_unit_id: unit.unit_id,
      type: "exchange",
      facts: {
        reason: "真实并发演示：耳机断连，申请同 SKU 质量换货。",
        quality_issue: true,
      },
    });
    const record = await write(customer, `/proposals/${proposal.id}/confirm`, {
      expected_version: proposal.version,
      summary_hash: proposal.summary_hash,
    });
    cases.push(record.id);
    let current = await write(staff, `/staff/cases/${record.id}/review`, {
      expected_version: record.version,
      decision: "approve_return",
      reason: "真实员工初审同意寄回。",
    });
    current = await write(customer, `/cases/${record.id}/return-shipment`, {
      expected_version: current.version,
      carrier: "演示快递",
      tracking_number: "DEMO-C-" + record.id.slice(0, 8),
    });
    await write(staff, `/staff/cases/${record.id}/receive`, {
      expected_version: current.version,
      received_serial: unit.serial,
    });
  }
  expect((await page.request.get(`/api/v1/cases/${cases[1]}`)).status()).toBe(
    404,
  );
  expect((await other.request.get(`/api/v1/cases/${cases[0]}`)).status()).toBe(
    404,
  );
  const current = await Promise.all(
    cases.map(async (id) =>
      (await staff.request.get(`/api/v1/staff/cases/${id}`)).json(),
    ),
  );
  const inspected = await Promise.all(
    cases.map((id, index) =>
      write(staff, `/staff/cases/${id}/inspect`, {
        expected_version: current[index].version,
        passed: true,
        verification: {
          reason: "并发真实验收，质量问题已核实。",
          quality_issue: true,
        },
      }),
    ),
  );
  expect(
    inspected.filter((record) => record.status === "WAITING_STOCK"),
  ).toHaveLength(1);
  expect(
    inspected.filter((record) => record.status === "READY_TO_FULFILL"),
  ).toHaveLength(1);
  const inventory = (
    await (await staff.request.get("/api/v1/staff/inventory")).json()
  ).find((product) => product.product_id === inputs.product_id);
  expect(inventory.available).toBe(0);
  expect(inventory.reserved + inventory.dispatched).toBe(1);
  const waiting = inspected.find((record) => record.status === "WAITING_STOCK");
  const completedId = inspected.find(
    (record) => record.status !== "WAITING_STOCK",
  ).id;
  await expect
    .poll(
      async () =>
        (
          await (
            await staff.request.get(`/api/v1/staff/cases/${completedId}`)
          ).json()
        ).status,
      { timeout: 45000 },
    )
    .toBe("COMPLETED");
  const receiptSerial = `E2E-C-REPLENISH-${seed().run_id}`;
  await write(admin, "/admin/inventory/receipts", {
    product_id: inputs.product_id,
    serial_numbers: [receiptSerial],
    source: "真实管理员补货演示",
  });
  const refreshed = await (
    await staff.request.get(`/api/v1/staff/cases/${waiting.id}`)
  ).json();
  await write(staff, `/staff/cases/${waiting.id}/resume-stock`, {
    expected_version: refreshed.version,
  });
  await expect
    .poll(
      async () =>
        (
          await (
            await staff.request.get(`/api/v1/staff/cases/${waiting.id}`)
          ).json()
        ).status,
      { timeout: 45000 },
    )
    .toBe("COMPLETED");
  const final = await Promise.all(
    cases.map(async (id) =>
      (await staff.request.get(`/api/v1/staff/cases/${id}`)).json(),
    ),
  );
  expect(
    final.every(
      (record) =>
        record.operations.length === 1 &&
        record.operations[0].status === "SUCCEEDED",
    ),
  ).toBe(true);
  const finalInventory = (
    await (await staff.request.get("/api/v1/staff/inventory")).json()
  ).find((product) => product.product_id === inputs.product_id);
  expect(finalInventory.reserved).toBe(0);
  expect(finalInventory.dispatched).toBe(2);
  await staff.goto("/staff/inventory");
  await staff.screenshot({
    path: path.join(evidence, "scenario-c-stock.png"),
    fullPage: true,
  });
  fs.writeFileSync(
    path.join(evidence, "scenario-c-real-concurrency.json"),
    JSON.stringify(
      {
        source:
          "Two real customer sessions and employee/admin role-authorized HTTP APIs; actual PostgreSQL row locks and independent DemoProvider",
        captured_at: new Date().toISOString(),
        product_id: inputs.product_id,
        customer_ids: inputs.customers.map((customer) => customer.customer_id),
        case_ids: cases,
        initial_inspection_statuses: inspected.map((record) => record.status),
        inventory_after_race: inventory,
        final_inventory: finalInventory,
        final_cases: final,
        checks: {
          exactly_one_initial_stock_claim: "pass",
          other_customer_waiting: "pass",
          customer_scope: "pass",
          explicit_admin_receipt_and_employee_resume: "pass",
          exactly_one_shipment_per_case: "pass",
        },
      },
      null,
      2,
    ) + "\n",
  );
  await contextB.close();
  await staffContext.close();
  await adminContext.close();
});
