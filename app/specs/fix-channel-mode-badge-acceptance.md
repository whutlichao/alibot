# 验收取证：频道头显示协作模式 pill（自主 / 队长制 · 队长名）

**票型**：实施票（前端补丁，无后端 / schema 改动）｜**门禁**：G-impl（合并由 coordinator 执行）
**Ticket**：本票**没有**对应的 GitHub issue，也**不新建**——沿用仓库 `fix-*` 先例
（`app/specs/fix-dock-fallback-no-channel-*` / `fix-sse-target-fail-fast-*`）：交付件用描述性命名，
PR 正文不带 issue 关联。
**base**：`c606b4a`（= 派活时 worktree 的 HEAD / 分支 `channel-mode-badge` 的起点）
**branch**：`channel-mode-badge`（本 worktree：`/Users/apple/orca/workspaces/alibot/channel-mode-badge`）
**采集时间**：2026-10-18（第 1 次 attempt）｜2026-10-19（第 2 次 attempt：`app.js` 隐藏态返修 + 证据重做，见 §11）｜**环境**：macOS（aarch64），Node（`node --test`，无 npm 依赖），
Python 3.11.13（仓库根 `.venv` 的解释器，本 worktree 用符号链接接上：`.venv -> ../../../alibot/.venv`）

> **返修摘要（第 2 次 attempt）**：第 1 次 attempt 的 `app.js` 隐藏态用 `pill.removeAttribute(...)`，而另外 5 个
> 用例文件的 DOM 替身没有这个方法 ⇒ 5 个文件真红、`i18n_ui.test.cjs` 整个子进程挂住（全量 glob 因此永不结束）。
> 本轮把隐藏态改成**写空串**（只用 `setAttribute`），红全转绿；根因（异常被 `loadWorkspace()` 的 catch 当成
> 「后端不可达」并排上重连定时器）与逐文件读数见 §11，浏览器证据已按新字节全部重拍（§4）。

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**实际跑出来的结果**。红证据在**隔离的基线副本**
`/tmp/cmb-red` 上取得（三份 studio 文件取自 `c606b4a`，`app.js` 的 md5 与基线逐字节相同，见 §2），
**不动**本 worktree 的工作区，也不碰用户正在跑的实例与它的数据目录。

---

## 0. 前置 skill 的执行

前置 skill：`/Users/apple/.pi/agent/skills/implement/SKILL.md`。它要求：先读规格 → 按 TDD 在约定接缝写
用例（先红后绿）→ 经常跑类型检查与单文件用例、收尾跑全量 → 收尾调用 `code-review` → 提交到当前分支。
本票的实际顺序逐条照做：

| 步骤 | 本票实际做的事 | 证据 |
| --- | --- | --- |
| 先读规格 | 逐条拆票面 Change 1–7 与 Constraints，落到 5 个 Target 文件 | §1 |
| TDD 红 | 先往 `app/tests/channel_config_ui.test.cjs` 写 8 条 pill 用例 + 1 条词条键用例，在基线副本上跑 | §2（`pass 0`→ 基线副本 `pass 8 / fail 9`） |
| 绿 | 实现后同一条命令 `17 pass / 0 fail` | §2、§3 |
| 类型检查 / 单文件 | 每改完一处跑 `node --test app/tests/channel_config_ui.test.cjs`；`scripts/check.sh --fast` 含 ruff / mypy / pyright | §3 |
| 全量 | `node --test app/tests/*.test.cjs`：`188 pass / 0 fail` | §3 |
| code-review | 两轴并行子代理（Standards + Spec），结论分开写 | §6 |
| 提交 | 一个 commit 在分支 `channel-mode-badge`，随后推送 | §7、§9 |

**本票不跑全量 pytest 与迁移链专项的理由**：本票 diff 里**没有一个 `.py` 文件**（`git diff --name-only
c606b4a...HEAD` 只有 `app/alibot/studio/*` 与 `app/tests/channel_config_ui.test.cjs`、`app/specs/**`），
没有 API / 数据库 / schema 改动，因此全量 pytest 与迁移链专项不在本票门禁内；门禁用 `scripts/check.sh --fast`
（ruff check / ruff format --check / mypy / pyright / node --test glob）加 `node --test app/tests/*.test.cjs`。

---

## 1. 改动面与落点

| 文件 | 动作 |
| --- | --- |
| `app/alibot/studio/index.html` | `.chan-top` 内新增 `<span class="chan-mode-pill" id="chanModePill" hidden>`，位置在 `</h1>`（`#chanTitle` 所在标题）之后、`.chan-tools` 之前；**不**放进 `.chan-title` |
| `app/alibot/studio/app.js` | 新增频道头 pill 的一段：`channelConfigsInFlight`（在飞去重）/ `cacheChannelConfig()`（配置缓存唯一写入口）/ `channelModeBadge()`（读数 → 文本）/ `channelModeLeaderName()` / `renderChannelModePill()`（只画缓存）/ `loadActiveChannelMode()`（读数入口）；`renderHeader()` 末尾调 `renderChannelModePill()`；`loadWorkspace()` 与 `selectTarget()` 各调一次 `loadActiveChannelMode()`；`loadTaskChannelConfig()` 加在飞去重并改走 `cacheChannelConfig()`；`loadChannelConfig()` / `submitChannelConfig()` 的缓存写入改走 `cacheChannelConfig()` |
| `app/alibot/studio/app.css` | 新增 `.chan-mode-pill` 一条（中性面 + 边框 + 次级文字，token 按名消费；不新造色位）；`.chan-title span` 那条 ellipsis 规则**一字未动** |
| `app/alibot/studio/i18n.js` | 新增两条同键词条：`channelConfig.modeBadge.title`（zh `协作模式：{mode}` / en `Collaboration mode: {mode}`）、`channelConfig.modeBadge.teamWithLeader`（zh `队长制 · {leader}` / en `Team · {leader}`）；模式名本身复用既有 `channelConfig.mode.autonomous` / `channelConfig.mode.team` |
| `app/tests/channel_config_ui.test.cjs` | 新增 8 条 pill 用例（见 §2.2），词条用例补两条新键；文件头清单同步 |
| `app/specs/fix-channel-mode-badge-acceptance.md` / `-pr-body.md` / `-evidence/*.png` | 本文件、PR 正文、浏览器截图 |

**没有改动**（票面 Constraint 逐条）：后端 `.py`（`_channel_payload` / `_channel_config_view` / `GET /api/channels`
载荷 / 端点 / schema / 迁移）；`.chan-title span` 的 ellipsis 语义；`renderMobileTopbar()` 的标题格式；
任务线程 `#ttTeamNotice` 的既有语义（本票不删它，也不改它）。

### 1.1 数据正本与读取点

```text
GET /api/channels/{id}/config ──┐
                                ├─→ loadTaskChannelConfig()  ← 唯一读取路径（在飞去重）
GET /api/channels/{id}/members ─┘        │
                                         ├─→ cacheChannelConfig()  ← 配置缓存唯一写入口
                                         │        └─→ renderChannelModePill()（只画缓存，不发请求）
                                         └─→ channelMembers[id]（队长显示名）
```

- **读取点两处**：`loadWorkspace()`（载入工作台）与 `selectTarget()`（切 target）。`renderHeader()` 只画缓存，
  不在渲染里发请求。
- **配置缓存唯一写入口**：`cacheChannelConfig()`。三个原本各写一次 `state.channelConfigs[id] = …` 的地方
  （弹窗打开、保存成功、任务视图读取）现在都走它，于是「保存成功后 pill 立马更新」不需要在保存路径上再写一遍。
- **读不到 / 还没读回** ⇒ `state.channelConfigs[id]` 为空 ⇒ pill `hidden`（不伪造 team / autonomous 结论；
  下一次切 target 或重载工作台再试，与 `loadTaskChannelConfig` 的既有纪律同一条）。

---

## 2. 红 → 绿证据

### 2.1 基线副本（改前）与命令

不还原本 worktree 的任何文件；用 `git archive` 把**基线 commit** 的三份 studio 文件导出到隔离副本，
只把**本票最终的测试文件**拷进去：

```sh
$ mkdir -p /tmp/cmb-base-full && git -C <worktree> archive c606b4a | tar -x -C /tmp/cmb-base-full
$ rm -rf /tmp/cmb-red && mkdir -p /tmp/cmb-red/app/alibot/studio /tmp/cmb-red/app/tests
$ for f in app/alibot/studio/app.js app/alibot/studio/app.css app/alibot/studio/index.html \
           app/alibot/studio/i18n.js app/alibot/studio/product-tokens.css; do
    git show c606b4a:$f > /tmp/cmb-red/$f ; done
$ cp app/tests/channel_config_ui.test.cjs /tmp/cmb-red/app/tests/
$ md5 -q /tmp/cmb-red/app/alibot/studio/app.js ; git show c606b4a:app/alibot/studio/app.js | md5
6ce5d7ea6580346c57878f7273a32273
6ce5d7ea6580346c57878f7273a32273          # ← 与基线逐字节相同，证明副本确实是「改前」
```

用例的 `vm` 导出串对本票新增的读数入口 `loadActiveChannelMode` 用 `typeof` 判存
（未声明的标识符在 `typeof` 下不抛错），于是**同一份用例**能在改前 / 改后两个 revision 上跑：

```sh
if (typeof loadActiveChannelMode === 'function')
  globalThis.__channelConfigTest.loadActiveChannelMode = loadActiveChannelMode;
```

### 2.2 三条命令的读数

| 命令 | 读数 |
| --- | --- |
| `node --test app/tests/*.test.cjs`（`c606b4a` 的干净归档 `/tmp/cmb-base-full`） | **tests 180 / pass 180 / fail 0**（`duration_ms` 3759.04） |
| `node --test app/tests/channel_config_ui.test.cjs`（**改前** `/tmp/cmb-red`） | **tests 17 / pass 8 / fail 9**（`duration_ms` 约 30） |
| `node --test app/tests/channel_config_ui.test.cjs`（**改后** 本 worktree） | **tests 17 / pass 17 / fail 0**（`duration_ms` 138.78） |
| `node --test app/tests/*.test.cjs`（**改后** 本 worktree） | **tests 188 / pass 188 / fail 0**（`duration_ms` 3779.82） |

改前失败清单（逐字抄自 `/tmp/cmb-evidence/red-baseline-final-tests.txt`）——9 条恰是本票新增的 9 条
（8 条 pill + 1 条词条键），既有 8 条**照旧绿**（这条很关键：红的只是新行为）：

```text
✖ 新增词条在 zh-CN / en 两份词条表里都取到值
✖ team 频道：pill 显示「队长制 · 队长名」；配置还没读回时先隐藏，不伪造结论
✖ autonomous 频道：pill 显示「自主」
✖ pill 的落点：在 #chanTitle 之后、.chan-tools 之前，且不在 .chan-title 里（那里带 ellipsis）
✖ 私信下 pill 隐藏：私信没有频道 mode
✖ 频道配置读不到：pill 隐藏，不伪造 team / autonomous 结论
✖ 队长成员读不到：pill 只显示「队长制」，不加占位名
✖ 在设置面把 team 改回 autonomous 并保存：pill 立刻变「自主」（不刷新页面）
✖ 切频道：pill 按新频道的读数重铺，不残留上一个频道的模式
ℹ tests 17
ℹ pass 8
ℹ fail 9
```

改后（本 worktree）：

```text
$ node --test app/tests/channel_config_ui.test.cjs
ℹ tests 17
ℹ pass 17
ℹ fail 0

$ node --test app/tests/*.test.cjs
ℹ tests 188
ℹ pass 188
ℹ fail 0
```

**基线对照**：`180`（改前全量）+ `8`（本票新增 pill 用例）= `188`（改后全量）；词条键用例是既有用例的
扩展，不新增计数。

### 2.3 八条用例断言的外部可观察事实

| # | 用例 | 断言什么 |
| --- | --- | --- |
| a | team 频道：pill 显示「队长制 · 队长名」 | 配置读回前 `hidden === true`；读回后 `textContent === "队长制 · Ada"`、`title` / `aria-label` 含「协作模式」与当前值且两者同值 |
| b | autonomous 频道：pill 显示「自主」 | `textContent === "自主"`、`hidden === false`、`aria-label` 含「自主」 |
| c | pill 的落点 | `index.html` 里 pill 的位置在 `#chanTitle` 之后、`.chan-tools` 之前，且 `<h1 class="chan-title">` 在 pill 之前已闭合（= 不在 `.chan-title` 里）；`.chan-title span` 的 ellipsis 规则原样存在；`.chan-mode-pill` 有自己的 pill 样式 |
| d | 私信下 pill 隐藏 | `hidden === true`、`textContent === ""`（不残留上一个频道的文本） |
| e | 频道配置读不到 | `hidden === true`、`textContent === ""`（不伪造结论） |
| f | 队长成员读不到 | `textContent === "队长制"`（不加占位名） |
| g | 设置面把 team 改回 autonomous 并保存 | 保存前 `队长制 · Ada`；PATCH 后**同一页面**（不重载）pill 变 `自主` |
| h | 切频道 | `ch_1`（team）→ `队长制 · Ada`；切到 `ch_2`（autonomous）后 `自主`，不残留旧频道读数 |

用例只驱动行为入口（`renderHeader()` / `loadActiveChannelMode()` / 设置面的按钮与保存），断言落在文本、
`hidden`、`title` / `aria-label` 与 `index.html` 的结构上；不读任何内部布尔，不调 `channelModeBadge()` 这类
实现辅助函数。

---

## 3. 门禁读数（第 2 次 attempt 实测，2026-10-19）

每条门禁命令都**自带外限时**（本票硬约束 §7）：逐文件跑用外层 wall-clock 上限 90s，全量 glob 上限 240s，
`scripts/check.sh --fast` 上限 600s。

```sh
$ node --test --test-timeout=20000 app/tests/*.test.cjs          # 外层上限 240s
ℹ tests 188
ℹ pass 188
ℹ fail 0
ℹ duration_ms 6071.59                                     # 7s 结束（不再挂住）

$ scripts/check.sh --fast                                        # 外层上限 600s
== ruff check
All checks passed!
== ruff format --check（只检查；不要跑 ruff format，它会整文件重排）
（通过）
== mypy（仓库根）
Success: no issues found in 92 source files
== pyright
0 errors, 0 warnings, 0 informations
== node --test（Studio DOM 用例族：glob，新增文件自动纳入）
ℹ tests 188
ℹ pass 188
ℹ fail 0

门禁全绿                                                 # 13s，exit 0
```

逐文件（13 个 `.test.cjs`，每个文件一条命令、各自 90s 上限）：

| 文件 | 读数 |
| --- | --- |
| `agent_detail_ui.test.cjs` | 19 / 19 pass |
| `approval_ui.test.cjs` | 7 / 7 pass |
| `channel_budget_ui.test.cjs` | 11 / 11 pass |
| `channel_config_ui.test.cjs` | 17 / 17 pass（本票用例） |
| `channel_resources_ui.test.cjs` | 16 / 16 pass |
| `i18n_ui.test.cjs` | 53 / 53 pass，5s 退出（§11.3） |
| `mobile_shell.test.cjs` | 9 / 9 pass |
| `narration_ui.test.cjs` | 11 / 11 pass |
| `rail_identity.test.cjs` | 8 / 8 pass |
| `run_panel_ui.test.cjs` | 12 / 12 pass |
| `search_ui.test.cjs` | 16 / 16 pass |
| `silent_ui.test.cjs` | 5 / 5 pass |
| `sse_events_ui.test.cjs` | 4 / 4 pass |
| 合计 | **188 / 188 pass / 0 fail** |

未跑 `ruff format`（仓库纪律：它会整文件重排）；`git diff --numstat` 只有真实改动行（§5）。

**本票不跑全量 pytest 与迁移链的理由（再次明确）**：`git diff --name-only c606b4a...HEAD | grep -c '\.py$'` = 0，
本票 diff 不含任何 Python 文件，不动 `GET /api/channels` 载荷、不加端点、不动 schema 与迁移，因此全量 pytest
与迁移链专项不在本票门禁内；门禁 = `scripts/check.sh --fast` + `node --test app/tests/*.test.cjs`。

---

## 4. 真实浏览器证据（ego-browser 0.6.0.12 / 真实 Chromium + 真实 uvicorn）

**第 2 次 attempt 全部重拍**：`app.js` 字节已变（隐藏态的写法从「摘属性」改成「写空串」，见 §11），
旧截图与新字节不对应，因此证据图**全部重新生成**（最终 13 张，`ls -la` 显示 09:09–09:10；第 1 次 attempt 的
07:31–07:32 旧图全部被覆盖，目录里没有留下与新字节不符的过期图）。装置换成新的隔离组合：
数据目录 `/tmp/alibot-cmb-data`（干净）、端口 8823（`lsof -nP -iTCP:8823` 采集前为空；第 1 次 attempt 遗留在
8811 的临时服务已停掉，避免读到旧数据目录）。

> 重拍顺序：`round-01`→`round-05` 采图，`round-07` 把种子状态（team + 队长 Ada）与 1440×900 视口复原。
> 第 6 轮脚本因上一轮保存后弹窗仍开着、`#btnChannelSettings` 被遮罩挡住而失败——这本身也说明「保存后不关窗」是
> 既有行为；`round-07` 改成「弹窗开着就地改回 team」后跑通，不影响任何证据读数。

**装置**（独立数据目录 + 空闲端口 8823，**未碰**用户正在跑的实例与默认数据目录）：

```sh
$ rm -rf /tmp/alibot-cmb-data && mkdir -p /tmp/alibot-cmb-data
$ PYTHONPATH=<worktree>/app /Users/apple/orca/alibot/.venv/bin/python -m alibot \
    --port 8823 --data-dir /tmp/alibot-cmb-data
INFO:     Uvicorn running on http://127.0.0.1:8823 (Press CTRL+C to quit)
$ curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8823/studio/
200
```

**种子**：全部走**真实 HTTP 写路径**（`/tmp/cmb-evidence/seed-8823.py`，用 `urllib`）：

```text
POST /api/agents   ×2      → Ada（mem_ba7e0b8f654a）、Bob（mem_f37bc8a1ed1d）
POST /api/channels ×2      → 「设计系统重构」ch_463b634720a5（成员 Ada + Bob）
                             「自主频道」  ch_eb69558ae3b0（成员 Ada + Bob）
PATCH /api/channels/ch_463b634720a5/config → mode=team, team_leader_member_id=Ada
POST /api/dms      ×1      → 与 Ada 的私信 dm_c10ac7b9b17f
```

**逐项读数**（逐字抄自 ego-browser 各轮脚本的输出；脚本在 `/tmp/cmb-evidence-2/round-0{1..5,7}.mjs`）：

| 验收项 | 浏览器读数 | 截图 |
| --- | --- | --- |
| team 频道（队长 Ada）→ `队长制 · Ada` | `TEAM-1440 {"text":"队长制 · Ada","title":"协作模式：队长制 · Ada","ariaLabel":"协作模式：队长制 · Ada","hidden":false,"chanTitle":"设计系统重构","titleRight":382,"pillLeft":394,"pillWidth":86,"pillInsideChanTitle":false,"pillDisplay":"flex","pillVisible":true,"toolsRight":1420}` | `team-channel-1440.png` |
| autonomous 频道 → `自主` | `AUTONOMOUS-1440 {"text":"自主","ariaLabel":"协作模式：自主","hidden":false,"chanTitle":"自主频道","pillVisible":true}` | `autonomous-channel-1440.png` |
| 私信 → pill 隐藏且不带可读标签 | `DM-1440 {"text":"","hidden":true,"titleAttr":"","ariaLabel":"","chanTitle":"@Ada","pillVisible":false,"pillDisplay":"none","mobileTopbarTitle":"@Ada"}` | `dm-hidden-1440.png` |
| 设置面把 team 改回 autonomous 并保存 → pill 立即变 `自主`（不刷新页面） | 保存前 `BEFORE-SAVE {"text":"队长制 · Ada","ariaLabel":"协作模式：队长制 · Ada","chanTitle":"设计系统重构"}`；保存后 `AFTER-SAVE-1440 {"text":"自主","ariaLabel":"协作模式：自主","hidden":false,"noReloadMark":"kept","navigationEntries":1,"overlayStillOpen":true,"configErrorHidden":true}` | `settings-saved-autonomous-1440.png` → 关窗后 `channel-header-autonomous-1440.png` |
| 三档视口 1440×900 | 见上两行（`pillVisible: true`、`pillLeft 394 > titleRight 382`、`pillDisplay: flex`） | `team-channel-1440.png`、`channel-header-autonomous-1440.png` |
| 三档视口 1024×768 | `TEAM-1024 {"text":"队长制 · Ada","pillVisible":true,"pillWidth":86}` / `VIEWPORT-1024 {"text":"自主","pillVisible":true,"pillRight":437,"toolsRight":1004}` | `team-channel-1024.png`、`channel-header-autonomous-1024.png` |
| 三档视口 430×900（≤900 移动壳） | `TEAM-430 {"text":"队长制 · Ada","pillVisible":true,"pillRight":223,"viewport":430,"mobileTopbarTitle":"#设计系统重构","mobileTopbarHasPill":false}` / `VIEWPORT-430 {"text":"自主","pillVisible":true,"pillRight":179,"viewport":430,"mobileTopbarVisible":true,"mobileTopbarTitle":"#设计系统重构","mobileTopbarHasPill":false}` | `team-channel-430.png`、`channel-header-autonomous-430.png` |
| 设置面改回 team（收尾复原） | `RESTORED-TEAM {"text":"队长制 · Ada","ariaLabel":"协作模式：队长制 · Ada","overlayStillOpen":true}`（`round-04`）/ `RESTORED {"text":"队长制 · Ada","ariaLabel":"协作模式：队长制 · Ada"}`（`round-07` 收尾） | — |
| 证据覆盖面（本轮补拍，回应 review P2「只有一档视口覆盖私信 / 保存」） | `DM-1024 {"text":"","hidden":true,"titleAttr":"","ariaLabel":"","pillVisible":false,"chanTitle":"@Ada"}` / `DM-430 {"text":"","hidden":true,"pillVisible":false,"chanTitle":"@Ada","mobileTopbarTitle":"@Ada"}` / `SAVE-1024 {"text":"自主","ariaLabel":"协作模式：自主","noReloadMark":"kept","navigationEntries":1,"overlayStillOpen":true}` / `SAVE-430 {"text":"自主","hidden":false,"overlayStillOpen":true,"mobileTopbarTitle":"#设计系统重构"}` | `dm-hidden-1024.png`、`dm-hidden-430.png`、`settings-saved-autonomous-1024.png`、`settings-saved-autonomous-430.png` |

**「不刷新页面」的自证**：保存前在页面里打窗口标记 `window.__cmbNoReloadMark = "kept"`，保存后读回仍是
`kept`，且 `performance.getEntriesByType("navigation").length === 1`（整页导航只有最初那一次）——pill 的文本
却已经从 `队长制 · Ada` 变成 `自主`。这是「按响应里的新配置就地重铺」，不是重新加载。

**隐藏态的新证据（本轮新增）**：私信读数里 `titleAttr` 与 `ariaLabel` 都是**空串**而不是残留的上一频道文案，
这正是本轮把隐藏态从 `removeAttribute` 改成写空串之后的可观察结果——真实 Chromium 里 `title=""` 不弹提示、
`aria-label=""` 在 accname 里落到下一层，隐藏件因此没有任何可读名字；而 `pillDisplay: "none"` 说明它确实不可见。

**移动端顶栏不变**：430×900 下 `#mtTitle` 仍是 `#设计系统重构`（`mobileTopbarHasPill: false`），
`renderMobileTopbar()` 的 `#名` / `@名` 格式未动。

**服务端字节自证**（浏览器实际拿到的文件 = 本 worktree 当轮的文件）：

```sh
$ for f in app.js index.html app.css i18n.js; do \
    echo "$f served=$(curl -s http://127.0.0.1:8823/studio/$f | md5 -q) worktree=$(md5 -q app/alibot/studio/$f)"; done
app.js served=b40e1c2634688eba726a33ff5ec515cd worktree=b40e1c2634688eba726a33ff5ec515cd MATCH
index.html served=c77485a6e3903092ab7cf449e8c9cc2d worktree=c77485a6e3903092ab7cf449e8c9cc2d MATCH
app.css served=d76c41c8fbb4fb6d079cad9c6c94af65 worktree=d76c41c8fbb4fb6d079cad9c6c94af65 MATCH
i18n.js served=91e7a29f9df705a114cfce4521d32953 worktree=91e7a29f9df705a114cfce4521d32953 MATCH
```

（`app.js` 的 `b40e1c26…` 是**最终字节**（含 review 后收紧的那段注释）对应的读数；第 1 次 attempt 的
`e854f3e7…`、返修中间态的 `794a39c4…` 都已作废，13 张图全部在最终字节上重拍。浏览器侧另有旁证：
`TEAM-1440` 的 `appJsSrc` 是 `http://127.0.0.1:8823/studio/app.js`，资源读数 `356473/356173` 字节。）

**live 数据目录纪律**：只用了 `/tmp/alibot-cmb-data` 与 8823 端口（干净目录、空闲端口），
用户的默认数据目录与常驻实例全程未被触碰；证据采集结束后服务已停止。

## 5. 改动清单

```sh
$ git diff --numstat c606b4a -- app/alibot/studio app/tests     # 含第 2 次 attempt 的返修
9       0       app/alibot/studio/app.css
123     10      app/alibot/studio/app.js
4       0       app/alibot/studio/i18n.js
7       0       app/alibot/studio/index.html
213     2       app/tests/channel_config_ui.test.cjs

$ git diff --name-only c606b4a | grep -c '\.py$'
0
```

- **没有整文件重排 / 重格式化**：`app.js` 的 10 行删除全是「三个缓存写入点 + 任务视图读取函数体替换」；
  第 2 次 attempt 只动 `renderChannelModePill()` 的隐藏态表达（8 增 5 删），没有碰其它函数。
- **没有 `.py`**：⇒ 全量 pytest / 迁移链不在本票门禁（§3 已给理由）。
- **未跟踪残留**：交付前 `git status --porcelain` 只剩本文件与 `-pr-body.md`（它们在本 commit 里入库）。

## 6. 双轴 code-review

方法：按 `/Users/apple/.pi/agent/skills/code-review/SKILL.md`，固定点 = `c606b4a`，被审 revision = `c606b4a...HEAD`
（含功能提交 `8ac6093` 与返修提交 `f27237a`）。**Standards** 与 **Spec** 两轴各起一个只读子代理并行审同一 diff，
两轴结论分开记录、互不抵消；标准来源按 `docs/agents/review-rules.md`（`CONTEXT.md` 的 `## Language`、`docs/adr/**`、
`app/README.md`、`AGENTS.md`、`docs/agents/**`）+ 该 skill 自带的坏味道基线。

### 6.1 Standards 轴

**结论：无硬性文档违规 → 可合并（附评审意见）。** 子代理逐条认可：pill 落点在 `.chan-title` 之外、CSS 只按名消费
既有 token、两条同键词条两侧齐备、`cacheChannelConfig()` 把三处缓存写入收成一个入口（顺手消掉了重复形状）、
用例断言落在 `getAttribute` 的可观察结果上。对这次返修的两个点名判断：

- 「隐藏态写空串 vs `removeAttribute`」**可接受**，不是硬违规——仓库文档只规定「沿用既有 vm + DOM 替身、不新开接缝」，
  没规定属性写法；并指出真正的瑕疵是**注释的形**：六行注释把 DOM 替身缺口与 `node --test` 挂死写进生产渲染注释。
- 「删掉本票用例里的 `FakeNode.removeAttribute`」**一致**：`app.js` 全文已无 `removeAttribute` 调用，
  `rail_identity.test.cjs` 的替身保有它且未被本票触碰。

其余 P2（judgement call）与处理：

| # | finding | 处理 |
| --- | --- | --- |
| S1 | **注释把测试事实写进生产注释**（Feature/形 问题） | **已修**：注释收紧为三行生产理由（空串语义 + 为什么不用可选方法），替身缺口与挂起现象留在本文 §11 |
| S2 | `channelModeLeaderName()` 读 `state.activeChannelId`（possible Feature Envy） | **不改**：唯一调用方是 `channelModeBadge(state.channelConfigs[channel.id])`，而 `channel` 恒为 `activeChannel()`，两者同源；加 `channelId` 形参会把内部一致性变成调用方责任，属接口扩大。记入 §9 遗留 |
| S3 | `channelModeBadge` 与 DOM 里的 `pill` 命名不一 | **不改**：功能提交的命名已被 coordinator 认可，改名只增加 diff 噪声，收益不抵 |
| S4 | `mode` 字面量在三处判（Repeated Switches） | **不改**：三处语境不同（读配置视图 / 铺 pill / 任务线程），抽成一张表反而变成新的「第四份词表」 |
| S5 | `title` 与 `aria-label` 同值两处重复 | **不改**：两处各 2 行，抽一个只有两处调用的一行包装属 Middle Man |
| S6 | pill 的两个读取点（`loadWorkspace` / `selectTarget`）未被用例直接驱动 | **承认并记录**：本轮不加用例（`selectTarget` 未在测试导出表里，加导出=新开 seam，票面 Constraint 禁止）；真实载入路径由浏览器证据覆盖（§4 `TEAM-1440` 走真实工作台载入），记入 §9 遗留 |
| S7 | `typeof loadActiveChannelMode === 'function'` 条件导出像 Speculative Generality | **保留**：它存在的目的就是让**同一份用例**能在改前 revision（`/tmp/cmb-red`）上跑出红证据（§2.1）；删掉会让红证据不可复跑 |

### 6.2 Spec 轴

**结论：一条 BLOCK（P1×2）→ 两条均已处理，复审口径下可合并。**

| # | finding | 处理 |
| --- | --- | --- |
| P1-a | 交付面第 5 条要求的 `-pr-body.md` 缺失、§7 只剩占位 | **已修**：`app/specs/fix-channel-mode-badge-pr-body.md`（Summary / Evidence / Merge Danger）已落盘，PR [#180](https://github.com/whutlichao/alibot/pull/180) 用 `--body-file` 创建；PR 号回填本文 §7 |
| P1-b | §11.2 把 i18n 挂起的入口写成「启动时 `loadWorkspace()`」，与源码不符（该文件 harness 把启动三行换成了导出块） | **已修**：核实后改正——入口是该文件「附件：发送时把草稿标识随消息带上」用例走 `deliver()`，`deliver()` 发完消息 `await loadWorkspace({silent:true, keepActive:true})` 才进到渲染路径；§11.2 补了带 `at async deliver` 的探针栈与「用例仍 pass、进程却挂住」的解释。机制结论（catch → `scheduleReconnect()` → 定时器永不收敛）不变 |
| P2 | 私信隐藏与「保存后立即变自主」只有 1440 一档视口 | **已修**：补 `dm-hidden-1024.png`、`dm-hidden-430.png`、`settings-saved-autonomous-1024.png`、`settings-saved-autonomous-430.png`，共 13 张（§4） |

两轴最差项：Standards = S1（P2，已修）；Spec = P1-a / P1-b（均已修）。

## 7. PR

- **PR**：[#180 — fix(studio): channel mode badge — hidden state without removeAttribute](https://github.com/whutlichao/alibot/pull/180)
- **base / head**：`main` ← `channel-mode-badge`
- **正文（`--body-file`）**：`app/specs/fix-channel-mode-badge-pr-body.md`（模板照 `/Users/apple/.pi/agent/skills/pr/SKILL.md`：
  Summary / Evidence / Merge Danger）
- **不开 GitHub issue**：沿用仓库 `fix-*` 先例（`fix-dock-fallback-no-channel-*` / `fix-sse-target-fail-fast-*`），
  正文不带 issue 关联。

```sh
$ gh pr create --base main --head channel-mode-badge \
    --title "fix(studio): channel mode badge — hidden state without removeAttribute" \
    --body-file app/specs/fix-channel-mode-badge-pr-body.md
https://github.com/whutlichao/alibot/pull/180
```

## 8. 决策账（Q → 谁答 → 答案 → 依据）

| # | 问题 | 谁答 | 答案 | 依据 |
| --- | --- | --- | --- | --- |
| D1 | pill 放在 `.chan-title` 里还是外面？ | 票面（Change 2） | **外面**：`.chan-top` 内、`#chanTitle` 之后、`.chan-tools` 之前 | `.chan-title span` 带 `overflow:hidden` + ellipsis，放进去会被截断；用例 c 用 `index.html` 的字节位置钉住 |
| D2 | 队长名从哪儿取？ | 票面（Change 1） | `GET /api/channels/{id}/members` 里 `team_leader_member_id` 对应成员的 `display_name`；读不到只显示「队长制」 | 用例 a / f；不编人名，不留占位 |
| D3 | 配置读不到时显示什么？ | 票面（Change 3） | `hidden`——不伪造 team / autonomous 结论 | 用例 e；与 `loadTaskChannelConfig` 既有纪律同一条 |
| D4 | 读取点放哪儿（会不会在渲染里发请求）？ | 本票 | `loadWorkspace()` / `selectTarget()` 两处显式读，`renderHeader()` 只画缓存 | `render` 里发请求会在 SSE 帧频繁重绘时打后端；`loadActiveChannelMode()` 同时管配置与队长成员表两跳 |
| D5 | 保存成功后怎么立刻更新？ | 票面（Change 5） | 配置缓存写入收敛到 `cacheChannelConfig()` 一个入口，写下就重铺 pill | 三处原有写入点（弹窗打开 / 保存 / 任务视图）全部走它；浏览器证据里 `noReloadMark` 仍在 |
| D6 | 要不要新增「队长制 · 队长名」整句词条？ | 票面（Change 6） | **要**：`channelConfig.modeBadge.teamWithLeader`（zh `队长制 · {leader}` / en `Team · {leader}`）；模式名复用既有两条 | 裸中文扫描（`i18n_ui.test.cjs`）与同键集守卫；界面与源码里不写裸中文 |
| D7 | 移动端顶栏要不要也带 pill？ | 票面（Change 4） | **不要**：`renderMobileTopbar()` 一字未动，430×900 下 `#mtTitle` 仍是 `#名` | 浏览器读数 `mobileTopbarHasPill: false` |
| D8 | 红证据怎么取才不动工作区？ | 本票 | `git archive` / `git show <rev>:<file>` 拼隔离副本（`/tmp/cmb-red`、`/tmp/cmb-base-full`），副本 `app.js` md5 与基线一致后开跑 | §2；本 worktree 全程未被还原 |

---

## 9. 剩余风险与显式遗留

1. **队长显示名的两跳异步**：`loadActiveChannelMode()` 先读配置、再（team 且成员表还没读回时）补读成员表，
   读齐后才铺 pill。若成员表读取很慢，pill 会在「读回配置但成员表还没到」的窗口里短暂显示「队长制」
   而不是「队长制 · Ada」——这是「不编人名」的代价；DOM 用例 g/h 与浏览器证据都在读齐之后取值，
   没有把它钉成「永不出现中间态」。
2. **不覆盖任务驱动下的 pill 变化**：pill 的读数在载入工作台 / 切 target 时刷新；若在**同一频道内**由别人
   （例如另一个浏览器窗口）改了 mode，本页面不会自动跟着变——本仓没有频道配置的推送通道（只有任务 / 消息
   SSE），这次也不新开端点。保存路径是同一页面内的唯一写路径，它已按响应就地更新。
3. **隐藏态用的是「空串属性」而不是「没有属性」**：`renderChannelModePill()` 在隐藏时写
   `title=""` / `aria-label=""`，不再调用 `removeAttribute`。两条写法在真实 DOM 里对外等价（空 `title` 不弹提示、
   空 `aria-label` 在 accname 里落到下一层，且 pill 本身 `hidden`），但**属性节点仍存在**——若将来有人写
   「DOM 里不许出现 title 属性」这类结构断言，要按新写法调整。选它的理由见 §11.2：`removeAttribute` 是可选
   方法，5 个既有用例文件的 DOM 替身没有它，用它就把替身缺口变成了渲染期异常。
4. **`≤900px` 抽屉态**：pill 在频道头（`.chan-head`）里，430×900 截图证明它仍可见且不被标题挤掉；
   但移动顶栏那一行按票面要求**不**带 pill，两条信息面因此不同（顶栏只有 `#名`）。
5. **review 留下的两条接口级遗留**（§6.1 S2 / S6，本轮按票面 Constraint 不动）：
   ①`channelModeLeaderName()` 读的是 `state.activeChannelId` 的成员表，而不是形参里的频道——当前唯一调用方
   `channelModeBadge(state.channelConfigs[channel.id])` 的 `channel` 恒为 `activeChannel()`，两者同源，所以是
   一致的；将来若有「给非当前频道的配置做预览」的需求，要加 `channelId` 形参，否则会取错成员表。
   ②pill 的两个读取点（`loadWorkspace()` / `selectTarget()`）没有被 DOM 用例直接驱动（用例走的是
   `loadActiveChannelMode()` 这个读数入口，`selectTarget` 也不在测试导出表里）。真实载入路径由浏览器证据覆盖
   （§4 `TEAM-1440` 是真实工作台载入后的读数），但「删掉 `selectTarget()` 里那行 `await` 仍全绿」这个缺口还在。
6. **未跑全量 pytest / 迁移链**：票面明确排除（diff 里没有一个 `.py`），理由见 §0 末段。

---

## 10. 复跑清单（全部用到的命令）

> 本票硬约束：**每条门禁命令自带外限时**。逐文件用 `/tmp/runfile.sh`（`node --test --test-timeout=20000 <file>`，
> 外层 wall-clock 上限 90s，超时即 kill 并报 124）；全量 glob 上限 240s；`scripts/check.sh --fast` 上限 600s。

```sh
# 基线（改前）全量 —— 干净归档，不动工作区
git archive c606b4a | tar -x -C /tmp/cmb-base
cd /tmp/cmb-base && node --test --test-timeout=20000 app/tests/*.test.cjs     # 180 / 180 pass

# 红（第 1 次 attempt 的 app.js 隐藏态写法复刻在 8ac6093 的隔离归档上）
git archive 8ac6093 | tar -x -C /tmp/cmb-red2
#   在 /tmp/cmb-red2/app/alibot/studio/app.js 的 renderChannelModePill() 里补回
#   `} else { pill.removeAttribute('title'); pill.removeAttribute('aria-label'); }`
cd /tmp/cmb-red2
for f in channel_budget_ui channel_resources_ui run_panel_ui search_ui mobile_shell; do
  /tmp/runfile.sh app/tests/$f.test.cjs 90
done
/tmp/runfile.sh app/tests/i18n_ui.test.cjs 40                                  # 超时被 kill（124）

# 绿（本 worktree，逐文件 + 全量）
for f in app/tests/*.test.cjs; do /tmp/runfile.sh "$f" 90; done                # 13 个文件全绿
node --test --test-timeout=20000 app/tests/*.test.cjs                          # 188 / 188 pass，7s
scripts/check.sh --fast                                                        # 门禁全绿，13s

# 浏览器装置（第 2 次 attempt：新端口 8823 + 干净数据目录）
rm -rf /tmp/alibot-cmb-data && mkdir -p /tmp/alibot-cmb-data
PYTHONPATH=<worktree>/app .venv/bin/python -m alibot --port 8823 --data-dir /tmp/alibot-cmb-data
<worktree>/.venv/bin/python /tmp/cmb-evidence/seed-8823.py
ego-browser nodejs < /tmp/cmb-evidence-2/round-01.mjs   # 以及 round-02 / 03 / 04 / 05 / 07
for f in app.js index.html app.css i18n.js; do
  echo "$f served=$(curl -s http://127.0.0.1:8823/studio/$f | md5 -q) worktree=$(md5 -q app/alibot/studio/$f)"
done
```

---

## 11. 第 2 次 attempt：5 个用例文件真红 → 全绿，以及 `i18n_ui.test.cjs` 挂住

### 11.1 红证据（隔离副本，不动本 worktree）

装置：`git archive 8ac6093`（= 本票的功能提交，`app.js` 与第 1 次 attempt 的**已提交**版本一致）导出到
`/tmp/cmb-red2`，只在 `renderChannelModePill()` 里补回第 1 次 attempt 那份**未提交**的隐藏态写法：

```js
  } else {
    pill.removeAttribute('title');
    pill.removeAttribute('aria-label');
  }
```

逐文件跑（每个文件一条命令、90s 上限），读数与 coordinator 派活时给的一致：

| 文件 | 改前（红，`/tmp/cmb-red2`） | 改后（绿，本 worktree） |
| --- | --- | --- |
| `channel_budget_ui.test.cjs` | **1 pass / 10 fail**（exit 1，2s） | **11 pass / 0 fail** |
| `channel_resources_ui.test.cjs` | **1 pass / 15 fail** | **16 pass / 0 fail** |
| `run_panel_ui.test.cjs` | **1 pass / 11 fail** | **12 pass / 0 fail** |
| `search_ui.test.cjs` | **13 pass / 3 fail** | **16 pass / 0 fail** |
| `mobile_shell.test.cjs` | **8 pass / 1 fail** | **9 pass / 0 fail** |
| `i18n_ui.test.cjs` | **53 条全部打印 pass，进程永不退出**（40s 上限 kill，exit 124） | **53 pass / 0 fail，5s 退出** |
| 其余 7 个文件 | 全绿 | 全绿 |
| 全量 glob | 卡在 `i18n_ui` 上，永不结束 | **188 / 188 pass / 0 fail，7s** |

红的样子（同一个异常，逐字抄自 `/tmp/gate-channel_budget_ui.test.log`）：

```text
✖ 私信视图隐藏预算入口（频道设置面只属于频道）
  TypeError: pill.removeAttribute is not a function
      at renderChannelModePill (app/alibot/studio/app.js:4938:10)
      at Object.renderHeader (app/alibot/studio/app.js:6098:3)
      at Object.loadDm (app/tests/channel_budget_ui.test.cjs:323:11)
```

### 11.2 根因与修法

**根因（两层）**：

1. **接缝层**：`renderChannelModePill()` 的隐藏分支调用 `pill.removeAttribute(...)`。真实 DOM 有这个方法，
   但 5 个**既有**用例文件各自带一份 DOM 替身（`_ui.test.cjs` 那一族），它们的 `FakeNode` 只实现
   `setAttribute` / `getAttribute`，没有 `removeAttribute`。于是「新增代码用到的方法」与「既有替身实现了的方法」
   之间出现缺口——而缺口落在**渲染路径**上，每个用例只要载入工作台 / 切 target 就会踩到。
   第 1 次 attempt 只给自己改的 `channel_config_ui.test.cjs` 补了这个方法，等于把缺口留在另外 5 份替身上。
2. **挂起层**：这个 `TypeError` 不是被测试框架抓到的，而是被 `loadWorkspace()` 的 `try/catch` 当成
   「后端不可达」吞掉了，随后 `scheduleReconnect()` 排上 1.5s 的重连定时器。定时器每次重试又抛同一条，
   于是**定时器永远在**：`node --test` 的子进程不退出 ⇒ 父进程等不到子进程结束 ⇒ 全量 glob 永不返回。
   这就是 `i18n_ui.test.cjs`「53 条全部打印通过、进程却挂住」的原因；根因不在该文件自身（该文件的 harness
   把启动三行 `applyStaticI18n(document); bindEvents(); loadWorkspace();` 换成了导出块，**启动时不读工作台**），
   所以**没有**改它。

**`i18n_ui.test.cjs` 走进渲染路径的入口**（review 追问后补取的栈，见下一段探针）：该文件里「附件：发送时把草稿
标识随消息带上」这条用例走 `deliver()`，而 `deliver()` 发完消息后会 `await loadWorkspace({ silent: true,
keepActive: true })` 重读工作台——这正是 `loadWorkspace()` 里那行 `await loadActiveChannelMode()` 被执行的时机。
异常在 `loadWorkspace()` 的 catch 里被吞掉，所以**该用例照旧 pass**，但 catch 里排上的重连定时器留在了事件循环里。

挂起的直接证据（在隔离副本 `/tmp/cmb-red2-probe` 里对 `loadWorkspace()` 的 catch 与 `scheduleReconnect()`
各加一行 `console.error` 探针，只改副本、不改工作区）：

```text
$ node --test --test-timeout=20000 app/tests/i18n_ui.test.cjs   # 7–8s 后 kill（进程不会自己退出）
[probe] loadWorkspace caught: pill.removeAttribute is not a function
 TypeError: pill.removeAttribute is not a function
    at renderChannelModePill (app/alibot/studio/app.js:4939:10)
    at loadActiveChannelMode (app/alibot/studio/app.js:4951:5)
    at loadWorkspace (app/alibot/studio/app.js:1084:11)
    at async deliver (app/alibot/studio/app.js:3371:5)          ← 入口：deliver() 发完消息重读工作台
[probe] scheduleReconnect armed
✔ 附件：发送时把草稿标识随消息带上，然后从 composer 里收走 (7.487417ms)   ← 用例仍然 pass
…
[probe] loadWorkspace caught: Cannot read properties of undefined (reading 'join')
[probe] scheduleReconnect armed
…（8 秒内重连又排了 6 轮，定时器永远在，进程不退出）
```

修好之后再跑同一个文件：`node --test app/tests/i18n_ui.test.cjs` → `53 pass / 0 fail`，5s 退出（§3）。

**修法**：把隐藏态的表达从「摘属性」换成「写空串」，只依赖 `setAttribute` 这一种所有替身都实现的方法：

```js
  pill.hidden = !badge;
  pill.textContent = badge ? badge.text : '';
  // 隐藏态写**空串**而不是摘除属性：空串在真实 DOM 里就是「没有这个名字 / 没有提示」……
  pill.setAttribute('title', badge ? badge.label : '');
  pill.setAttribute('aria-label', badge ? badge.label : '');
```

三条纪律都守住了：

- **不许**去给 5 个用例文件的替身补 `removeAttribute`（把缺口扩散成 6 份）——一处未改（`git diff` 里那 5 个
  文件零改动，`git status` 也不含它们）；
- **不许**把 `i18n_ui.test.cjs` 排除出门禁——它现在是 glob 的一部分，5s 跑完 53 条；
- 本票用例的替身**只保留真正需要的方法**：`channel_config_ui.test.cjs` 里第 1 次 attempt 补的
  `FakeNode.removeAttribute` 已删除（`app.js` 不再用），「不残留」用例改成断言「读不到名字」（空串或缺失都算没有），
  不把实现写法钉成契约：

```js
  assert.ok(!pill.getAttribute("title"), "隐藏时不携带上一频道的 title");
  assert.ok(!pill.getAttribute("aria-label"), "隐藏时不携带上一频道的 aria-label");
```

### 11.3 修后读数与其一致性

| 项 | 读数 |
| --- | --- |
| 13 个用例文件逐个跑（各自 90s 上限） | 全部 exit 0，合计 **188 pass / 0 fail**（§3 表） |
| 全量 glob（240s 上限） | **tests 188 / pass 188 / fail 0**，`duration_ms 6071.59`，7s 结束 |
| `scripts/check.sh --fast`（600s 上限） | exit 0，**门禁全绿**，13s |
| 基线 `c606b4a` 干净归档（自测） | **180 / 180 pass**，3.8s；`180 + 8`（本票新增 8 条 pill 用例）= `188`，数目自洽 |
| 浏览器证据 | 9 张图按新字节全部重拍（§4），`app.js` 服务端 md5 = 工作区 md5 = `794a39c4…` |

### 11.4 这次踩到的门禁纪律（留给下一位）

门禁命令必须自带外限时。第 1 次 attempt 用了一条**不带时限**的 `node --test app/tests/*.test.cjs && scripts/check.sh --fast`，
而挂住的 `i18n_ui` 又恰好只会在「测试全绿之后」卡住进程，于是命令看起来在跑、实际 56 分钟没有任何输出。
本轮所有门禁都走 `/tmp/runfile.sh`（每条命令 90/240/600s 上限，超时 kill 并报 124），任何一处挂住都能在一分钟内暴露。
