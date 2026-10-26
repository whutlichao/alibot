## Summary

skills 页签从「整页跟着列表长高」改成**固定高度的工作区**：顶部导入区留在原处，左右双栏占满余下高度、
各自内部滚动，长技能列表不再把页面撑高，右栏详情（含长 `SKILL.md`）始终在视口内。

```diff
 #viewAgent.agent-page                     ← 状态类 is-skills-fixed（由 setAgentDetailTab 落/撤）
 ├── .ad-tabs                              ← 切页签只改 aria-selected / is-active / hidden
 ├── .agent-page-scroll                    ← skills 档：外层不再产生溢出
 │   └── #adBody.agent-page-inner          ← + height:100% / column flex / min-height:0
 │       ├── #adPanelBase                  ← 基础信息和配置：整页滚动（不变）
 │       ├── [data-adpanel="skills"]       ← + column flex / flex:1 / min-height:0
 │       │   └── section.d-sec.agent-skill-section   ← 新增 agent-skill-section
 │       │       ├── 标题 / 作用域 / 导入区           ← flex:none（不参与收缩）
 │       │       └── .agent-skill-columns            ← + flex:1 1 auto / min-height 兜底
 │       │           ├── .agent-skill-left            ← + min-height:0 / overflow-y:auto
 │       │           └── .agent-skill-right           ← + min-height:0 / overflow-y:auto
 │       │               └── .skill-detail-markdown   ← 撤掉 60vh 自身的滚动盒
 │       └── #adPanelMcp                   ← mcp：整页滚动（不变）
 └── .agent-save-bar
```

三个取舍：

- **状态落在 `.agent-page` 的 `is-skills-fixed` 上，固定布局整块写在 `@media (min-width: 901px)`**：
  基础信息和配置 / mcp 两页仍是原来的整页滚动；`≤900px` 的单栏断点与页面滚动一个字没动（那里两栏
  上下堆叠、高度由内容决定，固定盒子反而会把右栏挤出视口）。
- **右栏只有一处滚动**：桌面档下 `.skill-detail-markdown` 的 `max-height: 60vh` / `overflow-y: auto` 被撤，
  长正文由 `.agent-skill-right` 这一根滚动条滚到底，不再出现 Markdown 嵌套滚动。
- **`.d-sec` 没有被改**：那是全局共用语汇，本页需要的高度链用新类 `agent-skill-section` 承接；面板按
  既有的 `data-adpanel` 选，没有引入 id 选择器。

## Evidence

**浏览器（ego-browser 0.6.0.20 + 真实 uvicorn，两个隔离后端）**

同一支探针 `/tmp/skl-evidence/round-05.mjs`，端口 **8797** = 基线 `8c038ee` 的 `app/` + 同 commit 的
`alibot-design-system`（`/tmp/alibot-sklayout`），**8798** = 本分支。种子 1 个 Agent + 14 个技能
（`code-review` 正文 120 行），种子脚本 `/tmp/skl-seed.py`。

> 样式条件已对齐：基线首次采样漏导出 `alibot-design-system`，`/design/tokens.css` 404、页面无 token，
> 与修复侧不可比（那组 `573/1563` 已作废）。下面数字全部来自补齐两份 foundation 之后的重跑，
> 两侧 `/design/*.css` 均 200。

| 读数（skills 页签） | 基线 `8c038ee` | 本分支 |
| --- | --- | --- |
| 1280x620 `.agent-page-scroll` | `clientHeight 525` / `scrollHeight 1388`（被撑高 863px） | `525` / `525`（页面不再被撑高） |
| 1280x620 两栏内部滚动 | 两栏 `1074` 高、`overflow-y: visible`，都不可滚 | `215 / 1074`、`215 / 2114`，都 `overflow-y: auto` |
| 1280x620 `.agent-skill-right` | `bottom 1455` > 视口 620，**离屏** | `bottom 596`，`rightFullyInViewport = true` |
| 1440x900 `.agent-skill-right` | `bottom 1455` > 视口 900，**离屏** | `bottom 876`，在视口内 |
| 右栏正文 `.skill-detail-markdown` | `max-height 372 / 540px` + 自带 `overflow-y: auto` | `max-height: none`、`overflow-y: visible` |
| 右栏滚到底（1280x620） | 追不到正文末尾（正文自己那根条才是短的） | `scrollTop == scrollHeight−clientHeight`，正文最后一行与容器底对齐，`window.scrollY 0` |
| base / mcp 页（1280x620） | 整页可滚 | 整页可滚（不变），`#viewAgent` 无 `is-skills-fixed` |
| ≤900px（900x620） | 单列 `845px`、页面滚动 | 同上，两栏无内部滚动（不变） |

截图（`/tmp/skl-evidence/`，均为补齐 design 后重跑）：`round05-baseline-1280x620.png`（右侧详情下半截掉出视口）
→ `round05-fixed-1280x620.png`（进视口、左右各自滚动）；`round05-baseline-1440x900.png` →
`round05-fixed-1440x900.png`；`final-skills-1280x620.png`、`accept-skills-900x620.png`（单栏不变）。

**自动回归**

`app/tests/agent_capabilities_ui.test.cjs` 新增 3 条，覆盖票面「实际页签切换行为与相关布局契约」：

- `切页签：skills 置上固定高度状态类，base / mcp 不带，且不重铺面板也不发请求` —— 走真实 `#adTabs`
  点击路径，断言状态类跟随、面板节点**引用不变**（不重铺 ⇒ 不丢草稿）、来回切零请求。
- `桌面档样式契约：skills 固定高度链条逐环在位，右栏只有一处滚动、正文不再自带 60vh` —— 1280 / 1440
  两档摊平 `app.css` 逐环求值：`min-height: 0` 传递、导入区 `flex: none`（「顶部导入区固定」的承重行）、
  导入区块不自带滚动条、两栏 `overflow-y: auto`、正文 `max-height: none`、元数据行不被压缩。
- `≤900px 单栏档：固定高度整块不生效，页面滚动与旧行为不变` —— 反向断言。

```sh
scripts/check.sh --studio      # 347 node tests passed；ruff / ruff format --check / mypy / pyright 全绿
```

**红证据在隔离基线上取得**（`scripts/red-evidence.sh 8c038ee app/tests/agent_capabilities_ui.test.cjs`）：

```text
基线 app.js md5：aaf09d6aa5437d49f7f85e2cd4d8501e
ℹ tests 30   ℹ pass 28   ℹ fail 2
✖ 切页签：skills 置上固定高度状态类…   AssertionError: skills 页签置上固定高度状态  false !== true
✖ 桌面档样式契约…                      AssertionError: 1280px：外层滚动器…  undefined !== 'auto'
```

## Merge Danger

**Door:** two-way（双向门）

纯呈现层改动，回滚就是 revert 这一个 commit：没有数据写入、没有 schema、没有接口变化，也没有
用户配置或迁移状态被改写。回归用例与红证据都是仓库内的文本，回滚后一并消失。

**Blast Radius:** 小（studio 单页签的布局）

- 只影响 Agent 详情 skills 页签在 `≥901px` 的呈现。`.d-sec` / `.kv` / `.skill-detail` 等共用类的既有声明
  一个未改；本票新增的两条窄选择器（`.agent-page.is-skills-fixed .skill-detail > .kv`、
  `.agent-page.is-skills-fixed .agent-skill-section > :not(.agent-skill-columns)`）都带状态类前缀，
  只在 skills 档生效。
- 基础信息和配置 / mcp 页、`≤900px` 单栏与移动端行为均未改，有正向 + 反向样式契约用例守着。
- 已知降级：极矮视口（如 1280x420）下双栏有 `min-height: 180px` 兜底，此时外层 `.agent-page-scroll`
  整页滚——刻意选择「宁可整页滚，不把两栏压成一条缝」。导入区不可压缩（`flex: none`）；它的结果行
  每次导入前先清空（`submitSkillImport()`，`app.js:8282`），不会跨导入累积，因此不需要导入区块内的二次滚动
  （试过，会把导入区自己的下拉框滚出视口，已回退）。极长单条错误同样由整页滚降级，内容仍可访问。
