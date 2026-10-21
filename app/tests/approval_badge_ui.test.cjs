/**
 * #209：线程关着时的待审批外显（任务卡片 / 列表行角标 + 任务页签圆点）。
 *
 * 接缝沿用 #153 的同一套装置（`approval_dom_harness.cjs`），不新开框架。
 * 断言只落在外部可观察行为上：全量 pending 快照的请求 URL 与次数、渲染出的文本 / 元素 / aria、
 * resolved 与 600s 过期后的消失、周期刷新的有界性，以及频道 target 与任务 target 不串台。
 */
const assert = require("node:assert/strict");
const test = require("node:test");
const {
  NOW,
  flush,
  channel,
  task,
  dm,
  approval,
  json,
  frame,
  createHarness,
  cardIn,
  odId,
  periodicTick,
} = require("./approval_dom_harness.cjs");

/* ─── 7. #209 全量读数：任务卡片 / 列表行角标 ─────────────────────────── */

test("#209 全量 pending 快照（不带 target_id）驱动任务卡片与列表行「待审批 N」角标", async () => {
  const first = task();
  const second = task({ id: "t-2", target_id: "tg-2", title: "另一个任务" });
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({
          approvals: [
            approval({ reply_id: "r-1", target_id: "tg-1" }),
            approval({ reply_id: "r-2", target_id: "tg-1" }),
            // 频道 target 的 pending 也在同一份全量快照里：它不得变成任务角标。
            approval({ reply_id: "r-ch", target_id: "ch-1" }),
          ],
        });
      }
      return json({});
    },
  });
  harness.api.state.tasks = [first, second];
  harness.api.state.boardTasks = [first, second];
  harness.api.renderTaskBoard();

  periodicTick(harness);
  await flush();
  await flush();

  const requests = harness.callsTo("/api/approvals");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/approvals?state=pending");
  assert.equal(requests[0].url.includes("target_id"), false, "全量读数不得带 target_id");

  const badge = odId(harness.columns, "task-approval-badge-t-1");
  assert.ok(badge, "有待审批的任务卡片应出现角标");
  assert.equal(badge.textContent, harness.api.t("board.approvalBadge", { count: 2 }));
  assert.match(badge.textContent, /2/);
  const aria = badge.getAttribute("aria-label");
  assert.match(aria, /修一处样式/);
  assert.match(aria, /2/);
  // 卡片根是 role=button，可访问名以它为准：条数必须并进根节点的 aria 名，否则子树被覆盖。
  const cardLabel = odId(harness.columns, "task-card-t-1").getAttribute("aria-label");
  assert.match(cardLabel, /修一处样式/);
  assert.match(cardLabel, /2/);
  assert.match(cardLabel, /待审批/);
  assert.equal(odId(harness.columns, "task-approval-badge-t-2"), null, "无 pending 的卡片不渲染角标");
  // 反向不串台：任务 target 的待审批不得出现在频道消息流的 #approvalBoard。
  assert.equal(cardIn(harness.board, "r-1"), null, "任务 target 不得串进频道审批板");

  // 未变化的下一轮周期刷新不得重建看板 / 审批卡 DOM（否则会收起流转菜单、丢焦点、点击落空）。
  const cardBefore = odId(harness.columns, "task-card-t-1");
  const approvalCardBefore = cardIn(harness.board, "r-ch");
  assert.ok(approvalCardBefore);
  periodicTick(harness);
  await flush();
  await flush();
  assert.equal(odId(harness.columns, "task-card-t-1"), cardBefore, "快照未变化时不重建看板");
  assert.equal(cardIn(harness.board, "r-ch"), approvalCardBefore, "快照未变化时不重建审批卡");

  harness.api.state.taskView = "list";
  harness.api.renderTaskBoard();
  const rowBadge = odId(harness.rows, "task-approval-badge-t-1");
  assert.ok(rowBadge, "列表行应出现同一个角标");
  assert.equal(rowBadge.textContent, harness.api.t("board.approvalBadge", { count: 2 }));
  const rowLabel = odId(harness.rows, "task-row-t-1").getAttribute("aria-label");
  assert.match(rowLabel, /2/);
  assert.match(rowLabel, /待审批/);
  assert.equal(odId(harness.rows, "task-approval-badge-t-2"), null);
});

/* ─── 8. #209 任务页签圆点 ─────────────────────────────────────────────── */

test("#209 当前频道有任务 pending 时任务页签圆点出现；DM 或该频道无 pending 时不出现", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({ approvals: [approval({ reply_id: "r-1", target_id: "tg-1" })] });
      }
      return json({});
    },
  });
  harness.api.state.tasks = [task()];
  harness.api.state.boardTasks = [task()];
  periodicTick(harness);
  await flush();
  await flush();
  harness.api.renderTabs();

  assert.equal(harness.taskDot.hidden, false, "本频道有任务 pending ⇒ 圆点出现");
  const aria = harness.taskDot.getAttribute("aria-label");
  assert.match(aria, /1/);
  assert.match(aria, /等待审批/);

  const dmHarness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({ approvals: [approval({ reply_id: "r-1", target_id: "tg-1" })] });
      }
      return json({});
    },
  });
  dmHarness.api.state.dms = [dm()];
  dmHarness.api.state.activeChannelId = "dm-1";
  dmHarness.api.state.tasks = [task()];
  periodicTick(dmHarness);
  await flush();
  await flush();
  dmHarness.api.renderTabs();
  assert.equal(dmHarness.taskDot.hidden, true, "DM 下不出现任务页签圆点");

  const emptyHarness = createHarness();
  emptyHarness.api.state.tasks = [task()];
  periodicTick(emptyHarness);
  await flush();
  await flush();
  emptyHarness.api.renderTabs();
  assert.equal(emptyHarness.taskDot.hidden, true, "本频道无任务 pending ⇒ 圆点不出现");
});

/* ─── 9. #209 resolved / 越过 600s 上界 ⇒ 角标与圆点消失 ──────────────── */

test("#209 审批 resolved 后角标与圆点即时消失", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({
          approvals: [
            approval({ reply_id: "r-1", target_id: "tg-1" }),
            approval({ reply_id: "r-2", target_id: "tg-1" }),
          ],
        });
      }
      return json({});
    },
  });
  harness.startTask();
  periodicTick(harness);
  await flush();
  await flush();
  assert.ok(odId(harness.columns, "task-approval-badge-t-1"));
  assert.equal(harness.taskDot.hidden, false);

  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({ type: "approval_resolved", reply_id: "r-1", state: "approved", decisions: [] }),
    "approval_resolved",
  );
  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({ type: "approval_resolved", reply_id: "r-2", state: "denied", decisions: [] }),
    "approval_resolved",
  );
  await flush();

  assert.equal(odId(harness.columns, "task-approval-badge-t-1"), null);
  assert.equal(harness.taskDot.hidden, true);
});

test("#209 越过 600s 上界后角标与圆点在下一次渲染/刷新后消失", async () => {
  const harness = createHarness({
    now: NOW,
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({
          approvals: [
            approval({ reply_id: "r-1", target_id: "tg-1", requested_at: new Date(NOW).toISOString() }),
          ],
        });
      }
      return json({});
    },
  });
  // 线程**关着**（activeTask = null）：两张审批列表里都没有卡，600s 过期只能由派生集合驱动。
  harness.api.state.tasks = [task()];
  harness.api.state.boardTasks = [task()];
  harness.api.renderTaskBoard();
  periodicTick(harness);
  await flush();
  await flush();
  assert.equal(cardIn(harness.taskList, "r-1"), null, "线程关着时不应挂审批卡");
  assert.ok(odId(harness.columns, "task-approval-badge-t-1"));
  assert.equal(harness.taskDot.hidden, false);

  harness.clock.now = NOW + 600_000;
  // 本地上界由既有 1s 倒计时探测：呈现态一变，角标/圆点在同一个出口消失，不必等下一轮 12s 轮询。
  const countdown = harness.intervalFns.find((item) => item.ms === 1000);
  assert.ok(countdown, "pending 存在时应注册 1s 倒计时");
  countdown.fn();

  assert.equal(odId(harness.columns, "task-approval-badge-t-1"), null);
  assert.equal(harness.taskDot.hidden, true);
});

/* ─── 10. #209 周期刷新有界；离线 / 隐藏跳过；在途不叠加 ───────────────── */

test("#209 周期刷新：在线可见时一轮恰好一次请求；离线 / 隐藏跳过；前一轮未返回不叠加", async () => {
  let snapshotCalls = 0;
  let hold = false;
  const releases = [];
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        snapshotCalls += 1;
        if (!hold) return json({ approvals: [] });
        return new Promise((resolve) => {
          releases.push(() => resolve(json({ approvals: [] })));
        });
      }
      return json({});
    },
  });

  hold = true;
  periodicTick(harness);
  periodicTick(harness);
  await flush();
  assert.equal(snapshotCalls, 1, "前一轮未返回时不得叠加第二个请求");
  releases[0]();
  await flush();
  await flush();
  hold = false;

  harness.api.state.online = false;
  periodicTick(harness);
  await flush();
  assert.equal(snapshotCalls, 1, "离线时跳过本轮");

  harness.api.state.online = true;
  harness.document.hidden = true;
  periodicTick(harness);
  await flush();
  assert.equal(snapshotCalls, 1, "页面隐藏时跳过本轮");

  harness.document.hidden = false;
  periodicTick(harness);
  await flush();
  await flush();
  assert.equal(snapshotCalls, 2, "在线可见时一轮恰好一次请求");
});

/* ─── 11. #209 频道 target 不串到任务面 ───────────────────────────────── */

test("#209 频道 target 的 pending 只属于 #approvalBoard，不产生任务角标或页签圆点", async () => {
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/approvals?state=pending") {
        return json({ approvals: [approval({ reply_id: "r-ch", target_id: "ch-1" })] });
      }
      return json({});
    },
  });
  harness.startTask();
  harness.api.render();
  periodicTick(harness);
  await flush();
  await flush();

  assert.ok(cardIn(harness.board, "r-ch"), "频道 target 的审批卡仍显示在消息流");
  assert.equal(odId(harness.columns, "task-approval-badge-t-1"), null);
  assert.equal(harness.taskDot.hidden, true);
});

/* ─── 12. #209 live 帧即时更新角标与圆点 ──────────────────────────────── */

test("#209 任务线程 live approval_required / approval_resolved 即时更新角标与圆点", async () => {
  const harness = createHarness();
  harness.startTask();
  harness.api.renderTaskBoard();
  harness.api.renderTabs();
  assert.equal(odId(harness.columns, "task-approval-badge-t-1"), null);
  assert.equal(harness.taskDot.hidden, true);

  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({
      type: "approval_required",
      reply_id: "r-live",
      member_id: "mem-1",
      tool_calls: [{ id: "call-live", name: "Bash", input: { command: "npm test" } }],
    }),
    "approval_required",
  );
  await flush();

  const badge = odId(harness.columns, "task-approval-badge-t-1");
  assert.ok(badge, "live 帧应即时生成任务角标");
  assert.equal(badge.textContent, harness.api.t("board.approvalBadge", { count: 1 }));
  assert.equal(harness.taskDot.hidden, false);

  harness.api.handleTaskStreamEvent(
    "t-1",
    frame({ type: "approval_resolved", reply_id: "r-live", state: "denied", decisions: [] }),
    "approval_resolved",
  );
  await flush();

  assert.equal(odId(harness.columns, "task-approval-badge-t-1"), null);
  assert.equal(harness.taskDot.hidden, true);
});

/* ─── 13. #209 进入工作区的真实入口 ───────────────────────────────────── */

test("#209 进入工作区（loadWorkspace）时读一次全量 pending 并渲染任务角标", async () => {
  const owner = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
  const harness = createHarness({
    fetch: (url) => {
      if (url === "/api/settings") {
        return json({ settings: { locale: "zh-CN", message_density: "comfortable", theme: "system" } });
      }
      if (url === "/api/workspace") {
        return json({
          member: owner,
          members: [owner],
          channels: [channel()],
          dms: [],
          agents: [],
          model_providers: [],
          local_providers: [],
          remote_consents: [],
        });
      }
      if (url === "/api/tasks") return json({ tasks: [task()] });
      if (url === "/api/reminders") return json({ reminders: [] });
      if (url === "/api/remote-data-flow") return json({ data_faces: [] });
      if (url === "/api/channels/ch-1/messages") return json({ channel: channel(), messages: [] });
      if (url === "/api/channels/ch-1/round-outcomes") return json({ round_outcomes: [] });
      if (url === "/api/channels/ch-1/members") return json({ members: [] });
      if (url === "/api/channels/ch-1/config") return json({ config: { mode: "autonomous" } });
      if (url === "/api/member-statuses") return json({ statuses: {} });
      if (url === "/api/approvals?state=pending") {
        return json({
          approvals: [
            approval({ reply_id: "r-boot", target_id: "tg-1" }),
            approval({ reply_id: "r-boot-ch", target_id: "ch-1" }),
          ],
        });
      }
      return json({});
    },
  });

  await harness.api.loadWorkspace({ silent: true });

  const requests = harness.callsTo("/api/approvals");
  assert.equal(requests.length, 1, "进入工作区只读一次全量 pending（不再按 target 另读一次）");
  assert.equal(requests[0].url, "/api/approvals?state=pending");
  assert.equal(requests[0].url.includes("target_id"), false);
  assert.ok(odId(harness.columns, "task-approval-badge-t-1"), "全量读数后任务卡片应有角标");
  // 同一份全量读数也恢复既有频道审批卡：删掉按 target 的入口不会让频道消息流回退。
  assert.ok(cardIn(harness.board, "r-boot-ch"), "频道审批卡应由全量读数恢复");
});
