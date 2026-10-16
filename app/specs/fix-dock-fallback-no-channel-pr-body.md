## Summary

修掉 dock 形态回退的一个落点缺口：**没有频道时关闭 Agent 详情（或任务线程），右侧原本会残留
「频道成员」成员面板 + 空态提示「先打开一个频道。」**。落点规则现在是**一份判据**，两条关闭路径共用：

```text
closeAgentDetail()  ┐
closeTaskDock()     ┴─► settleDockFallback()
                          │
                          ├─ dockFallbackMode()
                          │    ① state.activeTaskId  → 'task'      （任务线程仍开着）
                          │    ② state.activeAgentId → 'detail'    （详情仍开着，导航路径的既有落点）
                          │    ③ activeChannel()     → 'agent'     （当前有打开的频道）
                          │    ④ 否则                → null        （没有 target / 只有 DM / 频道已不存在）
                          │
                          ├─ 有落点 → setDockMode(mode)
                          └─ 无落点 → setDockMode('agent') + setDockOpen(false)   ← #dock.hidden === true
                                                                                     且不带 .is-open
```

**行为规则（本 PR 的唯一规则）**：无频道时关闭详情**不再残留成员面板** —— dock 直接收起。
「当前有打开的频道」的唯一判据是既有的 `activeChannel()`（`state.channels` 里 id 等于
`state.activeChannelId` 的那一项），因此「没有任何 target」「只打开着 DM」「`activeChannelId` 指向
已不存在的频道」三种情况同样收起。

```diff
 app/alibot/studio/app.js
+function dockFallbackMode() { … }        # 唯一的落点判据（两条关闭路径共用，不各写一份）
+function settleDockFallback() { … }      # 有落点就切形态；没有就 setDockOpen(false)

 closeTaskDock()                          # 新规则只在「这次确实在关一条线程」时生效
+  const hadThread = Boolean(state.activeTaskId);
-  setDockMode(state.activeAgentId ? 'detail' : 'agent');
+  if (hadThread) settleDockFallback();
+  else setDockMode(state.activeAgentId ? 'detail' : 'agent');   # 切视图的既有行为：不碰 dock 开合

 closeAgentDetail()
   state.activeAgentId = null;
-  setDockMode(state.activeTaskId ? 'task' : 'agent');
+  settleDockFallback();

 openTask() 的 catch
   closeTaskDock();
-  setDockMode('agent');                  # 「一份判据」之外的第二处写法，且已完全冗余
```

**刻意未动**（票面 R3/R4 与 Constraint）：`toggleDock()`（显式开关）在没有频道时**仍然打开** dock 并
显示词条 `dock.openChannelFirst`；`openAgentDetail()` / `openTask()` 的打开行为不受频道影响；
`setDockMode()` 的 `reveal` / `state.dockDismissed` 语义未改；没有新造第四种形态、没有新增面板、
`#dock` 的 380px 容器与 CSS 布局未动、`≤900px` 抽屉行为未动；`index.html` 结构未动；`i18n.js` 一个字
未动（没有新增用户可见文案）；后端与数据面一律未动（diff 无 `.py`）。

```diff
 app/alibot/studio/app.js            # +45 / −6（唯一判据 + 生效条件收紧）
 app/tests/agent_detail_ui.test.cjs  # +167 / −3（8 条回归用例 + 2 个替身构造助手）
 app/specs/fix-dock-fallback-no-channel-acceptance.md   # Answer（验收取证）
 app/specs/fix-dock-fallback-no-channel-pr-body.md      # 本文件
```

## Evidence

**Before**（`main` = `d7ba183` 的 `app.js`）｜**After**（本分支）

回归测试（`node --test app/tests/agent_detail_ui.test.cjs`）：

| 代码版本 | 结果 |
| --- | --- |
| `main` 的 `app.js`（隔离副本 `/tmp/dock-red`，md5 与 `main` 逐字节相同） | **tests 19 / pass 15 / fail 4** |
| `32614fe`（新规则已生效，但**未**限制在「确实在关」时） | **tests 19 / pass 18 / fail 1** |
| 本分支（After） | **tests 19 / pass 19 / fail 0** |
| 本分支 全量 `node --test app/tests/*.test.cjs` | **tests 84 / pass 84 / fail 0** |

`main` 一侧的 4 条红：无频道 / 只有 DM / `activeChannelId` 指向已不存在的频道时关闭详情、以及无频道时
关闭任务线程 —— 全部「dock 没被收起」。`32614fe` 一侧的那 1 条红是 **导航路径的行为差异**
（`✖ 切消息流不是「在关 dock 里的东西」… true !== false`），见下。

真实浏览器（改前用隔离副本，端口 8792；改后用本 worktree，端口 8791；两个实例都用干净的
`/tmp` 数据目录与新建的 Agent，**都没有频道**，也都没碰 8788 实例）：

```text
Before（main 的 app.js）
点 Agent 行 → 点 #btnAgentDetailClose
AFTER CLOSE (BASELINE) {"dockHidden":false,"dockIsOpen":true,"dockDisplay":"flex","dockWidth":380,
                        "rightSideVisible":true,"memberPanelVisible":true,
                        "bodyHasChannelMemberHeading":true,"bodyHasOpenChannelFirst":true}

After（本分支）
BEFORE CLOSE {"dockHidden":false,"dockIsOpen":true,"dockDisplay":"flex","dockWidth":380,
              "dockDetailVisible":true,"detailName":"小测"}
AFTER CLOSE  {"dockHidden":true,"dockIsOpen":false,"dockDisplay":"none","dockWidth":0,
              "rightSideVisible":false,"memberPanelVisible":false,
              "bodyHasChannelMemberHeading":false,"bodyHasOpenChannelFirst":false}
```

证据截图（真实渲染）：

| Before：点关闭之后，右侧残留「频道成员 + 先打开一个频道。」 | After：点关闭之后，右侧完全空 |
| --- | --- |
| `/tmp/dock-fix-evidence/dock-detail-closed-BASELINE.png` | `/tmp/dock-fix-evidence/dock-detail-closed.png` |

自证浏览器拿到的是本分支代码：
`curl -s http://127.0.0.1:8791/studio/app.js | md5 -q` = `md5 -q app/alibot/studio/app.js`
（两边同为 `45687f6d1bd6e1a55a1407f4d85cc4a8`）；基线实例上 `grep -c "dockFallbackMode"` = `0`。

### 导航路径的差异与收敛（coordinator 预审 follow-up）

`closeTaskDock()` 不只有 `#btnThreadClose` 一个调用点：`showSearchView()` / `showTaskBoard()` /
`showMessagesView()`（ESC / M / T / 命令面板）也调它，而它们改前**不碰 dock 的开合**（桌面 dock 是常驻列）。
最初版本让新规则无条件接管这些调用点，于是「没有频道时人显式打开面板 → 按 M/T/ESC」会把面板收掉：

```diff
 function closeTaskDock() {
   closeTaskStream();
+  const hadThread = Boolean(state.activeTaskId);   // 这次到底在不在关一条线程
   state.activeTaskId = null;
   …
-  settleDockFallback();
+  if (hadThread) settleDockFallback();
+  else setDockMode(state.activeAgentId ? 'detail' : 'agent');   // 切视图的既有行为：不碰 dock 开合
 }
```

收敛前后实测（新增断言：无频道时 `toggleDock()` 打开面板 → `showMessagesView()` ⇒ `#dock.hidden` 仍为
`false`、`.is-open` 仍在）：

```text
未收敛（32614fe）：✖ 切消息流不是「在关 dock 里的东西」：无频道时也不收起人显式打开的面板
                   AssertionError: 没有东西在被关闭，dock 的开合一个字不动   true !== false
                    ℹ tests 19  ℹ pass 18  ℹ fail 1
收敛后（本分支）：  ℹ tests 19  ℹ pass 19  ℹ fail 0
                   ℹ tests 84  ℹ pass 84  ℹ fail 0（全量四文件）
```

「关闭线程 / 关闭详情」那两组断言在收敛前后都全绿：有频道时仍回退到「频道成员」形态（成员面板）、
详情不被「切主视图」顶掉、没有频道时显式点开关仍能打开并看到「先打开一个频道。」。

验收取证（含 `diagnosing-bugs` 六阶段、逐条命令与实际输出、双轴 code-review 结论与 findings 逐条处理、
follow-up 回执与决策账）见 `app/specs/fix-dock-fallback-no-channel-acceptance.md`。

## Merge Danger

**Door:** two-way —— 单一函数级的状态落点调整：没有数据迁移、没有 schema 变更、没有接口变更、没有依赖
变更、没有 CSS / 标记结构变更。回滚只需 revert 这三个 commit（`c31f6de`、`32614fe`、`b75a83d`）。

**Blast Radius:** narrow —— 只在 Studio 前端的 dock 落点上。

- 影响面只有「关闭详情 / 关闭任务线程后的落点」。**有频道时行为一字未变**（仍是回成员面板），
  改动只落在「没有频道」这一个域，而那正是缺陷域。
- **切视图不受影响**：`showSearchView` / `showTaskBoard` / `showMessagesView` 虽然也调
  `closeTaskDock()`，但新规则只在 `hadThread`（这次确实在关一条线程）时生效，这三个导航入口保持改前
  行为——**不碰 dock 的开合**（桌面 dock 是常驻列，ESC / M / T 不管它）。这一条由新增断言钉住：
  没有频道时 `toggleDock()` 打开面板 → `showMessagesView()` ⇒ `#dock.hidden` 仍为 `false`。
- **显式入口不受影响**：没有频道时点「切换详情面板」仍然打开 dock 并显示空态提示（票面 R3 保持现状）；
  `renderDock()` 在无频道时的既有空态渲染保留。
- 「没有频道就没有成员面板」现在优先于「恢复人先前显式打开的空态面板」（票面 R5 已接受的后果）。
- 无新文案、无 i18n 改动、无后端 / 数据面改动、无 `.py` 改动；`git diff --numstat` 无整文件重排
  （`app.js` +45/−6，测试 +167/−3）。
