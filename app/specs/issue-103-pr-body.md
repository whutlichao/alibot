## Summary

Studio 左栏四处入口对齐设计系统原型（父票 #43 / 本票 #103）。只动 `rail-head` 与 `rail-actions`
两个区块与「私信」分组标签（用户 D4：不做整条左栏或全工作台的逐项复核）。

```diff
 index.html — rail-head
   <div class="rail-head">
     <div class="brand">…</div>
     <button id="btnReminders" …>          # 铃铛（#68）
+    <button id="btnSettings" …>           # 齿轮：从 .rail-foot 回到 head（原型 :515-518）
   </div>
+
+  <div class="search-shell">              # 新增（原型 :518-524）
+    <button class="search-btn" id="btnSearch" data-od-id="btn-workspace-search">
+      🔍  <span data-i18n="rail.searchPlaceholder">搜索消息、任务…</span>
+    </button>
+  </div>

 index.html — rail-actions（4 → 2）
   <button class="btn btn-primary" style="flex:1" id="btnNewChannel">＋ 新建频道</button>
-  <button class="btn" id="btnSearch">搜索</button>
-  <button class="btn" id="btnNewDm">私信</button>
-  <button class="btn" id="btnNewAgent">🤖 Agent</button>
+  <button class="btn" id="btnNewAgent" aria-label="新建 Agent">⊕</button>   # 纯图标，圆形加号

 index.html — 私信分组标签
-  <div class="group-label" data-i18n="rail.groupDms">私信</div>
+  <div class="group-label"><span data-i18n="rail.groupDms">私信</span>
+    <button id="btnNewDm" data-od-id="btn-new-dm">＋</button></div>
```

**四条承重口径**：

1. **id / `data-od-id` / click 绑定一律不动**。`#btnSettings`→`openSettingsModal`、
   `#btnSearch`→`showSearchView`（仍是主线视图，规格 #63 D14）、`#btnNewDm`→`openDmModal`、
   `#btnNewAgent`→`openAgentModal` 全部原样；`app.js` 本票**只改三处注释，绑定一行未改**。
2. **`rail-actions` 溢出修好了**：`#btnNewAgent` 的 `right` 从 **309 > 252**（被裁切）变成
   **239 ≤ 252**；直接子按钮数 4 → **2**。
3. **词条不分叉**：新增 `rail.searchPlaceholder` 两份取值；`rail.search` / `rail.newDm` /
   `rail.newAgent` 三条文字按钮文案已无节点引用，**两份表同步删除**（`i18n_ui.test.cjs` 的同键集
   用例是机器判据）。
4. **⌘K 徽标不搬（人定，用户本人 2026-10-17）**：原型 `:524` 在搜索按钮上印着 `⌘K`，但 `⌘K`
   真绑定的是**打开命令面板**、命令集里没有「搜索」——本仓纪律是「界面不承诺一个按键做不到的事」
   （`app.js` 命令面板一节注释；规格 #60 D10）。双轴 review 的 Standards 轴以 P1 阻断此点后，
   用户裁定**删掉徽标、保留按钮**，该裁定作废上位于票面原句「⌘K 徽标直接写在标记里」。

> Answer（逐条可复现读数 + 双轴 review 的 findings 与处理）：`app/specs/issue-103-acceptance.md`；
> tracker 上的摘要见 #103 的 comment。

## Evidence

**装置**（隔离，**未碰 8788 实例与默认数据目录**）：Before 用 `git archive 63e8163 app/alibot` 到
`/tmp/rail-103/baseline-root`（+ 软链 `alibot-design-system` 让 `/design/*` 也能服务），
After 用本分支。端口 `8791`（After）/ `8792`（Before），数据目录都在 `/tmp/rail-103/`。
同一支 ego-browser 探针分别打两个端口。

**服务端字节自证**（排除缓存/旧实例的假绿）：

```text
$ curl -s http://127.0.0.1:8791/studio/index.html | md5 -q ; md5 -q app/alibot/studio/index.html
6db76569f22243e5083e276464e0a8ce
6db76569f22243e5083e276464e0a8ce      # ← 浏览器拿到的字节 = 本 worktree 的文件
```

**桌面 1982×1001 @dpr2（Before → After）**：

| 判据 | Before | After |
| --- | --- | --- |
| `.rail-head` 子节点序 | `["brand","btnReminders"]` | `["brand","btnReminders","btnSettings"]` |
| `#btnSettings` | `(213, 962)` **底部** | `(209, 14)` **头部** |
| 提醒与设置 y 相同 | `false`（14 vs 962） | **`true`**（14 = 14） |
| `.search-shell` | **不存在** | `{x:0, w:251, h:46}`，**251 ≤ 252** |
| `#btnSearch` 的 class / 文案 | `btn` / `"搜索"` | **`search-btn`** / `"搜索消息、任务…"` |
| `.rail-actions` 直接子按钮数 | **4** | **2** |
| `#btnNewAgent` 的 `right` | **309**（> 252，被裁切） | **239**（≤ 252） |
| `#btnNewAgent` 内容 | `<rect>` 方块机器人 + 文字 "Agent" | 圆形加号，**无文字** |
| `#btnNewDm` 的父节点 | `.rail-actions` | **「私信」`.group-label`**（其中的 `+` 恰 1 个） |
| `.rail-foot` 子节点 | `[…, "btnSettings"]` | `[… 无控件]` |

**行为面（桌面）**：

```text
SEARCH_VIEW_AFTER   {"viewSearchHidden":false,"viewMessagesHidden":true,"activeId":"searchInput"}
DM_MODAL            {"dmOverlayHidden":false,"title":"新建私信"}
SETTINGS_MODAL      {"settingsOverlayHidden":false}
CMDK                {"cmdOverlayHidden":false,"activeId":"cmdInput","searchViewHidden":true}
CMDK_CLOSED         {"cmdOverlayHidden":true}
```

**≤900px（480×900）三态**：顶栏 `flex`；`#railToggle` 打开 → rail `visible`/`transform:none`、
`.is-open`、scrim 可见、`aria-expanded=true`、焦点进 `btnReminders`；点 scrim 收起 → `hidden`/
`translateX(-252px)`、`aria-expanded=false`、焦点还给 `#railToggle`；再开 + ESC 同上一行。
抽屉打开时六枚入口全部落在 rail 内，`headHeight` 两态都是 56（`.brand-sub` 未折行）。

**前端四支**（base `63e8163` → 本分支）：i18n_ui **43 → 44**、search_ui **13 → 16**、
agent_detail_ui **19 → 19**、mobile_shell **9 → 9**，四条全 0 fail。

**质量门禁**（cwd = `app/`）：`ruff check .` All checks passed ／ `ruff format --check .`
103 files already formatted ／ `mypy .` Success in 50 source files ／ `pyright alibot tests`
0 errors, 0 warnings, 0 informations。

## Merge Danger

**Door:** 单向 —— 这是纯前端形态对齐，没有任何数据面或写路径。`app/alibot/*.py` 一个字节未动
（`git diff --stat 63e8163...HEAD` 里没有一个 `.py`）。回退本 PR 就是恢复「设置齿轮在 rail 底部、
搜索是 rail-actions 里的文字按钮、私信是文字按钮、Agent 带文字与方块机器人图标」的旧形态。

**Blast Radius:** 小 —— 4 个 studio 文件 + 3 个前端测试文件（+ 两份文档）。`#btnSearch` 的 id 与
点击后的行为、`#btnNewDm` / `#btnNewAgent` / `#btnSettings` 的绑定、`⌘K` 的全局快捷键语义、
搜索视图自身形态全部未变。

- **有意偏离原型**（两处，均登记在 Answer §1.2 / §2.5）：① 原型 `:524` 的 `⌘K` 徽标**不搬**
  （人定，理由见上）；② 原型「私信」`.group-label` 没有 `+`，本票按票面 ④ 加了一个。
- **不做**（票面 Constraints）：不整条左栏 / 全工作台逐项复核；不改搜索视图自身形态；不新增主题
  快捷键或太阳图标；不碰主列 / dock / 弹窗 / `.nav-row` / 头像 / 状态点 / 徽标。
- **必须一起改的测试**：`app/tests/mobile_shell.test.cjs` 把 rail 按钮序写死为
  `["btnNewChannel","btnSearch","btnNewDm","btnNewAgent"]`，本票拆成 head / `.search-shell` /
  actions 三段并按新 DOM 序重算 Tab 断言——不改这一处，既有用例必红。
- **遗留**：私信 `+` 是 18×18，低于设计系统 `--tap-min: 44px`（照原型 `:544` 的分组 `+` 尺寸，
  本轮不放大，CSS 规则旁已登记）。
