# ADR-0017：深色取值层（同一 token 名集合的第二组取值）

> 状态：已采纳 ｜ 日期：2026-10-16 ｜ 票：ISSUE-71（本仓 `whutlichao/alibot` #71，母规格 #60 决策账 q1）
> 编号空间：**本包自己的 ADR 编号空间**（`DESIGN.md` / `SKILL.md` 引用的 `ADR-0014` / `ADR-0015` /
> `ADR-0016` 同属这一空间）。它与仓库的 `docs/adr/`（0009–0014）**互不占用**——本仓
> `CONTEXT.md` **视觉契约** 词条写明「该包里 `ADR-00xx` 的形式由设计系统自己编号与定义」。

## 背景

设计系统包自己登记过一条缺口（`DESIGN.md` 的 Known gaps）：「the source prototype implements …
no dark theme」。原型 `alibot-app.html` 只给一个 `<select>`-级别的假主题控件，包里没有任何深色取值。

ISSUE-71 的人定裁定（#60 决策账 q1，(b)）是**主题做实**：新增深色 token 族，主题三档
（浅色 / 深色 / 跟随系统），并且授权把写面扩到 `alibot-design-system/**`。没有深色取值时，
「跟随系统」是一个**假档位**；因此本 ADR 先把取值层定死。

## 决定

### 1. 深色是**同一 token 名集合**的第二组取值

不改名、不新增色相、不新增名字：深色层只对浅色层已有的名字给出第二组值，色相角（oklch 的第三位）
**逐字不变**——该家族是哪个角色就还是哪个角色。这条是仓库 `CONTEXT.md` **视觉契约** 的承诺：
下游按**名**消费 token，改名不是改一处定义，而是同时扯断全部消费点。

实测（`.scratch/theme_audit.py`，ISSUE-71 的取证脚本）：

| 文件 | 浅色档声明名 | 深色层覆盖名 | 深色层**新增**名 |
| --- | --- | --- | --- |
| `colors_and_type.css` | 85 | 17 | **0** |
| `tokens.css` | 46 | 3 | **0** |
| `ui_kits/app/product-ext.css` | 14 | 8 | **0** |
| `app/alibot/studio/product-tokens.css` | 14 | 8 | **0** |

### 2. `fill` 档两档同值；压其上的墨色固定为深梅墨

马卡龙亮档（`--accent` / `--accent-hover` / `--selected` / `--unread` / `--warn` / 四个
presence `-fill` / `--av-0…--av-4`）**是这一族的本色**，深色档不把它压暗。改成「深色档把 fill
压深、墨色翻浅」会让 `fill` 档不再是马卡龙亮档，与 `SKILL.md` §1 的档位语义（fill = the pastel
itself）冲突。

代价只有一个：`--on-accent` 在浅色档是 `var(--fg)`，而深色档 `--fg` 是**浅**梅墨。因此深色档把
`--on-accent` **显式固定**为深梅墨（与浅色档逐字同值 `oklch(33% 0.045 300)`）。于是：

- 主按钮 / 徽标（brand mark）/ 未读徽标 / 头像块 / `err` toast 的「底 × 墨」对比度**两档完全一致**；
- 「`fill` 档只与 `--on-accent` 配对」在两档都成立。

副作用（有意保留）：以 `--fg` 为**填充**的两处（`.toast`、`.seg button.is-active`）在深色档
自然**反相**成浅底 + 深字（`--fg` 底 / `--bg` 墨的对比度在两档分别是 11.71:1 / 14.36:1）。
这是同一件「反相浅片」在两个档位下的同一条规则，不另立分支。

### 3. 选择机制只有两条：显式标记 + 系统媒体查询

- 显式档位 → `:root[data-theme='dark']`（应用把工作区设置里的档位写到 `<html>` 上）；
- 「跟随系统」→ `@media (prefers-color-scheme: dark)`（`data-theme='system'` 或未标记时生效）。

两条都写进 CSS，且都落在**声明块**上：

```css
:root[data-theme='dark'] { …深色取值… }
@media (prefers-color-scheme: dark) {
  :root[data-theme='system'],
  :root:not([data-theme]) { …同一组深色取值… }
}
```

### 4. 取值落在各文件自己的块里，不新开文件

`colors_and_type.css` 持颜色族的深色值，`tokens.css` 持 elevation 的深色值（浅色档的阴影是
「深梅墨 + 低不透明度」压在浅纸上，深色档底本身就暗，同值会看不见分层表面），
三个产品角色族（`--selected*` / `--unread*` / `--warn*`）持在**产品扩展层的两个落点**
（`ui_kits/app/product-ext.css` 与 `app/alibot/studio/product-tokens.css`）——它们本来就要求两处同改。

不新开 `dark.css` 的理由：包的两个 foundation 是「import 这两个文件就够」的契约（`SKILL.md` §1），
再加一个必须记得加载的第五个文件，会让「颜色只有一个正本」变成「颜色分散在两个正本」。

### 5. `color-scheme` 跟着本层走

`colors_and_type.css` 的浅色 `:root` 显式声明 `color-scheme: light`，深色两层声明
`color-scheme: dark`。原生控件（滚动条、`<select>` 弹出层）因此跟**本层**的明暗走，而不是跟
操作系统走——否则「显式浅色 + 深色系统」会出现浅色界面配深色原生控件。

## 备选与不选的理由

| 备选 | 不选的理由 |
| --- | --- |
| 深色档把 `fill` 压深、`--on-accent` 保持 `var(--fg)` | 让 `fill` 档不再是「马卡龙亮档」，与包内档位语义（fill = the pastel itself）分叉；且要改 13 个 fill 名，深色层翻倍 |
| 用 `light-dark()` + `color-scheme` 单份取值 | 只支持较新的浏览器，且要把**浅色**取值整体改写进 `light-dark(浅, 深)`，浅色档的声明被重写——收益（少一份重复块）不抵风险 |
| 由应用 JS 用 `matchMedia` 解析「跟随系统」并只写 `data-theme` | 无 JS / JS 未跑完时没有深色，且把「系统外观」这条 CSS 事实搬到 JS；票面明确要求「跟随系统」走 `prefers-color-scheme` |
| 新开 `dark.css` | 见 §4 |
| 深色层只覆盖「会变的那几个名字」，不覆盖 elevation | 分层表面（modal / popover / composer / 深色档 toast）在深色档会丢掉「浮起来」的信号；3 个名字的代价换回 elevation 语义连贯 |

## 后果

- **必须两处同改**：CSS 没有把同一条声明块同时挂到属性选择器与媒体查询下的办法，因此同一组取值在
  每个文件里出现两次（`colors_and_type.css` 17 名、`tokens.css` 3 名、两个产品文件各 8 名）。
  这是本决定唯一的维护代价；漂移由 ISSUE-71 的取证脚本（token 名集合比对 + 取值块逐字比对）兜住，
  而不是靠注释提醒。
- **下游消费方按名沿用**：深色层不新增名字，因此任何按名消费的下游（本仓 `app/alibot/studio/`
  的 `app.css`、包内 `ui_kits/app/app.css` 与全部切片）**不需要为深色档改选择器**；只有「把 token
  当颜色用错层」的地方需要修（见下）。
- **应用层顺带修掉的三处「用错层」**（落点在本仓 `app/alibot/studio/app.css`，不属于本包）：
  1. `.avatar` 的墨色由 `--fg` 改为 `--on-accent`——头像块的底是 `--av-*`（fill 档），墨色按纪律只能
     是 `--on-accent`；浅色档两者同值，因此浅色档读数一字不变，深色档才可读；
  2. `.overlay` 的遮罩由 `--fg` 改为 `--on-accent`——遮罩必须**两档都暗**，而 `--on-accent` 是两档
     都暗的那支墨（浅色档同值）；
  3. `.toast .dot` 在深色档用 `--bg`——toast 在深色档反相成浅底，绿色点（`--online`）压在其上只有
     2.06:1，低于 §5 的图形档 ≥3:1；改为该底面的墨色后是 14.36:1。
- **对比度读数**（`SKILL.md` §5：文字 ≥4.5:1、图形 ≥3:1；由 `.scratch/theme_audit.py` 计算）：

| 组 | 名字 | 最差读数 | 阈值 |
| --- | --- | --- | --- |
| 正文 | `--fg` | 12.78:1（on `--surface`） | 4.5 |
| 次要文字 | `--muted` | 7.84:1 | 4.5 |
| 三级文字 | `--faint` | 5.04:1 | 4.5 |
| 家族文字档 | `--accent-deep` / `--selected-deep` / `--unread-deep` / `--warn-deep` / `--online-text` / `--working-text` / `--offline-deep` / `--error` | 6.02:1 | 4.5 |
| 图形档 | `--accent-graphic` / `--selected-graphic` / `--unread-graphic` / `--warn-graphic` / `--online` / `--working` / `--offline` / `--error` | 5.66:1 | 3.0 |
| fill × `--on-accent` | `--accent` / `--accent-hover` / `--selected` / `--unread` / `--warn` / 四个 `-fill` / `--av-0…--av-4` | 5.19:1（`--error-fill`） | 4.5 |

- **既有数据目录**：本决定只动 CSS，不动任何持久化结构；工作区设置里多出的 `theme` 档位由应用侧
  （ISSUE-71 的 `SCHEMA_VERSION → 10` 与 `workspace_settings.theme` 列）承担，与本包无关。
