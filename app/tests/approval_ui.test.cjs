/**
 * #153：审批卡（规格 §4.6 第 4 条 / ADR-0021 §四）的 DOM 行为。
 *
 * 断言只落在外部可观察行为上：恢复 / 实时插入的工具名、参数摘要、成员与 600s 倒计时；
 * 允许 / 拒绝提交与幂等；过期 / 重启后 pending 消失；敏感键掩码；频道与任务线程按 target 分开。
 * 共用装置见 `approval_dom_harness.cjs`。
 */
const assert = require("node:assert/strict");
const test = require("node:test");
const {
  NOW,
  flush,
  approval,
  json,
  frame,
  createHarness,
  actionButtons,
  cardIn,
  byClass,
} = require("./approval_dom_harness.cjs");

/* ─── 1. 快照恢复 · 审批卡内容 · 倒计时 ───────────────────────────────────── */

test("频道视图从 GET /approvals?state=pending 恢复审批卡：工具名、参数摘要、成员、剩余时间", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url.startsWith("/api/approvals?")) return json({ approvals: [approval()] });
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  await flush();

  const request = harness.callsTo("/api/approvals");
  assert.equal(request.length, 1);
  assert.equal(request[0].url, "/api/approvals?target_id=ch-1&state=pending");
  assert.equal(harness.board.hidden, false);

  const card = cardIn(harness.board, "reply-1");
  assert.ok(card, "频道消息流里应出现审批卡");
  assert.equal(card.dataset.state, "pending");
  assert.match(card.textContent, /Write/);
  assert.match(card.textContent, /notes\.txt/);
  assert.match(card.textContent, /Ada/);
  assert.equal(
    byClass(card, "approval-remaining")[0].textContent,
    harness.api.t("approval.remaining", { time: "09:00" }),
  );
  assert.equal(byClass(card, "approval-state")[0].textContent, harness.api.t("approval.pending"));
});

/* ─── 2. 倒计时归零 ⇒ expired，动作消失 ─────────────────────────────────── */

test("倒计时按 requested_at + 600s 递减，归零后显示已过期且不再有允许/拒绝", async () => {
  const harness = createHarness({
    now: NOW,
    fetch: (url) => {
      if (url.startsWith("/api/approvals?")) {
        return json({ approvals: [approval({ requested_at: new Date(NOW).toISOString() })] });
      }
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  let card = cardIn(harness.board, "reply-1");
  assert.equal(
    byClass(card, "approval-remaining")[0].textContent,
    harness.api.t("approval.remaining", { time: "10:00" }),
  );

  harness.clock.now = NOW + 60_000;
  harness.api.renderApprovalSurfaces();
  card = cardIn(harness.board, "reply-1");
  assert.equal(
    byClass(card, "approval-remaining")[0].textContent,
    harness.api.t("approval.remaining", { time: "09:00" }),
  );
  assert.equal(actionButtons(card).length, 2);

  harness.clock.now = NOW + 600_000;
  harness.api.renderApprovalSurfaces();
  card = cardIn(harness.board, "reply-1");
  assert.equal(card.dataset.state, "expired");
  assert.equal(byClass(card, "approval-state")[0].textContent, harness.api.t("approval.expired"));
  assert.equal(actionButtons(card).length, 0, "过期后不得还能提交");
});

/* ─── 3. 允许 · 幂等 · resolved 后禁止重复提交 ──────────────────────────── */

test("允许提交全部 tool_call 到 POST /api/approvals/{reply_id}，进入 approved 后不再发请求", async () => {
  let resolvePost = null;
  const postCalls = [];
  const harness = createHarness({
    fetch: (url, request) => {
      if (url.startsWith("/api/approvals?")) return json({ approvals: [approval()] });
      if (url === "/api/approvals/reply-1" && request.method === "POST") {
        postCalls.push(JSON.parse(request.body));
        return new Promise((resolve) => {
          resolvePost = () =>
            resolve(json({
              approval: approval({
                state: "approved",
                decisions: [
                  { tool_call_id: "call-1", confirmed: true },
                  { tool_call_id: "call-2", confirmed: true },
                ],
                resolved_at: new Date(NOW).toISOString(),
              }),
            }));
        });
      }
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  const allow = actionButtons(harness.board).find((button) => button.dataset.approvalAction === "allow");
  assert.ok(allow);

  // 双击：提交中的第二次点击不得再发一次 POST（按钮同时进入 disabled）。
  await harness.board.dispatchEvent({ type: "click", target: allow });
  await harness.board.dispatchEvent({ type: "click", target: allow });
  await flush();

  assert.equal(postCalls.length, 1, "提交中重复点击只允许一次 POST");
  assert.deepEqual(postCalls[0], {
    decisions: [
      { tool_call_id: "call-1", confirmed: true },
      { tool_call_id: "call-2", confirmed: true },
    ],
  });
  assert.ok(actionButtons(harness.board).every((button) => button.disabled), "提交中动作禁用");

  resolvePost();
  await flush();
  await flush();

  let card = cardIn(harness.board, "reply-1");
  assert.equal(card.dataset.state, "approved");
  assert.equal(byClass(card, "approval-state")[0].textContent, harness.api.t("approval.approved"));
  assert.equal(actionButtons(card).length, 0);

  // resolved 之后即使拿旧按钮再点一次也不得重复提交。
  await harness.board.dispatchEvent({ type: "click", target: allow });
  await flush();
  assert.equal(postCalls.length, 1);
  card = cardIn(harness.board, "reply-1");
  assert.equal(card.dataset.state, "approved");
});

test("拒绝提交 confirmed=false，UI 进入 denied", async () => {
  const postCalls = [];
  const harness = createHarness({
    fetch: (url, request) => {
      if (url.startsWith("/api/approvals?")) return json({ approvals: [approval()] });
      if (url === "/api/approvals/reply-1" && request.method === "POST") {
        postCalls.push(JSON.parse(request.body));
        return json({
          approval: approval({
            state: "denied",
            decisions: [
              { tool_call_id: "call-1", confirmed: false },
              { tool_call_id: "call-2", confirmed: false },
            ],
            resolved_at: new Date(NOW).toISOString(),
          }),
        });
      }
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  const deny = actionButtons(harness.board).find((button) => button.dataset.approvalAction === "deny");
  await harness.board.dispatchEvent({ type: "click", target: deny });
  await flush();

  assert.equal(postCalls.length, 1);
  assert.deepEqual(postCalls[0], {
    decisions: [
      { tool_call_id: "call-1", confirmed: false },
      { tool_call_id: "call-2", confirmed: false },
    ],
  });
  const card = cardIn(harness.board, "reply-1");
  assert.equal(card.dataset.state, "denied");
  assert.equal(byClass(card, "approval-state")[0].textContent, harness.api.t("approval.denied"));
  assert.equal(actionButtons(card).length, 0);
});

/* ─── 4. 实时帧 · 频道与任务线程各按 target ─────────────────────────────── */

test("SSE approval_required 实时插入、approval_resolved 更新；频道与任务线程按 target 分开", async () => {
  const harness = createHarness();

  harness.api.handleStreamEvent(
    "ch-1",
    frame({
      type: "approval_required",
      reply_id: "reply-ch",
      member_id: "mem-1",
      tool_calls: [{ id: "call-ch", name: "Write", input: { path: "channel.md" } }],
    }),
    "approval_required",
  );
  await flush();

  let channelCard = cardIn(harness.board, "reply-ch");
  assert.ok(channelCard, "频道 SSE 应实时插入审批卡");
  assert.match(channelCard.textContent, /channel\.md/);

  harness.api.handleStreamEvent(
    "ch-1",
    frame({
      type: "approval_resolved",
      reply_id: "reply-ch",
      state: "approved",
      decisions: [{ tool_call_id: "call-ch", confirmed: true }],
    }),
    "approval_resolved",
  );
  await flush();
  channelCard = cardIn(harness.board, "reply-ch");
  assert.equal(channelCard.dataset.state, "approved");

  harness.startTask();
  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({
      type: "approval_required",
      reply_id: "reply-tg",
      member_id: "mem-1",
      tool_calls: [{ id: "call-tg", name: "Bash", input: { command: "npm test" } }],
    }),
    "approval_required",
  );
  await flush();

  assert.equal(harness.taskSection.hidden, false);
  const taskCard = cardIn(harness.taskList, "reply-tg");
  assert.ok(taskCard, "任务线程应显示本 task target 的审批卡");
  assert.match(taskCard.textContent, /npm test/);
  assert.equal(cardIn(harness.taskList, "reply-ch"), null, "频道卡不得串进任务线程");
  assert.equal(cardIn(harness.board, "reply-tg"), null, "任务卡不得串进频道消息流");

  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({
      type: "approval_resolved",
      reply_id: "reply-tg",
      state: "expired",
      decisions: [],
    }),
    "approval_resolved",
  );
  await flush();
  assert.equal(cardIn(harness.taskList, "reply-tg").dataset.state, "expired");
});

/* ─── 5. 敏感键掩码 · 只渲染 {id,name,input} ───────────────────────────── */

test("只渲染 {id,name,input}；api_key / password / TOKEN / Authorization 的值在 DOM 中被掩码", async () => {
  const secrets = {
    api: "sk-live-DO-NOT-LEAK",
    password: "hunter2-DO-NOT-LEAK",
    authorization: "Bearer DO-NOT-LEAK",
    env: "env-DO-NOT-LEAK",
    callLevel: "call-level-DO-NOT-LEAK",
  };
  const harness = createHarness({
    fetch: (url) => {
      if (url.startsWith("/api/approvals?")) {
        return json({
          approvals: [
            approval({
              tool_calls: [
                {
                  id: "call-1",
                  name: "Bash",
                  input: {
                    command: "npm test",
                    api_key: secrets.api,
                    nested: { password: secrets.password, Authorization: secrets.authorization },
                    env: { TOKEN: secrets.env },
                  },
                  // 后端已收窄；即便多出来的字段也绝不能进 DOM。
                  credentials: secrets.callLevel,
                },
              ],
            }),
          ],
        });
      }
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  const card = cardIn(harness.board, "reply-1");
  const text = card.textContent;
  assert.match(text, /npm test/);
  assert.match(text, /api_key=••••/);
  assert.match(text, /"password":"••••"/);
  assert.match(text, /"TOKEN":"••••"/);
  assert.match(text, /"Authorization":"••••"/);
  Object.values(secrets).forEach((secret) => {
    assert.equal(text.includes(secret), false, `凭证值不得出现在 DOM：${secret}`);
  });
});

/* ─── 6. 重启 / 失联后 pending 消失 ⇒ expired ──────────────────────────── */

test("快照里 pending 消失（进程重启把 pending 标 expired）后卡片转为 expired 且不可提交", async () => {
  let records = [approval()];
  const harness = createHarness({
    fetch: (url) => {
      if (url.startsWith("/api/approvals?")) return json({ approvals: records.slice() });
      return json({});
    },
  });

  await harness.api.loadApprovalsFor("ch-1");
  assert.equal(cardIn(harness.board, "reply-1").dataset.state, "pending");
  assert.equal(actionButtons(harness.board).length, 2);

  records = [];
  await harness.api.loadApprovalsFor("ch-1");
  await flush();

  const card = cardIn(harness.board, "reply-1");
  assert.equal(card.dataset.state, "expired");
  assert.equal(byClass(card, "approval-state")[0].textContent, harness.api.t("approval.expired"));
  assert.equal(actionButtons(card).length, 0);
});

