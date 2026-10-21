// A06 actual integration. Only the new initial order intentionally lacks policy;
// model requests, signed callbacks, persistence and Chatwoot handoff are real.
import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import {
  root,
  evidence,
  env,
  login,
  widget,
  send,
  history,
  runtimeEvidence,
} from "./live-helpers.js";

function runFixture(script, args = []) {
  const output = execFileSync(
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
      "-",
      ...args,
    ],
    {
      cwd: root,
      encoding: "utf8",
      input: fs.readFileSync(path.join(root, "scripts", script), "utf8"),
    },
  );
  return JSON.parse(output.trim().split(/\r?\n/).pop());
}

test("A06 actual missing purchase policy forces local silence and real Chatwoot human handoff", async ({
  page,
  browser,
}) => {
  test.setTimeout(150000);
  const execution = runtimeEvidence();
  const checkedFiles = [
    "src/aftersales/agent_tools.py",
    "src/aftersales/retrieval.py",
  ];
  const workerSource = JSON.parse(
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
        "import hashlib,json,pathlib; print(json.dumps({p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in ['src/aftersales/agent_tools.py','src/aftersales/retrieval.py']}))",
      ],
      { cwd: root, encoding: "utf8" },
    ),
  );
  const hostSource = Object.fromEntries(
    checkedFiles.map((file) => [
      file,
      createHash("sha256")
        .update(fs.readFileSync(path.join(root, file)))
        .digest("hex"),
    ]),
  );
  expect(workerSource).toEqual(hostSource);
  const fixture = runFixture("seed_missing_policy_demo.py");
  await login(page, fixture.username);
  const order = await (
    await page.request.get(`/api/v1/orders/${fixture.order_id}`)
  ).json();
  expect(order.items[0].units[0].id).toBe(fixture.unit_id);
  expect(order.items[0].units[0].policy).toBeNull();
  const frame = await widget(page);
  const incoming = await send(
    page,
    frame,
    `订单 ${fixture.external_id} 中，序列号 ${fixture.serial} 的耳机持续断连，存在质量问题。我想换同款新品，请帮我查购买时适用的政策，核验能否申请，再告诉我下一步怎么办理。`,
  );
  const staffContext = await browser.newContext(),
    staff = await staffContext.newPage();
  await login(staff, "support");
  let local;
  await expect
    .poll(
      async () => {
        const response = await staff.request.get(
          `/api/v1/staff/conversations/${incoming.conversation_id}`,
        );
        if (response.status() === 404) return false;
        expect(response.status()).toBe(200);
        local = await response.json();
        return ["HANDOFF_PENDING", "HUMAN_ACTIVE"].includes(local.control_mode);
      },
      { timeout: 100000, intervals: [500, 1000, 2000] },
    )
    .toBe(true);
  let remote;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${process.env.CHATWOOT_PUBLIC_URL || "http://localhost:3000"}/api/v1/accounts/${env.CHATWOOT_ACCOUNT_ID}/conversations/${incoming.conversation_id}`,
          {
            headers: {
              Authorization: `Bearer ${env.CHATWOOT_BOT_ACCESS_TOKEN}`,
            },
          },
        );
        expect(response.status()).toBe(200);
        remote = await response.json();
        return remote.status;
      },
      { timeout: 30000 },
    )
    .toBe("open");
  expect(remote.account_id).toBe(Number(env.CHATWOOT_ACCOUNT_ID));
  expect(remote.inbox_id).toBe(Number(env.CHATWOOT_INBOX_ID));
  expect(remote.meta.hmac_verified).toBe(true);
  expect(remote.meta.sender.identifier).toBe(
    "aftersales:" + fixture.customer_id,
  );
  let state;
  await expect
    .poll(
      () => {
        state = runFixture("inspect_policy_handoff.py", [
          "--customer-id",
          fixture.customer_id,
          "--conversation-id",
          String(incoming.conversation_id),
        ]);
        return state.runs.at(-1)?.status;
      },
      { timeout: 20000 },
    )
    .toBe("STALE");
  expect(state.runs.at(-1).provider).not.toBe("mock");
  expect(state.runs.at(-1).events.some((event) => event.kind === "model")).toBe(
    true,
  );
  expect(
    state.control_audit.some((row) =>
      new RegExp(
        `^policy-review:(POLICY_NEEDS_REVIEW|POLICY_MISSING_OR_CONTRADICTORY|POLICY_RULE_SOURCE_MISSING):${fixture.customer_id}$`,
      ).test(row.actor_id),
    ),
  ).toBe(true);
  expect(state.proposals).toHaveLength(0);
  expect(state.cases).toHaveLength(0);
  expect(state.outbound).toHaveLength(0);
  expect(
    (await history(page, incoming.conversation_id)).filter(
      (message) =>
        message.id > incoming.id &&
        message.private === false &&
        message.message_type === 1 &&
        message.sender?.type === "agent_bot",
    ),
  ).toHaveLength(0);
  await staff.goto(`/staff/conversations/${incoming.conversation_id}`);
  await expect(staff.locator(".status")).toHaveText(/人工接待中|正在转人工/);
  await staff.screenshot({
    path: path.join(evidence, "a06-policy-missing-handoff.png"),
    fullPage: true,
  });
  const codeCommit = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: root,
    encoding: "utf8",
  }).trim();
  fs.writeFileSync(
    path.join(evidence, "a06-real-policy-handoff.json"),
    JSON.stringify(
      {
        source:
          "Actual configured model, isolated real Widget/Chromium, actual signed Chatwoot ingress and authoritative local/remote handoff; missing policy is declared initial fictional fixture data",
        captured_at: new Date().toISOString(),
        code_commit: codeCommit,
        runtime_budget: execution.runtime_budget,
        verified_worker_source_sha256: workerSource,
        fixture,
        incoming_message_id: incoming.id,
        conversation_id: incoming.conversation_id,
        local_control: local,
        remote: {
          status: remote.status,
          account_id: remote.account_id,
          inbox_id: remote.inbox_id,
          hmac_verified: remote.meta.hmac_verified,
        },
        state,
        checks: {
          authoritative_policy_missing: "pass",
          real_model_read: "pass",
          local_human_or_handoff_pending: "pass",
          actual_chatwoot_open: "pass",
          no_new_proposal_case_or_outbound: "pass",
        },
      },
      null,
      2,
    ) + "\n",
  );
  await staffContext.close();
});
