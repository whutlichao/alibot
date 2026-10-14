## Summary

补上「composer 里 `@` 的成员补全」——#2 票面承诺给 #②、#3 验收里却漏接的能力（issue #9）。

```text
composer 里敲下 @（或 @前缀）
  mentionTriggerAt(input)                          # 只看光标处那一个 @
    ├─ 左边界非法（user@ / v1.2@）      → 不弹    # 复用 mentions.py._left_boundary_ok
    ├─ 已输入闭合引号                   → 不弹    # 这段提及写完了
    └─ 否则                          → 弹清单
        候选 = GET /api/channels/{id}/members 里 kind=="agent" 的成员
           过滤 = 包含匹配（ASCII 大小写不敏感），插入的始终是库里的原名
           ├─ 无匹配                      → 清单消失，不打断输入
           ├─ 频道无 Agent 成员            → 明确空态（与「清单读不到」是两条独立空态）
           └─ 有候选                      → ↑↓ 移动 / Enter·Tab 选中 / Esc 关闭不改正文 / 点击选中

选中 → 替换 [@ 的位置, 光标) 这一段 @前缀
  mentionText(name)                                # 只拼字符串，不解析
    ├─ 名字不含 _TOKEN_STOPS 任一字符  → @名字
    └─ 否则取「闭引号不在名字里」的引号对 → @"带 空格的名字" / @“怪"名”
         └─ 三个闭引号都在名字里        → 插入形态不存在：清单里标「无法 @」+ 可读提示，不插无效文本
  其后留一个空格（光标后本来就有空白则不再多补），光标落到插入文本之后
```

```diff
 app/worksplice/studio/
   app.js       +  「@ 补全」一整节：触发判据 / 候选缓存 / 渲染 / 插入 / 提示
                +  state.channelMembers（候选缓存，不是业务正本）
                ~  loadWorkspace、selectChannel 各加一行 refreshChannelMembers()
                ~  sendMessage 加一行 closeMentionPicker()
                ~  bindEvents 的 #composerInput 键盘/焦点处理扩了补全分支
   app.css      +  .composer-mention / .mention-*（只消费既有 token）
   index.html   +  .composer-box 内的弹层挂载点（文档流内，不靠新 z-index 档位）
 app/tests/test_mention_completion.py   新增 9 条（插入形态被后端认可）
 app/specs/issue-9-acceptance.md        新增（逐条验收 + 聚焦自审）
```

**关键不变式：前端不新增一套解析规则。** 它只按 `mentions.py` 的引号规则拼字符串；正文是不是
提及**只由后端**在落库时裁决（`mention_snapshot`）。前端从后端借用的两条判据只决定「要不要弹」
与「怎么拼」，不决定「是不是提及」。

## Evidence

**Before:** composer 里输入 `@` 没有任何补全（#2 承诺、#3 漏接），插入的 `@名字` 全靠人手打，
含空格的名字只能靠人自己想起来写引号形式。基线 `25108fb`。

**After:** 真实 Chromium（ego-browser）手跑，独立临时数据目录 `/tmp/wsps9-data` + 脚本化替身
（零付费模型调用、零凭证）。

```text
输入 @ ：     options=[小明, 前端 设计师, 怪"名 无法 @]        ← 恰是本频道三个 Agent；owner 与非本频道成员未出现
@小明 + Enter：value="@小明 "          caret=4                ← 替换 @前缀 + 一个空格 + 光标落位
@设计 + Tab ：value="@\"前端 设计师\" " caret=10               ← 含空格的名字走引号形式
↓↓ / ↑ / Esc：高亮移动正确；Esc 后 value 与 caret 逐字节不变，继续输入不被打断
user@ / v1.2@：不弹清单；你好@：弹                         ← 与 _left_boundary_ok 对齐
空态频道 @  ：emptyText=「该频道还没有 Agent 成员：…」       hint=「Enter 发送 · Esc 关闭清单」
Shift+Enter ：value="第一行\n第二行"  posts=0               ← 仍是换行
清单打开 Enter：value="@小明 "  posts=0                     ← 选中而非发送
清单关闭 Enter：posts=[{body:"@小明 看这个"}]               ← 仍是发送
```

**硬约束（票面点名必须进 pytest）：两种插入形态都被后端解析器认可。**

```text
$ .venv/bin/python -m pytest app/tests/test_mention_completion.py -q
.........                                                                [100%]

CASE13 引号形式：body='@"前端 设计师" 这条按引号形式插入'
                 snapshot=[{member_id: mem_2b897e09aee3, display_name: 前端 设计师, start: 0, end: 9}]
CASE13 裸形式  ：body='@小明 这条按裸形式插入'
                 snapshot=[{member_id: mem_547e87935163, display_name: 小明, start: 0, end: 3}]
```

两条都在真实浏览器里由补全插入、再经真实 HTTP 面发送，`mention_snapshot` 都命中该成员，
消息流里也都被既有的 `--accent-soft` 底 + `--accent-deep` 字高亮。

**「插入后必须留一个空格」有可复现的反证**：裸形式的候选串必须**恰好等于**成员名，所以
`@小明一下` 的候选串是 `小明一下`、不命中 `小明`；`@小明 一下` 才命中 —— 这就是前端「插入后
一律留一个空格」的根据（pytest `test_a_name_glued_to_following_characters_stops_being_a_mention`）。

**红证据**（隔离 worktree `/tmp/wsps-red-9`，主工作树未动；3 组注入全部判红）：

```text
注入 A 只把空格当分隔符（忽略其它 _TOKEN_STOPS）→ 3 failed, 6 passed
注入 B 名字含引号就放弃（不换另一对引号）      → 1 failed, 8 passed
注入 D 引号对固定用直引号                      → 2 failed, 7 passed
还原后                                        → 9 passed
```

这 9 条在**基线（`25108fb`）上本来就全绿**，这一点如实说明：本票补的是**前端缺失的能力**，
`mentions.py` 是既有正本、本票不改它，所以「构造插入形态 → 快照命中」在基线已成立。这组 pytest
的判别力在**契约冻结**方向（前端拼接规则一旦偏离解析器就判红）；**前端行为**的红→绿证据由上面的
真实 Chromium 手跑承担（候选来源、触发判据、插入落位、空态在基线上都不存在）。

**门禁（全量）**

```text
$ .venv/bin/python -m pytest
152 passed, 1 skipped in 94.05s        # skipped = 默认跳过的真模型冒烟测试

$ .venv/bin/ruff check app && .venv/bin/ruff format --check app
All checks passed! / 31 files already formatted
$ .venv/bin/python -m mypy
Success: no issues found in 24 source files
$ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
pyright errors 0 files 24
```

**视觉与布局**：弹层只消费既有 token、无 hex/rgb/命名色、无过渡与动画（`reduce` 下天然无动效）；
三档视口（1280/1000/880）下 `rail 252`、`dock 380→340→100%`、消息流内层 `880`、正文
14px/1.65/68ch 全部未变，弹层三档都在视口内（挂在 `.composer-box` 内部，不被裁剪）；
`worksplice-design-system/` 与原型 `git status` 无输出（只读未改）。

逐条验收、命令与可见结果、以及**聚焦自审**（本票影响面 + 逐条确认既有行为未破）见
`app/specs/issue-9-acceptance.md`。

## Merge Danger

**Door:** two-way

只改前端（`app.js` / `app.css` / `index.html`），没有数据迁移、没有后端契约变更、没有解析规则
或 `mention_snapshot` 结构变更，`mentions.py` 一字未动；revert 一个提交即可完整回退。新增的 9 条
pytest 只是「无带宽成本」的契约断言。

**Blast Radius:** composer 一个交互（`@` 补全）与其取证。

- **合并冲突已解决**：#8（轮次失败可见，已合并进 main 的 `8d5c8da`）与本票都改 `app.js` / `app.css`。
  本票分支基线是旧 main `25108fb`，冲突只有 `app.js` 一个文件两处，已由合并提交 `3c254c4`
  解决，细节见下方「与 #8 的合并结果」。本票**未碰**消息流渲染、失败呈现、`onStreamOutcome`
  及其 CSS（`git diff` 里对这些符号命中数为 0）。中途一处挂在 `onStreamMessage` 上的候选刷新已移出，
  改挂 composer 的 `focus` 事件 + 切频道 + 初始加载。
- 同权重选择器的顺序依赖：新增 `.mention-opt` 与既有 `.member-pick .member-opt` 同为 (0,2,0)，
  高亮态用的是更具体的 `.mention-opt.is-active` (0,3,0)；真实浏览器已确认生效，无回归。
- 键盘层只**插一层**：清单打开**且有候选**时才接管 `↑↓`/`Enter`/`Tab`；空态时不接管
  （`Enter` 保持发送）；关闭时与改前逐字一致（`Shift+Enter`、`isComposing` 均未变，且
  `isComposing` 从「`Enter` 不发送」收紧为「组合期整段不接管」，行为等价而更一致）。
- 遗留（票面明确不做，已记录）：`/` 命令面板、跨频道补全、chip/富文本；未做「鼠标悬停与键盘
  高亮联动」（鼠标点击即选中，票面未要求）。

## 与 #8 的合并结果

`origin/main` 的 `8d5c8da`（#8 轮次失败可见）已并入本分支，合并提交
`3c254c4`，parents = `42c3409`（本票）+ `8d5c8da`（main）。

**冲突范围与解法。** 两处都在「刷新既有数据」的调用序列里，且**纯增量、互不矛盾**，因此
两处都**保留双方**，没有删除任何一方的调用：

```diff
 loadWorkspace
   await refreshMessages()
+  await refreshOutcomes()          # #8 的轮次结果
+  await refreshChannelMembers()    # #9 的候选成员表
   await refreshStatuses()

 selectChannel
+  closeMentionPicker()             # #9：候选清单属于刚被切换掉的频道
+  state.outcomes = {}              # #8：新频道的轮次结果清空后重建
   render()
   await refreshMessages()
+  await refreshOutcomes()
+  await refreshChannelMembers()
   state.online = true
```

第 2 处的顺序按「先关清单 → 再清 outcomes → 再 render → 然后依次 await 两个刷新」固定：
`closeMentionPicker()` 必须在 `render()` 之前（清单描述的是旧频道的成员表），`state.outcomes = {}`
也必须在 `render()` 之前（否则会先画出旧频道的轮次结果再被清掉）。**两个刷新互不依赖**，
先后只影响首屏顺序，不影响正确性。除此之外未动任何一行，`app.css` / `index.html` 由 git
自动合并、无人工介入。

**合入 main 后的门禁读数（本次新增，命令与结果原样）。**

```text
$ .venv/bin/python -m pytest
158 passed, 1 skipped in 94.68s        # 152→158：main 带来 test_round_outcome_visibility.py 的 6 条
                                       # skipped = 默认跳过的真模型冒烟测试

$ .venv/bin/ruff check app
All checks passed!
$ .venv/bin/ruff format --check app
36 files already formatted             # 31→36：main 新增的文件也算在内

$ .venv/bin/python -m mypy
Success: no issues found in 25 source files
$ node ~/.pi-lens/tools/node_modules/pyright/index.js --outputjson app/worksplice app/tests
pyright errors 0 files 25

$ node --check app/worksplice/studio/app.js
app.js syntax OK                       # 合并后的 app.js 语法自检
```

合入后这些读数**全绿**，两个函数里的调用序列同时满足 #8 的轮次结果与 #9 的成员表两条刷新
需求；`git status` 空、worktree 干净。
