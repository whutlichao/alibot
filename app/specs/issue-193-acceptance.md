# 验收取证：#193 成员面板「已加入 / 未加入」两段的视觉区分

**票型**：实施票（前端呈现补丁，无后端 / schema 改动）｜**门禁**：`scripts/check.sh`
**Ticket**：<https://github.com/whutlichao/alibot/issues/193>
**base**：`671e607`（= 派活时 worktree 的 HEAD）
**branch**：`agent列表区分不明显`（本 worktree：`/Users/apple/orca/workspaces/alibot/agent列表区分不明显`）
**采集时间**：2026-10-20｜**环境**：macOS（aarch64）；Node v25.0.0；Python 3.14.7 + 仓库根 `.venv` 的依赖
（本 worktree 无 `.venv`，按 `docs/agents/implementation.md` 用符号链接接上主仓的那一份）

---

## 0. 问题与设计裁定（grilling 结论）

现象：两段只差两条信号——`.dock-member` 的实线 `1px --border` vs `.is-outside` 的**虚线同色同宽**，
外加 mono/micro/`--faint`/uppercase 的段标签。暗档 `--surface` 25.5% L 与 `--border` 32% L 只差几个 L，
1× 屏上虚线几乎不可见。

裁定（用户逐条接受推荐）：

| # | 决策 | 结论 |
| --- | --- | --- |
| Q1 | 差异落在哪一层 | 反相：已加入保持卡片；未加入成为次级区块（`--panel` 井 + 平铺行） |
| Q2 | 未加入段的信息密度 | 只留 名字 / 角色 / 状态点 /（若有）不可用原因 +「加入」；删 `model_id` 与状态**文案**（状态点的 `title` / `aria-label` 保留四态） |
| Q3 | 段标题层级与文案 | 层级升到 `fs-sm` + 半粗 + `--fg`（井内 `--muted`）；文案与 i18n 一字不动 |
| Q4 | 加入成功的落点 | 把落点滚进视野，并把焦点交给新卡片的名字控件 |
| Q5 | 井的形状 | 内嵌圆角块（`--panel` + `1px --border` + `--r-md` + 内边距），不做负边距色带 |
| Q6 | 标题位置 | 标题进井内（标题 + 候选行是一个整体） |
| Q7 | 已加入卡片是否加填充 | 不动（默认态不该比候选池更响） |
| Q8 | 空 / 读失败态 | 井照常出现，里面是那一行既有文案 |
| Q9 | 候选行排布 | 保持竖直骨架，只删两行 |
| Q10 | 候选很多时 | 不做折叠 / 搜索 / 上限 |
| Q11 | 落地方式 | 先立票 #193，再改 |

`CONTEXT.md` 词条**不变**：**成员面板**仍是两段、文案仍是「已加入 / 未加入本频道」；本票只改呈现。
不立 ADR：可逆、无「读者会问为什么」的惊讶、也没有真正的替代方案之争。

---

## 1. 判据 → 落点

| 判据 | 落点 | 读数 |
| --- | --- | --- |
| 未加入段是 `--panel` 井 | `app/alibot/studio/app.css` `.dock-group.is-pool` | 暗档 `bg = oklch(0.18 0.014 85)`（dock 的 `--surface` 是 0.255）；浅档 `0.957` / dock `0.994`；`border 1px solid oklch(0.32 / 0.9)`；`radius 8px`；`padding 8px 8px 0` |
| 井的标记来自 JS | `app/alibot/studio/app.js` `renderDock()` | `section.dock-group.is-pool`，`data-od-id="dock-not-joined"` |
| 候选行是平铺行 | `app.css` `.dock-member.is-candidate` | `border-top: 0`、`radius: 0`、`border-bottom: 1px dashed`（仅非末行）；末行 `border-bottom: 0` |
| 候选行瘦身 | `app.js` `memberCard()` 的 `if (!canJoin)` 分支 | 候选行 `hasMeta=false`、`hasStatusText=false`、`hasDot=true`（`aria-label="空闲"`）、`hasJoin=true` |
| 已加入卡片不变 | `app.css` `.dock-member` | `bg transparent`、`border 1px`、`radius 8px` |
| 段标题升层级 | `app.css` `.dock-group-label` | `font-size 12px`、`weight 600`、`transform none`、`letter-spacing normal`、`--fg`（井内 `--muted`） |
| 旧 class 删除 | `app.js` / `app.css` | `.is-outside` 命中数 `0`；候选行 od-id 前缀改为 `dock-candidate-` |
| 加入落点 | `app.js` `joinChannelMember()` + `revealJoinedMember()` | 见 §3.3 |
| 文案与 i18n | `app/alibot/studio/i18n.js` | **未改**（`dock.joined` / `dock.notJoined` 原样） |

---

## 2. 门禁

```
$ scripts/check.sh --fast
== ruff check                       All checks passed!
== ruff format --check              241 files already formatted
== mypy                             Success: no issues found in 101 source files
== pyright                          0 errors, 0 warnings, 0 informations
== node --test（Studio DOM 用例族）
ℹ tests 201
ℹ pass 201
ℹ fail 0

门禁全绿
```

全量（`scripts/check.sh`，pytest 945 条 + 上面这些）：

```
第 1 次：1 failed, 945 passed, 10 skipped  ← test_agent_round.py::test_round_outcome_distinguishes_replied_ignored_and_failed
          该用例单独跑：1 passed；该模块单独跑：35 passed
第 2 次：门禁全绿（pytest 全绿 + node --test 201 pass / 0 fail）
```

本票 diff 里**没有一个 `.py` 文件**（`git show --stat` 只有 `app/alibot/studio/*` 与本文档），
第 1 次那条红是同一条用例在全量顺序下的既有时序抖动（脚本化替身按等待时间判定 `ignored`），
不是本票引入的回归；证据是同一 commit 上的第 2 次全量全绿。

`app/tests/i18n_ui.test.cjs`、`app/tests/agent_detail_ui.test.cjs` 里涉及 `#dockMembers` 的既有用例
（名字打开详情、面板的「未加入」段带 `[data-join]`、空态提示）全绿：本票没动文案，也没动
`data-join` / `data-agent-open` / `data-member` 这三个契约属性。

---

## 3. 真机证据（ego-browser，隔离实例）

实例：`../.venv/bin/python -m alibot --port 8793 --data-dir /tmp/alibot-193`（**不碰**用户实例与
默认数据目录）。数据：4 个 Agent（小红 / 秘书 / 小张 / 小明），频道「证据频道」加入前两个。
为让状态点为「空闲」，在**这个临时库**里确认了一次远端数据流告知（`POST /api/remote-data-flow/ack`）。

### 3.1 暗档（1982×1011，`/tmp/dock-193/evidence-dark.png`）

```
已加入（2）        ── 透明底卡片，79px 高
未加入本频道（2）  ── --panel 井，169px 高；井内候选行 63–64px
```

### 3.2 浅档（1982×1011，`/tmp/dock-193/evidence-light.png`）

```
dock  bg oklch(0.994 0.006 85)
pool  bg oklch(0.957 0.018 85)   border oklch(0.9 0.013 86)
card  bg rgba(0,0,0,0)           border oklch(0.9 0.013 86)
```

### 3.3 加入的落点（`/tmp/dock-193/after-join.png`）

点「加入」前 `joined=2 / pool=2`，点后：

```
joined 3 / pool 1 / focus = dock-open-mem_e66bd32a1ddb（小张）/ cardInView true
```

最后一次加入把候选池清空，井照常存在并显示既有空态文案：

```
未加入本频道（0）本工作区的 Agent 都已在这个频道里。
```

### 3.4 ≤900px 抽屉（860×900，`/tmp/dock-193/evidence-narrow.png`）

```
#dock hidden=false is-open=true width=380 x=480 viewport=860
```

抽屉里两段与桌面同构：井与平铺行都在，没有横向溢出。

---

## 4. 现场保护

- 没有改 `app/alibot/studio/i18n.js`；没有动 `CONTEXT.md` / ADR / `app/README.md`（本票不新增数据面词汇）。
- 唯一的环境痕迹：worktree 根上按 `docs/agents/implementation.md` 建的 `.venv` 符号链接（`.gitignore` 已含）。
- 临时实例与数据目录 `/tmp/alibot-193`、截图 `/tmp/dock-193/` 都是本票自己建的；用户实例与默认数据目录全程未触碰。
