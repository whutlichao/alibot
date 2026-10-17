## Summary

Studio 左栏不再有「工作区未读 N / Workspace unread」那一行。删掉的是一层**纯展示汇总**
（把 `state.channels` / `state.dms` 的 `unread_count` 相加再渲染），后端与 HTTP 契约一字未动：
`GET /api/workspace` 的 `unread_total` 照旧返回，只是前端不再消费它。

```diff
 app/alibot/studio/index.html
   <div class="rail-actions"> 新建频道 / 新建 Agent </div>
-  <div class="rail-unread" id="unreadSummary" hidden data-od-id="workspace-unread" aria-live="polite">
-    <span data-i18n="rail.unread">工作区未读</span>
-    <span class="badge" id="unreadTotal">0</span>
-  </div>
   <nav class="rail-scroll" data-od-id="nav-lists">
     ［频道］ ← #channelList，行内 unread-<id> 徽标照旧
     ［私信］ ← #dmList，行内 unread-<id> 徽标照旧

 app/alibot/studio/app.css
-  .rail-unread { … }
-  .rail-unread[hidden] { display: none; }
   .badge { … }        ← 行内徽标样式（未动）

 app/alibot/studio/app.js
   state = { …, - unreadTotal: 0, … }
   loadWorkspace()      - state.unreadTotal = Number(snapshot.unread_total) || 0
   scheduleRead()       - syncUnreadTotal()
   refreshDmList()      - syncUnreadTotal(); - renderUnreadSummary()
   refreshChannelList() - syncUnreadTotal(); - renderUnreadSummary()
   render()             - renderUnreadSummary()
   - function syncUnreadTotal() { … }
   - function renderUnreadSummary() { … }
   syncUnreadBadge(row, channel.unread_count, 'unread-' + channel.id)   ← 未动
   syncUnreadBadge(row, dm.unread_count,      'unread-' + dm.id)        ← 未动

 app/alibot/studio/i18n.js
   zh-CN: - 'rail.unread' / - 'rail.unreadCount'
   en:    - 'rail.unread' / - 'rail.unreadCount'

 app/tests/i18n_ui.test.cjs
+  一条守护用例（红 → 绿，见 Evidence）
```

```diff
  app/alibot/studio/index.html
  app/alibot/studio/app.css
  app/alibot/studio/app.js
  app/alibot/studio/i18n.js
+ app/tests/i18n_ui.test.cjs              # +131/−1（含 DOM 替身的 remove 语义）
+ app/specs/workspace-unread-removal-pr-body.md   # 本文件
```

**刻意未动**（票面约束）：`app/alibot/db.py` / `service.py` / `api.py` 的未读口径与 `unread_total`、
`app/tests/*.py`（一行未改）、频道/私信行内 `unread-<id>` 徽标、搜索命中 `<mark>` 高亮、
`--unread*` 色族 token、rail 其它结构、`CONTEXT.md` / ADR / `app/specs/` 下既有文档。

## Evidence

### 1) 守护用例：先红后绿（红证据取自 base `566899d` 的隔离 worktree）

`app/tests/i18n_ui.test.cjs` 里新增一条用例，断言 ① 标记里不再有 `id="unreadSummary"` /
`data-od-id="workspace-unread"` / `.rail-unread` / `rail.unread*` 绑定，且工具栏与名单之间不留任何节点；
② `#channelList`、空态、`rail.groupChannels` 仍在；③ 两份词条都没有落单键；④ `app.js` 里
`unreadTotal` / `syncUnreadTotal` / `renderUnreadSummary` 连同 `rail.unread*` 引用一并消失；
⑤ 渲染后频道与私信行内的未读徽标仍在、读数照后端走、归零时被摘掉；⑥ `.badge` 的 `--unread` 样式仍在。

**Before**（base `566899d` 隔离 worktree，把本分支的用例原样放进去跑）：

```text
$ node --test app/tests/i18n_ui.test.cjs        # @ /tmp/baseline-unread
✖ 左栏不再有「工作区未读」汇总行，频道与私信行内的未读徽标照旧
ℹ tests 45
ℹ pass 44
ℹ fail 1

✖ failing tests:
test at app/tests/i18n_ui.test.cjs:1916:1
✖ 左栏不再有「工作区未读」汇总行，频道与私信行内的未读徽标照旧 (0.778375ms)
  AssertionError [ERR_ASSERTION]: 左栏不再有未读汇总容器

  true !== false
```

**After**（本分支）：

```text
$ node --test app/tests/i18n_ui.test.cjs
✔ 左栏不再有「工作区未读」汇总行，频道与私信行内的未读徽标照旧 (5.9ms)
ℹ tests 45
ℹ pass 45
ℹ fail 0
```

### 2) 受影响 Studio 套件（本分支，全绿）

| 套件 | 读数 |
| --- | --- |
| `node --test app/tests/i18n_ui.test.cjs` | **45 passed / 0 failed** |
| `node --test app/tests/rail_identity.test.cjs` | **8 passed / 0 failed** |
| `node --test app/tests/search_ui.test.cjs` | **16 passed / 0 failed** |
| `node --test app/tests/agent_detail_ui.test.cjs` | **19 passed / 0 failed** |
| `node --test app/tests/mobile_shell.test.cjs` | **9 passed / 0 failed** |

### 3) 后端等价证明（零改动）

```text
$ ruff check app
All checks passed!

$ ruff format --check app
115 files already formatted

$ python -m pytest -q          # 本分支，492 passed, 1 skipped
.............................................................            [100%]
492 passed, 1 skipped in 300.14s (0:05:00)
```

> 环境注记：本机 shell 里 `http_proxy` / `no_proxy` 含 `[::1]` 一项，httpx 解析代理白名单时会把它读成
> 端口 `:1]`，于是 `test_agent_seam_client_lifecycle.py` 的 4 条用例在任何分支上都失败
> （base `566899d` 隔离 worktree 上同样 4 failed，同一句 `InvalidURL: Invalid port: ':1]'`）。
> 上面那次全绿是把 `*_proxy` / `*_PROXY` / `NODE_USE_ENV_PROXY` 一并 unset 后跑的；本分支与 base
> 在这 4 条上的对比读数完全一致，因此与本次改动无关。

### 4) 产品面：渲染后的左栏没有那一行

本地起 `python -m alibot --port 8799 --data-dir /tmp/alibot-unread-demo`（工作区里有一个频道、
`unread_total = 3`、频道行 `unread_count = 3`），用本机 Chrome 无头渲染 `/studio/` 后 dump DOM：

```text
DOM 体积: 49790 字符
unreadSummary      出现次数: 0
workspace-unread   出现次数: 0
rail-unread        出现次数: 0
id="channelList"         : True
data-i18n="rail.groupChannels": True
工具栏闭合 → 名单之间: '\n\n    '     ← 只有空白，没有残留节点
```

同一份渲染结果里 `.rail-actions` 与 `<nav class="rail-scroll" data-od-id="nav-lists">` 直接相邻，
频道分组照旧；左栏截图（本地 `/tmp/rail-unread-removed.png`）里只有「搜索 / 新建频道 + / 频道 / 私信 /
本机 AGENT」，没有「工作区未读」行。

### 5) 双轴 `code-review`（Standards / Spec）与逐条处置

**Standards 轴结论**：无硬性文档标准违规（本仓库没有 `CONTRIBUTING.md` / `CODING_STANDARDS.md`；
AGENTS.md 只有 CodeGraph/浏览器/语言三条规则，约束实际来自 `CONTEXT.md` 的 `## Language`）。
顺带修掉一处术语漂移：删掉的 `'rail.unread': '工作区未读'` 违反 `CONTEXT.md` 对
「**工作台 (Workspace)**」的 `_Avoid_: 工作区`。`syncUnreadTotal()` 的删除符合「业务状态的唯一正本是
后端；前端不持正本」与「`unread_total` = channel 型 + dm 型之和」两条。5 个文件的改动不构成
Shotgun Surgery：每处都是该关注点的唯一住所。

**Spec 轴结论**：票面 Target 四项与 Change 全部落地，约束逐条通过（后端 `.py` 零改动、
`unread-<id>` 徽标照旧、`--unread*` / `<mark>` 未动、numstat 无四位数噪声、i18n 键集/参数位/裸中文扫描全绿）。

| # | 轴 | finding | 处置 |
| --- | --- | --- | --- |
| 1 | Standards（judgement call） | 用例里频道徽标的 `data-od-id` 断言与 `rail_identity.test.cjs:615` 重复 | **已改**：频道侧只留「徽标在 + 读数」；新增私信侧 `unread-dm_1` 断言（原先全仓库无 DM 徽标覆盖） |
| 2 | Standards（judgement call） | 四条否定断言看着同义 | **判为保留**：`id` / `data-od-id` / `.rail-unread` / `rail.unread*` 是四条独立回归面，各自对应块内一件东西；用例里已写明 |
| 3 | Standards（judgement call） | 「不留空节点」靠 `indexOf` 切片数 `<div`，属性换序即误报、残留空 `<span>` 会漏检 | **已改**：新增 `railGap()`，按标签深度找 `.rail-actions` 的闭合点，断言到 `nav-lists` 之间只剩空白；注释先剥掉 |
| 4 | Standards（judgement call） | 直扫 `app.js` 原文，违背本文件「注释不算文案」的扫描纪律 | **已改**：`stripBlockComments` + 去行注释后用 `\b` 词边界 |
| 5 | Standards（judgement call） | `FakeNode.remove()` 不清 `parentNode`，替身会说谎 | **已改**：`removeChild()` 里把 `child.parentNode` 置空，`remove()` 复用同一条语义 |
| 6 | Standards（复核新增） | HTML 半边没剥注释，与同文件三处先例不一致 | **已改**：整条用例的 `html` 一律先剥 `<!-- -->` |
| 7 | Standards（复核新增） | `assert.match(css, /\.badge \{/)` 会被 `.mono, .count, .badge {` 满足，真规则删了仍绿 | **已改**：改为 `\.badge \{[^}]*var\(--unread\)` |
| 8 | Spec | 交付物 6（PR 正文 / 推送 / PR）缺失 | **本次交付**：本文件 + 推分支 + `gh pr create --body-file` |
| 9 | Spec | 红/绿证据只在本机，未落到交付物 | **已落盘**：见 Evidence 1（红证据取自 base 隔离 worktree） |
| 10 | Spec | `FakeNode.remove()` 加了却没调用点（无用脚手架） | **已改**：新增「未读归零时徽标被摘掉」用例，真实走到 `syncUnreadBadge → badge.remove()` |
| 11 | Spec | 只断言频道徽标，票面 Change 还要求私信徽标照旧 | **已改**：私信侧断言 `unread-dm_1` + 读数，并把归零断言挂在私信上（避免与 `rail_identity` 的频道侧重复） |
| 12 | Spec | 字符串扫描可能漏掉改名后的等价逻辑 | **部分处置**：改为词边界 + 剥注释，且补了 `rail.unread*` 引用为零；「换名再造一份汇总」不在单条用例可判定范围内，由 `CONTEXT.md` 的「前端不持正本」与后续 review 守 |

## Merge Danger

**Door:** two-way

纯 Studio 展示层移除：没有改数据、HTTP 字段或持久化格式，`/api/workspace` 的 `unread_total` 与各端点
读数一字未动，频道 / 私信行内的 `unread-<id>` 徽标全部保留。回退就是 revert 本 PR，不需要数据迁移，
也没有旧客户端兼容面。

**Blast Radius:** layout

只影响 Studio 左栏 `.rail-actions` 与 `.rail-scroll` 之间那一段高度：那一行消失后，频道分组整体上移
约一行（`--sp-3` + `--fs-mono-micro` 量级）。后端、CLI、事件流、搜索、任务看板、移动端外壳均不受影响。
