# 验收取证：#288 skills 列表行加启停开关，启停入口从右栏迁到行内

**票型**：实施票（纯前端 + 回归测试）｜**门禁**：G-impl（合并由 coordinator 执行）
**Ticket**：[#288](https://github.com/whutlichao/alibot/issues/288)
**父规格**：[#279](https://github.com/whutlichao/alibot/issues/279)（Agent 详情三分页签）
**前序**：[#283](https://github.com/whutlichao/alibot/issues/283)（skills 右栏详情）——本票修订它的 AC3
**改动面**：`app/alibot/studio/{app.js,app.css,i18n.js}`、`app/tests/agent_capabilities_ui.test.cjs`、
`CONTEXT.md`（skills 页签那段）、`app/specs/issue-283-acceptance.md`（AC3 修订行）、本文件。
**没动的面**：后端全部（`api.py` / `skills.py` / `db.py` / 迁移链 / `SCHEMA_VERSION`）、mcp 页签、`index.html`、频道配置面。

**正文口径（一句话判据）**：技能的启停**只有一个写入口**——左栏行内的 `role=switch`。
右栏保留一格 `skill-detail-state` 读数，它**陈述**装配状态，不是第二个入口。

---

## 1. 票面裁定 → 落点 → 读数

设计反馈评审逐条落定（Q1–Q20），下表按「读数」列取证。

### AC1 · 列表行不再展示技能描述，hash 前缀保留

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 行内没有描述 | `app.js` `skillRow()` 不再 append `resource-meta` 描述节点 | 用例「skills 页签：左栏技能列表与技能行入口」：`assert.doesNotMatch(skillRow.textContent, /在提交前做一次结构化自审/)` |
| 描述仍有唯一去处 | 右栏 `skill-detail-row-description`（`skillDetailRow('description', …)`，本票未动） | 同文件「默认选中第一行，五项元数据…」：`detailCell(panel,'description').textContent === '在提交前做一次结构化自审'` |
| hash 前缀仍在（faint、12 位） | `skillRow()` 的 `resource-meta is-faint` + `resource-hash` | 同用例：`assert.match(skillRow.textContent, /0123456789ab/)`；完整值仍在右栏 `skill-detail-hash` |

### AC2 · 启停的唯一写入口是行内开关；右栏不再有启停按钮

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 行内有一个开关，声明成 `switch` | `skillRow()`：`el('button','switch')` + `role="switch"` + `aria-checked` | 用例「…左栏技能列表与技能行入口」：`rowToggle.getAttribute('role') === 'switch'`、`aria-checked === 'true'`；用例「启停只打那一行…」：初值 `aria-checked === 'true'` |
| 开关在**行内**，不在右栏 | `skillRow()` 把它 append 到行容器 | 同用例：`skillRow.contains(rowToggle) === true` |
| 右栏**没有**第二个启停入口 | `skillDetailNode()` 只剩 `skillDelete` | 同用例：`byDataset(detail, 'skillToggle', 'sk_1') === null`（反向断言） |
| 开关不承载可见文案 | 无 `textContent`，可访问名走 `aria-label` | 用例「启停只打那一行…」：`toggle.textContent === ''`；`assert.ok(rowToggle.getAttribute('aria-label'))` |
| 可访问名带技能名、两份词条同键 | `skill.toggleLabel`（zh-CN `启用或停用「{name}」` / en `Enable or disable {name}`） | 用例「新增文案在两份词条里同键同参数位…」全绿；`grep -rn "'skill.enable'\|'skill.disable'" app/alibot/` → **0 命中**（两键随按钮删掉） |

### AC3 · 行结构：文字区是原生 `<button>`，开关是它的兄弟

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 文字区是原生 button | `skillRow()` 的 `el('button','skill-pick')` | 用例「skills 行：文字区是原生 button…」：`pick.tagName === 'button'` |
| 行容器不再合成 `role=button` | 撤掉 `row.setAttribute('role','button')` 与 `tabindex` | 同用例：`row.getAttribute('role') === null`；`sed -n '/^function skillRow/,/^}/p' app/alibot/studio/app.js \| grep -c "role', 'button"` → **0** |
| 开关不嵌在 button 里 | `row.appendChild(pick)` 与 `row.appendChild(toggle)` 是两次兄弟 append | 同用例：`toggle.parentNode === row`、`pick.contains(toggle) === false` |
| 点文字区换选中项 | 委派 `[data-skill-pick]` → `selectAgentSkill()` | 同用例：点 `sk_2` 的文字区后 `agentDetailSkillId === 'sk_2'`、`rowIsCurrent('sk_2') === true`、`rowIsCurrent('sk_1') === false` |
| 不再需要 keydown 兼容层 | 删掉 `#adBody` 上那段 keydown 委派（原生 button 已由浏览器翻成 click） | 用例全部通过；`grep -n "data-skill'\]" app/alibot/studio/app.js` → 0 命中 |

### AC4 · 开关的写路径：不乐观、只打那一行、不换选中项、请求期禁用

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 只打点中的那一行 | 委派 `[data-skill-toggle]` → `toggleSkill(id)` | 用例「启停只打那一行…」：`PATCH` 的 url 序列 `deepEqual(['/api/skills/sk_2'])`、body `deepEqual({enabled:false})` |
| **不乐观**：请求期间保持旧值 | `skillRow()` 的 `disabled = view.skillTogglingId === skill.id`；`toggleSkill()` 在请求前写 `skillTogglingId` 并重铺 | 用例「请求在飞时禁用（不乐观）」：请求挂起时 `inFlight.disabled === true` 且 `aria-checked === 'true'`（旧值），`release()` 后才 `'false'` |
| 一次只飞一个请求 | `toggleSkill()` 开头的 `if (view.skillTogglingId) return;` | 同上用例（连点不会产生第二条 PATCH） |
| 点开关**不**换选中项 | 委派里 `[data-skill-toggle]` 分支不调 `selectAgentSkill` | 用例「启停只打那一行…」：点 `sk_2` 的开关后 `agentDetailSkillId === 'sk_2'`；再点 `sk_1` 的开关仍 `'sk_2'` |
| 成功后开关与右栏读数一起变 | `loadAgentSkills()` 重读 → 重铺 → 两处同源 | 同用例：`aria-checked` 翻 `'false'`、`skill-detail-state === skill.disabled`、未点的 `sk_1` 仍 `'true'` |
| 离线禁用 | `disabled = !state.online \|\| …` | 用例「请求在飞时禁用…」末段：`state.online = false` 后 `disabled === true`（与 `resourceButton()` 同一口径） |
| 失败落在左栏既有错误位 | `view.skillError = t('skill.toggleFailed', …)` + `paintAgentCapabilities()` | 沿既有路径（`skill.toggleFailed` 词条未改）；不新开 toast 通道 |

### AC5 · 键盘与焦点：重铺换掉控件后焦点不掉

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 切选中项后焦点落到新选中行的文字区 | `selectAgentSkill()` 末尾 `focusSkillControl('pick', skillId)` | 用例「切选中项后焦点落到新选中行的文字区」：重铺后 `pick.focused === true` |
| 启停成功后焦点还给同一技能的新开关 | `toggleSkill()` 在**最后一次重铺之后** `focusSkillControl('switch', skill.id)` | 用例「请求在飞时禁用…」末段：`release()` + `settle()` 后 `skillToggle.sk_1.focused === true` |
| 为什么不是「下一次 paint 自动排空」的标记 | `toggleSkill()` 内部经历两次重铺（请求中一次、重读后一次），标记会在前一次就被消耗 | 见 `focusSkillControl()` 的注释（这条是本票自查时改掉的初版写法） |

### AC6 · 停用行的第二信号

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 停用行带 `is-disabled` | `skillRow()` 的 class 拼接 | 用例「启停只打那一行…」：`byDataset(panel,'skill','sk_2').classList.contains('is-disabled') === true` |
| 名称走 `--muted`（不是 `--faint`） | `app.css` `.skill-row.is-disabled .skill-pick b { color: var(--muted); }` | `grep -n "is-disabled" app/alibot/studio/app.css` 命中该规则；不删除线、不染红 |
| 停用行仍可选中、右栏照常显示详情 | 未对 `is-disabled` 做任何禁用选中项的判断 | `skillRow()` 里 `pick` 的 `disabled` 从未被设置 |

### AC7 · 视觉：`.switch` 与设计系统同形，热区 48px

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| `.switch` 与 `ui_kits/app/app.css` 同形同值 | `app.css`：34×20、`--r-pill`、`--panel-2` → `--accent-graphic`、`::after` 16px 滑块 `translateX(14px)` | 逐字对照设计系统原型；**零新增 token**（`--r-pill` / `--dur` / `--ease` / `--accent-graphic` / `--panel-2` 均既有） |
| 选中态改用 `aria-checked` 而非 `:checked` | `.switch[aria-checked='true']` | 开关是 `button[role=switch]`，没有 `:checked` 伪类可用 |
| 热区 48px | `.skill-pick { min-height: 48px; }` + 行 `padding: 10px var(--sp-3)` | **真浏览器实测**（`getBoundingClientRect`）：文字区 `385×48`、行 `433×68`；开关与文字区 `horizontallyDisjoint: true`、`gapPx: 12`。注：实测初版只有 38px（仅靠行 padding 撑），本票据此改成文字区自持 `min-height: 48px` |
| 两个控件物理不重叠 | 行是 `flex`，`pick { flex: 1 }` + `switch { flex: 0 0 auto }` | 无绝对定位、无 `z-index`、无 `pointer-events` 手工划分 |
| 动效尊重减弱偏好 | `@media (prefers-reduced-motion: reduce) { .switch, .switch::after { transition: none; } }` | 与本文件既有四处 reduced-motion 规则同一口径 |

---

## 2. 与 #283 验收记录的修订关系

| 位置 | 原文 | 本票处置 |
| --- | --- | --- |
| `issue-283-acceptance.md` AC3 | 「右栏的启用 / 停用与删除作用于当前选中项」 | **不改写历史**：在 AC3 标题下加一条「已由 #288 修订」的说明，指向本文件；表内读数按 283 当时的口径保留 |
| `skill.enable` / `skill.disable` | 两份 catalog 各一对 | 随右栏按钮的消失**一并删除**（与 #283 删 `skillsDetailPlaceholder` 同一规则）；`grep` 零命中断言见 AC2 |
| `skill.enabled` / `skill.disabled` | 列表徽标的消费点 | **保留**：右栏 `skill-detail-state` 仍是它的消费点（Q17 的裁定：删入口、留陈述） |

---

## 3. 门禁读数

```sh
# Node 用例族（含本票新增四条）
node --test app/tests/agent_capabilities_ui.test.cjs
# → tests 32 / pass 32 / fail 0

# 窄档：--fast + 前端相关 pytest + 静态守卫（diff 只落在 studio/** + *.test.cjs）
scripts/check.sh --studio
# → 349 Node 全绿 + pytest 全绿 → 「门禁全绿」
```

**环境备注**：本 worktree 起初没有 `.venv`，`check.sh` 会退到系统 `python3`（无 pytest）。
按脚本头的提示接上符号链接（`ln -sfn <主仓>/.venv .venv`）后即全绿；这条是 worktree 层级问题，不是代码问题。

---

## 4. 自查中改掉的两处初版写法（留痕）

1. **焦点恢复先是「标记 + paint 自动排空」**：`toggleSkill()` 内部有两次重铺，标记会在请求中那次就被消耗掉，终态节点拿到的是「没人还焦点」。改成显式的 `focusSkillControl(kind, id)`，在**最后一次重铺之后**调用。
2. **`.skill-row` 重写时漏了 `border-left: 2px solid transparent`**：`is-active` 的竖条靠它占位，漏掉会让选中的那一行整行向右错 2px。已并回同一行声明。

## 4.1 真浏览器验证中抓到的两处问题（替身测不到，已修）

1. **`finally` 里的 `skillTogglingId = null` 太晚**：初版把清除放在 `try/finally` 的 `finally` 里，而 `focusSkillControl()` 在 `try` 内部就已调用——于是最终那次重铺画出的开关**仍是禁用的**。真浏览器里对 `disabled` 控件调 `focus()` 会被默默忽略，「还焦点」是个静默失败的假动作（用例替身的 `focus()` 不理会 `disabled`，所以 32 条用例全绿也看不见它）。改成请求一结束就先清标记再重读。**实测**：键盘 Enter 翻转后 `document.activeElement` 是 `role=switch` 且 `data-skill-toggle` 仍是同一个技能 id，`disabled: false`。
2. **热区实际只有 38px，不是设计时说的 48px**：只靠行 `padding: 10px` 撑不出 48px（两行文字约 28px）。改成文字区自持 `min-height: 48px`；实测 `385×48`。

## 4.2 真浏览器读数（本 worktree 实例，:8792，隔离数据目录）

```text
rowCount 2 · rowText "alphahash cc991719c073"（描述不在行内）· rightDesc = 完整描述
rowRole null · pickTag BUTTON · switchRole switch · switchLabel "启用或停用「alpha」"
rowChildren ["BUTTON.skill-pick", "BUTTON.switch"] · pickContainsSwitch false
rowBorderLeft "2px oklch(0.7 0.081 300.5)"（选中竖条在位）· switch 34px x 20px
rightPaneToggleButton false（右栏无第二启停入口）

点另一行文字区 → ariaCurrent [null, "true"] · rowIsActive [false, true]
             → focusOnPick "sk_ff5042c74bc0" · 右栏 name "beta"
点未选中行的开关 → checked ["false","true"] · selectedStill "sk_ff5042c74bc0"（不换选中项）
             → focusSkillId 等于被点的那一行 · focusDisabled false · isDisabledRow [true,false]
键盘 Tab 顺序 → pick(sk_2) → switch(sk_2)（一行两个控件各占一个 Tab 位，无 roving）
键盘 Enter 在开关上 → checked 翻转 · 焦点仍在同一 switch 上且未禁用
键盘焦点环 → outline solid 2px oklch(0.7 0.086 356.7) offset 2px（--accent-graphic）
```

**环境**：临时实例跑的是本 worktree 的代码（`PYTHONPATH=<worktree>/app`，否则 `PACKAGE_DIR` 会指向主仓、读到主仓的 `studio/`——这个坑值得记一笔）；数据目录 `/tmp/alibot-288`，1 个 Agent + 2 个技能，跑完即删。

---

## 5. 未纳入本票的面（明确留给下一张票）

- **mcp 页签**的启停仍是「badge 已启用/已停用 + 停用/启用按钮」（`mcpRow()`）。Q2 的裁定是另开票：`.switch` 这次已落进 `app.css`，那张票是纯收益。
- **`.resource-row` 基线仍是 `align-items: flex-start`**：本票在 `.skill-row` 内用 `align-self: center` 处理了开关的纵向对齐，没有动全局基线（动它等于顺手改了 mcp 行与其它资源行）。
