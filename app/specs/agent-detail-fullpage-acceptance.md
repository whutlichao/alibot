# 验收取证：Agent 详情改为主线视图整页（不再占 dock 第三栏）

**票型**：实施票（前端重构 + 回归测试）｜**门禁**：G-impl（合并由 coordinator 执行）
**Ticket**：本票**没有**对应的 GitHub issue，也**不新建**——用户裁定交付件用描述性命名
（`app/specs/agent-detail-fullpage-*`），沿用 `fix-dock-fallback-no-channel-*` /
`fix-sse-target-fail-fast-*` 的先例。
**base**：`e2d4242`（= 派活时 main / origin/main）｜**branch**：`agent-detail-fullpage`
**采集时间**：2026-10-22｜**环境**：macOS（aarch64），Node（`node --test`，无需 npm 依赖），
Python 3.11.13（仓库根 `.venv` 的解释器）
**改动面**：`app/alibot/studio/{app.js,app.css,i18n.js,index.html}`、`app/README.md`、
`app/tests/{agent_detail_ui,round_outcome_inspect_ui}.test.cjs`。
**后端零改动**：`app/alibot/*.py` 一行未动，schema、迁移链与 HTTP 契约不变。

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**实际跑出来的结果**。红证据在**隔离的基线副本**
上取得（`scripts/red-evidence.sh` 用 `git archive` 导出基线，只换测试不换产品代码），
**不动**本 worktree 的工作区。

---

## 1. 规格 → 落点 → 读数

用户原话：「agent 的详情不要再在第三栏里显示了，而是直接像 channel 一样占据整个右侧页面，
所有的详情分区块展示，可编辑内容直接编辑。」

### Change 1 · 占据范围 = 整条右侧，dock 让位

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 详情是一条**主线视图**，不是 dock 的形态 | `app.js` `setViewMode()`：`$('#viewAgent').hidden = mode !== 'agent'`，`mode === 'agent'` 时 `setDockOpen(false)` | `state.mode === 'agent'`、`#viewAgent.hidden === false`、`#dock.hidden === true`（用例 1/2/3） |
| 主线视图集合收敛为四种 | `setViewMode()` 的注释与实现（消息流 / 看板 / 搜索 / Agent 详情） | 同上 |
| `state.dockMode` 只剩三种形态 | `setDockMode()` 只认 `isTask` / `isRun`；`#dockAgent.hidden = Boolean(isTask \|\| isRun)` | `grep "dockDetail"` 在 Studio 三件套里 **0 命中** |
| `activeAgentId` 仍是「打开的是哪个成员」的唯一正本 | `openAgentDetail()` 写它；`renderAgentDetail()` / `closeAgentDetail()` 读它 | 用例 1 断言 `activeAgentId === 'mem-1'` |
| 运行面板开着时先收掉 | `openAgentDetail()` 里 `if (state.runPanel.open) closeRunPanel()` | 用例 3：`runPanel.open === false` 且 `mode === 'agent'` |
| 记住进入前的 `state.mode` 并回到它 | `state.agentReturn = { mode, dockOpen }`（只在 `state.mode !== 'agent'` 时记录）；`closeAgentDetail()` 读它 | 用例：从 `board` 进入 → 关闭后 `mode === 'board'`（不跳回频道）；从 `channel` 进入 → 回 `channel` |
| dock 按进入前的开合恢复 | `closeAgentDetail()` 末尾 `setDockOpen(Boolean(back.dockOpen))` | 用例：进入前开着 → 关闭后 `#dock.hidden === false`；进入前收起 → 关闭后仍 `true` |
| 频道头（标题 + 消息 / 任务页签）与 composer 一起让位 | `syncChromeVisibility()`：`mode === 'agent'` 时 `#chanHead.hidden = true`；composer 由 `mode !== 'channel'` 已经隐藏 | 用例 1 断言两者都 `hidden === true`；新增用例「从频道进入再关闭」断言两者都回来 |
| 左栏 rail 不动 | 未改 `.rail` 的任何规则（`app.css` diff 只在 `.agent-page*` / `.agent-save-bar`） | `git diff e2d4242 -- app/alibot/studio/app.css` 可见 |

### Change 2 · 所有详情都是区块（没有页签）

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 六个区块**一页纵向**、按规格顺序 | `agentDetailNodes()` 返回 `[facts, status, config, runtime, globalMcp, prompt]` | 用例：`#adBody` 的直接子节点 `data-od-id` 序列逐项相等 |
| 既有 `data-od-id` 保留 | `agent-detail-section-facts` / `-status` / `-runtime` / `-global-mcp` / `-prompt` | 同上（序列断言里逐个出现） |
| 新「配置」区块有稳定 id | `sectionTitle('agentDetail.sectionConfig', 'agent-detail-section-config')` | 同上 |
| 页签节点与状态全删 | `index.html` 无 `#adTabOverview` / `#adTabRuntime` / `data-adtab` / `.dock-tabs`；`app.js` 无 `setAgentDetailTab` / `state.agentDetailTab` | 用例：正本正则逐条 `false`；`grep` 三件套 0 命中 |
| 区块标题走词条 | `sectionTitle(key, odId)` + `i18n.js` | 裸中文扫描（`i18n_ui.test.cjs` 三件套）绿 |

### Change 3 · 就地编辑 + 底部保存条

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 控件只写草稿，不直接 PATCH | `setAgentDraft()` 只改 `state.agentEdit.draft` 并重绘保存条 | 用例：改三个字段后 `patches().length === 0`，保存条出现 |
| 保存条固定底部、文案「有未保存的改动 [放弃] [保存]」 | `index.html` `#adSaveBar`（`hidden` 初值）+ `agent-save-bar` 样式 | 静态正则：`#adSave` / `#adDiscard` 存在，`#adSaveBar` 带 `hidden` |
| 保存 = 整份字段一起提交 | `submitAgentEdit()` 构造 11 个字段的完整 payload | 用例 `assert.deepEqual(patch[0].body, {...11 个字段...})` |
| 模型提供商 + 模型名拼成 `provider:model` | `agentDraftFrom()` 按第一个 `:` 拆；`submitAgentEdit()` 拼回 | 同上：`model_id: "ollama:qwen2.5"` |
| 联网档 `follow` ⇒ 提交 `web_access: null` | `submitAgentEdit()` | 同上：`web_access: null` |
| 后端拒绝 ⇒ 保存条与输入都还在 | `catch` 里 `state.agentEdit.error = err.message` + `renderAgentSaveBar()`，不重铺 `#adBody` | 用例：错误文案进 `#adSaveError`、`#adSaveBar.hidden === false`、输入框的值仍是人刚打的 |
| 保存成功 ⇒ `applyAgentUpdate()` 重铺、保存条消失、toast | `submitAgentEdit()` 成功分支 | 用例：`#adSaveBar.hidden === true`、左栏名字跟着变 |
| 放弃 ⇒ 丢弃草稿、按后端读数重铺 | `discardAgentEdit()` | 用例：输入回到后端值、保存条消失 |
| 关闭 / 切换成员 / 切换主线视图 ⇒ 静默丢弃草稿 | `closeAgentDetail()` / `openAgentDetail()` / `leaveAgentView()` | 用例：切换成员后保存条消失且输入是新成员的后端值；切视图后保存条消失（无确认框） |
| 暂停 / 恢复与启用状态同源 | `paintAgentDraftControls()` 只改草稿 `enabled`；按钮文案随草稿变 | 用例：点「暂停」→ 草稿 `enabled=false`、档位卡片跟着切、仍**不发** PATCH |
| 提供商候选来自 `state.providers` | `fillAgentProviderSelect()` 复用 `fillProviderSelect()`，草稿里的 provider 缺失时补一项 | 用例：候选集 = `state.providers`，当前值选中 |
| 远端告知块搬进「配置」且不丢「启用远端模型前需要你确认一次」 | `agentRemoteNoticeNode()` 复用 `renderRemoteNoticeBlock()`（判定与文案只有一份实现） | 用例：远端 provider 时 `data-od-id="agent-edit-remote-notice"` 可见且标题是 `agent.remoteTitle` 的插值结果；本机 provider 时隐藏 |

### Change 4 · 删除「编辑配置」弹窗

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| `#agentEditOverlay` 节点删除 | `index.html`（原 846–975 行整块） | 静态正则 `false` |
| 弹窗专属函数 / 绑定 / 状态删除 | `openAgentEditModal` / `closeAgentEditModal` / `state.agentEditId` / `#agentEditSubmit` 等 | `grep` 全仓（`app/**/*.js,cjs,html,css,md`）只在**历史验收文档**里命中，Studio 代码 0 命中 |
| 不留 `$('#agentEdit...')` 调用 | 同上 | 用例：运行时 `queries` 里没有以 `#agentEdit` 开头的选择器 |
| 新建 Agent 弹窗 `#agentModal` 不动 | `index.html` / `app.js` 新建路径 | `agent_modal_ui.test.cjs` 全绿（含在 267 条里） |
| ESC 分支不再引用弹窗 | `bindEvents` 的 keydown | 用例：ESC 在详情页不炸、不误关别的东西 |

### Change 5 · 文案

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 新增 / 重写文案进两份词条 | `i18n.js` 的 `agentDetail.*` | 用例：本票用到的键在 `zh-CN` 与 `en` 都能取到值 |
| 页签键删除 | `agentDetail.tabOverview` / `.tabRuntime` / `.tabsLabel` | `grep` 0 命中 |
| 无裸中文 | `index.html` / `app.js` / `app.css` | `i18n_ui.test.cjs` 三件套绿 |

### Change 6 · 文档同步

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| `app/README.md:622` 重写 | 「Agent 详情与配置编辑」小节开头 | 现文描述 `state.mode === 'agent'`、占整条右侧、dock 让位、六区块无页签、草稿 + 底部保存条 |
| dock 形态注释改成三种 | `index.html:187-188`、`:344-347`、`:443`；`app.js:229`、`:2383`；`i18n.js` 两份注释；`app.css:995/1065` 的 B/C 编号沿用 | `grep "第四形态\|四种形态\|形态 D\|第三形态\|dock 形态 C.*Agent"` 在 Studio 三件套与 README **0 命中**（评审曾指出验收文档只扫了前三个词、漏掉「第三形态」；已补扫并修正 `app.js` 那处注释） |
| `CONTEXT.md` 不新增术语、不改三个条目语义 | 未改动 `CONTEXT.md` | `git diff e2d4242 -- CONTEXT.md` 为空；**成员面板** / **成员名册** / **本机 Agent** 三条一字未动 |

---

## 2. 门禁与测试读数

```sh
# 窄门禁（ruff check / ruff format --check / mypy / pyright / node --test 全量 / 三条 Python 守卫）
bash scripts/check.sh --studio
# → 269 tests / 269 pass / 0 fail；门禁全绿

# 两个目标文件
node --test app/tests/agent_detail_ui.test.cjs      # 31 tests / 31 pass / 0 fail
node --test app/tests/round_outcome_inspect_ui.test.cjs  # 4 tests / 4 pass / 0 fail

# 全量前端用例
node --test app/tests/*.test.cjs                    # 269 tests / 269 pass / 0 fail
```

## 3. 红 → 绿证据

```sh
bash scripts/red-evidence.sh e2d4242 app/tests/agent_detail_ui.test.cjs
```

- 基线 `app.js` md5：`7e53cdd612d65f5406440f4c3385bdfb`
- 证据目录：`/var/folders/bf/27rt5fsx01q0bq602vmhprsw0000gn/T/alibot-red.WHE0t8`
- 读数：**31 tests / 1 pass / 30 fail** —— 唯一通过的是与形态无关的守卫；
  其余 30 条全部红在「详情还是 dock 形态」「没有配置区块」「没有保存条」「词条缺失」等
  本票要改的点上。
- 同一份测试在工作区（改动后）跑：**31 / 31 通过**。

红证据只换测试不换产品代码，因此它证明的是「这些断言确实咬住了旧实现」，
而不是「新实现被测试倒推着写」。

## 4. 双轴评审

Standards 轴与 Spec 轴各跑一个独立子代理（互不共享上下文）。两轴结论与处置：

### Standards 轴

| 发现 | 处置 |
| --- | --- |
| 14 条死词条残留（消费者随本票删除，机械守卫不查孤儿） | **已修**：删掉 `agentDetail.kvRole` / `kvEnabled` / `enabledOn` / `enabledOff` / `kvAutoClaim` / `autoClaimOn` / `autoClaimOff` / `kvWebAccess` / `webAccessFollow` / `webAccessOff` / `webAccessSearch` / `webAccessSearchFetch` / `saveFailed` / `pauseFailed` / `resumeFailed` 与 `toast.agentPaused` / `toast.agentResumed`，两份词条同步；`agentDetail.*` 现在 0 条无消费者 |
| 注释说谎：`agent.js` 引已删函数 `agentEditWebAccessValue()` | **已修**：改指向 `submitAgentEdit()` 的实际行为 |
| 注释说谎：`i18n.js` 两处提「运行时页签」 | **已修**：改成「详情页」 |
| README 与代码不符：运行区块字段清单还含当前状态、小节名叫「概览」 | **已修**：拆成「事实」/「当前状态」/「运行时」三处说法，与实现一一对应 |
| `paintTierGroup` / `paintAccessGroup` 逐字重复 | **已修**：合并成 `paintCardGroup(group, attribute, value)` |
| `resetAgentEditDraft(agent)` 的形参永不可达 | **已修**：去掉形参，调用点从 3 处 `(null)` 收敛 |
| `renderRemoteNoticeBlock` 接受字符串或节点两种参数 | **保留**：新建弹窗传选择器、详情页传节点，两处共用同一份判定与文案；拆成两个函数会让「远端告知」出现第二份实现 |
| `agentTierGroup` 是第四份同类 `.radio-card` 表 | **保留**：其余三份分别长在频道设置 / 新建弹窗 / 预算面，各有不同的取值键与文案来源；本票只消费既有控件族，不做跨面抽象 |

### Spec 轴

| 发现 | 处置 |
| --- | --- |
| `app.js` 一处注释仍称 Agent 详情为「dock 第三形态」 | **已修**：改成「整页详情（主线视图 `state.mode === 'agent'`）」 |
| 6 个配置控件失去 `<label for>` 关联（`agentInput()` 从不设 `id`） | **已修**：`agentInput()` 统一给 `id`；档位组改 `aria-labelledby`；系统提示 `textarea` 补 `aria-label`；新增一条用例把三类关联都钉住 |
| 草稿脏时 `renderAgentDetail()` 跳过重铺，全局 MCP 确认后仍显示「未确认」 | **已修**：新增 `refreshGlobalMcpSection()`，草稿脏时只换那一节只读内容；新增用例断言「按钮消失 + 输入节点还是同一个 + 输入值还在」 |
| 关闭详情只恢复 dock 开合、不恢复形态 | **复核后不改**：打开详情会主动关掉任务线程与运行面板（「dock 一次只显示一种形态」），因此进入前的形态只可能是「频道成员」，`dockFallbackMode()` 已经给出同一个答案；加一份 `dockMode` 记录是死分支（已回退） |
| `openTask()` 清 `activeAgentId` 但不清草稿与 `#adBody` | **已修**：改走 `leaveAgentView()`，与其它导航出口同一条 |
| 孤儿词条（与 Standards 轴同一批） | 同 Standards 轴处置 |
| ADR-0026 正文「人类面入口是 Agent 编辑弹窗的联网档」已随弹窗删除而过时 | **不改 ADR**：`docs/agents/review-rules.md` 判断规则 4「ADR 只做最小事实修正、决策内容不改」。决策（联网档是显式 opt-in、`null` = 清除覆盖）仍然成立，变的只是呈现位置；此处记事实修正：入口现为 Agent 详情页「配置」区块里的「联网」四档（`app/README.md` 已同步改口径） |
| 未在真实浏览器跑 ≤900px | **已知限制**：移动端只在 CSS 与逻辑层核对（`.agent-page` 是 `.main` 的 flex 子项、`#dock` 在 agent 视图下 `hidden`，≤900px 的抽屉规则未改）。本票不新开浏览器测试框架 |
