# 补票 ⑨ 验收取证（issue #9：composer 里 `@` 的成员补全）

本文件是 Answer 的支撑材料：每条验收项记录**命令**与**可见结果**，只列实际跑过后出现的事实。
issue 上的 Answer 摘要指向本文件。

> 阅读说明：§4 的代码块是**多次**真实浏览器实测的输出片段，每次都在独立临时数据目录上跑，
> 因此频道 id 与 seq 起点在各片段间不同 —— 它们记录的是各自那一次的事实，不是同一条连续时间线。

- 仓库：`whutlichao/alibot`
- worktree：`/Users/apple/orca/workspaces/alibot/issue-9-mention-complete`（Orca 独立顶层 worktree）
- 基线：`origin/main` 的 `25108fb`（#7 合并后的 main）；worktree 由它创建，未 rebase、未合并 main
- 采集时间：2026-10-14
- 环境：macOS，Python 3.11（`uv venv`），真实 Chromium（ego-browser）驱动
- 有效用例数：**152 passed, 1 skipped**（`app/tests/`；跳过的就是那条默认跳过的真模型冒烟测试）
- 票标题（开工时用 `gh issue view 9` 核对）：

> 补票：composer 里 @ 的成员补全（#2 承诺、#3 漏接的能力）

## 0. 前置 skill 与流程

按票面要求完整读了 `/Users/apple/.agents/skills/implement/SKILL.md`，依其流程执行：
读票（`gh issue view 9`）→ 声明票标题 → 先只读核实两个事实来源
（`app/worksplice/mentions.py` 的解析与引号规则、`app/worksplice/studio/app.js` 现有 composer
键盘处理）→ 实现 → 定期跑类型检查与单文件测试 → 全量 → **聚焦自审**（§7）→ 提交 → PR。

票面要求「任何影响交付行为、方案分叉或 scope 边界的决策走 `ask`」。本票共 3 组，均经
`orca orchestration ask` 提问并取得回执，裁定见 §5。

**影响面可圈定**（一个 composer 交互 + 它的取证），因此按票面允许省掉完整双轴 code-review，
改为 §7 的**聚焦自审**：逐条比对本票影响面、并逐一确认既有行为未被破坏。

## 1. 门禁结果（全量）

```text
$ .venv/bin/python -m pytest
152 passed, 1 skipped in 94.05s        # skipped = 默认跳过的真模型冒烟测试

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed! / 31 files already formatted

$ .venv/bin/python -m mypy             # 配置在仓库根 mypy.ini
Success: no issues found in 24 source files

$ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
pyright errors 0 files 24
```

本票**不需要**真模型调用、不需要任何凭证、不产生费用（§4 的 Agent 行为全部由脚本化替身固定）。

## 2. 事实来源核实（只读，先做）

| 事实 | 正本 | 本票怎么用 |
| --- | --- | --- |
| 名字含空格时写 `@"带 空格的名字"`，引号内容必须**完全相等** | `app/worksplice/mentions.py::_match_quoted` + `_QUOTE_PAIRS`（`"…"` / `“…”` / `‘…’` 三对） | 前端 `mentionText()` 照它拼插入字符串；**不**新增解析规则 |
| 裸形式的候选串遇到 `_TOKEN_STOPS` 即止；必须**恰好等于**成员名（不做前缀匹配） | `mentions.py::_candidate_token` / `_match_bare` | 决定「名字含分隔符 ⇒ 必须走引号形式」，也解释了「插入后必须留一个空格」（§3.6） |
| `@` 左边界：前面是 **ASCII** 字母或数字时不算提及 | `mentions.py::_left_boundary_ok` / `_is_ascii_alnum` | 前端触发判据（要不要弹清单）直接复用这条规则，见 §3.3 |
| composer 键盘契约：`Enter` 发送、`Shift+Enter` 换行、`isComposing` 不算发送 | `app.js::bindEvents` 的 `#composerInput` keydown | 补全只**插一层**（清单打开且有候选时先接管），关闭时与改前逐字一致（§7） |
| 成员渲染语汇：方形头像块 `avatarNode`、`.member-opt` 胶囊 | `app.js::avatarNode`、`renderMemberPicker` | 清单行复用同族形态，不新造色位（§3.8） |

## 3. 逐条验收（命令 + 可见结果）

### 3.1 输入 `@` 弹出清单，只含当前频道的 Agent 成员

数据目录 `/tmp/wsps9-data`（**不是** `~/Worksplice`），真实 uvicorn 进程 + 真实 Chromium。
频道「补全频道」的成员是 `小明`、`前端 设计师`、`怪"名`；`队友`、`局外人` **不在**该频道；
owner 不在任何 Agent 清单里。

```text
CASE1 输入 @ ：
{"value":"@","caret":1,"pickerHidden":false,
 "options":[{"name":"小小明","active":true},{"name":"前前端 设计师","active":false},
            {"name":"怪怪\"名无法 @","active":false}],
 "emptyText":null,"hint":"↑↓ 选择 · Enter 选中 · Esc 关闭"}
```

**读法**：清单恰是**该频道**的三个 Agent（顺序 = 后端成员表顺序）；owner 未出现；
两度不在该频道的 `队友` / `局外人` 未出现。第一项带 `is-active`（键盘当前指向），
`怪"名` 行尾多一个 `无法 @` 标记（见 §3.9）。截图
`/tmp/wsps-evidence-9/01-at-opens-picker.png`。

候选来源就是后端成员表本身：前端只做 `kind === "agent"` 过滤，不另猜一份名单；
「不含 owner」是**产品选择**而非解析器限制——`@Owner` 在后端仍是一条提及（§3.10 用例）。

### 3.2 键盘：`↑`/`↓` 移动、`Enter`/`Tab` 选中、`Esc` 关闭不改正文

```text
CASE4 ↓↓ 后高亮：[{"name":"小明","active":false},{"name":"前端 设计师","active":false},{"name":"怪\"名","active":true}]
CASE4 ↑ 后高亮： [{"name":"小明","active":false},{"name":"前端 设计师","active":true},{"name":"怪\"名","active":false}]
CASE4 Esc：      {"valueUnchanged":true,"caretUnchanged":true,"hidden":true}
CASE4 Esc 后继续输入：{"value":"@继续输入不被打断","caret":9,"hidden":true}
```

`Enter` 选中、`Tab` 选中分别见 §3.4、§3.5。`Esc` 只关清单：正文与光标位置**逐字节相同**，
且关掉的只是**那一个**触发点（把光标移开或改前缀后清单会重新可用）。

### 3.3 左边界与后端对齐（`@` 不误判：邮箱式 / 中文紧贴）

```text
CASE6a user@ 不弹清单：{"value":"联系 user@","caret":8,"hidden":true,"options":[]}
CASE6b v1.2@ 不弹清单：{"value":"版本 v1.2@","caret":8,"hidden":true,"options":[]}
CASE6d 中文紧贴 你好@ 要弹：[{"name":"小明",...},{"name":"前端 设计师",...},{"name":"怪\"名",...}]
```

**这条是「前端触发判据 ↔ `mentions.py._left_boundary_ok` 的对齐关系」**（coordinator 裁定 Q1=A）：
`@` 前面紧贴 ASCII 字母/数字（邮箱式、`v1.2@`）时**不弹清单**——那种位置插进去的 `@名字`
必然不被解析，照字面弹出会违背票面硬约束。紧贴中文（`你好@`）仍弹：后端裁定 2B 只排除邮箱式。

### 3.4 选中后替换 `@前缀`、留一个空格、光标落到插入文本之后（裸形式）

```text
CASE2 过滤后候选：{"value":"@小明","caret":3,"hidden":false,"options":[{"name":"小明","active":true}]}
CASE2 Enter 选中后：{"value":"@小明 ","caret":4,"hidden":true,"hint":"Enter 发送 · Shift+Enter 换行"}
```

`@小明`（3 字符）被整段替换成 `@小明` + 一个空格，光标落在下标 4 = 插入文本之后。
过滤是**包含匹配**（`@设计` 能命中 `前端 设计师`），ASCII 大小写不敏感（coordinator 裁定 A）。

### 3.5 名字含空格 ⇒ 引号形式 `@"带 空格的名字"`

```text
CASE3 Tab 选中后：{"value":"@\"前端 设计师\" ","caret":10,"hidden":true}
CASE10 鼠标点击第 2 项：{"value":"请 @\"前端 设计师\" ","caret":12,"hidden":true}  row=前前端 设计师
```

选中 `前端 设计师` 插入的是 `@"前端 设计师"`（9 字符）+ 空格，光标 10。鼠标点击同一条路径
（`mousedown` 拦截，textarea 不失焦，插入用的是它当前的选区）。

### 3.6 **硬约束：两种插入形态都被后端解析器认可**

票面要求「两种形式都必须是后端解析器**认可**的提及」，且这一条**必须进 pytest**。pytest 侧见
`app/tests/test_mention_completion.py`（§6）；浏览器侧把前端真正插入的字符串经 HTTP 面送出：

```text
CASE13 引号形式：{"body":"@\"前端 设计师\" 这条按引号形式插入","snapshot":[{"member_id":"mem_2b897e09aee3","display_name":"前端 设计师","start":0,"end":9}]}
CASE13 裸形式  ：{"body":"@小明 这条按裸形式插入","snapshot":[{"member_id":"mem_547e87935163","display_name":"小明","start":0,"end":3}]}
```

两条都在真实浏览器里由补全插入、再经真实 HTTP 面发送，`mention_snapshot` 都命中该成员。
截图 `/tmp/wsps-evidence-9/03-two-insert-forms.png`（消息流里两种形态都被既有 `--accent-soft`
底 + `--accent-deep` 字高亮）。

**「插入后必须留一个空格」有可复现的反证**（pytest `test_a_name_glued_to_following_characters_stops_being_a_mention`）：
裸形式的候选串必须**恰好等于**成员名，所以 `@小明一下` 的候选串是 `小明一下`、不命中 `小明`；
`@小明 一下` 才命中。这就是前端「插入后一律留一个空格」的根据（光标后本来就有空白时不再多补，
两种情形是同一条规则的两面）。

### 3.7 过滤无匹配 / 空态 / 不打断输入

```text
CASE7 无匹配：{"value":"@查无此人","caret":5,"hidden":true,"options":[]}
CASE7 继续输入未被打断：{"value":"@查无此人 继续打字","caret":10,"hidden":true}
CASE8 空态：{"value":"@","caret":1,"hidden":false,"options":[],
  "emptyText":"该频道还没有 Agent 成员：用左上角「Agent」按钮创建一个，再在新建频道时勾选它加入这个频道。",
  "hint":"Enter 发送 · Esc 关闭清单"}
```

无匹配时清单消失且**不打断**输入（继续打字正常、不吞按键）。频道没有 Agent 成员时给**明确空态**
而不是静默无反应，文案与新建频道弹窗的既有空态同一语汇；空态下 `Enter` 仍是发送（§3.11）。
截图 `/tmp/wsps-evidence-9/02-empty-state.png`。

成员清单**读不到**（后端请求失败）时是另一条独立空态：`暂时读不到该频道的成员清单（后端请求失败）：
恢复后按 @ 会重新给出候选。` —— 不把「读失败」伪装成「没有成员」。

### 3.8 位置正确性：正文中间 / 行首 / 多个 `@` / 选中后继续输入

```text
CASE5a 正文中间输入 @小 ：{"value":"请看一下@小","caret":6,"options":[{"name":"小明","active":true}]}
CASE5a 选中后（替换掉 @小 而不是重复插入）：{"value":"请看一下@小明 ","caret":8}
CASE5b 多个 @（光标在第二个之后）：{"value":"你好@小明 和 @","caret":9,"options":[小明,前端 设计师,怪"名]}
CASE5b 选择后：{"value":"你好@小明 和 @小明 ","caret":12}
CASE11 选中后继续输入：{"value":"@小明 请看这个","caret":8}
```

行首（§3.4）、正文中间、多个 `@`（只按**光标处**那个触发）、选中后继续输入都无错位、无重复插入。

### 3.9 名字含引号字符的成员：保留在清单、不插入无效文本、给可读提示

`mentions.py` 把 `" “ ” ‘ ’` 都当分隔符，因此这类名字的可表达性要按**引号对**逐一看：

```text
怪"名 候选与标记：{"value":"@怪","options":["怪怪\"名"],"note":[]}
怪"名 选中后：{"value":"@“怪\"名” ","caret":7}
引号形式前缀过滤：{"value":"@“小明","options":["小小明"]}

极端成员在清单里（a"b”c’d）：{"options":[{"name":"a\"b”c’d","note":"无法 @"}]}
选中后：{"value":"@","panelHidden":false,
  "toasts":["「a\"b”c’d」同时含 \" 、” 、’ 三类闭引号，提及解析器没有能表达这种名字的引号形式，因此没有插入任何文本。"]}
```

**读法**（coordinator 裁定 A，且这是本票唯一的规则修正点）：名字含 `"` 时**换一对引号**
`@“怪"名”` —— 那是合法形态、且被解析器认可（pytest 里已断言）。只有当名字同时含
`"` / `”` / `’` **三个闭引号**时才真的没有合法形态：该成员**保留在清单里**、行尾标 `无法 @`、
选中时**不插入任何无效文本**而是给一句可读提示（既满足票面硬约束，也不违反项目「不静默」原则）。
截图 `/tmp/wsps-evidence-9/06-uninsertable-toast.png`。手打 `@“` 前缀也能正常过滤候选。

### 3.10 硬约束的 pytest 侧（「插入的文本确实是后端认可的提及」）

见 §6 的逐条表格。核心是：把**前端会插入的那两种文本形态**经 HTTP 面发送，断言
`mention_snapshot` 命中该成员，含带空格名字的引号形式。

### 3.11 键盘契约不破：`Enter` 在清单打开时选中、关闭时发送；`Shift+Enter` 换行

```text
CASE9a Shift+Enter 换行：{"value":"第一行\n第二行","caret":7,"hidden":true} posts= 0
CASE9b 清单打开时 Enter 选中而非发送：{"value":"@小明 ","caret":4,"hidden":true} posts= 0
CASE9c 清单关闭时 Enter 发送：[{"url":"/api/channels/ch_84d30c269558/messages","body":"@小明 看这个"}]
```

`posts` 是页面内对 `POST …/messages` 的拦截计数（没有发送就是 0）。三条一起证明：
`Shift+Enter` 仍是换行且不发送；清单打开时 `Enter` 只选中；清单关闭时 `Enter` 才是发送。

### 3.12 视觉与布局契约

```text
CSS 契约自查：{"弹层规则含oklch字面量":false,
 "弹层规则里的token":["var(--sp-3)","var(--sp-2)","var(--border)","var(--selected-graphic)",
                      "var(--selected-soft)","var(--selected-deep)","var(--sp-5)","var(--fs-caption)","var(--warn-deep)"],
 "全文件hex":[],"全文件rgb":[],"全文件命名色":[]}
弹层动效属性：{"transitionDuration":"0s","animationName":"none"}
reduce 下弹层动效：{"matchesReduce":true,"transitionDuration":"0s","animationName":"none"}
```

弹层只用既有 token（`--selected*` 是既有产品扩展层角色族的「当前位置」档位，与 `.member-opt.is-on`
同一档位语义；`--warn-deep` 是既有警告文字档）。**不新造色位**：全文件无 hex / 无 `rgb()` /
无 CSS 命名色。弹层没有任何过渡或动画，因此 `prefers-reduced-motion: reduce` 下天然无动效。

三栏布局与正文度量（`VIEWPORT 1280x700` / `1000x620` / `880x700` 三档）：

```text
VIEWPORT 1280x700      {"rail":{"w":252},"dockW":"380px","appFlexDir":"row",
                        "box":{"w":880},"picker":{"w":878,"bottom":579},"pickerInViewport":true,"optionsVisible":3}
VIEWPORT 1000x620      {"rail":{"w":252},"dockW":"340px","appFlexDir":"row",
                        "box":{"w":708},"pickerInViewport":true,"optionsVisible":3}
VIEWPORT 880x700       {"rail":{"w":880,"h":294},"dockW":"100%","appFlexDir":"column",
                        "box":{"w":852},"pickerInViewport":true,"optionsVisible":3}
CASE14 布局与文本度量：{"static":{"fontSize":"14px","lineHeight":"23.1px","maxWidth":"599.648px"},
                        "composerBoxClip":"visible","pickerOverflowY":"auto"}
```

`rail 252` / `dock 380`（≤1080px 340）/ `≤900px` 列布局 / 消息流内层 `880` 全部未变；
正文仍是 14px / 1.65（= 23.1px）/ 68ch（599.6px @14px 等宽度量）。弹层挂在 `.composer-box`
**内部**（`overflow: visible`），因此不被 composer 裁剪、也不依赖新 z-index 档位；三档视口下
`pickerInViewport` 全为 true。截图 `/tmp/wsps-evidence-9/05-880-column.png`。

设计系统与原型**只读**：

```text
$ git status --short worksplice-design-system/
（无输出）⇒ 未修改 worksplice-design-system/ 与原型 worksplice-app.html
```

### 3.13 候选随成员变化刷新（穿透加入）

`事件频道` 初始只含 `小明`。经 HTTP 发一条 `@队友`（`队友` 不在该频道，但它是 Agent ⇒ 穿透并加入）：

```text
事件频道候选（应只有 小明）：["小明"]
穿透加入后候选（应含 队友）：["小明","队友"]
该频道成员表：["Owner","小明","队友"]
```

候选刷新挂在 **composer 自己的生命周期**上（`#composerInput` 的 `focus` 事件 + 切频道 + 初始加载），
**没有**碰 `onStreamMessage` / `renderMessages` / `onStreamOutcome` —— 那是 #8 的领域（§7）。

## 4. 以独立临时数据目录实跑（后端 + 真实浏览器）

数据目录 `/tmp/wsps9-data`（**不是** `~/Worksplice`），后端是真实 uvicorn 进程，浏览器是真实
Chromium（ego-browser）。Agent 行为由脚本化替身固定 ⇒ **零付费模型调用、零凭证**。

```text
$ WSPS_APP_DIR=<worktree>/app WSPS_DATA=/tmp/wsps9-data WSPS_PORT=8791 \
    .venv/bin/python /tmp/wsps9_backend.py
data dir: /tmp/wsps9-data
$ curl -s http://127.0.0.1:8791/api/health
{"status":"ok","member_id":"owner","data_dir":"/tmp/wsps9-data"}
```

造数（4 个频道 + 6 个 Agent，含带空格名、含引号名、三个闭引号都占的名字）：

| 名称 | 用途 |
| --- | --- |
| `小明` | 裸形式插入 |
| `前端 设计师` | 含空格 ⇒ 引号形式插入 |
| `怪"名` | 含 `"` ⇒ 换 `“…”` 一对 |
| `a"b”c’d` | 三个闭引号都占 ⇒ 无合法形态（清单里标 `无法 @`） |
| `队友` / `局外人` | 不在「补全频道」⇒ 验证候选只含本频道成员；并用于穿透加入 |

频道：`补全频道`（小明 + 前端 设计师 + 怪"名）、`空态频道`（无 Agent 成员）、
`事件频道`（只含 小明，用于 §3.13）、`极端名字频道`（只含 `a"b”c’d`）。

浏览器取证截图（`/tmp/wsps-evidence-9/`）：`01-at-opens-picker.png`、`02-empty-state.png`、
`03-two-insert-forms.png`、`04-1280x700.png`、`05-880-column.png`、`06-uninsertable-toast.png`、
`07-final-picker.png`。

## 5. 提问与裁定（走 `orca orchestration ask`，不自行拍板）

| 问题 | 裁定 | 对实现的影响 |
| --- | --- | --- |
| Q1 名字含引号字符的成员怎么处置？过滤是否大小写不敏感？ | **A**（人类裁定） | 保留在清单、选中时不插入无效文本并给可读提示；过滤 **ASCII 大小写不敏感**（插入的始终是库里的原名，所以放宽过滤不会插错）。另附同意：未启用（`offline`）的频道 Agent 成员**仍列入**清单 |
| Q2 左边界不合法的 `@`（邮箱式）要不要弹清单？ | **A**（同意我的推理） | **不弹**：把 `_left_boundary_ok` 搬进前端**触发判据**（不是重写解析）；票面「候选匹配以后端规则为准」即此 |
| Q3 无 Agent 成员的空态下 `Enter`/`Tab` 归谁？ | **A** | 空态弹层**不算**「可选中清单」：`Enter` 保持既有语义（发送）、`Tab` 不吞、`Esc` 关空态 |

Q1 的裁定还留下一处**规则修正**（本票唯一的实现分叉），见 §3.9：不是「含引号就放弃」，
而是按引号对逐一看 —— 名字含 `"` 时换 `“…”` 一对即可表达，只有三个闭引号都占时才真的没有形态。
这条修正只动前端拼字符串的规则，**没有**改 `mentions.py` 或 `mention_snapshot` 结构。

## 6. pytest 覆盖（`app/tests/test_mention_completion.py`，9 条）

只断言 HTTP 面可观察行为（发送返回的 `mention_snapshot`、频道成员表），不 import 实现内部、
不读表结构、不断言前端 DOM。

| 用例 | 断言 |
| --- | --- |
| `test_bare_form_is_a_mention_the_backend_records` | 前端插入的 `@小明 ` → 快照命中该成员 |
| `test_quoted_form_is_a_mention_the_backend_records` | 前端插入的 `@"前端 设计师" ` → 快照命中该成员（**票面点名的带空格引号形式**） |
| `test_quoted_form_is_recognized_for_any_name_the_parser_treats_as_a_token_stop` | 名字含任意分隔符（`甲、乙`）⇒ 引号形式被认可；同名的裸形式**不是**提及（对照面） |
| `test_a_name_containing_a_quote_is_inserted_with_another_quote_pair` | 名字含 `"` ⇒ 用 `@“怪"名”`，被认可；`@"怪"名"` **不是**（对照面） |
| `test_a_name_with_every_closing_quote_has_no_insert_form` | 三个闭引号都占 ⇒ 无形态；三种引号形式逐一试过都不被认可 |
| `test_a_name_glued_to_following_characters_stops_being_a_mention` | `@小明一下` **不是**提及、`@小明 一下` 是 ⇒ 「插入后留一个空格」有据 |
| `test_the_inserted_name_is_the_stored_name_so_leftmost_longest_picks_that_member` | `小明` 与 `小明王` 并存：插名字分别命中各自那条成员行（最左最长语义不被前端放宽的过滤改变） |
| `test_candidate_source_is_the_channel_agent_members_and_excludes_owner_and_outsiders` | 候选来源 = 频道成员表（含 owner、只含本频道 Agent）；并记录 `@Owner` / 频道外 `@名字` 在后端**仍是提及**（产品选择 ≠ 解析器限制） |
| `test_inserted_form_is_recorded_when_the_caret_was_in_the_middle_of_the_body` | 正文中间的插入形态位置快照正确（`start == 2`） |

前端与 pytest 共享同一条拼字符串规则：`app.js` 的 `mentionText()` / `MENTION_TOKEN_STOPS` /
`MENTION_QUOTE_PAIRS` 与测试文件里的 `frontend_insert_form()` / `FRONTEND_TOKEN_STOPS` /
`FRONTEND_QUOTE_PAIRS` 是**文字转录**关系（测试文件头部写明了这一点），两侧都**只拼字符串、
不解析正文**。

### 红证据（在隔离的基线 worktree 上取，主工作树未被改动）

`git worktree add /tmp/wsps-red-9 25108fb`，只把本票的测试文件拷过去，注入前端拼接规则的缺陷：

| 注入 | 判红用例数 | 代表判红用例 |
| --- | --- | --- |
| A 只把空格当分隔符（忽略其它 `_TOKEN_STOPS`） | 3 | `test_quoted_form_is_recognized_for_any_name_the_parser_treats_as_a_token_stop`、`test_a_name_with_every_closing_quote_has_no_insert_form` |
| B 名字含引号字符就放弃（不换另一对引号） | 1 | `test_a_name_containing_a_quote_is_inserted_with_another_quote_pair` |
| D 引号对固定用直引号（不看闭引号是否在名字里） | 2 | 同 A 两条 |

```text
--- 注入 A → 3 failed, 6 passed
--- 注入 B → 1 failed, 8 passed
--- 注入 D → 2 failed, 7 passed
--- 还原后 → 9 passed
```

**基线（`25108fb`）上这 9 条本来就全绿**，这是**正确且必须澄清**的一点（coordinator 已认可）：
本票补的是**前端缺失的能力**，`mentions.py` 是既有正本、本票不改它，因此「通过 HTTP 面构造插入
形态 → 快照命中」在基线就已成立。这组 pytest 的判别力在**契约冻结**方向 —— 它钉住「前端拼接规则
一旦偏离解析器就判红」（上表 3 组注入）与「票面点名的两种形态确实被认可」。因此**前端行为**的
红→绿证据由 §3 的真实 Chromium 手跑承担（候选来源、触发判据、插入落位、空态在基线上都不存在）。

## 7. 聚焦自审（本票影响面 + 既有行为未被破坏）

票面允许省掉完整双轴 code-review，但要求在本节记录聚焦自审。

### 7.1 影响面（逐文件）

```text
 app/worksplice/
   studio/app.js                        +283/-5（净 +280）
     ├─ 新增「@ 补全」一整节（判据、状态、渲染、插入、提示）
     ├─ state.channelMembers（候选缓存，不是业务正本）
     ├─ loadWorkspace / selectChannel 各加一行 refreshChannelMembers()
     ├─ sendMessage 加一行 closeMentionPicker()
     ├─ renderHeader 无频道时关清单
     └─ bindEvents 的 #composerInput 键盘/焦点处理扩了补全分支
   studio/app.css                       +21/-0    仅新增 .composer-mention / .mention-* 规则
   studio/index.html                    +3/-0     仅在 .composer-box 内加弹层挂载点
   tests/test_mention_completion.py     新增 9 条
   specs/issue-9-acceptance.md          新增（本文件）
```

### 7.2 未越界确认（对比 #8 的领域）

```text
$ git diff app/worksplice/studio/app.js | grep -c "onStreamOutcome"
0
$ git diff app/worksplice/studio/app.js | grep -cE "^\+.*(renderMessages|onStreamMessage|msg-retry|round-outcome)"
0
```

未新增/未修改任何消息流渲染、失败呈现、`onStreamOutcome` 及其 CSS。中途曾把「候选刷新」挂在
`onStreamMessage` 上，收到 coordinator 的范围提示后**已移出**，改挂 composer 自己的 `focus`
事件 + 切频道 + 初始加载（§3.13 用穿透加入复验了这条新路径仍然有效）。

### 7.3 逐条确认既有行为未被破坏

| 既有行为 | 确认方式 | 结果 |
| --- | --- | --- |
| `Enter` 发送 | §3.11 CASE9c（真实 POST 计数）；`test_send_message.py` 12 条全绿 | 未变 |
| `Shift+Enter` 换行 | §3.11 CASE9a（`posts= 0`、正文含 `\n`） | 未变 |
| 输入法组合中的 `Enter` 不算发送 | 代码从 `!event.isComposing` 改为 `if (event.isComposing) return`（更严：整段组合期不接管） | 未变（且更一致） |
| 正文提及高亮 | §3.6 消息流取证（两种形态都被高亮）；`test_mentions.py` 14 条全绿 | 未变 |
| `@` 不误判（邮箱式 / 中文紧贴） | §3.3 浏览器 + `test_mentions.py::test_email_like_text_is_not_a_mention` / `::test_mention_after_chinese_text_is_a_mention` / / 全部 `test_mentions.py` | 未变（**未改 `mentions.py`**） |
| 唤醒面（提及唤醒 / 穿透） | `test_agent_round.py` 32 条 + `test_bounded_relay.py` 25 条全绿；§3.13 真实穿透加入 | 未变 |
| 超长正文校验 | `test_send_message.py` 全绿 | 未变 |
| 三栏布局 / 正文 68ch、14px、1.65 | §3.12 三档视口几何 | 未变 |
| 弹层不被裁剪 / 不溢出 | §3.12（挂在 `.composer-box` 内、`overflow: visible`、三档 `pickerInViewport: true`） | 满足 |
| 设计系统与原型只读 | §3.12 `git status` 无输出 | 未改 |
| `prefers-reduced-motion` | §3.12（弹层无动效；既有 `working` 脉冲与 toast 的 reduce 规则未动） | 未变 |
| 业务状态正本仍然后端 | 候选只来自 `GET /api/channels/{id}/members`；前端不保存业务状态（无 localStorage 新增） | 未变 |

### 7.4 自审发现并已处置的项

- 中途一处中文注释错字（「清樕」）已改。
- 一处嵌套三元（`mentionPicker.empty`）被静态检查判"难读"，已改成 if/else。
- `app.css:20` 的 `!important` 与 `app.js` 里 `state.localProviders.indexOf` 两条静态告警
  **在基线即存在**（`git diff` 里不是新增行），未动。
- 新增弹层 `.mention-opt`（0,2,0）与既有 `.member-pick .member-opt`（0,2,0）同权重，
  依赖加载顺序；弹层用的是**更具体**的 `.mention-opt.is-active`（0,3,0）覆盖 `.is-on`，
  真实浏览器里已确认高亮生效（§3.2），无回归。

## 8. 取舍与遗留

**取舍**

- **前端只拼字符串、不解析**：候选来自后端成员表，插入按 `mentions.py` 的引号规则拼，是否提及
  始终由后端在落库时裁决。因此 `Mention` 的语义没有第二个落点。
- **空态与「读不到」是两条独立空态**：不把「成员清单读不到」伪装成「频道没有成员」。
- **`@Owner` 仍是提及**（后端既有事实），只是补全**候选**里不列 owner —— 票面要求的是补全清单
  不含 owner，本票没有、也不应该去改解析事实。
- **弹层挂在 composer-box 内部**（文档流），而不是 `position: absolute` + 新 z-index 档位：
  这样「不被裁剪 / 不溢出」由布局天然保证，也无需新增 z-index 分档。

**遗留（记录，不在本票范围）**

- 未做 `/` 命令面板、跨频道补全、chip/富文本（票面明确不做）。
- 未改后端解析规则与 `mention_snapshot` 结构（票面明确不做）。
- 需要「键盘高亮项与鼠标悬停项同步」这类细节时，当前实现只给键盘高亮（鼠标点击即选中，
  未额外做 hover 与键盘高亮的联动）—— 票面未要求，如实记录。
- worktree 基线仍是 `25108fb`；与 #8（现已合并进 main 的 `8d5c8da`）在 `app.js` / `app.css`
  上的合并冲突由 coordinator 处理（票面 Work constraints 已写明）。
