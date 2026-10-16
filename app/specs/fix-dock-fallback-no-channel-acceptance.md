# 验收取证：无频道时关闭 Agent 详情 / 任务线程收起 dock，不残留成员面板

**票型**：实施票（缺陷修复 + 回归测试）｜**门禁**：G-impl、G-merge（合并由 coordinator 执行）
**Ticket**：本票**没有**对应的 GitHub issue，也**不新建**——用户裁定：交付件用描述性命名
（`app/specs/fix-dock-fallback-no-channel-*`）、不关联 issue、PR 正文不带 issue 关联，
沿用 `app/specs/fix-sse-target-fail-fast-*` 的先例。
**base**：`d7ba183`（= 派活时 main / origin/main）｜**branch**：`fix-dock-fallback-no-channel`
**代码提交**：`c31f6de`（修复 + 回归测试）、`32614fe`（双轴 review 收口）、
`b75a83d`（coordinator follow-up：落点规则只在确实关闭形态时生效 + 导航用例）；本文件与 PR 正文随后续提交入 PR，
**最终 head 以远端分支与 PR head 读数为准**（不在此写死，避免自指漂移，沿 `fix-sse-target-fail-fast` 先例）
**采集时间**：2026-10-16｜**环境**：macOS（aarch64），Node（`node --test`，无需 npm 依赖），
Python 3.11.13（仓库根 `.venv` 的解释器）
**改动面**：`app/alibot/studio/app.js`（+45 / −6）、`app/tests/agent_detail_ui.test.cjs`（+167 / −3）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**实际跑出来的结果**。红证据在**隔离的基线副本**
（`/tmp/dock-red` = `main` 版本、`/tmp/dock-nav-red` = `32614fe` 版本，两份副本的 `app.js` 都逐字节
等于目标 revision，见 §4）上取得，**不动**本 worktree 的工作区，也不碰用户正在跑的 8788 实例与它的数据目录。

---

## 0. 前置 skill 执行证据（`diagnosing-bugs`）

前置 skill：`/Users/apple/.agents/skills/diagnosing-bugs/SKILL.md`（仓库外的全局 skill），六阶段实跑。

| Phase | 要求 | 本票实际做的事 |
| --- | --- | --- |
| 1 构造反馈回路 | 一条能**在本 bug 上变红**、确定、快、可无人值守运行的命令 | **回归级**（进仓、快、确定）：`node --test app/tests/agent_detail_ui.test.cjs` —— 沿用该文件既有的 `vm` + 替身节点接缝，把用户症状（「再点关闭后右侧残留成员面板」）压成两条断言：`#dock.hidden === true` 且 `!#dock.classList.contains('is-open')`。首跑即红（§2/§4）。**装置级**：真实浏览器 + 真后端（§6.4） |
| 1 收紧回路 | 更快、信号更锐、更确定 | 单文件 ~0.2s、无网络无并发、只用 `node:test` + 内置模块；断言落在**外部可观察**的 `hidden` / `.is-open` / 形态容器的 `hidden` 上，不读应用内部布尔 |
| 2 复现 + 最小化 | 症状与用户描述一致；逐项砍到每一步都 load-bearing | 最小化见 §2.2：只保留「没有频道」（`channels: []` + `activeChannelId: null`）这一个条件，症状即复现 |
| 3 假设 | 先列 3–5 条可证伪假设再验证 | §2.3，5 条，逐条给证伪预测与实测结论（第 5 条由 coordinator 预审的反例提出） |
| 4 仪器化 | 每个探针映射到一条具体预测；一次只改一个变量 | 探针 = **真实浏览器**里对 `#dock` 的 `hidden` / `.is-open` / `getComputedStyle().display` / `getBoundingClientRect().width` 读数（§6.4）。一次只改一个变量：先只改 `closeAgentDetail()` 看 (a)/(b) 转绿，再确认 `closeTaskDock()` 走同一判据。无 `[DEBUG-` 插桩留存 |
| 5 修复 + 回归测试 | 先写测试看它红，再修，再看它绿 | TDD 顺序见 §3；红/绿证据见 §4 |
| 6 清理 | 原始回路不再复现；回归测试通过；插桩清除；一次性原型删除或标记 | §5：装置级回路由「残留面板」转为「右侧不可见」；回归测试全绿；无插桩；临时副本在 `/tmp` 下、明确不入仓 |

### 0.2 与票面/前置 skill 的冲突处置

前置 skill 与技术事实无冲突：Phase 1「先造回路、不许先立假设」正是本票的做法（先写红测试，再动实现）。
票面把目标文件与改动范围写得足够精确（R1–R5 逐条），因此没有出现「票面与实测不符」的情况。

### 0.3 coordinator follow-up 的收敛（§12 有完整回执）

coordinator 预审 diff 后发来 follow-up（`msg_7d56e59ccfa2`）：新规则不得改变**导航入口**
（切搜索 / 切看板 / 切消息流）的既有行为。本票按它的要求把规则限制在「这次确实在关闭某个形态」时，
并补了一条外部可观察断言（§3.2 用例 g、§4 的第三栏红证据）。该收敛落在提交 `b75a83d`。

---

## 1. 根因

`app/alibot/studio/app.js` 里两条关闭路径各自**各写一份**落点推导，且这份推导只认「形态」不认「有没有
频道」：

```text
closeAgentDetail()                       closeTaskDock()
  state.activeAgentId = null               state.activeTaskId = null
  setDockMode(activeTaskId                 setDockMode(activeAgentId
    ? 'task' : 'agent')                      ? 'detail' : 'agent')
```

`setDockMode('agent')` = 把 `#dockAgent`（成员面板）设为可见形态；它**不会**因为
`activeChannel()` 为 `null` 而收起 dock。`setDockMode()` 只在 `isTask || isDetail` 时才
`setDockOpen(true)`，但**没有任何分支会 `setDockOpen(false)`** —— 于是没有频道时，关闭详情之后
dock 仍然开着、成员面板仍是可见形态，`renderDock()` 的空态文案「先打开一个频道。」就留在了右侧。
这就是用户 2026-10-16 报告的残留（改前现状）：

```text
无频道 → 点左栏 Agent 行（setDockMode('detail', true) 打开 dock）
        → 点 #btnAgentDetailClose
        → setDockMode('agent')  ← dock 依旧 hidden=false、.is-open 依旧在
        → 右侧留下「频道成员 + 先打开一个频道。」
```

**「当前有打开的频道」的唯一判据**是既有的 `activeChannel()`（`state.channels` 里 id 等于
`state.activeChannelId` 的那一项），因此「没有任何 target」「只打开着 DM」「`activeChannelId` 指向一个
已经不存在的频道」三种情况都要落到「没有频道」。

**另一条必须同时看清的事实**：`closeTaskDock()` 不只有 `#btnThreadClose`（任务线程的关闭按钮）一个调用点，
`showSearchView()` / `showTaskBoard()` / `showMessagesView()`（切搜索 / 切看板 / 切消息流，即 ESC / M / T /
命令面板）也调它——那三个调用点只是「顺手确保线程关着」，**并没有人在关 dock 里的东西**，它们改前的行为是
`setDockMode(activeAgentId ? 'detail' : 'agent')`，**不碰 dock 的开合**（桌面 dock 是常驻列，ESC 不管它）。
因此「有频道 → 频道成员；否则收起」这条新规则必须只在「这次确实在关一条线程」时生效（§12）。

---

## 2. `diagnosing-bugs` Phase 1–4 取证

### 2.1 Phase 1：反馈回路

**回归级回路**（进仓，同一缺陷，快且确定）：

```sh
$ node --test app/tests/agent_detail_ui.test.cjs
```

在同一条命令里，改动前（`main` 的 `app.js`）红、改动后绿（§4 三栏输出）。回路检查单：

- **红 capable** ✅：它驱动真实缺陷路径（点左栏 Agent 行 → `closeAgentDetail()`），断言的是**用户的确切
  症状**（`#dock` 收起 + 不带 `.is-open`），不是「没抛异常」。
- **确定** ✅：重复 3 次同判（无时间、无随机、无网络）。
- **快** ✅：单文件 ~0.15s（§4 的 `duration_ms` 165.05 / 153.59）。
- **可无人值守** ✅：`node:test`，无人工步骤。

**装置级回路**（最贴近事故）：真实后端 + 真实浏览器（§6.4 给出 URL、端口、数据目录、两张截图）。

### 2.2 Phase 2：最小化（每一步都 load-bearing）

| 元素 | 砍掉后会怎样 | 结论 |
| --- | --- | --- |
| **没有频道**（`channels: []` + `activeChannelId: null`） | 有频道时落点本来就是成员面板，用例恒绿 | **load-bearing**（唯一触发条件） |
| 点左栏 Agent 行（而不是直接调 `closeAgentDetail()`） | 少一步「打开」就无从谈「关闭后的残留」；`clickRow` 走的是真实事件委托路径 | 保留（症状保真） |
| `state.activeAgentId` 在关闭后被清空 | 不断言它就可能出现「形态清了、id 还在」的漂移 | 保留 |
| 断言 `#dock.classList.contains('is-open')` | 只断言 `hidden` 会漏掉「`hidden` 与 `.is-open` 不同进同出」这一类回归（票面把两者都写进契约） | 保留 |
| 只打开着 DM / `activeChannelId` 指向已不存在的频道 | 这两条是同一判据的边界，砍掉后「DM 也算频道」这类误读无人挡 | 保留（同一判据的两条边界） |
| `closeTaskDock()` 的「非关闭」调用点（切视图） | 砍掉这条断言后，导航动作会在无频道时收起人显式打开的面板（coordinator 预审发现的行为差异） | 保留（用例 g，§12） |

### 2.3 Phase 3：五条可证伪假设（先列后验）

| # | 假设 | 证伪预测 | 实测结论 |
| --- | --- | --- | --- |
| 1 | `closeAgentDetail()` 的落点无条件落到 `'agent'`，**不看有没有频道** | 若成立：无频道时点关闭应仍见成员面板；把落点改成「有频道才 `agent`，否则 `setDockOpen(false)`」后应转绿 | **成立**（§4：改前 4 条红 → 改后全绿；(c) 有频道那条改前改后都绿） |
| 2 | 是 `renderDock()` 在无频道时残留内容（空态渲染本身有 bug） | 若成立：不点关闭、只重新渲染也会出现残留 | **否**：残留来自「dock 被留在打开状态」，不是 `renderDock()` 的内容；票面 Constraint 也要求**保留**空态渲染（显式打开时仍要看到「先打开一个频道。」） |
| 3 | 是 DM 被当成了频道（`activeTarget()` 含 DM） | 若成立：只打开 DM 时点关闭会回到成员面板 | **否**：`activeTarget()` 确实含 DM，但它不是本票的判据；本票按票面只认 `activeChannel()` ⇒ DM 落到「收起」（§4 的 (b) 用例） |
| 4 | `closeTaskDock()` 是另一条独立路径，需要单独修 | 若成立：只改 `closeAgentDetail()` 时，`closeTaskDock()` 的无频道用例仍红 | **成立**：§4 的第 4 条红用例就是 `closeTaskDock()`；因此两条路径共用同一份判据（票面 R2） |
| 5 | `closeTaskDock()` 的**所有**调用点都应该套用新规则 | 若成立：切搜索 / 看板 / 消息流在「没有东西在被关闭」时也会收起 dock，且这是想要的 | **否**（coordinator 预审提出的反例）：那三个导航入口改前不碰 dock 的开合；把新规则也套上去会让「人显式打开的面板被 ESC / M 收掉」。处置：只有 `hadThread`（这次确实在关一条线程）才走新规则（§12） |

排序依据：假设 1 直接解释「为什么关闭后还留着面板」；假设 4 决定改动面是否必须跨两条路径；
假设 5 决定新规则的**生效条件**（不是落点本身）。

### 2.4 Phase 4：仪器化

探针 = 真实浏览器对 `#dock` 的四处读数（`hidden` / `.is-open` / `computeStyle().display` /
`getBoundingClientRect()`），映射到假设 1 与 2：

- `#dock` 的 `display` 与 `width` → 映射假设 1：「面板内容还在 DOM 里」与「它在屏幕右侧可见」是两件事，
  取证必须读**渲染结果**。
- `#dockMembers` 自身的 `getBoundingClientRect()` 与 `document.body.innerText` → 映射假设 2：空态文案
  即使仍在 `#dockMembers` 的 DOM 里，只要 dock 收起就**不可见**、也不该出现在可读文本里。

**一次只改一个变量**：先只把 `closeAgentDetail()` 的落点改成共用判据，确认 (a)/(b) 转绿、
(c) 仍绿；再把 `closeTaskDock()` 接上同一判据，确认第 4 条转绿；最后按假设 5 把生效条件收紧到
`hadThread`，确认新增的导航用例 g 由红转绿（§12）。无 `[DEBUG-...]` 前缀留存（§5）。

---

## 3. Phase 5：修复 + 回归测试（TDD 顺序）

1. **先写用例**：`app/tests/agent_detail_ui.test.cjs` 新增 8 条（§3.2；其中导航那条是 follow-up 之后补的）。
2. **看它红**：§4 的第一栏 —— 19 tests / pass 15 / **fail 4**，失败者正是 (a)/(b) 与两条同判据的
   边界用例；(c) 有频道那条**在改动前就是绿的**（它是防止「一收到底」的反向护栏）。
3. **再改实现**：抽出共用的落点判据（§3.1）。
4. **看它绿**：§4 的第三栏 —— 19 tests / pass 19 / fail 0；并按 follow-up 把生效条件收紧到
   `hadThread`（§12），第二栏的 `fail 1` 就是收紧前的导航差异。
5. **原始（未最小化）回路复跑**：§6.4 的真实浏览器装置级复跑。

### 3.1 修复

```diff
 app/alibot/studio/app.js
+/** 关闭 dock 里的某一个形态之后**落到哪里**——两条关闭路径共用这一份判据，不各写一份。 */
+function dockFallbackMode() {
+  if (state.activeTaskId) return 'task';       // ① 仍打开着任务线程
+  if (state.activeAgentId) return 'detail';    // ② 仍打开着 Agent 详情（导航路径的既有落点）
+  if (activeChannel()) return 'agent';         // ③ 当前有打开的频道 → 「频道成员」形态
+  return null;                                 // ④ 否则：没有落点 ⇒ 收起 dock
+}
+
+function settleDockFallback() {
+  const mode = dockFallbackMode();
+  setDockMode(mode || 'agent');
+  if (!mode) setDockOpen(false);               // 票面 R1c：hidden === true 且不带 .is-open
+}
+
 closeTaskDock()                      # 新规则只在「这次确实在关一条线程」时才生效（§12）
+  const hadThread = Boolean(state.activeTaskId);
   …
-  setDockMode(state.activeAgentId ? 'detail' : 'agent');
+  if (hadThread) settleDockFallback();
+  else setDockMode(state.activeAgentId ? 'detail' : 'agent');   // 切视图的既有行为：不碰 dock 开合

 closeAgentDetail()
   state.activeAgentId = null;
-  setDockMode(state.activeTaskId ? 'task' : 'agent');
+  settleDockFallback();
   renderMembers();

 openTask(taskId) 的 catch（原先在 closeTaskDock() 之后又硬写一次落点）
   } catch (err) {
     closeTaskDock();
-    setDockMode('agent');   // 与「一份判据」相悖的第二处写法，且现已完全冗余
     renderTaskBoard();
```

**保持既有行为（逐条对照票面 Constraint）**：

- **显式入口一字不动**（R3）：`toggleDock()` / `openAgentDetail()` / `openTask()` 的打开行为未改；
  `setDockOpen()` / `setDockMode()` 的 `reveal` 与 `state.dockDismissed` 语义未改。
- **唯一判据**（R4）：没有新造形态、没有新面板；「有频道」= `activeChannel() !== null`，
  380px 容器、CSS 布局、≤900px 抽屉行为全部未动（`app.css` / `index.html` 不在 diff 里）。
- **保留空态渲染**：`renderDock()` 在无频道时仍渲染 `dock.openChannelFirst`（§4 的显式入口用例）。
- **没有新文案**：`i18n.js` 一个字未动（diff 只有两个文件），因此不需要跑词条用例以外的东西。
- **不重排 / 不重格式化**：`git diff --numstat` 只有真实改动行（§7）。
- **切视图不碰 dock 开合**（coordinator follow-up）：`showSearchView()` / `showTaskBoard()` /
  `showMessagesView()` 这三个调用点保持改前行为（`else` 分支），只有 `hadThread` 时才走新规则（§12）。

### 3.2 回归测试（`app/tests/agent_detail_ui.test.cjs`，8 条）

| # | 用例 | 断言的外部可观察事实 |
| --- | --- | --- |
| a | 无频道时关闭 Agent 详情：dock 收起，不残留成员面板 | `#dock.hidden === true`、`!#dock.classList.contains('is-open')`、`#dockDetail.hidden === true` |
| b | 只打开着 DM 时关闭 Agent 详情：DM 不算频道，同样收起 dock | `#dock.hidden === true`、`!is-open` |
| b' | `activeChannelId` 指向已不存在的频道：等同于没有频道，收起 dock | `#dock.hidden === true`、`!is-open` |
| c | 有频道打开时关闭 Agent 详情：仍回退到「频道成员」形态（成员面板） | `#dock.hidden === false`、`is-open`、`#dockAgent.hidden === false`、`#dockDetail.hidden === true` |
| d | 无频道时关闭任务线程：与关闭详情共用同一份落点判据 | `#dock.hidden === true`、`!is-open`、`#dockTask.hidden === true` |
| e | 切主视图先走 `closeTaskDock()`：已打开的详情不被顶掉 | `#dock.hidden === false`、`#dockDetail.hidden === false`、`state.activeAgentId === 'mem-1'` |
| f | 显式入口不受频道影响：没有频道时点开关仍打开 dock 并显示空态提示 | `#dock.hidden === false`、`is-open`、`#dockMembers` 文本含 `dock.openChannelFirst` 词条值 |
| g | 切消息流不是「在关 dock 里的东西」：无频道时也不收起人显式打开的面板 | 先 `toggleDock()`（开）→ `await showMessagesView()` ⇒ `#dock.hidden === false`、`is-open` 仍在、`activeTaskId === null` |

用例 g 选 `showMessagesView()` 而不是 `showSearchView()` / `showTaskBoard()` 的理由：它是最短的导航路径
——不做网络刷新（看板那条要 `await refreshTasks()`）、也不抢焦点（搜索那条要 focus 输入框），
因此这条断言只把「导航不碰 dock 开合」这一个变量钉住；它也正是 ESC / M / 命令面板「消息视图」的落点。

接缝**沿用该文件既有那一套**（`vm` 装载 `i18n.js` + `app.js`、`fetch` 替身、`FakeNode` 替身节点、
`HIDDEN_IN_HTML` 从 `index.html` 读同一份正本），**没有**新开第二套前端测试框架；只把 `closeTaskDock`
`toggleDock` `setDockMode` `showMessagesView` 四个函数加进既有的 vm 导出串。新增两个替身构造助手 `channel(id, name)` /
`dm(id, name)`，并把文件里既有的同类内联字面量折叠到 `channel()` 上（review 处理项，§8）。

---

## 4. 红 → 绿证据

**红证据的取得方式（保护工作区）**：不还原本 worktree 的任何文件，而是用 `git show <rev>:<file>` 把基线
的三份 studio 文件导出到**隔离副本**，只把**本票最终的测试文件**拷进去，在副本里跑。一共两个副本：

```sh
$ mkdir -p /tmp/dock-red/app/alibot/studio /tmp/dock-red/app/tests
$ for f in app/alibot/studio/app.js app/alibot/studio/i18n.js app/alibot/studio/index.html; do
    git show main:$f > /tmp/dock-red/$f ; done
$ cp app/tests/agent_detail_ui.test.cjs /tmp/dock-red/app/tests/
$ md5 /tmp/dock-red/app/alibot/studio/app.js ; git show main:app/alibot/studio/app.js | md5
MD5 (/tmp/dock-red/app/alibot/studio/app.js) = 7fb7c7685562feeb1f2b81f1a0fbeac3
7fb7c7685562feeb1f2b81f1a0fbeac3          # ← 与 main 逐字节相同，证明副本确实是「改前」

$ mkdir -p /tmp/dock-nav-red/app/alibot/studio /tmp/dock-nav-red/app/tests
$ for f in app/alibot/studio/app.js app/alibot/studio/i18n.js app/alibot/studio/index.html; do
    git show 32614fe:$f > /tmp/dock-nav-red/$f ; done
$ cp app/tests/agent_detail_ui.test.cjs /tmp/dock-nav-red/app/tests/
$ md5 -q /tmp/dock-nav-red/app/alibot/studio/app.js ; git show 32614fe:app/alibot/studio/app.js | md5
1cf2ba16f881b2405242485f88356528
1cf2ba16f881b2405242485f88356528          # ← 「新规则尚未限制在确实关闭时」的那一版
```

| 命令（`node --test app/tests/agent_detail_ui.test.cjs`） | 结果 |
| --- | --- |
| 副本 1 = `main` 的 `app.js`（**改前**：落点不看频道） | **tests 19 / pass 15 / fail 4**（`duration_ms` 165.05） |
| 副本 2 = `32614fe` 的 `app.js`（新规则已生效，但**未限制在确实关闭时**） | **tests 19 / pass 18 / fail 1**（`duration_ms` 176.45） |
| 本分支 `b75a83d`（**After**：落点看频道 + 只在确实关闭时生效） | **tests 19 / pass 19 / fail 0**（`duration_ms` 153.59） |
| 本分支 `b75a83d` 全量 `node --test app/tests/*.test.cjs` | **tests 84 / pass 84 / fail 0**（`duration_ms` 3675.46） |

**副本 1（改前）的失败清单**（逐字抄自 `/tmp/dock-fix-evidence/red-baseline-final-tests.txt`）：

```text
✖ 无频道时关闭 Agent 详情：dock 收起，不残留成员面板
✖ 只打开着 DM 时关闭 Agent 详情：DM 不算频道，同样收起 dock
✖ `activeChannelId` 指向已不存在的频道：等同于没有频道，收起 dock
✖ 无频道时关闭任务线程：与关闭详情共用同一份落点判据（同样收起 dock）
ℹ tests 19
ℹ pass 15
ℹ fail 4
```

**副本 2（新规则过早生效）的失败清单**（逐字抄自 `/tmp/dock-fix-evidence/red-nav-before-guard.txt`）——
这正是 coordinator follow-up 指出的那条行为差异，只有导航用例挡得住：

```text
✖ 切消息流不是「在关 dock 里的东西」：无频道时也不收起人显式打开的面板
  AssertionError [ERR_ASSERTION]: 没有东西在被关闭，dock 的开合一个字不动
  true !== false        # actual: true（dock.hidden 被导航动作置成了 true）
ℹ tests 19
ℹ pass 18
ℹ fail 1
```

对照组（三份代码上**都绿**，证明修复没有「一收到底」、也没有把导航行为弄反）：
`有频道打开时关闭 Agent 详情：仍回退到「频道成员」形态（成员面板）`（用例 c）、
`切主视图先走 closeTaskDock()：已打开的详情不被顶掉`（用例 e）、
`显式入口不受频道影响：没有频道时点开关仍打开 dock 并显示空态提示`（用例 f）。

After 一侧（逐字抄自 `/tmp/dock-fix-evidence/green-after-fix.txt` / `full-studio.txt`）：

```text
$ node --test app/tests/agent_detail_ui.test.cjs
ℹ tests 19
ℹ pass 19
ℹ fail 0

$ node --test app/tests/*.test.cjs
ℹ tests 84
ℹ pass 84
ℹ fail 0
```

**基线对照**（派活时 coordinator 在 `d7ba183` 实测，本票开工时**逐条复跑确认**）：

```text
$ node --test app/tests/agent_detail_ui.test.cjs
ℹ tests 11  ℹ pass 11  ℹ fail 0

$ node --test app/tests/search_ui.test.cjs app/tests/i18n_ui.test.cjs app/tests/mobile_shell.test.cjs
ℹ tests 65  ℹ pass 65  ℹ fail 0
```

即 `11 + 65 + 8`（本票新增 8 条）= **84**，与 After 的全量数字一致。

---

## 5. `diagnosing-bugs` Phase 6：清理

- [x] **原始回路不再复现**：§6.4 的真实浏览器装置级复跑（改前：关闭后 `#dock` 仍 `display:flex`、
      380px；改后：`display:none`、宽度 0、右侧不可见、页面文本里连「频道成员」「先打开一个频道。」
      都不再出现）。
- [x] **回归测试通过**（§4）。
- [x] **无插桩留存**：本票没有加过任何 `[DEBUG-` / `console.log`；`app.js` 的 diff 只有实现与注释。
- [x] **一次性原型不入仓**：全部在 `/tmp` 下（`/tmp/dock-red`、`/tmp/dock-nav-red`（两个基线副本）、
      `/tmp/dock-fix-evidence`（证据与截图）、`/tmp/alibot-dock-fix`（真实运行的数据目录）），
      明确标记为不入仓。
- [x] **正确假设写进本文件与 commit message**：假设 1（落点不看频道）+ 假设 4（两条路径必须共用一份
      判据）+ 假设 5（新规则只在确实关闭时生效）；commit `c31f6de` / `b75a83d` 的标题即行为规则本身。

### 5.1 live 数据目录纪律

本票**没有**读写用户正在运行的实例：真实运行只用两个干净数据目录与两个空闲端口
（After：`/tmp/alibot-dock-fix` + `8791`；Before：`/tmp/alibot-dock-baseline` + `8792`，见 §6.4），
未触碰默认数据目录与 8788 端口。改动不涉及后端与数据面（diff 里没有一个 `.py` 文件）。

---

## 6. 验收项逐条对照（票面 Observable acceptance）

| # | 票面验收项 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 开工先核 `git rev-parse HEAD` = `d7ba183` | ✅ | 开工读数 `d7ba1838e3d9c579b964d1d9ad3f6601b036d7f5`；基线两条命令实测 11/11、65/65 |
| 2 | 红→绿回归测试（至少 a/b/c 三条外部可观察断言） | ✅ | §3.2 八条；§4 三栏：`main`（fail 4）→ `32614fe`（fail 1，导航差异）→ `b75a83d`（pass 19）；红证据在两个隔离副本上取得并 md5 自校验 |
| 3 | 全量 Studio 前端测试四个文件全绿 | ✅ | §4：`tests 84 / pass 84 / fail 0` |
| 4 | 真实浏览器核验（URL/端口/数据目录/两张截图） | ✅ | §6.4 |
| 5 | 双轴 code-review（Standards + Spec 两轴，findings 逐条处理） | ✅ | §8 |
| 6 | 交付前 `git status --porcelain` 干净、已推送、PR head = 本票最终提交、`--numstat` 无异常大改动 | ✅ | §7、§9 |
| 7 | 两份交付件 + `--body-file` 创建 PR（base = main） | ✅ | 本文件、`app/specs/fix-dock-fallback-no-channel-pr-body.md`、§9 |
| — | 本票不跑 pytest 全量 / ruff / mypy；diff 里出现 `.py` 即失败 | ✅ | `git diff --name-only main...HEAD` 只有 `app/alibot/studio/app.js` 与 `app/tests/agent_detail_ui.test.cjs` |
| — | 不新增用户可见文案（否则 zh/en 双份 + 跑 i18n 用例） | ✅ | `app/alibot/studio/i18n.js` 未在 diff 中；无新词条需求 |
| — | coordinator follow-up（`msg_7d56e59ccfa2`）：新规则只落在「确实在关的那个形态」上 + 补一条导航断言 | ✅ | §12（含改前改后实测输出） |

### 6.1 红→绿（票面 2a / 2b / 2c）逐条

- **2a 无频道**：`state.channels = []`、`state.activeChannelId = null` → 点左栏 Agent 行 → `closeAgentDetail()`
  ⇒ `#dock.hidden === true`、`!#dock.classList.contains('is-open')`（§4 第 1 条红 → 绿）。
- **2b 只打开着 DM**：`state.dms = [{ id:'d-1', kind:'dm', … }]`、`channels = []`、`activeChannelId = 'd-1'`
  ⇒ 同样收起（§4 第 2 条红 → 绿）。
- **2c 有频道**：`state.channels = [{ id:'ch-1', … }]`、`activeChannelId = 'ch-1'` ⇒ `#dock` 仍打开、
  `#dockAgent.hidden === false`、`#dockDetail.hidden === true`（改动前后都绿 —— 反向护栏）。

### 6.2 显式入口保持原状（票面 R3）

`toggleDock()` 在没有频道时**仍然打开** dock，并显示词条 `dock.openChannelFirst`
（「先打开一个频道。」）。用例 f 逐字断言的是**词条取值**（`catalogText(harness, 'zh-CN', 'dock.openChannelFirst')`），
不是手抄的字符串。

### 6.3 共用一份判据（票面 R2 / R4）

落点判据只在 `dockFallbackMode()` 一处，两条关闭路径都通过 `settleDockFallback()` 用它：

```sh
$ grep -n "settleDockFallback()\|dockFallbackMode()" app/alibot/studio/app.js
1654:function dockFallbackMode() {
1664:function settleDockFallback() {
1665:  const mode = dockFallbackMode();
1686:  if (hadThread) settleDockFallback();      # closeTaskDock()
2697:  settleDockFallback();                     # closeAgentDetail()
（另有 1672 / 2694 两行只出现在注释里，此处略去）
$ grep -n "state.activeTaskId ?" app/alibot/studio/app.js
2367:  setDockMode(state.activeTaskId ? 'task' : (state.activeAgentId ? 'detail' : 'agent'));
```

两条关闭路径里旧的形态三元式（`state.activeTaskId ? 'task' : 'agent'` 与
`state.activeAgentId ? 'detail' : 'agent'`）都已消失；`grep` 只剩 `render()` 里那一处**整体重绘同步**
（第 2367 行，从未属于关闭路径）。`closeTaskDock()` 的 `else` 分支里那一行
`setDockMode(state.activeAgentId ? 'detail' : 'agent')` 是**改前行为本身**（导航调用点专用，见 §12），
不是第二份落点判据 —— 它没有「有没有频道」这一步，也不碰 dock 的开合。`activeChannel()` 是唯一的「有频道」判据（未新增 `activeChannelId` 的第二次解析、
未新增形态）。

### 6.4 真实浏览器核验（UI 票门禁）

**运行方式**（用仓库根 `.venv` 的解释器跑代码；worktree 里没有 `.venv`）。**改前**用一个隔离副本
（`/tmp/dock-baseline`，`app.js` 取自 `main`，md5 与 `main` 一致），**改后**用本 worktree：

```sh
# After（本 worktree，端口 8791）
$ cd /Users/apple/orca/workspaces/alibot/fix-dock-fallback-no-channel
$ rm -rf /tmp/alibot-dock-fix && mkdir -p /tmp/alibot-dock-fix
$ PYTHONPATH=/Users/apple/orca/workspaces/alibot/fix-dock-fallback-no-channel/app     /Users/apple/orca/alibot/.venv/bin/python -m alibot --port 8791 --data-dir /tmp/alibot-dock-fix
alibot 工作台： http://127.0.0.1:8791/studio/
数据目录（备份即复制该目录）： /tmp/alibot-dock-fix

# Before（隔离副本：同一份 python 包 + main 的 studio/app.js，端口 8792）
$ cp -R app/alibot /tmp/dock-baseline/alibot
$ git show main:app/alibot/studio/app.js > /tmp/dock-baseline/alibot/studio/app.js
$ md5 -q /tmp/dock-baseline/alibot/studio/app.js ; git show main:app/alibot/studio/app.js | md5
7fb7c7685562feeb1f2b81f1a0fbeac3
7fb7c7685562feeb1f2b81f1a0fbeac3          # ← 与 main 逐字节相同
$ cd /tmp/dock-baseline && PYTHONPATH=/tmp/dock-baseline \
    /Users/apple/orca/alibot/.venv/bin/python -m alibot --port 8792 --data-dir /tmp/alibot-dock-baseline
```

- **URL**：`http://127.0.0.1:8791/studio/`（After）/ `http://127.0.0.1:8792/studio/`（Before）｜
  **端口**：`8791` / `8792`（都是空闲端口，**未碰 8788**）｜
  **数据目录**：`/tmp/alibot-dock-fix` / `/tmp/alibot-dock-baseline`（都是干净目录）
- **AGENT 行**：干净目录里没有 Agent，用允许范围内的 HTTP 接口各建了一个：
  `POST /api/agents {"name":"小测","role":"测试工程师","model_id":"ollama:qwen2.5",…}`。
  **两个实例都没有创建频道**（`GET /api/channels` → `{"channels":[]}`）。
- **浏览器纪律**：按仓库纪律用 ego-browser skill（`/Users/apple/.agents/skills/ego-browser/SKILL.md`）。
- **驱动方式**（两次完全相同）：`page.click('loc=css:button[aria-label="查看 小测 的详情"]')` →
  等 `#dockDetail:not([hidden])` → 截图 → `page.click("#btnAgentDetailClose")` → 读 DOM 渲染读数 → 截图。

**改前（Before，端口 8792、`main` 的 `app.js`）——复现用户报告的症状**（逐字抄自
`/tmp/dock-fix-evidence/browser-verify-baseline.txt`）：

```text
AFTER CLOSE (BASELINE) {"dockHidden":false,"dockIsOpen":true,"dockDisplay":"flex","dockWidth":380,
                        "rightSideVisible":true,"memberPanelVisible":true,
                        "bodyHasChannelMemberHeading":true,"bodyHasOpenChannelFirst":true,
                        "dockMembersText":"先打开一个频道。"}
```

右侧 380px 的「频道成员」面板还在，里面正是「先打开一个频道。」；截图
`/tmp/dock-fix-evidence/dock-detail-closed-BASELINE.png` 与用户描述逐字一致。

**改后（After，端口 8791、本分支 `b75a83d`）**（逐字抄自
`/tmp/dock-fix-evidence/browser-verify-raw.txt`）：

```text
BEFORE CLOSE {"dockHidden":false,"dockIsOpen":true,"dockDisplay":"flex","dockWidth":380,
              "dockDetailVisible":true,"detailName":"小测","dockMembersText":"先打开一个频道。"}
shot1 /tmp/dock-fix-evidence/dock-detail-open.png

AFTER CLOSE  {"dockHidden":true,"dockIsOpen":false,"dockDisplay":"none","dockWidth":0,
              "rightSideVisible":false,"memberPanelVisible":false,
              "bodyHasChannelMemberHeading":false,"bodyHasOpenChannelFirst":false}
shot2 /tmp/dock-fix-evidence/dock-detail-closed.png
```

- **截图 1（打开详情）**：`/tmp/dock-fix-evidence/dock-detail-open.png` —— 右侧 380px 详情面板
  （标题「小测」、概览 / 运行时页签、四个数字），`#dockMembers` 里躺着空态文案。
- **截图 2（点关闭之后）**：`/tmp/dock-fix-evidence/dock-detail-closed.png` —— **右侧完全空**，
  既没有成员面板，也没有「先打开一个频道。」；页面可读文本里连这两个串都不再出现。
- **「页面确实是本分支的代码」的自证**（避免缓存 / 旧实例的假绿）：

  ```sh
  $ curl -s http://127.0.0.1:8791/studio/app.js | grep -c "dockFallbackMode\|settleDockFallback"
  7
  $ curl -s http://127.0.0.1:8791/studio/app.js | grep -c "hadThread"          # 收敛后的生效条件
  2
  $ curl -s http://127.0.0.1:8791/studio/app.js | md5 -q ; md5 -q app/alibot/studio/app.js
  45687f6d1bd6e1a55a1407f4d85cc4a8
  45687f6d1bd6e1a55a1407f4d85cc4a8        # ← 浏览器拿到的字节 = 本 worktree 的文件
  $ curl -s http://127.0.0.1:8792/studio/app.js | grep -c "dockFallbackMode"   # 基线实例：没有新代码
  0
  ```

  （完整读数存在 `/tmp/dock-fix-evidence/served-appjs-proof.txt`。）

**装置级结论**：同一条人机序列（点 Agent 行 → 点关闭），改前右侧残留「频道成员」面板，改后右侧不可见
且 `#dock.hidden === true`、`.is-open` 不存在。live 数据目录（默认目录与 8788 实例）**全程未被触碰**。

## 7. 改动清单与现场保护

```sh
# 只看两个代码/测试文件（两份交付文档也在同一个 PR 里；它们的行数包含本文件自身，故不计入改动面）
$ git diff --numstat main...HEAD -- app/alibot/studio/app.js app/tests/agent_detail_ui.test.cjs
45      6       app/alibot/studio/app.js
167     3       app/tests/agent_detail_ui.test.cjs

$ git diff --stat main...HEAD -- app/alibot/studio/app.js app/tests/agent_detail_ui.test.cjs
 app/alibot/studio/app.js           |  51 +++++++++++--
 app/tests/agent_detail_ui.test.cjs | 170 ++++++++++++++++++++++++++++++++++++-
 2 files changed, 212 insertions(+), 9 deletions(-)

$ git status --porcelain            # 交付态：全部改动与交付件都已提交（最终读数见 §9 回执）
（空）
```

- **diff 里只有真实改动行**：`app.js` 的新增是「一份判据（`dockFallbackMode()` + `settleDockFallback()`
  + 注释）+ 两处调用点替换 + `closeTaskDock()` 的 `hadThread` 生效条件 + `openTask` catch 里删掉一行
  冗余」；6 行删除是两条三元表达式、一行冗余 `setDockMode('agent')`、以及被替换的两段旧注释。
  **没有任何整文件重排 / 重格式化**。
- **未改动**（票面 Ownership / Constraint）：`app/alibot/studio/index.html`（结构）、
  `app/alibot/studio/app.css`、`app/alibot/studio/i18n.js`、`CONTEXT.md`、`docs/adr/`、
  以及**任何** `app/alibot/*.py`（`git diff --name-only main...HEAD` 无 `.py`）。

---

## 8. 双轴 code-review（票面验收项 5）

方法：加载 `/Users/apple/.agents/skills/code-review/SKILL.md`，按它要求**并行起两个子代理**，
分别在 **Standards**（是否遵守仓库成文规范 + Fowler 坏味道基线）与 **Spec**（是否忠实实现本票裁定）两轴
审 `git diff main...HEAD`。固定点 = `main`（`d7ba183`）；被审 revision = `c31f6de`（代码 + 测试），
review 的处理落在 `32614fe`；其后 coordinator 的预审 follow-up 又推翻了本票对 P2 的处置，
收敛落在 `b75a83d`（§12）——两轮都逐条记在下面。

### Standards 轴结论

**通过，无硬违规**。机械自查：`--numstat` 无整文件重排；diff 无 `.py` 文件。3 条判断项 + 1 条正面确认：

| # | finding | 处理 |
| --- | --- | --- |
| S1 | 术语漂移：注释 / 用例名写「频道成员面板」，而 `CONTEXT.md` 的**面板**正名是「成员面板」，「频道成员」是成员关系概念 | **已处理**：把「成员面板」用于面板本身、「频道成员」只用于 dock 形态名（票面 R1 的用词）；`app.js` 与测试的措辞各改一处 |
| S2 | 测试文件头部清单编号与正文分段错位（头部加到 7，正文第 7 段是「新增文案」） | **已处理**：头部补齐第 7 条（新增文案）并把新条目编为 8，与正文 `/* ─── 8. … */` 对齐 |
| S3 | Duplicated Code（判断项）：新助手 `channel()` 与同文件既有内联频道字面量重复 | **已处理**：既有那处折叠到 `channel("ch-1", "频道")` |
| S4 | Speculative Generality（判断项）：`dockFallbackMode()` 里 `activeAgentId → 'detail'` 分支在各入口不可达 | **保留并加测试**：该分支保住的是**导航路径**的既有落点（切搜索 / 消息 / 看板都先调 `closeTaskDock()`，此刻详情仍开着就该留在详情），删掉会让「切主视图顺手关掉详情」成为未授权的行为变化；新增用例 e 把这条落点锁住，并在 §10 决策账登记 |
| — | 正面：删掉两条关闭路径各自的三元推导，正合 `setDockOpen()` 注释立的「不各写一遍」约定 | — |

### Spec 轴结论

**通过，R1–R5 逐条落地**（a/b/c 三条可观察断言 + 同一判据 + 显式入口不动 + 唯一判据 `activeChannel()`
+ 不新造形态 / 不改 CSS）。3 条 finding：

| # | finding | 处理 |
| --- | --- | --- |
| P1 | 票面 Target 点名的两份交付件当时还不存在 | **已处理**：本文件与 `app/specs/fix-dock-fallback-no-channel-pr-body.md` 即为产物（本就排在评审之后写） |
| P2 | scope creep：`showSearchView` / `showTaskBoard` / `showMessagesView` 都调 `closeTaskDock()`，因此在「无频道且没有打开的详情」时，导航动作也会收起 dock（并置 `state.dockDismissed`）——票面只逐字写了「关闭详情」与「关闭任务线程」两条路径 | **review 当时的处置**是「接受并登记」（把它读成 R2 的直接推论）；**该处置随后被 coordinator 的预审 follow-up 推翻**（`msg_7d56e59ccfa2`，§12）：导航入口改前不碰 dock 的开合，新规则必须只在「这次确实在关一条线程」时生效 ⇒ **已处理**：`closeTaskDock()` 记下 `hadThread`，只有它为真才走 `settleDockFallback()`；导航调用点保留改前行为。新增用例 g 钉住这条差异（§4 副本 2 的 `fail 1` 就是它） |
| P3 | `openTask` 的 catch 里 `closeTaskDock()` 之后又硬写一次 `setDockMode('agent')`，是「各写一份」的残留 | **已处理**：删除该行。行为等价可证：`settleDockFallback()` 已把形态落成 `'agent'` 或收起；该行无 `reveal`，既不会重开 dock、也不再改 `dockDismissed`，故有频道 / 无频道两种情形下都无观察差异；全量测试复跑 84/84 绿 |

**两轴小结**：Standards 4 findings（无硬性违规、3 判断项 + 1 正面确认），最重的是 S1 术语漂移（已改）；
Spec 3 findings，最重的是 P2 导航路径收起 dock —— review 当时判为「R2 的推论、登记而不改」，
coordinator 预审不接受该判断，最终按「只在确实关闭时生效」收敛（§12）。
**review 后的代码改动（P2 的收敛）未经第二次双轴 review**，但它是同一判据的一个生效条件收紧、
并由新增用例 g 在改前代码上取到了红证据；该限制如实登记在 §11。

---

## 9. PR

- **PR**：`whutlichao/alibot#<创建后回填>`（base = `main`，head = `fix-dock-fallback-no-channel`，**不带 issue 关联**）
- **body 文件**：`app/specs/fix-dock-fallback-no-channel-pr-body.md`
- **创建命令**：`gh pr create --base main --head fix-dock-fallback-no-channel --title "…" --body-file app/specs/fix-dock-fallback-no-channel-pr-body.md`
- **head 与本票最终提交一致**：以远端分支与 PR head 读数为准（本文件不写死 SHA，避免自指漂移）；
  核验命令见下方「PR 回执」。
- **PR 回执**：见 §9.1

---

## 10. 决策账（Q → 谁答 → 答案 → 依据）

| # | 问题 | 谁答 | 答案 | 依据 |
| --- | --- | --- | --- | --- |
| D1 | 关闭 Agent 详情、又没有任何 target 时，落点是什么？ | 用户（2026-10-16，票面 R1） | **收起 dock**（`hidden` + 无 `.is-open`），不得残留成员面板 | 票面 R1c；§4 的 a/b 两条红→绿 |
| D2 | 两条关闭路径要不要各写一份落点？ | 用户（票面 R2） | **不**：共用一份判据 | 票面 R2；实现上是 `dockFallbackMode()` / `settleDockFallback()` 一对函数 |
| D3 | `closeTaskDock()` 也被导航入口（搜索 / 消息 / 看板）调用；无频道时导航也要收起 dock 吗？ | **review 时**：本票先判「接受」（R2 的直接推论）；**随后 coordinator 预审改判**（`msg_7d56e59ccfa2`） | **不接受**：新规则只在「这次确实在关一条线程」时生效（`hadThread`）；导航调用点保持改前行为（不碰 dock 的开合） | coordinator follow-up（§12）；Spec 轴 finding P2；用例 g 在未收敛代码上取到红证据（§4 副本 2 = `fail 1`） |
| D4 | `dockFallbackMode()` 是否保留「仍打开着详情 → 详情」这一步？ | 本票（Spec 轴 finding P2 的处置） | **保留**：它保住 `main` 上导航路径的既有落点（切主视图不关详情），且对 R1 的三条规则不可见（`closeAgentDetail()` 先清 `activeAgentId`） | §8 S4；新增用例 e |
| D5 | 是否需要新增用户可见文案 / 动 `i18n.js`？ | 本票 | **不需要**：收起与空态都复用既有词条（`dock.openChannelFirst`） | 票面 Constraint；diff 不含 `i18n.js` |
| D6 | 红证据怎么取才不动工作区？ | 本票（票面前置 skill Phase 5 给了两种方式） | 用 `git show <rev>:<file>` 拼两个基线副本（`/tmp/dock-red` = `main`、`/tmp/dock-nav-red` = `32614fe`）+ 本票最终测试文件，副本 `app.js` 的 md5 与目标 revision 相同后开跑 | §4；本 worktree 全程未被还原 |
| D7 | 「一份判据」要不要连同「什么时候该用这份判据」一起共用？ | coordinator 预审（follow-up） | **要**：判据共用，但生效条件必须是「这次确实在关闭那个形态」 | §12；实现上是 `closeTaskDock()` 里的 `hadThread` 判定 |

---

## 11. 剩余风险与显式遗留

1. **导航路径的行为差异已按 coordinator 预审收敛（P2 / D3 / §12）**：新规则只在 `hadThread` 为真时
   生效，切搜索 / 看板 / 消息流保持改前行为（不碰 dock 的开合）；这一条由用例 g 钉住。
   **剩余风险**：这次收敛（`b75a83d`）在第一次双轴 review 之后落地，**没有第二次 review**——
   它是同一判据的生效条件收紧、新增断言在未收敛版本上取到了红证据（§4 副本 2），
   本票如实登记这一点而不声称覆盖面。
2. **`openTask` 失败路径的落点**：catch 现在完全交给共用判据（有频道 → 成员面板并附 toast；无频道 →
   收起并附 toast）。这是删掉冗余 `setDockMode('agent')` 后的等价行为，但本票**没有**为它单写用例
   （票面没点名该路径）。
3. **`hadThread` 把「关闭」定义为「调用时 `state.activeTaskId` 非空」**：如果将来有人在
   `activeTaskId` 已被清掉之后调用 `closeTaskDock()` 并期望走新落点，会拿到旧行为。当前所有调用点
   （`#btnThreadClose` / `openTask` 失败回退 / 三个导航入口）都符合这个定义，用例 d 与 g 覆盖了两侧。
4. **不覆盖 ≤900px 抽屉下的同一序列**：票面 R4 要求「不改 ≤900px 抽屉行为」，本票确实只复用
   `setDockOpen()`（它自带 ≤900px 的焦点搬运），既有 `mobile_shell.test.cjs` 的 4 组抽屉用例全部照旧
   通过；但没有在真机宽度下重跑一遍浏览器核验（桌面宽度 1440 下取证）。
5. **未跑 pytest 全量 / ruff / mypy**：票面明确排除（本票无 Python 改动，diff 里没有一个 `.py`）。

---

## 12. coordinator follow-up 回执与收敛（`msg_7d56e59ccfa2`）

**回执**：follow-up 于 2026-10-16T15:50:24Z 投递到本 Dispatch（`ctx_819bc69dcaf7`），
主题「把新的回退规则限制在「确实在关的那个形态」上」，本票在收尾前读取并按它执行。

**它的发现**（与本票 Spec 轴 finding P2 同源，但结论相反）：`closeTaskDock()` 除了 `#btnThreadClose`
还被 `showSearchView()` / `showTaskBoard()` / `showMessagesView()` 调用；改前的落点
`setDockMode(activeAgentId ? 'detail' : 'agent')` **不碰 dock 的开合**（桌面 dock 是常驻列，ESC 不管它），
而本票最初让 `settleDockFallback()` 无条件接管这些调用点 ⇒ 无频道时「人显式打开面板 → 按 M / T / ESC
或点搜索」会把面板收掉，属未授权的行为变化。

**收敛方式**（follow-up 给的 (a) 写法；选它而不选 (b) 的 `state.dockMode` 判定，因为它直接回答
「这次调用到底在不在关东西」，比读一个可能已被重绘同步过的形态字段更贴仓库既有语汇）：

```diff
 function closeTaskDock() {
   closeTaskStream();
+  const hadThread = Boolean(state.activeTaskId);
   state.activeTaskId = null;
   state.activeTask = null;
   state.taskMessages = [];
   state.taskSeenSeqs = new Set();
-  settleDockFallback();
+  if (hadThread) settleDockFallback();
+  else setDockMode(state.activeAgentId ? 'detail' : 'agent');   // 改前行为：不碰 dock 的开合
 }
```

`closeAgentDetail()` 的落点逻辑未动（follow-up 明确它不需要改：它只在详情确实显示时被调用）。

**证据要求与交付**（follow-up 要求「一条外部可观察断言 + 改前改后实测输出」）：

```sh
# 副本 2 = 未收敛版本（32614fe 的 app.js）；app.js md5 与 32614fe 相同
$ node --test app/tests/agent_detail_ui.test.cjs        # in /tmp/dock-nav-red
✖ 切消息流不是「在关 dock 里的东西」：无频道时也不收起人显式打开的面板
  AssertionError [ERR_ASSERTION]: 没有东西在被关闭，dock 的开合一个字不动
  true !== false
ℹ tests 19  ℹ pass 18  ℹ fail 1

# 收敛后的本分支（b75a83d）
$ node --test app/tests/agent_detail_ui.test.cjs
ℹ tests 19  ℹ pass 19  ℹ fail 0

$ node --test app/tests/*.test.cjs
ℹ tests 84  ℹ pass 84  ℹ fail 0
```

同时，「关闭线程 / 关闭详情」那两组断言全绿：用例 a / b / b' / d（收起）与 c / e / f（有频道回成员面板、
详情不被顶掉、显式入口照旧）在收敛前后都是绿的（§4 的三栏对照里可逐条核对）。
