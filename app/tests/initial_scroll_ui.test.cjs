/**
 * 刷新页面 / 重开浏览器后，工作台的消息流应停在**最新一条**（容器贴底）。
 *
 * 接缝沿用既有的 `approval_dom_harness.cjs`（`vm` 装载 `i18n.js` + `app.js`、`fetch` 替身、
 * DOM 替身），不新开框架。断言只落在外部可观察行为上——`#viewMessages` 的 `scrollTop`
 * 是否等于 `scrollHeight`——不断言内部函数调用次数。
 *
 * 三条判据：
 *   a) 首次 `loadWorkspace()` 成功后，消息流贴底（替身不会自己排版，因此把 `scrollHeight`
 *      设成非零值，用来区分「真的写了 scrollTop」与「什么都没做」）；
 *   b) 之后的静默刷新（`{ silent: true, keepActive: true }`，重连走的就是这条路）不得改写
 *      `scrollTop`：正在往上回看历史的用户不能被拉回底部；
 *   c) 首载失败、由重连完成的**第一次成功载入**仍属于「首次成功载入」，同样要贴底。
 */

const assert = require("node:assert/strict");
const test = require("node:test");
const { createHarness, channel, json, flush } = require("./approval_dom_harness.cjs");

const OWNER = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
// 与装置预置的成员一致：重连成功时成员集合不变，左栏行得以就地复用（替身不实现 remove()）。
const ADA = { id: "mem-1", kind: "agent", display_name: "Ada", avatar_slot: 1 };

/** 一次成功的工作台载入所需的全部读数；未列出的 URL 返回 `{}`（读坏也不影响主流程）。 */
function workspaceFetch(url) {
  if (url === "/api/settings") {
    return json({ settings: { locale: "zh-CN", message_density: "comfortable", theme: "system" } });
  }
  if (url === "/api/workspace") {
    return json({
      member: OWNER,
      members: [OWNER, ADA],
      channels: [channel()],
      dms: [],
      agents: [ADA],
      model_providers: [],
      local_providers: [],
      remote_consents: [],
    });
  }
  if (url === "/api/tasks") return json({ tasks: [] });
  if (url === "/api/reminders") return json({ reminders: [] });
  if (url === "/api/remote-data-flow") return json({ data_faces: [] });
  if (url === "/api/channels/ch-1/messages") return json({ channel: channel(), messages: [] });
  if (url === "/api/channels/ch-1/round-outcomes") return json({ round_outcomes: [] });
  if (url === "/api/channels/ch-1/members") return json({ members: [] });
  if (url === "/api/channels/ch-1/config") return json({ config: { mode: "autonomous" } });
  if (url === "/api/member-statuses") return json({ statuses: {} });
  if (url === "/api/approvals?state=pending") return json({ approvals: [] });
  return json({});
}

test("首次载入工作台后消息流贴底；静默刷新不改变回看位置", async () => {
  const harness = createHarness({ fetch: workspaceFetch });
  // 装置默认按「已经载入过」预置状态；这里还原真实页面的起点（`state.ready === false`）。
  harness.api.state.ready = false;

  const stream = harness.elementFor("#viewMessages");
  stream.scrollHeight = 640;
  stream.scrollTop = 0;

  await harness.api.loadWorkspace();
  await flush();

  assert.equal(stream.scrollTop, stream.scrollHeight, "首次载入后消息流应停在最新一条");

  // 用户往上回看历史：停在非底位置。
  stream.scrollTop = 120;
  await harness.api.loadWorkspace({ silent: true, keepActive: true });
  await flush();

  assert.equal(stream.scrollTop, 120, "静默刷新不得把正在回看的用户拉回底部");
});

test("首次载入失败后，重连完成的第一次成功载入仍贴底", async () => {
  let workspaces = 0;
  const harness = createHarness({
    fetch: (url) => {
      workspaces += url === "/api/workspace" ? 1 : 0;
      if (url === "/api/workspace" && workspaces === 1) {
        return { ok: false, status: 503, json: async () => ({ detail: "unavailable" }) };
      }
      return workspaceFetch(url);
    },
  });
  harness.api.state.ready = false;

  const stream = harness.elementFor("#viewMessages");
  stream.scrollHeight = 480;
  stream.scrollTop = 0;

  // 第一次尝试失败：消息面为空，不贴底（`scheduleReconnect` 排在下一拍）。
  // 用 `silent` 只是免掉失败 toast 的收尾定时器，失败分支本身与默认载入相同。
  await harness.api.loadWorkspace({ silent: true });
  assert.equal(stream.scrollTop, 0, "首载失败时不应滚动空消息面");

  // 重连（静默重载）成功：这是第一次真正拿到消息的载入，仍要贴底。
  await flush();
  assert.equal(workspaces, 2, "失败后应自动重连一次");
  assert.equal(stream.scrollTop, stream.scrollHeight, "重连完成的首次成功载入应贴底");
});
