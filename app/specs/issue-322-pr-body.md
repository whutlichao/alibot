## Summary

搜索结果里**任务命中**变成真按钮，点了在右侧 dock 打开那条任务线程。消息命中与 Agent 命中保持非交互。

判据照抄旧页面 `searchResultNode()`——**看载荷里有没有可打开的任务标识，不按 `kind` 再分派一次**：

```diff
 SearchView.vue  rows（每行预计算一次读数）
   hit → {
+    taskId: (hit.task && hit.task.id) || null,
     source, type, segments, snip
   }

 SearchView.vue  <template>
-  <div v-for="row in rows" class="result">
+  <component :is="row.taskId ? 'button' : 'div'" v-for="row in rows" class="result"
+             :type="row.taskId ? 'button' : undefined"
+             :data-opentask="row.taskId || undefined"
+             @click="openRow(row)">
     <div class="r-top">…</div> <div class="r-title">…</div> <div class="r-snip">…</div>
-  </div>
+  </component>

 App.vue
-  <SearchView :search="search" :active="searchActive" />
+  <SearchView :search="search" :active="searchActive" @open-task="openTask" />
```

`SearchView` 只抛意图，开线程的落点仍是 `App.vue` 的 `openTask()`——与看板卡片、消息流里的 chip
**同一条路径**，不是第二条。搜索留在主线视图、线程开在右侧，两者是两个维度（旧 `openTask()` 从不写
`state.mode`）。

有任务标识 ⇒ 原生 `<button>`（Tab 进、Enter / Space 由浏览器翻成 click，不自造 keydown）。
没有 ⇒ 真 `div`，**不是**禁用的 `button`：禁用按钮仍可聚焦，会让人以为能用。

### 附带的一处接线：私信下 dock 也按形态判断

开工后发现的票面未覆盖边界，已 ask 上报后由协调者裁定：

```diff
 App.vue
-const visibleDockTarget = computed(() => (isDm.value ? null : dockTarget.value));
+const visibleDockTarget = computed(() => {
+  const t = dockTarget.value;
+  if (!t || !t.kind) return null;
+  if (t.kind === 'members') return isDm.value ? null : t;
+  return t;
+});
```

判据回到旧 `dockFallbackMode()` 的原意：「成员」形态只属于频道；任务线程 / 运行面板是**独立形态**，
私信下仍算可用。不这么改，私信里搜到任务命中点下去右侧什么都不出现——正是票面要除掉的
「点了没反应的按钮」。`members` 档至今没有调用点。

另外三处「私信下不成立」的判据（`dockToggleAvailable` / 频道头页签与工具 / `channel-tools` 三个入口）
核实后**不需要改**：它们说的是频道专属入口，与 dock 能否显示一个已有形态是两件事。逐条理由见
`app/specs/issue-322-acceptance.md` §1.2。

## Evidence

**行形态**（ego-browser 实测，真实后端 + 真实数据）：

```text
消息命中  tag=DIV     opentask=null               tabindex=null  disabled=null  role=null
任务命中  tag=BUTTON  opentask=task_73bfd2ab54ea  type=button    od-id=search-task
任务命中  tag=BUTTON  opentask=task_2449532bb1e2  type=button    od-id=search-task
```

**点「设计任务甲」⇒ 右侧线程是这条任务、属于正确的 target**：

```text
dockExists=true  dockClass="dock is-open"  threadExists=true
threadTitle="设计任务甲"
threadHeadSub="任务线程#ab54ea#design · 讨论 1 条"
searchStillVisible=true                                  ← 搜索视图仍在主线
```

**键盘**（CDP 真实按键）：Tab 序列 `search-input → 全部 → 消息 → 任务 → Agent → search-task`，
消息命中行被跳过（`div` 不可聚焦）；在 `search-task` 上 Enter ⇒ `{threadExists:true, title:"设计任务甲"}`。

**消息命中行点不动**（先确认 dock 已真的关掉，排除残留）：`afterMsg.dockExists=false`。

**用例**（全部从 `mount(App)` 进，6 条新增/改写）：

```text
$ cd app/frontend && env -u NODE_ENV npx vitest run tests/search-commands.test.js
 Test Files  1 passed (1)
      Tests  36 passed (36)
```

其中一条专钉判据：`kind: 'task'` 但载荷缺 `task.id` ⇒ 仍是 `DIV`、无 `data-opentask`。

**全量门禁**：

```text
$ ./scripts/check.sh
== pytest（全量）      1271 passed, 11 skipped in 744.86s (0:12:24)
== ruff check          All checks passed!
== ruff format --check 366 files already formatted
== mypy（仓库根）      Success: no issues found in 134 source files
== pyright             0 errors, 0 warnings, 0 informations
== Vitest              Test Files 22 passed (22) / Tests 431 passed (431)
门禁全绿
```

产物由 `check.sh` 头部 `npm run build` 生成，`git status` 里看不到 `app/alibot/studio/v2/`。

## Merge Danger

**Door:** 双向

纯前端补交互：无 schema、无移除功能、端点未变、后端零改动、`app.css` 零改动。

**Blast Radius:** 小

- 搜索视图的命中行渲染（一个视图内的一个循环）；
- `App.vue` 的一处接线 + `visibleDockTarget` 的判据。后者是 dock 形态的唯一读点——但 `dockTarget`
  的写点（`openTask` / `openRunPanel` / `closeDock` / `selectTarget`）全都不读 `isDm`，所以形态判据
  仍然只在这一个读点上收口。
- 既有行为不受影响：`app-shell.test.js` 那条用例（原题「切到私信 ⇒ `#dock` 整栏消失」）照旧通过——
  它先开运行面板再切私信，`selectTarget()` 会把 `dockTarget` 置 `null`。收口时把它的**标题与注释**
  改成如实的说法（现题「切到私信 ⇒ 运行面板收起（`resetRunPanelState()` 清掉 `dockTarget`，不是
  `isDm` 一刀切）」），**断言一条未动**。

**文档改动（决策未动）**：本 PR 同时修正了 ADR-0038 §237 的一处过时事实描述——原文「私信下
`dockTarget` 恒为 `null`（整栏消失）」改成按**形态**裁决（`members` 屏蔽，`task` / `run` 照常可见），
并以「**事实修正（#322）**」标记指回旧页面 `dockFallbackMode()` 的注释。按 `review-rules` 判断题 4
（ADR 只做最小事实修正），模块划分、三档形态、「`dockTarget` 是 dock 形态唯一判据」这些决策内容
**一字未动**。

Closes #322
