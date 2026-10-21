/**
 * #221：任务线程（右侧 dock）呈现——「关联消息」区块并入「讨论」后的回归用例。
 *
 * 接缝沿用既有那一套（`vm` + `fetch` 替身 + DOM 替身；装置由 `approval_dom_harness.cjs`
 * 提供，它已经导出 `renderTaskThread`），不新开第二套前端测试框架、不新开 seam。
 *
 * 断言落在用户可见面：
 *
 * 1. 头部副行是「#频道 · 讨论 N 条」，N 与「讨论 · N 条」标题同源（非 system 消息数）；
 *    未读后缀保持原样；
 * 2. 「讨论」渲染全部非 system 消息；带 `task_ref` 的用户消息在**整条线程**里只出现一次
 *    （不再「关联消息」一份 +「讨论」一份），且仍挂 `.task-ref-chip`（既有语义，不新增/删除）；
 * 3. 原「关联消息」区块不存在。这一条必须读**源码正本**而不是替身：替身按选择器按需创建节点，
 *    天生读不到「这个 id 已经不在标记里」；`index.html` 的结构声明与 `app.js` 的渲染入口才是
 *    区块存在与否的正本（`i18n_ui.test.cjs` 读源有同样的先例）；
 * 4. 词条：`task.threadHeadSub` 两份都带 `{channel}` / `{count}`；`task.linkedEmpty` /
 *    `dock.linked` 已删除（全键参数位比对由 `i18n_ui.test.cjs` 机械守住，这里只钉本票的键）。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { byClass, createHarness } = require("./approval_dom_harness.cjs");

const STUDIO_DIR = path.join(__dirname, "../alibot/studio");
const OWNER = { id: "owner", kind: "owner", display_name: "owner", avatar_slot: 4 };
const ADA = { id: "mem-1", kind: "agent", display_name: "Ada", avatar_slot: 1 };

function readStudio(name) {
  return fs.readFileSync(path.join(STUDIO_DIR, name), "utf8");
}

/** 一条任务消息流里的行（`GET /api/tasks/{id}/messages` 的项形状）。 */
function threadMessage(seq, body, overrides) {
  return Object.assign(
    {
      id: "msg_" + seq,
      seq,
      kind: "agent",
      body,
      created_at: "2026-10-21T09:0" + (seq % 10) + ":00Z",
      mention_snapshot: [],
      task_ref: null,
      attachments: [],
      author: OWNER,
    },
    overrides || {},
  );
}

function occurrences(text, part) {
  return String(text).split(part).length - 1;
}

/** 打开线程并铺上给定的消息流（`renderTaskThread` 是既有的渲染入口）。

    标题显式给出：`.task-ref-chip` 的文案取自任务快照的标题，不依赖装置里的默认值。 */
function openThread(harness, options) {
  const config = options || {};
  harness.startTask({
    id: "t-1",
    title: "整理发布清单",
    channel_name: "设计系统重构",
    unread_count: config.unread || 0,
  });
  harness.api.state.taskMessages = config.messages || [];
  harness.api.renderTaskThread();
  return {
    headSub: harness.elementFor("#ttHeadSub").textContent,
    replyTitle: harness.elementFor("#ttReplyTitle").textContent,
    replies: harness.elementFor("#ttReplies"),
    history: harness.elementFor("#ttHistory"),
  };
}

/** 真实页面里 `#ttLinked` 与 `#ttReplies` 是同一段 `.tt-scroll` 的兄弟节点；替身把它们按需
    创建成互不相连的节点，这里挂到同一个父节点下，于是「整条线程里这条正文出现几次」读的
    就是同一件事（基线 61ef770：关联消息 1 + 讨论 1 = 2）。 */
function threadScroll(harness) {
  const scroll = harness.document.createElement("div");
  scroll.appendChild(harness.elementFor("#ttLinked"));
  scroll.appendChild(harness.elementFor("#ttReplies"));
  return scroll;
}

/* ─── 1. 头部副行：讨论条数与「讨论」标题同一计数 ─────────────────────────── */

test("任务线程头部副行显示「#频道 · 讨论 N 条」，N = 非 system 消息数", () => {
  const harness = createHarness();
  const view = openThread(harness, {
    messages: [
      threadMessage(1, "任务已创建", { kind: "system", author: OWNER }),
      threadMessage(2, "第一条讨论"),
      threadMessage(3, "第二条讨论", { author: ADA }),
      threadMessage(4, "指派给 Ada", { kind: "system" }),
    ],
  });

  assert.equal(view.headSub, "#设计系统重构 · 讨论 2 条");
  assert.equal(view.replyTitle, harness.api.t("task.discussionCount", { count: 2 }));
  assert.equal(view.replies.children.length, 2, "「讨论」只渲染非 system 消息");
  assert.equal(view.history.children.length, 2, "「状态变更记录」仍渲染 system 行");
});

test("未读后缀保持原样，跟在讨论条数之后", () => {
  const harness = createHarness();
  const view = openThread(harness, {
    unread: 3,
    messages: [threadMessage(1, "一条讨论")],
  });

  assert.equal(view.headSub, "#设计系统重构 · 讨论 1 条 · 3 条未读");
});

/* ─── 2. 讨论：带 task_ref 的消息只出现一次且仍带 chip ───────────────────── */

test("带 task_ref 的用户消息在整条线程里只出现一次，且仍显示既有 .task-ref-chip", () => {
  const harness = createHarness();
  const body = "这条回复带了指针";
  const view = openThread(harness, {
    messages: [
      threadMessage(1, "任务已创建", { kind: "system" }),
      threadMessage(2, body, { kind: "owner", task_ref: "t-1" }),
      threadMessage(3, "没有指针的回复", { author: ADA }),
    ],
  });

  assert.equal(view.replies.children.length, 2);
  assert.equal(
    occurrences(threadScroll(harness).textContent, body),
    1,
    "整条线程（关联消息 + 讨论）里这条正文只出现一次",
  );
  assert.equal(occurrences(view.replies.textContent, body), 1, "讨论里有且只有一份");

  const chips = byClass(view.replies, "task-ref-chip");
  assert.equal(chips.length, 1, "带 task_ref 的那一行有一个任务 chip");
  assert.equal(chips[0].dataset.openTask, "t-1", "chip 仍指向该任务（既有语义）");
  assert.match(chips[0].textContent, /整理发布清单/, "chip 文案取自任务标题");
});

/* ─── 3. 「关联消息」区块不存在（静态正本扫描） ──────────────────────────── */

test("「关联消息」区块从三件套里移除：没有 #ttLinked / .tt-linked / linkedTaskMessages", () => {
  const leftovers = [
    ["index.html", /ttLinked|dock\.linked/],
    ["app.js", /ttLinked|linkedTaskMessages|task\.linkedEmpty|dock\.linked/],
    ["app.css", /tt-linked|ttLinked/],
  ];
  for (const [file, pattern] of leftovers) {
    const match = pattern.exec(readStudio(file));
    assert.equal(
      match,
      null,
      file + " 里不应再有「关联消息」区块的残留（命中：" + (match ? match[0] : "无") + "）",
    );
  }
});

/* ─── 4. 词条：参数位一致、不再使用的键已删除 ───────────────────────────── */

test("词条：task.threadHeadSub 两份参数位一致；task.linkedEmpty / dock.linked 已删除", () => {
  const harness = createHarness();
  const zh = harness.catalogs["zh-CN"];
  const en = harness.catalogs.en;

  for (const text of [zh["task.threadHeadSub"], en["task.threadHeadSub"]]) {
    assert.match(text, /\{channel\}/);
    assert.match(text, /\{count\}/);
  }
  assert.match(zh["task.threadHeadSub"], /讨论/);
  assert.match(en["task.threadHeadSub"], /discussion/i);

  for (const key of ["task.linkedEmpty", "dock.linked"]) {
    assert.equal(Object.prototype.hasOwnProperty.call(zh, key), false, key + " 已从 zh-CN 删除");
    assert.equal(Object.prototype.hasOwnProperty.call(en, key), false, key + " 已从 en 删除");
  }
});
