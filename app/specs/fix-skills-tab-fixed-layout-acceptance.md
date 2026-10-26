# 验收记录：Agent 详情 skills 页签改成固定高度工作区

**票型**：实施票｜**门禁**：G-impl + G-merge｜**测试档**：窄（`scripts/check.sh --studio`；CI 跑全量复核）
**Ticket**：本票**没有** GitHub issue，不新建；交付件用描述性命名（沿
`app/specs/fix-dock-fallback-no-channel-acceptance.md` 先例）。
**base**：`8c038ee`（= 派活时 `main`）｜**branch**：`fix-skills-tab-fixed-layout`
**PR body**：`app/specs/fix-skills-tab-fixed-layout-pr-body.md`
**采集时间**：2026-10-26｜**环境**：macOS（aarch64）、Python 3.11.13（仓库根 `.venv`）、ego-browser 0.6.0.20

---

## Status

| 判据（票面 Observable acceptance） | 落点 | 读数 | 结论 |
| --- | --- | --- | --- |
| 旧代码可复现的**浏览器红证据**：页面被长列表撑高、右侧详情离屏 | 基线 `8c038ee` 的 `app/` **+ 同 commit 的 `alibot-design-system`**（`/tmp/alibot-sklayout`，端口 8797）+ 同一支探针 | 1280x620：`.agent-page-scroll` = `525 / 1388`（撑高 863px），`.agent-skill-right` `bottom 1455` > 620，`rightFullyInViewport=false`；1440x900 同理（`805 / 1388`、`bottom 1455` > 900） | ✅ |
| 修改后左右**独立滚动** | `.agent-page.is-skills-fixed .agent-skill-left,.agent-skill-right { min-height:0; overflow-y:auto }` | 1280x620：左 `215 / 1074`、右 `215 / 2114`；1440x900：`495 / 1074`、`495 / 2114`，两者 `overflow-y: auto` | ✅ |
| 右栏**始终在视口内**（1280x620 + 常规桌面） | 同上 + `.agent-skill-columns { flex: 1 1 auto }` | 1280x620 `rightFullyInViewport=true`（`bottom 596`）；1440x900 `true`（`bottom 876`） | ✅ |
| 页面本身**不被长列表撑高** | `.agent-page.is-skills-fixed` 下子层各自滚，外层不再产生溢出 | 两档 `scrollHeight == clientHeight`（`525/525`、`805/805`） | ✅ |
| 切到**基础配置与 MCP** 仍能正常滚动 | 状态类只由 `setAgentDetailTab` 在 skills 档落/撤 | 1280x620：base 与 mcp 整页可滚，`#viewAgent.className` 回到 `agent-page` | ✅ |
| **≤900px 单栏**可访问 | 固定布局整块写在 `@media (min-width: 901px)` | 900x620：`grid-template-columns` 单列 `845px`，页面滚动，两栏 `overflow-y: visible` | ✅ |
| 长右侧正文可**独立滚到底** | 桌面档撤掉 `.skill-detail-markdown` 的 `max-height:60vh` / `overflow-y:auto` | 1280x620：`right.scrollTop == scrollHeight−clientHeight`，正文最后一行与容器底对齐，`window.scrollY 0` | ✅ |
| 右侧**避免 Markdown 嵌套滚动** | 同上 | `.skill-detail-markdown` 的 `maxHeight` 由 `372px`/`540px` 变 `none`，`overflowY` 由 `auto` 变 `visible` | ✅ |
| 自动回归覆盖**实际页签切换行为**与布局契约 | `app/tests/agent_capabilities_ui.test.cjs` 新增 3 条 | 本文件 30 passed；`scripts/check.sh --studio` 347 passed | ✅ |
| 红证据在**隔离基线**上取得 | `scripts/red-evidence.sh 8c038ee …` | 基线 app.js md5 `aaf09d6aa5437d49f7f85e2cd4d8501e`，`30 tests / 28 pass / 2 fail` | ✅ |
| `scripts/check.sh --studio` 通过 | 门禁唯一入口 | `ℹ tests 347 · pass 347 · fail 0`；ruff / format / mypy / pyright 全绿 | ✅ |

### 证据修正（重要）

基线副本最初只用 `git archive <rev> app` 导出，**漏了仓库根的 `alibot-design-system/`**，于是
`/design/tokens.css` 返回 404：基线页面拿不到设计 token（行高、字号、间距全是浏览器默认），
与修复侧不可比。曾据此写出的一组数字（`573 / 1563`、`bottom 1610`）**已作废，不再引用**。

修正做法：补导出同 commit 的 `alibot-design-system/`（两份 foundation 与工作区逐字节一致：
`tokens.css` md5 `e717cd3e772fd859919d286c7c6cac32`、`colors_and_type.css` md5
`5f1e4c4ccff0b39a4cca519acd4d18c4`），重启 8797，两侧 `/design/*.css` 均 200 后重跑同一支
`round-05.mjs`。上表全部数字来自这次重跑。

---

## 1. 实现

**改动面**

| 文件 | 改动 |
| --- | --- |
| `app/alibot/studio/app.js` | `setAgentDetailTab()` 落/撤 `.agent-page.is-skills-fixed`；`agentSkillsTabNodes()` 根节点加 `agent-skill-section` |
| `app/alibot/studio/app.css` | 新增 `@media (min-width: 901px)` 固定高度块：高度链 + 两栏 `overflow-y: auto` + 正文撤 `60vh` |
| `app/tests/agent_capabilities_ui.test.cjs` | 新增 3 条（页签状态契约 / 桌面档样式契约 / ≤900 反向） |
| `CONTEXT.md` | `Agent 详情页签` 词条补一句固定高度工作区的现行口径 |
| `app/README.md` | 视觉契约节补「Agent 详情 skills 页签的固定高度工作区」一段 |

**为什么不是 `position: sticky`**：票面要的是「顶部导入区固定 + 双栏各自滚动 + 页面不被撑高」。
整页不再溢出（`scrollHeight == clientHeight`）时导入区自然不动，比 sticky 少一层叠放与
`z-index` 交互面；矮视口下外层 `overflow-y: auto` 仍在，整页还能滚到导入区，不裁内容。

**为什么状态类是 `.agent-page` 而不是面板**：只有 `skills` 一档需要改外层的滚动行为；面板
（`[data-adpanel='skills']`）不是外层滚动容器（`.agent-page-scroll`）的祖先，因此状态类放在两者
共同的祖先 `.agent-page` 上。`≤900px` 不变的理由是它把「容器高度由内容决定」当契约，固定盒子会把右栏挤出视口。

---

## 2. 双轴 code-review 与逐条处置

按 `/Users/apple/.agents/skills/code-review/SKILL.md`，两个 `reviewer` 子代理并行跑（Standards / Spec 各一），
结论分开记。

### Standards 轴（无硬违规，全部判断项）

| Finding | 处置 | 理由 |
| --- | --- | --- |
| P2 冗余规则：`.agent-page.is-skills-fixed > .agent-page-scroll { overflow-y: auto }` 与基础规则同值，零效果，却被测试反向锚定 | **采纳** | 删掉该规则；断言改为「基础规则是 `auto`」+「固定高度不写零效果的重复声明」 |
| P2 Duplicated Code：`.agent-page-inner` / 面板 / `.agent-skill-section` 三条声明几乎相同 | **不采纳** | 抽 utility 类会把一条只服务本页的规则抬成共通语汇（review-rules 判断题 1）；三处各自的可读成本低于一次跨页抽象 |
| P2 选择器风格：`#adPanelSkills` 是全表唯一 id 选择器 | **采纳** | 改用既有的 `data-adpanel='skills'`，并加断言钉住「不靠 id 定位」 |
| P2 测试辅助重复：`declarationsOf` / `flattenCss` / `mediaMatches` 与 `mobile_shell.test.cjs` 同源，且 `mediaMatches` 已分叉 | **部分采纳** | 不抽公共模块（`approval_dom_harness.cjs` 那种共享的正当理由是假时钟 / 定时器等非它不可的能力）；但把分叉写进注释：本文件只认宽度，`mobile_shell` 多认一个动效档 |
| P2 文档同步：`CONTEXT.md` 未记固定高度 | **采纳** | `Agent 详情页签` 词条补三句；`app/README.md` 视觉契约节补一段 |
| 判断项 1（共享行为不为单票需求而变）| 已通过 | `.d-sec` 零新增规则；新窄选择器全部带状态类前缀 |

### Spec 轴

| Finding | 处置 | 理由 |
| --- | --- | --- |
| **P1** 布局契约缺「导入区不被压缩」的断言：删掉 `flex: none` 那行三条新用例仍全绿 | **采纳（真缺陷）** | 补断言：`.agent-skill-section > :not(.agent-skill-columns)` 的 `flex === 'none'`，并再钉一条「导入区块不自带滚动条」 |
| P2「顶部导入区固定」靠「整页不滚」，导入结果行累积后可能失效 | **不采纳（前提不成立）** | reviewer 假设「会话里连导多个结果会累积」。已核实 `submitSkillImport()`（`app.js:8282`）**每次先 `skillImportResults = []`**，成功 / 失败只 push 当次那一条，结果行不会跨导入累积；要造出 60 行只能靠改 DOM（非正常导入可达）。曾试过给导入区块加 `overflow-y: auto` 兜底，浏览器复核发现它把导入区自己的下拉框滚出视口，已回退。保留最小布局：导入区钉成不可压缩（`flex: none`），极矮视口 / 极长单条错误由外层整页滚降级，内容仍可访问 |
| P2「避免 Markdown 嵌套滚动」只在 `≥901px` 成立，`60vh` 仍留在基础规则 | **不采纳（须收窄的是描述，不是代码）** | 票面明写「沿用已有 ≤900px 单栏断点及页面滚动，不引入新的移动布局」；单栏下容器高度由内容决定，撤掉 `60vh` 会让长正文把整页拉成一条万像素长条。验收文档与 PR 均已把这条限定为「桌面档」 |
| P2 `.skill-detail > .kv { flex: none }` 是落在固定块之外的通用规则，base 与 ≤900px 也生效 | **采纳** | 移进 `@media (min-width: 901px)` 并加状态类前缀，只在固定高度档生效 |

---

## 3. 门禁与红证据

```sh
# 本文件
node --test app/tests/agent_capabilities_ui.test.cjs     # 30 passed

# Studio 窄档（票面门禁；CI 跑全量复核）
scripts/check.sh --studio                                # 347 node tests passed；ruff / mypy / pyright 全绿

# 红证据（隔离基线副本，产品代码保持基线字节，只覆盖测试）
scripts/red-evidence.sh 8c038ee app/tests/agent_capabilities_ui.test.cjs
```

红读数（基线 `8c038ee`，app.js md5 `aaf09d6aa5437d49f7f85e2cd4d8501e`）：

```text
ℹ tests 30   ℹ pass 28   ℹ fail 2
✖ 切页签：skills 置上固定高度状态类…  AssertionError: skills 页签置上固定高度状态  false !== true
✖ 桌面档样式契约…                     AssertionError: 1280px：外层滚动器…  undefined !== 'auto'
```

---

## 4. 浏览器证据（隔离后端，ego-browser）

两个端口、同一支探针 `/tmp/skl-evidence/round-05.mjs`：

- **8797** 基线：`/tmp/alibot-sklayout`（`git archive 8c038ee app alibot-design-system`），
  `HOME=/tmp/skl-base-home`、`ALIBOT_DATA_DIR=/tmp/skl-base-data`。
- **8798** 修改后：本 worktree 的 `app/`，`HOME=/tmp/skl-new-home`、`ALIBOT_DATA_DIR=/tmp/skl-new-data`。

种子脚本 `/tmp/skl-seed.py <port> <home>`：建 1 个 Agent、往隔离 `~/.agents/skills/` 写 14 个技能并全部导入，
其中 `code-review` 的 `SKILL.md` 正文 120 行（用于验证长正文独立滚到底）。

截图（`/tmp/skl-evidence/`）：`round05-baseline-1280x620.png`、`round05-baseline-1440x900.png`、
`round05-fixed-1280x620.png`、`round05-fixed-1440x900.png`、`final-skills-1280x620.png`、
`accept-skills-900x620.png`、`accept-tab-base-1280x620.png`、`accept-tab-mcp-1280x620.png`。

协调者另有一套独立的 70 行技能静态压力 fixture（`/tmp/alibot-skills-acceptance/`）验证过同一行为，
结论一致。

---

## 5. 遗留 / 说明

- 本地只跑 Studio 窄档；全量 pytest 由 CI 在本 head 上复核（票面口径）。
- `≤900px` 下单栏仍用 `.skill-detail-markdown` 自带的 `60vh` 滚动盒（既有行为，票面明确不引入新移动布局）。
- 隔离服务在本票证据归档后按协调者指示关闭（只关本票创建的 8797 / 8798 PID，不碰用户的 8788）。
