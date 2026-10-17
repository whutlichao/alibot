<!-- 本文件是 #103 的 Answer（逐条可复现读数）；tracker 上的 issue #103 保持原样，摘要回填为 comment。 -->

# Issue #103 Answer —— Studio 左栏四处入口对齐原型（提醒+设置同回 rail 头部 / 恢复 `.search-shell` / rail-actions 收为两按钮 / 私信入口挪到分组 `+`）

> 票面：GitHub [#103](https://github.com/whutlichao/alibot/issues/103)（实施票 UI 对齐修复；父票 #43）。
> 人定裁决：**D1**（齿轮回头部、不要太阳）｜**D2**（恢复 `.search-shell` 形态，点击仍进主线视图）｜
> **D3**（文字「私信」挪到分组标题的 `+`）｜**D4**（只修这四处，不做整条左栏复核）。
> 另有一条本票新增的人定：**⌘K 徽标删除**（见 §1.2）。

| 项 | 值 |
| --- | --- |
| 分支 | `rail-header-entries` |
| 提交 | `bcda9b8` feat（四处入口落地 + 词条 + 测试）/ `6358c2f` fix（双轴 review 收口：删 ⌘K 徽标）+ 本文件与 PR 正文的 docs 提交 |
| 本票 base | `63e8163`（`main`，即 #102 合并后的读数） |
| PR | [#104](https://github.com/whutlichao/alibot/pull/104)（base `main`，head `rail-header-entries`） |
| PR body | `app/specs/issue-103-pr-body.md`（`--body-file` 用） |

---

## 0. 交付概览

| 落点 | 动作 |
| --- | --- |
| `app/alibot/studio/index.html` | ① `#btnSettings` 从 `.rail-foot` 移进 `.rail-head`（序：品牌 → 提醒 → 设置）；② 新增 `.search-shell` > `.search-btn#btnSearch`（放大镜 + 词条文案，**无** ⌘K 徽标）；③ `.rail-actions` 收为 `#btnNewChannel` + 纯图标 `#btnNewAgent`（圆形加号）；④ `#btnNewDm` 移进「私信」分组标签的 `+`，标签文字改挂内层 `<span data-i18n>`。 |
| `app/alibot/studio/app.css` | 新增 `.search-shell` / `.search-btn` / `.search-btn span` 三条（原型 `ui_kits/app/app.css:74-82` 的对应四条，去掉徽标那条）；`.group-label` 改为 flex 两栏并补 `.group-label button`（原型 `:544` 的 `+` 形态）；`.rail-actions` / `.search-shell` 定义位置按 DOM 序重排。 |
| `app/alibot/studio/app.js` | **仅三处注释**：设置入口的落点、搜索入口的落点、私信入口的新落点。**绑定一行未改**。 |
| `app/alibot/studio/i18n.js` | 新增 `rail.searchPlaceholder`（中「搜索消息、任务…」/ 英 `Search messages, tasks…`）；`rail.search` / `rail.newDm` / `rail.newAgent` 三条已无节点引用，**两份表同步删除**。 |
| `app/tests/search_ui.test.cjs` | 新增 3 条：入口形态（含「按钮上没有任何 kbd」）、点击进主线视图、`⌘K` 开面板而非搜索视图。 |
| `app/tests/i18n_ui.test.cjs` | 新增 1 条「两枚 icon-btn 同处 rail-head + 顺序 + `.rail-foot` 无控件」；既有提醒用例改在 head 片段内断言。 |
| `app/tests/mobile_shell.test.cjs` | 替身的 rail 拆成 head / `.search-shell` / actions 三段（原先把四个 id 平铺在一个 `.rail-actions` 里），Tab 序断言按新 DOM 序重算。 |

**未改**：`app/alibot/*.py`（zero 后端改动）、`alibot-design-system/**`、`docs/adr/**`、`CONTEXT.md`、
主列 / dock / 弹窗 / `.nav-row` / 头像 / 状态点 / 徽标。

---

## 1. 人定与代答登记

### 1.1 票面既有的人定（用户本人，2026-10-17）

| # | 裁定 | 落地 | 证据 |
| --- | --- | --- | --- |
| D1 | 齿轮移回头部，**不要**太阳图标、不新增主题快捷键 | `#btnSettings` 在 `.rail-head` 内、序为 品牌 → 提醒 → 设置；图标仍是原型 `:515` 的「圆 + 八条射线」齿轮；无任何主题快捷键 | `index.html:70-76`；浏览器 `headOrder = ["brand","btnReminders","btnSettings"]` |
| D2 | 恢复 `.search-shell` 形态，点击仍进主线搜索视图 | `.search-shell` > `.search-btn#btnSearch`（放大镜 + 文案）；`showSearchView` 语义未变 | `index.html:82-88`；`SEARCH_VIEW_AFTER {"viewSearchHidden":false,"activeId":"searchInput"}` |
| D3 | 文字「私信」移除，挪到「私信」分组标题的 `+` | `.rail-actions` 里已无「私信」按钮；`#btnNewDm` 是「私信」`.group-label` 内唯一的 button | `index.html:117`；`newDmParentIsDmLabel = true`、`dmPlusCount = 1` |
| D4 | 只修这四处，不做整条左栏复核 | diff 只落在 4 个 studio 文件 + 3 个测试文件（+ 两份文档） | `git diff --stat 63e8163...HEAD` |

### 1.2 本票新增的人定（用户本人，2026-10-17）—— ⌘K 徽标删除

双轴 review 的 Standards 轴以 **P1 / BLOCK** 提出：搜索按钮上的 `<kbd>⌘K</kbd>` 承诺了一个该按钮
做不到的能力。依据是仓库既有纪律（`app.js` 命令面板一节的注释原文）：

> 「⌘K 真绑定的是**打开命令面板**本身（不是搜索视图），因此这里也不给它挂 ⌘K 标签——界面不承诺一个
> 按键做不到的事。」

以及 `app/specs/issue-60-spec.md` 的 **D10**（「本仓纪律是界面不承诺不存在的能力，照抄未绑定的 kbd
提示就是把原型噪音当成契约」）。命令集（`app.js` 的 `commandList()`）里没有「搜索」一条，`⌘K`
到不了搜索——与 dispatch Change ② 那句「⌘K 徽标直接写在标记里」正面冲突。**这是票面与仓库标准
的冲突，不是我能自决的事**，故按行为规则 1 走 `orca orchestration ask`。

裁定（回信原文）：

> 这条同时把 spec 里的「⌘K 徽标是符号不是文案」那句作废上位于本裁定：以本回复为准。

落地（`6358c2f`）：删掉 `<kbd>⌘K</kbd>`，保留按钮本身；`.search-btn kbd` 随之成为死规则，删除；
`search_ui.test.cjs` 的入口形态用例改为断言「按钮上没有任何 `kbd`、不出现 `⌘K`」，并新增一条把
理由钉进测试（`⌘K` 打开的是命令面板，`#viewSearch` 保持 hidden）。

**该裁定作废了原型 `alibot-app.html:524` 的那枚徽标落在 studio**——这是有意偏离，登记在此以免下次
逐项原型复核把它当成新的不一致。

### 1.3 三条 P2 的处置（代答，per coordinator 授权）

| # | Finding | 处置 |
| --- | --- | --- |
| P2-1 | **Mysterious Name**：`rail.searchPlaceholder` 装的是按钮**可见文案**，与 `search.fieldPlaceholder`（输入框 placeholder）同名不同义 | **不改**：dispatch Change ② 明文指定了这个键名，且它表达的就是「搜索入口上的提示语」 |
| P2-2 | 私信 `+` 的触控目标 18×18，低于设计系统声明 `--tap-min: 44px` | **照原型不放大**：这是原型 `:544` 分组 `+` 的尺寸；已在 `app.css` 该规则旁登记「低于 `--tap-min`，本轮照原型不放大」 |
| P2-3 | `.search-btn kbd` 是本文件第三份「kbd 小芯片」规则（另有 `.search-field kbd` / `.cmd-field .kbd`） | **随徽标删除已成死规则，直接删**（coordinator 指定）；未去合并 `.search-field kbd` / `.cmd-field .kbd`（超出本票范围） |

---

## 2. 验收逐条

### 2.1 前端四支（本票 base `63e8163` 之上）

| 命令 | 改动前（base） | 改动后 | 判据 |
| --- | --- | --- | --- |
| `node --test app/tests/i18n_ui.test.cjs` | 43 pass / 0 fail | **44 pass / 0 fail** | 只许持平或增加 ✓ |
| `node --test app/tests/search_ui.test.cjs` | 13 pass / 0 fail | **16 pass / 0 fail** | ✓ |
| `node --test app/tests/agent_detail_ui.test.cjs` | 19 pass / 0 fail | **19 pass / 0 fail** | ✓ |
| `node --test app/tests/mobile_shell.test.cjs` | 9 pass / 0 fail | **9 pass / 0 fail** | ✓ |

（这些读数取自 `6358c2f` 的工作区，不是改动前的旧读数。）

### 2.2 质量门禁

| 命令（cwd = `app/`） | 读数 |
| --- | --- |
| `ruff check .` | All checks passed! |
| `ruff format --check .` | 103 files already formatted |
| `python -m mypy .` | Success: no issues found in 50 source files |
| `node ~/.pi-lens/tools/node_modules/pyright/index.js alibot tests` | 0 errors, 0 warnings, 0 informations |

本票零 `.py` 改动（`git diff --stat 63e8163...HEAD` 里没有一个 `.py`），因此不跑后端 pytest 全量
（门禁 G-impl 的约定）。

### 2.3 真实浏览器证据（ego-browser，桌面 1982×1001 @dpr2）

**装置（隔离，未碰 8788 实例与默认数据目录）**：

```sh
# Before：git archive 出 base 的 app/ 子树 + 软链设计系统目录，令 /design/* 也能服务
git archive 63e8163 app/alibot | tar -x -C /tmp/rail-103/baseline-root
ln -s <repo>/alibot-design-system /tmp/rail-103/baseline-root/alibot-design-system

# After：本分支工作区
cd <repo> && PYTHONPATH=<repo>/app python -m alibot --port 8791 --data-dir /tmp/rail-103/data-after
cd /tmp/rail-103/baseline-root && PYTHONPATH=. python -m alibot --port 8792 --data-dir /tmp/rail-103/data-baseline
```

| URL | 端口 | 数据目录 |
| --- | --- | --- |
| `http://127.0.0.1:8791/studio/`（After） | 8791 | `/tmp/rail-103/data-after` |
| `http://127.0.0.1:8792/studio/`（Before） | 8792 | `/tmp/rail-103/data-baseline` |

**服务端字节自证**（避免缓存 / 旧实例的假绿）：

```text
$ curl -s http://127.0.0.1:8791/studio/index.html | md5 -q ; md5 -q app/alibot/studio/index.html
6db76569f22243e5083e276464e0a8ce
6db76569f22243e5083e276464e0a8ce      # ← 浏览器拿到的字节 = 本 worktree 的文件
```

**逐条读数**（同一支探针分别打两个端口，读数来自 `/tmp/rail-103/evidence/PF-after.txt` 与
`/tmp/rail-103/evidence/PF-baseline.txt`）：

| 判据 | Before（`63e8163`，8792） | After（`6358c2f`，8791） |
| --- | --- | --- |
| rail 宽 / 右边界 | `{x:0, w:252, right:252}` | `{x:0, w:252, right:252}` |
| `.rail-head` 的 `gap` | `8px` | `8px` |
| `.rail-head` 子节点序 | `["brand","btnReminders"]` | `["brand","btnReminders","btnSettings"]` |
| `#btnReminders` | `{x:209, y:14, w:30, h:30, right:239}` | `{x:171, y:14, w:30, h:30, right:201}` |
| `#btnSettings` | `{x:213, y:962, w:30, h:30}`（**底部**） | `{x:209, y:14, w:30, h:30, right:239}`（**头部**） |
| 两者 y 相同 | `false`（14 vs 962） | **`true`**（14 = 14） |
| 两者都落在 rail 内 | `true` / `true` | **`true` / `true`** |
| `.search-shell` 存在 | **`null`**（不存在） | `{x:0, y:56, w:251, h:46, right:251}` |
| `.search-shell` 宽 ≤ rail 宽 | — | **`true`**（251 ≤ 252） |
| `#btnSearch` 的 class | `btn`（文字按钮） | **`search-btn`** |
| `#btnSearch` 内的 `kbd` 数量 | `0` | **`0`**（人定删除，见 §1.2） |
| `#btnSearch` 文案 | `"搜索"` | `"搜索消息、任务…"`（走 `rail.searchPlaceholder`） |
| `.rail-actions` 直接子按钮数 | **4** | **2** |
| `.rail-actions` 子节点 | `["btnNewChannel","btnSearch","btnNewDm","btnNewAgent"]` | **`["btnNewChannel","btnNewAgent"]`** |
| `#btnNewAgent` 盒子 | `{x:229, w:80, right:309}` → **right 309 > 252，被裁切** | `{x:199, w:40, right:239}` → **right 239 ≤ 252** ✓ |
| `#btnNewAgent` 有文字 | `true`（"Agent"） | **`false`**（纯图标） |
| `#btnNewAgent` 图标 | `<rect .../>`（方块机器人） | **`<path d="M12 5v14M5 12h14"/><circle cx="12" cy="12" r="9" opacity=".35"/>`**（圆形加号） |
| `#btnNewDm` 盒子 | `{x:171, y:56, w:50, h:32}` | `{x:217, y:244, w:18, h:18}` |
| `#btnNewDm` 的父节点是「私信」`.group-label` | **`false`**（在 `.rail-actions`） | **`true`** |
| 私信分组标签内的 `+` 数量 | `0` | **`1`** |
| `.rail-foot` 子节点 | `["meAvatar","","presence online","btnSettings"]` | **`["meAvatar","","presence online"]`**（不再有控件） |
| 六个入口是否都在 rail 内 | `newAgent: false`（其余 true） | **全 `true`** |

**行为面（桌面断点，同一支探针）**：

```text
SEARCH_VIEW_BEFORE  {"viewSearchHidden":true}
SEARCH_VIEW_AFTER   {"viewSearchHidden":false,"viewMessagesHidden":true,"activeId":"searchInput"}
   → 点 .search-btn 进主线搜索视图，且焦点落在 #searchInput（与改动前一致）
DM_MODAL            {"dmOverlayHidden":false,"title":"新建私信"}
   → 点「私信」分组标题的 + 打开新建私信弹窗
SETTINGS_MODAL      {"settingsOverlayHidden":false}
   → 点头部的齿轮打开设置弹窗
CMDK                {"cmdOverlayHidden":false,"activeId":"cmdInput","searchViewHidden":true}
CMDK_CLOSED         {"cmdOverlayHidden":true}
   → 全局 ⌘K 行为未变：打开命令面板、ESC 关闭；且它不进搜索视图（故按钮上不印它）
```

**截图**：rail 局部 `evidence/FINAL2-after-rail-clip.png`（头部铃铛 + 齿轮并排、搜索入口通栏、
「新建频道」+ 圆形加号两枚、「私信 + `+`」）、整页 `evidence/FINAL2-after-desktop.png`、
≤900px 抽屉 `evidence/FINAL2-after-mobile-drawer.png`。

### 2.4 ≤900px 断点（480×900，三态 + 开合两条路径）

| 状态 | topbar | rail 可见性 / transform | `.is-open` | scrim | `#railToggle` 的 `aria-expanded` | 焦点 |
| --- | --- | --- | --- | --- | --- | --- |
| 收起（初始） | `flex` | `hidden` / `translateX(-252px)` | `false` | `hidden` | `false` | — |
| `#railToggle` 打开 | `flex` | `visible` / `none` | `true` | 可见 | `true` | `btnReminders`（head 里第一枚控件） |
| 点 scrim 收起 | `flex` | `hidden` / `translateX(-252px)` | `false` | `hidden` | `false` | `railToggle`（还给触发者） |
| 再打开 + ESC 收起 | `flex` | `hidden` / `translateX(-252px)` | `false` | `hidden` | `false` | `railToggle` |

抽屉打开的瞬间六枚入口全部落在 rail 内（`withinRail` 六个键全 `true`），`.search-shell` 宽
251 ≤ 252，`.rail-actions` 仍是 2 个按钮，`#btnNewDm` 仍在「私信」标签内。
`headHeight` 在开合两态都是 56（与基线一致，`.brand-sub` 未折行）：
`subLines = 1`、`headHeight = 56`（Before 与 After 同为 56）。

### 2.5 双轴 code-review（`63e8163...HEAD`，两轴分开、不合并排序）

#### Standards 轴

结论：**1 条 P1（已人定并修复）**，另 3 条 P2（登记/不扩 scope）。

| # | Finding | 处理 |
| --- | --- | --- |
| S-1 **P1** | `index.html` 的 `<kbd>⌘K</kbd>` 承诺了该按钮做不到的能力（`⌘K` 打开的是命令面板，命令集里没有「搜索」）；与 `app.js` 该节注释、规格 D10 正面冲突 | **人定删除**（§1.2），`6358c2f`。原型 `:524` 的徽标因此不搬，登记为有意偏离 |
| S-2 P2 | Mysterious Name：`rail.searchPlaceholder` 与 `search.fieldPlaceholder` 同名不同义 | **不改**（dispatch 明文指定键名），§1.3 P2-1 |
| S-3 P2 | Duplicated Code：`.search-btn kbd` 是第三份「kbd 小芯片」规则 | **已随徽标删除成为死规则，直接删**；合并另两处超出本票范围，§1.3 P2-3 |
| S-4 P2 | 私信 `+` 的 18×18 低于 `--tap-min: 44px`（≤900px 时正落在抽屉里） | **照原型尺寸保留**，在 `app.css` 规则旁登记，§1.3 P2-2 |

Standards 轴同时确认的三条「Correct」：键集不分叉（三个旧键两份表同步删除、全仓 grep 零命中）；
新增/改动的中文节点都挂了键（含为何把 `rail.groupDms` 挂在内层 `<span>` 而不是外层
`.group-label`——`applyStaticI18n` 是 `textContent =`，挂外层会把 `+` 按钮冲掉）；`app.css` 新增规则
零裸色值、`data-od-id` 全部保留。

#### Spec 轴

结论：**无 P0/P1，OK with notes**。四项改动逐条落地；既有 `id` / `data-od-id` / 绑定 / 全局 `⌘K`
行为均未变；后端零改动迹象；键集不分叉；用例数 44/16/19/9 符合「只许持平或增加」。

| # | Finding | 处理 |
| --- | --- | --- |
| P-1 P2 | 改了 dispatch Target 清单外的 `app/tests/mobile_shell.test.cjs` 与 `app.js` 的三处注释 | **必要**：该测试的替身把 rail 按钮序写死为 `["btnNewChannel","btnSearch","btnNewDm","btnNewAgent"]`，不改就红。已在 PR 正文与本节点明原因 |
| P-2 P2 | 原型 `:543-545` 的「私信」`.group-label` 只有文字、**没有** `+`；本票在私信标签新增了 `+` | **dispatch ④ 明确要求的落点**，属有意偏离原型；登记在此以免下次逐项复核当成新的不一致 |
| P-3 P2 | `#btnSettings` 进 head 后 252px 下 head 可用宽度减少约 38px，`.brand-sub` 无单行约束、可能折行 | **已实测排除**：`headHeight` 两态都是 56（Before 也是 56），`subLines = 1`，两枚 icon-btn 的 y 都是 14 |
| P-4 P2 | 该轴所在会话只有只读工具、无法跑 `gh` / 命令，四项验收（浏览器、质量门禁、后端零改动、PR head）留给 supervisor 核实 | **本 Answer 已逐条补齐**（§2.1–§2.4、§3） |

---

## 3. 交付物

| 项 | 值 |
| --- | --- |
| worktree | 干净（`git status --short` 空） |
| 提交 | `bcda9b8`（feat）+ `6358c2f`（review 收口）+ 本文件与 PR 正文的 docs 提交 |
| 分支已推送 | `rail-header-entries` → `origin`，head = 本票最终提交 |
| PR | [#104](https://github.com/whutlichao/alibot/pull/104)（base `main`，head `rail-header-entries`） |
| PR body | `app/specs/issue-103-pr-body.md`（`gh pr create --body-file` / `--body-file` 更新） |
| #103 摘要 comment | 已发（逐条读数摘要 + Answer / PR 指针） |
| `worker_done` | 从本 worker 终端发送并已收到成功回执（`succeeded`，带 Task ID / Dispatch ID） |

---

## 4. 已知边界与遗留

1. **原型徽标不搬**（§1.2）：`alibot-app.html:524` 的 `⌘K` 徽标是**有意**不在 studio 落地的。
   若将来要让 `⌘K` 真的进搜索视图，那是改既有全局快捷键语义的另一张票。
2. **私信 `+` 不达 `--tap-min`**（§1.3 P2-2）：18×18 是原型尺寸，本票照原型不放大。
3. **`.search-shell` 只有三条规则**：原型第四条 `.search-btn kbd` 随徽标删除（无节点带它）。
4. **`search_ui.test.cjs` 的入口断言与原型相反**：它断言「按钮上没有 kbd」，这是人定裁定的机器判据，
   不是测试写松了。
